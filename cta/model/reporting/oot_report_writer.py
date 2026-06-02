"""OOT evaluation report writer.

Build timestamped OOT report folders from group-pool runtime bundles.
"""
from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from cta.model.reporting.group_pool_aggregate import AggregateConfig
from cta.model.reporting.oot_concentration import (
    compute_concentration_diagnostics,
    compute_deployable_capital_metrics,
)
from cta.model.reporting.oot_report_views import (
    write_cluster_symbol_interval_signal_views,
    write_drilldown,
    write_trade_position_time_distributions,
)
from cta.model.reporting.walk_forward_diagnostics import build_walk_forward_summary

logger = logging.getLogger(__name__)


OOT_SUBDIRS: tuple[str, ...] = (
    "00_overview",
    "01_aggregate",
    "02_by_cluster",
    "03_by_symbol",
    "04_by_interval",
    "05_by_signal_type",
    "06_drilldown",
    "07_benchmark",
    "08_models",
    "09_diagnostics",
    "reports",
    "raw",
    "meta",
)


def _safe_name(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]+", "_", str(value).strip().lower())
    out = out.strip("_")
    return out or "unknown"


def _git_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return str(proc.stdout).strip()


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists() or not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _find_first(bundle_dir: Path, pattern: str) -> Path | None:
    cands = sorted(bundle_dir.glob(pattern))
    return cands[0] if cands else None


def _find_trade_path(bundle_dir: Path) -> Path | None:
    return _find_first(bundle_dir, "*_all_symbol_group_oot_trade_details.csv")


def _load_trades(bundle_dir: Path) -> pd.DataFrame:
    p = _find_trade_path(bundle_dir)
    if p is None or not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p, encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read all_trade_details: %s (%s)", p, exc)
        return pd.DataFrame()


def _executed_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=bool)
    if "execution_status" in df.columns:
        s = df["execution_status"].astype(str).str.lower()
        mask = s.eq("executed")
        if bool(mask.any()):
            return mask
    if "net_pnl" in df.columns:
        return pd.to_numeric(df["net_pnl"], errors="coerce").notna()
    return pd.Series([False] * len(df), index=df.index)


def _compute_max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    eq = pd.to_numeric(equity, errors="coerce").dropna()
    if eq.empty:
        return float("nan")
    peak = eq.cummax()
    dd = (eq - peak) / peak.replace(0, np.nan)
    return float(dd.min()) if len(dd) else float("nan")


def _drawdown_recovery_days(equity: pd.Series, dates: pd.Series) -> int:
    eq = pd.to_numeric(equity, errors="coerce")
    dt = pd.to_datetime(dates, errors="coerce")
    if eq.empty or dt.isna().all():
        return 0
    peak = float("-inf")
    peak_idx = -1
    trough_idx = -1
    max_dd = 0.0
    for i, v in enumerate(eq.to_numpy(dtype=float)):
        if not np.isfinite(v):
            continue
        if v >= peak:
            peak = float(v)
            peak_idx = i
        if peak > 0:
            dd = float(v / peak - 1.0)
            if dd < max_dd:
                max_dd = dd
                trough_idx = i
    if trough_idx < 0:
        return 0
    target_peak = float(eq.iloc[peak_idx]) if peak_idx >= 0 else float("nan")
    for j in range(trough_idx + 1, len(eq)):
        if np.isfinite(eq.iloc[j]) and eq.iloc[j] >= target_peak:
            d0 = pd.Timestamp(dt.iloc[trough_idx])
            d1 = pd.Timestamp(dt.iloc[j])
            if pd.isna(d0) or pd.isna(d1):
                return 0
            return int((d1.normalize() - d0.normalize()).days)
    return 0


def _annualized_return(total_return_pct: float, start_ts: pd.Timestamp | None, end_ts: pd.Timestamp | None) -> float:
    if start_ts is None or end_ts is None or not np.isfinite(total_return_pct):
        return float("nan")
    days = max(1, int((end_ts.normalize() - start_ts.normalize()).days))
    base = 1.0 + float(total_return_pct)
    if base <= 0:
        return float("nan")
    return float(base ** (365.0 / float(days)) - 1.0)


