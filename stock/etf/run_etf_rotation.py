from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from .backtest import run_backtest, write_backtest_outputs
from .config import StrategyConfig
from .data import TushareEtfDownloader, normalize_daily_frame

LOGGER = logging.getLogger(__name__)
STRATEGY_RULE_VERSION = "2026-07-18.winner-holding-v1"


def build_parser() -> argparse.ArgumentParser:
    """Build the ETF rotation command-line parser."""
    parser = argparse.ArgumentParser(
        description="Run the multi-asset ETF rotation backtest"
    )
    parser.add_argument(
        "--start", required=True, help="Inclusive start date, YYYY-MM-DD"
    )
    parser.add_argument("--end", required=True, help="Inclusive end date, YYYY-MM-DD")
    parser.add_argument("--initial-capital", type=float, default=1_000_000)
    parser.add_argument("--atr-period", type=int, choices=(5, 10), default=5)
    parser.add_argument("--adx-period", type=int, choices=(5, 10), default=5)
    parser.add_argument("--adx-threshold", type=float, default=20.0)
    parser.add_argument("--atr-stop-multiple", type=float, default=2.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.01)
    parser.add_argument("--max-position-weight", type=float, default=0.20)
    parser.add_argument("--entry-rank", type=int, default=5)
    parser.add_argument("--exit-rank", type=int, default=10)
    parser.add_argument("--entry-confirmation-days", type=int, default=1)
    parser.add_argument("--max-entry-gap-atr", type=float)
    parser.add_argument("--max-industry-weight", type=float, default=0.50)
    parser.add_argument("--correlation-lookback", type=int, default=60)
    parser.add_argument("--min-correlation-observations", type=int, default=40)
    parser.add_argument("--correlation-threshold", type=float)
    parser.add_argument("--max-correlation-weight", type=float)
    parser.add_argument("--winner-holding-enabled", action="store_true")
    parser.add_argument("--market-state-enabled", action="store_true")
    parser.add_argument("--dynamic-risk-enabled", action="store_true")
    parser.add_argument("--winner-promotion-atr-multiple", type=float, default=2.0)
    parser.add_argument("--exit-rank-confirmation-days", type=int, default=3)
    parser.add_argument("--max-portfolio-stop-risk", type=float)
    parser.add_argument("--max-cluster-stop-risk", type=float)
    parser.add_argument("--caution-max-gross-weight", type=float, default=0.50)
    parser.add_argument("--caution-entry-rank", type=int, default=3)
    parser.add_argument("--caution-risk-fraction", type=float, default=0.50)
    parser.add_argument("--risk-off-breadth-threshold", type=float, default=0.35)
    parser.add_argument("--market-state-confirmation-days", type=int, default=2)
    parser.add_argument("--run-id", default="etf_rotation")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--benchmark-csv", type=Path)
    parser.add_argument("--etf-csv", type=Path)
    parser.add_argument("--metadata-csv", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path("stock/etf/data"))
    parser.add_argument("--output-dir", type=Path)
    return parser


