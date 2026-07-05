"""Pyramid position manager for layered entries."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from cta.portfolio_logic.config import PyramidConfig, TrailingExitConfig, normalize_portfolio_interval


@dataclass
class Layer:
    """One add-on layer in a pyramid position."""

    layer_id: int
    interval: str
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    notional: float
    atr_pct_at_entry: float
    signal_score: float
    trade_filter_prob: float
    trade_filter_prob_pctl: float
    hard_stop_price: float
    trail_stop_price: float
    trailing_activated: bool = False
    exited: bool = False

    @property
    def effective_stop(self) -> float:
        """Risk floor/ceiling stop after combining hard + trailing stops."""
        hard = float(self.hard_stop_price)
        trail = float(self.trail_stop_price)
        hard_ok = math.isfinite(hard)
        trail_ok = math.isfinite(trail)
        if not hard_ok and not trail_ok:
            return float("nan")
        if not hard_ok:
            return trail
        if not trail_ok:
            return hard
        if str(self.direction).lower() == "short":
            return float(min(hard, trail))
        return float(max(hard, trail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_id": int(self.layer_id),
            "interval": str(self.interval),
            "direction": str(self.direction),
            "entry_time": pd.Timestamp(self.entry_time).isoformat(),
            "entry_price": float(self.entry_price),
            "notional": float(self.notional),
            "atr_pct_at_entry": float(self.atr_pct_at_entry),
            "signal_score": float(self.signal_score),
            "trade_filter_prob": float(self.trade_filter_prob),
            "trade_filter_prob_pctl": float(self.trade_filter_prob_pctl),
            "hard_stop_price": float(self.hard_stop_price),
            "trail_stop_price": float(self.trail_stop_price),
            "trailing_activated": bool(self.trailing_activated),
            "exited": bool(self.exited),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Layer":
        return cls(
            layer_id=int(payload.get("layer_id", 0)),
            interval=str(payload.get("interval", "")),
            direction=str(payload.get("direction", "long")).lower(),
            entry_time=pd.Timestamp(payload.get("entry_time")),
            entry_price=float(payload.get("entry_price", 0.0) or 0.0),
            notional=float(payload.get("notional", 0.0) or 0.0),
            atr_pct_at_entry=float(payload.get("atr_pct_at_entry", 0.0) or 0.0),
            signal_score=float(payload.get("signal_score", 0.0) or 0.0),
            trade_filter_prob=float(payload.get("trade_filter_prob", 0.0) or 0.0),
            trade_filter_prob_pctl=float(payload.get("trade_filter_prob_pctl", 0.0) or 0.0),
            hard_stop_price=float(payload.get("hard_stop_price", 0.0) or 0.0),
            trail_stop_price=float(payload.get("trail_stop_price", 0.0) or 0.0),
            trailing_activated=bool(payload.get("trailing_activated", False)),
            exited=bool(payload.get("exited", False)),
        )


@dataclass
class PyramidPosition:
    """Position with multiple layers."""

    pos_id: str
    symbol: str
    exchange: str
    direction: str
    layers: list[Layer] = field(default_factory=list)
    running_high: float = float("-inf")
    running_low: float = float("inf")

    @property
    def active_layers(self) -> list[Layer]:
        return [x for x in self.layers if not x.exited]

    @property
    def total_active_notional(self) -> float:
        return float(sum(x.notional for x in self.active_layers))

    def update_running_extremes(self, high: float | None, low: float | None) -> None:
        if high is not None:
            self.running_high = max(float(self.running_high), float(high))
        if low is not None:
            self.running_low = min(float(self.running_low), float(low))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pos_id": str(self.pos_id),
            "symbol": str(self.symbol),
            "exchange": str(self.exchange),
            "direction": str(self.direction),
            "layers": [layer.to_dict() for layer in self.layers],
            "running_high": float(self.running_high),
            "running_low": float(self.running_low),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PyramidPosition":
        layers = [Layer.from_dict(x) for x in list(payload.get("layers", []))]
        return cls(
            pos_id=str(payload.get("pos_id", "")),
            symbol=str(payload.get("symbol", "")),
            exchange=str(payload.get("exchange", "")),
            direction=str(payload.get("direction", "long")).lower(),
            layers=layers,
            running_high=float(payload.get("running_high", float("-inf"))),
            running_low=float(payload.get("running_low", float("inf"))),
        )


class PyramidManager:
    """Manage open/add decisions and layer metadata."""

    def __init__(self, cfg: PyramidConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _init_layer_stop(
        *,
        direction: str,
        entry_price: float,
        interval: str,
        trailing_cfg: TrailingExitConfig,
    ) -> float:
        interval_key = normalize_portfolio_interval(interval)
        params = trailing_cfg.interval_params.get(interval_key)
        if params is None:
            params = trailing_cfg.interval_params.get(trailing_cfg.default_interval_params_key)
        if params is None:
            params = next(iter(trailing_cfg.interval_params.values()))
        if str(direction).lower() == "short":
            return float(entry_price) * (1.0 + float(params.fallback_hard_stop_pct))
        return float(entry_price) * (1.0 - float(params.fallback_hard_stop_pct))

    def _initial_trail_stop(self, direction: str) -> float:
        if str(direction).lower() == "short":
            return float("inf")
        return float("-inf")

    def _cooldown_minutes_required(self, interval: str) -> float:
        interval_key = normalize_portfolio_interval(interval)
        bars = int(self.cfg.cooldown_bars_per_interval.get(interval_key, 0))
        if bars <= 0:
            return 0.0
        step = int(self.cfg.interval_to_minutes.get(interval_key, max(1, int(self.cfg.cooldown_minutes))))
        return float(bars * step)

    def open_first_layer(
        self,
        *,
        symbol: str,
        exchange: str,
        direction: str,
        interval: str,
        entry_time: pd.Timestamp,
        entry_price: float,
        notional: float,
        atr_pct_at_entry: float,
        signal_score: float,
        trade_filter_prob: float,
        trade_filter_prob_pctl: float,
        trailing_cfg: TrailingExitConfig,
    ) -> PyramidPosition:
        pos_id = f"{symbol}_{exchange}_{direction}_{pd.Timestamp(entry_time).isoformat()}"
        direction_norm = str(direction).lower()
        first_layer = Layer(
            layer_id=0,
            interval=str(interval),
            direction=direction_norm,
            entry_time=pd.Timestamp(entry_time),
            entry_price=float(entry_price),
            notional=float(notional),
            atr_pct_at_entry=float(atr_pct_at_entry) if atr_pct_at_entry is not None else float("nan"),
            signal_score=float(signal_score),
            trade_filter_prob=float(trade_filter_prob),
            trade_filter_prob_pctl=float(trade_filter_prob_pctl),
            hard_stop_price=self._init_layer_stop(
                direction=direction_norm,
                entry_price=float(entry_price),
                interval=str(interval),
                trailing_cfg=trailing_cfg,
            ),
            trail_stop_price=self._initial_trail_stop(direction_norm),
        )
        pos = PyramidPosition(
            pos_id=pos_id,
            symbol=str(symbol).upper(),
            exchange=str(exchange).upper(),
            direction=direction_norm,
            layers=[first_layer],
        )
        pos.update_running_extremes(float(entry_price), float(entry_price))
        return pos

    def decide_add_layer(
        self,
        *,
        pos: PyramidPosition,
        new_interval: str,
        new_signal_type: str | None = None,
        current_time: pd.Timestamp,
        current_price: float,
        min_profit_atr_to_add: float,
        htf_aligned: bool,
    ) -> bool:
        if not self.cfg.enabled:
            return False
        if new_signal_type is not None and self.cfg.allowed_signal_type_interval:
            key = f"{str(new_signal_type).strip().lower()}|{normalize_portfolio_interval(new_interval)}"
            if key not in set(self.cfg.allowed_signal_type_interval):
                return False
        if not htf_aligned and self.cfg.require_htf_still_aligned:
            return False
        active_layers = pos.active_layers
        if len(active_layers) >= int(self.cfg.max_active_layers):
            return False
        if len(pos.layers) >= int(self.cfg.max_lifetime_layers):
            return False
        if not active_layers:
            return False
        last_layer = active_layers[-1]
        elapsed_min = (pd.Timestamp(current_time) - pd.Timestamp(last_layer.entry_time)).total_seconds() / 60.0
        min_elapsed = self._cooldown_minutes_required(str(new_interval))
        if min_elapsed <= 0 and int(self.cfg.cooldown_minutes) > 0:
            min_elapsed = float(self.cfg.cooldown_minutes)
        if elapsed_min < float(min_elapsed):
            return False
        if self.cfg.one_layer_per_interval:
            active_intervals = {normalize_portfolio_interval(x.interval) for x in active_layers}
            if normalize_portfolio_interval(str(new_interval)) in active_intervals:
                return False
        base_layer = active_layers[0]
        atr_abs = float(base_layer.atr_pct_at_entry) * float(base_layer.entry_price)
        if atr_abs > 0:
            if pos.direction == "short":
                profit_atr = (float(base_layer.entry_price) - float(current_price)) / atr_abs
            else:
                profit_atr = (float(current_price) - float(base_layer.entry_price)) / atr_abs
            if float(profit_atr) < float(min_profit_atr_to_add):
                return False
        return True

    def add_layer(
        self,
        *,
        pos: PyramidPosition,
        interval: str,
        entry_time: pd.Timestamp,
        entry_price: float,
        notional: float,
        atr_pct_at_entry: float,
        signal_score: float,
        trade_filter_prob: float,
        trade_filter_prob_pctl: float,
        trailing_cfg: TrailingExitConfig,
        size_multiplier: float = 1.0,
    ) -> Layer:
        layer_id = int(len(pos.layers))
        scaled_notional = float(notional) * max(0.0, float(size_multiplier))
        layer = Layer(
            layer_id=layer_id,
            interval=str(interval),
            direction=str(pos.direction).lower(),
            entry_time=pd.Timestamp(entry_time),
            entry_price=float(entry_price),
            notional=float(scaled_notional),
            atr_pct_at_entry=float(atr_pct_at_entry) if atr_pct_at_entry is not None else float("nan"),
            signal_score=float(signal_score),
            trade_filter_prob=float(trade_filter_prob),
            trade_filter_prob_pctl=float(trade_filter_prob_pctl),
            hard_stop_price=self._init_layer_stop(
                direction=pos.direction,
                entry_price=float(entry_price),
                interval=str(interval),
                trailing_cfg=trailing_cfg,
            ),
            trail_stop_price=self._initial_trail_stop(pos.direction),
        )
        pos.layers.append(layer)
        pos.update_running_extremes(float(entry_price), float(entry_price))
        return layer

    def force_close_all(
        self,
        *,
        pos: PyramidPosition,
        exit_price: float,
        exit_time: pd.Timestamp,
        reason: str = "force_close",
    ) -> list[dict[str, Any]]:
        """Close all active layers immediately and return exit events."""
        events: list[dict[str, Any]] = []
        for layer in pos.active_layers:
            layer.exited = True
            events.append(
                {
                    "pos_id": pos.pos_id,
                    "symbol": pos.symbol,
                    "exchange": pos.exchange,
                    "direction": pos.direction,
                    "layer_id": int(layer.layer_id),
                    "exit_datetime": pd.Timestamp(exit_time),
                    "exit_price": float(exit_price),
                    "notional": float(layer.notional),
                    "reason": str(reason),
                }
            )
        return events


__all__ = ["Layer", "PyramidPosition", "PyramidManager"]