def _monthly_sharpe(executed: pd.DataFrame, capital: float, rf_annual: float) -> float:
    if executed.empty or capital <= 0:
        return float("nan")
    ex = executed.copy()
    ex["_exit"] = pd.to_datetime(ex.get("exit_datetime", ex.get("final_exit_datetime", pd.NaT)), errors="coerce")
    ex = ex.dropna(subset=["_exit"])
    if ex.empty:
        return float("nan")
    ex["_month"] = ex["_exit"].dt.to_period("M").dt.to_timestamp()
    pnl = pd.to_numeric(
        ex.get("net_pnl", pd.Series(0.0, index=ex.index)),
        errors="coerce",
    ).fillna(0.0)
    monthly = pnl.groupby(ex["_month"]).sum()
    ret = monthly / float(capital)
    if len(ret) < 2:
        return float("nan")
    excess = ret - float(rf_annual) / 12.0
    std = float(excess.std(ddof=1))
    if not np.isfinite(std) or std <= 1e-9:
        return float("nan")
    return float(excess.mean() / std * np.sqrt(12.0))


def _format_reproducibility_section(info: dict[str, Any] | None, *, run_tag: str, git_sha: str) -> str:
    """Render a fail-open reproducibility section for executive_summary.md."""
    if not info:
        return ""
    argv = info.get("argv")
    if isinstance(argv, (list, tuple)):
        cmd = " ".join(shlex.quote(str(part)) for part in argv)
    else:
        cmd = str(argv or "")
    generated_at = str(info.get("generated_at") or "")
    note = str(info.get("note") or "")
    key_cfg = info.get("key_cfg") if isinstance(info.get("key_cfg"), dict) else {}
    cfg_text = "，".join(f"{key}={value}" for key, value in key_cfg.items()) if key_cfg else "见 meta/cfg_fingerprint.json"
    return (
        "\n## 复现信息\n"
        f"- 运行命令：`{cmd or '见 meta/cfg_fingerprint.json'}`\n"
        f"- 启动时间：{generated_at or '见 meta/cfg_fingerprint.json'}\n"
        f"- git_sha：{str(info.get('git_sha') or git_sha or '')}\n"
        f"- run_tag：{str(info.get('run_tag') or run_tag)}\n"
        f"- 关键 cfg：{cfg_text}\n"
        f"- 主要测试方向：{note or '默认 cfg，cluster_both 全量 OOT'}\n"
    )


