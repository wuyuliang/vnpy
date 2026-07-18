from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pandas as pd

from .backtest import run_backtest, write_backtest_outputs
from .config import StrategyConfig
from .data import normalize_daily_frame
from .experiments import write_experiment_report
from .run_etf_rotation import STRATEGY_RULE_VERSION

LOGGER = logging.getLogger(__name__)


def build_experiment_configs() -> dict[str, StrategyConfig]:
    """Return the six immutable, preregistered winner-holding configurations."""
    baseline = StrategyConfig(atr_period=5, adx_period=10)
    risk_controls = replace(
        baseline,
        dynamic_risk_enabled=True,
        max_industry_weight=0.35,
        correlation_threshold=0.90,
        max_correlation_weight=0.30,
        max_portfolio_stop_risk=0.03,
        max_cluster_stop_risk=0.015,
    )
    winner_holding = replace(
        baseline,
        winner_holding_enabled=True,
        atr_stop_multiple=3.0,
        exit_rank=20,
        winner_promotion_atr_multiple=2.0,
        exit_rank_confirmation_days=3,
    )
    market_states = replace(
        baseline,
        market_state_enabled=True,
        dynamic_risk_enabled=True,
        caution_max_gross_weight=0.50,
        caution_entry_rank=3,
        caution_risk_fraction=0.50,
        risk_off_breadth_threshold=0.35,
        market_state_confirmation_days=2,
    )
    combined = replace(
        baseline,
        winner_holding_enabled=True,
        market_state_enabled=True,
        dynamic_risk_enabled=True,
        atr_stop_multiple=3.0,
        exit_rank=20,
        winner_promotion_atr_multiple=2.0,
        exit_rank_confirmation_days=3,
        max_industry_weight=0.35,
        correlation_threshold=0.90,
        max_correlation_weight=0.30,
        max_portfolio_stop_risk=0.03,
        max_cluster_stop_risk=0.015,
        caution_max_gross_weight=0.50,
        caution_entry_rank=3,
        caution_risk_fraction=0.50,
        risk_off_breadth_threshold=0.35,
        market_state_confirmation_days=2,
    )
    return {
        "clean_baseline": baseline,
        "risk_controls_only": risk_controls,
        "winner_holding_only": winner_holding,
        "market_states_only": market_states,
        "combined": combined,
        "combined_cost2x": replace(
            combined,
            commission_rate=combined.commission_rate * 2,
            slippage_rate=combined.slippage_rate * 2,
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run preregistered ETF winner-holding experiments"
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--benchmark-csv", type=Path, required=True)
    parser.add_argument("--etf-csv", type=Path, required=True)
    parser.add_argument("--metadata-csv", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--skip-existing", action="store_true")
    return parser


def _read_inputs(
    benchmark_path: Path,
    etf_path: Path,
    metadata_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    for path in (benchmark_path, etf_path, metadata_path):
        if not path.is_file():
            raise ValueError(f"input file does not exist: {path}")
    benchmark = normalize_daily_frame(pd.read_csv(benchmark_path))
    etfs = normalize_daily_frame(pd.read_csv(etf_path))
    metadata = pd.read_csv(metadata_path)
    required_metadata = {"symbol", "name", "list_date", "fund_type"}
    missing = required_metadata - set(metadata)
    if missing:
        raise ValueError(f"metadata missing columns: {sorted(missing)}")
    metadata["list_date"] = pd.to_datetime(metadata["list_date"], errors="raise")
    if "delist_date" in metadata:
        metadata["delist_date"] = pd.to_datetime(
            metadata["delist_date"], errors="coerce"
        )
    return benchmark, etfs, metadata


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        start = pd.Timestamp(args.start)
        end = pd.Timestamp(args.end)
        if start > end:
            raise ValueError("start must not be after end")
        benchmark, etfs, metadata = _read_inputs(
            args.benchmark_csv,
            args.etf_csv,
            args.metadata_csv,
        )
        configs = build_experiment_configs()
        run_dirs: dict[str, Path] = {}
        for run_id, config in configs.items():
            run_dir = args.output_root / run_id
            run_dirs[run_id] = run_dir
            if args.skip_existing and (run_dir / "summary.json").is_file():
                LOGGER.info("Skipping existing run %s", run_id)
                continue
            LOGGER.info("Running %s", run_id)
            result = run_backtest(
                benchmark,
                etfs,
                metadata,
                config,
                start_date=start,
                end_date=end,
            )
            write_backtest_outputs(
                result,
                run_dir,
                config,
                run_metadata={
                    "run_id": run_id,
                    "strategy_rule_version": STRATEGY_RULE_VERSION,
                    "data_source": "local_csv",
                    "requested_start_date": str(start.date()),
                    "requested_end_date": str(end.date()),
                    "benchmark_csv": str(args.benchmark_csv),
                    "etf_csv": str(args.etf_csv),
                    "metadata_csv": str(args.metadata_csv),
                },
            )
        write_experiment_report(
            run_dirs,
            benchmark,
            args.output_root / "report",
            baseline_run_id="clean_baseline",
            candidate_run_id="combined",
            cost_stress_run_id="combined_cost2x",
        )
        return 0
    except (KeyError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
