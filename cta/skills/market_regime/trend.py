"""§01 趋势识别 / Trend Detection —— 多指标融合打分.

对应 cta/cta_skills/01_market_regime/01_trend_detection.md §4-§6。

指标组成（权重来自 md §5 策略 A）
--------------------------------
- MA 斜率            : 0.35 * sign(ma_slow_slope_N)
- ADX(14) + DI 差    : 0.25 * (adx > 25 ? sign(+DI - -DI) : 0)
- HH/LL 结构         : 0.20 * sign(最近 N bar 是否创新高/低)
- 方向效率比 DER     : 0.20 * (der > 0.3 ? sign(close[t]-close[t-N]) : 0)

输出
----
每根 bar 一行，新列：
- trend_score       float ∈ [-1, 1]
- trend_dir         int ∈ {-1, 0, 1}   （|score| > 0.5 取 sign，否则 0）
- trend_strength    float ∈ [0, 1]      （|score|）
- trend_maturity    str ∈ {young, mature, late, none}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

Maturity = Literal["young", "mature", "late", "none"]
_MATURITY_VALS: tuple[str, ...] = ("young", "mature", "late", "none")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class TrendState:
    score: float                         # [-1, 1]
    direction: int                       # -1 / 0 / 1
    strength: float                      # [0, 1]
    maturity: Maturity

    def __post_init__(self) -> None:
        if not (-1.0 <= self.score <= 1.0):
            raise ValueError(f"score 应 ∈ [-1,1]，got {self.score}")
        if self.direction not in (-1, 0, 1):
            raise ValueError(f"direction 应 ∈ {{-1,0,1}}，got {self.direction}")
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError(f"strength 应 ∈ [0,1]，got {self.strength}")
        if self.maturity not in _MATURITY_VALS:
            raise ValueError(
                f"maturity 非法: {self.maturity!r}，合法值 {_MATURITY_VALS}"
            )


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder_smooth(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _adx_di(
    high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """返回 (adx, plus_di, minus_di)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    dn_move = low.shift(1) - low
    plus_dm = pd.Series(
        np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0),
        index=high.index,
    )

    atr = _wilder_smooth(tr, n)
    plus_di = 100.0 * _wilder_smooth(plus_dm, n) / atr.where(atr > 0)
    minus_di = 100.0 * _wilder_smooth(minus_dm, n) / atr.where(atr > 0)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).where(
        (plus_di + minus_di) > 0
    )
    adx = _wilder_smooth(dx, n)
    return adx, plus_di, minus_di


