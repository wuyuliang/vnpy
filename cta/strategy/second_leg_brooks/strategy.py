"""Causal candidate generation for the Brooks second-leg-down strategy."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from cta.strategy.common.setup_pipeline import (
    SetupInstrument,
    execution_timeline,
    order_window,
    safe_ratio,
)
from cta.strategy.common.sizing import size_for_risk_band
from cta.strategy.multi_timeframe_trend_strategy import CANDIDATE_COLUMNS

from .config import SecondLegBrooksConfig
from .rules import build_brooks_features, iter_matches

SETUP_NAME = "second_leg_brooks"

BROOKS_AUDIT_COLUMNS = (
    "pattern_type",
    "first_leg_index",
    "first_leg_atr",
    "pullback_bars",
    "pullback_ratio",
    "rally_high",
    "swing_low",
    "entry_body_atr",
    "entry_lower_wick_ratio",
    "entry_volume_ratio",
    "volume_baseline_samples",
    "break_of_swing_low",
    "minutes_since_open",
    "minutes_until_close",
    "capital_basis",
    "single_lot_relief",
    "capital_share",
)

_REPLAY_COLUMNS = (
    "candidate_id", "contract_code", "exchange_trade_date", "setup",
    "direction_label", "signal_time", "active_time", "expires_at",
    "entry", "stop", "target", "signal_trigger", "signal_stop_price",
    "signal_target_price", "adjustment_scale", "adjustment_offset",
    "adjustment_version",
)

BROOKS_CANDIDATE_COLUMNS = tuple(
    dict.fromkeys((*CANDIDATE_COLUMNS, *BROOKS_AUDIT_COLUMNS, *_REPLAY_COLUMNS))
)


def generate_brooks_candidates(
    signal_bars: pd.DataFrame,
    *,
    sessions: tuple[Any, ...],
    instrument: SetupInstrument,
    config: SecondLegBrooksConfig | None = None,
    equity: float = 1_000_000.0,
    execution_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Generate engine-ready short candidates from completed signal bars.

    ``signal_bars`` carries the signal timeframe; pass the one-minute frame as
    ``execution_bars`` whenever the two differ, because a five-minute bar
    closing at 10:05 is only known at 10:05 and the order it produces belongs
    to the 10:06 one-minute bar.
    """
    cfg = config or SecondLegBrooksConfig()
    if not math.isfinite(float(equity)) or float(equity) <= 0:
        raise ValueError("equity must be finite and positive")
    if signal_bars.empty:
        return _empty_candidates()

    frame = build_brooks_features(signal_bars, sessions, cfg)
    timeline = execution_timeline(execution_bars, frame)
    rows: list[dict[str, Any]] = []
    for entry_index, match in iter_matches(frame, cfg):
        entry = frame.iloc[entry_index]
        swing = frame.iloc[match.first_index]
        current = _instrument_for_row(entry, instrument)
        signal_time = pd.Timestamp(entry["bar_end"])

        reason = ""
        active_time, expires_at = order_window(
            signal_time,
            execution_index=timeline,
            ttl_bars=cfg.order_ttl_bars,
            signal_timeframe_minutes=cfg.signal_timeframe_minutes,
        )
        if active_time is None:
            active_time = expires_at = signal_time
            reason = "NO_NEXT_EVENT"

        scale = float(entry.get("adjustment_scale", 1.0))
        offset = float(entry.get("adjustment_offset", 0.0))
        if not math.isfinite(scale) or scale <= 0 or not math.isfinite(offset):
            raise ValueError("candidate adjustment is invalid")
        tick = current.tick_size * scale
        signal_trigger = float(entry["close"]) - tick
        # 止损放回抽高点上方：回抽高点被突破，第二段的假设就被证伪了
        signal_stop = float(match.extras["rally_high"]) + cfg.stop_buffer_ticks * tick
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
                multiplier=current.multiplier,
                stressed_round_trip_cost=current.stressed_round_trip_cost,
                min_risk_pct=cfg.min_risk_pct,
                max_risk_pct=cfg.max_risk_pct,
                max_capital_share=cfg.max_capital_share,
                margin_rate=current.margin_rate,
                single_lot_capital_share=cfg.single_lot_capital_share,
            )
            reason = risk.reason
        if current.metadata_asof > signal_time:
            reason = "METADATA_NOT_EFFECTIVE"

        filtered = bool(reason)
        rows.append(_candidate_row(
            entry=entry,
            swing=swing,
            match=match,
            entry_index=entry_index,
            instrument=current,
            signal_time=signal_time,
            active_time=active_time,
            expires_at=expires_at,
            expires_index=min(
                entry_index + cfg.order_ttl_bars, len(frame) - 1
            ),
            trigger=trigger,
            stop=stop,
            signal_trigger=signal_trigger,
            signal_stop=signal_stop,
            adjustment_scale=scale,
            adjustment_offset=offset,
            quantity=0 if filtered or risk is None else risk.quantity,
            risk=risk,
            reason=reason,
            sequence=len(rows) + 1,
            config=cfg,
        ))
    if not rows:
        return _empty_candidates()
    return pd.DataFrame(rows, columns=BROOKS_CANDIDATE_COLUMNS)


