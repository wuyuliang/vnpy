"""
分钟级同比特征 (Time-of-Day Comparison)

核心思想：
    同一品种每天的日内结构有规律性（开盘波动、午盘沉寂、尾盘异动），
    偏离历史同期的行为往往是交易信号。

对比基准: 1天前同时刻、3天均值同时刻、5天均值同时刻
时间粒度: 1分钟、3分钟、5分钟、10分钟、20分钟
特征类型: 价格收益率、波动率(振幅)、成交量、K线形态

仅在分钟级数据中生成，天级数据自动跳过。
"""
import numpy as np
import pandas as pd


# ====================================================================
# 辅助函数
# ====================================================================

def _extract_trade_date(dt: pd.Series) -> pd.Series:
    """
    从 datetime 提取交易日期。
    夜盘(21:00-23:59)归属下一个交易日：简化处理为 date + 1 day，
    但严格做法需要交易日历，这里用 hour >= 21 判断。
    """
    date = pd.to_datetime(dt.dt.date)
    is_night = dt.dt.hour >= 21
    # 夜盘归属下一自然日（近似交易日）
    result = date.copy()
    result.loc[is_night] = result.loc[is_night] + pd.Timedelta(days=1)
    return result


def _extract_time_key(dt: pd.Series) -> pd.Series:
    """从 datetime 提取时间标识 (HH:MM 字符串)"""
    return dt.dt.strftime("%H:%M")


