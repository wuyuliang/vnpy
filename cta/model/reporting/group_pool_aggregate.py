"""Group-pool aggregate report.

读取所有 symbol group 的 OOT trade_details（已被
``_write_group_pool_runtime_bundle`` 合并到一张表），按月 / 按周聚合，
输出 3 张 CSV + 一段 logger 打印：

- ``{prefix}_aggregate_monthly_metrics.csv``：每月一行（trade_count / win_rate /
  net_pnl / return_pct / cum_return_pct / drawdown_pct）
- ``{prefix}_aggregate_weekly_metrics.csv``：每周一行（同上字段）
- ``{prefix}_aggregate_summary.csv``：单行汇总（trade_count / win_rate /
  monthly_sharpe / weekly_sharpe / max_dd / total_return_pct / group_count）

公式对齐 ``pipeline_oot_evaluation._evaluate_oot_real_execution`` 的口径：
- ``initial_capital`` 默认 10_000_000；
- 年化 sharpe = 月/周 excess_return 的 mean/std × √period_per_year
  （月 12，周 52），无风险利率默认 2%/年；
- max_drawdown 用 cumulative net_pnl 上的 running peak 计算。

也可作为 CLI 独立运行，对已存在的 bundle 重新聚合：

    python -m cta.model.reporting.group_pool_aggregate --bundle-dir /path/to/bundle
"""
from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# 与 pipeline_oot_evaluation.py 输出对齐
TRADE_PNL_COL = "net_pnl"
TRADE_GROSS_COL = "gross_pnl"
TRADE_STATUS_COL = "execution_status"
EXIT_DT_CANDIDATES: tuple[str, ...] = (
    "final_exit_datetime",
    "exit_datetime",
    "entry_fill_datetime",
    "datetime",
)


@dataclass(frozen=True)
class AggregateConfig:
    """聚合配置；默认对齐 DEFAULT_OOT_EVAL_CONFIG。"""

    initial_capital: float = 10_000_000.0
    benchmark_annual_return: float = 0.02
    risk_free_annual_return: float = 0.02

    @classmethod
    def from_oot_config(cls, oot_cfg: object) -> "AggregateConfig":
        """从 OotEvaluationConfig 派生（避免硬编码漂移）。"""
        def _get(name: str, default: float) -> float:
            return float(getattr(oot_cfg, name, default))

        return cls(
            initial_capital=_get("initial_capital", 10_000_000.0),
            benchmark_annual_return=_get("benchmark_annual_return", 0.02),
            risk_free_annual_return=_get("risk_free_annual_return", 0.02),
        )


__all__ = [
    "AggregateConfig",
    "build_aggregate_reports",
    "write_aggregate_reports",
]


# ---------- 内部工具 ----------

def _resolve_exit_datetime(df: pd.DataFrame) -> pd.Series:
    """优先 final_exit_datetime → exit_datetime → entry_fill_datetime → datetime。"""
    for col in EXIT_DT_CANDIDATES:
        if col in df.columns:
            ts = pd.to_datetime(df[col], errors="coerce")
            if ts.notna().any():
                return ts
    return pd.Series([pd.NaT] * len(df), index=df.index)


def _filter_executed(df: pd.DataFrame) -> pd.DataFrame:
    """只保留真实成交行：execution_status 在 executed/filled/closed，
    或 net_pnl 非空（兼容老版本无 status 列的情况）。"""
    if df.empty:
        return df
    if TRADE_STATUS_COL in df.columns:
        status = df[TRADE_STATUS_COL].astype(str).str.lower()
        mask = status.isin({"executed", "filled", "closed", "stop_loss", "trailing_stop", "horizon_exit"})
        if not mask.any() and TRADE_PNL_COL in df.columns:
            # 退化：用 net_pnl 非空作为已成交判据（兼容 status 取值不在白名单的旧数据）
            mask = pd.to_numeric(df[TRADE_PNL_COL], errors="coerce").notna()
        return df.loc[mask].copy()
    if TRADE_PNL_COL in df.columns:
        return df.loc[pd.to_numeric(df[TRADE_PNL_COL], errors="coerce").notna()].copy()
    return df