def _write_headline_and_summary(
    report_dir: Path,
    trades: pd.DataFrame,
    *,
    run_tag: str,
    initial_capital: float,
    risk_free_annual: float,
    reproducibility_info: dict[str, Any] | None = None,
) -> None:
    overview = report_dir / "00_overview"
    overview.mkdir(parents=True, exist_ok=True)

    executed = trades.loc[_executed_mask(trades)].copy() if not trades.empty else pd.DataFrame()
    executed["entry_datetime"] = pd.to_datetime(executed.get("entry_datetime", pd.NaT), errors="coerce")
    executed["exit_datetime"] = pd.to_datetime(executed.get("exit_datetime", pd.NaT), errors="coerce")
    pnl = pd.to_numeric(
        executed.get("net_pnl", pd.Series(0.0, index=executed.index)),
        errors="coerce",
    ).fillna(0.0)
    gross = pd.to_numeric(
        executed.get("gross_pnl", pd.Series(pnl, index=executed.index)),
        errors="coerce",
    ).fillna(pnl)
    cost = gross - pnl
    cum_equity = float(initial_capital) + pnl.cumsum()
    max_dd = _compute_max_drawdown(cum_equity)
    total_ret = float(pnl.sum() / float(initial_capital)) if initial_capital > 0 else float("nan")
    start_ts = pd.to_datetime(trades.get("datetime", pd.Series([], dtype=object)), errors="coerce").min() if not trades.empty else pd.NaT
    end_ts = pd.to_datetime(trades.get("datetime", pd.Series([], dtype=object)), errors="coerce").max() if not trades.empty else pd.NaT
    ann_ret = _annualized_return(total_ret, None if pd.isna(start_ts) else pd.Timestamp(start_ts), None if pd.isna(end_ts) else pd.Timestamp(end_ts))
    calmar = float(ann_ret / abs(max_dd)) if np.isfinite(ann_ret) and np.isfinite(max_dd) and max_dd < 0 else float("nan")
    monthly_sharpe = _monthly_sharpe(executed, float(initial_capital), float(risk_free_annual))
    hold_days = (
        (executed["exit_datetime"] - executed["entry_datetime"]).dt.total_seconds() / 86400.0
        if not executed.empty
        else pd.Series([], dtype=float)
    )
    avg_hold_days = float(pd.to_numeric(hold_days, errors="coerce").dropna().mean()) if len(hold_days) else float("nan")
    recovery_days = _drawdown_recovery_days(cum_equity, executed.get("exit_datetime", pd.Series([], dtype=object)))
    win_count = int((pnl > 0).sum())
    trade_count = int(len(executed))
    win_rate = float(win_count / trade_count) if trade_count > 0 else float("nan")

    cluster_count = int(trades.get("group_name", pd.Series([], dtype=object)).astype(str).nunique()) if not trades.empty and "group_name" in trades.columns else 0
    symbol_count = int(trades.get("symbol", pd.Series([], dtype=object)).astype(str).nunique()) if not trades.empty and "symbol" in trades.columns else 0
    interval_count = int(trades.get("interval", pd.Series([], dtype=object)).astype(str).nunique()) if not trades.empty and "interval" in trades.columns else 0
    commission_pct_of_gross = float(cost.sum() / abs(gross.sum())) if abs(float(gross.sum())) > 1e-12 else float("nan")
    concentration = compute_concentration_diagnostics(
        trades,
        initial_capital=float(initial_capital),
    )
    deployable = compute_deployable_capital_metrics(trades, risk_capital_multiplier=2.0)

    git_sha = _git_sha()
    headline_row = {
                "run_tag": str(run_tag),
                "start_date": "" if pd.isna(start_ts) else str(pd.Timestamp(start_ts).date()),
                "end_date": "" if pd.isna(end_ts) else str(pd.Timestamp(end_ts).date()),
                "trade_count": trade_count,
                "win_rate": win_rate,
                "annualized_return_pct": ann_ret,
                "max_drawdown_pct": max_dd,
                "monthly_sharpe": monthly_sharpe,
                "calmar_like": calmar,
                "total_return_pct": total_ret,
                "avg_holding_days": avg_hold_days,
                "longest_dd_recovery_days": int(recovery_days),
                "cluster_count": cluster_count,
                "symbol_count": symbol_count,
                "interval_count": interval_count,
                "commission_pct_of_gross": commission_pct_of_gross,
                "slippage_pct_of_gross": float("nan"),
                "git_sha": git_sha,
    }
    headline_row.update(concentration)
    headline_row.update(deployable)
    headline = pd.DataFrame([headline_row])
    headline.to_csv(overview / "headline_metrics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([concentration]).to_csv(
        report_dir / "09_diagnostics" / "concentration_diagnostics.csv",
        index=False,
        encoding="utf-8-sig",
    )
    build_walk_forward_summary(
        trades,
        n_windows=4,
        initial_capital=float(initial_capital),
    ).to_csv(report_dir / "09_diagnostics" / "walk_forward_summary.csv", index=False, encoding="utf-8-sig")

    best_cluster = ""
    worst_cluster = ""
    if not executed.empty and "group_name" in executed.columns:
        gp = pd.to_numeric(executed["net_pnl"], errors="coerce").fillna(0.0).groupby(executed["group_name"].astype(str)).sum()
        if len(gp):
            best_cluster = str(gp.sort_values(ascending=False).index[0])
            worst_cluster = str(gp.sort_values(ascending=True).index[0])

    warning_line = ""
    if bool(concentration.get("concentration_warning", False)):
        warning_line = "\n⚠️ 收益集中度偏高：请优先查看 09_diagnostics/concentration_diagnostics.csv。\n"

    summary_md = (
        f"# OOT 评估摘要 — {run_tag}\n\n"
        f"期间：{headline.iloc[0]['start_date']} → {headline.iloc[0]['end_date']}\n\n"
        f"总收益率 {total_ret:.4f}，年化 {ann_ret:.4f}，最大回撤 {max_dd:.4f}，月度夏普 {monthly_sharpe:.4f}。\n"
        f"最强板块：{best_cluster or 'N/A'}；最弱板块：{worst_cluster or 'N/A'}。\n"
        f"{warning_line}"
        f"{_format_reproducibility_section(reproducibility_info, run_tag=run_tag, git_sha=git_sha)}"
    )
    (overview / "executive_summary.md").write_text(summary_md, encoding="utf-8")

    brief = (
        f"# OOT Brief ({run_tag})\n\n"
        f"- 期间：{headline.iloc[0]['start_date']} ~ {headline.iloc[0]['end_date']}\n"
        f"- 收益：{total_ret:.2%}，年化：{ann_ret:.2%}，最大回撤：{max_dd:.2%}\n"
        f"- 成交：{trade_count} 笔，胜率：{win_rate:.2%}\n"
    )
    (report_dir / "reports" / "brief.md").write_text(brief, encoding="utf-8")


