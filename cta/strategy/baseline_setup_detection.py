"""Setup detection helpers for baseline candidate generation."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import (
    BULL_PULLBACK_MIN_QUALITY,
    TREND_ACCELERATION_LONG_MIN_BODY,
    TREND_ACCELERATION_LONG_MIN_SCORE,
    TREND_ACCELERATION_SHORT_MAX_SCORE,
    TREND_ACCELERATION_SHORT_MIN_BODY,
)
from cta.config.skill_tight_range_breakout_config import VALID_SIDE_MODES
from cta.skills.price_action.breakout_pullback import PullbackSetup, pullback_entry_trigger
from cta.skills.price_action.tight_range_breakout import TightRangeSetup, resolve_breakout_trigger
from cta.strategy.baseline_helpers import _safe_bool, _safe_float, _side_allowed


def _infer_regime_label(row: pd.Series) -> str:
    raw = row.get("regime_label", "")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    trend_dir = _safe_float(row.get("trend_dir", np.nan))
    if np.isfinite(trend_dir):
        if trend_dir > 0:
            return "trend_up"
        if trend_dir < 0:
            return "trend_down"

    trend_score = _safe_float(row.get("trend_score", np.nan))
    if np.isfinite(trend_score):
        if trend_score > 0.2:
            return "trend_up"
        if trend_score < -0.2:
            return "trend_down"
    return "range"


def _resolve_candidate_entry(
    order: dict[str, Any],
    next_bar: pd.Series,
) -> tuple[bool, float, float]:
    side = str(order.get("side", "")).strip().lower()
    order_type = str(order.get("order_type", "market")).strip().lower()
    next_open = _safe_float(next_bar.get("open", np.nan))
    next_high = _safe_float(next_bar.get("high", np.nan))
    next_low = _safe_float(next_bar.get("low", np.nan))

    if order_type == "market":
        if not np.isfinite(next_open):
            return False, float("nan"), float("nan")
        return True, float(next_open), float("nan")

    trigger = _safe_float(order.get("price", np.nan))
    if not np.isfinite(trigger):
        return False, float("nan"), float("nan")

    if side == "long":
        if not np.isfinite(next_high) or next_high < trigger:
            return False, float("nan"), float(trigger)
        entry_price = max(next_open, trigger) if np.isfinite(next_open) else trigger
        return True, float(entry_price), float(trigger)
    if side == "short":
        if not np.isfinite(next_low) or next_low > trigger:
            return False, float("nan"), float(trigger)
        entry_price = min(next_open, trigger) if np.isfinite(next_open) else trigger
        return True, float(entry_price), float(trigger)
    return False, float("nan"), float("nan")


def _simulate_candidate_execution_path(
    frame: pd.DataFrame,
    *,
    entry_i: int,
    horizon_i: int,
    side: str,
    entry_price: float,
    atr_v: float,
    stop_loss_pct: float,
) -> dict[str, Any]:
    """Simulate candidate execution with stop-aware path (no lookahead order ambiguity)."""
    seg = frame.iloc[entry_i : horizon_i + 1]
    if seg.empty or not np.isfinite(entry_price):
        return {
            "exit_i": horizon_i,
            "exit_price": float("nan"),
            "mfe_atr": float("nan"),
            "mae_atr": float("nan"),
            "pnl_atr": float("nan"),
            "stop_hit": False,
        }

    side_l = str(side).strip().lower()
    stop_pct = max(0.0, float(stop_loss_pct))
    if side_l == "short":
        stop_price = entry_price * (1.0 + stop_pct)
    else:
        stop_price = entry_price * (1.0 - stop_pct)

    actual_exit_i = int(horizon_i)
    exit_price = _safe_float(seg.iloc[-1].get("close", np.nan))
    stop_hit = False

    for j in range(entry_i, horizon_i + 1):
        row = frame.iloc[j]
        bar_open = _safe_float(row.get("open", np.nan))
        bar_high = _safe_float(row.get("high", np.nan))
        bar_low = _safe_float(row.get("low", np.nan))
        if side_l == "short":
            if np.isfinite(bar_high) and bar_high >= stop_price:
                stop_hit = True
                actual_exit_i = j
                exit_price = max(stop_price, bar_open) if np.isfinite(bar_open) else stop_price
                break
        else:
            if np.isfinite(bar_low) and bar_low <= stop_price:
                stop_hit = True
                actual_exit_i = j
                exit_price = min(stop_price, bar_open) if np.isfinite(bar_open) else stop_price
                break

    exec_seg = frame.iloc[entry_i : actual_exit_i + 1]
    seg_high = float(exec_seg["high"].astype(float).max())
    seg_low = float(exec_seg["low"].astype(float).min())
    if side_l == "short":
        mfe = entry_price - seg_low
        mae = seg_high - entry_price
        pnl = entry_price - exit_price if np.isfinite(exit_price) else np.nan
    else:
        mfe = seg_high - entry_price
        mae = entry_price - seg_low
        pnl = exit_price - entry_price if np.isfinite(exit_price) else np.nan

    if np.isfinite(atr_v) and atr_v > 0:
        mfe_atr = float(mfe / atr_v)
        mae_atr = float(mae / atr_v)
        pnl_atr = float(pnl / atr_v) if np.isfinite(pnl) else float("nan")
    else:
        mfe_atr = float("nan")
        mae_atr = float("nan")
        pnl_atr = float("nan")
    return {
        "exit_i": int(actual_exit_i),
        "exit_price": float(exit_price) if np.isfinite(exit_price) else float("nan"),
        "mfe_atr": mfe_atr,
        "mae_atr": mae_atr,
        "pnl_atr": pnl_atr,
        "stop_hit": bool(stop_hit),
    }


def _build_raw_setup_candidates(
    frame: pd.DataFrame,
    i: int,
    signal_type: str,
    contract: Any,
    mode: str,
) -> list[dict[str, Any]]:
    mode_l = str(mode).strip().lower()
    if mode_l not in VALID_SIDE_MODES:
        mode_l = "both"

    bar = frame.iloc[i]
    tick = float(abs(contract.tick_size))
    out: list[dict[str, Any]] = []

    if signal_type == "donchian_breakout":
        close = _safe_float(bar.get("close"))
        ue = _safe_float(bar.get("don_upper_entry"))
        le = _safe_float(bar.get("don_lower_entry"))
        if np.isfinite(ue) and close > ue:
            out.append(
                {
                    "side": "long",
                    "order_type": "stop",
                    "trigger": _safe_float(bar.get("high")) + tick,
                    "filtered_reason": None if _side_allowed(mode_l, "long") else "side_mode",
                }
            )
        if np.isfinite(le) and close < le:
            out.append(
                {
                    "side": "short",
                    "order_type": "stop",
                    "trigger": _safe_float(bar.get("low")) - tick,
                    "filtered_reason": None if _side_allowed(mode_l, "short") else "side_mode",
                }
            )
        return out

    if signal_type == "atr_breakout":
        if i <= 0:
            return out
        prev = frame.iloc[i - 1]
        close = _safe_float(bar.get("close"))
        up = _safe_float(bar.get("atr_upper"))
        lo = _safe_float(bar.get("atr_lower"))
        pup = _safe_float(prev.get("atr_upper"))
        plo = _safe_float(prev.get("atr_lower"))
        pclose = _safe_float(prev.get("close"))
        if np.isfinite(up) and np.isfinite(pup) and close > up and pclose <= pup:
            out.append(
                {
                    "side": "long",
                    "order_type": "market",
                    "trigger": np.nan,
                    "filtered_reason": None if _side_allowed(mode_l, "long") else "side_mode",
                }
            )
        if np.isfinite(lo) and np.isfinite(plo) and close < lo and pclose >= plo:
            out.append(
                {
                    "side": "short",
                    "order_type": "market",
                    "trigger": np.nan,
                    "filtered_reason": None if _side_allowed(mode_l, "short") else "side_mode",
                }
            )
        return out

    if signal_type == "tight_range_breakout":
        valid = _safe_bool(bar.get("tr_valid", False))
        if not valid:
            return out
        close = _safe_float(bar.get("close"))
        upper = _safe_float(bar.get("tr_upper"))
        lower = _safe_float(bar.get("tr_lower"))
        if np.isfinite(upper) and close >= upper:
            side = "long"
        elif np.isfinite(lower) and close <= lower:
            side = "short"
        else:
            side = "long" if int(_safe_float(bar.get("tr_direction_bias", 0.0))) >= 0 else "short"
        setup = TightRangeSetup(
            valid=valid,
            upper=upper if np.isfinite(upper) else close,
            lower=lower if np.isfinite(lower) else close,
            range_atr=_safe_float(bar.get("tr_range_atr", np.nan)),
            count=int(_safe_float(bar.get("tr_count", 0.0))),
            direction_bias=1 if side == "long" else -1,
        )
        trig = resolve_breakout_trigger(setup, bar, tick_size=tick)
        if trig is None:
            out.append(
                {
                    "side": side,
                    "order_type": "stop",
                    "trigger": np.nan,
                    "filtered_reason": "invalid_trigger",
                }
            )
            return out
        reasons: list[str] = []
        if not _safe_bool(bar.get("breakout_pass", False)):
            reasons.append("quality_gate")
        if not _side_allowed(mode_l, str(trig["side"]).lower()):
            reasons.append("side_mode")
        out.append(
            {
                "side": str(trig["side"]).lower(),
                "order_type": "stop",
                "trigger": _safe_float(trig.get("trigger", np.nan)),
                "filtered_reason": ",".join(reasons) if reasons else None,
            }
        )
        return out

    if signal_type == "breakout_pullback_continuation":
        if not _safe_bool(bar.get("bp_valid", False)):
            return out
        side = str(bar.get("bp_direction", "")).strip().lower()
        if side not in {"long", "short"}:
            return out
        setup = PullbackSetup(
            valid=_safe_bool(bar.get("bp_valid", False)),
            direction=side,  # type: ignore[arg-type]
            breakout_level=_safe_float(bar.get("bp_breakout_level", np.nan)),
            pullback_low=_safe_float(bar.get("bp_pullback_low", np.nan)),
            bars_since_breakout=int(_safe_float(bar.get("bp_bars_since_breakout", 0.0))),
            confirmed=_safe_bool(bar.get("bp_confirmed", False)),
        )
        trig = pullback_entry_trigger(setup, bar, tick_size=tick)
        reasons: list[str] = []
        trigger = np.nan
        if not _safe_bool(bar.get("bp_confirmed", False)):
            reasons.append("not_confirmed")
        elif trig is None:
            reasons.append("invalid_trigger")
        else:
            side = str(trig.get("side", side)).strip().lower()
            trigger = _safe_float(trig.get("trigger", np.nan))
        if not _side_allowed(mode_l, side):
            reasons.append("side_mode")
        out.append(
            {
                "side": side,
                "order_type": "stop",
                "trigger": trigger,
                "filtered_reason": ",".join(reasons) if reasons else None,
            }
        )
        return out

    if signal_type == "trend_acceleration_breakout":
        close = _safe_float(bar.get("close", np.nan))
        high = _safe_float(bar.get("high", np.nan))
        low = _safe_float(bar.get("low", np.nan))
        don_up = _safe_float(bar.get("don_upper_entry", np.nan))
        don_lo = _safe_float(bar.get("don_lower_entry", np.nan))
        accel = _safe_float(bar.get("trend_acceleration_score", np.nan))
        body = _safe_float(bar.get("breakout_body_strength", np.nan))
        trend_dir = _safe_float(bar.get("trend_dir", np.nan))
        trend_score = _safe_float(bar.get("trend_score", np.nan))
        if (
            np.isfinite(don_up)
            and close > don_up
            and trend_dir > 0
            and trend_score > 0
            and accel >= float(TREND_ACCELERATION_LONG_MIN_SCORE)
            and body >= float(TREND_ACCELERATION_LONG_MIN_BODY)
        ):
            out.append(
                {
                    "side": "long",
                    "order_type": "stop",
                    "trigger": high + tick if np.isfinite(high) else np.nan,
                    "filtered_reason": None if _side_allowed(mode_l, "long") else "side_mode",
                }
            )
        elif (
            np.isfinite(don_lo)
            and close < don_lo
            and trend_dir < 0
            and accel <= float(TREND_ACCELERATION_SHORT_MAX_SCORE)
            and body >= float(TREND_ACCELERATION_SHORT_MIN_BODY)
        ):
            # 空头保守触发：默认阈值更高，避免牛市增强策略误做反向单。
            out.append(
                {
                    "side": "short",
                    "order_type": "stop",
                    "trigger": low - tick if np.isfinite(low) else np.nan,
                    "filtered_reason": None if _side_allowed(mode_l, "short") else "side_mode",
                }
            )
        return out

    if signal_type == "bull_pullback_continuation":
        if not _safe_bool(bar.get("bp_valid", False)):
            return out
        side = str(bar.get("bp_direction", "long")).strip().lower()
        if side != "long":
            return out
        if not _safe_bool(bar.get("bp_confirmed", False)):
            return out
        if _safe_float(bar.get("pullback_quality", 0.0)) < float(BULL_PULLBACK_MIN_QUALITY):
            return out
        level = _safe_float(bar.get("bp_breakout_level", np.nan))
        trigger = level + tick if np.isfinite(level) else _safe_float(bar.get("high", np.nan)) + tick
        out.append(
            {
                "side": "long",
                "order_type": "stop",
                "trigger": trigger,
                "filtered_reason": None if _side_allowed(mode_l, "long") else "side_mode",
            }
        )
        return out

    if signal_type == "bull_volatility_contraction_breakout":
        close = _safe_float(bar.get("close", np.nan))
        tr_valid = _safe_bool(bar.get("tr_valid", False))
        tr_upper = _safe_float(bar.get("tr_upper", np.nan))
        contraction_pctl = _safe_float(bar.get("volatility_contraction_pctl", np.nan))
        trend_dir = _safe_float(bar.get("trend_dir", np.nan))
        if tr_valid and np.isfinite(tr_upper) and close >= tr_upper and trend_dir > 0 and contraction_pctl <= 0.35:
            out.append(
                {
                    "side": "long",
                    "order_type": "stop",
                    "trigger": _safe_float(bar.get("high", np.nan)) + tick,
                    "filtered_reason": None if _side_allowed(mode_l, "long") else "side_mode",
                }
            )
        return out

    return out


__all__ = [
    "_infer_regime_label",
    "_resolve_candidate_entry",
    "_simulate_candidate_execution_path",
    "_build_raw_setup_candidates",
]
