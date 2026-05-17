"""Candidate-event training dataset builder."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from cta.config.baseline_skill_suite_config import BASELINE_SIGNAL_TYPES
from cta.config.skill_tight_range_breakout_config import CTA_ROOT
from cta.model.feature.candidate_baseline_bridge import (
    SYMBOLS_RANKING_PATH,
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _resolve_run_exchange,
    generate_candidate_events_from_baselines,
)
from cta.model.feature.candidate_schema import CandidateTrainingDatasetResult, standardize_candidate_events
from cta.model.feature.training_feature_builder import FEATURE_ROOT, build_training_feature_table

logger = logging.getLogger(__name__)

MODEL_FEATURE_ROOT: Path = CTA_ROOT / "data" / "model_feature"
DEFAULT_MACRO_FEATURE_PATH: Path = CTA_ROOT / "data" / "feature" / "macro" / "macro_daily.parquet"


def _load_macro_feature_table(path: Path) -> pd.DataFrame:
    """Load macro feature table keyed by trade_date."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"macro feature file not found: {p}")
    df = pd.read_parquet(p)
    if "trade_date" in df.columns:
        out = df.copy()
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
    else:
        out = df.reset_index().rename(columns={"index": "trade_date"})
        if "trade_date" not in out.columns:
            raise KeyError(f"macro feature table missing trade_date: {p}")
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["trade_date"]).copy()
    keep_cols = ["trade_date"] + [c for c in out.columns if str(c).startswith("macro_")]
    if len(keep_cols) <= 1:
        raise ValueError(f"macro feature table has no macro_* columns: {p}")
    out = out[keep_cols].drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
    return out.reset_index(drop=True)


def _merge_macro_features(samples: pd.DataFrame, macro_df: pd.DataFrame) -> pd.DataFrame:
    """Join macro features by candidate trade date."""
    if samples.empty:
        return samples.copy()
    out = samples.copy()
    if "candidate_trade_date" in out.columns:
        out["_candidate_trade_date"] = pd.to_datetime(
            out["candidate_trade_date"], errors="coerce"
        ).dt.normalize()
    elif "datetime" in out.columns:
        out["_candidate_trade_date"] = pd.to_datetime(out["datetime"], errors="coerce").dt.normalize()
    else:
        return out
    merged = out.merge(
        macro_df,
        left_on="_candidate_trade_date",
        right_on="trade_date",
        how="left",
    )
    merged = merged.drop(columns=["_candidate_trade_date", "trade_date"], errors="ignore")
    return merged