def _write_aggregate(report_dir: Path, bundle_dir: Path) -> None:
    agg = report_dir / "01_aggregate"
    monthly_src = _find_first(bundle_dir, "*_aggregate_monthly_metrics.csv")
    weekly_src = _find_first(bundle_dir, "*_aggregate_weekly_metrics.csv")
    summary_src = _find_first(bundle_dir, "*_aggregate_summary.csv")
    if monthly_src is not None:
        _copy_if_exists(monthly_src, agg / "monthly_metrics.csv")
    if weekly_src is not None:
        _copy_if_exists(weekly_src, agg / "weekly_metrics.csv")
    if summary_src is not None:
        _copy_if_exists(summary_src, agg / "summary.csv")


def _write_diagnostics(report_dir: Path, run_records: Iterable[dict[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for rec in run_records:
        result = rec.get("result")
        if result is None:
            continue
        metrics_path = Path(getattr(result, "metrics_path", ""))
        if not metrics_path.exists():
            continue
        try:
            df = pd.read_csv(metrics_path, encoding="utf-8-sig")
        except Exception:
            continue
        if df.empty:
            continue
        auc_cols = [c for c in df.columns if "auc" in str(c).lower()]
        for _, row in df.iterrows():
            win_id = int(pd.to_numeric(pd.Series([row.get("window_id", np.nan)]), errors="coerce").fillna(-1).iloc[0])
            split = str(row.get("split", ""))
            for col in auc_cols:
                auc = pd.to_numeric(pd.Series([row.get(col, np.nan)]), errors="coerce").iloc[0]
                if not np.isfinite(auc):
                    continue
                alert = "low_auc" if float(auc) < 0.52 else ""
                rows.append(
                    {
                        "group_name": str(rec.get("group_name", "")),
                        "pool_name": str(rec.get("pool_name", "")),
                        "interval": str(rec.get("interval", "")),
                        "window_id": win_id,
                        "split": split,
                        "metric_name": str(col),
                        "auc": float(auc),
                        "alert": alert,
                    }
                )
    diag = pd.DataFrame(rows, columns=["group_name", "pool_name", "interval", "window_id", "split", "metric_name", "auc", "alert"])
    diag.to_csv(report_dir / "09_diagnostics" / "auc_per_window.csv", index=False, encoding="utf-8-sig")


def _write_meta(report_dir: Path, *, run_tag: str, cfg: AggregateConfig) -> None:
    meta = report_dir / "meta"
    (meta / "git_sha.txt").write_text((_git_sha() or "") + "\n", encoding="utf-8")
    (meta / "run_tag.txt").write_text(str(run_tag) + "\n", encoding="utf-8")
    snapshot = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "run_tag": str(run_tag),
        "initial_capital": float(cfg.initial_capital),
        "risk_free_annual_return": float(cfg.risk_free_annual_return),
        "benchmark_annual_return": float(cfg.benchmark_annual_return),
    }
    (meta / "config_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_stub_html_reports(report_dir: Path, *, run_tag: str) -> None:
    reports = report_dir / "reports"
    executive_html = (
        "<html><head><meta charset='utf-8'><title>OOT Executive</title></head>"
        f"<body><h1>OOT Executive Report ({run_tag})</h1>"
        "<p>See 00_overview/headline_metrics.csv and executive_summary.md.</p></body></html>"
    )
    analyst_html = (
        "<html><head><meta charset='utf-8'><title>OOT Analyst</title></head>"
        f"<body><h1>OOT Analyst Report ({run_tag})</h1>"
        "<p>See 01_aggregate ~ 09_diagnostics for detailed tables.</p></body></html>"
    )
    (reports / "executive.html").write_text(executive_html, encoding="utf-8")
    (reports / "analyst.html").write_text(analyst_html, encoding="utf-8")


def write_oot_evaluation_report(
    bundle_dir: Path,
    run_records: Iterable[dict[str, Any]],
    *,
    run_tag: str = "prod",
    cfg: AggregateConfig | None = None,
    reproducibility_info: dict[str, Any] | None = None,
) -> Path:
    """Build one timestamped OOT report directory from one runtime bundle."""
    bundle_dir = Path(bundle_dir).resolve()
    cfg = cfg or AggregateConfig()
    ts = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y%m%d_%H%M%S")
    report_dir = bundle_dir.parent / f"oot_{ts}_{_safe_name(run_tag)}"
    for name in OOT_SUBDIRS:
        (report_dir / name).mkdir(parents=True, exist_ok=True)

    trades = _load_trades(bundle_dir)
    trade_path = _find_trade_path(bundle_dir)
    if trade_path is not None:
        _copy_if_exists(trade_path, report_dir / "raw" / "all_trade_details.csv")

    _write_aggregate(report_dir, bundle_dir)
    _write_headline_and_summary(
        report_dir,
        trades,
        run_tag=run_tag,
        initial_capital=float(cfg.initial_capital),
        risk_free_annual=float(cfg.risk_free_annual_return),
        reproducibility_info=reproducibility_info,
    )
    write_cluster_symbol_interval_signal_views(report_dir, trades, cfg=cfg)
    write_drilldown(report_dir, trades)
    write_trade_position_time_distributions(report_dir, trades)
    _write_diagnostics(report_dir, run_records)
    _write_meta(report_dir, run_tag=run_tag, cfg=cfg)
    _write_stub_html_reports(report_dir, run_tag=run_tag)

    # compatibility copy from existing symbol_group_details layout
    detail_root = bundle_dir / "symbol_group_details"
    cluster_dir = report_dir / "02_by_cluster"
    if detail_root.exists() and detail_root.is_dir():
        for d in sorted(detail_root.iterdir()):
            if not d.is_dir():
                continue
            dst = cluster_dir / _safe_name(d.name)
            dst.mkdir(parents=True, exist_ok=True)
            for f in d.glob("*.csv"):
                _copy_if_exists(f, dst / f.name)
            for f in d.glob("*.md"):
                _copy_if_exists(f, dst / f.name)

    logger.info("OOT report directory written: %s", report_dir)
    return report_dir


__all__ = ["write_oot_evaluation_report", "OOT_SUBDIRS"]