def _period_label(freq: str) -> str:
    """bucket 列的标签（CSV 列头）。"""
    return {"M": "month", "W": "week"}.get(freq, freq.lower())


def _period_qualifier(freq: str) -> str:
    """summary 列前缀（与 OOT summary 的 monthly_*/weekly_* 保持一致）。"""
    return {"M": "monthly", "W": "weekly"}.get(freq, freq.lower())


def _annualization_factor(freq: str) -> float:
    return {"M": 12.0, "W": 52.0}.get(freq, 1.0)


def _empty_bucket_frame(freq: str) -> pd.DataFrame:
    label = _period_label(freq)
    return pd.DataFrame(
        columns=[
            label,
            "trade_count",
            "win_count",
            "loss_count",
            "win_rate",
            "gross_pnl",
            "net_pnl",
            "return_pct",
            "cum_equity",
            "cum_return_pct",
            "drawdown_pct",
        ]
    )


def _bucket_metrics(trades: pd.DataFrame, *, freq: str, cfg: AggregateConfig) -> pd.DataFrame:
    """按月（freq='M'）或按周（freq='W'）聚合 trades 得到带累计 equity / drawdown 的表。"""
    if trades.empty:
        return _empty_bucket_frame(freq)

    exit_dt = _resolve_exit_datetime(trades)
    pnl = pd.to_numeric(trades.get(TRADE_PNL_COL, pd.Series(0.0, index=trades.index)), errors="coerce").fillna(0.0)
    gross = pd.to_numeric(
        trades.get(TRADE_GROSS_COL, pnl), errors="coerce"
    ).fillna(pnl)
    win_mask = pnl > 0
    loss_mask = pnl < 0

    work = pd.DataFrame(
        {
            "exit_dt": exit_dt,
            "pnl": pnl.to_numpy(),
            "gross": gross.to_numpy(),
            "win": win_mask.astype(int).to_numpy(),
            "loss": loss_mask.astype(int).to_numpy(),
        },
        index=trades.index,
    )
    work = work.dropna(subset=["exit_dt"])
    if work.empty:
        return _empty_bucket_frame(freq)

    bucket = work["exit_dt"].dt.to_period(freq)
    grouped = pd.DataFrame(
        {
            "trade_count": bucket.groupby(bucket).count(),
            "win_count": work["win"].groupby(bucket).sum(),
            "loss_count": work["loss"].groupby(bucket).sum(),
            "gross_pnl": work["gross"].groupby(bucket).sum(),
            "net_pnl": work["pnl"].groupby(bucket).sum(),
        }
    ).sort_index()

    label_col = _period_label(freq)
    grouped[label_col] = grouped.index.astype(str)
    safe_count = grouped["trade_count"].replace(0, np.nan)
    grouped["win_rate"] = grouped["win_count"].astype(float) / safe_count
    capital = float(cfg.initial_capital) if float(cfg.initial_capital) > 0 else 1.0
    grouped["return_pct"] = grouped["net_pnl"].astype(float) / capital

    cum_pnl = grouped["net_pnl"].astype(float).cumsum()
    cum_equity = capital + cum_pnl
    grouped["cum_equity"] = cum_equity
    grouped["cum_return_pct"] = cum_pnl / capital
    running_peak = cum_equity.cummax().replace(0, np.nan)
    grouped["drawdown_pct"] = (cum_equity - running_peak) / running_peak

    grouped = grouped.reset_index(drop=True)
    return grouped[[
        label_col,
        "trade_count",
        "win_count",
        "loss_count",
        "win_rate",
        "gross_pnl",
        "net_pnl",
        "return_pct",
        "cum_equity",
        "cum_return_pct",
        "drawdown_pct",
    ]]


