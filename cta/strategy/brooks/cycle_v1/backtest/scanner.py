"""Causal multi-timeframe market-cycle scan for EMA-eligible futures roots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from ..config import BrooksCycleConfig
from ..core.features.causal import add_causal_features
from ..core.market_cycle import classify_market_cycles
from ..instruments.sessions import (
    aggregate_completed_bars,
    aggregate_completed_daily_bars,
)
from .data_loader import LoadedSymbol
from .timeframes import Timeframe, TimeframeSet
from .universe import build_dynamic_ema_universe


CYCLE_SNAPSHOT_COLUMNS = (
    "root_symbol",
    "exchange",
    "vt_symbol",
    "exchange_trade_date",
    "contract_code",
    "short_tf",
    "medium_tf",
    "long_tf",
    "short_feature_asof",
    "medium_feature_asof",
    "long_feature_asof",
    "short_cycle",
    "medium_cycle",
    "long_cycle",
    "short_direction",
    "medium_direction",
    "long_direction",
    "ema_asof_trade_date",
    "ema_asof_bar_end",
    "ema1",
    "ema3",
    "ema5",
    "eligible",
    "reason_code",
)


@dataclass(frozen=True)
class CycleScanArtifacts:
    universe_daily: pd.DataFrame
    cycle_snapshots: pd.DataFrame
    replay_frames: dict[str, SymbolReplayFrames]


@dataclass(frozen=True)
class SymbolReplayFrames:
    short: pd.DataFrame
    medium: pd.DataFrame
    long: pd.DataFrame


def empty_scan_artifacts() -> CycleScanArtifacts:
    daily = pd.DataFrame(
        columns=[
            "root_symbol",
            "exchange_trade_date",
            "contract_code",
            "bar_end",
            "close",
        ]
    )
    return CycleScanArtifacts(
        universe_daily=build_dynamic_ema_universe(daily),
        cycle_snapshots=pd.DataFrame(columns=CYCLE_SNAPSHOT_COLUMNS),
        replay_frames={},
    )


def scan_loaded_symbols(
    symbols: tuple[LoadedSymbol, ...],
    *,
    config: BrooksCycleConfig,
    timeframes: TimeframeSet,
    start: date,
    end: date,
) -> CycleScanArtifacts:
    if end < start:
        raise ValueError("end precedes start")
    if not symbols:
        raise ValueError("at least one loaded symbol is required")
    roots = [item.root_symbol for item in symbols]
    if len(roots) != len(set(roots)):
        raise ValueError("loaded root symbols must be unique")

    daily_parts: list[pd.DataFrame] = []
    for item in symbols:
        daily = aggregate_completed_daily_bars(
            item.minute_bars,
            sessions=item.sessions,
        )
        if daily.empty:
            continue
        daily["root_symbol"] = item.root_symbol
        daily_parts.append(daily)
    daily_bars = (
        pd.concat(daily_parts, ignore_index=True)
        if daily_parts
        else pd.DataFrame(
            columns=sorted(
                {
                    "root_symbol",
                    "exchange_trade_date",
                    "contract_code",
                    "bar_end",
                    "close",
                }
            )
        )
    )
    universe = build_dynamic_ema_universe(daily_bars)

    snapshot_parts: list[pd.DataFrame] = []
    replay_frames: dict[str, SymbolReplayFrames] = {}
    for item in symbols:
        frames = build_symbol_replay_frames(
            item,
            config=config,
            timeframes=timeframes,
        )
        replay_frames[item.root_symbol] = frames
        symbol_universe = universe.loc[
            universe["root_symbol"].eq(item.root_symbol)
        ].copy()
        if symbol_universe.empty:
            continue
        snapshots = _scan_symbol(
            item,
            timeframes,
            symbol_universe,
            frames=frames,
        )
        if snapshots.empty:
            continue
        requested = snapshots["exchange_trade_date"].map(
            lambda value: start <= value <= end
        )
        snapshots = snapshots.loc[requested & snapshots["eligible"]].copy()
        if not snapshots.empty:
            snapshot_parts.append(snapshots)
    cycle_snapshots = (
        pd.concat(snapshot_parts, ignore_index=True)
        if snapshot_parts
        else pd.DataFrame(columns=CYCLE_SNAPSHOT_COLUMNS)
    )
    if not cycle_snapshots.empty:
        cycle_snapshots = cycle_snapshots.sort_values(
            ["short_feature_asof", "root_symbol", "contract_code"],
            kind="stable",
        ).reset_index(drop=True)
    return CycleScanArtifacts(
        universe_daily=universe,
        cycle_snapshots=cycle_snapshots,
        replay_frames=replay_frames,
    )


def _scan_symbol(
    item: LoadedSymbol,
    timeframes: TimeframeSet,
    universe: pd.DataFrame,
    *,
    frames: SymbolReplayFrames,
) -> pd.DataFrame:
    short = _role_frame(frames.short, "short")
    if short.empty:
        return pd.DataFrame()
    result = _align_higher(
        short,
        _role_frame(frames.medium, "medium"),
        role="medium",
    )
    result = _align_higher(
        result,
        _role_frame(frames.long, "long"),
        role="long",
    )
    result["root_symbol"] = item.root_symbol
    result["exchange"] = item.exchange
    result["vt_symbol"] = item.vt_symbol
    result["short_tf"] = timeframes.short.canonical
    result["medium_tf"] = timeframes.medium.canonical
    result["long_tf"] = timeframes.long.canonical
    return result.merge(
        universe,
        on=["root_symbol", "exchange_trade_date", "contract_code"],
        how="left",
        validate="many_to_one",
    )


def build_symbol_replay_frames(
    item: LoadedSymbol,
    *,
    config: BrooksCycleConfig,
    timeframes: TimeframeSet,
) -> SymbolReplayFrames:
    """Return complete causal features and cycles for each configured timeframe."""
    return SymbolReplayFrames(
        short=_feature_cycle_timeframe(item, config, timeframes.short),
        medium=_feature_cycle_timeframe(item, config, timeframes.medium),
        long=_feature_cycle_timeframe(item, config, timeframes.long),
    )


def _classify_timeframe(
    item: LoadedSymbol,
    config: BrooksCycleConfig,
    timeframe: Timeframe,
    role: str,
) -> pd.DataFrame:
    complete = _feature_cycle_timeframe(item, config, timeframe)
    return _role_frame(complete, role)


def _role_frame(complete: pd.DataFrame, role: str) -> pd.DataFrame:
    if complete.empty:
        return complete
    cycle_columns = [
        "cycle",
        "direction",
        "strength",
        "confidence",
        "bull_pressure",
        "bear_pressure",
        "range_subtype",
        "feature_asof",
        "reason",
        "evidence",
        "adjustment_scale",
        "adjustment_offset",
        "adjustment_known_at",
        "adjustment_version",
        "adjustment_source",
    ]
    result = complete.loc[
        :,
        [
            "contract_code",
            "exchange_trade_date",
            "bar_end",
            "feature_sequence",
            *cycle_columns,
        ],
    ].copy()
    rename = {
        column: f"{role}_{column}"
        for column in result.columns
        if column not in {"contract_code", "exchange_trade_date"}
    }
    return result.rename(columns=rename).reset_index(drop=True)


def _feature_cycle_timeframe(
    item: LoadedSymbol,
    config: BrooksCycleConfig,
    timeframe: Timeframe,
) -> pd.DataFrame:
    signal_columns = {
        "signal_open",
        "signal_high",
        "signal_low",
        "signal_close",
        "adjustment_scale",
        "adjustment_offset",
        "adjustment_known_at",
        "adjustment_version",
        "adjustment_source",
    }
    missing_signal = sorted(signal_columns.difference(item.minute_bars.columns))
    if missing_signal:
        raise ValueError(
            "minute bars are missing PIT signal fields: " + ",".join(missing_signal)
        )
    signal_minutes = item.minute_bars.copy()
    for raw_name in ("open", "high", "low", "close"):
        signal_minutes[raw_name] = signal_minutes[f"signal_{raw_name}"]
    bars = aggregate_completed_bars(
        signal_minutes,
        minutes=timeframe.minutes,
        sessions=item.sessions,
    )
    if bars.empty:
        return pd.DataFrame()
    mechanics = item.minute_bars.loc[
        :,
        [
            "contract_code",
            "bar_end",
            "price_tick",
            "base_round_trip_cost_price",
            "adjustment_scale",
            "adjustment_offset",
            "adjustment_known_at",
            "adjustment_version",
            "adjustment_source",
        ],
    ].rename(columns={"bar_end": "source_max_bar_end"})
    bars = bars.merge(
        mechanics,
        on=["contract_code", "source_max_bar_end"],
        how="left",
        validate="one_to_one",
    )
    mechanics_columns = [
        "price_tick",
        "base_round_trip_cost_price",
        "adjustment_scale",
        "adjustment_offset",
        "adjustment_known_at",
        "adjustment_version",
        "adjustment_source",
    ]
    if bars[mechanics_columns].isna().any().any():
        raise ValueError("aggregated bars are missing point-in-time mechanics")
    known_at = pd.to_datetime(bars["adjustment_known_at"])
    if known_at.dt.tz is None or (
        known_at > pd.to_datetime(bars["feature_asof"])
    ).any():
        raise ValueError("signal adjustment must be known by feature_asof")
    bars = bars.sort_values("bar_end", kind="stable").reset_index(drop=True)
    bars["session_bucket"] = (
        bars["segment_id"].astype(str)
        + ":"
        + pd.to_datetime(bars["bar_end"]).dt.strftime("%H:%M")
    )
    stressed_cost = bars["base_round_trip_cost_price"] * config.risk.cost_stress_mult
    features = add_causal_features(
        bars,
        config,
        price_tick=bars["price_tick"],
        round_trip_cost_price=stressed_cost,
    )
    cycles = classify_market_cycles(features, config)
    return pd.concat(
        [
            features.reset_index(drop=True),
            cycles.drop(columns=["feature_asof", "feature_sequence"]).reset_index(
                drop=True
            ),
        ],
        axis=1,
    ).sort_values("feature_asof", kind="stable").reset_index(drop=True)


def _align_higher(
    decisions: pd.DataFrame,
    higher: pd.DataFrame,
    *,
    role: str,
) -> pd.DataFrame:
    asof = "short_feature_asof"
    higher_asof = f"{role}_feature_asof"
    if higher.empty:
        result = decisions.copy()
        for suffix in (
            "cycle",
            "direction",
            "strength",
            "confidence",
            "bull_pressure",
            "bear_pressure",
            "range_subtype",
            "feature_asof",
            "reason",
        ):
            result[f"{role}_{suffix}"] = pd.NA
        return result
    right = higher.drop(columns=["exchange_trade_date"]).sort_values(
        higher_asof, kind="stable"
    )
    return pd.merge_asof(
        decisions.sort_values(asof, kind="stable"),
        right,
        left_on=asof,
        right_on=higher_asof,
        by="contract_code",
        direction="backward",
        allow_exact_matches=True,
    )


__all__ = [
    "CYCLE_SNAPSHOT_COLUMNS",
    "CycleScanArtifacts",
    "SymbolReplayFrames",
    "build_symbol_replay_frames",
    "empty_scan_artifacts",
    "scan_loaded_symbols",
]
