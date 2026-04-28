"""Config for skill-based tight range breakout strategy/backtest."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

CTA_ROOT: Path = Path(__file__).resolve().parents[1]
DATA_ROOT: Path = CTA_ROOT / "data"
DATA_ORIGIN_ROOT: Path = DATA_ROOT / "origin"
DATA_DAY_DIR: Path = DATA_ORIGIN_ROOT / "day"
SYMBOLS_LIST_PATH: Path = DATA_DAY_DIR / "symbols_list.csv"
REPORT_BACKTEST_ROOT: Path = CTA_ROOT / "report" / "backtest"
VALID_SIDE_MODES: Final[set[str]] = {"both", "long", "short"}


@dataclass(frozen=True)
class StrategyConfig:
    """Strategy-level parameters."""

    lookback: int = 10
    alpha: float = 1.5
    min_count: int = 5
    min_breakout_score: float = 0.30
    lots: int = 1
    risk_per_trade_pct: float = 0.005
    initial_stop_atr_mult: float = 1.5
    trailing_stop_atr_mult: float = 2.2
    max_holding_bars: int = 20
    align_trend_direction: bool = True
    trade_side_mode: str = "both"

    def __post_init__(self) -> None:
        mode = str(self.trade_side_mode).strip().lower()
        if mode not in VALID_SIDE_MODES:
            raise ValueError(f"trade_side_mode must be one of {sorted(VALID_SIDE_MODES)}")


@dataclass(frozen=True)
class BacktestConfig:
    """Backtest-level parameters and paths."""

    initial_capital: float = 1_000_000.0
    periods_per_year: int = 252
    output_root: Path = REPORT_BACKTEST_ROOT
    data_root: Path = DATA_ORIGIN_ROOT
    data_day_dir: Path = DATA_DAY_DIR
    symbols_list_path: Path = SYMBOLS_LIST_PATH
    interval: str = "day"


__all__ = [
    "CTA_ROOT",
    "DATA_ROOT",
    "DATA_ORIGIN_ROOT",
    "DATA_DAY_DIR",
    "SYMBOLS_LIST_PATH",
    "REPORT_BACKTEST_ROOT",
    "VALID_SIDE_MODES",
    "StrategyConfig",
    "BacktestConfig",
]
