"""Pre-registered execution and cost stress scenarios."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StressScenario:
    name: str
    fee_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    entry_delay_bars: int = 0
    adverse_ohlc: bool = False
    limit_lock_exit: bool = False


STRESS_SCENARIOS = (
    StressScenario("BASE_COST"),
    StressScenario("SLIPPAGE_1_5X", slippage_multiplier=1.5),
    StressScenario("TOTAL_COST_2X", fee_multiplier=2.0, slippage_multiplier=2.0),
    StressScenario("ONE_BAR_DELAY", entry_delay_bars=1),
    StressScenario("ADVERSE_OHLC", adverse_ohlc=True),
    StressScenario("LIMIT_LOCK_EXIT", limit_lock_exit=True),
)


__all__ = ["STRESS_SCENARIOS", "StressScenario"]
