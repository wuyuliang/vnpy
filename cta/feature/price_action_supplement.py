"""
§14 Al Brooks 形态补充特征 / Price Action Supplement

在 price_action.py 180+ 原语之上，追加 tight range / bull-flag / bear-flag / bpb /
swing 索引 / trend channel 等 Brooks 常用但未直接实现的形态。
全部 bar-derivable，对应 FEATURES.md §14。

输出列：
    - pa_tight_range_flag_{5,8,10}
    - pa_bull_flag_flag
    - pa_bear_flag_flag
    - pa_bpb_flag
    - pa_swing_high_idx
    - pa_swing_low_idx
    - pa_trend_channel_top
    - pa_trend_channel_bot
    - pa_trend_channel_slope
    - pa_channel_width_atr
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.volatility import atr
from cta.feature.price_action import (
    swing_high, swing_low,
    is_trend_bar,
    breakout_up, breakout_down,
    bar_body_relative,
)


_TIGHT_RANGE_WINDOWS: tuple[int, ...] = (5, 8, 10)

# swing 窗口 N=3：左右各 3 根
_SWING_LEFT = 3
_SWING_RIGHT = 3


def _tight_range_flag(high: pd.Series, low: pd.Series, close: pd.Series,
                      atr_14: pd.Series, window: int) -> pd.Series:
    """
    近 window bar 内 tight-range：窗口振幅 / ATR < 1.5 且收盘相对中位偏离 < 0.2
    """
    hi = high.rolling(window, min_periods=window).max()
    lo = low.rolling(window, min_periods=window).min()
    atr_safe = atr_14.replace(0, np.nan)
    height_atr = (hi - lo) / atr_safe
    mid = (hi + lo) / 2
    span = (hi - lo).replace(0, np.nan)
    bias = ((close - mid) / span).abs()
    flag = ((height_atr < 1.5) & (bias < 0.2)).astype(int)
    # 窗口内样本不足时给 0
    flag[hi.isna() | lo.isna() | atr_safe.isna()] = 0
    return flag


def _bull_flag(df: pd.DataFrame, atr_14: pd.Series,
               legs: int = 5, pullback: int = 3) -> pd.Series:
    """
    多头旗形：
      前导 legs 根（约 5 根）趋势 K 中多头占优，累积涨幅 > atr_14；
      紧接着 >= pullback 根（约 3 根）紧缩回调：bar_range 收窄 + 回吐 < 50% 前导涨幅。
    返回 0/1 flag。
    """
    o = df["open"]
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]

    trend = is_trend_bar(o, c, h, l)  # +1/0/-1
    bull_cnt = (trend == 1).astype(int).rolling(legs).sum()
    bear_cnt = (trend == -1).astype(int).rolling(legs).sum()
    # 前导段在 [t-pullback-legs+1, t-pullback] 区间
    bull_cnt_lead = bull_cnt.shift(pullback)
    bear_cnt_lead = bear_cnt.shift(pullback)

    lead_start_close = c.shift(legs + pullback - 1)
    lead_end_close = c.shift(pullback)
    lead_gain = lead_end_close - lead_start_close
    atr_lead = atr_14.shift(pullback)

    # 回调段：当前到 pullback 根以内，累计回吐与前导涨幅
    pull_low = l.rolling(pullback).min()
    pull_range_avg = (h - l).rolling(pullback).mean()
    lead_range_avg = (h - l).shift(pullback).rolling(legs).mean()

    cond_lead = (
        (bull_cnt_lead >= legs * 0.5) &
        (bull_cnt_lead > bear_cnt_lead) &
        (lead_gain > atr_lead)
    )
    cond_pullback_shallow = (lead_end_close - pull_low) < 0.5 * lead_gain
    cond_tight = pull_range_avg < lead_range_avg
    flag = (cond_lead & cond_pullback_shallow & cond_tight).astype(int)
    return flag.fillna(0).astype(int)


def _bear_flag(df: pd.DataFrame, atr_14: pd.Series,
               legs: int = 5, pullback: int = 3) -> pd.Series:
    """空头旗形：对称 bull flag。"""
    o = df["open"]
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]

    trend = is_trend_bar(o, c, h, l)
    bull_cnt = (trend == 1).astype(int).rolling(legs).sum()
    bear_cnt = (trend == -1).astype(int).rolling(legs).sum()
    bull_cnt_lead = bull_cnt.shift(pullback)
    bear_cnt_lead = bear_cnt.shift(pullback)

    lead_start_close = c.shift(legs + pullback - 1)
    lead_end_close = c.shift(pullback)
    lead_drop = lead_start_close - lead_end_close
    atr_lead = atr_14.shift(pullback)

    pull_high = h.rolling(pullback).max()
    pull_range_avg = (h - l).rolling(pullback).mean()
    lead_range_avg = (h - l).shift(pullback).rolling(legs).mean()

    cond_lead = (
        (bear_cnt_lead >= legs * 0.5) &
        (bear_cnt_lead > bull_cnt_lead) &
        (lead_drop > atr_lead)
    )
    cond_pullback_shallow = (pull_high - lead_end_close) < 0.5 * lead_drop
    cond_tight = pull_range_avg < lead_range_avg
    flag = (cond_lead & cond_pullback_shallow & cond_tight).astype(int)
    return flag.fillna(0).astype(int)


def _bpb_flag(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    Breakout-Pullback-Continuation:
      [t-5..t-2] 内发生向上突破 dc_upper_window；
      [t-2..t-1] 有回撤但 close 未跌破突破参考位；
      t 收阳且 close > t-1 close。
    （空头对称取反再合并为同一 flag，区分方向留给 regime / trend_score。）
    """
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]
    o = df["open"]

    bu = breakout_up(h, window).astype(int)
    bd = breakout_down(l, window).astype(int)
    # 过去 5 根出现过突破
    bu_recent = bu.rolling(5).max().shift(1).fillna(0)
    bd_recent = bd.rolling(5).max().shift(1).fillna(0)

    # 回测：最近 3 根内低点接近但未跌破前 20 日前高
    ref_high = h.rolling(window).max().shift(1)
    pull_low = l.rolling(3).min()
    cond_pb_up = (pull_low <= ref_high * 1.002) & (pull_low >= ref_high * 0.99)

    ref_low = l.rolling(window).min().shift(1)
    pull_high = h.rolling(3).max()
    cond_pb_dn = (pull_high >= ref_low * 0.998) & (pull_high <= ref_low * 1.01)

    cont_up = (c > o) & (c > c.shift(1))
    cont_dn = (c < o) & (c < c.shift(1))

    flag_up = (bu_recent.astype(bool) & cond_pb_up & cont_up)
    flag_dn = (bd_recent.astype(bool) & cond_pb_dn & cont_dn)
    return (flag_up | flag_dn).fillna(False).astype(int)