def _candidate_row(
    *,
    entry: pd.Series,
    swing: pd.Series,
    match: Any,
    entry_index: int,
    instrument: SetupInstrument,
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
    config: SecondLegBrooksConfig,
) -> dict[str, Any]:
    filtered = bool(reason)
    candidate_id = f"{instrument.symbol.upper()}-{sequence:06d}"
    contract = str(entry.get("contract_code", instrument.contract))
    body = abs(float(entry["close"]) - float(entry["open"]))
    return {
        "symbol": instrument.symbol.upper(),
        "exchange": instrument.exchange.upper(),
        "contract": contract,
        "metadata_asof": instrument.metadata_asof,
        "interval": "minute1",
        "datetime": active_time,
        "signal_datetime": signal_time,
        "signal_type": SETUP_NAME,
        "setup_type": SETUP_NAME,
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
        "pivot_time": pd.Timestamp(swing["bar_end"]),
        "structure_known_at": signal_time,
        "decision_asof": signal_time,
        "decision_sequence": 0,
        "order_active_at": active_time,
        "order_active_sequence": 0,
        "order_expire_at": expires_at,
        "order_expire_bar_i": expires_index,
        "fill_time": pd.NaT,
        "daily_feature_asof": pd.NaT,
        "daily_direction": math.nan,
        "daily_bull_trend_id": math.nan,
        "daily_ema5": math.nan,
        "daily_ema10": math.nan,
        "daily_ema20": math.nan,
        "daily_atr14": math.nan,
        "prior_5d_high": math.nan,
        "prior_5d_low": math.nan,
        "nearest_obstacle": math.nan,
        "obstacle_distance_atr": math.nan,
        "feature_atr14": float(entry["atr"]) / adjustment_scale,
        "feature_volume": float(entry["volume"]),
        "feature_volume_threshold": config.entry_volume_mult
        * float(entry["volume_baseline"]),
        "feature_volume_expanded": 1,
        "signal_i": entry_index,
        "pullback_start_i": match.first_index,
        "chase_high_candidate": 0,
        # --- 审计列 ---
        "pattern_type": match.pattern_type,
        "first_leg_index": match.first_index - entry_index,
        "first_leg_atr": safe_ratio(
            match.extras["first_leg"], float(entry["atr"])
        ),
        "pullback_bars": int(match.extras["pullback_bars"]),
        "pullback_ratio": float(match.extras["pullback_ratio"]),
        "rally_high": float(match.extras["rally_high"]),
        "swing_low": float(match.extras["swing_low"]),
        "entry_body_atr": safe_ratio(body, float(entry["atr"])),
        "entry_lower_wick_ratio": safe_ratio(
            float(entry["close"]) - float(entry["low"]), body
        ),
        "entry_volume_ratio": safe_ratio(
            float(entry["volume"]), float(entry["volume_baseline"])
        ),
        "volume_baseline_samples": int(entry["volume_baseline_samples"]),
        "break_of_swing_low": float(match.extras["swing_low"]) - float(entry["low"]),
        "minutes_since_open": int(entry["minutes_since_open"]),
        "minutes_until_close": int(entry["minutes_until_close"]),
        "capital_basis": "margin" if instrument.margin_rate is not None else "notional",
        "single_lot_relief": int(
            bool(risk is not None and getattr(risk, "single_lot_relief", False))
        ),
        "capital_share": 0.0 if risk is None else risk.capital_share,
        # --- replay 列 ---
        "candidate_id": candidate_id,
        "contract_code": contract,
        "exchange_trade_date": entry.get("exchange_trade_date", signal_time.date()),
        "setup": SETUP_NAME,
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
        "adjustment_version": entry.get("adjustment_version", "none"),
    }


def _instrument_for_row(row: pd.Series, fallback: SetupInstrument) -> SetupInstrument:
    def value(name: str, default: object) -> object:
        found = row.get(name, default)
        return default if found is None or pd.isna(found) else found

    margin = value("instrument_margin_rate_short", fallback.margin_rate)
    return SetupInstrument(
        symbol=str(value("symbol", fallback.symbol)),
        exchange=str(value("exchange", fallback.exchange)),
        contract=str(value("contract_code", fallback.contract)),
        tick_size=float(value("instrument_tick_size", fallback.tick_size)),
        multiplier=float(value("instrument_multiplier", fallback.multiplier)),
        stressed_round_trip_cost=float(value(
            "instrument_stressed_round_trip_cost",
            fallback.stressed_round_trip_cost,
        )),
        metadata_asof=value("instrument_metadata_asof", fallback.metadata_asof),
        margin_rate=None if margin is None or pd.isna(margin) else float(margin),
    )


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=BROOKS_CANDIDATE_COLUMNS)


__all__ = [
    "BROOKS_AUDIT_COLUMNS",
    "BROOKS_CANDIDATE_COLUMNS",
    "SETUP_NAME",
    "SetupInstrument",
    "generate_brooks_candidates",
]
