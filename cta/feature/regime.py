"""
§16 市场状态机 / Regime Labels

把 §3（波动率）+ §6（price_action）+ §13（volatility_regime）的分数聚合为
离散 regime 标签，供 `05_regime_switch_strategies` 使用。bar-derivable。

regime_label 取值：
    trend_up / trend_down / range / compression / expansion / transition

依赖特征（由 compute.py 上游提供）：
    - pa_trend_strength_20     (price_action)
    - pa_trading_range_20      (price_action_context)
    - pa_higher_high / pa_lower_low (price_action)
    - atr_pct_zscore_252 / bb_width_zscore_252 / vol_of_vol_20 (volatility_regime)

输出列：
    - regime_label    (string)
    - regime_conf     (float 0-1)
    - regime_age      (int，自上次切换以来的 bar 数)
    - transition_flag (0/1)
    - transition_risk (float 0-1)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


_LABELS = ["trend_up", "trend_down", "range", "compression", "expansion", "transition"]
REGIME_LABELS = tuple(_LABELS)


def _softmax(arr: np.ndarray) -> np.ndarray:
    """数值稳定 softmax（最后一维）"""
    m = np.max(arr, axis=-1, keepdims=True)
    e = np.exp(arr - m)
    return e / np.sum(e, axis=-1, keepdims=True)


def compute_regime_features(feat: pd.DataFrame) -> pd.DataFrame:
    """
    feat 需已包含以下（全部 NaN-safe）：
      - pa_trend_strength_20（或 price_action 派生的等价列）
      - pa_trading_range_20
      - pa_higher_high / pa_lower_low
      - atr_pct_zscore_252, bb_width_zscore_252
    缺失列视为 NaN，不会导致崩溃。
    返回 DataFrame（index 与 feat 对齐）
    """
    n = len(feat)
    idx = feat.index

    def _col(name: str) -> pd.Series:
        return feat[name] if name in feat.columns else pd.Series(np.nan, index=idx)

    trend = _col("pa_trend_strength_20")  # [-100, 100]
    tr_range = _col("pa_trading_range_20")  # [0, 1]
    hh = _col("pa_higher_high")
    ll = _col("pa_lower_low")
    atr_z = _col("atr_pct_zscore_252")
    bb_z = _col("bb_width_zscore_252")
    vov = _col("vol_of_vol_20")

    hh_recent = hh.fillna(0).rolling(5, min_periods=1).sum()
    ll_recent = ll.fillna(0).rolling(5, min_periods=1).sum()

    # 各 regime 的原始得分（0-1 量级）
    s_trend_up = np.clip((trend - 30) / 70, 0, 1) * (hh_recent >= 3).astype(float)
    s_trend_dn = np.clip((-trend - 30) / 70, 0, 1) * (ll_recent >= 3).astype(float)
    s_range = np.clip((tr_range - 0.6) / 0.4, 0, 1)
    s_compr = np.clip(-bb_z - 0.5, 0, 2) / 2
    s_expa = np.clip(atr_z - 0.5, 0, 2) / 2
    # transition：都不占优
    s_trans = 1.0 - pd.concat(
        [s_trend_up, s_trend_dn, s_range, s_compr, s_expa], axis=1
    ).max(axis=1).clip(lower=0, upper=1)

    mat = np.stack([
        s_trend_up.fillna(0).values,
        s_trend_dn.fillna(0).values,
        s_range.fillna(0).values,
        s_compr.fillna(0).values,
        s_expa.fillna(0).values,
        s_trans.fillna(0).values,
    ], axis=-1)

    prob = _softmax(mat * 3.0)  # 乘 3 把差异拉开
    order = np.argsort(-prob, axis=-1)
    top_idx = order[:, 0]
    second_idx = order[:, 1]
    conf = prob[np.arange(n), top_idx] - prob[np.arange(n), second_idx]

    labels = np.array([_LABELS[i] for i in top_idx])

    # regime_age: 自上次切换以来的 bar 数
    age = np.zeros(n, dtype=int)
    for i in range(1, n):
        if labels[i] == labels[i - 1]:
            age[i] = age[i - 1] + 1

    result = pd.DataFrame(index=idx)
    result["regime_label"] = labels
    result["regime_conf"] = conf
    result["regime_age"] = age
    result["transition_flag"] = (
        (result["regime_label"] == "transition") | (result["regime_age"] < 5)
    ).astype(int)

    # transition_risk = 0.5 * (1 - conf) + 0.5 * sigmoid(vol_of_vol zscore)
    if vov.notna().any():
        vov_mean = vov.rolling(60, min_periods=10).mean()
        vov_std = vov.rolling(60, min_periods=10).std().replace(0, np.nan)
        vov_z = (vov - vov_mean) / vov_std
        vov_sig = 1.0 / (1.0 + np.exp(-vov_z.fillna(0)))
    else:
        vov_sig = pd.Series(0.5, index=idx)

    result["transition_risk"] = 0.5 * (1 - result["regime_conf"]) + 0.5 * vov_sig
    result["transition_risk"] = result["transition_risk"].clip(0, 1)

    return result


__all__ = ["compute_regime_features", "REGIME_LABELS"]