def _period_stats(buckets: pd.DataFrame, freq: str, cfg: AggregateConfig) -> dict[str, float]:
    """返回该频率（月/周）下的 sharpe / max_dd / avg / std 等指标。

    summary 列名前缀用 ``monthly_`` / ``weekly_``（与 OOT summary 一致）。
    """
    qual = _period_qualifier(freq)
    if buckets.empty:
        return {
            f"{qual}_obs": 0,
            f"{qual}_sharpe": float("nan"),
            f"max_dd_pct_{qual}": float("nan"),
            f"avg_{qual}_return_pct": float("nan"),
            f"std_{qual}_return_pct": float("nan"),
            f"excess_avg_{qual}_return_pct": float("nan"),
        }
    rets = pd.to_numeric(buckets["return_pct"], errors="coerce").dropna()
    af = _annualization_factor(freq)
    rf_per_period = float(cfg.risk_free_annual_return) / af
    excess = rets - rf_per_period
    mean_ex = float(excess.mean()) if len(excess) else float("nan")
    std_ex = float(excess.std(ddof=1)) if len(excess) > 1 else float("nan")
    sharpe = float("nan")
    if std_ex and not math.isnan(std_ex) and std_ex > 1e-9:
        sharpe = mean_ex / std_ex * math.sqrt(af)
    max_dd = float(buckets["drawdown_pct"].min()) if "drawdown_pct" in buckets.columns else float("nan")
    return {
        f"{qual}_obs": int(len(buckets)),
        f"{qual}_sharpe": sharpe,
        f"max_dd_pct_{qual}": max_dd if not math.isnan(max_dd) else float("nan"),
        f"avg_{qual}_return_pct": float(rets.mean()),
        f"std_{qual}_return_pct": float(rets.std(ddof=1)) if len(rets) > 1 else float("nan"),
        f"excess_avg_{qual}_return_pct": mean_ex,
    }


def _compute_summary(
    monthly: pd.DataFrame,
    weekly: pd.DataFrame,
    trades: pd.DataFrame,
    cfg: AggregateConfig,
) -> pd.DataFrame:
    pnl = pd.to_numeric(trades.get(TRADE_PNL_COL, pd.Series(0.0, index=trades.index)), errors="coerce").fillna(0.0)
    gross = pd.to_numeric(trades.get(TRADE_GROSS_COL, pnl), errors="coerce").fillna(pnl)
    trade_count = int(len(trades))
    win_count = int((pnl > 0).sum())
    loss_count = int((pnl < 0).sum())
    summary: dict[str, float] = {
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": float(win_count / trade_count) if trade_count else float("nan"),
        "gross_pnl_total": float(gross.sum()),
        "net_pnl_total": float(pnl.sum()),
        "total_return_pct": float(pnl.sum() / cfg.initial_capital) if cfg.initial_capital else float("nan"),
        "initial_capital": float(cfg.initial_capital),
        "is_final_account_pnl": False,
        "group_count": int(trades["group_name"].nunique()) if "group_name" in trades.columns else 0,
        "interval_count": int(trades["group_interval"].nunique()) if "group_interval" in trades.columns else 0,
        "risk_free_annual_return": float(cfg.risk_free_annual_return),
        "benchmark_annual_return": float(cfg.benchmark_annual_return),
    }
    summary.update(_period_stats(monthly, "M", cfg))
    summary.update(_period_stats(weekly, "W", cfg))
    return pd.DataFrame([summary])


# ---------- 对外入口 ----------

