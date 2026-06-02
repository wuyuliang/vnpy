"""P1-7: wire RotationStepper intents into sim/live strategy on_bar loop."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
from typing import Any, Mapping, Protocol

import pandas as pd

from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.sim.adapters.rotation_stepper import (
    RotationOrderIntent,
    intent_to_legacy_order,
)

logger = logging.getLogger(__name__)


class RotationStepperLike(Protocol):
    """Minimal protocol for runtime rotation stepper."""

    def step(self, t: pd.Timestamp, portfolio_state: PortfolioState) -> list[RotationOrderIntent]:
        ...


@dataclass(frozen=True)
class RotationOrderWireConfig:
    """Execution params used to translate rotation intents into strategy orders."""

    default_contract_size: float = 1.0
    default_lot_size: int = 1
    default_order_type: str = "market"
    only_trade_matching_vt_symbol: bool = True
    contract_size_by_symbol: dict[str, float] = field(default_factory=dict)
    lot_size_by_symbol: dict[str, int] = field(default_factory=dict)
    max_lots_by_symbol: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "RotationOrderWireConfig":
        if payload is None:
            return cls()
        data = dict(payload)
        return cls(
            default_contract_size=float(data.get("default_contract_size", 1.0)),
            default_lot_size=int(data.get("default_lot_size", 1)),
            default_order_type=str(data.get("default_order_type", "market")),
            only_trade_matching_vt_symbol=bool(data.get("only_trade_matching_vt_symbol", True)),
            contract_size_by_symbol={
                str(k).upper(): float(v)
                for k, v in dict(data.get("contract_size_by_symbol", {})).items()
            },
            lot_size_by_symbol={
                str(k).upper(): int(v)
                for k, v in dict(data.get("lot_size_by_symbol", {})).items()
            },
            max_lots_by_symbol={
                str(k).upper(): int(v)
                for k, v in dict(data.get("max_lots_by_symbol", {})).items()
            },
        )


def _symbol_from_vt_symbol(vt_symbol: object) -> str:
    raw = str(vt_symbol or "").strip()
    if not raw:
        return ""
    return raw.split(".", 1)[0].strip().upper()


def _resolve_price(intent: RotationOrderIntent, bar: Any) -> float:
    hint = intent.candidate.get("entry_price_hint") if isinstance(intent.candidate, dict) else None
    if hint is not None:
        try:
            px = float(hint)
            if math.isfinite(px) and px > 0.0:
                return px
        except (TypeError, ValueError):
            pass
    close_price = getattr(bar, "close_price", None)
    if close_price is None:
        return 0.0
    try:
        px = float(close_price)
    except (TypeError, ValueError):
        return 0.0
    return px if math.isfinite(px) and px > 0.0 else 0.0


def _dispatch_via_strategy(strategy: Any, order: dict[str, Any], bar: Any) -> None:
    dispatch = getattr(strategy, "_dispatch_order", None)
    if callable(dispatch):
        dispatch(dict(order), bar)
        return

    side = str(order.get("side", "")).lower()
    lots = max(int(order.get("lots", 0) or 0), 0)
    if lots <= 0:
        logger.debug(
            "rotation drop: reason=non_positive_lots symbol=%s side=%s lots=%s",
            order.get("rotation_symbol", ""),
            side,
            order.get("lots", 0),
        )
        return
    stop = str(order.get("order_type", "market")).lower() == "stop"
    price = float(order.get("price", getattr(bar, "close_price", 0.0)))
    if side == "long" and callable(getattr(strategy, "buy", None)):
        strategy.buy(price, lots, stop=stop)
    elif side == "short" and callable(getattr(strategy, "short", None)):
        strategy.short(price, lots, stop=stop)


def dispatch_rotation_intents(
    strategy: Any,
    *,
    intents: list[RotationOrderIntent],
    bar: Any,
    cfg: RotationOrderWireConfig,
) -> list[dict[str, Any]]:
    """Translate intents and dispatch orders onto the strategy instance."""
    dispatched: list[dict[str, Any]] = []
    strategy_symbol = _symbol_from_vt_symbol(getattr(strategy, "vt_symbol", ""))
    for intent in intents:
        symbol = str(intent.symbol).upper()
        if cfg.only_trade_matching_vt_symbol and strategy_symbol and strategy_symbol != symbol:
            logger.debug(
                "rotation drop: reason=vt_symbol_mismatch strategy_symbol=%s intent_symbol=%s side=%s",
                strategy_symbol,
                symbol,
                intent.side,
            )
            continue
        price = _resolve_price(intent, bar)
        if not (math.isfinite(price) and price > 0.0):
            logger.debug(
                "rotation drop: reason=invalid_price symbol=%s price=%r close=%r",
                symbol,
                price,
                getattr(bar, "close_price", None),
            )
            continue
        contract_size = float(cfg.contract_size_by_symbol.get(symbol, cfg.default_contract_size))
        lot_size = int(cfg.lot_size_by_symbol.get(symbol, cfg.default_lot_size))
        max_lots = cfg.max_lots_by_symbol.get(symbol)
        order = intent_to_legacy_order(
            intent,
            price=price,
            contract_size=contract_size,
            lot_size=lot_size,
            max_lots=max_lots,
            order_type=cfg.default_order_type,
        )
        _dispatch_via_strategy(strategy, order, bar)
        dispatched.append(order)
    return dispatched


def wire_rotation_main_loop(
    strategy: Any,
    *,
    stepper: RotationStepperLike,
    portfolio_state: PortfolioState,
    cfg: RotationOrderWireConfig | None = None,
) -> None:
    """Monkey-patch strategy.on_bar to execute rotation intents every bar tick."""
    if bool(getattr(strategy, "_rotation_main_loop_wired", False)):
        return
    if not callable(getattr(strategy, "on_bar", None)):
        raise TypeError("strategy must expose callable on_bar(bar)")
    wire_cfg = cfg or RotationOrderWireConfig()
    original_on_bar = strategy.on_bar

    def _wrapped_on_bar(bar: Any) -> None:
        original_on_bar(bar)
        dt = pd.Timestamp(getattr(bar, "datetime", None))
        if pd.isna(dt):
            return
        intents = list(stepper.step(dt, portfolio_state) or [])
        if not intents:
            return
        dispatch_rotation_intents(
            strategy,
            intents=intents,
            bar=bar,
            cfg=wire_cfg,
        )

    setattr(strategy, "on_bar", _wrapped_on_bar)
    setattr(strategy, "_rotation_main_loop_wired", True)


__all__ = [
    "RotationOrderWireConfig",
    "dispatch_rotation_intents",
    "wire_rotation_main_loop",
]