def build_and_save_candidate_training_dataset(
    candidate_df: pd.DataFrame,
    symbol: str,
    interval: str,
    output_root: Path = MODEL_FEATURE_ROOT,
    feature_root: Path = FEATURE_ROOT,
    run_tag: str | None = None,
    generic_columns: Iterable[str] | None = None,
    enable_macro_features: bool = True,
    macro_feature_path: Path = DEFAULT_MACRO_FEATURE_PATH,
) -> CandidateTrainingDatasetResult:
    """Build and persist standardized candidate-events + merged training samples."""
    sym = str(symbol).upper()
    interval_norm = str(interval).lower()
    tag = str(run_tag).strip() if run_tag else pd.Timestamp.now().strftime("%Y%m%d")
    dataset_dir = output_root / interval_norm / sym / tag
    dataset_dir.mkdir(parents=True, exist_ok=True)

    standardized = standardize_candidate_events(candidate_df)
    if standardized.empty:
        merged = standardized.copy()
    else:
        merged_raw = build_training_feature_table(
            candidate_df=standardized,
            symbol=sym,
            interval=interval_norm,
            feature_root=feature_root,
            generic_columns=generic_columns,
        )
        merged = standardize_candidate_events(merged_raw)
        if enable_macro_features:
            try:
                macro_df = _load_macro_feature_table(macro_feature_path)
                merged = _merge_macro_features(merged, macro_df)
            except Exception as exc:  # noqa: BLE001
                logger.warning("skip macro feature join: %s", exc)

    candidate_events_parquet = dataset_dir / f"{tag}_{sym}_{interval_norm}_candidate_events.parquet"
    training_samples_parquet = dataset_dir / f"{tag}_{sym}_{interval_norm}_training_samples.parquet"
    summary_parquet = dataset_dir / f"{tag}_{sym}_{interval_norm}_dataset_summary.parquet"

    standardized.to_parquet(candidate_events_parquet, index=False)
    merged.to_parquet(training_samples_parquet, index=False)

    if standardized.empty:
        summary_rows: list[dict[str, object]] = [
            {
                "symbol": sym,
                "interval": interval_norm,
                "total_candidates": 0,
                "executed_count": 0,
                "filtered_count": 0,
                "blocked_risk_count": 0,
                "blocked_capacity_count": 0,
                "blocked_execution_count": 0,
                "not_triggered_market_count": 0,
                "good_opportunity_count": 0,
                "unknown_opportunity_count": 0,
                "generic_feature_columns": 0,
                "candidate_events_parquet": str(candidate_events_parquet),
                "training_samples_parquet": str(training_samples_parquet),
            }
        ]
    else:
        summary_rows = [
            {
                "symbol": sym,
                "interval": interval_norm,
                "total_candidates": int(len(standardized)),
                "executed_count": int((standardized["sample_status"] == "executed").sum()),
                "filtered_count": int((standardized["sample_status"] == "filtered_by_rule").sum()),
                "blocked_risk_count": int((standardized["sample_status"] == "blocked_by_risk").sum()),
                "blocked_capacity_count": int((standardized["sample_status"] == "blocked_by_capacity").sum()),
                "blocked_execution_count": int((standardized["sample_status"] == "blocked_by_execution").sum()),
                "not_triggered_market_count": int(
                    (standardized["sample_status"] == "not_triggered_market").sum()
                ),
                "good_opportunity_count": int((standardized["is_good_opportunity"] == 1).sum()),
                "unknown_opportunity_count": int((standardized["opportunity_class"] == "U").sum()),
                "generic_feature_columns": int(len([c for c in merged.columns if c.startswith("generic_")])),
                "candidate_events_parquet": str(candidate_events_parquet),
                "training_samples_parquet": str(training_samples_parquet),
            }
        ]
    pd.DataFrame(summary_rows).to_parquet(summary_parquet, index=False)

    logger.info("candidate dataset saved: %s", dataset_dir)
    return CandidateTrainingDatasetResult(
        dataset_dir=dataset_dir,
        candidate_events_parquet=candidate_events_parquet,
        training_samples_parquet=training_samples_parquet,
        summary_parquet=summary_parquet,
    )


def generate_and_save_candidate_training_dataset(
    symbol: str,
    exchange: str | None,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str = "both",
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    horizon_bars: int = 20,
    output_root: Path = MODEL_FEATURE_ROOT,
    feature_root: Path = FEATURE_ROOT,
    run_tag: str | None = None,
    generic_columns: Iterable[str] | None = None,
    enable_macro_features: bool = True,
    macro_feature_path: Path = DEFAULT_MACRO_FEATURE_PATH,
) -> CandidateTrainingDatasetResult:
    """One-stop API: generate candidate events from baselines and persist dataset."""
    candidate_df = generate_candidate_events_from_baselines(
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        trade_side_mode=trade_side_mode,
        signal_types=signal_types,
        horizon_bars=horizon_bars,
    )
    return build_and_save_candidate_training_dataset(
        candidate_df=candidate_df,
        symbol=symbol,
        interval=interval,
        output_root=output_root,
        feature_root=feature_root,
        run_tag=run_tag,
        generic_columns=generic_columns,
        enable_macro_features=enable_macro_features,
        macro_feature_path=macro_feature_path,
    )


