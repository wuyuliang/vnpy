"""
成交量与持仓量类特征

包含：
    - 成交量均线 / 量比
    - OBV (能量潮)
    - VWAP (成交量加权均价)
    - MFI (资金流量指数)
    - AD (Accumulation/Distribution)
    - CMF (Chaikin Money Flow)
    - Force Index
    - 持仓量变化 / 持仓量比率
    - 量价背离
"""
import numpy as np
import pandas as pd


# ============================================================
# 成交量均线 / 量比
# ============================================================

def volume_ma(volume: pd.Series, window: int) -> pd.Series:
    """成交量简单移动平均"""
    return volume.rolling(window).mean()


def volume_ratio(volume: pd.Series, window: int = 5) -> pd.Series:
    """量比 = 当日成交量 / N日成交量均值"""
    ma = volume_ma(volume, window).replace(0, np.nan)
    return volume / ma


def volume_zscore(volume: pd.Series, window: int = 20) -> pd.Series:
    """成交量 Z-Score"""
    ma = volume.rolling(window).mean()
    std = volume.rolling(window).std().replace(0, np.nan)
    return (volume - ma) / std


# ============================================================
# OBV (能量潮)
# ============================================================

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On Balance Volume"""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def obv_ma(close: pd.Series, volume: pd.Series, window: int = 20
           ) -> pd.Series:
    """OBV 的移动平均"""
    return obv(close, volume).rolling(window).mean()


# ============================================================
# VWAP
# ============================================================

def rolling_vwap(close: pd.Series, volume: pd.Series, window: int = 20
                 ) -> pd.Series:
    """滚动 VWAP"""
    pv = close * volume
    vol_sum = volume.rolling(window).sum().replace(0, np.nan)
    return pv.rolling(window).sum() / vol_sum


# ============================================================
# MFI (资金流量指数)
# ============================================================

def mfi(high: pd.Series, low: pd.Series, close: pd.Series,
        volume: pd.Series, window: int = 14) -> pd.Series:
    """Money Flow Index"""
    tp = (high + low + close) / 3
    mf = tp * volume
    tp_diff = tp.diff()

    pos_mf = pd.Series(np.where(tp_diff > 0, mf, 0), index=close.index)
    neg_mf = pd.Series(np.where(tp_diff < 0, mf, 0), index=close.index)

    pos_sum = pos_mf.rolling(window).sum()
    neg_sum = neg_mf.rolling(window).sum()

    mr = pos_sum / neg_sum
    return 100 - 100 / (1 + mr)


# ============================================================
# AD (Accumulation/Distribution)
# ============================================================

def ad_line(high: pd.Series, low: pd.Series, close: pd.Series,
            volume: pd.Series) -> pd.Series:
    """A/D 线"""
    clv = ((close - low) - (high - close)) / (high - low)
    clv = clv.fillna(0)
    return (clv * volume).cumsum()


def chaikin_osc(high: pd.Series, low: pd.Series, close: pd.Series,
                volume: pd.Series, fast: int = 3, slow: int = 10
                ) -> pd.Series:
    """Chaikin Oscillator = AD的EMA(fast) - AD的EMA(slow)"""
    ad = ad_line(high, low, close, volume)
    return ad.ewm(span=fast, adjust=False).mean() - ad.ewm(
        span=slow, adjust=False
    ).mean()


# ============================================================
# CMF (Chaikin Money Flow)
# ============================================================

def cmf(high: pd.Series, low: pd.Series, close: pd.Series,
        volume: pd.Series, window: int = 20) -> pd.Series:
    """Chaikin Money Flow"""
    clv = ((close - low) - (high - close)) / (high - low)
    clv = clv.fillna(0)
    vol_sum = volume.rolling(window).sum().replace(0, np.nan)
    return (clv * volume).rolling(window).sum() / vol_sum


# ============================================================
# Force Index
# ============================================================

def force_index(close: pd.Series, volume: pd.Series,
                window: int = 13) -> pd.Series:
    """Force Index"""
    fi = close.diff() * volume
    return fi.ewm(span=window, adjust=False).mean()


# ============================================================
# 持仓量特征
# ============================================================

def oi_change(open_interest: pd.Series) -> pd.Series:
    """持仓量变化"""
    return open_interest.diff()


def oi_change_pct(open_interest: pd.Series) -> pd.Series:
    """持仓量变化率 (%)"""
    result = open_interest.pct_change() * 100
    return result.replace([np.inf, -np.inf], np.nan)


def oi_volume_ratio(open_interest: pd.Series, volume: pd.Series) -> pd.Series:
    """持仓量 / 成交量"""
    return open_interest / volume.replace(0, np.nan)


def oi_ma(open_interest: pd.Series, window: int) -> pd.Series:
    """持仓量移动平均"""
    return open_interest.rolling(window).mean()


# ============================================================
# 量价背离
# ============================================================

def oi_price_divergence(open_interest: pd.Series, close: pd.Series,
                       window: int = 10) -> pd.Series:
    """
    持仓量-价格背离
    持仓量增加但价格下跌 或 持仓量减少但价格上涨 = 分歧信号
    返回: 1=同向(正常), -1=背离(异常信号)
    """
    oi_dir = np.sign(open_interest.diff(window))
    price_dir = np.sign(close.diff(window))
    return (oi_dir * price_dir).fillna(0)


def oi_acceleration(open_interest: pd.Series, window: int = 5) -> pd.Series:
    """
    持仓量变化加速度（二阶差分的移动平均）
    正 = 持仓加速增长（趋势延续），负 = 持仓加速减少
    """
    oi_diff = open_interest.diff()
    accel = oi_diff.diff()
    return accel.rolling(window, min_periods=1).mean()


def oi_concentration(open_interest: pd.Series, volume: pd.Series,
                     window: int = 20) -> pd.Series:
    """
    持仓量集中度变化: OI/Volume 比值的变异系数
    高 = 持仓结构不稳定，低 = 持仓结构稳定
    """
    ratio = open_interest / volume.replace(0, np.nan)
    mean_r = ratio.rolling(window, min_periods=2).mean()
    std_r = ratio.rolling(window, min_periods=2).std()
    return std_r / mean_r.replace(0, np.nan)


def price_volume_corr(close: pd.Series, volume: pd.Series,
                      window: int = 20) -> pd.Series:
    """价量相关系数（滚动）"""
    return close.rolling(window).corr(volume)


def price_volume_divergence(close: pd.Series, volume: pd.Series,
                            window: int = 10) -> pd.Series:
    """
    量价背离：价格创新高但成交量未配合
    返回: 价格变化方向与成交量变化方向的差异
    """
    price_direction = np.sign(close.diff(window))
    vol_direction = np.sign(volume.rolling(window).mean().diff(window))
    return price_direction - vol_direction


# ============================================================
# 批量生成成交量/持仓量类特征
# ============================================================

def compute_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种数据批量计算成交量/持仓量类特征
    输入 df 需包含: close, high, low, volume, open_interest
    """
    c = df["close"]
    h = df["high"]
    l = df["low"]  # noqa: E741
    v = df["volume"]
    oi = df["open_interest"]
    result = pd.DataFrame(index=df.index)

    # 成交量均线 / 量比
    for w in [5, 10, 20]:
        result[f"vol_ma_{w}"] = volume_ma(v, w)
    result["vol_ratio_5"] = volume_ratio(v, 5)
    result["vol_ratio_20"] = volume_ratio(v, 20)
    result["vol_zscore_20"] = volume_zscore(v, 20)

    # OBV
    result["obv"] = obv(c, v)
    result["obv_ma_20"] = obv_ma(c, v, 20)

    # VWAP
    for w in [10, 20]:
        result[f"vwap_{w}"] = rolling_vwap(c, v, w)

    # MFI
    result["mfi_14"] = mfi(h, l, c, v, 14)

    # AD & Chaikin
    result["ad_line"] = ad_line(h, l, c, v)
    result["chaikin_osc"] = chaikin_osc(h, l, c, v)

    # CMF
    result["cmf_20"] = cmf(h, l, c, v, 20)

    # Force Index
    result["force_idx_13"] = force_index(c, v, 13)

    # 持仓量
    result["oi_change"] = oi_change(oi)
    result["oi_change_pct"] = oi_change_pct(oi)
    result["oi_vol_ratio"] = oi_volume_ratio(oi, v)
    for w in [5, 20]:
        result[f"oi_ma_{w}"] = oi_ma(oi, w)

    # 持仓量进阶
    result["oi_price_div_10"] = oi_price_divergence(oi, c, 10)
    result["oi_acceleration_5"] = oi_acceleration(oi, 5)
    result["oi_concentration_20"] = oi_concentration(oi, v, 20)

    # 量价关系
    result["pv_corr_20"] = price_volume_corr(c, v, 20)
    result["pv_divergence_10"] = price_volume_divergence(c, v, 10)

    return result
