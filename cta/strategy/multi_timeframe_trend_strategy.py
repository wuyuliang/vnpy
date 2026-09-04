"""Daily-direction/5-minute-entry trend strategy candidate facade.

Strategy assumptions: strict daily EMA5/EMA10/EMA20 order grants direction;
5-minute persistent progress or a volume-confirmed pullback breakout supplies
the entry plan. The strategy supports long and short actual-contract research.

Entries are next-event stop plans. Both setups use confirmed-swing trailing
stops. A 2R level is retained only as a non-executable chart reference. Position
size risks 1%-2% of equity
including stressed costs. Fees, slippage, limits, sessions, and rolls must come
from effective-dated execution metadata. The strategy is expected to struggle
in EMA whipsaws, exhaustion trends, event gaps, and distorted low-liquidity
volume regimes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.multi_timeframe_trend_rules import (
    PullbackState,
    SignalCandidate,
    advance_pullback_state,
    assess_obstacle,
    entry_range_position,
    build_daily_context,
    build_intraday_context,
    detect_always_in,
    size_for_risk,
    two_r_target,
)
from cta.strategy.multi_timeframe_trend_backtest.diagnostics import (
    GateFailOpenDiagnostics,
    observe_gate,
)


CANDIDATE_COLUMNS = (
    "symbol",
    "exchange",
    "contract",
    "metadata_asof",
    "interval",
    "datetime",
    "signal_datetime",
    "signal_type",
    "setup_type",
    "side",
    "direction",
    "direction_value",
    "order_type",
    "trigger",
    "stop_price",
    "target_price",
    "target_price_virtual",
    "quantity",
    "risk_budget",
    "loss_per_lot",
    "candidate_status",
    "sample_status",
    "is_executed",
    "is_filtered",
    "is_triggered",
    "filtered_reason",
    "rejection_code",
    "feature_asof",
    "known_at",
    "pivot_time",
    "structure_known_at",
    "decision_asof",
    "decision_sequence",
    "order_active_at",
    "order_active_sequence",
    "order_expire_at",
    "order_expire_bar_i",
    "fill_time",
    "daily_feature_asof",
    "daily_direction",
    "daily_bull_trend_id",
    "daily_ema5",
    "daily_ema10",
    "daily_ema20",
    "daily_atr14",
    "prior_5d_high",
    "prior_5d_low",
    "nearest_obstacle",
    "obstacle_distance_atr",
    "feature_atr14",
    "feature_volume",
    "feature_volume_threshold",
    "feature_volume_expanded",
    "signal_i",
    "pullback_start_i",
    "chase_high_candidate",
)


@dataclass(frozen=True)
class InstrumentSpec:
    """Execution inputs that must be known for the actual contract."""

    symbol: str
    exchange: str
    contract: str
    tick_size: float
    multiplier: float
    stressed_round_trip_cost: float
    metadata_asof: object

    def __post_init__(self) -> None:
        if not all(str(value).strip() for value in (self.symbol, self.exchange, self.contract)):
            raise ValueError("symbol, exchange, and contract must not be empty")
        contract = str(self.contract).strip().upper()
        if contract == str(self.symbol).strip().upper() or not any(char.isdigit() for char in contract):
            raise ValueError("contract must identify an actual dated contract")
        positive = (self.tick_size, self.multiplier)
        if not all(np.isfinite(value) and value > 0 for value in positive):
            raise ValueError("tick_size and multiplier must be finite and positive")
        if (
            not np.isfinite(self.stressed_round_trip_cost)
            or self.stressed_round_trip_cost < 0
        ):
            raise ValueError("stressed_round_trip_cost must be finite and nonnegative")
        metadata_asof = pd.Timestamp(self.metadata_asof)
        if metadata_asof is pd.NaT or metadata_asof.tzinfo is None:
            raise ValueError("metadata_asof must be a timezone-aware timestamp")
        object.__setattr__(self, "metadata_asof", metadata_asof)


def generate_multi_timeframe_candidates(
    daily_bars: pd.DataFrame,
    minute5_bars: pd.DataFrame,
    *,
    instrument: InstrumentSpec,
    config: MultiTimeframeTrendConfig | None = None,
    equity: float = 1_000_000.0,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> pd.DataFrame:
    """Generate causal eligible and filtered plans without simulating future fills."""
    cfg = config or MultiTimeframeTrendConfig()
    if not np.isfinite(equity) or equity <= 0:
        raise ValueError("equity must be finite and positive")
    if daily_bars.empty or minute5_bars.empty:
        return _empty_candidates()

    daily = build_daily_context(_with_bar_end(daily_bars), cfg)
    intraday = build_intraday_context(_with_bar_end(minute5_bars), cfg)
    aligned = _align_completed_daily(intraday, daily)

    rows: list[dict[str, object]] = []
    pullback_state = PullbackState()
    prior_direction = 0
    for index in range(len(aligned)):
        row = aligned.iloc[index]
        row_instrument = _instrument_for_row(row, instrument)
        direction = int(row.get("daily_direction", 0) or 0)
        if direction != prior_direction:
            pullback_state = PullbackState()
        prior_direction = direction
        if direction == 0:
            continue
        if direction in cfg.candidate_direction_blacklist:
            continue

        always_in = detect_always_in(
            aligned,
            index=index,
            direction=direction,
            config=cfg,
            tick_size=row_instrument.tick_size,
        )
        pullback_state, pullback = advance_pullback_state(
            aligned,
            index=index,
            direction=direction,
            state=pullback_state,
            config=cfg,
            tick_size=row_instrument.tick_size,
        )

        candidates: list[tuple[SignalCandidate, str]] = []
        allowed_pullback = (
            pullback is not None
            and pullback.setup_type not in cfg.candidate_setup_blacklist
        )
        if allowed_pullback:
            candidates.append((pullback, ""))
        if (
            always_in is not None
            and always_in.setup_type not in cfg.candidate_setup_blacklist
        ):
            duplicate_reason = "DUPLICATE_SIGNAL" if allowed_pullback else ""
            candidates.append((always_in, duplicate_reason))
        for candidate, forced_reason in candidates:
            rows.append(
                _candidate_row(
                    candidate=candidate,
                    row=row,
                    daily_context=daily,
                    instrument=row_instrument,
                    config=cfg,
                    equity=equity,
                    forced_reason=forced_reason,
                    diagnostics=diagnostics,
                )
            )

    if not rows:
        return _empty_candidates()
    result = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
    priority = result["signal_type"].map({"pullback_breakout": 0, "always_in": 1})
    result = result.assign(_priority=priority.fillna(99)).sort_values(
        ["signal_datetime", "_priority"], kind="stable"
    )
    return result.drop(columns="_priority").reset_index(drop=True)


def _entry_quality_reason(
    *,
    candidate: SignalCandidate,
    row: pd.Series,
    daily_atr14: float,
    config: MultiTimeframeTrendConfig,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> str:
    """Reject entries that chase exhausted volume, sit too far from their stop,
    or buy the top of a still-contracted range.

    Every check fails open: a missing or non-finite input leaves the candidate
    alone rather than silently filtering it.
    """
    trigger = float(candidate.trigger)
    trigger_missing = not np.isfinite(trigger)
    observe_gate(
        diagnostics,
        "entry_quality_trigger_missing",
        fail_open=trigger_missing,
    )
    if trigger_missing:
        return ""

    limit = float(config.max_entry_volume_ratio)
    if limit > 0:
        volume = float(pd.to_numeric(row.get("volume", np.nan), errors="coerce"))
        threshold = float(
            pd.to_numeric(row.get("volume_threshold", np.nan), errors="coerce")
        )
        missing_volume_input = not (
            np.isfinite(volume) and np.isfinite(threshold) and threshold > 0
        )
        observe_gate(
            diagnostics,
            "entry_volume_ratio_missing_input",
            fail_open=missing_volume_input,
        )
        if not missing_volume_input:
            if volume / threshold > limit:
                return "ENTRY_VOLUME_RATIO_TOO_HIGH"

    limit = float(config.max_entry_stop_distance_atr)
    if limit > 0:
        stop = float(candidate.stop_price)
        missing_stop_input = not (
            np.isfinite(daily_atr14)
            and daily_atr14 > 0
            and np.isfinite(stop)
        )
        observe_gate(
            diagnostics,
            "entry_stop_distance_missing_input",
            fail_open=missing_stop_input,
        )
        if not missing_stop_input:
            if abs(trigger - stop) / daily_atr14 > limit:
                return "ENTRY_STOP_DISTANCE_TOO_WIDE"

    range_high = float(
        pd.to_numeric(row.get("range_window_high", np.nan), errors="coerce")
    )
    range_low = float(
        pd.to_numeric(row.get("range_window_low", np.nan), errors="coerce")
    )
    limit = float(config.min_entry_range_width_atr)
    if limit > 0:
        missing_range_input = not (
            np.isfinite(daily_atr14)
            and daily_atr14 > 0
            and np.isfinite(range_high)
            and np.isfinite(range_low)
            and range_high > range_low
        )
        observe_gate(
            diagnostics,
            "entry_range_width_missing_input",
            fail_open=missing_range_input,
        )
        if (
            not missing_range_input
            and (range_high - range_low) / daily_atr14 < limit
        ):
            return "ENTRY_RANGE_TOO_NARROW"

    return ""


def _entry_range_position_reason(
    *,
    candidate: SignalCandidate,
    row: pd.Series,
    config: MultiTimeframeTrendConfig,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> str:
    """Reject an entry sitting in the top of its recent range.

    Kept separate from the other gates because a candidate blocked only by this
    one is still a live chase-high signal: the engine keeps trading it virtually
    so the strategy can tell whether chasing is currently paying.
    """
    limit = float(config.max_entry_range_position)
    if limit <= 0:
        return ""
    trigger = float(candidate.trigger)
    range_high = float(
        pd.to_numeric(row.get("range_window_high", np.nan), errors="coerce")
    )
    range_low = float(
        pd.to_numeric(row.get("range_window_low", np.nan), errors="coerce")
    )
    position = entry_range_position(trigger, range_high, range_low)
    if not np.isfinite(position):
        observe_gate(
            diagnostics, "entry_range_position_no_window", fail_open=True
        )
        return ""
    observe_gate(diagnostics, "entry_range_position_no_window")
    if candidate.direction < 0:
        position = 1.0 - position
    return "ENTRY_RANGE_POSITION_TOO_HIGH" if position > limit else ""


def _candidate_row(
    *,
    candidate: SignalCandidate,
    row: pd.Series,
    daily_context: pd.DataFrame,
    instrument: InstrumentSpec,
    config: MultiTimeframeTrendConfig,
    equity: float,
    forced_reason: str,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> dict[str, object]:
    signal_time = pd.Timestamp(candidate.signal_time)
    obstacle = assess_obstacle(candidate, daily_context, config)
    risk = size_for_risk(
        equity=equity,
        entry=candidate.trigger,
        stop=candidate.stop_price,
        multiplier=instrument.multiplier,
        stressed_round_trip_cost=instrument.stressed_round_trip_cost,
        risk_per_trade=config.risk_per_trade,
    )
    metadata_reason = (
        "METADATA_NOT_EFFECTIVE" if instrument.metadata_asof > signal_time else ""
    )
    daily_ema5 = float(pd.to_numeric(row.get("daily_ema5", np.nan), errors="coerce"))
    daily_ema20 = float(pd.to_numeric(row.get("daily_ema20", np.nan), errors="coerce"))
    daily_ema_gap_reason = ""
    if candidate.setup_type == "always_in" and candidate.direction > 0:
        if (
            not np.isfinite(daily_ema5)
            or not np.isfinite(daily_ema20)
            or daily_ema20 <= 0
        ):
            daily_ema_gap_reason = "DAILY_EMA_GAP_BELOW_MIN"
        else:
            daily_ema_gap_ratio = (daily_ema5 - daily_ema20) / daily_ema20
            if daily_ema_gap_ratio < config.always_in_long_daily_ema_gap_min_ratio:
                daily_ema_gap_reason = "DAILY_EMA_GAP_BELOW_MIN"
    prior_5d_high = float(row.get("prior_5d_high", np.nan))
    prior_5d_low = float(row.get("prior_5d_low", np.nan))
    raw_bull_trend_id = pd.to_numeric(
        row.get("daily_bull_trend_id", 0),
        errors="coerce",
    )
    daily_bull_trend_id = (
        int(raw_bull_trend_id)
        if pd.notna(raw_bull_trend_id)
        and np.isfinite(raw_bull_trend_id)
        and float(raw_bull_trend_id).is_integer()
        and raw_bull_trend_id > 0
        else 0
    )
    if not np.isfinite(prior_5d_high) or not np.isfinite(prior_5d_low):
        daily_breakout_reason = "DAILY_FIVE_BAR_HISTORY_UNAVAILABLE"
    elif (
        candidate.direction > 0
        and candidate.trigger < prior_5d_high
    ) or (
        candidate.direction < 0
        and candidate.trigger > prior_5d_low
    ):
        daily_breakout_reason = "DAILY_FIVE_BAR_BREAKOUT_NOT_MET"
    else:
        daily_breakout_reason = ""
    entry_quality_reason = _entry_quality_reason(
        candidate=candidate,
        row=row,
        daily_atr14=float(
            pd.to_numeric(row.get("daily_atr14", np.nan), errors="coerce")
        ),
        config=config,
        diagnostics=diagnostics,
    )
    base_reason = (
        forced_reason
        or metadata_reason
        or daily_ema_gap_reason
        or daily_breakout_reason
        or entry_quality_reason
        or obstacle.reason
        or risk.reason
    )
    range_position_reason = _entry_range_position_reason(
        candidate=candidate,
        row=row,
        config=config,
        diagnostics=diagnostics,
    )
    # 只被区间位置拦下、其余全部通过的候选，才是可以做虚拟单的追高信号
    chase_high_candidate = int(bool(range_position_reason) and not base_reason)
    reason = base_reason or range_position_reason
    target = two_r_target(
        candidate.trigger,
        candidate.stop_price,
        candidate.direction,
        config.pullback_target_r,
    )
    order_active_at = signal_time
    order_expire_at = pd.NaT
    side = "long" if candidate.direction > 0 else "short"
    swing_kind = "low" if candidate.direction > 0 else "high"
    pivot_time = (
        row.get(f"latest_swing_{swing_kind}_pivot_time", pd.NaT)
        if candidate.setup_type == "always_in"
        else pd.NaT
    )
    filtered = bool(reason)
    if reason == "RISK_BELOW_ONE_LOT":
        sample_status = "blocked_by_risk"
    elif filtered:
        sample_status = "filtered_by_rule"
    else:
        sample_status = "not_triggered_market"
    return {
        "symbol": instrument.symbol.upper(),
        "exchange": instrument.exchange.upper(),
        "contract": instrument.contract.upper(),
        "metadata_asof": instrument.metadata_asof,
        "interval": "minute5",
        "datetime": order_active_at,
        "signal_datetime": signal_time,
        "signal_type": candidate.setup_type,
        "setup_type": candidate.setup_type,
        "side": side,
        "direction": side,
        "direction_value": candidate.direction,
        "order_type": "stop",
        "trigger": candidate.trigger,
        "stop_price": candidate.stop_price,
        "target_price": np.nan,
        "target_price_virtual": target,
        "quantity": 0 if filtered else risk.quantity,
        "risk_budget": risk.risk_budget,
        "loss_per_lot": risk.loss_per_lot,
        "candidate_status": "filtered" if filtered else "not_triggered",
        "sample_status": sample_status,
        "is_executed": 0,
        "is_filtered": int(filtered),
        "is_triggered": 0,
        "filtered_reason": reason,
        "rejection_code": reason,
        "chase_high_candidate": chase_high_candidate,
        "feature_asof": signal_time,
        "known_at": signal_time,
        "pivot_time": pivot_time,
        "structure_known_at": candidate.known_at,
        "decision_asof": signal_time,
        "decision_sequence": 0,
        "order_active_at": order_active_at,
        "order_active_sequence": 1,
        "order_expire_at": order_expire_at,
        "order_expire_bar_i": candidate.signal_index + config.order_expiry_bars,
        "fill_time": pd.NaT,
        "daily_feature_asof": row.get("daily_feature_asof", pd.NaT),
        "daily_direction": candidate.direction,
        "daily_bull_trend_id": daily_bull_trend_id,
        "daily_ema5": daily_ema5,
        "daily_ema10": row.get("daily_ema10", np.nan),
        "daily_ema20": daily_ema20,
        "daily_atr14": row.get("daily_atr14", np.nan),
        "prior_5d_high": prior_5d_high,
        "prior_5d_low": prior_5d_low,
        "nearest_obstacle": obstacle.price,
        "obstacle_distance_atr": obstacle.distance_atr,
        "feature_atr14": row.get("atr14", np.nan),
        "feature_volume": row.get("volume", np.nan),
        "feature_volume_threshold": row.get("volume_threshold", np.nan),
        "feature_volume_expanded": int(bool(row.get("volume_expanded", False))),
        "signal_i": candidate.signal_index,
        "pullback_start_i": candidate.pullback_start_index,
    }


def _instrument_for_row(
    row: pd.Series,
    instrument: InstrumentSpec,
) -> InstrumentSpec:
    def field(name: str, fallback: object) -> object:
        value = row.get(name, fallback)
        return fallback if value is None or pd.isna(value) else value

    return InstrumentSpec(
        symbol=str(field("symbol", instrument.symbol)),
        exchange=str(field("exchange", instrument.exchange)),
        contract=str(field("contract_code", instrument.contract)),
        tick_size=float(field("instrument_tick_size", instrument.tick_size)),
        multiplier=float(field("instrument_multiplier", instrument.multiplier)),
        stressed_round_trip_cost=float(
            field(
                "instrument_stressed_round_trip_cost",
                instrument.stressed_round_trip_cost,
            )
        ),
        metadata_asof=field("instrument_metadata_asof", instrument.metadata_asof),
    )


def _align_completed_daily(
    intraday: pd.DataFrame,
    daily_context: pd.DataFrame,
) -> pd.DataFrame:
    daily_columns = [
        "bar_end",
        "daily_direction",
        "daily_bull_trend_id",
        "daily_ema5",
        "daily_ema10",
        "daily_ema20",
        "daily_atr14",
        "prior_5d_high",
        "prior_5d_low",
    ]
    higher = daily_context[daily_columns].rename(
        columns={"bar_end": "daily_feature_asof"}
    )
    result = pd.merge_asof(
        intraday.sort_values("bar_end"),
        higher.sort_values("daily_feature_asof"),
        left_on="bar_end",
        right_on="daily_feature_asof",
        direction="backward",
        allow_exact_matches=True,
    )
    result["daily_direction"] = result["daily_direction"].fillna(0).astype(int)
    result["daily_bull_trend_id"] = (
        result["daily_bull_trend_id"].fillna(0).astype(int)
    )
    return result.reset_index(drop=True)


def _with_bar_end(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "bar_end" not in result:
        raise KeyError("bars require explicit bar_end; datetime semantics are ambiguous")
    result["bar_end"] = pd.to_datetime(result["bar_end"], errors="coerce")
    if result["bar_end"].isna().any():
        raise ValueError("bar_end contains invalid timestamps")
    if result["bar_end"].dt.tz is None:
        raise ValueError("bar_end must be timezone-aware")
    return result.sort_values("bar_end").reset_index(drop=True)


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDIDATE_COLUMNS)


__all__ = [
    "CANDIDATE_COLUMNS",
    "InstrumentSpec",
    "generate_multi_timeframe_candidates",
]
