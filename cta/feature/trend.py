"""
趋势类特征

包含：
    - SMA / EMA / WMA / DEMA / TEMA 多周期均线
    - MACD (DIF, DEA, MACD柱)
    - ADX / +DI / -DI (趋势强度)
    - Aroon 指标 (Aroon Up / Down / Oscillator)
    - 均线多头/空头排列
    - 价格与均线偏离度
    - 线性回归斜率
    - SuperTrend
"""
import numpy as np
import pandas as pd


# ============================================================
# 均线系列
# ============================================================

def sma(series: pd.Series, window: int) -> pd.Series:
    """简单移动平均"""
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=span, adjust=False).mean()


def wma(series: pd.Series, window: int) -> pd.Series:
    """加权移动平均"""
    weights = np.arange(1, window + 1, dtype=float)
    return series.rolling(window).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )


def dema(series: pd.Series, span: int) -> pd.Series:
    """双重指数移动平均"""
    e1 = ema(series, span)
    e2 = ema(e1, span)
    return 2 * e1 - e2


def tema(series: pd.Series, span: int) -> pd.Series:
    """三重指数移动平均"""
    e1 = ema(series, span)
    e2 = ema(e1, span)
    e3 = ema(e2, span)
    return 3 * e1 - 3 * e2 + e3


# ============================================================
# MACD
# ============================================================

def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> pd.DataFrame:
    """
    MACD 指标
    返回: DataFrame(dif, dea, macd_hist)
    """
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    hist = 2 * (dif - dea)
    return pd.DataFrame({
        "macd_dif": dif,
        "macd_dea": dea,
        "macd_hist": hist,
    })


# ============================================================
# ADX / DI
# ============================================================

def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
        ) -> pd.DataFrame:
    """
    ADX 与 +DI / -DI
    返回: DataFrame(plus_di, minus_di, adx)
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # +DM / -DM
    plus_dm = np.where(
        (high - prev_high) > (prev_low - low),
        np.maximum(high - prev_high, 0),
        0.0,
    )
    minus_dm = np.where(
        (prev_low - low) > (high - prev_high),
        np.maximum(prev_low - low, 0),
        0.0,
    )

    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    atr = tr.ewm(alpha=1 / window, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / window, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / window, adjust=False).mean() / atr

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_val = dx.ewm(alpha=1 / window, adjust=False).mean()

    return pd.DataFrame({
        "plus_di": plus_di,
        "minus_di": minus_di,
        "adx": adx_val,
    })


# ============================================================
# Aroon
# ============================================================

def aroon(high: pd.Series, low: pd.Series, window: int = 25) -> pd.DataFrame:
    """
    Aroon 指标
    返回: DataFrame(aroon_up, aroon_down, aroon_osc)
    """
    aroon_up = high.rolling(window + 1).apply(
        lambda x: x.argmax() / window * 100, raw=True
    )
    aroon_down = low.rolling(window + 1).apply(
        lambda x: x.argmin() / window * 100, raw=True
    )
    return pd.DataFrame({
        "aroon_up": aroon_up,
        "aroon_down": aroon_down,
        "aroon_osc": aroon_up - aroon_down,
    })


# ============================================================
# 均线排列 & 偏离
# ============================================================

def ma_alignment(close: pd.Series,
                 periods: list[int] | None = None) -> pd.Series:
    """
    均线多头/空头排列信号
    返回: 1=多头排列(短>中>长), -1=空头排列, 0=混合
    """
    if periods is None:
        periods = [5, 10, 20, 60]
    mas = [sma(close, p) for p in sorted(periods)]
    bullish = pd.Series(True, index=close.index)
    bearish = pd.Series(True, index=close.index)
    for i in range(len(mas) - 1):
        bullish &= mas[i] > mas[i + 1]
        bearish &= mas[i] < mas[i + 1]
    result = pd.Series(0, index=close.index, dtype=int)
    result[bullish] = 1
    result[bearish] = -1
    return result


def ma_bias(close: pd.Series, window: int) -> pd.Series:
    """价格与均线的偏离度 (BIAS)，百分比"""
    ma = sma(close, window)
    return (close - ma) / ma.replace(0, np.nan) * 100


# ============================================================
# 线性回归斜率
# ============================================================

def linear_slope(series: pd.Series, window: int) -> pd.Series:
    """滚动线性回归斜率（标准化为每日变化率）"""
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(y: np.ndarray) -> float:
        y_mean = y.mean()
        return np.sum((x - x_mean) * (y - y_mean)) / x_var

    return series.rolling(window).apply(_slope, raw=True)


# ============================================================
# SuperTrend
# ============================================================

def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
               atr_period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    SuperTrend 指标
    返回: DataFrame(supertrend, direction)
        direction: 1=上升趋势, -1=下降趋势
    """
    hl2 = (high + low) / 2
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_val = tr.ewm(alpha=1 / atr_period, adjust=False).mean()

    upper_band = hl2 + multiplier * atr_val
    lower_band = hl2 - multiplier * atr_val

    st = pd.Series(np.nan, index=close.index)
    direction = pd.Series(1, index=close.index)

    for i in range(1, len(close)):
        if close.iloc[i] > upper_band.iloc[i - 1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < lower_band.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
            if direction.iloc[i] == 1 and lower_band.iloc[i] < lower_band.iloc[i - 1]:
                lower_band.iloc[i] = lower_band.iloc[i - 1]
            if direction.iloc[i] == -1 and upper_band.iloc[i] > upper_band.iloc[i - 1]:
                upper_band.iloc[i] = upper_band.iloc[i - 1]

        st.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == 1 else upper_band.iloc[i]

    return pd.DataFrame({"supertrend": st, "supertrend_dir": direction})


# ============================================================
# 批量生成趋势类特征
# ============================================================

def compute_trend_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种数据批量计算趋势类特征
    输入 df 需包含: close, high, low
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    result = pd.DataFrame(index=df.index)

    # 多周期均线
    for w in [3, 5, 10, 20, 60, 120]:
        result[f"sma_{w}"] = sma(close, w)
        result[f"ema_{w}"] = ema(close, w)

    # MACD
    macd_df = macd(close)
    result = pd.concat([result, macd_df], axis=1)

    # ADX
    adx_df = adx(high, low, close, 14)
    result = pd.concat([result, adx_df], axis=1)

    # Aroon
    aroon_df = aroon(high, low, 25)
    result = pd.concat([result, aroon_df], axis=1)

    # 均线排列
    result["ma_alignment"] = ma_alignment(close)

    # BIAS
    for w in [3, 5, 10, 20, 60]:
        result[f"bias_{w}"] = ma_bias(close, w)

    # 线性回归斜率
    for w in [3, 10, 20, 60]:
        result[f"slope_{w}"] = linear_slope(close, w)

    # SuperTrend
    st_df = supertrend(high, low, close)
    result = pd.concat([result, st_df], axis=1)

    return result