def build_aggregate_reports(
    trade_details: pd.DataFrame,
    *,
    cfg: AggregateConfig | None = None,
) -> dict[str, pd.DataFrame]:
    """从合并后的 trade_details 构建月度 / 周度 / 单行 summary 3 张表。

    输入要求列：``net_pnl``、可选的 ``gross_pnl`` / ``execution_status`` /
    ``group_name`` / ``group_interval`` / 任一退出时间列。
    """
    cfg = cfg or AggregateConfig()
    if trade_details is None or trade_details.empty:
        return {
            "monthly": _empty_bucket_frame("M"),
            "weekly": _empty_bucket_frame("W"),
            "summary": _compute_summary(
                _empty_bucket_frame("M"),
                _empty_bucket_frame("W"),
                pd.DataFrame(columns=[TRADE_PNL_COL]),
                cfg,
            ),
        }
    executed = _filter_executed(trade_details)
    monthly = _bucket_metrics(executed, freq="M", cfg=cfg)
    weekly = _bucket_metrics(executed, freq="W", cfg=cfg)
    summary = _compute_summary(monthly, weekly, executed, cfg)
    return {"monthly": monthly, "weekly": weekly, "summary": summary}


def write_aggregate_reports(
    bundle_dir: Path,
    trade_details: pd.DataFrame,
    *,
    run_date_tag: str,
    group_key: str,
    side_key: str,
    cfg: AggregateConfig | None = None,
) -> dict[str, Path]:
    """写 3 个 CSV + logger 打印关键指标。

    返回 dict[name → Path]，name 为 monthly/weekly/summary。
    """
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg or AggregateConfig()
    reports = build_aggregate_reports(trade_details, cfg=cfg)
    prefix = f"{run_date_tag}_group_pool_{group_key}_{side_key}"
    paths: dict[str, Path] = {
        "monthly": bundle_dir / f"{prefix}_aggregate_monthly_metrics.csv",
        "weekly": bundle_dir / f"{prefix}_aggregate_weekly_metrics.csv",
        "summary": bundle_dir / f"{prefix}_aggregate_summary.csv",
    }
    for name, path in paths.items():
        reports[name].to_csv(path, index=False, encoding="utf-8-sig")
    _log_summary(reports["summary"], reports["monthly"], reports["weekly"], paths)
    return paths


# ---------- 打印 ----------

def _safe_float(x: object, default: float = float("nan")) -> float:
    try:
        v = float(x)
        return v if not math.isnan(v) else default
    except (TypeError, ValueError):
        return default


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.2f}%" if not math.isnan(x) else "nan"


def _fmt_bucket_list(df: pd.DataFrame, label_col: str) -> str:
    if df.empty:
        return "(none)"
    parts: list[str] = []
    for _, row in df.iterrows():
        parts.append(
            f"{row[label_col]}: {_fmt_pct(_safe_float(row['return_pct'], 0.0))} "
            f"({int(row.get('win_count', 0))}/{int(row.get('trade_count', 0))} wins)"
        )
    return " | ".join(parts)


