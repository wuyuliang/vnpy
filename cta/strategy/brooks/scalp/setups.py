"""Deterministic 5-minute Brooks setup detectors."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import math
from typing import Any

import pandas as pd

from cta.strategy.brooks.scalp.regime import RegimeSnapshot, RegimeState


class SetupType(str, Enum):
    SECOND_ENTRY_CONTINUATION = "second_entry_continuation"
    STRONG_BREAKOUT_FOLLOW_THROUGH = "strong_breakout_follow_through"
    FAILED_BREAKOUT_RANGE_FADE = "failed_breakout_range_fade"


class SecondEntryState(str, Enum):
    IDLE = "IDLE"
    PULLBACK_LEG_1 = "PULLBACK_LEG_1"
    FIRST_SIGNAL_ARMED = "FIRST_SIGNAL_ARMED"
    FIRST_ATTEMPT_FAILED = "FIRST_ATTEMPT_FAILED"
    PULLBACK_LEG_2 = "PULLBACK_LEG_2"
    SECOND_ENTRY_READY = "SECOND_ENTRY_READY"


@dataclass(frozen=True)
class SetupCandidate:
    candidate_id: str
    rule_id: str
    symbol: str
    contract_code: str
    direction: int
    regime: RegimeState
    setup_bar_end: datetime
    trigger_price: float
    structural_stop: float
    available_space_price: float
    feature_source_max: datetime
    expires_after_bar_end: datetime
    context: dict[str, float | int | str]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["regime"] = self.regime.value
        for key in ("setup_bar_end", "feature_source_max", "expires_after_bar_end"):
            result[key] = result[key].isoformat()
        return result


@dataclass(frozen=True)
class SecondEntryUpdate:
    state: SecondEntryState
    candidate: SetupCandidate | None = None
    reset_reason: str = ""


@dataclass
class SecondEntryTracker:
    symbol: str
    direction: int
    price_tick: float
    state: SecondEntryState = SecondEntryState.IDLE
    _bars: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _pullback: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _impulse_ready: bool = field(default=False, repr=False)
    _first_trigger: float | None = field(default=None, repr=False)
    _first_stop: float | None = field(default=None, repr=False)
    _first_attempt_triggered: bool = field(default=False, repr=False)
    _shadow_mfe_price: float | None = field(default=None, repr=False)
    _failure_bar: dict[str, Any] | None = field(default=None, repr=False)
    _last_reset_reason: str = field(default="", repr=False)
    _reset_history: list[str] = field(default_factory=list, repr=False)
    _contract_code: str = field(default="", repr=False)
    _session_id: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be 1 or -1")
        if self.price_tick <= 0:
            raise ValueError("price_tick must be positive")

    def update(self, bar: dict[str, Any] | pd.Series, regime: RegimeSnapshot) -> SecondEntryUpdate:
        row = dict(bar)
        reset_reason = self._context_reset_reason(row, regime)
        if reset_reason:
            self.reset(reset_reason)
            self._record_bar(row)
            return SecondEntryUpdate(self.state, reset_reason=reset_reason)

        if self.state is not SecondEntryState.IDLE and len(self._pullback) >= 10:
            self.reset("pullback_timeout")
            self._record_bar(row)
            return SecondEntryUpdate(self.state, reset_reason="pullback_timeout")

        prior = self._bars[-1] if self._bars else None
        if self.state is SecondEntryState.IDLE:
            if self._impulse_ready and prior is not None and self._starts_pullback(row, prior):
                self.state = SecondEntryState.PULLBACK_LEG_1
                self._pullback = [row]
                self._impulse_ready = False
            else:
                self._record_bar(row)
                self._impulse_ready = self._has_impulse()
                return SecondEntryUpdate(self.state)
        elif self.state is SecondEntryState.PULLBACK_LEG_1:
            self._pullback.append(row)
            if self._signal_quality(row):
                self._first_trigger, self._first_stop = self._shadow_levels(row)
                self._first_attempt_triggered = False
                self._shadow_mfe_price = None
                self.state = SecondEntryState.FIRST_SIGNAL_ARMED
        elif self.state is SecondEntryState.FIRST_SIGNAL_ARMED:
            self._pullback.append(row)
            self._update_shadow_attempt(row)
            if self._first_attempt_triggered and self._shadow_reached_half_r(row):
                self.reset("first_attempt_mfe_reached")
                self._record_bar(row)
                return SecondEntryUpdate(self.state, reset_reason="first_attempt_mfe_reached")
            if self._first_attempt_triggered and self._shadow_failed(row):
                self._failure_bar = row
                self.state = SecondEntryState.FIRST_ATTEMPT_FAILED
        elif self.state is SecondEntryState.FIRST_ATTEMPT_FAILED:
            self._pullback.append(row)
            if self._extends_second_leg(row):
                self.state = SecondEntryState.PULLBACK_LEG_2
        elif self.state is SecondEntryState.PULLBACK_LEG_2:
            self._pullback.append(row)
            if self._signal_quality(row):
                self.state = SecondEntryState.SECOND_ENTRY_READY
                candidate = self._candidate(row, regime)
                self._record_bar(row)
                return SecondEntryUpdate(self.state, candidate=candidate)
        else:
            self.reset("candidate_emitted")

        self._record_bar(row)
        return SecondEntryUpdate(self.state)

    def reset(self, reason: str = "") -> None:
        if reason:
            self._last_reset_reason = reason
            self._reset_history.append(reason)
            self._reset_history = self._reset_history[-10:]
        self.state = SecondEntryState.IDLE
        self._pullback.clear()
        self._impulse_ready = False
        self._first_trigger = None
        self._first_stop = None
        self._first_attempt_triggered = False
        self._shadow_mfe_price = None
        self._failure_bar = None

    def _context_reset_reason(self, row: dict[str, Any], regime: RegimeSnapshot) -> str:
        expected = RegimeState.STRONG_TREND_UP if self.direction > 0 else RegimeState.STRONG_TREND_DOWN
        if regime.state is not expected or regime.direction != self.direction:
            return "regime_changed" if self.state is not SecondEntryState.IDLE else ""
        contract = str(row.get("contract_code", ""))
        if self._contract_code and contract != self._contract_code:
            return "contract_changed"
        session = str(row.get("session_id", ""))
        if self._session_id and session and session != self._session_id:
            return "session_changed"
        if bool(row.get("roll_freeze", False)):
            return "roll_freeze"
        if bool(row.get("data_interrupted", False)):
            return "data_interrupted"
        return ""

    def _record_bar(self, row: dict[str, Any]) -> None:
        self._bars.append(row)
        self._bars = self._bars[-12:]
        self._contract_code = str(row.get("contract_code", self._contract_code))
        session = str(row.get("session_id", ""))
        if session:
            self._session_id = session

    def _has_impulse(self) -> bool:
        if len(self._bars) < 3:
            return False
        bars = self._bars[-3:]
        atr = _positive_float(bars[-1].get("atr_14"))
        if atr is None:
            return False
        directional = sum(
            1 for bar in bars if self.direction * (float(bar["close"]) - float(bar["open"])) > 0
        )
        net = self.direction * (float(bars[-1]["close"]) - float(bars[0]["open"]))
        return directional >= 2 and net >= 0.50 * atr

    def _starts_pullback(self, row: dict[str, Any], prior: dict[str, Any]) -> bool:
        if self.direction > 0:
            return float(row["close"]) < float(row["open"]) or float(row["low"]) < float(prior["low"])
        return float(row["close"]) > float(row["open"]) or float(row["high"]) > float(prior["high"])

    def _signal_quality(self, row: dict[str, Any]) -> bool:
        high = float(row["high"])
        low = float(row["low"])
        open_ = float(row["open"])
        close = float(row["close"])
        bar_range = high - low
        atr = _positive_float(row.get("atr_14"))
        ema = _finite_float(row.get("ema_20"))
        if bar_range <= 0 or atr is None or ema is None or bar_range > 1.20 * atr:
            return False
        if abs(close - open_) / bar_range < 0.35:
            return False
        if self.direction > 0:
            depth = _finite_float(row.get("pullback_depth_long_20"))
            return bool(
                depth is not None
                and 0.10 <= depth <= 0.60
                and low <= ema + 0.25 * atr
                and close >= ema - 0.25 * atr
                and int(row.get("pa_bull_reversal", 0)) == 1
                and (close - low) / bar_range >= 0.65
                and int(row.get("pa_buy_climax", 0)) == 0
            )
        depth = _finite_float(row.get("pullback_depth_short_20"))
        return bool(
            depth is not None
            and 0.10 <= depth <= 0.60
            and high >= ema - 0.25 * atr
            and close <= ema + 0.25 * atr
            and int(row.get("pa_bear_reversal", 0)) == 1
            and (high - close) / bar_range >= 0.65
            and int(row.get("pa_sell_climax", 0)) == 0
        )

    def _shadow_levels(self, row: dict[str, Any]) -> tuple[float, float]:
        if self.direction > 0:
            return float(row["high"]) + self.price_tick, float(row["low"]) - self.price_tick
        return float(row["low"]) - self.price_tick, float(row["high"]) + self.price_tick

    def _shadow_reached_half_r(self, row: dict[str, Any]) -> bool:
        assert self._first_trigger is not None and self._first_stop is not None
        risk = abs(self._first_trigger - self._first_stop)
        if self.direction > 0:
            return float(row["high"]) >= self._first_trigger + 0.50 * risk
        return float(row["low"]) <= self._first_trigger - 0.50 * risk

    def _update_shadow_attempt(self, row: dict[str, Any]) -> None:
        assert self._first_trigger is not None
        if not self._first_attempt_triggered:
            self._first_attempt_triggered = (
                float(row["high"]) >= self._first_trigger
                if self.direction > 0
                else float(row["low"]) <= self._first_trigger
            )
        if not self._first_attempt_triggered:
            return
        price = float(row["high"] if self.direction > 0 else row["low"])
        if self._shadow_mfe_price is None:
            self._shadow_mfe_price = price
        elif self.direction > 0:
            self._shadow_mfe_price = max(self._shadow_mfe_price, price)
        else:
            self._shadow_mfe_price = min(self._shadow_mfe_price, price)

    def _shadow_failed(self, row: dict[str, Any]) -> bool:
        assert self._first_stop is not None
        if self.direction > 0:
            return float(row["low"]) < self._first_stop
        return float(row["high"]) > self._first_stop

    def _extends_second_leg(self, row: dict[str, Any]) -> bool:
        assert self._failure_bar is not None
        if self.direction > 0:
            return (
                float(row["low"]) <= float(self._failure_bar["low"])
                or float(row["close"]) < float(row["open"])
            )
        return (
            float(row["high"]) >= float(self._failure_bar["high"])
            or float(row["close"]) > float(row["open"])
        )

    def _candidate(self, row: dict[str, Any], regime: RegimeSnapshot) -> SetupCandidate:
        if self.direction > 0:
            trigger = _round_up(float(row["high"]) + self.price_tick, self.price_tick)
            stop = _round_down(min(float(bar["low"]) for bar in self._pullback) - self.price_tick, self.price_tick)
        else:
            trigger = _round_down(float(row["low"]) - self.price_tick, self.price_tick)
            stop = _round_up(max(float(bar["high"]) for bar in self._pullback) + self.price_tick, self.price_tick)
        return _make_candidate(
            SetupType.SECOND_ENTRY_CONTINUATION,
            self.symbol,
            str(row["contract_code"]),
            self.direction,
            regime,
            row,
            trigger,
            stop,
            0.0,
            {
                "second_entry_state": self.state.value,
                "shadow_first_attempt_failed": 1,
                "first_attempt_triggered": int(self._first_attempt_triggered),
                "first_trigger": float(self._first_trigger or 0.0),
                "first_stop": float(self._first_stop or 0.0),
                "shadow_mfe_price": float(self._shadow_mfe_price or 0.0),
                "failure_bar_end": str(
                    self._failure_bar.get("bar_end", "") if self._failure_bar else ""
                ),
                "failure_bar_low": float(
                    self._failure_bar.get("low", 0.0) if self._failure_bar else 0.0
                ),
                "failure_bar_high": float(
                    self._failure_bar.get("high", 0.0) if self._failure_bar else 0.0
                ),
                "last_reset_reason": self._last_reset_reason,
                "reset_history": "|".join(self._reset_history),
            },
        )


def detect_setups(
    completed_5m: pd.DataFrame,
    latest_regime: RegimeSnapshot,
    spec: object,
    config: object | None = None,
    *,
    symbol: str | None = None,
    second_entry_tracker: SecondEntryTracker | None = None,
) -> list[SetupCandidate]:
    """Return at most one candidate using the frozen setup priority."""
    del config
    if len(completed_5m) < 1:
        return []
    tick = float(spec.price_tick)
    if tick <= 0:
        raise ValueError("spec.price_tick must be positive")
    vt_symbol = symbol or str(getattr(spec, "root_symbol", ""))
    if not vt_symbol:
        raise ValueError("symbol is required")
    current = completed_5m.iloc[-1]
    contract = str(current.get("contract_code", ""))
    if contract != latest_regime.source_contract:
        return []

    if second_entry_tracker is not None:
        update = second_entry_tracker.update(current, latest_regime)
        if update.candidate is not None:
            return [update.candidate]

    breakout = _breakout_follow_through(completed_5m, latest_regime, vt_symbol, tick)
    if breakout is not None:
        return [breakout]
    range_fade = _failed_breakout_range_fade(completed_5m, latest_regime, vt_symbol, tick)
    return [range_fade] if range_fade is not None else []


def _breakout_follow_through(
    bars: pd.DataFrame,
    regime: RegimeSnapshot,
    symbol: str,
    tick: float,
) -> SetupCandidate | None:
    if len(bars) < 2 or regime.state not in {
        RegimeState.STRONG_TREND_UP,
        RegimeState.STRONG_TREND_DOWN,
        RegimeState.TIGHT_RANGE_BREAKOUT_MODE,
    }:
        return None
    previous = bars.iloc[-2]
    current = bars.iloc[-1]
    atr = _positive_float(current.get("atr_14"))
    if atr is None:
        return None
    prior_mid = (float(previous["high"]) + float(previous["low"])) / 2.0
    bar_range = float(current["high"]) - float(current["low"])
    if bar_range <= 0 or bar_range > 1.50 * atr:
        return None

    allow_long = regime.state is RegimeState.TIGHT_RANGE_BREAKOUT_MODE or regime.direction == 1
    allow_short = regime.state is RegimeState.TIGHT_RANGE_BREAKOUT_MODE or regime.direction == -1
    long_ok = allow_long and bool(
        int(previous.get("pa_breakout_up_10", 0)) == 1
        and float(previous.get("pa_breakout_strength_10", 0.0)) >= 0.35
        and float(previous.get("pa_bo_body_ratio_10", 0.0)) >= 0.60
        and float(previous.get("pa_bo_close_pos_10", 0.5)) >= 0.80
        and float(previous.get("volume_ratio_prev20", 0.0)) >= 1.20
        and float(current["close"]) > float(current["open"])
        and float(current["close"]) > float(previous["close"])
        and float(current["low"]) >= prior_mid - 0.10 * atr
        and float(current.get("pa_follow_through_10", 0.0)) > 0
        and int(current.get("pa_buy_climax", 0)) == 0
    )
    short_ok = allow_short and bool(
        int(previous.get("pa_breakout_down_10", 0)) == 1
        and float(previous.get("pa_breakout_strength_10", 0.0)) <= -0.35
        and float(previous.get("pa_bo_body_ratio_10", 0.0)) >= 0.60
        and float(previous.get("pa_bo_close_pos_10", 0.5)) <= 0.20
        and float(previous.get("volume_ratio_prev20", 0.0)) >= 1.20
        and float(current["close"]) < float(current["open"])
        and float(current["close"]) < float(previous["close"])
        and float(current["high"]) <= prior_mid + 0.10 * atr
        and float(current.get("pa_follow_through_10", 0.0)) < 0
        and int(current.get("pa_sell_climax", 0)) == 0
    )
    if not long_ok and not short_ok:
        return None
    direction = 1 if long_ok else -1
    if direction > 0:
        trigger = _round_up(float(current["high"]) + tick, tick)
        stop = _round_down(min(float(previous["low"]), float(current["low"])) - tick, tick)
    else:
        trigger = _round_down(float(current["low"]) - tick, tick)
        stop = _round_up(max(float(previous["high"]), float(current["high"])) + tick, tick)
    return _make_candidate(
        SetupType.STRONG_BREAKOUT_FOLLOW_THROUGH,
        symbol,
        str(current["contract_code"]),
        direction,
        regime,
        current,
        trigger,
        stop,
        0.0,
        {"breakout_bar_end": _iso(previous["bar_end"]), "plan_pending": 1},
    )


def _failed_breakout_range_fade(
    bars: pd.DataFrame,
    regime: RegimeSnapshot,
    symbol: str,
    tick: float,
) -> SetupCandidate | None:
    if len(bars) < 2 or regime.state is not RegimeState.WIDE_TRADING_RANGE:
        return None
    previous = bars.iloc[-2]
    current = bars.iloc[-1]
    contract = str(current["contract_code"])
    long_trigger = _round_up(float(current["high"]) + tick, tick)
    short_trigger = _round_down(float(current["low"]) - tick, tick)
    height = regime.range_high - regime.range_low
    long_ok = bool(
        float(previous["low"]) < regime.range_low - tick
        and float(previous["close"]) > regime.range_low
        and float(current["close"]) > float(current["open"])
        and float(current["close"]) > float(previous["close"])
        and float(current["low"]) >= float(previous["low"])
        and int(current.get("pa_bull_reversal", 0)) == 1
        and long_trigger <= regime.range_low + height / 3.0
    )
    short_ok = bool(
        float(previous["high"]) > regime.range_high + tick
        and float(previous["close"]) < regime.range_high
        and float(current["close"]) < float(current["open"])
        and float(current["close"]) < float(previous["close"])
        and float(current["high"]) <= float(previous["high"])
        and int(current.get("pa_bear_reversal", 0)) == 1
        and short_trigger >= regime.range_high - height / 3.0
    )
    if not long_ok and not short_ok:
        return None
    direction = 1 if long_ok else -1
    trigger = long_trigger if direction > 0 else short_trigger
    stop = (
        _round_down(float(previous["low"]) - tick, tick)
        if direction > 0
        else _round_up(float(previous["high"]) + tick, tick)
    )
    risk = abs(trigger - stop)
    available = abs(regime.range_mid - trigger)
    if risk <= 0 or available < 1.20 * risk:
        return None
    return _make_candidate(
        SetupType.FAILED_BREAKOUT_RANGE_FADE,
        symbol,
        contract,
        direction,
        regime,
        current,
        trigger,
        stop,
        available,
        {"obstacle": regime.range_mid, "range_snapshot_asof": regime.feature_asof.isoformat()},
    )


def _make_candidate(
    setup_type: SetupType,
    symbol: str,
    contract: str,
    direction: int,
    regime: RegimeSnapshot,
    row: pd.Series | dict[str, Any],
    trigger: float,
    stop: float,
    available: float,
    context: dict[str, float | int | str],
) -> SetupCandidate:
    setup_end = _aware(row["bar_end"])
    source_max = _aware(row.get("source_max_bar_end", row["bar_end"]))
    if source_max > setup_end:
        raise ValueError("feature source is later than setup bar")
    raw_id = f"{symbol}|{contract}|{setup_type.value}|{direction}|{setup_end.isoformat()}"
    candidate_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24]
    return SetupCandidate(
        candidate_id=candidate_id,
        rule_id=setup_type.value,
        symbol=symbol,
        contract_code=contract,
        direction=direction,
        regime=regime.state,
        setup_bar_end=setup_end,
        trigger_price=trigger,
        structural_stop=stop,
        available_space_price=available,
        feature_source_max=source_max,
        expires_after_bar_end=(pd.Timestamp(setup_end) + pd.Timedelta(minutes=3)).to_pydatetime(),
        context=dict(context),
    )


def _round_up(value: float, tick: float) -> float:
    return round(math.ceil((value - 1e-12) / tick) * tick, 10)


def _round_down(value: float, tick: float) -> float:
    return round(math.floor((value + 1e-12) / tick) * tick, 10)


def _finite_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _positive_float(value: object) -> float | None:
    result = _finite_float(value)
    return result if result is not None and result > 0 else None


def _aware(value: object) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("setup timestamps must be timezone aware")
    return timestamp.to_pydatetime()


def _iso(value: object) -> str:
    return _aware(value).isoformat()


__all__ = [
    "SecondEntryState",
    "SecondEntryTracker",
    "SecondEntryUpdate",
    "SetupCandidate",
    "SetupType",
    "detect_setups",
]
