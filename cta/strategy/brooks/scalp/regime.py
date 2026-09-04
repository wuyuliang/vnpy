"""Deterministic 30-minute Brooks market-regime classification."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum

import numpy as np
import pandas as pd


class RegimeState(str, Enum):
    STRONG_TREND_UP = "STRONG_TREND_UP"
    STRONG_TREND_DOWN = "STRONG_TREND_DOWN"
    WIDE_TRADING_RANGE = "WIDE_TRADING_RANGE"
    TIGHT_RANGE_BREAKOUT_MODE = "TIGHT_RANGE_BREAKOUT_MODE"
    CLIMAX_TRANSITION = "CLIMAX_TRANSITION"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class RegimeSnapshot:
    state: RegimeState
    direction: int
    feature_asof: datetime
    range_high: float
    range_low: float
    range_mid: float
    source_contract: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["state"] = self.state.value
        result["feature_asof"] = self.feature_asof.isoformat()
        return result


REQUIRED_COLUMNS = {
    "bar_end",
    "high",
    "low",
    "close",
    "atr_14",
    "ema_20",
    "ema_60",
    "causal_range_over_atr_20",
    "direction_change_ratio_20",
    "pa_always_in_dir",
    "pa_trend_strength_20",
    "pa_ema_slope_20",
    "pa_trend_bar_net",
    "pa_overlap_ratio_10",
    "pa_overlap_ratio_20",
    "pa_buy_climax",
    "pa_sell_climax",
    "pa_momentum_decay",
    "pa_barb_wire",
}


def classify_latest_regime(
    completed_30m: pd.DataFrame,
    *,
    source_contract: str,
    warmup_bars: int = 252,
    prior_state: RegimeState | None = None,
    roll_warmup: bool = False,
) -> RegimeSnapshot:
    """Classify the latest complete bar using frozen prior range boundaries."""
    if not source_contract:
        raise ValueError("source_contract is required")
    missing = sorted(REQUIRED_COLUMNS.difference(completed_30m.columns))
    if missing:
        raise ValueError(f"missing regime columns: {','.join(missing)}")
    if completed_30m.empty:
        raise ValueError("completed_30m is empty")
    current = completed_30m.iloc[-1]
    asof = _aware_datetime(current["bar_end"])
    prior_range = completed_30m.iloc[-21:-1]
    if len(completed_30m) < max(21, warmup_bars) or len(prior_range) < 20:
        return _snapshot(RegimeState.UNAVAILABLE, 0, asof, prior_range, source_contract)
    required_values = [current[column] for column in REQUIRED_COLUMNS if column != "bar_end"]
    if not all(_finite(value) for value in required_values) or float(current["atr_14"]) <= 0:
        return _snapshot(RegimeState.UNAVAILABLE, 0, asof, prior_range, source_contract)

    climax_now = _is_climax(current) or roll_warmup
    if climax_now:
        return _snapshot(RegimeState.CLIMAX_TRANSITION, 0, asof, prior_range, source_contract)
    if prior_state is RegimeState.CLIMAX_TRANSITION:
        recent = completed_30m.iloc[-2:]
        if len(recent) < 2 or any(_is_climax(row) for _, row in recent.iterrows()):
            return _snapshot(RegimeState.CLIMAX_TRANSITION, 0, asof, prior_range, source_contract)

    recent_10 = completed_30m.iloc[-10:]
    recent_height = float(recent_10["high"].max() - recent_10["low"].min())
    atr = float(current["atr_14"])
    tight = (
        int(current["pa_barb_wire"]) == 1
        or (
            float(current["pa_overlap_ratio_10"]) >= 0.75
            and float(current["causal_range_over_atr_20"]) < 4.0
        )
        or recent_height < 2.2 * atr
    )
    if tight:
        return _snapshot(RegimeState.TIGHT_RANGE_BREAKOUT_MODE, 0, asof, prior_range, source_contract)

    if _strong_up(current):
        return _snapshot(RegimeState.STRONG_TREND_UP, 1, asof, prior_range, source_contract)
    if _strong_down(current):
        return _snapshot(RegimeState.STRONG_TREND_DOWN, -1, asof, prior_range, source_contract)
    if _wide_range(current):
        return _snapshot(RegimeState.WIDE_TRADING_RANGE, 0, asof, prior_range, source_contract)
    return _snapshot(RegimeState.UNAVAILABLE, 0, asof, prior_range, source_contract)


def classify_regime(
    completed_30m: pd.DataFrame,
    config: object | None = None,
    *,
    source_contract: str | None = None,
) -> pd.DataFrame:
    """Classify every causally available prefix for audit/reporting."""
    warmup = 252
    if config is not None:
        intervals = getattr(config, "intervals", config)
        warmup = int(getattr(intervals, "warmup_30m", warmup))
    contract = source_contract
    if contract is None and "contract_code" in completed_30m and not completed_30m.empty:
        contract = str(completed_30m.iloc[0]["contract_code"])
    if not contract:
        raise ValueError("source_contract is required")
    rows: list[dict[str, object]] = []
    prior = RegimeState.UNAVAILABLE
    for end in range(1, len(completed_30m) + 1):
        snapshot = classify_latest_regime(
            completed_30m.iloc[:end],
            source_contract=contract,
            warmup_bars=warmup,
            prior_state=prior,
        )
        rows.append(snapshot.to_dict())
        prior = snapshot.state
    return pd.DataFrame(rows, index=completed_30m.index)


def _strong_up(row: pd.Series) -> bool:
    return bool(
        float(row["pa_always_in_dir"]) >= 0.10
        and float(row["pa_trend_strength_20"]) >= 3.0
        and float(row["pa_ema_slope_20"]) > 0
        and float(row["ema_20"]) > float(row["ema_60"])
        and float(row["close"]) > float(row["ema_20"])
        and float(row["pa_trend_bar_net"]) >= 0.15
        and float(row["pa_overlap_ratio_20"]) <= 0.65
        and int(row["pa_buy_climax"]) == 0
    )


def _strong_down(row: pd.Series) -> bool:
    return bool(
        float(row["pa_always_in_dir"]) <= -0.10
        and float(row["pa_trend_strength_20"]) <= -3.0
        and float(row["pa_ema_slope_20"]) < 0
        and float(row["ema_20"]) < float(row["ema_60"])
        and float(row["close"]) < float(row["ema_20"])
        and float(row["pa_trend_bar_net"]) <= -0.15
        and float(row["pa_overlap_ratio_20"]) <= 0.65
        and int(row["pa_sell_climax"]) == 0
    )


def _wide_range(row: pd.Series) -> bool:
    return bool(
        float(row["direction_change_ratio_20"]) >= 0.50
        and float(row["pa_overlap_ratio_20"]) >= 0.55
        and float(row["causal_range_over_atr_20"]) >= 4.0
        and abs(float(row["pa_always_in_dir"])) < 0.10
        and abs(float(row["pa_trend_strength_20"])) <= 15.0
        and int(row["pa_barb_wire"]) == 0
    )


def _is_climax(row: pd.Series) -> bool:
    return bool(
        int(row["pa_buy_climax"]) == 1
        or int(row["pa_sell_climax"]) == 1
        or float(row["pa_momentum_decay"]) >= 1.0
    )


def _snapshot(
    state: RegimeState,
    direction: int,
    asof: datetime,
    prior_range: pd.DataFrame,
    source_contract: str,
) -> RegimeSnapshot:
    if prior_range.empty:
        high = low = mid = np.nan
    else:
        high = float(prior_range["high"].max())
        low = float(prior_range["low"].min())
        mid = (high + low) / 2.0
    return RegimeSnapshot(state, direction, asof, high, low, mid, source_contract)


def _aware_datetime(value: object) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("regime bar_end must be timezone aware")
    return timestamp.to_pydatetime()


def _finite(value: object) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


__all__ = ["RegimeSnapshot", "RegimeState", "classify_latest_regime", "classify_regime"]
