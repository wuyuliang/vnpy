"""Backtest runner and CLI for baseline skill suite."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import BASELINE_SIGNAL_TYPES, TRAINING_FEATURE_COLUMNS
from cta.config.skill_tight_range_breakout_config import BacktestConfig, VALID_SIDE_MODES
from cta.skills.data_backtest.event_driven_backtest import EngineConfig, run_backtest
from cta.skills.data_backtest.trade_evaluation import summarize_trades
from cta.skills.data_backtest.transaction_cost import estimate_cost
from cta.strategy.baseline_candidate_gen import build_training_samples_from_trade_log, generate_candidate_opportunities
from cta.strategy.baseline_feature_frame import prepare_master_feature_frame
from cta.strategy.baseline_helpers import (
    SYMBOLS_RANKING_PATH,
    BaselineSuiteRunResult,
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _resolve_run_exchange,
)
from cta.strategy.baseline_strategies import create_baseline_strategy
from cta.strategy.skill_tight_range_backtest import (
    build_contract_spec,
    load_bars,
    normalize_interval,
    resolve_exchange,
    suggest_periods_per_year,
)

logger = logging.getLogger(__name__)


def _compute_metrics(
    trade_log: pd.DataFrame,
    equity_curve: pd.Series,
    initial_capital: float,
    periods_per_year: int,
) -> dict[str, float]:
    s = summarize_trades(trade_log, equity_curve, periods_per_year=periods_per_year)
    total_pnl = float(s["total_pnl"])
    return {
        "total_pnl": total_pnl,
        "total_return": total_pnl / float(initial_capital) if initial_capital else 0.0,
        "annualized": float(s["annualized"]),
        "mdd": float(s["mdd"]),
        "sharpe": float(s["sharpe"]),
        "calmar": float(s["calmar"]),
        "winrate": float(s["winrate"]),
        "pf": float(s["pf"]),
        "trade_count": int(s["trade_count"]),
    }


def run_baseline_suite(
    symbol: str,
    exchange: str | None,
    interval: str,
    start_date: str,
    end_date: str,
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    trade_side_mode: str = "both",
    initial_capital: float = 1_000_000.0,
    periods_per_year: int | None = None,
    output_root: Path | None = None,
) -> BaselineSuiteRunResult:
    """Run baseline suite and export summary + training samples."""
    interval_norm = normalize_interval(interval)
    mode = str(trade_side_mode).strip().lower()
    if mode not in VALID_SIDE_MODES:
        raise ValueError(f"invalid trade_side_mode={trade_side_mode}, valid={sorted(VALID_SIDE_MODES)}")
    strategies = tuple(str(s).strip().lower() for s in signal_types)
    for st in strategies:
        if st not in BASELINE_SIGNAL_TYPES:
            raise ValueError(f"unsupported signal_type={st}, valid={BASELINE_SIGNAL_TYPES}")

    ppy = int(periods_per_year) if periods_per_year is not None else suggest_periods_per_year(interval_norm)
    bcfg = BacktestConfig(
        initial_capital=float(initial_capital),
        periods_per_year=ppy,
        interval=interval_norm,
    )
    sym = str(symbol).upper()
    ex = str(exchange).upper() if exchange else resolve_exchange(sym, bcfg.symbols_list_path)
    bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
    frame = prepare_master_feature_frame(bars, interval=interval_norm)
    contract = build_contract_spec(sym, ex)

    run_date = pd.Timestamp.now().strftime("%Y%m%d")
    root = output_root or bcfg.output_root
    out_dir = root / f"{run_date}_baseline_skill_suite_{sym}_{interval_norm}_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    sample_parts: list[pd.DataFrame] = []
    for st in strategies:
        strat = create_baseline_strategy(
            signal_type=st,
            frame=frame,
            contract=contract,
            trade_side_mode=mode,
        )
        engine_cfg = EngineConfig(
            fill_rule="next_open",
            stop_fill="worst",
            cost_fn=estimate_cost,
            slippage_ticks=contract.slippage_ticks,
        )
        out = run_backtest(frame, strat, engine_cfg)
        trade_log = out["trade_log"].copy()
        equity_curve = (out["equity_curve"].astype(float) + float(initial_capital)).rename("equity")
        metrics = _compute_metrics(trade_log, equity_curve, float(initial_capital), ppy)

        strat_dir = out_dir / st
        strat_dir.mkdir(parents=True, exist_ok=True)
        trades_path = strat_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_{st}_trades.csv"
        equity_path = strat_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_{st}_equity.csv"
        summary_path = strat_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_{st}_summary.csv"
        trade_log.to_csv(trades_path, index=False, encoding="utf-8-sig")
        pd.DataFrame({"equity": equity_curve}).to_csv(equity_path, index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [{**metrics, "symbol": sym, "exchange": ex, "interval": interval_norm, "signal_type": st}]
        ).to_csv(summary_path, index=False, encoding="utf-8-sig")

        summary_rows.append(
            {
                "symbol": sym,
                "exchange": ex,
                "interval": interval_norm,
                "trade_side_mode": mode,
                "signal_type": st,
                **metrics,
                "trades_path": str(trades_path),
                "equity_path": str(equity_path),
                "summary_path": str(summary_path),
            }
        )
        candidates = generate_candidate_opportunities(
            frame=frame,
            symbol=sym,
            exchange=ex,
            interval=interval_norm,
            signal_type=st,
            horizon_bars=20,
            trade_side_mode=mode,
        )
        if candidates.empty:
            samples = build_training_samples_from_trade_log(
                trade_log=trade_log,
                frame=frame,
                symbol=sym,
                exchange=ex,
                interval=interval_norm,
                signal_type=st,
            )
            if not samples.empty:
                samples = samples.copy()
                samples["label_class"] = samples["label_win"].astype(int)
                samples["future_mfe_atr"] = samples["label_mfe_atr"].astype(float)
                samples["future_mae_atr"] = samples["label_mae_atr"].astype(float)
                trend_dir = pd.to_numeric(samples.get("feature_trend_dir", 0), errors="coerce").fillna(0.0)
                samples["regime_label"] = np.where(
                    trend_dir > 0,
                    "trend_up",
                    np.where(trend_dir < 0, "trend_down", "range"),
                )
                sample_parts.append(samples)
        else:
            sample_parts.append(candidates)

    suite_summary = pd.DataFrame(summary_rows)
    suite_summary_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_suite_summary.csv"
    suite_summary.to_csv(suite_summary_path, index=False, encoding="utf-8-sig")

    if sample_parts:
        training_samples = pd.concat(sample_parts, axis=0, ignore_index=True).sort_values("datetime")
    else:
        training_samples = build_training_samples_from_trade_log(
            trade_log=pd.DataFrame(),
            frame=frame,
            symbol=sym,
            exchange=ex,
            interval=interval_norm,
            signal_type="none",
        )
    training_samples_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_training_samples.csv"
    training_samples.to_csv(training_samples_path, index=False, encoding="utf-8-sig")

    report_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_baseline_report.md"
    summary_view = suite_summary[
        [
            "signal_type",
            "total_return",
            "annualized",
            "mdd",
            "sharpe",
            "calmar",
            "winrate",
            "pf",
            "trade_count",
        ]
    ]
    try:
        summary_block = summary_view.to_markdown(index=False, floatfmt=".6f")
    except (ImportError, ValueError) as exc:
        logger.warning("to_markdown failed (%s), fallback to to_string", exc)
        summary_block = "```\n" + summary_view.to_string(index=False, float_format="%.6f") + "\n```"

    lines = [
        "# Baseline Skill Suite Report",
        "",
        f"- symbol: `{sym}.{ex}`",
        f"- interval: `{interval_norm}`",
        f"- trade_side_mode: `{mode}`",
        f"- range: `{start_date}` -> `{end_date}`",
        f"- bars: `{len(frame)}`",
        f"- strategies: `{', '.join(strategies)}`",
        "",
        "## Summary",
        summary_block,
        "",
        f"- suite_summary_csv: `{suite_summary_path}`",
        f"- training_samples_csv: `{training_samples_path}`",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("baseline suite done: %s", out_dir)
    return BaselineSuiteRunResult(
        output_dir=out_dir,
        summary_path=suite_summary_path,
        training_samples_path=training_samples_path,
        report_path=report_path,
    )


def run_baseline_suite_multi(
    symbol: str,
    exchange: str | None,
    intervals: Sequence[str] | str,
    start_date: str,
    end_date: str,
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    trade_side_mode: str = "both",
    initial_capital: float = 1_000_000.0,
    periods_per_year: int | None = None,
    output_root: Path | None = None,
) -> list[BaselineSuiteRunResult]:
    """Run baseline suite across multiple intervals."""
    interval_tuple = _normalize_intervals(intervals)
    results: list[BaselineSuiteRunResult] = []
    for idx, interval in enumerate(interval_tuple, start=1):
        logger.info(
            "[%d/%d] baseline suite symbol=%s interval=%s",
            idx,
            len(interval_tuple),
            str(symbol).upper(),
            interval,
        )
        try:
            res = run_baseline_suite(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                signal_types=signal_types,
                trade_side_mode=trade_side_mode,
                initial_capital=initial_capital,
                periods_per_year=periods_per_year,
                output_root=output_root,
            )
        except Exception:
            logger.exception("baseline suite failed for symbol=%s interval=%s", symbol, interval)
            continue
        results.append(res)
    return results


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run baseline skill suite and build training samples")
    parser.add_argument("--symbol", default="RB0")
    parser.add_argument("--exchange", default=None)
    parser.add_argument(
        "--top-n-symbols",
        type=int,
        default=0,
        help=(
            "if > 0, ignore --symbol and load top-N symbols from --symbols-ranking-path "
            "ordered by research_rank"
        ),
    )
    parser.add_argument(
        "--symbols-ranking-path",
        default=str(SYMBOLS_RANKING_PATH),
        help="csv path of symbol research ranking (default cta/feature/symbols_research_ranking.csv)",
    )
    parser.add_argument(
        "--interval",
        nargs="+",
        default=["60min"],
        help=(
            "one or more intervals (day/60min/30min/15min/5min/min). "
            "Accepts space-separated and comma-separated tokens; duplicates are deduped."
        ),
    )
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--end", default="2019-12-31")
    parser.add_argument("--trade-side-mode", default="both", choices=sorted(VALID_SIDE_MODES))
    parser.add_argument("--signal-types", default=",".join(BASELINE_SIGNAL_TYPES))
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--periods-per-year", type=int, default=None)
    parser.add_argument("--output-root", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _parse_args(argv)
    signal_types = tuple(s.strip() for s in str(args.signal_types).split(",") if s.strip())
    intervals = _normalize_intervals(args.interval)
    output_root = Path(args.output_root).resolve() if args.output_root else None

    top_n = int(getattr(args, "top_n_symbols", 0))
    if top_n > 0:
        symbols_to_run = _load_top_n_symbols_from_ranking(
            Path(args.symbols_ranking_path),
            top_n=top_n,
        )
        logger.info(
            "top-n symbol mode enabled: top_n=%s ranking_path=%s loaded=%s",
            top_n,
            args.symbols_ranking_path,
            [s for s, _ in symbols_to_run],
        )
    else:
        symbols_to_run = [(str(args.symbol).upper(), str(args.exchange).upper() if args.exchange else None)]

    all_results: list[BaselineSuiteRunResult] = []
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
        results = run_baseline_suite_multi(
            symbol=symbol,
            exchange=run_exchange,
            intervals=intervals,
            start_date=args.start,
            end_date=args.end,
            signal_types=signal_types,
            trade_side_mode=args.trade_side_mode,
            initial_capital=args.initial_capital,
            periods_per_year=args.periods_per_year,
            output_root=output_root,
        )
        all_results.extend(results)
        for result in results:
            logger.info("[%s] summary: %s", symbol, result.summary_path)
            logger.info("[%s] training_samples: %s", symbol, result.training_samples_path)
            logger.info("[%s] report: %s", symbol, result.report_path)
        if len(results) < len(intervals):
            logger.warning(
                "[%s] only %d/%d intervals succeeded; see logs for failures",
                symbol,
                len(results),
                len(intervals),
            )

    if not all_results:
        raise SystemExit("no baseline suite runs succeeded")


__all__ = [
    "_compute_metrics",
    "run_baseline_suite",
    "run_baseline_suite_multi",
    "_parse_args",
    "main",
]

