"""Causal candidate generation for the Second Leg Down strategy."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

from cta.strategy.common.bar_shapes import breaks_structure_low, is_volume_surge
from cta.strategy.common.sizing import size_for_risk_band
from cta.strategy.multi_timeframe_trend_strategy import CANDIDATE_COLUMNS

from .config import SecondLegDownConfig
from .rules import build_second_leg_features, match_second_leg_pattern, safe_ratio


SECOND_LEG_AUDIT_COLUMNS = (
    "pattern_type",
    "first_body_atr",
    "last_body_atr",
    "leg_drop_atr",
    "first_upper_wick_ratio",
    "last_lower_wick_ratio",
    "first_volume_ratio",
    "last_volume_ratio",
    "volume_baseline_samples",
    "structure_low",
    "structure_break",
    "minutes_since_open",
    "minutes_until_close",
    "capital_basis",
    "single_lot_relief",
    "capital_share",
)

_REPLAY_COLUMNS = (
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
    "signal_trigger",
    "signal_stop_price",
    "signal_target_price",
    "adjustment_scale",
    "adjustment_offset",
    "adjustment_version",
)

SECOND_LEG_CANDIDATE_COLUMNS = tuple(
    dict.fromkeys((*CANDIDATE_COLUMNS, *SECOND_LEG_AUDIT_COLUMNS, *_REPLAY_COLUMNS))
)


@dataclass(frozen=True)
class SecondLegInstrument:
    """Execution inputs known for an actual dated futures contract."""

    symbol: str
    exchange: str
    contract: str
    tick_size: float
    multiplier: float
    stressed_round_trip_cost: float
    metadata_asof: object
    margin_rate: float | None = None

    def __post_init__(self) -> None:
        if not all(
            str(value).strip() for value in (self.symbol, self.exchange, self.contract)
        ):
            raise ValueError("symbol, exchange, and contract must not be empty")
        contract = str(self.contract).strip().upper()
        if contract == str(self.symbol).strip().upper() or not any(
            character.isdigit() for character in contract
        ):
            raise ValueError("contract must identify an actual dated contract")
        if not all(
            math.isfinite(float(value)) and float(value) > 0
            for value in (self.tick_size, self.multiplier)
        ):
            raise ValueError("tick_size and multiplier must be finite and positive")
        if (
            not math.isfinite(float(self.stressed_round_trip_cost))
            or float(self.stressed_round_trip_cost) < 0
        ):
            raise ValueError(
                "stressed_round_trip_cost must be finite and nonnegative"
            )
        metadata_asof = pd.Timestamp(self.metadata_asof)
        if metadata_asof is pd.NaT or metadata_asof.tzinfo is None:
            raise ValueError("metadata_asof must be a timezone-aware timestamp")
        if self.margin_rate is not None and (
            not math.isfinite(float(self.margin_rate))
            or float(self.margin_rate) <= 0
        ):
            raise ValueError("margin_rate must be finite and positive when provided")
        object.__setattr__(self, "metadata_asof", metadata_asof)


def _execution_timeline(
    execution_bars: pd.DataFrame | None,
    signal_frame: pd.DataFrame,
) -> pd.DatetimeIndex:
    """Sorted bar-end index the orders live on."""
    source = signal_frame if execution_bars is None else execution_bars
    stamps = pd.to_datetime(source["bar_end"], errors="raise")
    index = pd.DatetimeIndex(stamps).sort_values()
    if index.has_duplicates:
        index = index.drop_duplicates()
    return index


def _order_window(
    signal_time: pd.Timestamp,
    *,
    execution_index: pd.DatetimeIndex,
    signal_frame: pd.DataFrame,
    signal_index: int,
    config: SecondLegDownConfig,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Resolve activation and expiry on the execution timeline.

    The order goes live on the first execution bar strictly after the signal
    bar closes, and lives for ``order_ttl_bars`` *signal* bars, converted to
    execution bars so a five-minute setup keeps a five-minute-shaped window.
    """
    position = int(execution_index.searchsorted(signal_time, side="right"))
    if position >= len(execution_index):
        return None, None
    active_time = pd.Timestamp(execution_index[position])
    step = max(1, int(config.signal_timeframe_minutes))
    span = max(1, int(config.order_ttl_bars) * step)
    expires_position = min(position + span - 1, len(execution_index) - 1)
    return active_time, pd.Timestamp(execution_index[expires_position])


