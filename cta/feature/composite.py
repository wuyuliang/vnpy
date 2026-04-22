"""
§17 综合评分 / Composite Scores

把 §1 / §3 / §6 / §9 / §13 / §15 的原始/中间特征聚合为 0-1 或 -1~1 的评分，
供策略门控 / 加权使用。bar-derivable。

输入：compute.py 上游已把各类特征拼到同一 DataFrame 里，本模块读取现成列。
缺失列一律视为 NaN，不会崩溃（在早期品种或数据不全时仍可生成部分分数）。

输出列：
    - trend_score          [-1, 1]
    - compression_score    [0, 1]
    - expansion_score      [0, 1]
    - breakout_mode_score  [0, 1]
    - setup_quality_score  [0, 1]
    - breakout_quality_score [0, 1]
    - context_score        [0, 1]
    - rr_score             [0, 1]
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _col(feat: pd.DataFrame, name: str) -> pd.Series:
    if name in feat.columns:
        return feat[name]
    return pd.Series(np.nan, index=feat.index)


def _norm(s: pd.Series, lo: float, hi: float) -> pd.Series:
    """线性映射到 [0, 1]，越界截断"""
    return ((s - lo) / (hi - lo)).clip(0, 1)


def _sigmoid(s: pd.Series) -> pd.Series:
    return 1.0 / (1.0 + np.exp(-s))


def compute_composite_features(feat: pd.DataFrame) -> pd.DataFrame:
    """
    feat 需已拼好 §1-§15 各类列。返回 DataFrame（index 与 feat 对齐）。
    """
    idx = feat.index
    out = pd.DataFrame(index=idx)

    # ---- trend_score ----
    slope20 = _col(feat, "slope_20")
    adx = _col(feat, "adx")
    ma_align = _col(feat, "ma_alignment")
    hh_hl_bias = (
        _col(feat, "pa_higher_high").fillna(0).rolling(10, min_periods=1).mean()
        - _col(feat, "pa_lower_low").fillna(0).rolling(10, min_periods=1).mean()
    )
    # slope_20 归一到 [-1, 1]：用 252 日 std 做 scale
    slope_std = slope20.rolling(252, min_periods=20).std().replace(0, np.nan)
    slope_norm = (slope20 / (2 * slope_std)).clip(-1, 1)
    adx_scaled = (_norm(adx, 20, 40) * 2 - 1)
    trend_sum = (
        slope_norm.fillna(0) +
        adx_scaled.fillna(0) +
        ma_align.fillna(0).clip(-1, 1) +
        hh_hl_bias.fillna(0)
    ) / 4
    out["trend_score"] = np.tanh(trend_sum)

    # ---- compression_score ----
    tight8 = _col(feat, "pa_tight_range_flag_8").fillna(0)
    bb_width = _col(feat, "bb_width")
    # bb_width 30% 分位作为 threshold：用 252 日 rolling quantile
    bb_q30 = bb_width.rolling(252, min_periods=30).quantile(0.3)
    bb_low = (bb_width <= bb_q30).astype(float)
    inside_10 = _col(feat, "pa_inside_bar_ratio_10").fillna(0)
    out["compression_score"] = (
        tight8 * 0.5 + bb_low * 0.3 + inside_10 * 0.2
    ).clip(0, 1)

    # ---- expansion_score ----
    atr_z = _col(feat, "atr_pct_zscore_252").fillna(0)
    range_atr = _col(feat, "range_height_atr_10").fillna(0)
    out["expansion_score"] = (
        (atr_z > 1).astype(float) * 0.5 +
        _norm(range_atr, 1.5, 3.5) * 0.5
    ).clip(0, 1)

    # ---- breakout_mode_score ----
    # 最近 10 根 compression_score >= 0.5 后出现 breakout_up / breakout_down
    compr_recent = out["compression_score"].shift(1).rolling(10, min_periods=1).max()
    bo_up = _col(feat, "pa_breakout_up_20").fillna(0)
    bo_dn = _col(feat, "pa_breakout_down_20").fillna(0)
    bo_any = ((bo_up > 0) | (bo_dn > 0)).astype(float)
    out["breakout_mode_score"] = (
        _norm(compr_recent, 0.3, 0.8) * 0.6 + bo_any * 0.4
    ).clip(0, 1)

    # ---- setup_quality_score ----
    sig_strength = _col(feat, "pa_signal_strength_10").fillna(0)
    pull_depth = _col(feat, "pa_pullback_depth_20").fillna(0).abs()
    ema20 = _col(feat, "ema_20")
    close = _col(feat, "close")
    atr14 = _col(feat, "atr_14").replace(0, np.nan)
    dist_ema = ((close - ema20) / atr14).abs().fillna(0)
    out["setup_quality_score"] = (
        _norm(sig_strength, 0, 1) * 0.5 +
        _norm(pull_depth, 0, 50) * 0.25 +
        _norm(-dist_ema, -3, 0) * 0.25
    ).clip(0, 1)

    # ---- breakout_quality_score ----
    body20 = _col(feat, "pa_bo_body_ratio_20").fillna(0)
    closepos20 = _col(feat, "pa_bo_close_pos_20").fillna(0.5)
    vol20 = _col(feat, "pa_bo_vol_ratio_20").fillna(1)
    vol_norm = _norm(vol20, 0.5, 3.0)
    out["breakout_quality_score"] = (
        _norm(body20, 0.3, 0.9) * 0.4 +
        _norm(closepos20, 0.5, 1.0) * 0.3 +
        vol_norm * 0.3
    ).clip(0, 1)

    # ---- context_score ----
    align = _col(feat, "mtf_align_flag").fillna(0)
    conflict = _col(feat, "mtf_conflict_score").fillna(0)
    regime_conf = _col(feat, "regime_conf").fillna(0)
    range_score = _col(feat, "range_ratio_20").fillna(0)
    out["context_score"] = (
        align * 0.4 +
        (1 - _norm(conflict, 0, 0.6)) * 0.3 +
        regime_conf * 0.2 +
        _norm(range_score, 0.02, 0.12) * 0.1
    ).clip(0, 1)

    # ---- rr_score ----
    expected_rr = _col(feat, "pa_expected_rr").fillna(0)
    out["rr_score"] = _norm(expected_rr, 0.5, 3.0)

    return out
