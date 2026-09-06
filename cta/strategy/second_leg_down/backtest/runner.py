"""Run the daily-trend/5-minute Second Leg Down strategy on actual contracts."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
import json
import math
from pathlib import Path

import pandas as pd

from cta.strategy.brooks.cycle_v1.backtest.execution_metadata import BACKTEST_GATEWAY
from cta.strategy.multi_timeframe_trend_backtest import runner as shared_runner
from cta.strategy.second_leg_down.config import SecondLegDownConfig
from cta.strategy.second_leg_down.rules import build_second_leg_features
from cta.strategy.second_leg_down.strategy import (
    SecondLegInstrument,
    generate_second_leg_down_candidates,
)


DEFAULT_OUTPUT_ROOT = Path("cta/strategy/report/second_leg_down")


def build_parser():
    parser = shared_runner.build_parser()
    parser.description = __doc__
    parser.set_defaults(
        risk_per_trade=0.005,
        output_root=str(DEFAULT_OUTPUT_ROOT),
    )
    for action in parser._actions:
        if action.dest == "config_override":
            action.help = (
                "Override one SecondLegDownConfig field, repeatable. "
                "Example: --config-override volume_filter_enabled=false"
            )
    return parser


def build_reproduction_command(args):
    command = shared_runner.build_reproduction_command(args)
    argv = list(command["argv"])
    argv[2] = "cta.strategy.second_leg_down.backtest.runner"
    command["argv"] = argv
    command["shell_command"] = shared_runner.shlex.join(argv)
    command["shell_command"] = (
        f"cd {shared_runner.shlex.quote(str(shared_runner.REPO_ROOT))} && "
        + command["shell_command"]
    )
    return command


def run_from_args(args):
    config = SecondLegDownConfig(
        risk_per_trade=args.risk_per_trade,
        max_risk_per_trade=args.risk_per_trade,
        max_risk_pct=args.risk_per_trade,
        max_concurrent_positions=args.max_concurrent_positions,
        intraday_margin_utilization=args.intraday_margin_utilization,
        overnight_margin_utilization=args.overnight_margin_utilization,
        max_symbol_margin_utilization=args.max_symbol_margin_utilization,
        overnight_reduction_minutes=args.overnight_reduction_minutes,
    )
    if not args.run_id:
        start = shared_runner._parse_date(args.start, "start")
        end = shared_runner._parse_date(args.end, "end")
        args.run_id = (
            f"{datetime.now():%Y%m%d_%H%M%S_%f}_"
            f"{start:%Y%m%d}_{end:%Y%m%d}_1d_"
            f"{config.signal_timeframe_minutes}m_1m"
        )
    return shared_runner.run_from_args(
        args,
        config=config,
        strategy_data_builder=_prepare_strategy_data,
        reproduction_builder=build_reproduction_command,
        context_builder=_build_run_context,
        config_finalizer=lambda item: item.for_replay(),
    )


def _prepare_strategy_data(
    loaded,
    *,
    metadata_store,
    config: SecondLegDownConfig,
    start: date,
    end: date,
    initial_equity: float,
    aggregation_cache=None,
    gate_diagnostics=None,
):
    del gate_diagnostics
    minute = loaded.minute_bars.sort_values("bar_end", kind="stable").reset_index(
        drop=True
    )
    signal = minute.copy()
    for column in ("open", "high", "low", "close"):
        signal_column = f"signal_{column}"
        if signal_column in signal:
            signal[column] = signal[signal_column]
    signal = _attach_execution_instruments(
        signal,
        loaded=loaded,
        metadata_store=metadata_store,
    )
    instrument = _representative_instrument(
        loaded,
        metadata_store=metadata_store,
    )
    signal_frame = _signal_timeframe_bars(
        signal,
        sessions=loaded.sessions,
        config=config,
        aggregation_cache=aggregation_cache,
    )
    daily_signal = shared_runner._aggregate_trading_day_daily_bars(
        signal,
        sessions=loaded.sessions,
        aggregation_cache=aggregation_cache,
    )
    candidates = generate_second_leg_down_candidates(
        signal_frame,
        daily_bars=daily_signal,
        sessions=loaded.sessions,
        instrument=instrument,
        config=config,
        equity=initial_equity,
        # 订单永远活在 1 分钟执行时间轴上，即使形态是 5 分钟找出来的
        execution_bars=signal if signal_frame is not signal else None,
    )
    if not candidates.empty:
        candidates = candidates.loc[
            candidates["exchange_trade_date"].map(lambda value: start <= value <= end)
        ].reset_index(drop=True)

    # 回放上下文必须逐分钟对齐，所以它始终按 1 分钟算，
    # 与形态用的信号周期无关。
    features = build_second_leg_features(signal, loaded.sessions, config)
    context = features.loc[:, ["bar_end", "atr"]].copy()
    scale = pd.to_numeric(signal["adjustment_scale"], errors="coerce")
    context["atr"] = pd.to_numeric(context["atr"], errors="coerce") / scale
    replay_minute = minute.merge(context, on="bar_end", how="left", validate="one_to_one")

    daily_actual = shared_runner._aggregate_trading_day_daily_bars(
        minute,
        sessions=loaded.sessions,
        aggregation_cache=aggregation_cache,
    )
    if aggregation_cache is None:
        five_actual = shared_runner.aggregate_completed_bars(
            minute, minutes=5, sessions=loaded.sessions
        )
    else:
        five_actual = shared_runner.cached_aggregate_completed_bars(
            aggregation_cache,
            minute,
            minutes=5,
            sessions=loaded.sessions,
            compute=shared_runner.aggregate_completed_bars,
        )
    return (
        candidates,
        shared_runner._sort_aggregated_bars(daily_actual),
        shared_runner._sort_aggregated_bars(five_actual),
        replay_minute,
        context,
        pd.DataFrame(),
    )


def _signal_timeframe_bars(signal, *, sessions, config, aggregation_cache):
    """Aggregate the signal frame to ``signal_timeframe_minutes`` completed bars.

    Returns the input unchanged at one minute, so the default path costs
    nothing. Aggregation reuses the shared session-aware helper, which only
    emits a bar once its whole segment slice is complete — a partial bar at a
    session break never becomes a signal.
    """
    minutes = int(getattr(config, "signal_timeframe_minutes", 1))
    if minutes <= 1:
        return signal
    if aggregation_cache is None:
        aggregated = shared_runner.aggregate_completed_bars(
            signal, minutes=minutes, sessions=sessions
        )
    else:
        aggregated = shared_runner.cached_aggregate_completed_bars(
            aggregation_cache,
            signal,
            minutes=minutes,
            sessions=sessions,
            compute=shared_runner.aggregate_completed_bars,
        )
    aggregated = shared_runner._sort_aggregated_bars(aggregated)
    # 聚合只保留 OHLCV，合约标识和复权系数要按 bar_end 贴回来
    carry = [
        name
        for name in (
            "contract_code", "exchange_trade_date",
            "adjustment_scale", "adjustment_offset",
            "tick_size", "multiplier", "margin_rate",
            "stressed_round_trip_cost", "metadata_asof",
        )
        if name in signal.columns and name not in aggregated.columns
    ]
    if not carry:
        return aggregated
    return pd.merge_asof(
        aggregated.sort_values("bar_end", kind="stable"),
        signal.loc[:, ["bar_end", *carry]].sort_values("bar_end", kind="stable"),
        on="bar_end",
        direction="backward",
    )


def _attach_execution_instruments(frame, *, loaded, metadata_store):
    result = frame.copy()
    snapshots = {}
    for key, group in result.groupby(
        ["contract_code", "exchange_trade_date"], sort=False, dropna=False
    ):
        first = group.iloc[0]
        timestamp = pd.Timestamp(first["bar_end"])
        snapshot = metadata_store.execution_snapshot(
            root_symbol=loaded.root_symbol,
            exchange=loaded.exchange,
            contract_code=str(first["contract_code"]),
            exchange_trade_date=first["exchange_trade_date"],
            gateway=BACKTEST_GATEWAY,
            order_types=("STOP",),
            decision_asof=timestamp.to_pydatetime(),
            order_event=timestamp.to_pydatetime(),
        )
        snapshots[key] = snapshot

    values = [snapshots[(row.contract_code, row.exchange_trade_date)] for row in result.itertuples()]
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


def _representative_instrument(loaded, *, metadata_store):
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
    return SecondLegInstrument(
        symbol=loaded.root_symbol,
        exchange=loaded.exchange,
        contract=str(first["contract_code"]),
        tick_size=float(snapshot.price_tick),
        multiplier=float(snapshot.contract_size),
        stressed_round_trip_cost=cost,
        metadata_asof=snapshot.instrument.known_at,
        margin_rate=float(snapshot.daily.margin_rate_short),
    )


def _capital_basis_summary(candidates: pd.DataFrame) -> dict[str, dict[str, int]]:
    if candidates.empty or "capital_basis" not in candidates:
        return {}
    result = {}
    for basis, rows in candidates.groupby("capital_basis", sort=True):
        eligible = rows["filtered_reason"].fillna("").astype(str).eq("")
        quantity = pd.to_numeric(rows.loc[eligible, "quantity"], errors="coerce").fillna(0)
        result[str(basis)] = {
            "candidate_count": int(len(rows)),
            "eligible_count": int(eligible.sum()),
            "quantity": int(quantity.sum()),
        }
    return result


def _build_run_context(**values):
    args = values["args"]
    config = values["config"]
    aggregation_cache = values["aggregation_cache"]
    gate_diagnostics = values["gate_diagnostics"]
    return {
        "strategy": "second_leg_down",
        "strategy_version": "second-leg-down-daily-trend-v2",
        "requested_start": values["start"].isoformat(),
        "requested_end": values["end"].isoformat(),
        "warmup_start": values["warmup_start"].isoformat(),
        "effective_warmup_starts": {
            key: value.isoformat() for key, value in values["effective_starts"].items()
        },
        "requested_symbols": [item.vt_symbol for item in values["selected"]],
        "loaded_symbols": [item.vt_symbol for item in values["loaded_items"]],
        "discovered_symbol_count": len(values["discovered"]),
        "timeframes": {
            "direction": "1d",
            "entry": f"{config.signal_timeframe_minutes}min",
            "execution": "1min",
        },
        "risk_per_trade": config.max_risk_pct,
        "risk_band": {
            "minimum": config.min_risk_pct,
            "maximum": config.max_risk_pct,
            "max_capital_share": config.max_capital_share,
        },
        "capital_basis": _capital_basis_summary(values["candidates"]),
        "portfolio_limits": {
            "max_concurrent_positions": config.max_concurrent_positions,
            "intraday_margin_utilization": config.intraday_margin_utilization,
            "overnight_margin_utilization": config.overnight_margin_utilization,
            "max_symbol_margin_utilization": config.max_symbol_margin_utilization,
            "overnight_reduction_minutes": config.overnight_reduction_minutes,
        },
        "aggregation_cache": aggregation_cache.stats,
        "gate_fail_open": gate_diagnostics.fail_open_counts(),
        "gate_evaluations": gate_diagnostics.evaluation_counts(),
        "minute_data_update": values["minute_update"],
        "execution_metadata_update": values["metadata_update"],
        "config": {
            field.name: getattr(config, field.name)
            for field in shared_runner.fields(config)
        },
        "output_root": str(args.output_root),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary, output = run_from_args(args)
    except (
        shared_runner.ScalpBlockedMetadataError,
        shared_runner.BlockedMetadataError,
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
