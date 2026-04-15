"""
统计/分形类特征

包含：
    - 分形维度 (Hurst 指数)
    - 价格序列信息熵
"""
import numpy as np
import pandas as pd


def hurst_exponent(close: pd.Series, window: int = 60) -> pd.Series:
    """
    Hurst 指数（R/S 分析）
    H > 0.5 = 趋势性（正自相关），H < 0.5 = 均值回归，H ≈ 0.5 = 随机游走

    R/S 算法:
    1. 计算对数收益率序列
    2. 对每个子窗口计算 R/S (极差/标准差)
    3. 用多个子窗口尺度的 log(R/S) vs log(n) 回归斜率估计 H
    """
    log_ret = np.log(close / close.shift(1))

    def _hurst(arr):
        n = len(arr)
        if n < 8:
            return np.nan
        # 去除 NaN
        arr = arr[~np.isnan(arr)]
        if len(arr) < 8:
            return np.nan

        # 使用多个子窗口尺度
        max_k = min(len(arr) // 2, 32)
        if max_k < 3:
            return np.nan

        sizes = []
        rs_values = []
        # 从 k=3 开始，步进 +1，确保小窗口也能产生足够尺度点
        for k in range(3, max_k + 1):
            num_blocks = len(arr) // k
            if num_blocks < 1:
                break
            rs_list = []
            for b in range(num_blocks):
                block = arr[b * k:(b + 1) * k]
                mean_b = block.mean()
                std_b = block.std()
                if std_b == 0:
                    continue
                cumdev = np.cumsum(block - mean_b)
                r = cumdev.max() - cumdev.min()
                rs_list.append(r / std_b)
            if rs_list:
                sizes.append(k)
                rs_values.append(np.mean(rs_list))

        if len(sizes) < 3:
            return np.nan

        log_sizes = np.log(sizes)
        log_rs = np.log(rs_values)
        # 线性回归
        x_mean = log_sizes.mean()
        y_mean = log_rs.mean()
        x_var = ((log_sizes - x_mean) ** 2).sum()
        if x_var == 0:
            return np.nan
        slope = ((log_sizes - x_mean) * (log_rs - y_mean)).sum() / x_var
        return np.clip(slope, 0, 1)

    return log_ret.rolling(window, min_periods=20).apply(_hurst, raw=True)


def price_entropy(close: pd.Series, window: int = 60,
                  n_bins: int = 10) -> pd.Series:
    """
    价格序列信息熵（Shannon Entropy）
    低 = 有序(趋势)，高 = 无序(震荡/随机)

    对收益率分箱后计算概率分布的熵
    """
    log_ret = np.log(close / close.shift(1))

    def _entropy(arr):
        arr = arr[~np.isnan(arr)]
        if len(arr) < 5:
            return np.nan
        # 等频分箱
        hist, _ = np.histogram(arr, bins=n_bins)
        probs = hist / hist.sum()
        probs = probs[probs > 0]
        return -np.sum(probs * np.log2(probs))

    return log_ret.rolling(window, min_periods=10).apply(_entropy, raw=True)


# ============================================================
# 批量生成统计类特征
# ============================================================

def compute_stats_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    为单品种批量计算统计/分形类特征
    输入 df 需包含: close
    """
    c = df["close"]
    result = pd.DataFrame(index=df.index)

    for w in [20, 60]:
        result[f"hurst_{w}"] = hurst_exponent(c, w)
        result[f"entropy_{w}"] = price_entropy(c, w)

    return result
