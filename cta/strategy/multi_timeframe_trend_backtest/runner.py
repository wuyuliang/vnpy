"""Run the daily-direction/5-minute-entry trend strategy on actual contracts."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import shlex

import numpy as np
import pandas as pd

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.data_code.futures_downloader_utils import normalize_contract_filename
from cta.strategy.brooks.scalp.config import load_config as load_scalp_config
from cta.strategy.brooks.scalp.metadata import (
    DEFAULT_META_ROOT,
    BlockedMetadataError as ScalpBlockedMetadataError,
)
from cta.strategy.brooks.cycle_v1.backtest.data_loader import LoadedSymbol
from cta.strategy.brooks.cycle_v1.backtest.execution_metadata import (
    BACKTEST_GATEWAY,
    build_execution_metadata_store,
)
from cta.strategy.brooks.cycle_v1.backtest.market_data_update import (
    DEFAULT_DAY_ROOT,
    DEFAULT_RANKING_CSV,
)
from cta.strategy.brooks.cycle_v1.backtest.replay import (
    build_metadata_coverage_requests,
)
from cta.strategy.brooks.cycle_v1.backtest.runner import (
    DEFAULT_DATA_ROOT,
    DEFAULT_METADATA_CACHE_ROOT,
    DEFAULT_VENDOR_CACHE_ROOT,
    prepare_backtest_metadata,
    prepare_backtest_symbols,
)
from cta.strategy.brooks.cycle_v1.instruments.metadata import (
    BlockedMetadataError,
)
from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSpec,
    aggregate_completed_bars,
    aggregate_completed_daily_bars,
)
from cta.strategy.brooks.cycle_v1.legacy_adapters.scalp import (
    load_normalized_symbol,
)
from cta.strategy.multi_timeframe_trend_rules import (
    build_daily_context,
    build_intraday_context,
)
from cta.strategy.multi_timeframe_trend_strategy import (
    CANDIDATE_COLUMNS,
    InstrumentSpec,
    generate_multi_timeframe_candidates,
)

from .charts import render_opportunity_charts
from .engine import (
    DAILY_EQUITY_COLUMNS,
    EXIT_LEG_COLUMNS,
    FILL_COLUMNS,
    ORDER_COLUMNS,
    PLAN_COLUMNS,
    REJECTION_COLUMNS,
    SCALING_EVENT_COLUMNS,
    TRADE_COLUMNS,
    PortfolioReplayInput,
    ReplayArtifacts,
    replay_trend_portfolio,
)
from cta.data_code.build_symbol_turnover import (
    DEFAULT_OUTPUT as DEFAULT_TURNOVER_TABLE,
    load_turnover_table,
)
from .report import publish_backtest_report


DEFAULT_OUTPUT_ROOT = Path("cta/strategy/report/multi_timeframe_trend")
REPO_ROOT = Path(__file__).resolve().parents[3]
WARMUP_CALENDAR_DAYS = 120


class _TrackDownloadOption(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        provided = set(getattr(namespace, "_download_options_provided", ()))
        provided.add(self.dest)
        namespace._download_options_provided = provided


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=[])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--initial-equity", type=float, default=1_000_000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.01)
    parser.add_argument("--max-concurrent-positions", type=int, default=5)
    parser.add_argument("--intraday-margin-utilization", type=float, default=0.60)
    parser.add_argument("--overnight-margin-utilization", type=float, default=0.20)
    parser.add_argument("--max-symbol-margin-utilization", type=float, default=0.40)
    parser.add_argument("--overnight-reduction-minutes", type=int, default=10)
    parser.add_argument("--meta-root", default=str(DEFAULT_META_ROOT))
    parser.add_argument(
        "--metadata-cache-root",
        default=str(DEFAULT_METADATA_CACHE_ROOT),
        action=_TrackDownloadOption,
    )
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument(
        "--vendor-cache-root",
        default=str(DEFAULT_VENDOR_CACHE_ROOT),
        help=(
            "Directory holding the durable per-fetch vendor metadata cache. "
            "Pass an empty string to disable caching and always hit the vendor."
        ),
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--metadata-warmup-days",
        type=int,
        default=30,
        help=(
            "Extend the metadata window this many trading days before --start, "
            "so the daily warmup has execution metadata. 0 disables the extension."
        ),
    )
    parser.add_argument(
        "--no-auto-metadata",
        dest="auto_metadata",
        action="store_false",
        help=(
            "Do not build missing execution metadata automatically. Without this "
            "the run prepares metadata from the vendor whenever it is missing, "
            "even when --download-minute-data is not set."
        ),
    )
    parser.set_defaults(auto_metadata=True)
    parser.add_argument(
        "--include-top-turnover",
        type=float,
        default=0.0,
        help=(
            "Union into the selected universe every root covering this share of "
            "recent turnover, so a liquid symbol the research ranking places "
            "below --top-n is still traded. 0 disables. Needs --turnover-table."
        ),
    )
    parser.add_argument(
        "--turnover-table",
        default=str(DEFAULT_TURNOVER_TABLE),
        help=(
            "Per-root daily turnover table built by "
            "cta.data_code.build_symbol_turnover, used by the turnover-share "
            "universe gate. Ignored when turnover_share_threshold is 0."
        ),
    )
    parser.add_argument(
        "--config-override",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "Override one MultiTimeframeTrendConfig field, repeatable. "
            "Example: --config-override pre_break_protection_enabled=false"
        ),
    )
    parser.add_argument("--download-minute-data", action="store_true")
    parser.add_argument("--top-n", type=int, default=0, action=_TrackDownloadOption)
    parser.add_argument("--include-ema-eligible", action="store_true")
    parser.add_argument(
        "--ranking-csv",
        default=str(DEFAULT_RANKING_CSV),
        action=_TrackDownloadOption,
    )
    parser.add_argument(
        "--day-root",
        default=str(DEFAULT_DAY_ROOT),
        action=_TrackDownloadOption,
    )
    parser.add_argument(
        "--download-rate-limit",
        type=int,
        default=450,
        action=_TrackDownloadOption,
    )
    parser.add_argument(
        "--download-audit-output",
        default="",
        action=_TrackDownloadOption,
    )
    return parser


def build_reproduction_command(args: argparse.Namespace) -> dict[str, object]:
    argv = [
        "python3",
        "-m",
        "cta.strategy.multi_timeframe_trend_backtest.runner",
    ]
    recorded_symbols = getattr(args, "_symbols_before_turnover_topup", None)
    if recorded_symbols is None:
        recorded_symbols = args.symbols
    if recorded_symbols:
        argv.extend(["--symbols", *[str(symbol) for symbol in recorded_symbols]])
    argv.extend([
        "--start",
        str(args.start),
        "--end",
        str(args.end),
        "--initial-equity",
        _format_cli_value(args.initial_equity),
    ])
    if float(args.risk_per_trade) != 0.01:
        argv.extend(["--risk-per-trade", _format_cli_value(args.risk_per_trade)])
    for name, default, option in (
        ("max_concurrent_positions", 5, "--max-concurrent-positions"),
        ("intraday_margin_utilization", 0.60, "--intraday-margin-utilization"),
        ("overnight_margin_utilization", 0.20, "--overnight-margin-utilization"),
        ("max_symbol_margin_utilization", 0.40, "--max-symbol-margin-utilization"),
        ("overnight_reduction_minutes", 10, "--overnight-reduction-minutes"),
    ):
        value = getattr(args, name)
        if value != default:
            argv.extend([option, _format_cli_value(value)])
    for name, default, option in (
        ("meta_root", str(DEFAULT_META_ROOT), "--meta-root"),
        (
            "metadata_cache_root",
            str(DEFAULT_METADATA_CACHE_ROOT),
            "--metadata-cache-root",
        ),
        ("data_root", str(DEFAULT_DATA_ROOT), "--data-root"),
        ("vendor_cache_root", str(DEFAULT_VENDOR_CACHE_ROOT), "--vendor-cache-root"),
        ("turnover_table", str(DEFAULT_TURNOVER_TABLE), "--turnover-table"),
        ("output_root", str(DEFAULT_OUTPUT_ROOT), "--output-root"),
    ):
        value = str(getattr(args, name))
        if value != default:
            argv.extend([option, value])
    if args.download_minute_data:
        argv.append("--download-minute-data")
    if args.top_n:
        argv.extend(["--top-n", str(args.top_n)])
    if args.include_ema_eligible:
        argv.append("--include-ema-eligible")
    if str(args.ranking_csv) != str(DEFAULT_RANKING_CSV):
        argv.extend(["--ranking-csv", str(args.ranking_csv)])
    if str(args.day_root) != str(DEFAULT_DAY_ROOT):
        argv.extend(["--day-root", str(args.day_root)])
    if args.download_rate_limit != 450:
        argv.extend(["--download-rate-limit", str(args.download_rate_limit)])
    if args.download_audit_output:
        argv.extend(["--download-audit-output", str(args.download_audit_output)])
    if int(getattr(args, "metadata_warmup_days", 30)) != 30:
        argv.extend(
            ["--metadata-warmup-days", str(int(args.metadata_warmup_days))]
        )
    if not bool(getattr(args, "auto_metadata", True)):
        argv.append("--no-auto-metadata")
    if float(getattr(args, "include_top_turnover", 0.0) or 0.0) > 0:
        argv.extend(
            ["--include-top-turnover", _format_cli_value(args.include_top_turnover)]
        )
    for item in getattr(args, "config_override", ()) or ():
        argv.extend(["--config-override", str(item)])
    return {
        "working_directory": str(REPO_ROOT),
        "argv": argv,
        "shell_command": f"cd {shlex.quote(str(REPO_ROOT))} && {shlex.join(argv)}",
    }


def run_from_args(args: argparse.Namespace) -> tuple[dict[str, object], Path]:
    start = _parse_date(args.start, "start")
    end = _parse_date(args.end, "end")
    if end < start:
        raise ValueError("end precedes start")
    if not math.isfinite(args.initial_equity) or args.initial_equity <= 0:
        raise ValueError("initial-equity must be finite and positive")
    config = MultiTimeframeTrendConfig(
        risk_per_trade=args.risk_per_trade,
        max_concurrent_positions=args.max_concurrent_positions,
        intraday_margin_utilization=args.intraday_margin_utilization,
        overnight_margin_utilization=args.overnight_margin_utilization,
        max_symbol_margin_utilization=args.max_symbol_margin_utilization,
        overnight_reduction_minutes=args.overnight_reduction_minutes,
    )
    config_overrides = _parse_config_overrides(args.config_override)
    if config_overrides:
        config = replace(config, **config_overrides)
    run_id = args.run_id or _default_run_id(start, end)
    if Path(run_id).name != run_id or run_id in {"", ".", ".."}:
        raise ValueError("run-id must be one plain directory name")
    warmup_start = start - timedelta(days=WARMUP_CALENDAR_DAYS)
    _extend_symbols_with_top_turnover(args, end=end)
    discovered, selected, minute_update = prepare_backtest_symbols(
        args,
        start=start,
        end=end,
    )
    effective_starts = {
        item.root_symbol: _effective_warmup_start(item.source_directory, warmup_start, start)
        for item in selected
    }
    metadata_start = min(effective_starts.values(), default=start)
    metadata_start = min(
        metadata_start,
        _trading_days_before(selected, start, int(args.metadata_warmup_days)),
    )
    metadata, metadata_update, preparation_gap = prepare_backtest_metadata(
        args,
        selected,
        start=metadata_start,
        end=end,
        allow_runtime_defaults=True,
    )
    gap_rows: list[dict[str, object]] = (
        [preparation_gap] if preparation_gap is not None else []
    )
    loaded_items: list[LoadedSymbol] = []
    if preparation_gap is None:
        for item in selected:
            try:
                loaded_items.append(
                    load_normalized_symbol(
                        symbol=item.vt_symbol,
                        start=effective_starts[item.root_symbol],
                        end=end,
                        data_root=Path(args.data_root),
                        metadata=metadata,
                        config=load_scalp_config(),
                    )
                )
            except (
                ScalpBlockedMetadataError,
                FileNotFoundError,
                OSError,
                ValueError,
            ) as exc:
                gap_rows.append(
                    {
                        "root_symbol": item.root_symbol,
                        "field": "normalized_input",
                        "reason_code": getattr(exc, "code", "BLOCKED_METADATA"),
                        "reason": str(exc),
                    }
                )

    roll_execution_bars: dict[str, pd.DataFrame] = {}
    metadata_loaded_items: list[LoadedSymbol] = []
    for loaded in loaded_items:
        try:
            roll_bars = _load_roll_execution_bars(
                loaded,
                data_root=Path(args.data_root),
            )
        except (OSError, ValueError) as exc:
            gap_rows.append(
                {
                    "root_symbol": loaded.root_symbol,
                    "field": "roll_execution_input",
                    "reason_code": "BLOCKED_METADATA",
                    "reason": str(exc),
                }
            )
            roll_bars = _empty_bars()
        roll_execution_bars[loaded.root_symbol] = roll_bars
        metadata_bars = pd.concat(
            [loaded.minute_bars, roll_bars],
            ignore_index=True,
            sort=False,
        )
        metadata_loaded_items.append(replace(loaded, minute_bars=metadata_bars))

    coverage = _empty_coverage()
    execution_store = None
    if loaded_items and not gap_rows:
        try:
            execution_store = build_execution_metadata_store(
                metadata,
                tuple(metadata_loaded_items),
                cost_stress_mult=1.0,
            )
        except ValueError as exc:
            gap_rows.append(
                {
                    "root_symbol": "ALL",
                    "field": "execution_metadata",
                    "reason_code": "BLOCKED_METADATA",
                    "reason": str(exc),
                }
            )
        if execution_store is not None:
            requests = build_metadata_coverage_requests(
                tuple(metadata_loaded_items),
                start=start,
                end=end,
            )
            coverage = execution_store.coverage_report(requests)
            for row in coverage.loc[coverage["covered"].eq(0)].itertuples(index=False):
                request = requests[int(row.request_index)]
                gap_rows.append(
                    {
                        "root_symbol": request["root_symbol"],
                        "field": row.field,
                        "reason_code": row.reason_code,
                        "reason": row.detail,
                    }
                )

    candidates = _empty_candidates()
    daily_actual = _empty_bars()
    hourly_actual = _empty_bars()
    five_actual = _empty_bars()
    portfolio_inputs: list[PortfolioReplayInput] = []
    artifacts = _empty_replay(candidates)
    if loaded_items and execution_store is not None:
        candidate_frames: list[pd.DataFrame] = []
        daily_frames: list[pd.DataFrame] = []
        hourly_frames: list[pd.DataFrame] = []
        five_frames: list[pd.DataFrame] = []
        for loaded in loaded_items:
            (
                symbol_candidates,
                symbol_daily,
                symbol_five,
                symbol_minute,
                symbol_context,
                symbol_daily_context,
            ) = _prepare_strategy_data(
                loaded,
                metadata_store=execution_store,
                config=config,
                start=start,
                end=end,
                initial_equity=float(args.initial_equity),
            )
            candidate_frames.append(symbol_candidates)
            daily_frames.append(symbol_daily.assign(symbol=loaded.root_symbol))
            hourly_frames.append(
                _sort_aggregated_bars(
                    aggregate_completed_bars(
                        symbol_minute,
                        minutes=60,
                        sessions=loaded.sessions,
                    )
                ).assign(symbol=loaded.root_symbol)
            )
            five_frames.append(symbol_five.assign(symbol=loaded.root_symbol))
            portfolio_inputs.append(
                PortfolioReplayInput(
                    root_symbol=loaded.root_symbol,
                    exchange=loaded.exchange,
                    minute_bars=loaded.minute_bars,
                    roll_execution_bars=roll_execution_bars[loaded.root_symbol],
                    five_minute_context=symbol_context,
                    candidates=symbol_candidates,
                    sessions=loaded.sessions,
                    daily_context=symbol_daily_context,
                )
            )
        candidates = pd.concat(candidate_frames, ignore_index=True)
        daily_actual = pd.concat(daily_frames, ignore_index=True)
        hourly_actual = pd.concat(hourly_frames, ignore_index=True)
        five_actual = pd.concat(five_frames, ignore_index=True)
        artifacts = _empty_replay(candidates)
        if not gap_rows:
            try:
                artifacts = replay_trend_portfolio(
                    inputs=tuple(portfolio_inputs),
                    metadata_store=execution_store,
                    config=config,
                    start=start,
                    end=end,
                    initial_equity=float(args.initial_equity),
                    turnover_table=load_turnover_table(args.turnover_table),
                )
            except BlockedMetadataError as exc:
                artifacts = _blocked_replay(candidates, detail=str(exc))
                gap_rows.append(
                    {
                        "root_symbol": "ALL",
                        "field": "roll_execution",
                        "reason_code": exc.reason_code,
                        "reason": str(exc),
                    }
                )

    reproduction = build_reproduction_command(args)
    context = {
        "strategy": "multi_timeframe_trend",
        "strategy_version": "daily-ema5-10-20_5m-v1",
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "effective_warmup_starts": {
            key: value.isoformat() for key, value in effective_starts.items()
        },
        "requested_symbols": [item.vt_symbol for item in selected],
        "loaded_symbols": [item.vt_symbol for item in loaded_items],
        "discovered_symbol_count": len(discovered),
        "timeframes": {
            "direction": "1day",
            "entry": "5min",
            "execution": "1min",
        },
        "risk_per_trade": config.risk_per_trade,
        "candidate_blacklist": {
            "setup_types": list(config.candidate_setup_blacklist),
            "directions": list(config.candidate_direction_blacklist),
        },
        "daily_filters": {
            "always_in_long_daily_ema_gap_min_ratio": (
                config.always_in_long_daily_ema_gap_min_ratio
            ),
            "first_trend_entry_daily_breakout_buffer_ratio": (
                config.first_trend_entry_daily_breakout_buffer_ratio
            ),
        },
        "portfolio_limits": {
            "max_concurrent_positions": config.max_concurrent_positions,
            "intraday_margin_utilization": config.intraday_margin_utilization,
            "overnight_margin_utilization": config.overnight_margin_utilization,
            "max_symbol_margin_utilization": config.max_symbol_margin_utilization,
            "overnight_reduction_minutes": config.overnight_reduction_minutes,
        },
        "position_scaling_config": {
            "symbol_loss_streak": config.symbol_loss_streak,
            "symbol_position_scale": config.symbol_position_scale,
            "portfolio_drawdown_threshold": (
                config.portfolio_drawdown_threshold
            ),
            "portfolio_position_scale": config.portfolio_position_scale,
        },
        "symbol_loss_cooldown_config": {
            "enabled": config.symbol_loss_cooldown_enabled,
            "pair_window_hours": config.symbol_loss_pair_window_hours,
            "cooldown_hours": config.symbol_loss_cooldown_hours,
            "release_time": "09:00",
        },
        "minute_data_update": minute_update,
        "execution_metadata_update": metadata_update,
    }
    entry_trade_dates = _entry_trade_dates(
        artifacts.trades,
        minute_bars_by_symbol={
            item.root_symbol: item.minute_bars for item in loaded_items
        },
    )
    summary, output = publish_backtest_report(
        Path(args.output_root) / run_id,
        artifacts=artifacts,
        initial_equity=float(args.initial_equity),
        metadata_gaps=gap_rows,
        context=context,
        reproduction_command=reproduction,
        daily_market_bars=daily_actual,
        entry_trade_dates=entry_trade_dates,
        symbols=tuple(item.root_symbol for item in loaded_items),
        assumed_mechanics=tuple(metadata_update.get("assumptions", ())),
    )
    metadata_gaps = pd.DataFrame(
        gap_rows,
        columns=["root_symbol", "field", "reason_code", "reason"],
    )
    _safe_csv(metadata_gaps).to_csv(output / "metadata_gaps.csv", index=False)
    _safe_csv(coverage).to_csv(output / "metadata_coverage.csv", index=False)
    source_files = pd.DataFrame(
        [
            {
                "root_symbol": item.root_symbol,
                "vt_symbol": item.vt_symbol,
                "source_file": str(path),
            }
            for item in loaded_items
            for path in item.source_files
        ]
        + [
            {
                "root_symbol": root_symbol,
                "vt_symbol": next(
                    item.vt_symbol
                    for item in loaded_items
                    if item.root_symbol == root_symbol
                ),
                "source_file": str(path),
            }
            for root_symbol, bars in roll_execution_bars.items()
            if "_source_path" in bars
            for path in sorted(set(bars["_source_path"].astype(str)))
        ],
        columns=["root_symbol", "vt_symbol", "source_file"],
    )
    source_files.to_csv(output / "source_files.csv", index=False)
    render_opportunity_charts(
        output,
        candidates=candidates,
        daily_bars=daily_actual,
        hourly_bars=hourly_actual,
        five_minute_bars=five_actual,
        trades=artifacts.trades,
        orders=artifacts.orders,
        rejections=artifacts.rejections,
    )
    return summary, output


def _aggregate_trading_day_daily_bars(
    minute_bars: pd.DataFrame,
    *,
    sessions: tuple[SessionSpec, ...],
) -> pd.DataFrame:
    """Build one completed daily bar per exchange trading day."""
    return aggregate_completed_daily_bars(minute_bars, sessions=sessions)


def _prepare_strategy_data(
    loaded: LoadedSymbol,
    *,
    metadata_store: object,
    config: MultiTimeframeTrendConfig,
    start: date,
    end: date,
    initial_equity: float,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    minute = loaded.minute_bars.copy().sort_values("bar_end", kind="stable")
    signal_minute = minute.copy()
    for column in ("open", "high", "low", "close"):
        signal_column = f"signal_{column}"
        if signal_column not in signal_minute:
            raise ValueError(f"normalized signal data is missing {signal_column}")
        signal_minute[column] = signal_minute[signal_column]
    daily_signal = _sort_aggregated_bars(
        _aggregate_trading_day_daily_bars(
            signal_minute,
            sessions=loaded.sessions,
        )
    )
    five_signal = _sort_aggregated_bars(
        aggregate_completed_bars(
            signal_minute,
            minutes=5,
            sessions=loaded.sessions,
        )
    )
    daily_actual = _sort_aggregated_bars(
        _aggregate_trading_day_daily_bars(minute, sessions=loaded.sessions)
    )
    five_actual = _sort_aggregated_bars(
        aggregate_completed_bars(
            minute,
            minutes=5,
            sessions=loaded.sessions,
        )
    )
    daily_context = (
        build_daily_context(daily_signal, config)
        if not daily_signal.empty
        else pd.DataFrame()
    )
    if daily_signal.empty or five_signal.empty:
        return (
            _empty_candidates(),
            daily_actual,
            five_actual,
            minute,
            pd.DataFrame(columns=["bar_end", "daily_direction"]),
            daily_context,
        )
    five_signal = _attach_adjustment(five_signal, minute)
    daily_signal = _attach_adjustment(daily_signal, minute)
    five_signal = _attach_signal_bar_instruments(
        five_signal,
        loaded=loaded,
        metadata_store=metadata_store,
    )
    instrument = _representative_instrument(
        loaded,
        metadata_store=metadata_store,
        initial_equity=initial_equity,
    )
    raw_candidates = generate_multi_timeframe_candidates(
        daily_signal,
        five_signal,
        instrument=instrument,
        config=config,
        equity=initial_equity,
    )
    candidates = _execution_candidates(
        raw_candidates,
        five_signal=five_signal,
        minute=minute,
        start=start,
        end=end,
    )
    context = _management_context(
        daily_signal=daily_signal,
        five_signal=five_signal,
        five_actual=five_actual,
        config=config,
    )
    return candidates, daily_actual, five_actual, minute, context, daily_context


def _load_roll_execution_bars(
    loaded: LoadedSymbol,
    *,
    data_root: str | Path,
) -> pd.DataFrame:
    active = loaded.minute_bars.sort_values("bar_end", kind="stable").reset_index(
        drop=True
    )
    prior_contract = active["contract_code"].astype(str).shift(1)
    transitions = active.loc[
        prior_contract.notna()
        & active["contract_code"].astype(str).ne(prior_contract)
    ].copy()
    if transitions.empty:
        return _empty_bars()

    active_times = pd.DatetimeIndex(pd.to_datetime(active["bar_end"]))
    trade_dates = active.set_index("bar_end")["exchange_trade_date"]
    minute_root = Path(data_root)
    origin_root = minute_root.parent if minute_root.name == "minute" else minute_root
    rows: list[pd.Series] = []
    for transition in transitions.itertuples(index=True):
        transition_time = pd.Timestamp(transition.bar_end)
        old_contract = str(prior_contract.iloc[int(transition.Index)])
        path = (
            origin_root
            / "contract"
            / loaded.root_symbol
            / "minute"
            / f"{normalize_contract_filename(old_contract)}.parquet"
        )
        if not path.is_file():
            continue
        raw = pd.read_parquet(path)
        timestamp_column = "datetime" if "datetime" in raw else "trade_time"
        contract_column = (
            "contract_code" if "contract_code" in raw else "ts_code"
        )
        required = {
            timestamp_column,
            contract_column,
            "open",
            "high",
            "low",
            "close",
        }
        missing = sorted(required.difference(raw.columns))
        if missing:
            raise ValueError(
                f"roll execution file is missing {','.join(missing)}: {path}"
            )
        timestamps = pd.to_datetime(raw[timestamp_column], errors="raise")
        if timestamps.dt.tz is None:
            timestamps = timestamps.dt.tz_localize("Asia/Shanghai")
        else:
            timestamps = timestamps.dt.tz_convert("Asia/Shanghai")
        candidates = raw.loc[
            raw[contract_column].astype(str).str.upper().eq(old_contract)
            & timestamps.gt(transition_time)
            & timestamps.isin(active_times)
        ].copy()
        if candidates.empty:
            continue
        candidates["bar_end"] = timestamps.loc[candidates.index]
        selected = candidates.sort_values("bar_end", kind="stable").iloc[0].copy()
        selected["contract_code"] = old_contract
        selected["exchange_trade_date"] = trade_dates.loc[selected["bar_end"]]
        selected["_source_path"] = str(path)
        rows.append(selected)
    if not rows:
        return _empty_bars()
    result = pd.DataFrame(rows).reset_index(drop=True)
    numeric = ["open", "high", "low", "close"]
    result[numeric] = result[numeric].apply(pd.to_numeric, errors="raise")
    return result


def _representative_instrument(
    loaded: LoadedSymbol,
    *,
    metadata_store: object,
    initial_equity: float,
) -> InstrumentSpec:
    del initial_equity
    minute = loaded.minute_bars.sort_values("bar_end", kind="stable").reset_index(drop=True)
    if len(minute) < 2:
        raise ValueError("strategy requires at least two normalized minute bars")
    first = minute.iloc[0]
    second = minute.iloc[1]
    snapshot = metadata_store.execution_snapshot(
        root_symbol=loaded.root_symbol,
        exchange=loaded.exchange,
        contract_code=str(first["contract_code"]),
        exchange_trade_date=first["exchange_trade_date"],
        gateway=BACKTEST_GATEWAY,
        order_types=("STOP",),
        decision_asof=pd.Timestamp(first["bar_end"]).to_pydatetime(),
        order_event=pd.Timestamp(second["bar_end"]).to_pydatetime(),
    )
    cost = float(snapshot.stressed_round_trip_fee_cash) + (
        float(snapshot.stressed_round_trip_slippage_ticks)
        * float(snapshot.price_tick)
        * float(snapshot.contract_size)
    )
    metadata_asof = snapshot.instrument.known_at
    return InstrumentSpec(
        symbol=f"{loaded.root_symbol}0",
        exchange=loaded.exchange,
        contract=str(first["contract_code"]),
        tick_size=float(snapshot.price_tick),
        multiplier=float(snapshot.contract_size),
        stressed_round_trip_cost=cost,
        metadata_asof=metadata_asof,
    )


def _attach_signal_bar_instruments(
    frame: pd.DataFrame,
    *,
    loaded: LoadedSymbol,
    metadata_store: object,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    required = {"bar_end", "contract_code", "exchange_trade_date"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(
            "signal bars are missing execution metadata keys: "
            + ",".join(missing)
        )
    result = frame.copy()
    tick_sizes: list[float] = []
    multipliers: list[float] = []
    costs: list[float] = []
    metadata_asof: list[object] = []
    for row in result.itertuples(index=False):
        decision_asof = pd.Timestamp(row.bar_end).to_pydatetime()
        snapshot = metadata_store.execution_snapshot(
            root_symbol=loaded.root_symbol,
            exchange=loaded.exchange,
            contract_code=str(row.contract_code),
            exchange_trade_date=row.exchange_trade_date,
            gateway=BACKTEST_GATEWAY,
            order_types=("STOP",),
            decision_asof=decision_asof,
            order_event=decision_asof,
        )
        tick_size = float(snapshot.price_tick)
        contract_size = float(snapshot.contract_size)
        tick_sizes.append(tick_size)
        multipliers.append(contract_size)
        costs.append(
            float(snapshot.stressed_round_trip_fee_cash)
            + float(snapshot.stressed_round_trip_slippage_ticks)
            * tick_size
            * contract_size
        )
        metadata_asof.append(snapshot.instrument.known_at)
    result["instrument_tick_size"] = tick_sizes
    result["instrument_multiplier"] = multipliers
    result["instrument_stressed_round_trip_cost"] = costs
    result["instrument_metadata_asof"] = metadata_asof
    return result


def _attach_adjustment(aggregated: pd.DataFrame, minute: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "bar_end",
        "adjustment_scale",
        "adjustment_offset",
        "adjustment_version",
    ]
    missing = sorted(set(columns).difference(minute.columns))
    if missing:
        raise ValueError("normalized adjustment data is missing: " + ",".join(missing))
    lookup = minute.loc[:, columns].drop_duplicates("bar_end", keep="last")
    result = aggregated.merge(lookup, on="bar_end", how="left", validate="one_to_one")
    if result[["adjustment_scale", "adjustment_offset"]].isna().any().any():
        raise ValueError("aggregated bars are missing point-in-time adjustment")
    return result


def _execution_candidates(
    candidates: pd.DataFrame,
    *,
    five_signal: pd.DataFrame,
    minute: pd.DataFrame,
    start: date,
    end: date,
) -> pd.DataFrame:
    if candidates.empty:
        return _empty_candidates()
    five = five_signal.sort_values("bar_end", kind="stable").reset_index(drop=True)
    five_by_time = five.set_index("bar_end", drop=False)
    minute = minute.sort_values("bar_end", kind="stable").reset_index(drop=True)
    minute_times = pd.DatetimeIndex(pd.to_datetime(minute["bar_end"]))
    minute_by_time = minute.set_index("bar_end", drop=False)
    rows: list[dict[str, object]] = []
    for raw in candidates.to_dict("records"):
        signal_time = pd.Timestamp(raw["signal_datetime"])
        if signal_time not in five_by_time.index or signal_time not in minute_by_time.index:
            raise ValueError("candidate signal has no actual-contract mapping")
        five_row = five_by_time.loc[signal_time]
        minute_row = minute_by_time.loc[signal_time]
        trade_date = minute_row["exchange_trade_date"]
        if not start <= trade_date <= end:
            continue
        scale = float(five_row["adjustment_scale"])
        offset = float(five_row["adjustment_offset"])
        if not math.isfinite(scale) or scale <= 0 or not math.isfinite(offset):
            raise ValueError("candidate adjustment is invalid")

        def raw_price(
            value: object,
            adjustment_offset: float = offset,
            adjustment_scale: float = scale,
        ) -> float:
            if value is None or pd.isna(value):
                return math.nan
            return (float(value) - adjustment_offset) / adjustment_scale

        active_position = int(minute_times.searchsorted(signal_time, side="right"))
        reason = str(raw.get("filtered_reason", "") or "")
        deferred_risk = reason == "RISK_BELOW_ONE_LOT"
        if deferred_risk:
            reason = ""
        if active_position >= len(minute):
            active_time = signal_time
            reason = reason or "NO_NEXT_EVENT"
        else:
            active_time = pd.Timestamp(minute.iloc[active_position]["bar_end"])
        expiry_index = int(raw["order_expire_bar_i"])
        if expiry_index >= len(five):
            expires_at = pd.Timestamp(minute.iloc[-1]["bar_end"])
        else:
            expires_at = pd.Timestamp(five.iloc[expiry_index]["bar_end"])
        row = dict(raw)
        row.update(
            {
                "candidate_id": f"{str(raw.get('symbol', 'AG0')).rstrip('0')}-{len(rows) + 1:06d}",
                "symbol": str(raw.get("symbol", "")).rstrip("0"),
                "contract_code": str(minute_row["contract_code"]),
                "contract": str(minute_row["contract_code"]),
                "exchange_trade_date": trade_date,
                "setup": raw["setup_type"],
                "direction_label": raw.get("direction", ""),
                "direction": int(raw["direction_value"]),
                "signal_time": signal_time,
                "active_time": active_time,
                "expires_at": expires_at,
                "signal_trigger": raw["trigger"],
                "signal_stop_price": raw["stop_price"],
                "signal_target_price": raw.get("target_price_virtual", math.nan),
                "signal_prior_5d_high": raw["prior_5d_high"],
                "trigger": raw_price(raw["trigger"]),
                "stop_price": raw_price(raw["stop_price"]),
                "prior_5d_high": raw_price(raw["prior_5d_high"]),
                "target_price": math.nan,
                "target_price_virtual": raw_price(
                    raw.get("target_price_virtual", math.nan)
                ),
                "entry": raw_price(raw["trigger"]),
                "stop": raw_price(raw["stop_price"]),
                "target": raw_price(raw.get("target_price_virtual", math.nan)),
                "adjustment_scale": scale,
                "adjustment_offset": offset,
                "adjustment_version": five_row["adjustment_version"],
                "filtered_reason": reason,
                "candidate_status": "filtered" if reason else "not_triggered",
            }
        )
        if deferred_risk:
            row.update(
                {
                    "sample_status": "not_triggered_market",
                    "is_filtered": 0,
                    "rejection_code": "",
                    "quantity": math.nan,
                }
            )
        rows.append(row)
    if not rows:
        return _empty_candidates()
    return pd.DataFrame(rows)


def _management_context(
    *,
    daily_signal: pd.DataFrame,
    five_signal: pd.DataFrame,
    five_actual: pd.DataFrame,
    config: MultiTimeframeTrendConfig,
) -> pd.DataFrame:
    daily = build_daily_context(daily_signal, config)
    intraday = build_intraday_context(five_signal, config)
    higher = daily.loc[
        :,
        ["bar_end", "daily_direction"],
    ].rename(columns={"bar_end": "daily_feature_asof"})
    context = pd.merge_asof(
        intraday.sort_values("bar_end"),
        higher.sort_values("daily_feature_asof"),
        left_on="bar_end",
        right_on="daily_feature_asof",
        direction="backward",
        allow_exact_matches=True,
    )
    context["daily_direction"] = context["daily_direction"].fillna(0).astype(int)
    actual = five_actual.loc[
        :, ["bar_end", "open", "high", "low", "close"]
    ].rename(columns={name: f"actual_{name}" for name in ("open", "high", "low", "close")})
    context = context.merge(actual, on="bar_end", how="left", validate="one_to_one")
    for column in ("open", "high", "low", "close"):
        context[column] = context.pop(f"actual_{column}")
    scale = pd.to_numeric(context["adjustment_scale"], errors="raise")
    offset = pd.to_numeric(context["adjustment_offset"], errors="raise")
    context["atr14"] = pd.to_numeric(context["atr14"], errors="coerce") / scale
    for kind in ("high", "low"):
        column = f"latest_swing_{kind}"
        context[column] = (pd.to_numeric(context[column], errors="coerce") - offset) / scale
    return context


def _empty_candidates() -> pd.DataFrame:
    columns = list(CANDIDATE_COLUMNS) + [
        "candidate_id",
        "contract_code",
        "exchange_trade_date",
        "setup",
        "direction_label",
        "signal_time",
        "active_time",
        "expires_at",
        "entry",
        "stop",
        "target",
        "signal_prior_5d_high",
        "adjustment_scale",
        "adjustment_offset",
        "adjustment_version",
    ]
    return pd.DataFrame(columns=list(dict.fromkeys(columns)))


def _empty_replay(candidates: pd.DataFrame) -> ReplayArtifacts:
    return ReplayArtifacts(
        candidates=candidates,
        plans=pd.DataFrame(columns=PLAN_COLUMNS),
        orders=pd.DataFrame(columns=ORDER_COLUMNS),
        fills=pd.DataFrame(columns=FILL_COLUMNS),
        exit_legs=pd.DataFrame(columns=EXIT_LEG_COLUMNS),
        trades=pd.DataFrame(columns=TRADE_COLUMNS),
        daily_equity=pd.DataFrame(columns=DAILY_EQUITY_COLUMNS),
        rejections=pd.DataFrame(columns=REJECTION_COLUMNS),
        position_scaling_events=pd.DataFrame(columns=SCALING_EVENT_COLUMNS),
    )


def _blocked_replay(
    candidates: pd.DataFrame,
    *,
    detail: str,
) -> ReplayArtifacts:
    artifacts = _empty_replay(candidates)
    rows = []
    for candidate in candidates.to_dict("records"):
        if str(candidate.get("filtered_reason", "") or "").strip():
            continue
        rows.append(
            {
                "root_symbol": str(candidate.get("symbol", "")),
                "feature_asof": candidate.get(
                    "signal_time", candidate.get("signal_datetime", pd.NaT)
                ),
                "reason_code": "BLOCKED_METADATA",
                "candidate_id": str(candidate["candidate_id"]),
                "risk_budget": math.nan,
                "loss_per_lot": math.nan,
                "detail": detail,
            }
        )
    return ReplayArtifacts(
        candidates=artifacts.candidates,
        plans=artifacts.plans,
        orders=artifacts.orders,
        fills=artifacts.fills,
        exit_legs=artifacts.exit_legs,
        trades=artifacts.trades,
        daily_equity=artifacts.daily_equity,
        rejections=pd.DataFrame(rows, columns=REJECTION_COLUMNS),
        position_scaling_events=artifacts.position_scaling_events,
    )


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "bar_end", "open", "high", "low", "close", "volume",
            "open_interest", "contract_code", "exchange_trade_date",
        ]
    )


def _empty_coverage() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "request_index", "contract_code", "exchange_trade_date",
            "decision_asof", "order_event", "field", "covered",
            "reason_code", "detail",
        ]
    )


def _entry_trade_dates(
    trades: pd.DataFrame,
    *,
    minute_bars_by_symbol: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Map each actual entry minute to its exchange trading date."""
    columns = ["candidate_id", "exchange_trade_date"]
    if trades.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    for symbol, symbol_trades in trades.groupby("symbol", sort=False):
        bars = minute_bars_by_symbol.get(str(symbol))
        if bars is None:
            raise ValueError(f"entry minute bars are missing for {symbol}")
        required = {"bar_end", "exchange_trade_date"}
        missing = sorted(required.difference(bars.columns))
        if missing:
            raise ValueError("entry minute bars are missing: " + ",".join(missing))
        lookup = bars.loc[:, ["bar_end", "exchange_trade_date"]].copy()
        lookup["bar_end"] = pd.to_datetime(lookup["bar_end"], errors="raise")
        if lookup["bar_end"].duplicated().any():
            raise ValueError(f"entry minute bars are duplicated for {symbol}")
        trade_dates = lookup.set_index("bar_end")["exchange_trade_date"]
        for trade in symbol_trades.to_dict("records"):
            entry_time = pd.Timestamp(trade["entry_time"])
            if entry_time not in trade_dates.index:
                raise ValueError(
                    f"entry minute is missing for {trade['candidate_id']}"
                )
            rows.append(
                {
                    "candidate_id": trade["candidate_id"],
                    "exchange_trade_date": trade_dates.loc[entry_time],
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _sort_aggregated_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Restore event order after the session-aware aggregator groups by segment."""
    if "bar_end" not in frame:
        raise ValueError("aggregated bars require bar_end")
    return frame.sort_values("bar_end", kind="stable").reset_index(drop=True)


def _parse_config_overrides(items: Sequence[str]) -> dict[str, object]:
    """Parse ``NAME=VALUE`` overrides against the config dataclass fields.

    Only declared fields are accepted and each value is coerced to the field's
    declared type, so a typo or a wrong type fails loudly at parse time instead
    of silently producing a run with the default behaviour.
    """
    if not items:
        return {}
    declared = {field.name: field for field in fields(MultiTimeframeTrendConfig)}
    parsed: dict[str, object] = {}
    for item in items:
        text = str(item)
        if "=" not in text:
            raise ValueError(f"config-override must be NAME=VALUE, got {text!r}")
        name, _, raw = text.partition("=")
        name = name.strip()
        raw = raw.strip()
        if name not in declared:
            raise ValueError(f"unknown config field: {name}")
        if name in parsed:
            raise ValueError(f"duplicate config-override for {name}")
        annotation = str(declared[name].type)
        if "bool" in annotation:
            lowered = raw.lower()
            if lowered in {"true", "1", "yes", "on"}:
                parsed[name] = True
            elif lowered in {"false", "0", "no", "off"}:
                parsed[name] = False
            else:
                raise ValueError(f"{name} expects a boolean, got {raw!r}")
        elif "int" in annotation and "float" not in annotation:
            parsed[name] = int(raw)
        elif "float" in annotation:
            parsed[name] = float(raw)
        else:
            raise ValueError(
                f"{name} is not overridable from the command line "
                "(only bool, int and float fields are)"
            )
    return parsed


def _extend_symbols_with_top_turnover(
    args: argparse.Namespace,
    *,
    end: date,
) -> None:
    """Add liquid roots the research ranking ranks below ``--top-n``.

    ``--top-n`` orders by the hand-maintained ``research_rank`` column, which
    does not track turnover: SN, LC, JD, LH, NI and AO all sit inside the top
    80% of traded value while ranking outside the top 40. This unions those
    roots into the explicit symbol list before selection, so they are downloaded
    and traded like any other requested symbol.
    """
    share = float(getattr(args, "include_top_turnover", 0.0) or 0.0)
    if share <= 0:
        return
    if not 0 < share <= 1:
        raise ValueError("include-top-turnover must be in (0, 1]")
    table = load_turnover_table(args.turnover_table)
    if table.empty:
        # 静默跳过会让人以为补池生效了，实际什么都没发生
        raise ValueError(
            "include-top-turnover needs a turnover table; "
            f"{args.turnover_table!r} is missing or empty. Build it with: "
            "python3 -m cta.data_code.build_symbol_turnover --start <YYYY-MM-DD> "
            "--end <YYYY-MM-DD>"
        )
    frame = table.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"])
    frame = frame[frame["trade_date"].dt.date <= end]
    if frame.empty:
        raise ValueError(
            f"turnover table {args.turnover_table!r} has no rows on or before "
            f"{end.isoformat()}; rebuild it to cover the backtest window"
        )
    recent = sorted(frame["trade_date"].unique())[-20:]
    weights = (
        frame[frame["trade_date"].isin(recent)]
        .groupby(frame["root_symbol"].astype(str).str.upper())["turnover"]
        .sum()
        .sort_values(ascending=False)
    )
    weights = weights[weights > 0]
    if weights.empty:
        return
    cumulative = weights.cumsum() / weights.sum()
    keep = int((cumulative < share).sum()) + 1
    existing = {
        str(token).upper().split(".")[0].rstrip("0")
        for token in getattr(args, "symbols", ()) or ()
    }
    added = [root for root in weights.index[:keep] if root not in existing]
    if added:
        # 命令重建要用原始值：把补进来的品种 materialize 进 --symbols 会让
        # RUN_COMMAND.sh 既看不出用过补池，重跑时也未必得到同一个池子
        if not hasattr(args, "_symbols_before_turnover_topup"):
            args._symbols_before_turnover_topup = list(
                getattr(args, "symbols", ()) or ()
            )
        args.symbols = list(getattr(args, "symbols", ()) or ()) + added
        args._turnover_topup_added = list(added)


def _trading_days_before(
    selected: Sequence[Any],
    start: date,
    trading_days: int,
) -> date:
    """Return the date ``trading_days`` sessions before ``start``.

    Sessions are counted from the local minute partitions, which are one file
    per traded date. With no partitions to count, fall back to a calendar
    estimate rather than silently leaving the window unextended.
    """
    if trading_days <= 0:
        return start
    dates: set[date] = set()
    for item in selected:
        directory = getattr(item, "source_directory", None)
        if directory is None:
            continue
        for path in Path(directory).glob("*.parquet"):
            try:
                value = pd.Timestamp(path.stem).date()
            except ValueError:
                continue
            if value < start:
                dates.add(value)
    ordered = sorted(dates)
    if len(ordered) >= trading_days:
        return ordered[-trading_days]
    return start - timedelta(days=int(trading_days * 1.5) + 7)


def _effective_warmup_start(directory: Path, warmup: date, requested: date) -> date:
    partition_dates: list[date] = []
    for path in directory.glob("*.parquet"):
        try:
            partition_dates.append(pd.Timestamp(path.stem).date())
        except ValueError:
            continue
    return min(max(warmup, min(partition_dates, default=warmup)), requested)


def _parse_date(value: str, label: str) -> date:
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a valid date") from exc


def _default_run_id(start: date, end: date) -> str:
    return (
        f"{datetime.now():%Y%m%d_%H%M%S_%f}_"
        f"{start:%Y%m%d}_{end:%Y%m%d}_1d_5m_1m"
    )


def _format_cli_value(value: object) -> str:
    return format(value, "g") if isinstance(value, float) else str(value)


def _safe_csv(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.copy().replace([np.inf, -np.inf], np.nan)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary, output = run_from_args(args)
    except (
        ScalpBlockedMetadataError,
        BlockedMetadataError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": getattr(exc, "reason_code", "INVALID_ARGUMENT"),
                    "reason": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": summary["requested_interval_status"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_parser",
    "build_reproduction_command",
    "main",
    "run_from_args",
]
