"""Live periodic report writer (weekly/monthly/summary)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from cta.model.reporting.group_pool_aggregate import AggregateConfig, build_aggregate_reports


def _normalize_trade_details(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "execution_status" in out.columns:
        status = out["execution_status"].astype(str).str.lower().str.strip()
        status = status.replace({"filled": "executed", "candidate": "blocked"})
        out["execution_status"] = status
    if "datetime" in out.columns and "exit_datetime" not in out.columns:
        out["exit_datetime"] = out["datetime"]
    if "net_pnl" not in out.columns:
        out["net_pnl"] = 0.0
    out["net_pnl"] = pd.to_numeric(out["net_pnl"], errors="coerce")
    return out


def write_periodic_reports(
    *,
    trade_details: pd.DataFrame,
    out_dir: Path | str,
    run_tag: str = "live",
    initial_capital: float = 10_000_000.0,
    risk_free_annual_return: float = 0.02,
    benchmark_annual_return: float = 0.02,
) -> dict[str, str]:
    """Write monthly/weekly/summary CSV using OOT aggregate formulas."""
    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_trade_details(trade_details)
    cfg = AggregateConfig(
        initial_capital=float(initial_capital),
        risk_free_annual_return=float(risk_free_annual_return),
        benchmark_annual_return=float(benchmark_annual_return),
    )
    reports = build_aggregate_reports(normalized, cfg=cfg)

    tag = str(run_tag).strip() or "live"
    monthly_path = out_root / f"{tag}_live_monthly_metrics.csv"
    weekly_path = out_root / f"{tag}_live_weekly_metrics.csv"
    summary_path = out_root / f"{tag}_live_summary.csv"

    reports["monthly"].to_csv(monthly_path, index=False, encoding="utf-8-sig")
    reports["weekly"].to_csv(weekly_path, index=False, encoding="utf-8-sig")
    reports["summary"].to_csv(summary_path, index=False, encoding="utf-8-sig")

    return {
        "monthly": str(monthly_path),
        "weekly": str(weekly_path),
        "summary": str(summary_path),
    }


__all__ = ["write_periodic_reports"]

