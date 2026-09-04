"""Seven-state Brooks Market Cycle classifier with causal hysteresis."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from ..config import BrooksCycleConfig
from .types import CycleSnapshot, EventKey, MarketCycle, RangeSubtype, TrendSnapshot


REQUIRED_STATE_COLUMNS = frozenset(
    {
        "open", "high", "low", "close", "body", "bar_range", "body_ratio",
        "close_pos_long", "close_pos_short", "atr", "ema_slope", "ema_distance",
        "ema_separation", "trend_efficiency", "overlap_ratio", "overlap_ratio_recent",
        "direction_change_ratio", "bull_trend_bar_ratio", "bear_trend_bar_ratio",
        "close_pos_long_mean", "close_pos_short_mean", "prior_high", "prior_low",
        "breakout_distance_long", "breakout_distance_short",
        "bull_trend_bar_count_recent", "bear_trend_bar_count_recent",
        "hh_score", "hl_score", "lh_score", "ll_score", "range_high", "range_low",
        "range_mid", "range_width_atr", "range_width_cost_multiple", "range_aux_gate",
        "bar_range_percentile", "cumulative_move_atr", "momentum_decay",
        "bull_channel_age", "bear_channel_age", "bull_channel_slope_atr",
        "bear_channel_slope_atr", "bull_median_pullback_bars",
        "bear_median_pullback_bars", "bull_median_pullback_depth",
        "bear_median_pullback_depth", "bull_max_pullback_depth",
        "bear_max_pullback_depth", "bull_ema_cross_count", "bear_ema_cross_count",
        "bull_opposite_trend_bar_ratio", "bear_opposite_trend_bar_ratio",
    }
)

PRECEDENCE = (
    MarketCycle.STRONG_BULL_BREAKOUT,
    MarketCycle.STRONG_BEAR_BREAKOUT,
    MarketCycle.BULL_TIGHT_CHANNEL,
    MarketCycle.BEAR_TIGHT_CHANNEL,
    MarketCycle.BULL_BROAD_CHANNEL,
    MarketCycle.BEAR_BROAD_CHANNEL,
    MarketCycle.TRADING_RANGE,
)
PRECEDENCE_RANK = {state: -index for index, state in enumerate(PRECEDENCE)}


@dataclass(frozen=True)
class DirectionalView:
    d: int
    pressure: float
    breakout_distance: float
    structure_score: float
    close_pos: float
    close_strength: float
    trend_bar_ratio: float
    trend_bar_count_recent: int
    channel_age: int
    channel_slope_strength: float
    median_pullback_bars: float
    median_pullback_depth: float
    max_pullback_depth: float
    ema_cross_count: int
    opposite_trend_bar_ratio: float
    parts: Mapping[str, float]


class _Hysteresis:
    def __init__(self) -> None:
        self.current = MarketCycle.UNAVAILABLE
        self.bars_in_current = 0
        self.pending: MarketCycle | None = None
        self.pending_count = 0

    def unavailable(self) -> None:
        self.current = MarketCycle.UNAVAILABLE
        self.bars_in_current = 0
        self.pending = None
        self.pending_count = 0

    def choose(
        self,
        scores: Mapping[MarketCycle, float],
        config: BrooksCycleConfig,
        *,
        force_transition: bool,
    ) -> MarketCycle:
        if force_transition:
            self._set(MarketCycle.TRANSITION)
            return self.current
        challenger = max(
            scores, key=lambda state: (scores[state], PRECEDENCE_RANK[state])
        )
        challenger_score = scores[challenger]
        current_score = scores.get(self.current, 0.0)
        if challenger_score < config.cycle.enter_score:
            if self.current in {MarketCycle.UNAVAILABLE, MarketCycle.TRANSITION}:
                self._set(MarketCycle.TRANSITION)
            else:
                self.bars_in_current += 1
                self.pending = None
                self.pending_count = 0
            return self.current
        if self.current is MarketCycle.UNAVAILABLE:
            self.current = MarketCycle.TRANSITION
            self.bars_in_current = 1
        if challenger == self.current:
            self.bars_in_current += 1
            self.pending = None
            self.pending_count = 0
            return self.current
        if self.pending == challenger:
            self.pending_count += 1
        else:
            self.pending = challenger
            self.pending_count = 1
        old_ready = (
            self.current in {MarketCycle.UNAVAILABLE, MarketCycle.TRANSITION}
            or self.bars_in_current >= config.cycle.min_state_bars
        )
        if (
            challenger_score - current_score >= config.cycle.switch_margin
            and self.pending_count >= config.cycle.confirm_bars
            and old_ready
        ):
            self._set(challenger)
        else:
            self.bars_in_current += 1
        return self.current

    def _set(self, state: MarketCycle) -> None:
        if self.current == state:
            self.bars_in_current += 1
        else:
            self.current = state
            self.bars_in_current = 1
        self.pending = None
        self.pending_count = 0


def classify_market_cycles(
    features: pd.DataFrame,
    config: BrooksCycleConfig,
) -> pd.DataFrame:
    """Classify every completed prefix and preserve state-machine history."""
    missing = sorted((REQUIRED_STATE_COLUMNS | {"bar_end"}).difference(features.columns))
    if missing:
        raise ValueError(f"missing market-cycle columns: {','.join(missing)}")
    if "feature_sequence" not in features:
        raise ValueError("feature_sequence is required")
    machine = _Hysteresis()
    rows: list[dict[str, object]] = []
    prior_bull = math.nan
    prior_bear = math.nan
    directional_strength_history: list[float] = []
    lookback = config.features.percentile_lookback
    for position, (_, row) in enumerate(features.iterrows()):
        timestamp = pd.Timestamp(row["bar_end"])
        if timestamp.tzinfo is None:
            raise ValueError("bar_end must be timezone-aware")
        event = EventKey(timestamp.to_pydatetime(), int(row["feature_sequence"]))
        if position < lookback:
            snapshot = CycleSnapshot.unavailable(event, "INSUFFICIENT_CAUSAL_HISTORY")
            machine.unavailable()
            rows.append(_snapshot_row(snapshot))
            continue
        history = features.iloc[position - lookback : position]
        if not all(_finite(row[name]) for name in REQUIRED_STATE_COLUMNS):
            directional_strength_history.append(math.nan)
            machine.unavailable()
            rows.append(
                _snapshot_row(
                    CycleSnapshot.unavailable(event, "NON_FINITE_REQUIRED_STATE_INPUT")
                )
            )
            continue
        try:
            bull = directional_view(row, history, 1)
            bear = directional_view(row, history, -1)
        except (KeyError, ValueError):
            directional_strength_history.append(math.nan)
            machine.unavailable()
            rows.append(
                _snapshot_row(
                    CycleSnapshot.unavailable(event, "NON_FINITE_REQUIRED_STATE_INPUT")
                )
            )
            continue
        current_directional_strength = abs(bull.pressure - bear.pressure)
        prior_strength = pd.Series(
            directional_strength_history[-lookback:], dtype=float
        )
        if len(prior_strength) < lookback or prior_strength.isna().any():
            directional_strength_history.append(current_directional_strength)
            prior_bull = bull.pressure
            prior_bear = bear.pressure
            machine.unavailable()
            rows.append(
                _snapshot_row(
                    CycleSnapshot.unavailable(event, "INSUFFICIENT_PRESSURE_HISTORY")
                )
            )
            continue
        snapshot = _classify_one(
            row,
            history,
            event,
            config,
            machine,
            bull=bull,
            bear=bear,
            historical_directional_strength=prior_strength,
            prior_bull=prior_bull,
            prior_bear=prior_bear,
        )
        directional_strength_history.append(current_directional_strength)
        if math.isfinite(snapshot.bull_pressure):
            prior_bull = snapshot.bull_pressure
            prior_bear = snapshot.bear_pressure
        rows.append(_snapshot_row(snapshot))
    return pd.DataFrame(rows, index=features.index)


def _classify_one(
    row: pd.Series,
    history: pd.DataFrame,
    event: EventKey,
    config: BrooksCycleConfig,
    machine: _Hysteresis,
    *,
    bull: DirectionalView,
    bear: DirectionalView,
    historical_directional_strength: pd.Series,
    prior_bull: float,
    prior_bear: float,
) -> CycleSnapshot:
    try:
        directional_strength = abs(bull.pressure - bear.pressure)
        direction = (
            int(np.sign(bull.pressure - bear.pressure))
            if directional_strength >= config.cycle.direction_min_pressure_margin
            else 0
        )
        scores = {
            MarketCycle.STRONG_BULL_BREAKOUT: _score_breakout(row, history, bull, config),
            MarketCycle.BULL_TIGHT_CHANNEL: _score_tight(row, history, bull, direction, config),
            MarketCycle.BULL_BROAD_CHANNEL: _score_broad(row, history, bull, direction, config),
            MarketCycle.TRADING_RANGE: _score_range(
                row,
                history,
                directional_strength,
                historical_directional_strength,
                config,
            ),
            MarketCycle.BEAR_BROAD_CHANNEL: _score_broad(row, history, bear, direction, config),
            MarketCycle.BEAR_TIGHT_CHANNEL: _score_tight(row, history, bear, direction, config),
            MarketCycle.STRONG_BEAR_BREAKOUT: _score_breakout(row, history, bear, config),
        }
    except (KeyError, ValueError):
        machine.unavailable()
        return CycleSnapshot.unavailable(event, "NON_FINITE_REQUIRED_STATE_INPUT")
    if not all(_finite(score) for score in scores.values()):
        machine.unavailable()
        return CycleSnapshot.unavailable(event, "NON_FINITE_REQUIRED_STATE_INPUT")

    ordered = sorted(scores.values(), reverse=True)
    top, second = ordered[0], ordered[1]
    confidence = min(
        1.0,
        max(0.0, top - second) / config.cycle.confidence_full_margin,
    )
    current_directional = machine.current not in {
        MarketCycle.UNAVAILABLE, MarketCycle.TRANSITION, MarketCycle.TRADING_RANGE
    }
    climax = (
        float(row["bar_range_percentile"]) >= config.cycle.climax_range_percentile
        and abs(float(row["ema_distance"])) >= config.cycle.climax_ema_distance_atr
        and float(row["cumulative_move_atr"]) >= config.cycle.climax_cumulative_move_atr
        and float(row["momentum_decay"]) >= config.cycle.transition_min_momentum_decay
    )
    opposite_shock = False
    if machine.current in {
        MarketCycle.STRONG_BULL_BREAKOUT,
        MarketCycle.BULL_TIGHT_CHANNEL,
        MarketCycle.BULL_BROAD_CHANNEL,
    } and math.isfinite(prior_bear):
        opposite_shock = (
            max(scores[MarketCycle.STRONG_BEAR_BREAKOUT], bear.pressure)
            >= config.cycle.transition_opposite_score
            and bear.pressure - prior_bear >= config.cycle.transition_pressure_delta
        )
    if machine.current in {
        MarketCycle.STRONG_BEAR_BREAKOUT,
        MarketCycle.BEAR_TIGHT_CHANNEL,
        MarketCycle.BEAR_BROAD_CHANNEL,
    } and math.isfinite(prior_bull):
        opposite_shock = (
            max(scores[MarketCycle.STRONG_BULL_BREAKOUT], bull.pressure)
            >= config.cycle.transition_opposite_score
            and bull.pressure - prior_bull >= config.cycle.transition_pressure_delta
        )
    ambiguous = current_directional and top - second < config.cycle.transition_score_margin
    cycle = machine.choose(
        scores,
        config,
        force_transition=climax or opposite_shock or ambiguous,
    )
    range_subtype = _range_subtype(row, config) if cycle is MarketCycle.TRADING_RANGE else None
    cycle_direction = _cycle_direction(cycle)
    evidence: dict[str, float | str] = {
        "reason": "CLASSIFIED",
        "top_score": top,
        "second_score": second,
        "directional_strength": directional_strength,
        "bull_pressure": bull.pressure,
        "bear_pressure": bear.pressure,
    }
    evidence.update({f"score_{state.value}": value for state, value in scores.items()})
    return CycleSnapshot(
        cycle=cycle,
        direction=cycle_direction if cycle_direction != 0 else direction if cycle is MarketCycle.TRANSITION else 0,
        strength=directional_strength,
        confidence=confidence,
        bull_pressure=bull.pressure,
        bear_pressure=bear.pressure,
        range_subtype=range_subtype,
        feature_event=event,
        evidence=evidence,
    )


def directional_view(row: pd.Series, history: pd.DataFrame, d: int) -> DirectionalView:
    if d not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    prefix = "bull" if d == 1 else "bear"
    structure_a = float(row["hh_score" if d == 1 else "ll_score"])
    structure_b = float(row["hl_score" if d == 1 else "lh_score"])
    parts = {
        "ema_slope": _q_pos(d * float(row["ema_slope"]), d * history["ema_slope"]),
        "ema_separation": _q_pos(
            d * float(row["ema_separation"]), d * history["ema_separation"]
        ),
        "ema_distance": _q_pos(
            d * float(row["ema_distance"]), d * history["ema_distance"]
        ),
        "structure_a": structure_a,
        "structure_b": structure_b,
        "trend_bars": float(row[f"{prefix}_trend_bar_ratio"]),
        "close_strength": float(row[f"close_pos_{'long' if d == 1 else 'short'}_mean"]),
        "efficiency": _q_pos(float(row["trend_efficiency"]), history["trend_efficiency"]),
        "low_overlap": 1.0 - float(row["overlap_ratio"]),
    }
    pressure = _required_mean(*parts.values())
    return DirectionalView(
        d=d,
        pressure=pressure,
        breakout_distance=float(row[f"breakout_distance_{'long' if d == 1 else 'short'}"]),
        structure_score=_required_mean(structure_a, structure_b),
        close_pos=float(row[f"close_pos_{'long' if d == 1 else 'short'}"]),
        close_strength=parts["close_strength"],
        trend_bar_ratio=parts["trend_bars"],
        trend_bar_count_recent=int(row[f"{prefix}_trend_bar_count_recent"]),
        channel_age=int(row[f"{prefix}_channel_age"]),
        channel_slope_strength=d * float(row[f"{prefix}_channel_slope_atr"]),
        median_pullback_bars=float(row[f"{prefix}_median_pullback_bars"]),
        median_pullback_depth=float(row[f"{prefix}_median_pullback_depth"]),
        max_pullback_depth=float(row[f"{prefix}_max_pullback_depth"]),
        ema_cross_count=int(row[f"{prefix}_ema_cross_count"]),
        opposite_trend_bar_ratio=float(row[f"{prefix}_opposite_trend_bar_ratio"]),
        parts=parts,
    )


def gate_breakout(row: pd.Series, view: DirectionalView, config: BrooksCycleConfig) -> bool:
    broke = (
        float(row["close"]) > float(row["prior_high"])
        if view.d == 1
        else float(row["close"]) < float(row["prior_low"])
    )
    return bool(
        broke
        and view.breakout_distance >= config.cycle.bo_min_distance_atr
        and view.d * float(row["body"]) > 0
        and float(row["body_ratio"]) >= config.cycle.bo_min_body_ratio
        and view.close_pos >= config.cycle.bo_min_close_pos
        and view.trend_bar_count_recent >= config.cycle.bo_min_trend_bars
        and float(row["overlap_ratio_recent"]) <= config.cycle.bo_max_overlap
        and view.pressure >= config.cycle.bo_min_pressure
    )


def build_trend_snapshot(
    cycle: CycleSnapshot,
    features: Mapping[str, object],
) -> TrendSnapshot:
    """Expose cycle direction and confirmed major structure without new inference."""
    support = _first_finite(
        features.get("latest_confirmed_swing_low"),
        features.get("range_low"),
    )
    resistance = _first_finite(
        features.get("latest_confirmed_swing_high"),
        features.get("range_high"),
    )
    return TrendSnapshot(
        direction=cycle.direction,
        strength=cycle.strength,
        confidence=cycle.confidence,
        cycle=cycle.cycle,
        major_support=support,
        major_resistance=resistance,
        feature_event=cycle.feature_event,
    )


def _score_breakout(
    row: pd.Series,
    history: pd.DataFrame,
    view: DirectionalView,
    config: BrooksCycleConfig,
) -> float:
    distance_column = "breakout_distance_long" if view.d == 1 else "breakout_distance_short"
    raw = _required_mean(
        _q_pos(view.breakout_distance, history[distance_column]),
        _q_pos(float(row["body_ratio"]), history["body_ratio"]),
        view.close_strength,
        view.trend_bar_ratio,
        1.0 - float(row["overlap_ratio_recent"]),
        view.pressure,
    )
    return raw if gate_breakout(row, view, config) else 0.0


def _score_tight(
    row: pd.Series,
    history: pd.DataFrame,
    view: DirectionalView,
    direction: int,
    config: BrooksCycleConfig,
) -> float:
    prefix = "bull" if view.d == 1 else "bear"
    continuation = float(row["hl_score" if view.d == 1 else "lh_score"])
    eligible = (
        direction == view.d
        and view.channel_age >= config.cycle.channel_min_bars
        and view.channel_slope_strength >= config.cycle.tight_min_slope
        and view.median_pullback_bars <= config.cycle.tight_max_median_pb_bars
        and view.max_pullback_depth <= config.cycle.tight_max_pb_depth
        and view.ema_cross_count <= config.cycle.tight_max_ema_crosses
        and view.opposite_trend_bar_ratio <= config.cycle.tight_max_opposite_ratio
        and continuation >= config.cycle.tight_min_structure_score
    )
    raw = _required_mean(
        view.pressure,
        _q_pos(
            view.channel_slope_strength,
            view.d * history[f"{prefix}_channel_slope_atr"],
        ),
        _q_neg(view.median_pullback_bars, history[f"{prefix}_median_pullback_bars"]),
        _q_neg(view.max_pullback_depth, history[f"{prefix}_max_pullback_depth"]),
        _q_neg(view.ema_cross_count, history[f"{prefix}_ema_cross_count"]),
        _q_neg(
            view.opposite_trend_bar_ratio,
            history[f"{prefix}_opposite_trend_bar_ratio"],
        ),
        view.structure_score,
    )
    return raw if eligible else 0.0


def _score_broad(
    row: pd.Series,
    history: pd.DataFrame,
    view: DirectionalView,
    direction: int,
    config: BrooksCycleConfig,
) -> float:
    prefix = "bull" if view.d == 1 else "bear"
    continuation = float(row["hl_score" if view.d == 1 else "lh_score"])
    eligible = (
        direction == view.d
        and view.channel_slope_strength > config.cycle.broad_min_slope
        and continuation >= config.cycle.broad_min_structure_score
        and (
            view.median_pullback_bars > config.cycle.tight_max_median_pb_bars
            or view.median_pullback_depth > config.cycle.tight_max_pb_depth
            or view.ema_cross_count > config.cycle.tight_max_ema_crosses
        )
        and float(row["trend_efficiency"]) >= config.cycle.broad_min_efficiency
    )
    broadness = _required_mean(
        _q_pos(view.median_pullback_bars, history[f"{prefix}_median_pullback_bars"]),
        _q_pos(view.median_pullback_depth, history[f"{prefix}_median_pullback_depth"]),
        _q_pos(view.ema_cross_count, history[f"{prefix}_ema_cross_count"]),
    )
    raw = _required_mean(
        view.pressure,
        _q_pos(
            view.channel_slope_strength,
            view.d * history[f"{prefix}_channel_slope_atr"],
        ),
        view.structure_score,
        broadness,
        _q_pos(float(row["trend_efficiency"]), history["trend_efficiency"]),
    )
    return raw if eligible else 0.0


def _score_range(
    row: pd.Series,
    history: pd.DataFrame,
    directional_strength: float,
    historical_directional_strength: pd.Series,
    config: BrooksCycleConfig,
) -> float:
    eligible = (
        abs(float(row["ema_slope"])) <= config.cycle.range_max_abs_slope
        and float(row["overlap_ratio"]) >= config.cycle.range_min_overlap
        and float(row["direction_change_ratio"]) >= config.cycle.range_min_direction_changes
        and float(row["trend_efficiency"]) <= config.cycle.range_max_efficiency
        and directional_strength <= config.cycle.range_max_directional_strength
        and (not config.cycle.use_range_aux_gate or bool(row["range_aux_gate"]))
    )
    raw = _required_mean(
        _q_neg(abs(float(row["ema_slope"])), history["ema_slope"].abs()),
        float(row["overlap_ratio"]),
        float(row["direction_change_ratio"]),
        _q_neg(float(row["trend_efficiency"]), history["trend_efficiency"]),
        _q_neg(directional_strength, historical_directional_strength),
    )
    return raw if eligible else 0.0


def _range_subtype(row: pd.Series, config: BrooksCycleConfig) -> RangeSubtype:
    if float(row["range_width_cost_multiple"]) < config.cycle.range_min_width_cost_multiple:
        return RangeSubtype.UNTRADEABLE_TIGHT
    if float(row["range_width_atr"]) <= config.cycle.range_tight_max_width_atr:
        return RangeSubtype.TIGHT_BREAKOUT_MODE
    return RangeSubtype.BROAD


def _q_pos(value: float, history: pd.Series) -> float:
    numeric = pd.to_numeric(history, errors="coerce")
    if not _finite(value) or numeric.isna().any() or numeric.empty:
        raise ValueError("non-finite causal rank input")
    return float((numeric <= value).sum()) / len(numeric)


def _q_neg(value: float, history: pd.Series) -> float:
    return _q_pos(-float(value), -pd.to_numeric(history, errors="coerce"))


def _required_mean(*values: float) -> float:
    if not all(_finite(value) for value in values):
        raise ValueError("non-finite required state input")
    return float(sum(values) / len(values))


def _finite(value: object) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _first_finite(*values: object) -> float | None:
    for value in values:
        if _finite(value):
            return float(value)
    return None


def _cycle_direction(cycle: MarketCycle) -> int:
    if cycle in {
        MarketCycle.STRONG_BULL_BREAKOUT,
        MarketCycle.BULL_TIGHT_CHANNEL,
        MarketCycle.BULL_BROAD_CHANNEL,
    }:
        return 1
    if cycle in {
        MarketCycle.STRONG_BEAR_BREAKOUT,
        MarketCycle.BEAR_TIGHT_CHANNEL,
        MarketCycle.BEAR_BROAD_CHANNEL,
    }:
        return -1
    return 0


def _snapshot_row(snapshot: CycleSnapshot) -> dict[str, object]:
    return {
        "cycle": snapshot.cycle.value,
        "direction": snapshot.direction,
        "strength": snapshot.strength,
        "confidence": snapshot.confidence,
        "bull_pressure": snapshot.bull_pressure,
        "bear_pressure": snapshot.bear_pressure,
        "range_subtype": snapshot.range_subtype.value if snapshot.range_subtype else None,
        "feature_asof": snapshot.feature_event.timestamp,
        "feature_sequence": snapshot.feature_event.sequence,
        "reason": str(snapshot.evidence.get("reason", "")),
        "evidence": dict(snapshot.evidence),
    }


__all__ = [
    "DirectionalView", "REQUIRED_STATE_COLUMNS", "build_trend_snapshot",
    "classify_market_cycles", "directional_view", "gate_breakout",
]
