"""
§13 波动率体制补充特征 / Volatility Regime Supplement

在 volatility.py 已有 atr/natr/bb/hist_vol/gk 的基础上，补充**分位 / 分类 / 二阶**视角。
全部 bar-derivable，对应 FEATURES.md §13。

输出列：
    - atr_pct_zscore_252
    - bb_width_zscore_252
    - vol_regime_3              (1=low / 2=normal / 3=high)
    - vol_of_vol_20
    - range_ratio_{10,20,60}
    - range_height_atr_{5,10,20}
    - consecutive_hh_{5,10,20}
    - consecutive_ll_{5,10,20}
    - zscore_close_{10,20,60}
    - breakout_dist_atr_{10,20}        (close 距离 dc_upper_N / atr_14)
    - breakout_dist_atr_down_{10,20}   (close 距离 dc_lower_N / atr_14)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.volatility import atr, natr, bollinger_bands, donchian_channel


_ZSCORE_WINDOWS: tuple[int, ...] = (252,)
_RANGE_RATIO_WINDOWS: tuple[int, ...] = (10, 20, 60)
_RANGE_ATR_WINDOWS: tuple[int, ...] = (5, 10, 20)
_CONSEC_WINDOWS: tuple[int, ...] = (5, 10, 20)
_ZCLOSE_WINDOWS: tuple[int, ...] = (10, 20, 60)
_BREAK_DIST_WINDOWS: tuple[int, ...] = (10, 20)


def _rolling_zscore(series: pd.Series, window: int,
                    min_periods: int | None = None) -> pd.Series:
    """滚动 zscore：(x - mean_N) / std_N，window 内样本不足时返回 NaN"""
    mp = min_periods if min_periods is not None else max(10, window // 4)
    mean = series.rolling(window, min_periods=mp).mean()
    std = series.rolling(window, min_periods=mp).std()
    return (series - mean) / std.replace(0, np.nan)


def _rolling_percentile_rank(series: pd.Series, window: int,
                             min_periods: int | None = None) -> pd.Series:
    """滚动分位（0-100）：当前值在过去 window 期中的百分位排名"""
    mp = min_periods if min_periods is not None else max(10, window // 4)

    def _pct(arr: np.ndarray) -> float:
        if np.isnan(arr[-1]):
            return np.nan
        n = np.sum(~np.isnan(arr))
        if n < 2:
            return np.nan
        return float((np.sum(arr[:-1] <= arr[-1]) + 0.5) / n * 100)

    return series.rolling(window, min_periods=mp).apply(_pct, raw=True)


def _consecutive_flag_count(flag: pd.Series, window: int) -> pd.Series:
    """滚动窗口内 flag==1 的连续计数（从当前向前）"""
    flag = flag.fillna(0).astype(int)
    # run-length 从当前向前：x.cumsum() 在 reset 位置做差，参考 pandas recipe
    grp = (flag == 0).cumsum()
    running = flag.groupby(grp).cumsum()
    return running.rolling(window, min_periods=1).max()


def compute_volatility_regime_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    输入 df 需包含: open, high, low, close
    返回 DataFrame（index 与 df 对齐）
    """
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]

    result = pd.DataFrame(index=df.index)

    # ---- natr / bb_width 的 252 日 zscore ----
    natr_14 = natr(h, l, c, 14)
    bb = bollinger_bands(c, 20, 2.0)["bb_width"]
    for w in _ZSCORE_WINDOWS:
        result[f"atr_pct_zscore_{w}"] = _rolling_zscore(natr_14, w)
        result[f"bb_width_zscore_{w}"] = _rolling_zscore(bb, w)

    # ---- 波动率体制三分类（1=low / 2=normal / 3=high） ----
    natr_pct = _rolling_percentile_rank(natr_14, 252)
    regime = pd.Series(np.nan, index=df.index, dtype="float64")
    regime[natr_pct < 30] = 1.0
    regime[(natr_pct >= 30) & (natr_pct <= 70)] = 2.0
    regime[natr_pct > 70] = 3.0
    result["vol_regime_3"] = regime

    # ---- 波动率的波动率（natr_14 的 20 日 std） ----
    result["vol_of_vol_20"] = natr_14.rolling(20, min_periods=5).std()

    # ---- 窗口内振幅占比 ----
    atr_14 = atr(h, l, c, 14)
    for w in _RANGE_RATIO_WINDOWS:
        hi = h.rolling(w, min_periods=max(3, w // 3)).max()
        lo = l.rolling(w, min_periods=max(3, w // 3)).min()
        result[f"range_ratio_{w}"] = (hi - lo) / c.replace(0, np.nan)

    # ---- 窗口振幅 / ATR ----
    for w in _RANGE_ATR_WINDOWS:
        hi = h.rolling(w, min_periods=max(2, w // 3)).max()
        lo = l.rolling(w, min_periods=max(2, w // 3)).min()
        result[f"range_height_atr_{w}"] = (hi - lo) / atr_14.replace(0, np.nan)

    # ---- 连续 HH / LL 计数 ----
    hh_flag = (h > h.shift(1)).astype(int)
    ll_flag = (l < l.shift(1)).astype(int)
    for w in _CONSEC_WINDOWS:
        result[f"consecutive_hh_{w}"] = _consecutive_flag_count(hh_flag, w)
        result[f"consecutive_ll_{w}"] = _consecutive_flag_count(ll_flag, w)

    # ---- close zscore ----
    for w in _ZCLOSE_WINDOWS:
        result[f"zscore_close_{w}"] = _rolling_zscore(c, w)

    # ---- 突破距 ATR ----
    for w in _BREAK_DIST_WINDOWS:
        dc = donchian_channel(h, l, w)
        atr_safe = atr_14.replace(0, np.nan)
        result[f"breakout_dist_atr_{w}"] = (c - dc["dc_upper"]) / atr_safe
        result[f"breakout_dist_atr_down_{w}"] = (c - dc["dc_lower"]) / atr_safe

    return result