def generate_and_save_candidate_training_dataset_multi(
    symbol: str,
    exchange: str | None,
    intervals: Sequence[str],
    start_date: str,
    end_date: str,
    trade_side_mode: str = "both",
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    horizon_bars: int = 20,
    output_root: Path = MODEL_FEATURE_ROOT,
    feature_root: Path = FEATURE_ROOT,
    run_tag: str | None = None,
    generic_columns: Iterable[str] | None = None,
    enable_macro_features: bool = True,
    macro_feature_path: Path = DEFAULT_MACRO_FEATURE_PATH,
) -> list[CandidateTrainingDatasetResult]:
    """Run candidate dataset generation for multiple intervals."""
    interval_tuple = _normalize_intervals(intervals)
    results: list[CandidateTrainingDatasetResult] = []
    for idx, interval in enumerate(interval_tuple, start=1):
        logger.info(
            "[%d/%d] running candidate dataset generation for symbol=%s interval=%s",
            idx,
            len(interval_tuple),
            symbol,
            interval,
        )
        try:
            result = generate_and_save_candidate_training_dataset(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                trade_side_mode=trade_side_mode,
                signal_types=signal_types,
                horizon_bars=horizon_bars,
                output_root=output_root,
                feature_root=feature_root,
                run_tag=run_tag,
                generic_columns=generic_columns,
                enable_macro_features=enable_macro_features,
                macro_feature_path=macro_feature_path,
            )
        except Exception:
            logger.exception("candidate dataset generation failed for interval=%s", interval)
            continue
        results.append(result)
    return results


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build candidate-event training dataset")
    parser.add_argument("--symbol", default="RB0")
    parser.add_argument("--exchange", default="SHFE")
    parser.add_argument("--top-n-symbols", type=int, default=0)
    parser.add_argument("--symbols-ranking-path", default=str(SYMBOLS_RANKING_PATH))
    parser.add_argument(
        "--interval",
        nargs="+",
        default=["60min"],
        help="one or more intervals, supports comma and whitespace separators",
    )
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--end", default="2019-12-31")
    parser.add_argument("--trade-side-mode", default="both")
    parser.add_argument("--horizon-bars", type=int, default=20)
    parser.add_argument("--run-tag", default=None)
    parser.add_argument("--output-root", default=str(MODEL_FEATURE_ROOT))
    parser.add_argument("--feature-root", default=str(FEATURE_ROOT))
    parser.add_argument("--signal-types", default=",".join(BASELINE_SIGNAL_TYPES))
    parser.add_argument("--macro-feature-path", default=str(DEFAULT_MACRO_FEATURE_PATH))
    parser.add_argument(
        "--enable-macro-features",
        dest="enable_macro_features",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--disable-macro-features",
        dest="enable_macro_features",
        action="store_false",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    from cta.utils.random_seed import seed_all_from_env

    used_seed = seed_all_from_env("CTA_GLOBAL_SEED")
    if used_seed is not None:
        logger.info("candidate_training_dataset: seeded global RNG from CTA_GLOBAL_SEED=%s", used_seed)

    args = _parse_args(argv)
    intervals = _normalize_intervals(args.interval)
    signal_types = tuple(s.strip() for s in str(args.signal_types).split(",") if s.strip())
    output_root = Path(args.output_root).resolve()
    feature_root = Path(args.feature_root).resolve()
    macro_feature_path = Path(args.macro_feature_path).resolve()

    top_n = int(getattr(args, "top_n_symbols", 0))
    if top_n > 0:
        symbols_to_run = _load_top_n_symbols_from_ranking(Path(args.symbols_ranking_path), top_n=top_n)
        logger.info(
            "top-n symbol mode enabled: top_n=%s ranking_path=%s loaded=%s",
            top_n,
            args.symbols_ranking_path,
            [s for s, _ in symbols_to_run],
        )
    else:
        symbols_to_run = [(str(args.symbol).upper(), str(args.exchange).upper() if args.exchange else None)]

    for sidx, (symbol, exchange_from_rank) in enumerate(symbols_to_run, start=1):
        run_exchange = _resolve_run_exchange(exchange_from_rank, args.exchange)
        logger.info(
            "[%d/%d] run symbol=%s exchange=%s intervals=%s",
            sidx,
            len(symbols_to_run),
            symbol,
            run_exchange,
            list(intervals),
        )
        results = generate_and_save_candidate_training_dataset_multi(
            symbol=symbol,
            exchange=run_exchange,
            intervals=intervals,
            start_date=args.start,
            end_date=args.end,
            trade_side_mode=args.trade_side_mode,
            signal_types=signal_types,
            horizon_bars=int(args.horizon_bars),
            run_tag=args.run_tag,
            output_root=output_root,
            feature_root=feature_root,
            enable_macro_features=bool(args.enable_macro_features),
            macro_feature_path=macro_feature_path,
        )
        for interval, result in zip(intervals, results):
            logger.info("[%s][%s] candidate_events: %s", symbol, interval, result.candidate_events_parquet)
            logger.info("[%s][%s] training_samples: %s", symbol, interval, result.training_samples_parquet)
            logger.info("[%s][%s] summary: %s", symbol, interval, result.summary_parquet)


if __name__ == "__main__":
    main()


__all__ = [
    "MODEL_FEATURE_ROOT",
    "SYMBOLS_RANKING_PATH",
    "CandidateTrainingDatasetResult",
    "_normalize_intervals",
    "_load_top_n_symbols_from_ranking",
    "_resolve_run_exchange",
    "_parse_args",
    "standardize_candidate_events",
    "generate_candidate_events_from_baselines",
    "build_and_save_candidate_training_dataset",
    "generate_and_save_candidate_training_dataset",
    "generate_and_save_candidate_training_dataset_multi",
]
