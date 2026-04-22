"""
§18 入场/止损建议价 / Entry & Stop Hints

为每根 bar 输出"假设此刻入场"的建议止损 / 止盈价位（不依赖真实持仓）。
bar-derivable，所有价格列均为绝对价格（非百分比）。

输出列（long / short 成对）：
    - atr_based_stop_{long,short}          (k=1.5)
    - atr_based_target_{long,short}        (k=3.0)
    - chandelier_stop_{long,short}         (22 日极值 +/- 3 × ATR)
    - micro_channel_stop_{long,short}      (最近 5 根极值)
    - liquidity_filter_pass                (是否满足 ADV 过滤)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.volatility import atr


_STOP_K = 1.5
_TARGET_K = 3.0
_CHANDELIER_N = 22
_CHANDELIER_ATR_MULT = 3.0
_MICRO_N = 5
# ADV 过滤阈值：20 日均量 × 收盘价 × 合约乘数（此处不知乘数，用 1 近似；策略
# 可用 liquidity_filter_pass 再结合自己维护的 multiplier 重算）
_ADV_MULTIPLIER = 1.0
_ADV_THRESHOLD = 5e7  # 仅作 bar-derivable 版本的默认阈值


def compute_entry_stop_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    df 需包含: high, low, close, volume
    返回 DataFrame（index 与 df 对齐）
    """
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]
    v = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)

    atr_14 = atr(h, l, c, 14)

    result = pd.DataFrame(index=df.index)

    # ATR-based stop / target
    result["atr_based_stop_long"] = c - _STOP_K * atr_14
    result["atr_based_stop_short"] = c + _STOP_K * atr_14
    result["atr_based_target_long"] = c + _TARGET_K * atr_14
    result["atr_based_target_short"] = c - _TARGET_K * atr_14

    # Chandelier stop：长多=rolling high - 3*ATR，长空=rolling low + 3*ATR
    roll_hi = h.rolling(_CHANDELIER_N, min_periods=max(5, _CHANDELIER_N // 2)).max()
    roll_lo = l.rolling(_CHANDELIER_N, min_periods=max(5, _CHANDELIER_N // 2)).min()
    result["chandelier_stop_long"] = roll_hi - _CHANDELIER_ATR_MULT * atr_14
    result["chandelier_stop_short"] = roll_lo + _CHANDELIER_ATR_MULT * atr_14

    # Micro-channel stop：最近 N 根的极值（包含当前 bar）
    result["micro_channel_stop_long"] = l.rolling(
        _MICRO_N, min_periods=1
    ).min()
    result["micro_channel_stop_short"] = h.rolling(
        _MICRO_N, min_periods=1
    ).max()

    # Liquidity filter：20 日均量 × close × multiplier ≥ threshold
    vol_ma_20 = v.rolling(20, min_periods=5).mean()
    adv = (vol_ma_20 * c * _ADV_MULTIPLIER).fillna(0)
    result["liquidity_filter_pass"] = (adv >= _ADV_THRESHOLD).astype(int)

    return result
