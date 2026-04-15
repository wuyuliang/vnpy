"""
波动率类特征

包含：
    - ATR (真实波幅)
    - 布林带 (Bollinger Bands)
    - 历史波动率 (Historical Volatility)
    - Keltner Channel
    - Donchian Channel
    - 波动率比率
    - 价格振幅
    - Garman-Klass 波动率
    - Parkinson 波动率
"""
import numpy as np
import pandas as pd


# ============================================================
# ATR
# ============================================================

def true_range(high: pd.Series, low: pd.Series, close: pd.Series
               ) -> pd.Series:
    """真实波幅"""
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        window: int = 14) -> pd.Series:
    """平均真实波幅"""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / window, adjust=False).mean()


def natr(high: pd.Series, low: pd.Series, close: pd.Series,
         window: int = 14) -> pd.Series:
    """标准化 ATR（ATR / close * 100）"""
    return atr(high, low, close, window) / close.replace(0, np.nan) * 100


# ============================================================
# 布林带
# ============================================================

def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0
                    ) -> pd.DataFrame:
    """
    布林带
    返回: DataFrame(bb_mid, bb_upper, bb_lower, bb_width, bb_pctb)
    """
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0, np.nan) * 100
    pctb = (close - lower) / (upper - lower).replace(0, np.nan)

    return pd.DataFrame({
        "bb_mid": mid,
        "bb_upper": upper,
        "bb_lower": lower,
        "bb_width": width,
        "bb_pctb": pctb,
    })


# ============================================================
# 历史波动率
# ============================================================

def hist_volatility(close: pd.Series, window: int = 20,
                    annualize: int = 252) -> pd.Series:
    """历史波动率（年化）"""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window).std() * np.sqrt(annualize)


def realized_variance(close: pd.Series, window: int = 20) -> pd.Series:
    """已实现方差"""
    log_ret = np.log(close / close.shift(1))
    return (log_ret ** 2).rolling(window).mean()


# ============================================================
# Keltner Channel
# ============================================================

def keltner_channel(high: pd.Series, low: pd.Series, close: pd.Series,
                    ema_window: int = 20, atr_window: int = 10,
                    multiplier: float = 1.5) -> pd.DataFrame:
    """
    Keltner 通道
    返回: DataFrame(kc_mid, kc_upper, kc_lower)
    """
    mid = close.ewm(span=ema_window, adjust=False).mean()
    atr_val = atr(high, low, close, atr_window)
    upper = mid + multiplier * atr_val
    lower = mid - multiplier * atr_val
    return pd.DataFrame({
        "kc_mid": mid,
        "kc_upper": upper,
        "kc_lower": lower,
    })


# ============================================================
# Donchian Channel
# ============================================================

def donchian_channel(high: pd.Series, low: pd.Series, window: int = 20
                     ) -> pd.DataFrame:
    """
    唐奇安通道
    返回: DataFrame(dc_upper, dc_lower, dc_mid)
    """
    upper = high.rolling(window).max()
    lower = low.rolling(window).min()
    mid = (upper + lower) / 2
    return pd.DataFrame({
        "dc_upper": upper,
        "dc_lower": lower,
        "dc_mid": mid,
    })


# ============================================================
# 波动率比率
# ============================================================

def volatility_ratio(close: pd.Series, short: int = 5, long: int = 20
                     ) -> pd.Series:
    """短期波动率 / 长期波动率"""
    log_ret = np.log(close / close.shift(1))
    vol_short = log_ret.rolling(short).std()
    vol_long = log_ret.rolling(long).std()
    return vol_short / vol_long.replace(0, np.nan)


# ============================================================
# 价格振幅
# ============================================================

def intraday_range(high: pd.Series, low: pd.Series, close: pd.Series
                   ) -> pd.Series:
    """日内振幅 (%)"""
    return (high - low) / close * 100


def avg_range(high: pd.Series, low: pd.Series, close: pd.Series,
              window: int = 20) -> pd.Series:
    """平均日内振幅"""
    return intraday_range(high, low, close).rolling(window).mean()


# ============================================================
# Garman-Klass 波动率
# ============================================================

def garman_klass_vol(open_: pd.Series, high: pd.Series, low: pd.Series,
                     close: pd.Series, window: int = 20) -> pd.Series:
    """
    Garman-Klass 波动率估计
    利用 OHLC 四价获得更高效的波动率估计
    """
    log_hl = np.log(high / low) ** 2
    log_co = np.log(close / open_) ** 2
    gk = 0.5 * log_hl - (2 * np.log(2) - 1) * log_co
    return np.sqrt((gk.rolling(window).mean() * 252).clip(lower=0))


# ============================================================
# Parkinson 波动率
# ============================================================

def parkinson_vol(high: pd.Series, low: pd.Series,
                  window: int = 20) -> pd.Series:
    """Parkinson 波动率（仅用 High/Low）"""
    log_hl = np.log(high / low) ** 2
    return np.sqrt(log_hl.rolling(window).mean() / (4 * np.log(2)) * 252)


# ============================================================
# 批量生成波动率类特征
# ============================================================

def compute_volatility_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种数据批量计算波动率类特征
    输入 df 需包含: open, high, low, close
    """
    o = df["open"]
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]
    result = pd.DataFrame(index=df.index)

    # ATR
    for w in [3, 5, 14, 20]:
        result[f"atr_{w}"] = atr(h, l, c, w)
    result["natr_14"] = natr(h, l, c, 14)

    # 布林带
    bb = bollinger_bands(c, 20, 2.0)
    result = pd.concat([result, bb], axis=1)

    # 历史波动率
    for w in [3, 10, 20, 60]:
        result[f"hist_vol_{w}"] = hist_volatility(c, w)
    result["realized_var_20"] = realized_variance(c, 20)

    # Keltner Channel
    kc = keltner_channel(h, l, c)
    result = pd.concat([result, kc], axis=1)

    # Donchian Channel
    for w in [3, 10, 20]:
        dc = donchian_channel(h, l, w)
        dc.columns = [f"{col}_{w}" for col in dc.columns]
        result = pd.concat([result, dc], axis=1)

    # 波动率比率
    result["vol_ratio_5_20"] = volatility_ratio(c, 5, 20)
    result["vol_ratio_10_60"] = volatility_ratio(c, 10, 60)

    # 振幅
    result["intraday_range"] = intraday_range(h, l, c)
    result["avg_range_20"] = avg_range(h, l, c, 20)

    # Garman-Klass & Parkinson
    result["gk_vol_20"] = garman_klass_vol(o, h, l, c, 20)
    result["parkinson_vol_20"] = parkinson_vol(h, l, 20)

    return result
