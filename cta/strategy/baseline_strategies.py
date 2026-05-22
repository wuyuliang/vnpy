"""Baseline strategy classes and factory."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import BASELINE_SIGNAL_TYPES
from cta.config.mean_reversion_setup_config import MeanReversionSetupConfig
from cta.config.skill_tight_range_breakout_config import StrategyConfig, VALID_SIDE_MODES
from cta.skills.price_action.breakout_pullback import PullbackSetup, pullback_entry_trigger
from cta.skills.price_action.tight_range_breakout import TightRangeSetup, resolve_breakout_trigger
from cta.strategy.baseline_helpers import _entry_order, _safe_bool, _safe_float, _side_allowed
from cta.strategy.skill_tight_range_breakout import ContractSpec, SkillTightRangeBreakoutStrategy
from cta.strategy.mean_reversion_range_setup import MeanReversionRangeSetupGenerator


class DonchianBaselineStrategy:
    """Donchian breakout baseline."""

    def __init__(self, frame: pd.DataFrame, contract: ContractSpec, trade_side_mode: str = "both") -> None:
        self.frame = frame.reset_index(drop=True)
        self.contract = contract
        self.trade_side_mode = trade_side_mode

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        if i <= 0:
            return []
        close = _safe_float(bar.get("close"))
        ue = _safe_float(bar.get("don_upper_entry"))
        le = _safe_float(bar.get("don_lower_entry"))
        ux = _safe_float(bar.get("don_upper_exit"))
        lx = _safe_float(bar.get("don_lower_exit"))
        tick = float(abs(self.contract.tick_size))

        if position == 0:
            if np.isfinite(ue) and close > ue and _side_allowed(self.trade_side_mode, "long"):
                return [_entry_order(self.contract, "long", lots=1, order_type="stop", price=float(bar["high"]) + tick)]
            if np.isfinite(le) and close < le and _side_allowed(self.trade_side_mode, "short"):
                return [_entry_order(self.contract, "short", lots=1, order_type="stop", price=float(bar["low"]) - tick)]
            return []

        if position > 0 and np.isfinite(lx) and close < lx:
            return [_entry_order(self.contract, "flat", lots=abs(position), order_type="market")]
        if position < 0 and np.isfinite(ux) and close > ux:
            return [_entry_order(self.contract, "flat", lots=abs(position), order_type="market")]
        return []


class ATRBreakoutBaselineStrategy:
    """ATR channel breakout baseline."""

    def __init__(self, frame: pd.DataFrame, contract: ContractSpec, trade_side_mode: str = "both") -> None:
        self.frame = frame.reset_index(drop=True)
        self.contract = contract
        self.trade_side_mode = trade_side_mode

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        if i <= 0:
            return []
        prev = self.frame.iloc[i - 1]
        close = _safe_float(bar.get("close"))
        up = _safe_float(bar.get("atr_upper"))
        lo = _safe_float(bar.get("atr_lower"))
        ma = _safe_float(bar.get("atr_ma"))
        pup = _safe_float(prev.get("atr_upper"))
        plo = _safe_float(prev.get("atr_lower"))
        pclose = _safe_float(prev.get("close"))

        if position == 0:
            if np.isfinite(up) and np.isfinite(pup) and close > up and pclose <= pup:
                if _side_allowed(self.trade_side_mode, "long"):
                    return [_entry_order(self.contract, "long", lots=1, order_type="market")]
            if np.isfinite(lo) and np.isfinite(plo) and close < lo and pclose >= plo:
                if _side_allowed(self.trade_side_mode, "short"):
                    return [_entry_order(self.contract, "short", lots=1, order_type="market")]
            return []

        if position > 0 and np.isfinite(ma) and close < ma:
            return [_entry_order(self.contract, "flat", lots=abs(position), order_type="market")]
        if position < 0 and np.isfinite(ma) and close > ma:
            return [_entry_order(self.contract, "flat", lots=abs(position), order_type="market")]
        return []


class BreakoutPullbackBaselineStrategy:
    """Breakout-pullback continuation baseline with ATR trailing exit."""

    def __init__(
        self,
        frame: pd.DataFrame,
        contract: ContractSpec,
        trade_side_mode: str = "both",
        max_holding_bars: int = 30,
        trailing_stop_atr_mult: float = 2.0,
        initial_stop_atr_mult: float = 1.2,
    ) -> None:
        self.frame = frame.reset_index(drop=True)
        self.contract = contract
        self.trade_side_mode = trade_side_mode
        self.max_holding_bars = int(max_holding_bars)
        self.trailing_stop_atr_mult = float(trailing_stop_atr_mult)
        self.initial_stop_atr_mult = float(initial_stop_atr_mult)

        self._prev_position: int = 0
        self._entry_index: int = -1
        self._high_since_entry: float = float("-inf")
        self._low_since_entry: float = float("inf")
        self._stop_price: float | None = None
        self._pending_initial_stop: float | None = None

    def _sync(self, i: int, bar: pd.Series, position: int) -> None:
        if self._prev_position == 0 and position != 0:
            atr_v = _safe_float(bar.get("atr14"))
            if not np.isfinite(atr_v) or atr_v <= 0:
                atr_v = max(_safe_float(bar.get("high")) - _safe_float(bar.get("low")), self.contract.tick_size)
            self._entry_index = i
            self._high_since_entry = _safe_float(bar.get("high"))
            self._low_since_entry = _safe_float(bar.get("low"))
            if position > 0:
                fallback = _safe_float(bar.get("open")) - self.initial_stop_atr_mult * atr_v
                self._stop_price = (
                    max(float(self._pending_initial_stop), fallback)
                    if self._pending_initial_stop is not None
                    else fallback
                )
            else:
                fallback = _safe_float(bar.get("open")) + self.initial_stop_atr_mult * atr_v
                self._stop_price = (
                    min(float(self._pending_initial_stop), fallback)
                    if self._pending_initial_stop is not None
                    else fallback
                )
            self._pending_initial_stop = None
        if self._prev_position != 0 and position == 0:
            self._entry_index = -1
            self._high_since_entry = float("-inf")
            self._low_since_entry = float("inf")
            self._stop_price = None
            self._pending_initial_stop = None

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        self._sync(i, bar, position)
        tick = float(abs(self.contract.tick_size))

        if position == 0:
            if not _safe_bool(bar.get("bp_valid", False)) or not _safe_bool(bar.get("bp_confirmed", False)):
                self._prev_position = int(position)
                return []
            direction = str(bar.get("bp_direction", "")).lower()
            if direction not in {"long", "short"} or (not _side_allowed(self.trade_side_mode, direction)):
                self._prev_position = int(position)
                return []
            setup = PullbackSetup(
                valid=_safe_bool(bar.get("bp_valid", False)),
                direction=direction,  # type: ignore[arg-type]
                breakout_level=_safe_float(bar.get("bp_breakout_level")),
                pullback_low=_safe_float(bar.get("bp_pullback_low")),
                bars_since_breakout=int(_safe_float(bar.get("bp_bars_since_breakout"))),
                confirmed=_safe_bool(bar.get("bp_confirmed", False)),
            )
            trig = pullback_entry_trigger(setup, bar, tick_size=tick)
            self._prev_position = int(position)
            if trig is None:
                return []
            self._pending_initial_stop = float(trig["stop"])
            return [
                _entry_order(
                    self.contract,
                    side=str(trig["side"]),
                    lots=1,
                    order_type="stop",
                    price=float(trig["trigger"]),
                )
            ]

        atr_v = _safe_float(bar.get("atr14"))
        if (not np.isfinite(atr_v)) or atr_v <= 0:
            atr_v = max(_safe_float(bar.get("high")) - _safe_float(bar.get("low")), self.contract.tick_size)
        bars_held = i - self._entry_index if self._entry_index >= 0 else 0
        high = _safe_float(bar.get("high"))
        low = _safe_float(bar.get("low"))

        if position > 0:
            self._high_since_entry = max(self._high_since_entry, high)
            trail = self._high_since_entry - self.trailing_stop_atr_mult * atr_v
            self._stop_price = trail if self._stop_price is None else max(float(self._stop_price), trail)
            stop_hit = low <= float(self._stop_price)
        else:
            self._low_since_entry = min(self._low_since_entry, low)
            trail = self._low_since_entry + self.trailing_stop_atr_mult * atr_v
            self._stop_price = trail if self._stop_price is None else min(float(self._stop_price), trail)
            stop_hit = high >= float(self._stop_price)
        if stop_hit or bars_held >= self.max_holding_bars:
            self._prev_position = int(position)
            return [_entry_order(self.contract, side="flat", lots=abs(position), order_type="market")]

        self._prev_position = int(position)
        return []


class BullExtensionBaselineStrategy:
    """Bull-market extension baseline signals (long-first, short-conservative)."""

    def __init__(
        self,
        frame: pd.DataFrame,
        contract: ContractSpec,
        trade_side_mode: str = "both",
        signal_kind: str = "trend_acceleration_breakout",
    ) -> None:
        self.frame = frame.reset_index(drop=True)
        self.contract = contract
        self.trade_side_mode = trade_side_mode
        self.signal_kind = str(signal_kind).strip().lower()

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        if position != 0:
            return []
        tick = float(abs(self.contract.tick_size))
        close = _safe_float(bar.get("close", np.nan))
        high = _safe_float(bar.get("high", np.nan))
        low = _safe_float(bar.get("low", np.nan))

        if self.signal_kind == "trend_acceleration_breakout":
            if (
                np.isfinite(_safe_float(bar.get("don_upper_entry", np.nan)))
                and close > _safe_float(bar.get("don_upper_entry", np.nan))
                and _safe_float(bar.get("trend_dir", 0.0)) > 0
                and _safe_float(bar.get("trend_acceleration_score", 0.0)) >= 0.55
                and _safe_float(bar.get("breakout_body_strength", 0.0)) >= 0.6
                and _side_allowed(self.trade_side_mode, "long")
            ):
                return [_entry_order(self.contract, "long", lots=1, order_type="stop", price=high + tick)]
            if (
                np.isfinite(_safe_float(bar.get("don_lower_entry", np.nan)))
                and close < _safe_float(bar.get("don_lower_entry", np.nan))
                and _safe_float(bar.get("trend_dir", 0.0)) < 0
                and _safe_float(bar.get("trend_acceleration_score", 0.0)) <= -0.75
                and _safe_float(bar.get("breakout_body_strength", 0.0)) >= 0.7
                and _side_allowed(self.trade_side_mode, "short")
            ):
                return [_entry_order(self.contract, "short", lots=1, order_type="stop", price=low - tick)]
            return []

        if self.signal_kind == "bull_pullback_continuation":
            if (
                _safe_bool(bar.get("bp_valid", False))
                and _safe_bool(bar.get("bp_confirmed", False))
                and str(bar.get("bp_direction", "")).strip().lower() == "long"
                and _safe_float(bar.get("pullback_quality", 0.0)) >= 0.55
                and _side_allowed(self.trade_side_mode, "long")
            ):
                level = _safe_float(bar.get("bp_breakout_level", np.nan))
                trigger = level + tick if np.isfinite(level) else high + tick
                return [_entry_order(self.contract, "long", lots=1, order_type="stop", price=trigger)]
            return []

        if self.signal_kind == "bull_volatility_contraction_breakout":
            if (
                _safe_bool(bar.get("tr_valid", False))
                and close >= _safe_float(bar.get("tr_upper", np.nan))
                and _safe_float(bar.get("volatility_contraction_pctl", 1.0)) <= 0.35
                and _safe_float(bar.get("trend_dir", 0.0)) > 0
                and _side_allowed(self.trade_side_mode, "long")
            ):
                return [_entry_order(self.contract, "long", lots=1, order_type="stop", price=high + tick)]
            return []

        return []


class MeanReversionRangeBaselineStrategy:
    """Opt-in market-entry strategy wrapper for range mean-reversion setups."""

    def __init__(
        self,
        frame: pd.DataFrame,
        contract: ContractSpec,
        trade_side_mode: str,
        cfg: MeanReversionSetupConfig,
        interval: str,
    ) -> None:
        self.frame = frame.reset_index(drop=True)
        self.contract = contract
        self.trade_side_mode = trade_side_mode
        self.interval = str(interval)
        self.generator = MeanReversionRangeSetupGenerator(cfg)

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        if position != 0:
            return []
        from cta.config.symbol_cluster_config import infer_symbol_cluster

        candidate = self.generator.candidate_from_row(
            bar,
            cluster=infer_symbol_cluster(str(getattr(self.contract, "symbol", ""))),
            interval=self.interval,
        )
        if candidate is None:
            return []
        side = str(candidate["side"])
        if not _side_allowed(self.trade_side_mode, side):
            return []
        return [_entry_order(self.contract, side, lots=1, order_type="market")]


def create_baseline_strategy(
    signal_type: str,
    frame: pd.DataFrame,
    contract: ContractSpec,
    trade_side_mode: str = "both",
    mean_reversion_cfg: MeanReversionSetupConfig | None = None,
    interval: str = "day",
) -> Any:
    """Factory for baseline strategy objects."""
    st = str(signal_type).strip().lower()
    mode = str(trade_side_mode).strip().lower()
    if mode not in VALID_SIDE_MODES:
        raise ValueError(f"invalid trade_side_mode={trade_side_mode}, valid={sorted(VALID_SIDE_MODES)}")

    if st == "donchian_breakout":
        return DonchianBaselineStrategy(frame=frame, contract=contract, trade_side_mode=mode)
    if st == "atr_breakout":
        return ATRBreakoutBaselineStrategy(frame=frame, contract=contract, trade_side_mode=mode)
    if st == "tight_range_breakout":
        cfg = StrategyConfig(
            lookback=10,
            alpha=1.5,
            min_count=5,
            min_breakout_score=0.30,
            lots=1,
            risk_per_trade_pct=0.005,
            initial_stop_atr_mult=1.5,
            trailing_stop_atr_mult=2.2,
            max_holding_bars=20,
            align_trend_direction=False,
            trade_side_mode=mode,
        )
        return SkillTightRangeBreakoutStrategy(frame=frame, cfg=cfg, contract=contract, capital_base=1_000_000.0)
    if st == "breakout_pullback_continuation":
        return BreakoutPullbackBaselineStrategy(frame=frame, contract=contract, trade_side_mode=mode)
    if st in {
        "trend_acceleration_breakout",
        "bull_pullback_continuation",
        "bull_volatility_contraction_breakout",
    }:
        return BullExtensionBaselineStrategy(
            frame=frame,
            contract=contract,
            trade_side_mode=mode,
            signal_kind=st,
        )
    if st == "mean_reversion_range":
        return MeanReversionRangeBaselineStrategy(
            frame=frame,
            contract=contract,
            trade_side_mode=mode,
            cfg=mean_reversion_cfg or MeanReversionSetupConfig(),
            interval=interval,
        )
    raise ValueError(f"unsupported signal_type={signal_type}, valid={BASELINE_SIGNAL_TYPES}")


__all__ = [
    "DonchianBaselineStrategy",
    "ATRBreakoutBaselineStrategy",
    "BreakoutPullbackBaselineStrategy",
    "BullExtensionBaselineStrategy",
    "MeanReversionRangeBaselineStrategy",
    "create_baseline_strategy",
]
