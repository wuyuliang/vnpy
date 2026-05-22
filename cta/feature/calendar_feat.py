"""
日历/时间类特征

包含：
    - 星期几
    - 月份 / 季度
    - 月初/月末/周初/周末
    - 距离月末天数
    - 节假日前后效应（需配合交易日历）
    - 季节性收益均值
"""
import numpy as np
import pandas as pd


# ============================================================
# 基础日历特征
# ============================================================

def day_of_week(dt: pd.Series) -> pd.Series:
    """星期几 (0=Monday, 4=Friday)"""
    return dt.dt.dayofweek


def month(dt: pd.Series) -> pd.Series:
    """月份 (1-12)"""
    return dt.dt.month


def quarter(dt: pd.Series) -> pd.Series:
    """季度 (1-4)"""
    return dt.dt.quarter


def day_of_month(dt: pd.Series) -> pd.Series:
    """一月中的第几天"""
    return dt.dt.day


def day_of_year(dt: pd.Series) -> pd.Series:
    """一年中的第几天"""
    return dt.dt.dayofyear


def week_of_year(dt: pd.Series) -> pd.Series:
    """一年中的第几周"""
    return dt.dt.isocalendar().week.astype(int)


# ============================================================
# 周初/周末/月初/月末
# ============================================================

def is_week_start(dt: pd.Series) -> pd.Series:
    """是否为交易周第一天（dayofweek 比前一天小，说明跨周了）"""
    dow = dt.dt.dayofweek
    prev_dow = dow.shift(1)
    # 首行无法判断，默认为 False
    return (dow < prev_dow).fillna(False).astype(int)


def is_week_end(dt: pd.Series) -> pd.Series:
    """是否为自然周最后一个交易日（通常周五）。"""
    return (dt.dt.dayofweek == 4).astype(int)


def is_month_start(dt: pd.Series) -> pd.Series:
    """是否为月度首个交易日"""
    m = dt.dt.month
    prev_m = m.shift(1)
    # 首行: NaN != m 会返回 True，用 fillna 修正
    return (m != prev_m).fillna(False).astype(int)


def is_month_end(dt: pd.Series) -> pd.Series:
    """是否为自然月末（无需依赖下一行数据）。"""
    return dt.dt.is_month_end.astype(int)


# ============================================================
# 距离月末天数
# ============================================================

def days_to_month_end(dt: pd.Series) -> pd.Series:
    """距离月末的自然日天数"""
    month_end = dt.dt.to_period("M").dt.to_timestamp("M")
    return (month_end - dt).dt.days


# ============================================================
# 周期性编码 (sin/cos)
# ============================================================

def cyclical_encode(values: pd.Series, period: int
                    ) -> tuple[pd.Series, pd.Series]:
    """将周期性特征编码为 sin/cos"""
    angle = 2 * np.pi * values / period
    return np.sin(angle), np.cos(angle)


# ============================================================
# 季节性收益（历史同月/同周均值）
# ============================================================

def seasonal_return(close: pd.Series, dt: pd.Series,
                    by: str = "month") -> pd.Series:
    """
    历史同期平均收益率
    by: "month" 或 "weekday"
    """
    ret = close.pct_change()
    if by == "month":
        group_key = dt.dt.month
    elif by == "weekday":
        group_key = dt.dt.dayofweek
    else:
        raise ValueError(f"不支持的分组方式: {by}")

    # 扩展均值：只用截至当前的历史数据
    expanding_mean = ret.groupby(group_key).expanding().mean()
    # 重排索引
    expanding_mean = expanding_mean.droplevel(0).sort_index()
    return expanding_mean


# ============================================================
# 批量生成日历类特征
# ============================================================

def compute_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种数据批量计算日历类特征
    输入 df 需包含: datetime, close
    """
    dt = df["datetime"]
    result = pd.DataFrame(index=df.index)

    # 基础日历
    result["dow"] = day_of_week(dt)
    result["month"] = month(dt)
    result["quarter"] = quarter(dt)
    result["dom"] = day_of_month(dt)
    result["doy"] = day_of_year(dt)
    result["woy"] = week_of_year(dt)

    # 周初周末月初月末
    result["is_week_start"] = is_week_start(dt)
    result["is_week_end"] = is_week_end(dt)
    result["is_month_start"] = is_month_start(dt)
    result["is_month_end"] = is_month_end(dt)

    # 距月末天数
    result["days_to_month_end"] = days_to_month_end(dt)

    # 周期性编码
    dow_sin, dow_cos = cyclical_encode(result["dow"], 5)
    result["dow_sin"] = dow_sin
    result["dow_cos"] = dow_cos
    month_sin, month_cos = cyclical_encode(result["month"], 12)
    result["month_sin"] = month_sin
    result["month_cos"] = month_cos

    return result
