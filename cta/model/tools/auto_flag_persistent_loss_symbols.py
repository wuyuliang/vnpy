"""Scan recent OOT trade-detail reports and flag persistently-loss symbols.

M2 自动化：扫 ``cta/report/backtest/*_POOL_*_model_pipeline`` 目录里的
``*_oot_trade_details.csv``，对每个 symbol 在最近 N 份报告里聚合 net_pnl，连续
负 PnL 超过阈值的 symbol 输出到候选 disable manifest，让团队 review 后并入
``cta/feature/symbol_disable_manifest.csv``。

设计要点：
- **不直接覆盖现有 manifest**：脚本输出 ``<out>``，需要人工 review 后合并；
- **只取 POOL 报告**：单 symbol 报告天然只有一个品种数据，无法对比；
- **支持 reason 过滤**：可以限定 minute60 / day / minute30 等 interval 单独评估；
- **配置阈值**：``min_reports`` (默认 3)、``loss_threshold`` (默认每份报告 net_pnl<-1000)。

用法
----

    python -m cta.model.tools.auto_flag_persistent_loss_symbols \
        --report-root cta/report/backtest \
        --pattern '*_POOL_minute60_*_model_pipeline' \
        --min-reports 3 \
        --loss-threshold -1000 \
        --out cta/feature/symbol_disable_manifest.candidate.csv
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from cta.config.skill_tight_range_breakout_config import CTA_ROOT

logger = logging.getLogger(__name__)

DEFAULT_REPORT_ROOT: Path = CTA_ROOT / "report" / "backtest"


def _find_trade_detail_files(report_root: Path, pattern: str) -> list[Path]:
    """Find OOT trade detail csv files under report_root matching pattern."""
    matched_dirs = sorted([p for p in report_root.glob(pattern) if p.is_dir()])
    files: list[Path] = []
    for d in matched_dirs:
        for f in d.glob("*_oot_trade_details.csv"):
            files.append(f)
    return files


def aggregate_symbol_pnl(
    files: Sequence[Path],
) -> pd.DataFrame:
    """Return per-symbol per-report net_pnl aggregation."""
    rows: list[dict[str, object]] = []
    for fp in files:
        try:
            df = pd.read_csv(fp, encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            logger.warning("read %s failed: %s", fp, exc)
            continue
        if "symbol" not in df.columns or "net_pnl" not in df.columns:
            continue
        exec_mask = df.get("execution_status", "executed").astype(str) == "executed"
        sub = df.loc[exec_mask, ["symbol", "net_pnl"]].copy()
        sub["net_pnl"] = pd.to_numeric(sub["net_pnl"], errors="coerce").fillna(0.0)
        agg = sub.groupby(sub["symbol"].astype(str).str.upper())["net_pnl"].sum().reset_index()
        agg["report"] = fp.parent.name
        rows.append(agg)
    if not rows:
        return pd.DataFrame(columns=["symbol", "net_pnl", "report"])
    return pd.concat(rows, axis=0, ignore_index=True)


def flag_persistent_loss(
    agg_df: pd.DataFrame,
    *,
    min_reports: int = 3,
    loss_threshold: float = -1000.0,
) -> pd.DataFrame:
    """Return symbols losing ≥ min_reports times with net_pnl ≤ loss_threshold each."""
    if agg_df.empty:
        return pd.DataFrame(columns=["symbol", "loss_report_count", "total_net_pnl", "reports"])
    losers = agg_df.loc[agg_df["net_pnl"] <= float(loss_threshold)].copy()
    if losers.empty:
        return pd.DataFrame(columns=["symbol", "loss_report_count", "total_net_pnl", "reports"])
    grouped = (
        losers.groupby("symbol")
        .agg(
            loss_report_count=("report", "nunique"),
            total_net_pnl=("net_pnl", "sum"),
            reports=("report", lambda s: ";".join(sorted(set(s)))),
        )
        .reset_index()
    )
    out = grouped.loc[grouped["loss_report_count"] >= int(min_reports)].copy()
    return out.sort_values("total_net_pnl").reset_index(drop=True)


def write_candidate_manifest(
    flagged: pd.DataFrame,
    out_path: Path,
    *,
    interval_label: str = "auto",
    source_tag: str = "",
) -> Path:
    """Write a candidate manifest in the standard schema for human review."""
    today = datetime.now().strftime("%Y-%m-%d")
    tag = str(source_tag).strip() or datetime.now().strftime("%Y%m%d")
    source_value = f"oot_{tag}_{str(interval_label).strip() or 'auto'}"
    rows: list[dict[str, str]] = []
    for _, r in flagged.iterrows():
        rows.append(
            {
                "symbol": str(r["symbol"]).upper(),
                "reason": "persistent_loss",
                "source": source_value,
                "disabled_at": today,
                "notes": (
                    f"loss_reports={int(r['loss_report_count'])}, "
                    f"total_net_pnl={float(r['total_net_pnl']):.2f}"
                ),
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["symbol", "reason", "source", "disabled_at", "notes"]).to_csv(
        out_path, index=False, encoding="utf-8-sig"
    )
    return out_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auto-flag persistent-loss symbols from OOT reports")
    p.add_argument(
        "--report-root",
        default=str(DEFAULT_REPORT_ROOT),
        help=f"backtest report root (default {DEFAULT_REPORT_ROOT})",
    )
    p.add_argument(
        "--pattern",
        default="*_POOL_*_model_pipeline",
        help="glob pattern under report-root to scan",
    )
    p.add_argument(
        "--min-reports",
        type=int,
        default=3,
        help="symbol must lose in at least N reports to be flagged (default 3)",
    )
    p.add_argument(
        "--loss-threshold",
        type=float,
        default=-1000.0,
        help="per-report net_pnl threshold to count as a loss (default -1000)",
    )
    p.add_argument(
        "--interval-label",
        default="auto",
        help="label for the source field (e.g. minute60, day)",
    )
    p.add_argument(
        "--source-tag",
        default="",
        help="source tag for traceability, e.g. 20260512 (source=oot_<tag>_<interval>)",
    )
    p.add_argument(
        "--out",
        default=str(CTA_ROOT / "feature" / "symbol_disable_manifest.candidate.csv"),
        help="output candidate manifest path; review and merge into main manifest manually",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _parse_args(argv)
    report_root = Path(args.report_root).expanduser().resolve()
    files = _find_trade_detail_files(report_root, str(args.pattern))
    logger.info("found %d trade detail files under %s matching %r", len(files), report_root, args.pattern)
    if not files:
        logger.warning("no files matched; check --pattern")
        return
    agg = aggregate_symbol_pnl(files)
    flagged = flag_persistent_loss(
        agg,
        min_reports=int(args.min_reports),
        loss_threshold=float(args.loss_threshold),
    )
    out_path = Path(args.out).expanduser().resolve()
    write_candidate_manifest(
        flagged,
        out_path,
        interval_label=str(args.interval_label),
        source_tag=str(args.source_tag),
    )
    logger.info("flagged %d persistent-loss symbols → %s", len(flagged), out_path)
    if len(flagged):
        logger.info("preview:\n%s", flagged.head(20).to_string(index=False))


__all__ = [
    "aggregate_symbol_pnl",
    "flag_persistent_loss",
    "write_candidate_manifest",
    "main",
]


if __name__ == "__main__":
    main()
