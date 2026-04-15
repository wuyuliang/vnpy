"""
截面类特征（跨品种）

包含：
    - 截面收益率排名 (rank)
    - 截面 Z-Score 标准化
    - 截面动量排名
    - 截面波动率排名
    - 截面成交量排名
    - 截面持仓量排名
    - 相对强弱 (品种间)

注意：截面特征需要多品种数据同时计算，输入为宽表格式。
"""
import numpy as np
import pandas as pd


# ============================================================
# 通用截面工具
# ============================================================

def cross_section_rank(wide_df: pd.DataFrame) -> pd.DataFrame:
    """
    截面排名 (百分位)
    输入: wide_df，行=日期, 列=品种
    输出: 同形状 DataFrame，值为 [0, 1] 的排名百分位
    """
    return wide_df.rank(axis=1, pct=True)


def cross_section_zscore(wide_df: pd.DataFrame) -> pd.DataFrame:
    """
    截面 Z-Score
    输入: wide_df，行=日期, 列=品种
    """
    mean = wide_df.mean(axis=1)
    std = wide_df.std(axis=1).replace(0, np.nan)
    return wide_df.sub(mean, axis=0).div(std, axis=0)


def cross_section_demean(wide_df: pd.DataFrame) -> pd.DataFrame:
    """截面去均值"""
    mean = wide_df.mean(axis=1)
    return wide_df.sub(mean, axis=0)


# ============================================================
# 截面收益率特征
# ============================================================

def cs_return_rank(close_wide: pd.DataFrame, window: int = 1
                   ) -> pd.DataFrame:
    """截面收益率排名"""
    ret = close_wide.pct_change(window)
    return cross_section_rank(ret)


def cs_return_zscore(close_wide: pd.DataFrame, window: int = 1
                     ) -> pd.DataFrame:
    """截面收益率 Z-Score"""
    ret = close_wide.pct_change(window)
    return cross_section_zscore(ret)


# ============================================================
# 截面动量特征
# ============================================================

def cs_momentum_rank(close_wide: pd.DataFrame, window: int = 20
                     ) -> pd.DataFrame:
    """截面动量（N日收益率）排名"""
    momentum = close_wide.pct_change(window)
    return cross_section_rank(momentum)


# ============================================================
# 截面波动率特征
# ============================================================

def cs_volatility_rank(close_wide: pd.DataFrame, window: int = 20
                       ) -> pd.DataFrame:
    """截面波动率排名"""
    log_ret = np.log(close_wide / close_wide.shift(1))
    vol = log_ret.rolling(window).std()
    return cross_section_rank(vol)


# ============================================================
# 截面成交量/持仓量排名
# ============================================================

def cs_volume_rank(volume_wide: pd.DataFrame) -> pd.DataFrame:
    """截面成交量排名"""
    return cross_section_rank(volume_wide)


def cs_oi_rank(oi_wide: pd.DataFrame) -> pd.DataFrame:
    """截面持仓量排名"""
    return cross_section_rank(oi_wide)


# ============================================================
# 相对强弱
# ============================================================

def relative_strength(close_wide: pd.DataFrame, window: int = 20
                      ) -> pd.DataFrame:
    """
    品种相对强弱 = 品种收益率 / 全品种等权平均收益率
    """
    ret = close_wide.pct_change(window)
    avg_ret = ret.mean(axis=1).replace(0, np.nan)
    return ret.div(avg_ret, axis=0)


# ============================================================
# 从长表构建宽表
# ============================================================

def pivot_to_wide(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """
    将长表 (symbol, datetime, value_col) 转为宽表 (datetime x symbol)
    """
    return df.pivot_table(
        index="datetime", columns="symbol", values=value_col
    )


# ============================================================
# 批量生成截面类特征
# ============================================================

def compute_cross_section_features(all_data: pd.DataFrame) -> pd.DataFrame:
    """
    为所有品种数据批量计算截面类特征
    输入 all_data: 包含多品种的长表，需含 symbol, datetime, close, volume, open_interest

    返回: 长表格式，追加截面特征列
    """
    close_wide = pivot_to_wide(all_data, "close")
    volume_wide = pivot_to_wide(all_data, "volume")
    oi_wide = pivot_to_wide(all_data, "open_interest")

    features = {}

    # 截面收益率排名
    for w in [1, 3, 5, 20]:
        features[f"cs_ret_rank_{w}"] = cs_return_rank(close_wide, w)
        features[f"cs_ret_zscore_{w}"] = cs_return_zscore(close_wide, w)

    # 截面动量排名
    for w in [3, 10, 20, 60]:
        features[f"cs_mom_rank_{w}"] = cs_momentum_rank(close_wide, w)

    # 截面波动率排名
    for w in [3, 20, 60]:
        features[f"cs_vol_rank_{w}"] = cs_volatility_rank(close_wide, w)

    # 成交量/持仓量排名
    features["cs_volume_rank"] = cs_volume_rank(volume_wide)
    features["cs_oi_rank"] = cs_oi_rank(oi_wide)

    # 相对强弱
    for w in [3, 10, 20, 60]:
        features[f"cs_rel_strength_{w}"] = relative_strength(close_wide, w)

    # 将宽表特征 melt 回长表并合并
    result_parts = []
    for feat_name, feat_wide in features.items():
        melted = feat_wide.reset_index().melt(
            id_vars="datetime", var_name="symbol", value_name=feat_name
        )
        result_parts.append(melted.set_index(["datetime", "symbol"]))

    if not result_parts:
        return all_data

    cs_features = pd.concat(result_parts, axis=1).reset_index()
    merged = all_data.merge(cs_features, on=["datetime", "symbol"], how="left")
    return merged
