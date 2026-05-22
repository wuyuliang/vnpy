"""Config for baseline skill suite backtests and sample construction."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pandas as pd

from cta.config.skill_tight_range_breakout_config import CTA_ROOT

BASELINE_SIGNAL_TYPES: Final[tuple[str, ...]] = (
    "donchian_breakout",
    "atr_breakout",
    "tight_range_breakout",
    "breakout_pullback_continuation",
    "trend_acceleration_breakout",
    "bull_pullback_continuation",
    "bull_volatility_contraction_breakout",
    "mean_reversion_range",
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
    "is_one_way_bar",
    "is_limit_up_close",
    "is_limit_down_close",
    "bp_breakout_level",
    "bp_bars_since_breakout",
    "bp_confirmed",
    "trend_acceleration_score",
    "pullback_quality",
    "volatility_contraction_pctl",
    "breakout_body_strength",
    "mr_sma",
    "mr_zscore",
    "mr_bb_upper",
    "mr_bb_lower",
    "mr_rsi",
    "mr_adx",
    "mr_signal_strength",
)

DEFAULT_REPORT_ROOT: Final[Path] = CTA_ROOT / "report" / "backtest"
LABEL_MAE_PENALTY: Final[float] = 0.7
LABEL_THRESHOLD: Final[float] = 0.2

# 候选机会等级断点：opportunity_score = future_mfe_atr - LABEL_MAE_PENALTY * future_mae_atr
# A: score >= A_BREAK；B: score >= B_BREAK；C: score >= LABEL_THRESHOLD；D: 其余；
# U: opportunity_score 不可信（atr_warmup 期或缺价），由 candidate_training_dataset 设置。
OPPORTUNITY_CLASS_A_BREAK: Final[float] = 1.2
OPPORTUNITY_CLASS_B_BREAK: Final[float] = 0.6

# Bull-market baseline setup thresholds.  The short side is deliberately stricter
# to reduce counter-trend shorts during broad upside regimes.
TREND_ACCELERATION_LONG_MIN_SCORE: Final[float] = 0.55
TREND_ACCELERATION_LONG_MIN_BODY: Final[float] = 0.60
TREND_ACCELERATION_SHORT_MAX_SCORE: Final[float] = -0.75
TREND_ACCELERATION_SHORT_MIN_BODY: Final[float] = 0.70
BULL_PULLBACK_MIN_QUALITY: Final[float] = 0.55


_VALID_BASELINE_SIDE_MODES: Final[frozenset[str]] = frozenset({"both", "long", "short"})


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

    def __post_init__(self) -> None:
        # P2.1：值域校验，把"参数写错少打一个 0"这种 bug 在配置加载时就拦住，
        # 不再让它沉到下游 backtest / OOT 才报错。
        if not str(self.symbol).strip():
            raise ValueError("symbol must not be empty")
        if str(self.trade_side_mode).strip().lower() not in _VALID_BASELINE_SIDE_MODES:
            raise ValueError(
                f"trade_side_mode must be one of {sorted(_VALID_BASELINE_SIDE_MODES)}: "
                f"got {self.trade_side_mode!r}"
            )
        if float(self.initial_capital) <= 0:
            raise ValueError(f"initial_capital must be > 0: {self.initial_capital}")
        if self.periods_per_year is not None and int(self.periods_per_year) <= 0:
            raise ValueError(f"periods_per_year must be > 0 or None: {self.periods_per_year}")
        if not self.signal_types:
            raise ValueError("signal_types must not be empty")
        if pd.Timestamp(self.start_date) >= pd.Timestamp(self.end_date):
            raise ValueError(
                f"start_date must be earlier than end_date: "
                f"start={self.start_date} end={self.end_date}"
            )


__all__ = [
    "BASELINE_SIGNAL_TYPES",
    "TRAINING_FEATURE_COLUMNS",
    "DEFAULT_REPORT_ROOT",
    "LABEL_MAE_PENALTY",
    "LABEL_THRESHOLD",
    "OPPORTUNITY_CLASS_A_BREAK",
    "OPPORTUNITY_CLASS_B_BREAK",
    "TREND_ACCELERATION_LONG_MIN_SCORE",
    "TREND_ACCELERATION_LONG_MIN_BODY",
    "TREND_ACCELERATION_SHORT_MAX_SCORE",
    "TREND_ACCELERATION_SHORT_MIN_BODY",
    "BULL_PULLBACK_MIN_QUALITY",
    "BaselineSuiteConfig",
]