def _aggregate_bars(df: pd.DataFrame, freq_minutes: int) -> pd.DataFrame:
    """
    将 1 分钟K线按 freq_minutes 聚合。
    每个交易日内独立分组，不跨日聚合。
    """
    if freq_minutes <= 1:
        return df.copy()

    df = df.copy()
    if "trade_date" not in df.columns:
        df["trade_date"] = _extract_trade_date(df["datetime"])

    # 在每个交易日内按顺序分组
    df["_bar_idx"] = df.groupby("trade_date").cumcount()
    df["_group"] = df["_bar_idx"] // freq_minutes

    agg_funcs = {
        "datetime": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in df.columns:
        agg_funcs["volume"] = "sum"
    if "trade_date" in df.columns:
        agg_funcs["trade_date"] = "first"

    # 保留 symbol 等元数据
    for col in ["symbol", "exchange"]:
        if col in df.columns:
            agg_funcs[col] = "first"

    grouped = df.groupby(["trade_date", "_group"], sort=False)
    result = grouped.agg(agg_funcs).reset_index(drop=True)
    return result


def _same_time_shift(series: pd.Series, time_key: pd.Series,
                     trade_date: pd.Series, n_days: int) -> pd.Series:
    """
    在同一时段内向前偏移 N 个交易日。
    返回: N 个交易日前同时段的值。
    """
    df_tmp = pd.DataFrame({
        "value": series,
        "time_key": time_key,
        "trade_date": trade_date,
    })
    # 每个 time_key 组内按交易日排序后 shift
    result = df_tmp.groupby("time_key")["value"].shift(n_days)
    return result


def _same_time_mean(series: pd.Series, time_key: pd.Series,
                    trade_date: pd.Series, n_days: int) -> pd.Series:
    """
    同一时段过去 N 个交易日的均值。
    """
    df_tmp = pd.DataFrame({
        "value": series,
        "time_key": time_key,
        "trade_date": trade_date,
    })
    result = df_tmp.groupby("time_key")["value"].transform(
        lambda x: x.rolling(n_days, min_periods=1).mean().shift(1)
    )
    return result


def _same_time_std(series: pd.Series, time_key: pd.Series,
                   trade_date: pd.Series, n_days: int) -> pd.Series:
    """
    同一时段过去 N 个交易日的标准差。
    """
    df_tmp = pd.DataFrame({
        "value": series,
        "time_key": time_key,
        "trade_date": trade_date,
    })
    result = df_tmp.groupby("time_key")["value"].transform(
        lambda x: x.rolling(n_days, min_periods=2).std().shift(1)
    )
    return result


# ====================================================================
# 核心特征计算
# ====================================================================

def _compute_tod_features_for_freq(df: pd.DataFrame, freq_minutes: int
                                    ) -> pd.DataFrame:
    """
    为指定时间粒度计算全部同比特征。
    返回的 DataFrame 与原始 df 行数可能不同（如果聚合了）。
    """
    agg_df = _aggregate_bars(df, freq_minutes)

    if "trade_date" not in agg_df.columns:
        agg_df["trade_date"] = _extract_trade_date(agg_df["datetime"])
    time_key = _extract_time_key(agg_df["datetime"])
    trade_date = agg_df["trade_date"]

    result = pd.DataFrame(index=agg_df.index)
    tag = f"{freq_minutes}min"

    # ---- 价格收益率 ----
    ret = agg_df["close"].pct_change().fillna(0)

    for n_days, label in [(1, "1d"), (3, "3d"), (5, "5d")]:
        if n_days == 1:
            ref = _same_time_shift(ret, time_key, trade_date, 1)
        else:
            ref = _same_time_mean(ret, time_key, trade_date, n_days)
        result[f"tod_ret_vs_{label}_{tag}"] = ret - ref

    # 收益率 Z-Score (5 天)
    mean_5d = _same_time_mean(ret, time_key, trade_date, 5)
    std_5d = _same_time_std(ret, time_key, trade_date, 5)
    result[f"tod_ret_zscore_{tag}"] = (ret - mean_5d) / std_5d.replace(0, np.nan)

    # ---- 波动率（振幅）----
    rng = (agg_df["high"] - agg_df["low"]) / agg_df["close"].replace(0, np.nan)

    for n_days, label in [(1, "1d"), (3, "3d"), (5, "5d")]:
        if n_days == 1:
            ref = _same_time_shift(rng, time_key, trade_date, 1)
        else:
            ref = _same_time_mean(rng, time_key, trade_date, n_days)
        result[f"tod_range_vs_{label}_{tag}"] = rng - ref

    # 振幅比值 (1 天)
    ref_1d = _same_time_shift(rng, time_key, trade_date, 1)
    result[f"tod_range_ratio_1d_{tag}"] = rng / ref_1d.replace(0, np.nan)

    # ---- 成交量 ----
    if "volume" in agg_df.columns:
        vol = agg_df["volume"].astype(float)

        for n_days, label in [(1, "1d"), (3, "3d"), (5, "5d")]:
            if n_days == 1:
                ref = _same_time_shift(vol, time_key, trade_date, 1)
            else:
                ref = _same_time_mean(vol, time_key, trade_date, n_days)
            result[f"tod_vol_vs_{label}_{tag}"] = vol / ref.replace(0, np.nan)

        # 成交量 Z-Score
        mean_5d_vol = _same_time_mean(vol, time_key, trade_date, 5)
        std_5d_vol = _same_time_std(vol, time_key, trade_date, 5)
        result[f"tod_vol_zscore_{tag}"] = (
            (vol - mean_5d_vol) / std_5d_vol.replace(0, np.nan)
        )

    # ---- K线形态 ----
    body = (agg_df["close"] - agg_df["open"]).abs()
    full_range = (agg_df["high"] - agg_df["low"]).replace(0, np.nan)
    body_ratio = (body / full_range).fillna(0)
    close_pos = ((agg_df["close"] - agg_df["low"]) / full_range).fillna(0.5)
    direction = np.sign(agg_df["close"] - agg_df["open"]).fillna(0)

    # 实体占比 vs 3天均值
    ref_br = _same_time_mean(body_ratio, time_key, trade_date, 3)
    result[f"tod_body_ratio_vs_3d_{tag}"] = body_ratio - ref_br

    # 收盘位置 vs 3天均值
    ref_cp = _same_time_mean(close_pos, time_key, trade_date, 3)
    result[f"tod_close_pos_vs_3d_{tag}"] = close_pos - ref_cp

    # 方向一致性 (与过去5天同时段)
    dir_match_sum = pd.Series(0.0, index=agg_df.index)
    for lag in range(1, 6):
        past_dir = _same_time_shift(direction, time_key, trade_date, lag)
        dir_match_sum += (direction == past_dir).astype(float)
    result[f"tod_dir_consistency_{tag}"] = dir_match_sum / 5

    return result, agg_df


def compute_minute_tod_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为分钟级数据批量计算同比特征。

    对于 M > 1 分钟的粒度，需要将聚合后的特征映射回原始 1 分钟行。
    映射方式：每个聚合块内的所有 1 分钟行共享同一组特征值。

    输入 df 需包含: datetime, open, high, low, close, volume(可选)
    """
    # 检测是否为分钟数据：同一天有多行
    if "datetime" not in df.columns:
        return pd.DataFrame(index=df.index)

    dates = df["datetime"].dt.date
    rows_per_date = dates.value_counts()
    if rows_per_date.median() < 10:
        # 不是分钟数据，跳过
        return pd.DataFrame(index=df.index)

    # 准备工作副本
    df_work = df.copy()
    df_work["trade_date"] = _extract_trade_date(df_work["datetime"])

    all_results = pd.DataFrame(index=df.index)

    for freq in [1, 3, 5, 10, 20]:
        feat_df, agg_df = _compute_tod_features_for_freq(df_work, freq)

        if freq == 1:
            # 1 分钟粒度：直接对齐（行数相同）
            for col in feat_df.columns:
                all_results[col] = feat_df[col].values
        else:
            # M > 1 分钟：将聚合结果映射回原始行
            # 构建映射索引：每根原始K线对应哪个聚合组
            bar_idx = df_work.groupby("trade_date").cumcount()
            group_idx = bar_idx // freq

            # 聚合 df 中每个 trade_date 内的组序号
            if "trade_date" not in agg_df.columns:
                agg_df["trade_date"] = _extract_trade_date(agg_df["datetime"])
            agg_group_idx = agg_df.groupby("trade_date").cumcount()

            # 构建 (trade_date, group) -> 聚合行号 的映射
            agg_df_tmp = pd.DataFrame({
                "trade_date": agg_df["trade_date"].values,
                "_agg_group": agg_group_idx.values,
                "_agg_row": np.arange(len(agg_df)),
            })

            orig_keys = pd.DataFrame({
                "trade_date": df_work["trade_date"].values,
                "_agg_group": group_idx.values,
            })

            merged = orig_keys.merge(agg_df_tmp, on=["trade_date", "_agg_group"],
                                     how="left")
            agg_row_map = merged["_agg_row"].values
            valid_mask = ~np.isnan(agg_row_map)
            safe_idx = np.where(valid_mask, agg_row_map.astype(int), 0)

            for col in feat_df.columns:
                vals = feat_df[col].values
                mapped = vals[safe_idx]
                mapped = mapped.astype(float)
                mapped[~valid_mask] = np.nan
                all_results[col] = mapped

    return all_results
