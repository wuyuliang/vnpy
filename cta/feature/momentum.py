"""
动量类特征

包含：
    - RSI (相对强弱指数)
    - ROC / ROCMA (变化率)
    - Williams %R
    - CCI (商品通道指数)
    - Stochastic KDJ
    - 动量 (Momentum)
    - TSI (True Strength Index)
    - Ultimate Oscillator
    - 收益率序列
"""
import numpy as np
import pandas as pd


# ============================================================
# RSI
# ============================================================

def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """相对强弱指数"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ============================================================
# ROC
# ============================================================

def roc(close: pd.Series, window: int) -> pd.Series:
    """变化率 (%)"""
    return close.pct_change(window) * 100


def roc_ma(close: pd.Series, roc_window: int = 12, ma_window: int = 6
           ) -> pd.Series:
    """ROC 的移动平均"""
    r = roc(close, roc_window)
    return r.rolling(ma_window).mean()


# ============================================================
# Williams %R
# ============================================================

def williams_r(high: pd.Series, low: pd.Series, close: pd.Series,
               window: int = 14) -> pd.Series:
    """威廉指标 %R，范围 [-100, 0]"""
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    return -100 * (hh - close) / (hh - ll).replace(0, np.nan)


# ============================================================
# CCI
# ============================================================

def cci(high: pd.Series, low: pd.Series, close: pd.Series,
        window: int = 20) -> pd.Series:
    """商品通道指数"""
    tp = (high + low + close) / 3
    ma = tp.rolling(window).mean()
    md = tp.rolling(window).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * md).replace(0, np.nan)


# ============================================================
# KDJ (Stochastic)
# ============================================================

def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
        k_window: int = 9, d_window: int = 3
        ) -> pd.DataFrame:
    """
    KDJ 随机指标
    返回: DataFrame(stoch_k, stoch_d, stoch_j)
    """
    hh = high.rolling(k_window).max()
    ll = low.rolling(k_window).min()
    rsv = (close - ll) / (hh - ll).replace(0, np.nan) * 100

    k = rsv.ewm(com=d_window - 1, adjust=False).mean()
    d = k.ewm(com=d_window - 1, adjust=False).mean()
    j = 3 * k - 2 * d

    return pd.DataFrame({"stoch_k": k, "stoch_d": d, "stoch_j": j})


# ============================================================
# Momentum
# ============================================================

def momentum(close: pd.Series, window: int) -> pd.Series:
    """动量 = 当前价 - N日前价"""
    return close - close.shift(window)


# ============================================================
# TSI (True Strength Index)
# ============================================================

def tsi(close: pd.Series, slow: int = 25, fast: int = 13) -> pd.Series:
    """真实强度指数"""
    diff = close.diff()
    double_smooth = diff.ewm(span=slow, adjust=False).mean().ewm(
        span=fast, adjust=False
    ).mean()
    double_smooth_abs = diff.abs().ewm(span=slow, adjust=False).mean().ewm(
        span=fast, adjust=False
    ).mean()
    return 100 * double_smooth / double_smooth_abs.replace(0, np.nan)


# ============================================================
# Ultimate Oscillator
# ============================================================

def ultimate_oscillator(high: pd.Series, low: pd.Series, close: pd.Series,
                        s: int = 7, m: int = 14, l: int = 28) -> pd.Series:
    """终极震荡指标"""
    prev_close = close.shift(1)
    bp = close - pd.concat([low, prev_close], axis=1).min(axis=1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    avg_s = bp.rolling(s).sum() / tr.rolling(s).sum()
    avg_m = bp.rolling(m).sum() / tr.rolling(m).sum()
    avg_l = bp.rolling(l).sum() / tr.rolling(l).sum()

    return 100 * (4 * avg_s + 2 * avg_m + avg_l) / 7


# ============================================================
# 收益率
# ============================================================

def returns(close: pd.Series, window: int = 1) -> pd.Series:
    """对数收益率"""
    return np.log(close / close.shift(window))


def returns_pct(close: pd.Series, window: int = 1) -> pd.Series:
    """百分比收益率"""
    return close.pct_change(window)


# ============================================================
# 批量生成动量类特征
# ============================================================

def compute_momentum_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种数据批量计算动量类特征
    输入 df 需包含: close, high, low
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    result = pd.DataFrame(index=df.index)

    # RSI
    for w in [3, 6, 14, 24]:
        result[f"rsi_{w}"] = rsi(close, w)

    # ROC
    for w in [3, 5, 10, 20]:
        result[f"roc_{w}"] = roc(close, w)
    result["roc_ma_12_6"] = roc_ma(close, 12, 6)

    # Williams %R
    result["williams_r_14"] = williams_r(high, low, close, 14)

    # CCI
    for w in [3, 14, 20]:
        result[f"cci_{w}"] = cci(high, low, close, w)

    # KDJ
    kdj_df = kdj(high, low, close)
    result = pd.concat([result, kdj_df], axis=1)

    # 动量
    for w in [3, 5, 10, 20]:
        result[f"momentum_{w}"] = momentum(close, w)

    # TSI
    result["tsi"] = tsi(close)

    # Ultimate Oscillator
    result["ultimate_osc"] = ultimate_oscillator(high, low, close)

    # 收益率
    for w in [1, 3, 5, 10, 20]:
        result[f"log_ret_{w}"] = returns(close, w)
        result[f"pct_ret_{w}"] = returns_pct(close, w)

    return result