def generate_second_leg_down_candidates(
    minute_bars: pd.DataFrame,
    *,
    daily_bars: pd.DataFrame,
    sessions: tuple[Any, ...],
    instrument: SecondLegInstrument,
    config: SecondLegDownConfig | None = None,
    equity: float = 1_000_000.0,
    execution_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Generate engine-ready short candidates from completed signal bars.

    ``minute_bars`` carries the *signal* timeframe: one-minute bars by default,
    or aggregated bars when ``signal_timeframe_minutes > 1``. Orders always live
    on the execution timeline, so pass the one-minute frame as
    ``execution_bars`` whenever the two differ — a five-minute bar closing at
    10:05 is only known at 10:05, and the order it produces belongs to the
    10:06 one-minute bar, not to 10:10.
    """
    cfg = config or SecondLegDownConfig()
    if not math.isfinite(float(equity)) or float(equity) <= 0:
        raise ValueError("equity must be finite and positive")
    if minute_bars.empty:
        return _empty_candidates()

    frame = build_second_leg_features(
        minute_bars,
        sessions,
        cfg,
        daily_bars=daily_bars,
    )
    execution_index = _execution_timeline(execution_bars, frame)
    rows: list[dict[str, Any]] = []
    for signal_index in range(len(frame)):
        match = match_second_leg_pattern(frame, signal_index, cfg)
        if match is None:
            continue
        first = frame.iloc[match.first_index]
        last = frame.iloc[match.last_index]
        current_instrument = _instrument_for_row(last, instrument)
        signal_time = pd.Timestamp(last["bar_end"])
        reason = ""
        active_time, expires_at = _order_window(
            signal_time,
            execution_index=execution_index,
            signal_frame=frame,
            signal_index=signal_index,
            config=cfg,
        )
        if active_time is None:
            active_time = signal_time
            expires_at = signal_time
            reason = "NO_NEXT_EVENT"

        scale = float(last.get("adjustment_scale", 1.0))
        offset = float(last.get("adjustment_offset", 0.0))
        if not math.isfinite(scale) or scale <= 0 or not math.isfinite(offset):
            raise ValueError("candidate adjustment is invalid")
        signal_trigger = float(last["close"]) - current_instrument.tick_size * scale
        signal_stop = _stop_price(
            frame,
            match.first_index,
            match.last_index,
            signal_trigger,
            cfg,
            current_instrument.tick_size * scale,
        )
        trigger = (signal_trigger - offset) / scale
        stop = (signal_stop - offset) / scale
        if not math.isfinite(stop) or stop <= trigger:
            reason = reason or "INVALID_STRUCTURAL_STOP"

        risk = None
        if not reason:
            risk = size_for_risk_band(
                equity=float(equity),
                entry=trigger,
                stop=stop,
                multiplier=current_instrument.multiplier,
                stressed_round_trip_cost=current_instrument.stressed_round_trip_cost,
                min_risk_pct=cfg.min_risk_pct,
                max_risk_pct=cfg.max_risk_pct,
                max_capital_share=cfg.max_capital_share,
                margin_rate=current_instrument.margin_rate,
                single_lot_capital_share=cfg.single_lot_capital_share,
            )
            reason = risk.reason
        if current_instrument.metadata_asof > signal_time:
            reason = "METADATA_NOT_EFFECTIVE"

        filtered = bool(reason)
        quantity = 0 if filtered or risk is None else risk.quantity
        row = _candidate_row(
            frame=frame,
            first=first,
            last=last,
            first_index=match.first_index,
            signal_index=signal_index,
            pattern_type=match.pattern_type,
            instrument=current_instrument,
            signal_time=signal_time,
            active_time=active_time,
            expires_at=expires_at,
            expires_index=min(signal_index + cfg.order_ttl_bars, len(frame) - 1),
            trigger=trigger,
            stop=stop,
            signal_trigger=signal_trigger,
            signal_stop=signal_stop,
            adjustment_scale=scale,
            adjustment_offset=offset,
            quantity=quantity,
            risk=risk,
            reason=reason,
            sequence=len(rows) + 1,
            volume_surge_mult=cfg.volume_surge_mult,
            volume_baseline_min_samples=cfg.volume_baseline_min_samples,
        )
        rows.append(row)
    if not rows:
        return _empty_candidates()
    return pd.DataFrame(rows, columns=SECOND_LEG_CANDIDATE_COLUMNS)


def _stop_price(
    frame: pd.DataFrame,
    first_index: int,
    last_index: int,
    trigger: float,
    config: SecondLegDownConfig,
    tick_size: float,
) -> float:
    if config.stop_mode == "pattern_open":
        return float(frame.iloc[first_index]["open"])
    if config.stop_mode == "pattern_high":
        pattern_high = frame.iloc[first_index : last_index + 1]["high"].max()
        return float(pattern_high) + tick_size
    return trigger + config.stop_atr_mult * float(frame.iloc[last_index]["atr"])


def _candidate_row(
    *,
    frame: pd.DataFrame,
    first: pd.Series,
    last: pd.Series,
    first_index: int,
    signal_index: int,
    pattern_type: str,
    instrument: SecondLegInstrument,
    signal_time: pd.Timestamp,
    active_time: pd.Timestamp,
    expires_at: pd.Timestamp,
    expires_index: int,
    trigger: float,
    stop: float,
    signal_trigger: float,
    signal_stop: float,
    adjustment_scale: float,
    adjustment_offset: float,
    quantity: int,
    risk: Any,
    reason: str,
    sequence: int,
    volume_surge_mult: float,
    volume_baseline_min_samples: int,
) -> dict[str, Any]:
    filtered = bool(reason)
    signal_structure_low = float(first["structure_low"])
    structure_low = (signal_structure_low - adjustment_offset) / adjustment_scale
    capital_basis = risk.capital_basis if risk is not None else (
        "margin" if instrument.margin_rate is not None else "notional"
    )
    single_lot_relief = int(
        bool(risk is not None and getattr(risk, "single_lot_relief", False))
    )
    candidate_id = f"{instrument.symbol.upper()}-{sequence:06d}"
    contract = str(last.get("contract_code", instrument.contract))
    exchange_trade_date = last.get("exchange_trade_date", signal_time.date())
    return {
        "symbol": instrument.symbol.upper(),
        "exchange": instrument.exchange.upper(),
        "contract": contract,
        "metadata_asof": instrument.metadata_asof,
        "interval": "minute1",
        "datetime": active_time,
        "signal_datetime": signal_time,
        "signal_type": "second_leg_down",
        "setup_type": "second_leg_down",
        "side": "short",
        "direction": -1,
        "direction_value": -1,
        "order_type": "stop",
        "trigger": trigger,
        "stop_price": stop,
        "target_price": math.nan,
        "target_price_virtual": math.nan,
        "quantity": quantity,
        "risk_budget": math.nan if risk is None else risk.risk_budget,
        "loss_per_lot": math.nan if risk is None else risk.loss_per_lot,
        "candidate_status": "filtered" if filtered else "not_triggered",
        "sample_status": "filtered_by_rule" if filtered else "not_triggered_market",
        "is_executed": 0,
        "is_filtered": int(filtered),
        "is_triggered": 0,
        "filtered_reason": reason,
        "rejection_code": reason,
        "feature_asof": signal_time,
        "known_at": signal_time,
        "pivot_time": pd.NaT,
        "structure_known_at": signal_time,
        "decision_asof": signal_time,
        "decision_sequence": 0,
        "order_active_at": active_time,
        "order_active_sequence": 0,
        "order_expire_at": expires_at,
        "order_expire_bar_i": expires_index,
        "fill_time": pd.NaT,
        "daily_feature_asof": last["daily_feature_asof"],
        "daily_direction": int(last["daily_direction"]),
        "daily_bull_trend_id": math.nan,
        "daily_ema5": float(last["daily_ema5"]),
        "daily_ema10": float(last["daily_ema10"]),
        "daily_ema20": float(last["daily_ema20"]),
        "daily_atr14": math.nan,
        "prior_5d_high": math.nan,
        "prior_5d_low": math.nan,
        "nearest_obstacle": math.nan,
        "obstacle_distance_atr": math.nan,
        "feature_atr14": float(last["atr"]) / adjustment_scale,
        "feature_volume": float(last["volume"]),
        "feature_volume_threshold": volume_surge_mult
        * float(last["volume_baseline"]),
        "feature_volume_expanded": int(
            is_volume_surge(
                float(last["volume"]),
                float(last["volume_baseline"]),
                int(last["volume_baseline_samples"]),
                volume_surge_mult,
                volume_baseline_min_samples,
            )
        ),
        "signal_i": signal_index,
        "pullback_start_i": first_index,
        "chase_high_candidate": 0,
        "pattern_type": pattern_type,
        "first_body_atr": safe_ratio(
            abs(float(first["close"]) - float(first["open"])), float(first["atr"])
        ),
        "last_body_atr": safe_ratio(
            abs(float(last["close"]) - float(last["open"])), float(last["atr"])
        ),
        # 整条腿的净跌幅（第一根开盘 → 最后一根收盘）除以同一把 ATR 尺子
        "leg_drop_atr": safe_ratio(
            float(first["open"]) - float(last["close"]), float(last["atr"])
        ),
        "first_upper_wick_ratio": safe_ratio(
            float(first["high"]) - float(first["open"]),
            abs(float(first["close"]) - float(first["open"])),
        ),
        "last_lower_wick_ratio": safe_ratio(
            float(last["close"]) - float(last["low"]),
            abs(float(last["close"]) - float(last["open"])),
        ),
        "first_volume_ratio": safe_ratio(
            float(first["volume"]), float(first["volume_baseline"])
        ),
        "last_volume_ratio": safe_ratio(
            float(last["volume"]), float(last["volume_baseline"])
        ),
        "volume_baseline_samples": int(last["volume_baseline_samples"]),
        "structure_low": structure_low,
        "structure_break": int(
            breaks_structure_low(float(last["low"]), signal_structure_low)
        ),
        "minutes_since_open": int(last["minutes_since_open"]),
        "minutes_until_close": int(last["minutes_until_close"]),
        "single_lot_relief": single_lot_relief,
        "capital_basis": capital_basis,
        "capital_share": 0.0 if risk is None else risk.capital_share,
        "candidate_id": candidate_id,
        "contract_code": contract,
        "exchange_trade_date": exchange_trade_date,
        "setup": "second_leg_down",
        "direction_label": "short",
        "signal_time": signal_time,
        "active_time": active_time,
        "expires_at": expires_at,
        "entry": trigger,
        "stop": stop,
        "target": math.nan,
        "signal_trigger": signal_trigger,
        "signal_stop_price": signal_stop,
        "signal_target_price": math.nan,
        "adjustment_scale": adjustment_scale,
        "adjustment_offset": adjustment_offset,
        "adjustment_version": last.get("adjustment_version", "none"),
    }


def _instrument_for_row(
    row: pd.Series, fallback: SecondLegInstrument
) -> SecondLegInstrument:
    def value(name: str, default: object) -> object:
        found = row.get(name, default)
        return default if found is None or pd.isna(found) else found

    margin = value("instrument_margin_rate_short", fallback.margin_rate)
    return SecondLegInstrument(
        symbol=str(value("symbol", fallback.symbol)),
        exchange=str(value("exchange", fallback.exchange)),
        contract=str(value("contract_code", fallback.contract)),
        tick_size=float(value("instrument_tick_size", fallback.tick_size)),
        multiplier=float(value("instrument_multiplier", fallback.multiplier)),
        stressed_round_trip_cost=float(
            value(
                "instrument_stressed_round_trip_cost",
                fallback.stressed_round_trip_cost,
            )
        ),
        metadata_asof=value("instrument_metadata_asof", fallback.metadata_asof),
        margin_rate=None if margin is None or pd.isna(margin) else float(margin),
    )


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=SECOND_LEG_CANDIDATE_COLUMNS)


__all__ = [
    "SECOND_LEG_AUDIT_COLUMNS",
    "SECOND_LEG_CANDIDATE_COLUMNS",
    "SecondLegInstrument",
    "generate_second_leg_down_candidates",
]
