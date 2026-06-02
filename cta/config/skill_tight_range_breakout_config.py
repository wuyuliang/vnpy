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
        # P2.1：金融关键参数值域校验。把"少打一个 0"这种 typo 在配置层拦住。
        mode = str(self.trade_side_mode).strip().lower()
        if mode not in VALID_SIDE_MODES:
            raise ValueError(f"trade_side_mode must be one of {sorted(VALID_SIDE_MODES)}")
        if int(self.lookback) <= 0:
            raise ValueError(f"lookback must be > 0: {self.lookback}")
        if int(self.min_count) <= 0:
            raise ValueError(f"min_count must be > 0: {self.min_count}")
        if int(self.lots) <= 0:
            raise ValueError(f"lots must be > 0: {self.lots}")
        if not (0.0 <= float(self.min_breakout_score) <= 1.0):
            raise ValueError(
                f"min_breakout_score must be in [0, 1]: {self.min_breakout_score}"
            )
        # risk_per_trade_pct 是单笔风险敞口比例，正常范围 0.1%-2%，>5% 几乎肯定是 typo。
        if not (0.0 < float(self.risk_per_trade_pct) <= 0.05):
            raise ValueError(
                f"risk_per_trade_pct must be in (0, 0.05]: {self.risk_per_trade_pct} "
                f"— normal CTA range 0.001~0.02; >5% almost certainly a typo"
            )
        if float(self.initial_stop_atr_mult) <= 0:
            raise ValueError(f"initial_stop_atr_mult must be > 0: {self.initial_stop_atr_mult}")
        if float(self.trailing_stop_atr_mult) <= 0:
            raise ValueError(f"trailing_stop_atr_mult must be > 0: {self.trailing_stop_atr_mult}")
        if int(self.max_holding_bars) <= 0:
            raise ValueError(f"max_holding_bars must be > 0: {self.max_holding_bars}")
        if float(self.alpha) <= 0:
            raise ValueError(f"alpha must be > 0: {self.alpha}")


@dataclass(frozen=True)
class BacktestConfig:
    """Backtest-level parameters and paths."""

    initial_capital: float = 10_000_000.0
    periods_per_year: int = 252
    output_root: Path = REPORT_BACKTEST_ROOT
    data_root: Path = DATA_ORIGIN_ROOT
    data_day_dir: Path = DATA_DAY_DIR
    symbols_list_path: Path = SYMBOLS_LIST_PATH
    interval: str = "day"

    def __post_init__(self) -> None:
        if float(self.initial_capital) <= 0:
            raise ValueError(f"initial_capital must be > 0: {self.initial_capital}")
        if int(self.periods_per_year) <= 0:
            raise ValueError(f"periods_per_year must be > 0: {self.periods_per_year}")
        if not str(self.interval).strip():
            raise ValueError("interval must not be empty")


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