def _direction_efficiency_ratio(close: pd.Series, n: int = 20) -> pd.Series:
    """|close[t] - close[t-n]| / sum(|close.diff()|, last n)."""
    net_move = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=max(n // 2, 5)).sum()
    return net_move / path.where(path > 0)


def _hh_ll_structure(
    high: pd.Series, low: pd.Series, n: int = 20, lookback: int = 5,
) -> pd.Series:
    """
    最近 lookback bar 内若出现 n 期新高 → +1，n 期新低 → -1，否则 0。
    混合命中（既有新高又有新低）→ 0。
    """
    roll_high = high.rolling(n, min_periods=max(n // 2, 5)).max()
    roll_low = low.rolling(n, min_periods=max(n // 2, 5)).min()
    made_hh = (high >= roll_high)
    made_ll = (low <= roll_low)
    recent_hh = made_hh.rolling(lookback, min_periods=1).max().astype(float)
    recent_ll = made_ll.rolling(lookback, min_periods=1).max().astype(float)
    return (recent_hh - recent_ll).clip(-1, 1)


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def compute_trend_state(
    df: pd.DataFrame,
    ma_fast: int = 20,
    ma_slow: int = 50,
    slope_window: int = 5,
    adx_n: int = 14,
    der_n: int = 20,
    hh_n: int = 20,
    hh_lookback: int = 5,
    direction_threshold: float = 0.5,
) -> pd.DataFrame:
    """
    输入：含 high/low/close 的 OHLCV df。
    输出：与输入同长度，新增列
        trend_score, trend_dir, trend_strength, trend_maturity。

    任何 NaN 位置（指标窗口不够）会 propagate 到 trend_score；direction 对 NaN
    取 0，maturity 取 'none'。
    """
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"compute_trend_state 缺少列 {miss}")

    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)

    # 1) MA 斜率
    ma = _ema(close, ma_slow)
    ma_slope = (ma - ma.shift(slope_window)) / ma.shift(slope_window).where(
        ma.shift(slope_window) > 0
    )
    # 对斜率做 sign（+1/-1）并用 tanh 做平滑（避免边缘抖动）
    sign_slope = np.tanh(ma_slope * 100.0)   # 1% 斜率就接近 ±1

    # 2) ADX + DI
    adx, plus_di, minus_di = _adx_di(high, low, close, adx_n)
    adx_component = pd.Series(
        np.where(
            adx > 25,
            np.sign(plus_di - minus_di).fillna(0),
            0.0,
        ),
        index=close.index,
        dtype=float,
    )

    # 3) HH/LL 结构
    hhll = _hh_ll_structure(high, low, hh_n, hh_lookback)

    # 4) DER
    der = _direction_efficiency_ratio(close, der_n)
    der_sign = np.sign(close - close.shift(der_n)).fillna(0.0)
    der_component = pd.Series(
        np.where(der > 0.3, der_sign, 0.0),
        index=close.index,
        dtype=float,
    )

    # 加权求和
    score = (
        0.35 * sign_slope.fillna(0.0)
        + 0.25 * adx_component
        + 0.20 * hhll.fillna(0.0)
        + 0.20 * der_component
    )
    # 所有指标都 NaN 才标 NaN；任何一个可用即给数值
    all_nan_mask = (
        sign_slope.isna() & hhll.isna()
    )
    score = score.mask(all_nan_mask, np.nan).clip(-1.0, 1.0)

    direction = pd.Series(0, index=close.index, dtype=int)
    direction = direction.mask(score > direction_threshold, 1)
    direction = direction.mask(score < -direction_threshold, -1)

    strength = score.abs()
    maturity = classify_maturity(score, threshold=direction_threshold)

    out["trend_score"] = score
    out["trend_dir"] = direction
    out["trend_strength"] = strength
    out["trend_maturity"] = maturity
    return out


def classify_maturity(
    score_series: pd.Series,
    lookback: int = 60,
    threshold: float = 0.5,
) -> pd.Series:
    """
    根据 trend_score 历史分布标 young/mature/late/none。

    规则
    ----
    - 定义 "在趋势中" = |score| > threshold；连续命中的 bar 数记为 run_length
    - run_length < 20 : young
    - 20 <= run_length <= lookback 且分数还在走强/持平：mature
    - run_length > lookback 且 score 有从高点回落：late
    - 若当前 |score| <= threshold：none
    """
    score = score_series.astype(float)
    abs_score = score.abs()
    in_trend = (abs_score > threshold).astype(int)

    # run length: 连续命中的计数
    # 用 cumsum trick：每次变回 0 都把基线抬上去
    grp = (in_trend.diff().fillna(in_trend) != 0).cumsum()
    run_length = in_trend.groupby(grp).cumsum()

    out = pd.Series("none", index=score.index, dtype=object)
    out[in_trend == 1] = "mature"
    out[(in_trend == 1) & (run_length < 20)] = "young"

    # late: run > lookback 且最近 |score| 从 rolling_max 回落 > 20%
    peak = abs_score.rolling(lookback, min_periods=5).max()
    late_mask = (
        (in_trend == 1)
        & (run_length > lookback)
        & (abs_score < peak * 0.8)
    )
    out[late_mask] = "late"
    return out
