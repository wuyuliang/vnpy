"""Portfolio executor for cross-sectional momentum rotation candidates."""
from __future__ import annotations

import math
from dataclasses import dataclass
import logging
from typing import Callable, Mapping

import pandas as pd

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.strategy.cross_sectional_momentum_rotation import (
    RolloverCheck,
    CrossSectionalMomentumRotation,
)
from cta.portfolio_logic.portfolio_state import PortfolioState

logger = logging.getLogger(__name__)

UniverseProvider = Callable[[pd.Timestamp], Mapping[str, pd.DataFrame]]
DrawdownProvider = Callable[[PortfolioState], float]


@dataclass(frozen=True)
class RotationOrderIntent:
    """Portfolio-facing target intent emitted from one rotation candidate."""

    symbol: str
    exchange: str
    side: str
    signal_type: str
    signal_datetime: pd.Timestamp
    entry_datetime: pd.Timestamp
    planned_exit_datetime: pd.Timestamp
    stop_price: float
    target_weight: float
    target_notional: float
    candidate: dict[str, object]


class CrossSectionalRotationExecutor:
    """Translate weekly cross-sectional rebalance candidates into order intents."""

    def __init__(
        self,
        *,
        cfg: CrossSectionalRotationConfig,
        universe_provider: UniverseProvider,
        interval: str = "day",
        exchange_by_symbol: Mapping[str, str] | None = None,
        disabled_symbols: set[str] | None = None,
        rollover_check: RolloverCheck | None = None,
        drawdown_provider: DrawdownProvider | None = None,
    ) -> None:
        self.cfg = cfg
        self.interval = str(interval)
        self.universe_provider = universe_provider
        self.exchange_by_symbol = {
            str(symbol).upper(): str(exchange).upper()
            for symbol, exchange in dict(exchange_by_symbol or {}).items()
        }
        self.disabled_symbols = {str(symbol).upper() for symbol in (disabled_symbols or set())}
        self.rollover_check = rollover_check
        self.drawdown_provider = drawdown_provider
        self.rotation = CrossSectionalMomentumRotation(cfg)
        self.last_rebalance_dt: pd.Timestamp | None = None

    def step(self, t: pd.Timestamp, portfolio_state: PortfolioState) -> list[RotationOrderIntent]:
        """Return target intents for the current rebalance timestamp."""
        as_of = pd.Timestamp(t)
        universe_bars = self.universe_provider(as_of)
        current_drawdown_pct = (
            float(self.drawdown_provider(portfolio_state))
            if self.drawdown_provider is not None
            else 0.0
        )
        candidates = self.rotation.generate_rebalance_candidates(
            as_of,
            universe_bars,
            interval=self.interval,
            last_rebalance_dt=self.last_rebalance_dt,
            current_drawdown_pct=current_drawdown_pct,
            disabled_symbols=self.disabled_symbols,
            rollover_check=self.rollover_check,
        )
        if candidates.empty:
            return []

        self.last_rebalance_dt = as_of
        intents: list[RotationOrderIntent] = []
        for _, row in candidates.iterrows():
            symbol = str(row.get("symbol", "")).upper()
            raw_weight = row.get("target_weight", 0.0)
            target_weight = float(raw_weight) if raw_weight is not None else 0.0
            # NaN guard: float(nan) 是 truthy，不能依赖 `or 0.0` 兜底（H3 修复）。
            if not math.isfinite(target_weight):
                logger.warning("skip rotation intent: symbol=%s has non-finite target_weight", symbol)
                continue
            target_notional = abs(target_weight) * float(portfolio_state.equity)
            if not symbol or target_notional <= 0.0:
                continue
            intents.append(
                RotationOrderIntent(
                    symbol=symbol,
                    exchange=self.exchange_by_symbol.get(symbol, ""),
                    side=str(row.get("side", "")).lower(),
                    signal_type=str(row.get("signal_type", "")),
                    signal_datetime=pd.Timestamp(row["signal_datetime"]),
                    entry_datetime=pd.Timestamp(row["entry_datetime"]),
                    planned_exit_datetime=pd.Timestamp(row["planned_exit_datetime"]),
                    stop_price=float(row.get("stop_price", float("nan"))),
                    target_weight=target_weight,
                    target_notional=target_notional,
                    candidate=row.to_dict(),
                )
            )
        logger.info(
            "cross-sectional rotation executor emitted %d intents at %s for interval=%s",
            len(intents),
            as_of,
            self.interval,
        )
        return intents


__all__ = [
    "CrossSectionalRotationExecutor",
    "RotationOrderIntent",
]
