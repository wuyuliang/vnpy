"""训练数据集构建:遍历 top-N 品种,找 setup 点,打标签,聚合为 (X, y)。

核心流程(每个 vt_symbol × ltf interval):
1. 加载该品种整段 ltf 特征
2. 逐 bar 调用 HTF/MTF/LTF → 挑出 should_enter 的 bar 作为样本
3. 对样本调用 labeler.label_setup 打标签
4. X = 该 bar 的 pa_* 特征子集 + MTF/HTF 附加上下文

输出:
- X: DataFrame(样本数 × 特征数)
- y: Series(0/1)
- meta: DataFrame(vt_symbol / ts / entry / stop / mfe / mae / holding_bars / exit_kind)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cta.strategy.brooks.config.params import BrooksV3Params
from cta.strategy.brooks.config.symbols import get_symbol_meta
from cta.strategy.brooks.core.features.adapter import FeatureAdapter
from cta.strategy.brooks.core.model.labeler import label_setup
from cta.strategy.brooks.core.signal import (
    detect_htf_bias,
    detect_ltf_entry,
    detect_mtf_setup,
)

logger = logging.getLogger(__name__)


# pa_* 特征中用于训练的子集(剔除冗余/常量列;下一轮可调)
DEFAULT_FEATURE_COLS = [
    # trend
    "pa_always_in_dir",
    "pa_trend_strength_3", "pa_trend_strength_10", "pa_trend_strength_20",
    "pa_trend_bar_cluster_20",
    "pa_ema_slope_20", "pa_ema_slope_60", "pa_ema_slope_accel",
    # breakout
    "pa_breakout_up_3", "pa_breakout_up_10", "pa_breakout_up_20",
    "pa_breakout_strength_3", "pa_breakout_strength_10", "pa_breakout_strength_20",
    "pa_breakout_fail_3", "pa_breakout_fail_10", "pa_breakout_fail_20",
    "pa_breakout_pullback_3", "pa_breakout_pullback_10", "pa_breakout_pullback_20",
    # pullback
    "pa_pullback_depth_3", "pa_pullback_depth_10", "pa_pullback_depth_20",
    "pa_two_leg_pullback", "pa_pullback_retrace",
    "pa_pullback_tb_quality_3", "pa_pullback_tb_quality_5",
    "pa_pullback_to_ema_20",
    # channel / tightness
    "pa_micro_channel_3", "pa_micro_channel_5",
    "pa_tight_channel_10", "pa_tight_channel_20",
    "pa_pre_bo_tightness_5", "pa_pre_bo_tightness_10",
    "pa_channel_width_chg", "pa_channel_overshoot",
    # structure
    "pa_h123",
    "pa_signal_strength_3", "pa_signal_strength_10", "pa_signal_strength_20",
    "pa_expected_rr", "pa_expected_reward",
    # bar features
    "pa_bar_range_rel_10", "pa_bar_range_rel_20",
    "pa_body_ratio", "pa_close_position",
    "pa_is_trend_bar", "pa_consec_bull", "pa_consec_bear",
]


@dataclass
class Sample:
    vt_symbol: str
    ts: pd.Timestamp
    entry_price: float
    stop_price: float
    features: dict
    # MTF/HTF 上下文(可作为特征)
    htf_direction: int
    htf_trend_strength: float
    mtf_pullback_depth: float
    mtf_h123: int


@dataclass
class Dataset:
    X: pd.DataFrame
    y: pd.Series
    meta: pd.DataFrame

    feature_cols: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.X)

    def time_split(self, train_end: str, val_end: str) -> "tuple[Dataset, Dataset, Dataset]":
        """按 meta['ts'] 分 train / val / test,闭区间。"""
        ts = pd.to_datetime(self.meta["ts"])
        train_mask = ts <= pd.Timestamp(train_end)
        val_mask = (ts > pd.Timestamp(train_end)) & (ts <= pd.Timestamp(val_end))
        test_mask = ts > pd.Timestamp(val_end)

        def sub(m: pd.Series) -> Dataset:
            return Dataset(
                X=self.X.loc[m].reset_index(drop=True),
                y=self.y.loc[m].reset_index(drop=True),
                meta=self.meta.loc[m].reset_index(drop=True),
                feature_cols=self.feature_cols,
            )

        return sub(train_mask), sub(val_mask), sub(test_mask)


def build_dataset(
    vt_symbols: list[str],
    params: BrooksV3Params,
    start_date: str | None = None,
    end_date: str | None = None,
    feature_cols: list[str] | None = None,
    adapter: FeatureAdapter | None = None,
) -> Dataset:
    """按 cfg 扫描 vt_symbols,提取所有 setup 样本 + 标签。"""
    adapter = adapter or FeatureAdapter(mode="offline")
    feature_cols = feature_cols or DEFAULT_FEATURE_COLS
    ltf = params.intervals.ltf
    htf = params.intervals.htf
    mtf = params.intervals.mtf

    all_samples: list[Sample] = []
    labels: list[int] = []
    meta_rows: list[dict] = []

    for vt in vt_symbols:
        meta_s = get_symbol_meta(vt, params.contract)
        try:
            ltf_df = adapter.get_range(vt, ltf, start_date, end_date)
        except FileNotFoundError:
            logger.warning("skip %s: %s feature missing", vt, ltf)
            continue
        if ltf_df.empty:
            continue
        ltf_df = ltf_df.reset_index(drop=True)
        logger.info("build_dataset %s %s: %d bars", vt, ltf, len(ltf_df))

        for i, row in ltf_df.iterrows():
            ts = row["datetime"]
            htf_feat = adapter.get(vt, htf, ts)
            mtf_feat = adapter.get(vt, mtf, ts)
            hb = detect_htf_bias(htf_feat, params.signal.htf)
            if hb.direction != 1:
                continue
            ms = detect_mtf_setup(mtf_feat, hb.direction, params.signal.mtf)
            if not ms.is_valid:
                continue
            le = detect_ltf_entry(row, ms.direction, params.signal.ltf, meta_s.pricetick)
            if not le.should_enter:
                continue

            # 构造特征向量
            feat_vec = {c: float(row.get(c, np.nan)) if row.get(c) is not None else np.nan
                        for c in feature_cols}
            # 叠加 HTF/MTF 上下文
            feat_vec["ctx_htf_trend_strength"] = hb.strength
            feat_vec["ctx_mtf_pullback_depth"] = ms.pullback_depth
            feat_vec["ctx_mtf_h123"] = float(
                {"h1": 1, "h2": 2, "h3": 3}.get(ms.setup_type, 0))

            # 打标签
            lab = label_setup(
                ohlc=ltf_df,
                entry_idx=int(i),
                entry_price=le.trigger_price,
                stop_price=le.stop_price,
                target_rr=params.model.target_rr,
                target_bars=params.model.target_bars,
                direction=1,
            )
            if lab.exit_kind == "nan":
                continue

            all_samples.append(Sample(
                vt_symbol=vt, ts=pd.Timestamp(ts),
                entry_price=le.trigger_price, stop_price=le.stop_price,
                features=feat_vec,
                htf_direction=hb.direction, htf_trend_strength=hb.strength,
                mtf_pullback_depth=ms.pullback_depth,
                mtf_h123=int({"h1": 1, "h2": 2, "h3": 3}.get(ms.setup_type, 0)),
            ))
            labels.append(lab.label)
            meta_rows.append({
                "vt_symbol": vt, "ts": pd.Timestamp(ts),
                "entry": le.trigger_price, "stop": le.stop_price,
                "mfe": lab.mfe, "mae": lab.mae,
                "holding_bars": lab.holding_bars,
                "exit_kind": lab.exit_kind, "exit_price": lab.exit_price,
            })

    if not all_samples:
        return Dataset(X=pd.DataFrame(), y=pd.Series(dtype=int),
                       meta=pd.DataFrame(), feature_cols=feature_cols)

    feature_cols_full = list(all_samples[0].features.keys())
    X = pd.DataFrame([s.features for s in all_samples])[feature_cols_full]
    y = pd.Series(labels, dtype=int)
    meta = pd.DataFrame(meta_rows)
    logger.info("build_dataset done: %d samples, %d features, pos_rate=%.3f",
                len(X), len(feature_cols_full), y.mean() if len(y) else 0.0)
    return Dataset(X=X, y=y, meta=meta, feature_cols=feature_cols_full)


__all__ = ["Dataset", "Sample", "build_dataset", "DEFAULT_FEATURE_COLS"]
