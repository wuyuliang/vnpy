"""Portfolio executor wrapper for spread arbitrage strategy."""
from __future__ import annotations

from typing import Callable, Mapping

import pandas as pd

from cta.config.spread_arbitrage_config import SpreadArbitrageConfig
from cta.config.spread_pair_registry import SpreadPair
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.strategy.spread_arbitrage_strategy import SpreadArbitrageStrategy, SpreadOrderIntent

BarsProvider = Callable[[pd.Timestamp], Mapping[str, pd.DataFrame]]
DrawdownProvider = Callable[[PortfolioState], float]


class SpreadArbitrageExecutor:
    """Translate bars snapshot into spread order intents."""

    def __init__(
        self,
        *,
        cfg: SpreadArbitrageConfig,
        pairs: tuple[SpreadPair, ...],
        bars_provider: BarsProvider,
        interval: str = "day",
        drawdown_provider: DrawdownProvider | None = None,
    ) -> None:
        self.bars_provider = bars_provider
        self.drawdown_provider = drawdown_provider
        self.strategy = SpreadArbitrageStrategy(
            cfg=cfg,
            pairs=pairs,
            interval=interval,
        )

    def step(self, t: pd.Timestamp, portfolio_state: PortfolioState) -> list[SpreadOrderIntent]:
        """Run one timestamp and return spread intents."""
        ts = pd.Timestamp(t)
        bars = self.bars_provider(ts)
        drawdown = (
            float(self.drawdown_provider(portfolio_state))
            if self.drawdown_provider is not None
            else 0.0
        )
        return self.strategy.step(
            ts,
            bars,
            portfolio_state,
            current_drawdown_pct=drawdown,
        )


__all__ = ["SpreadArbitrageExecutor", "BarsProvider", "DrawdownProvider"]

