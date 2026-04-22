"""
§15 多周期对齐特征 / Multi-Timeframe Alignment

为每根 bar 附上"日线 / 60m / 30m / 5m 视角下的趋势评分"，实现 Brooks 风格的
三级共振（HTF 方向 → MTF 确认 → LTF 触发）。

实现策略：以当前 bar 的频率为"时钟"，通过内部 resample 聚合出 HTF/MTF bar，
在各自频率上算 `trend_strength_score / 100 ∈ [-1, 1]`，然后以 as-of 方式左连接
回原始 bar 上。5m 信号检测用 `bull_reversal_bar / bear_reversal_bar`。

输出列：
    - htf_trend_score_day
    - htf_bias             (-1 / 0 / +1，基于 htf_trend_score_day 的死区切分)
    - mtf_trend_score_60m
    - mtf_trend_score_30m
    - mtf_align_flag
    - ltf_signal_ready_5m
    - mtf_conflict_score
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.price_action import (
    trend_strength_score,
    bull_reversal_bar,
    bear_reversal_bar,
)


def _trade_date(dt: pd.Series) -> pd.Series:
    """夜盘（>=21:00）归属下一个自然日，近似交易日。"""
    date = pd.to_datetime(dt.dt.date)
    is_night = dt.dt.hour >= 21
    out = date.copy()
    out.loc[is_night] = out.loc[is_night] + pd.Timedelta(days=1)
    return out


def _resample_key(dt: pd.Series, rule: str) -> pd.Series:
    """
    把 datetime 映射到聚合键：
      rule='D'    -> 交易日（夜盘归次日）
      rule='60T'  -> 60 分钟 floor
      rule='30T'  -> 30 分钟 floor
      rule='5T'   -> 5 分钟 floor
    """
    if rule == "D":
        return _trade_date(dt)
    minutes = int(rule.rstrip("TMmin"))
    return dt.dt.floor(f"{minutes}min")


def _resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """按 rule 聚合 OHLC，返回以聚合键为 index 的 DataFrame。"""
    key = _resample_key(df["datetime"], rule)
    agg = df.groupby(key, sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    agg.index.name = "_key"
    return agg


def _trend_score_on(df_htf: pd.DataFrame, window: int = 20) -> pd.Series:
    """在 HTF bar 上算 trend_strength_score 并归一到 [-1, 1]。"""
    score = trend_strength_score(
        df_htf["open"], df_htf["high"], df_htf["low"], df_htf["close"], window
    ) / 100.0
    return score.clip(-1, 1)


def _map_back(df: pd.DataFrame, htf_series: pd.Series, rule: str) -> pd.Series:
    """
    把 HTF bar 的分数映射回 df 的每根 bar：
      当前 bar 的 key -> 查找 htf_series[key]；若落在最后一根未完成 HTF 上，
      用 shift(1) 避免未来信息泄漏。
    """
    key = _resample_key(df["datetime"], rule)
    # 为防止使用未完成的当前 HTF bar，统一取上一根已完成的 HTF bar 分数
    shifted = htf_series.shift(1)
    return key.map(shifted).astype(float)


def _ltf_signal_ready(df: pd.DataFrame, rule: str = "5T") -> pd.Series:
    """
    在 5 分钟 bar 上检测 `bull_reversal_bar / bear_reversal_bar`；
    合并为 signal_ready = (bull_rev | bear_rev) ∈ {0, 1}。
    映射回原始 bar：当前 bar 所在 5m 区间的上一完成 5m bar 的信号。
    """
    df5 = _resample_ohlc(df, rule)
    bull = bull_reversal_bar(df5["open"], df5["high"], df5["low"], df5["close"])
    bear = bear_reversal_bar(df5["open"], df5["high"], df5["low"], df5["close"])
    signal = (bull.astype(int) | bear.astype(int)).astype(float)

    key = _resample_key(df["datetime"], rule)
    return key.map(signal.shift(1)).fillna(0).astype(float)


def compute_multi_timeframe_features(df: pd.DataFrame,
                                     interval: str = "day") -> pd.DataFrame:
    """
    df 需包含: datetime, open, high, low, close
    interval: day / minute / minute5 / minute15 / minute30 / minute60
    返回 DataFrame（index 与 df 对齐）
    """
    result = pd.DataFrame(index=df.index)

    if "datetime" not in df.columns:
        return result

    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])

    if interval == "day":
        # day 自身就是 HTF；mtf_60 / mtf_30 无更细粒度可聚合，用日级分数代替并标注 conflict=0
        htf = _trend_score_on(_resample_ohlc(df, "D"), 20)
        htf_mapped = _map_back(df, htf, "D")
        result["htf_trend_score_day"] = htf_mapped
        result["mtf_trend_score_60m"] = htf_mapped
        result["mtf_trend_score_30m"] = htf_mapped
        result["ltf_signal_ready_5m"] = 0.0
    else:
        htf_day = _trend_score_on(_resample_ohlc(df, "D"), 20)
        mtf_60 = _trend_score_on(_resample_ohlc(df, "60T"), 20)
        mtf_30 = _trend_score_on(_resample_ohlc(df, "30T"), 20)
        result["htf_trend_score_day"] = _map_back(df, htf_day, "D")
        result["mtf_trend_score_60m"] = _map_back(df, mtf_60, "60T")
        result["mtf_trend_score_30m"] = _map_back(df, mtf_30, "30T")
        # 5m 信号只在 ≤5m 频率下有意义；更高频率则取聚合后的 5m 视图
        if interval in ("minute", "minute5"):
            result["ltf_signal_ready_5m"] = _ltf_signal_ready(df, "5T")
        else:
            # minute15/30/60 的 bar 本身已 >= 5m，跳过 LTF 检测
            result["ltf_signal_ready_5m"] = 0.0

    # ---- htf_bias：死区 |x| < 0.2 视为中性 ----
    htf_val = result["htf_trend_score_day"]
    bias = pd.Series(0.0, index=df.index)
    bias[htf_val >= 0.2] = 1.0
    bias[htf_val <= -0.2] = -1.0
    result["htf_bias"] = bias

    # ---- align flag: HTF / MTF_60 方向一致且幅度 ≥ 0.3 ----
    htf_s = result["htf_trend_score_day"]
    mtf_s = result["mtf_trend_score_60m"]
    align = (
        (np.sign(htf_s) == np.sign(mtf_s)) &
        (htf_s.abs() >= 0.3) &
        (mtf_s.abs() >= 0.3)
    )
    result["mtf_align_flag"] = align.astype(int)

    # ---- conflict score: 三个分数的 std（越高越分歧） ----
    tri = pd.concat(
        [result["htf_trend_score_day"],
         result["mtf_trend_score_60m"],
         result["mtf_trend_score_30m"]],
        axis=1,
    )
    result["mtf_conflict_score"] = tri.std(axis=1, ddof=0)

    return result
