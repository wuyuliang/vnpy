"""Config for baseline skill suite backtests and sample construction."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cta.config.skill_tight_range_breakout_config import CTA_ROOT

BASELINE_SIGNAL_TYPES: Final[tuple[str, ...]] = (
    "donchian_breakout",
    "atr_breakout",
    "tight_range_breakout",
    "breakout_pullback_continuation",
)

TRAINING_FEATURE_COLUMNS: Final[tuple[str, ...]] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "turnover",
    "atr14",
    "trend_score",
    "trend_dir",
    "tr_range_atr",
    "breakout_score",
    "don_upper_entry",
    "don_lower_entry",
    "don_upper_exit",
    "don_lower_exit",
    "don_atr20",
    "atr_ma",
    "atr_value",
    "atr_upper",
    "atr_lower",
    "bp_breakout_level",
    "bp_bars_since_breakout",
    "bp_confirmed",
)

DEFAULT_REPORT_ROOT: Final[Path] = CTA_ROOT / "report" / "backtest"
LABEL_MAE_PENALTY: Final[float] = 0.7
LABEL_THRESHOLD: Final[float] = 0.2

# 候选机会等级断点：opportunity_score = future_mfe_atr - LABEL_MAE_PENALTY * future_mae_atr
# A: score >= A_BREAK；B: score >= B_BREAK；C: score >= LABEL_THRESHOLD；D: 其余；
# U: opportunity_score 不可信（atr_warmup 期或缺价），由 candidate_training_dataset 设置。
OPPORTUNITY_CLASS_A_BREAK: Final[float] = 1.2
OPPORTUNITY_CLASS_B_BREAK: Final[float] = 0.6


@dataclass(frozen=True)
class BaselineSuiteConfig:
    """High-level parameters for baseline suite run."""

    symbol: str = "RB0"
    exchange: str | None = "SHFE"
    interval: str = "60min"
    start_date: str = "2000-01-01"
    end_date: str = "2019-12-31"
    trade_side_mode: str = "both"
    initial_capital: float = 1_000_000.0
    periods_per_year: int | None = None
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES
    output_root: Path = DEFAULT_REPORT_ROOT


__all__ = [
    "BASELINE_SIGNAL_TYPES",
    "TRAINING_FEATURE_COLUMNS",
    "DEFAULT_REPORT_ROOT",
    "LABEL_MAE_PENALTY",
    "LABEL_THRESHOLD",
    "OPPORTUNITY_CLASS_A_BREAK",
    "OPPORTUNITY_CLASS_B_BREAK",
    "BaselineSuiteConfig",
]