def _swing_idx(swing_series: pd.Series) -> pd.Series:
    """
    最近一个已确认 swing 与当前 bar 的相对索引距离：
      swing_series 来自 price_action.swing_high/low（非 swing 位为 NaN）。
      返回 i - last_swing_i，swing 未确认位返回 NaN。
    """
    positions = np.arange(len(swing_series))
    swing_pos = np.where(~swing_series.isna().values, positions, np.nan)
    swing_pos_ffill = pd.Series(swing_pos, index=swing_series.index).ffill()
    dist = pd.Series(positions, index=swing_series.index) - swing_pos_ffill
    return dist


def _trend_channel(df: pd.DataFrame, atr_14: pd.Series,
                   n_swings: int = 3) -> pd.DataFrame:
    """
    以最近 n_swings 个确认 swing high / swing low 为端点，做线性拟合得到
    通道上沿 / 下沿 / 斜率 / 宽度 /atr。
    实现方式（避免逐行拟合的 O(n²)）：
      - 对每 bar 取最近 `n_swings*3`-bar 窗口内的 swing 点，拟合 y = a*i + b
      - 缺点：swing 稀疏时窗口可能不够，此时返回 NaN
    """
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]

    sh = swing_high(h, _SWING_LEFT, _SWING_RIGHT)
    sl = swing_low(l, _SWING_LEFT, _SWING_RIGHT)

    # swing 点按位置升序列出（pos, val）
    sh_val = sh.values
    sl_val = sl.values
    sh_pts: list[tuple[int, float]] = [
        (i, float(v)) for i, v in enumerate(sh_val) if not np.isnan(v)
    ]
    sl_pts: list[tuple[int, float]] = [
        (i, float(v)) for i, v in enumerate(sl_val) if not np.isnan(v)
    ]

    n = len(h)
    top = np.full(n, np.nan)
    bot = np.full(n, np.nan)
    slope = np.full(n, np.nan)

    ptr_h = 0  # 指向 sh_pts 中下一个尚未纳入的位置
    ptr_l = 0
    buf_h: list[tuple[int, float]] = []
    buf_l: list[tuple[int, float]] = []

    for i in range(n):
        cutoff = i - _SWING_RIGHT
        while ptr_h < len(sh_pts) and sh_pts[ptr_h][0] <= cutoff:
            buf_h.append(sh_pts[ptr_h])
            if len(buf_h) > n_swings:
                buf_h.pop(0)
            ptr_h += 1
        while ptr_l < len(sl_pts) and sl_pts[ptr_l][0] <= cutoff:
            buf_l.append(sl_pts[ptr_l])
            if len(buf_l) > n_swings:
                buf_l.pop(0)
            ptr_l += 1

        if len(buf_h) < 2 or len(buf_l) < 2:
            continue
        hx = np.fromiter((p[0] for p in buf_h), dtype=float, count=len(buf_h))
        hy = np.fromiter((p[1] for p in buf_h), dtype=float, count=len(buf_h))
        lx = np.fromiter((p[0] for p in buf_l), dtype=float, count=len(buf_l))
        ly = np.fromiter((p[1] for p in buf_l), dtype=float, count=len(buf_l))
        a_top, b_top = np.polyfit(hx, hy, 1)
        a_bot, b_bot = np.polyfit(lx, ly, 1)
        top[i] = a_top * i + b_top
        bot[i] = a_bot * i + b_bot
        slope[i] = (a_top + a_bot) / 2

    result = pd.DataFrame(index=df.index)
    result["pa_trend_channel_top"] = top
    result["pa_trend_channel_bot"] = bot
    result["pa_trend_channel_slope"] = slope
    atr_safe = atr_14.replace(0, np.nan)
    result["pa_channel_width_atr"] = (result["pa_trend_channel_top"] -
                                      result["pa_trend_channel_bot"]) / atr_safe
    return result


