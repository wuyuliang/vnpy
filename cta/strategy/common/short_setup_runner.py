"""Strategy-neutral backtest plumbing for intraday single-timeframe setups.

Everything here is the same for any setup that (a) reads one intraday
timeframe, (b) emits candidates on the shared ``CANDIDATE_COLUMNS`` contract,
and (c) executes on one-minute bars. A concrete strategy supplies its config,
its candidate generator and its feature builder; it should not be re-deriving
execution snapshots or re-implementing aggregation carry-over.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import json
from typing import Any, Callable

import pandas as pd

from cta.strategy.brooks.cycle_v1.backtest.execution_metadata import BACKTEST_GATEWAY
from cta.strategy.multi_timeframe_trend_backtest import runner as shared_runner

from .setup_pipeline import SetupInstrument

INSTRUMENT_CARRY_COLUMNS = (
    "contract_code", "exchange_trade_date",
    "adjustment_scale", "adjustment_offset",
    "tick_size", "multiplier", "margin_rate",
    "stressed_round_trip_cost", "metadata_asof",
)


def build_parser(*, module_path: str, output_root, config_name: str, description: str):
    """The shared parser, re-pointed at one strategy's module and defaults."""
    parser = shared_runner.build_parser()
    parser.description = description
    parser.set_defaults(risk_per_trade=0.005, output_root=str(output_root))
    for action in parser._actions:
        if action.dest == "config_override":
            action.help = (
                f"Override one {config_name} field, repeatable. "
                "Example: --config-override entry_volume_mult=1.5"
            )
    parser.set_defaults(_module_path=module_path)
    return parser


def build_reproduction_command(args, *, module_path: str):
    command = shared_runner.build_reproduction_command(args)
    argv = list(command["argv"])
    argv[2] = module_path
    command["argv"] = argv
    command["shell_command"] = (
        f"cd {shared_runner.shlex.quote(str(shared_runner.REPO_ROOT))} && "
        + shared_runner.shlex.join(argv)
    )
    return command


def default_run_id(args) -> str:
    start = shared_runner._parse_date(args.start, "start")
    end = shared_runner._parse_date(args.end, "end")
    return f"{datetime.now():%Y%m%d_%H%M%S_%f}_{start:%Y%m%d}_{end:%Y%m%d}_1m"


def signal_frame_from_minutes(minute: pd.DataFrame) -> pd.DataFrame:
    """Swap in the back-adjusted signal prices when the loader supplies them."""
    signal = minute.copy()
    for column in ("open", "high", "low", "close"):
        adjusted = f"signal_{column}"
        if adjusted in signal:
            signal[column] = signal[adjusted]
    return signal


def attach_execution_instruments(frame, *, loaded, metadata_store) -> pd.DataFrame:
    """Add per-bar tick size, multiplier, stressed cost and margin rate."""
    result = frame.copy()
    snapshots: dict[tuple[Any, Any], Any] = {}
    for key, group in result.groupby(
        ["contract_code", "exchange_trade_date"], sort=False, dropna=False
    ):
        first = group.iloc[0]
        timestamp = pd.Timestamp(first["bar_end"])
        snapshots[key] = metadata_store.execution_snapshot(
            root_symbol=loaded.root_symbol,
            exchange=loaded.exchange,
            contract_code=str(first["contract_code"]),
            exchange_trade_date=first["exchange_trade_date"],
            gateway=BACKTEST_GATEWAY,
            order_types=("STOP",),
            decision_asof=timestamp.to_pydatetime(),
            order_event=timestamp.to_pydatetime(),
        )
    values = [
        snapshots[(row.contract_code, row.exchange_trade_date)]
        for row in result.itertuples()
    ]
    result["instrument_tick_size"] = [float(item.price_tick) for item in values]
    result["instrument_multiplier"] = [float(item.contract_size) for item in values]
    result["instrument_stressed_round_trip_cost"] = [
        float(item.stressed_round_trip_fee_cash)
        + float(item.stressed_round_trip_slippage_ticks)
        * float(item.price_tick)
        * float(item.contract_size)
        for item in values
    ]
    result["instrument_metadata_asof"] = [item.instrument.known_at for item in values]
    result["instrument_margin_rate_short"] = [
        float(item.daily.margin_rate_short) for item in values
    ]
    result["symbol"] = loaded.root_symbol
    result["exchange"] = loaded.exchange
    return result


def representative_instrument(loaded, *, metadata_store) -> SetupInstrument:
    """A fallback instrument for rows whose per-bar snapshot is missing."""
    minute = loaded.minute_bars.sort_values("bar_end", kind="stable").reset_index(
        drop=True
    )
    if len(minute) < 2:
        raise ValueError("strategy requires at least two normalized minute bars")
    first, second = minute.iloc[0], minute.iloc[1]
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
    return SetupInstrument(
        symbol=loaded.root_symbol,
        exchange=loaded.exchange,
        contract=str(first["contract_code"]),
        tick_size=float(snapshot.price_tick),
        multiplier=float(snapshot.contract_size),
        stressed_round_trip_cost=cost,
        metadata_asof=snapshot.instrument.known_at,
        margin_rate=float(snapshot.daily.margin_rate_short),
    )


