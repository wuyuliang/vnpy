"""Run the single-ETF previous-day EMA5/EMA10 open strategy from local CSV data."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from .ema5_open_strategy import Ema5OpenConfig, run_ema5_open_backtest
from .render_trade_charts import render_all_trade_charts

ETF_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ETF_ROOT / "data/20260717_2018_20260717_point_in_time_live"
DEFAULT_DAILY_CSV = DEFAULT_SOURCE_ROOT / "etfs_lifecycle_clean.csv"
DEFAULT_METADATA_CSV = DEFAULT_SOURCE_ROOT / "metadata.csv"
DEFAULT_OUTPUT_DIR = ETF_ROOT / "output/20260719_chuangyeban"


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_csv(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_and_write(
    *,
    daily_csv: Path,
    metadata_csv: Path,
    output_dir: Path,
    config: Ema5OpenConfig | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run the local backtest, persist audits, and render its trade card."""
    cfg = config or Ema5OpenConfig()
    daily = pd.read_csv(daily_csv)
    metadata = pd.read_csv(metadata_csv)
    required_metadata = {"symbol", "name", "fund_type"}
    missing_metadata = required_metadata - set(metadata.columns)
    if missing_metadata:
        raise ValueError(f"ETF metadata missing columns: {sorted(missing_metadata)}")
    selected = metadata.loc[metadata["symbol"].astype(str).eq(cfg.symbol)]
    if len(selected) != 1 or not str(selected.iloc[0]["name"]).strip():
        raise ValueError(f"metadata requires one named row for {cfg.symbol}")

    result = run_ema5_open_backtest(daily, cfg)
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_csv(result.signals, output_dir / "signals.csv")
    _atomic_write_csv(result.trades, output_dir / "trades.csv")
    _atomic_write_csv(result.positions, output_dir / "positions.csv")
    _atomic_write_csv(result.equity_curve, output_dir / "equity_curve.csv")
    summary = {
        **result.summary,
        "daily_csv": str(daily_csv),
        "metadata_csv": str(metadata_csv),
        "output_dir": str(output_dir),
        "parameters": {
            "ema_fast_period": 5,
            "ema_slow_period": 10,
            "entry_rule": "open>previous_ema5 and previous_ema5>previous_ema10",
            "exit_rule": "open<previous_ema10 or previous_ema5<=previous_ema10",
            "initial_capital": cfg.initial_capital,
            "lot_size": cfg.lot_size,
            "commission_rate": cfg.commission_rate,
            "min_commission": cfg.min_commission,
            "slippage_rate": cfg.slippage_rate,
        },
    }
    _atomic_write_json(summary, output_dir / "summary.json")
    render_all_trade_charts(
        daily_csv=daily_csv,
        metadata_csv=metadata_csv,
        trades_csv=output_dir / "trades.csv",
        positions_csv=output_dir / "positions.csv",
        output_dir=output_dir / "charts",
        report_start=result.equity_curve.iloc[0]["datetime"],
        report_end=result.equity_curve.iloc[-1]["datetime"],
        overwrite=overwrite,
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone local backtest CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-csv", type=Path, default=DEFAULT_DAILY_CSV)
    parser.add_argument("--metadata-csv", type=Path, default=DEFAULT_METADATA_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--symbol", default="159915.SZ")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the strategy and print its JSON summary."""
    args = build_parser().parse_args(argv)
    summary = run_and_write(
        daily_csv=args.daily_csv,
        metadata_csv=args.metadata_csv,
        output_dir=args.output_dir,
        config=Ema5OpenConfig(
            symbol=args.symbol,
            initial_capital=args.initial_capital,
        ),
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