def _log_summary(
    summary: pd.DataFrame,
    monthly: pd.DataFrame,
    weekly: pd.DataFrame,
    paths: dict[str, Path],
) -> None:
    if summary.empty:
        logger.info("[group_pool_aggregate] empty report (no executed trades)")
        return
    row = summary.iloc[0].to_dict()
    win_rate = _safe_float(row.get("win_rate"))
    logger.info(
        "===== GROUP-POOL AGGREGATE REPORT =====\n"
        "  trades=%d win=%d loss=%d win_rate=%s\n"
        "  net_pnl=%.0f gross_pnl=%.0f total_return=%s\n"
        "  group_count=%d interval_count=%d initial_capital=%.0f",
        int(row.get("trade_count", 0)),
        int(row.get("win_count", 0)),
        int(row.get("loss_count", 0)),
        _fmt_pct(win_rate),
        _safe_float(row.get("net_pnl_total"), 0.0),
        _safe_float(row.get("gross_pnl_total"), 0.0),
        _fmt_pct(_safe_float(row.get("total_return_pct"), 0.0)),
        int(row.get("group_count", 0)),
        int(row.get("interval_count", 0)),
        _safe_float(row.get("initial_capital"), 0.0),
    )
    logger.info(
        "  monthly: obs=%d sharpe=%.3f max_dd=%s avg=%s std=%s",
        int(row.get("monthly_obs", 0)),
        _safe_float(row.get("monthly_sharpe")),
        _fmt_pct(_safe_float(row.get("max_dd_pct_monthly"), 0.0)),
        _fmt_pct(_safe_float(row.get("avg_monthly_return_pct"), 0.0)),
        _fmt_pct(_safe_float(row.get("std_monthly_return_pct"), 0.0)),
    )
    logger.info(
        "  weekly : obs=%d sharpe=%.3f max_dd=%s avg=%s std=%s",
        int(row.get("weekly_obs", 0)),
        _safe_float(row.get("weekly_sharpe")),
        _fmt_pct(_safe_float(row.get("max_dd_pct_weekly"), 0.0)),
        _fmt_pct(_safe_float(row.get("avg_weekly_return_pct"), 0.0)),
        _fmt_pct(_safe_float(row.get("std_weekly_return_pct"), 0.0)),
    )
    if not monthly.empty and "return_pct" in monthly.columns:
        sorted_m = monthly.sort_values("return_pct", ascending=False)
        logger.info("  monthly top-3   : %s", _fmt_bucket_list(sorted_m.head(3), "month"))
        logger.info("  monthly bottom-3: %s", _fmt_bucket_list(sorted_m.tail(3).iloc[::-1], "month"))
    logger.info("  written:")
    for name, p in paths.items():
        logger.info("    %-7s -> %s", name, p)


# ---------- CLI（对已存在 bundle 再次聚合）----------

def _detect_bundle_trade_csv(bundle_dir: Path) -> Path:
    cands = sorted(bundle_dir.glob("*_all_symbol_group_oot_trade_details.csv"))
    if not cands:
        raise FileNotFoundError(
            f"no *_all_symbol_group_oot_trade_details.csv under {bundle_dir}"
        )
    if len(cands) > 1:
        logger.warning("multiple trade-detail CSVs found, using newest: %s", cands[-1])
    return cands[-1]


def _detect_run_tag(trade_csv: Path) -> tuple[str, str, str]:
    """从 ``{date}_group_pool_{group}_{side}_all_symbol_group_oot_trade_details.csv``
    回推 run_date_tag / group_key / side_key。失败时返回 ('rerun', 'cluster', 'both')。"""
    name = trade_csv.stem
    # name = "{date}_group_pool_{group}_{side}_all_symbol_group_oot_trade_details"
    parts = name.split("_")
    try:
        idx = parts.index("group_pool") if "group_pool" in parts else parts.index("group")
        return parts[idx - 1], parts[idx + 2], parts[idx + 3]
    except (ValueError, IndexError):
        return "rerun", "cluster", "both"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="rebuild group-pool aggregate reports from a bundle")
    parser.add_argument("--bundle-dir", required=True, help="path to *_GROUP_POOL_*_portfolio_logic_runtime/")
    parser.add_argument("--initial-capital", type=float, default=10_000_000.0)
    parser.add_argument("--risk-free", type=float, default=0.02, help="annual risk-free rate, e.g. 0.02")
    parser.add_argument("--benchmark", type=float, default=0.02, help="annual benchmark return")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bundle_dir = Path(args.bundle_dir).resolve()
    trade_csv = _detect_bundle_trade_csv(bundle_dir)
    run_tag, group_key, side_key = _detect_run_tag(trade_csv)
    df = pd.read_csv(trade_csv, encoding="utf-8-sig")
    cfg = AggregateConfig(
        initial_capital=args.initial_capital,
        risk_free_annual_return=args.risk_free,
        benchmark_annual_return=args.benchmark,
    )
    write_aggregate_reports(
        bundle_dir,
        df,
        run_date_tag=run_tag,
        group_key=group_key,
        side_key=side_key,
        cfg=cfg,
    )


if __name__ == "__main__":
    main()