def _parse_dates(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    try:
        start_date = pd.Timestamp(start)
        end_date = pd.Timestamp(end)
    except ValueError as exc:
        raise ValueError("start and end must be valid YYYY-MM-DD dates") from exc
    if start_date > end_date:
        raise ValueError("start must not be after end")
    return start_date, end_date


def calculate_download_start(start: pd.Timestamp, warmup_bars: int) -> pd.Timestamp:
    """Return a conservative weekday warmup date before the report start."""
    return pd.Timestamp(start) - pd.offsets.BDay(warmup_bars + 20)


def _read_local(
    benchmark_path: Path,
    etf_path: Path,
    metadata_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    for path in (benchmark_path, etf_path, metadata_path):
        if not path.exists():
            raise ValueError(f"input file does not exist: {path}")
    benchmark = normalize_daily_frame(pd.read_csv(benchmark_path))
    etfs = normalize_daily_frame(pd.read_csv(etf_path))
    metadata = pd.read_csv(metadata_path)
    required = {"symbol", "name", "list_date", "fund_type"}
    missing = required - set(metadata.columns)
    if missing:
        raise ValueError(f"metadata missing columns: {sorted(missing)}")
    metadata["list_date"] = pd.to_datetime(metadata["list_date"], errors="raise")
    return benchmark, etfs, metadata


def _download(
    start: str,
    end: str,
    cache_dir: Path,
    benchmark_symbol: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    downloader = TushareEtfDownloader()
    LOGGER.info("Downloading ETF metadata")
    metadata = downloader.fetch_metadata()
    LOGGER.info("Downloading benchmark daily bars")
    benchmark = downloader.fetch_benchmark(benchmark_symbol, start, end)
    LOGGER.info("Downloading %s ETF histories", len(metadata))
    etfs = downloader.fetch_etfs(
        metadata,
        start,
        end,
        trade_dates=benchmark["datetime"].tolist(),
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    benchmark.to_csv(cache_dir / "benchmark.csv", index=False)
    etfs.to_csv(cache_dir / "etfs.csv", index=False)
    metadata.to_csv(cache_dir / "metadata.csv", index=False)
    return benchmark, etfs, metadata


def main(argv: Sequence[str] | None = None) -> int:
    """Run a downloaded or offline ETF rotation backtest."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        start, end = _parse_dates(args.start, args.end)
        config = StrategyConfig(
            initial_capital=args.initial_capital,
            atr_period=args.atr_period,
            adx_period=args.adx_period,
            adx_threshold=args.adx_threshold,
            atr_stop_multiple=args.atr_stop_multiple,
            risk_per_trade=args.risk_per_trade,
            max_position_weight=args.max_position_weight,
            entry_rank=args.entry_rank,
            exit_rank=args.exit_rank,
            entry_confirmation_days=args.entry_confirmation_days,
            max_entry_gap_atr=args.max_entry_gap_atr,
            max_industry_weight=args.max_industry_weight,
            correlation_lookback=args.correlation_lookback,
            min_correlation_observations=args.min_correlation_observations,
            correlation_threshold=args.correlation_threshold,
            max_correlation_weight=args.max_correlation_weight,
            winner_holding_enabled=args.winner_holding_enabled,
            market_state_enabled=args.market_state_enabled,
            dynamic_risk_enabled=args.dynamic_risk_enabled,
            winner_promotion_atr_multiple=args.winner_promotion_atr_multiple,
            exit_rank_confirmation_days=args.exit_rank_confirmation_days,
            max_portfolio_stop_risk=args.max_portfolio_stop_risk,
            max_cluster_stop_risk=args.max_cluster_stop_risk,
            caution_max_gross_weight=args.caution_max_gross_weight,
            caution_entry_rank=args.caution_entry_rank,
            caution_risk_fraction=args.caution_risk_fraction,
            risk_off_breadth_threshold=args.risk_off_breadth_threshold,
            market_state_confirmation_days=args.market_state_confirmation_days,
        )
        if args.skip_download:
            paths = (args.benchmark_csv, args.etf_csv, args.metadata_csv)
            if any(path is None for path in paths):
                raise ValueError(
                    "--skip-download requires --benchmark-csv, --etf-csv and --metadata-csv"
                )
            benchmark, etfs, metadata = _read_local(*paths)
            data_source = "local_csv"
            data_cache_identifier = "|".join(str(path) for path in paths)
        else:
            download_start = calculate_download_start(start, config.warmup_bars)
            benchmark, etfs, metadata = _download(
                str(download_start.date()),
                args.end,
                args.cache_dir,
                config.benchmark_symbol,
            )
            data_source = "tushare"
            data_cache_identifier = str(args.cache_dir)
        if benchmark.empty or etfs.empty:
            raise ValueError("no daily data is available for the requested run")
        output_dir = args.output_dir or Path("stock/etf/output") / args.run_id
        LOGGER.info("Running ETF rotation backtest")
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
            output_dir,
            config,
            run_metadata={
                "strategy_rule_version": STRATEGY_RULE_VERSION,
                "data_source": data_source,
                "data_cache_identifier": data_cache_identifier,
                "requested_start_date": str(start.date()),
                "requested_end_date": str(end.date()),
                "benchmark_rows": int(len(benchmark)),
                "etf_rows": int(len(etfs)),
                "etf_symbols": int(etfs["symbol"].nunique()),
                "metadata_symbols": int(metadata["symbol"].nunique()),
                "benchmark_data_start": str(benchmark["datetime"].min().date()),
                "benchmark_data_end": str(benchmark["datetime"].max().date()),
                "etf_data_start": str(etfs["datetime"].min().date()),
                "etf_data_end": str(etfs["datetime"].max().date()),
            },
        )
        LOGGER.info("Wrote results to %s", output_dir)
        return 0
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
