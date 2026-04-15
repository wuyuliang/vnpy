"""
价格形态类特征

包含：
    - K线实体与影线比例
    - 缺口 (Gap)
    - N日新高/新低
    - 支撑阻力位距离
    - 价格位置 (当前价在 N 日高低范围中的位置)
    - 连涨/连跌天数
    - 内/外包线
"""
import numpy as np
import pandas as pd


# ============================================================
# K线形态特征
# ============================================================

def candle_body_ratio(open_: pd.Series, high: pd.Series, low: pd.Series,
                      close: pd.Series) -> pd.Series:
    """实体占全幅的比例"""
    body = (close - open_).abs()
    full_range = high - low
    return body / full_range.replace(0, np.nan)


def upper_shadow_ratio(open_: pd.Series, high: pd.Series, low: pd.Series,
                       close: pd.Series) -> pd.Series:
    """上影线占全幅的比例"""
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    upper_shadow = high - upper_body
    full_range = high - low
    return upper_shadow / full_range.replace(0, np.nan)


def lower_shadow_ratio(open_: pd.Series, high: pd.Series, low: pd.Series,
                       close: pd.Series) -> pd.Series:
    """下影线占全幅的比例"""
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    lower_shadow = lower_body - low
    full_range = high - low
    return lower_shadow / full_range.replace(0, np.nan)


def candle_direction(open_: pd.Series, close: pd.Series) -> pd.Series:
    """K线方向: 1=阳线, -1=阴线, 0=十字星"""
    return np.sign(close - open_)


# ============================================================
# 缺口 (Gap)
# ============================================================

def gap(open_: pd.Series, close: pd.Series) -> pd.Series:
    """缺口 = 今开 - 昨收 (绝对值)"""
    return open_ - close.shift(1)


def gap_pct(open_: pd.Series, close: pd.Series) -> pd.Series:
    """缺口百分比"""
    prev_close = close.shift(1)
    return (open_ - prev_close) / prev_close.replace(0, np.nan) * 100


# ============================================================
# N日新高/新低
# ============================================================

def is_n_day_high(high: pd.Series, window: int) -> pd.Series:
    """是否创 N 日新高: 当前高点 > 过去 N 日最高 (0/1)"""
    prev_max = high.shift(1).rolling(window - 1).max()
    return (high > prev_max).astype(int)


def is_n_day_low(low: pd.Series, window: int) -> pd.Series:
    """是否创 N 日新低: 当前低点 < 过去 N 日最低 (0/1)"""
    prev_min = low.shift(1).rolling(window - 1).min()
    return (low < prev_min).astype(int)


def days_since_high(high: pd.Series, window: int) -> pd.Series:
    """距离 N 日内最高点的天数"""
    return high.rolling(window).apply(
        lambda x: window - 1 - x.argmax(), raw=True
    )


def days_since_low(low: pd.Series, window: int) -> pd.Series:
    """距离 N 日内最低点的天数"""
    return low.rolling(window).apply(
        lambda x: window - 1 - x.argmin(), raw=True
    )


# ============================================================
# 价格位置
# ============================================================

def price_position(close: pd.Series, high: pd.Series, low: pd.Series,
                   window: int) -> pd.Series:
    """当前收盘价在 N 日价格区间中的相对位置 [0, 1]"""
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    return (close - ll) / (hh - ll).replace(0, np.nan)


# ============================================================
# 连涨/连跌
# ============================================================

def consecutive_up_days(close: pd.Series) -> pd.Series:
    """连续上涨天数（连跌为负数）"""
    direction = np.sign(close.diff()).fillna(0)
    result = pd.Series(0, index=close.index, dtype=int)
    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d != 0 and direction.iloc[i] == direction.iloc[i - 1]:
            result.iloc[i] = result.iloc[i - 1] + d
        else:
            result.iloc[i] = d
    return result


# ============================================================
# 内/外包线
# ============================================================

def inside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    """内包线: 今日高低在昨日高低之内 (0/1)"""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    return ((high <= prev_high) & (low >= prev_low)).astype(int)


def outside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    """外包线: 今日高低完全包含昨日高低 (0/1)"""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    return ((high >= prev_high) & (low <= prev_low)).astype(int)


# ============================================================
# 批量生成价格形态类特征
# ============================================================

def compute_pattern_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种数据批量计算价格形态类特征
    输入 df 需包含: open, high, low, close
    """
    o = df["open"]
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]
    result = pd.DataFrame(index=df.index)

    # K线形态
    result["body_ratio"] = candle_body_ratio(o, h, l, c)
    result["upper_shadow_ratio"] = upper_shadow_ratio(o, h, l, c)
    result["lower_shadow_ratio"] = lower_shadow_ratio(o, h, l, c)
    result["candle_dir"] = candle_direction(o, c)

    # 缺口
    result["gap"] = gap(o, c)
    result["gap_pct"] = gap_pct(o, c)

    # N日新高/新低
    for w in [3, 5, 10, 20, 60]:
        result[f"is_{w}d_high"] = is_n_day_high(h, w)
        result[f"is_{w}d_low"] = is_n_day_low(l, w)
    for w in [3, 20, 60]:
        result[f"days_since_{w}d_high"] = days_since_high(h, w)
        result[f"days_since_{w}d_low"] = days_since_low(l, w)

    # 价格位置
    for w in [3, 10, 20, 60]:
        result[f"price_pos_{w}"] = price_position(c, h, l, w)

    # 连涨连跌
    result["consecutive_days"] = consecutive_up_days(c)

    # 内/外包线
    result["inside_bar"] = inside_bar(h, l)
    result["outside_bar"] = outside_bar(h, l)

    return result