def signal_timeframe_bars(signal, *, sessions, minutes: int, aggregation_cache):
    """Aggregate to ``minutes`` completed bars, carrying instrument columns.

    Returns the input unchanged at one minute, so the default path costs
    nothing. The shared aggregator only emits a bar once its whole segment
    slice is complete, so a partial bar at a session break never signals.
    """
    minutes = int(minutes)
    if minutes <= 1:
        return signal
    if aggregation_cache is None:
        aggregated = shared_runner.aggregate_completed_bars(
            signal, minutes=minutes, sessions=sessions
        )
    else:
        aggregated = shared_runner.cached_aggregate_completed_bars(
            aggregation_cache, signal, minutes=minutes, sessions=sessions,
            compute=shared_runner.aggregate_completed_bars,
        )
    aggregated = shared_runner._sort_aggregated_bars(aggregated)
    # 聚合只保留 OHLCV，合约标识和复权系数要按 bar_end 贴回来
    carry = [
        name for name in INSTRUMENT_CARRY_COLUMNS
        if name in signal.columns and name not in aggregated.columns
    ]
    carry += [
        name for name in signal.columns
        if name.startswith("instrument_") and name not in aggregated.columns
    ]
    if not carry:
        return aggregated
    return pd.merge_asof(
        aggregated.sort_values("bar_end", kind="stable"),
        signal.loc[:, ["bar_end", *carry]].sort_values("bar_end", kind="stable"),
        on="bar_end",
        direction="backward",
    )


def replay_context(features: pd.DataFrame, signal: pd.DataFrame) -> pd.DataFrame:
    """Per-minute ATR in actual-contract prices, for the replay engine."""
    context = features.loc[:, ["bar_end", "atr"]].copy()
    scale = pd.to_numeric(signal["adjustment_scale"], errors="coerce")
    context["atr"] = pd.to_numeric(context["atr"], errors="coerce") / scale
    return context


def aggregated_review_bars(minute, *, sessions, aggregation_cache):
    """Daily and five-minute bars used by the report and the charts."""
    daily = shared_runner._aggregate_trading_day_daily_bars(
        minute, sessions=sessions, aggregation_cache=aggregation_cache
    )
    if aggregation_cache is None:
        five = shared_runner.aggregate_completed_bars(
            minute, minutes=5, sessions=sessions
        )
    else:
        five = shared_runner.cached_aggregate_completed_bars(
            aggregation_cache, minute, minutes=5, sessions=sessions,
            compute=shared_runner.aggregate_completed_bars,
        )
    return (
        shared_runner._sort_aggregated_bars(daily),
        shared_runner._sort_aggregated_bars(five),
    )


def capital_basis_summary(candidates: pd.DataFrame) -> dict[str, dict[str, int]]:
    if candidates.empty or "capital_basis" not in candidates:
        return {}
    result: dict[str, dict[str, int]] = {}
    for basis, rows in candidates.groupby("capital_basis", sort=True):
        eligible = rows["filtered_reason"].fillna("").astype(str).eq("")
        quantity = pd.to_numeric(
            rows.loc[eligible, "quantity"], errors="coerce"
        ).fillna(0)
        result[str(basis)] = {
            "candidate_count": int(len(rows)),
            "eligible_count": int(eligible.sum()),
            "quantity": int(quantity.sum()),
        }
    return result


def build_run_context(values, *, strategy: str, strategy_version: str, entry_tf: str):
    args, config = values["args"], values["config"]
    return {
        "strategy": strategy,
        "strategy_version": strategy_version,
        "requested_start": values["start"].isoformat(),
        "requested_end": values["end"].isoformat(),
        "warmup_start": values["warmup_start"].isoformat(),
        "effective_warmup_starts": {
            key: value.isoformat()
            for key, value in values["effective_starts"].items()
        },
        "requested_symbols": [item.vt_symbol for item in values["selected"]],
        "loaded_symbols": [item.vt_symbol for item in values["loaded_items"]],
        "discovered_symbol_count": len(values["discovered"]),
        "timeframes": {
            "direction": entry_tf, "entry": entry_tf, "execution": "1min",
        },
        "risk_per_trade": config.max_risk_pct,
        "risk_band": {
            "minimum": config.min_risk_pct,
            "maximum": config.max_risk_pct,
            "max_capital_share": config.max_capital_share,
        },
        "capital_basis": capital_basis_summary(values["candidates"]),
        "portfolio_limits": {
            "max_concurrent_positions": config.max_concurrent_positions,
            "intraday_margin_utilization": config.intraday_margin_utilization,
            "overnight_margin_utilization": config.overnight_margin_utilization,
            "max_symbol_margin_utilization": config.max_symbol_margin_utilization,
            "overnight_reduction_minutes": config.overnight_reduction_minutes,
        },
        "aggregation_cache": values["aggregation_cache"].stats,
        "gate_fail_open": values["gate_diagnostics"].fail_open_counts(),
        "gate_evaluations": values["gate_diagnostics"].evaluation_counts(),
        "minute_data_update": values["minute_update"],
        "execution_metadata_update": values["metadata_update"],
        "config": {
            field.name: getattr(config, field.name)
            for field in shared_runner.fields(config)
        },
        "output_root": str(args.output_root),
    }


def cli_main(parser_factory: Callable[[], Any], runner: Callable[[Any], Any],
             argv: Sequence[str] | None = None) -> int:
    """Parse, run, and print the one-line JSON status every runner shares."""
    args = parser_factory().parse_args(argv)
    try:
        summary, output = runner(args)
    except (
        shared_runner.ScalpBlockedMetadataError,
        shared_runner.BlockedMetadataError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps(
            {"status": getattr(exc, "reason_code", "INVALID_ARGUMENT"),
             "reason": str(exc)},
            ensure_ascii=False,
        ))
        return 2
    print(json.dumps(
        {"status": summary["requested_interval_status"], "output": str(output)},
        ensure_ascii=False,
    ))
    return 0