def compute_price_action_supplement_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    输入 df 需包含: open, high, low, close
    返回 DataFrame（index 与 df 对齐）
    """
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]

    atr_14 = atr(h, l, c, 14)
    result = pd.DataFrame(index=df.index)

    # ---- tight range 旗标 ----
    for w in _TIGHT_RANGE_WINDOWS:
        result[f"pa_tight_range_flag_{w}"] = _tight_range_flag(h, l, c, atr_14, w)

    # ---- bull / bear flag ----
    result["pa_bull_flag_flag"] = _bull_flag(df, atr_14)
    result["pa_bear_flag_flag"] = _bear_flag(df, atr_14)

    # ---- breakout-pullback-continuation ----
    result["pa_bpb_flag"] = _bpb_flag(df, window=20)

    # ---- swing 索引距离 ----
    sh = swing_high(h, _SWING_LEFT, _SWING_RIGHT)
    sl = swing_low(l, _SWING_LEFT, _SWING_RIGHT)
    result["pa_swing_high_idx"] = _swing_idx(sh)
    result["pa_swing_low_idx"] = _swing_idx(sl)

    # ---- trend channel 三件套 ----
    tc = _trend_channel(df, atr_14, n_swings=3)
    result = pd.concat([result, tc], axis=1)

    return result
