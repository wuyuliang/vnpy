"""§02 震荡识别 / Range Detection —— range 打分 + 上下沿 + 年龄.

对应 cta/cta_skills/01_market_regime/02_range_detection.md §4-§6。

range_score 组成（权重来自 md §5 策略 A）
----------------------------------------
- ADX 低           : 0.30 * (adx < 20)
- 布林带宽压缩     : 0.25 * (bb_width < 其 30% 分位)
- 反持续 (1-DER)   : 0.25 * (DER < 0.3)           # 用 DER 低作为 Hurst 的廉价代理
- 通道拟合残差大   : 0.20 * (residual_pct > 0.01)   # 残差大=偏离趋势线=乱动=震荡

上下沿：rolling high/low(N)。宽度：(upper - lower) / mid。
范围年龄：range_score 连续高于阈值的 bar 数。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class RangeState:
    score: float                              # [0,1]
    upper: float
    lower: float
    width_pct: float                          # (upper-lower)/mid
    age_bars: int                             # 连续在 range 的 bar 数

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score 应 ∈ [0,1]，got {self.score}")
        if self.upper <= self.lower:
            raise ValueError(
                f"upper ({self.upper}) 必须 > lower ({self.lower})"
            )
        if self.age_bars < 0:
            raise ValueError(f"age_bars 应 ≥ 0，got {self.age_bars}")


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
def _wilder_smooth(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _adx(
    high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14,
) -> pd.Series:
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
    denom = (plus_di + minus_di).where((plus_di + minus_di) > 0)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return _wilder_smooth(dx, n)


def _bollinger_width(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    ma = close.rolling(n, min_periods=max(n // 2, 5)).mean()
    std = close.rolling(n, min_periods=max(n // 2, 5)).std(ddof=0)
    width = (2 * k * std) / ma.where(ma > 0)
    return width


def _rolling_pct_rank(s: pd.Series, window: int) -> pd.Series:
    def _rank(x: np.ndarray) -> float:
        return float(np.mean(x <= x[-1]))
    return s.rolling(window=window, min_periods=max(window // 2, 10)).apply(
        _rank, raw=True,
    )


def _direction_efficiency_ratio(close: pd.Series, n: int = 20) -> pd.Series:
    net = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=max(n // 2, 5)).sum()
    return net / path.where(path > 0)


def _channel_residual_pct(close: pd.Series, n: int = 50) -> pd.Series:
    """
    对最近 n bar 做简单线性回归，返回 std(residual) / close 的滚动值。
    越小表示 close 越贴近趋势线 → 更有方向性；越大表示震荡强。
    """
    def _resid_std(x: np.ndarray) -> float:
        m = len(x)
        if m < 2:
            return float("nan")
        idx = np.arange(m, dtype=float)
        idx_mean = idx.mean()
        idx_var = ((idx - idx_mean) ** 2).sum()
        if idx_var <= 0:
            return float("nan")
        y_mean = x.mean()
        slope = ((idx - idx_mean) * (x - y_mean)).sum() / idx_var
        intercept = y_mean - slope * idx_mean
        fit = slope * idx + intercept
        return float(np.std(x - fit))

    resid_std = close.rolling(n, min_periods=max(n // 2, 10)).apply(
        _resid_std, raw=True,
    )
    return resid_std / close.where(close > 0)


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def compute_range_state(
    df: pd.DataFrame,
    bb_n: int = 20,
    bb_k: float = 2.0,
    adx_n: int = 14,
    der_n: int = 20,
    channel_n: int = 50,
    bound_n: int = 20,
    pct_window: int = 252,
    score_threshold: float = 0.6,
) -> pd.DataFrame:
    """
    输入：含 high/low/close 的 OHLCV df。
    输出：同长度 df，新列
        range_score, range_upper, range_lower, range_width_pct, range_age.
    """
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"compute_range_state 缺少列 {miss}")

    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)

    # ---- 指标 ----
    adx = _adx(high, low, close, adx_n)
    bb_w = _bollinger_width(close, bb_n, bb_k)
    bb_w_pct = _rolling_pct_rank(bb_w, pct_window)
    der = _direction_efficiency_ratio(close, der_n)
    resid_pct = _channel_residual_pct(close, channel_n)

    # ---- 得分 ----
    c1 = (adx < 20).astype(float).where(adx.notna(), np.nan)
    c2 = (bb_w_pct < 0.3).astype(float).where(bb_w_pct.notna(), np.nan)
    c3 = (der < 0.3).astype(float).where(der.notna(), np.nan)
    # 通道残差：residual > 1%（偏离线性趋势） → 乱动 → 更像 range；
    # residual < 1% → 贴趋势线 → 趋势性强 → 非 range。
    c4 = (resid_pct > 0.01).astype(float).where(resid_pct.notna(), np.nan)

    score = (0.30 * c1.fillna(0) + 0.25 * c2.fillna(0)
             + 0.25 * c3.fillna(0) + 0.20 * c4.fillna(0))
    # 所有四个子指标都 NaN 才置 NaN
    all_nan = c1.isna() & c2.isna() & c3.isna() & c4.isna()
    score = score.mask(all_nan, np.nan).clip(0.0, 1.0)

    # ---- 上下沿 ----
    upper = high.rolling(bound_n, min_periods=max(bound_n // 2, 5)).max()
    lower = low.rolling(bound_n, min_periods=max(bound_n // 2, 5)).min()
    mid = (upper + lower) / 2.0
    width_pct = (upper - lower) / mid.where(mid > 0)

    # ---- age ----
    age = detect_range_age(score, threshold=score_threshold)

    out["range_score"] = score
    out["range_upper"] = upper
    out["range_lower"] = lower
    out["range_width_pct"] = width_pct
    out["range_age"] = age
    return out


def detect_range_age(
    range_score_series: pd.Series,
    threshold: float = 0.6,
) -> pd.Series:
    """
    返回每根 bar 上 `range_score > threshold` 的连续命中次数（命中期从 1 开始累加）。
    一旦 score 跌破阈值，age 重置为 0。
    """
    s = range_score_series.astype(float)
    hit = (s > threshold).astype(int)
    # 利用 groupby + cumcount：命中段内单调累加，非命中一律 0
    grp = (hit.diff().fillna(hit) != 0).cumsum()
    age = hit.groupby(grp).cumsum()
    return age.astype(int)
