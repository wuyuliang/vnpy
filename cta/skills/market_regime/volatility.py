"""§04 波动率状态 / Volatility Regime —— 四态分类 + vol targeting 仓位.

对应 cta/cta_skills/01_market_regime/04_volatility_regime.md §6。

四态定义
--------
- compression : vol_pct < 0.2  且  atr_ratio(14/60) < 0.7
- expansion   : vol_pct > 0.8  且  atr_ratio(14/60) > 1.3
- low         : vol_pct < 0.4  （去掉上面两态后的剩余）
- high        : else

所有函数 pure：输入 OHLCV DataFrame，输出一个 len 与输入相同的新 DataFrame。
列前缀统一 vol_。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

VolRegime = Literal["compression", "expansion", "low", "high"]
_VOL_REGIMES: tuple[str, ...] = ("compression", "expansion", "low", "high")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class VolState:
    """单根 bar 的 vol 状态。批量版把 5 个字段展开成列输出。"""
    regime: VolRegime
    atr: float
    atr_pct: float                   # 历史分位 [0,1]
    atr_ratio_fast_slow: float
    annualized_vol: float

    def __post_init__(self) -> None:
        if self.regime not in _VOL_REGIMES:
            raise ValueError(
                f"regime 非法: {self.regime!r}，合法值: {_VOL_REGIMES}"
            )
        if not (0.0 <= self.atr_pct <= 1.0):
            raise ValueError(
                f"atr_pct 应 ∈ [0,1]，got {self.atr_pct}"
            )


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
def _wilder_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, n: int,
) -> pd.Series:
    """Wilder ATR: 用 EWM 衰减系数 1/n 近似 Wilder 平滑。"""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder 平滑 = EMA with alpha=1/n
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _rolling_pct_rank(s: pd.Series, window: int) -> pd.Series:
    """
    每个点在过去 window（含自身）范围内的分位 [0,1]。
    min_periods=window 的 N-1 个前值会是 NaN。
    """
    def _rank(x: np.ndarray) -> float:
        # 位置 = rank(最后一个) / 长度；这里用的是 "比它小等于的占比"
        last = x[-1]
        return float(np.mean(x <= last))
    return s.rolling(window=window, min_periods=max(window // 2, 10)).apply(
        _rank, raw=True,
    )


def _annualized_vol(close: pd.Series, window: int = 60,
                    bars_per_year: int = 252) -> pd.Series:
    r = np.log(close / close.shift(1))
    return r.rolling(window=window, min_periods=max(window // 2, 10)).std(ddof=0) \
            * np.sqrt(bars_per_year)


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def compute_vol_state(
    df: pd.DataFrame,
    atr_n_fast: int = 14,
    atr_n_slow: int = 60,
    pct_window: int = 252,
    ann_window: int = 60,
    bars_per_year: int = 252,
) -> pd.DataFrame:
    """
    输入：含 high/low/close 的 OHLCV df（可含 open/volume/datetime）。
    输出：同长度 df，新列前缀 vol_ :
        vol_regime (str), vol_atr, vol_atr_pct, vol_atr_ratio, vol_ann.
    原有列保留。

    不同 interval 可通过调整 pct_window / ann_window 使用；默认 252 bar 对
    day 合适，分钟级应放大（如 minute30: 480-960）。
    """
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"compute_vol_state 缺少列 {miss}")

    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)

    atr_fast = _wilder_atr(high, low, close, atr_n_fast)
    atr_slow = _wilder_atr(high, low, close, atr_n_slow)

    atr_pct = _rolling_pct_rank(atr_fast, pct_window)
    # atr_slow 为 0 时置 NaN 防止除零
    atr_ratio = atr_fast / atr_slow.where(atr_slow > 0)
    ann_vol = _annualized_vol(close, ann_window, bars_per_year)

    regime = _classify(atr_pct, atr_ratio)

    out["vol_atr"] = atr_fast
    out["vol_atr_pct"] = atr_pct
    out["vol_atr_ratio"] = atr_ratio
    out["vol_ann"] = ann_vol
    out["vol_regime"] = regime
    return out


def _classify(atr_pct: pd.Series, atr_ratio: pd.Series) -> pd.Series:
    """按 §04 §4 阈值映射四态；缺数据返回 'high'（默认保守）。"""
    regime = pd.Series(index=atr_pct.index, dtype=object)

    # 缺失：给 high（保守）
    regime[:] = "high"

    mask_low = atr_pct < 0.4
    mask_comp = (atr_pct < 0.2) & (atr_ratio < 0.7)
    mask_exp = (atr_pct > 0.8) & (atr_ratio > 1.3)

    regime.loc[mask_low] = "low"
    regime.loc[mask_comp] = "compression"
    regime.loc[mask_exp] = "expansion"
    # atr_pct 或 atr_ratio 为 NaN 的行：保持 high
    na_mask = atr_pct.isna() | atr_ratio.isna()
    regime.loc[na_mask] = "high"
    return regime


def vol_target_size(
    atr: float,
    contract_multiplier: float,
    equity: float,
    risk_pct: float = 0.003,
) -> float:
    """
    vol targeting 单手数：`risk_pct * equity / (atr * contract_multiplier)`。

    若 atr 为 0 或 NaN，返回 0（无波动无法估风险）。
    """
    if contract_multiplier <= 0:
        raise ValueError(f"contract_multiplier 应 > 0，got {contract_multiplier}")
    if equity <= 0:
        raise ValueError(f"equity 应 > 0，got {equity}")
    if risk_pct <= 0:
        raise ValueError(f"risk_pct 应 > 0，got {risk_pct}")
    if atr is None or atr != atr or atr <= 0:
        return 0.0
    return float(risk_pct * equity / (atr * contract_multiplier))
