"""Tests for candidate-event training dataset builder."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.model.feature.candidate_training_dataset import (
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _parse_args,
    build_and_save_candidate_training_dataset,
    standardize_candidate_events,
)



class TestCandidateTrainingDatasetPart02(unittest.TestCase):
    def test_normalize_intervals_raises_when_empty(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_intervals(["", " , ", "   "])

    def test_standardize_candidate_events_maps_status_and_labels(self) -> None:
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["minute60", "minute60", "minute60"],
                "datetime": pd.to_datetime(
                    ["2019-01-02 09:00:00", "2019-01-02 10:00:00", "2019-01-02 11:00:00"]
                ),
                "signal_datetime": pd.to_datetime(
                    ["2019-01-02 08:00:00", "2019-01-02 09:00:00", "2019-01-02 10:00:00"]
                ),
                "signal_type": ["donchian_breakout", "atr_breakout", "tight_range_breakout"],
                "side": ["long", "short", "long"],
                "candidate_status": ["filled", "filtered", "not_triggered"],
                "filtered_reason": ["", "quality_gate", ""],
                "trigger": [3510.0, 3500.0, 3515.0],
                "entry_price": [3510.0, float("nan"), float("nan")],
                "stop_price": [3508.0, 3450.0, 3525.0],
                "future_mfe_atr": [1.00, 0.40, 0.10],
                "future_mae_atr": [0.50, 0.20, 0.30],
                "atr_warmed": [1, 1, 1],
                "feature_close": [3510.0, 3505.0, 3512.0],
                "feature_atr14": [20.0, 21.0, 22.0],
            }
        )

        out = standardize_candidate_events(candidate)
        self.assertEqual(len(out), 3)
        for col in (
            "candidate_id",
            "setup_type",
            "direction",
            "sample_status",
            "block_reason",
            "entry_price_virtual",
            "stop_price_virtual",
            "future_return_atr",
            "atr_pct_at_entry",
            "executed_flag",
            "is_good_opportunity",
            "opportunity_class",
        ):
            self.assertIn(col, out.columns)

        # C4: not_triggered must map to its own status, NOT blocked_by_execution.
        self.assertEqual(
            list(out["sample_status"]),
            ["executed", "filtered_by_rule", "not_triggered_market"],
        )
        self.assertEqual(list(out["executed_flag"]), [1, 0, 0])
        self.assertEqual(out.loc[1, "block_reason"], "quality_gate")
        self.assertEqual(out.loc[2, "block_reason"], "next_bar_not_triggered")
        # C2: when entry_price is NaN, fall back to trigger (3500), NEVER stop_price (3450).
        self.assertAlmostEqual(float(out.loc[1, "entry_price_virtual"]), 3500.0, places=6)
        # W2.1: atr_pct_at_entry = atr14 / entry_price_virtual
        self.assertAlmostEqual(float(out.loc[0, "atr_pct_at_entry"]), 20.0 / 3510.0, places=9)
        self.assertAlmostEqual(float(out.loc[1, "atr_pct_at_entry"]), 21.0 / 3500.0, places=9)
        # 文档要求机会质量标签独立于是否成交，filtered 样本也允许成为好机会。
        self.assertEqual(int(out.loc[1, "is_good_opportunity"]), 1)

    def test_candidate_id_stable_between_candidate_events_and_training_samples(self) -> None:
        """C1: 即便 merge_asof 重排了行序，candidate_events.parquet 与
        training_samples.parquet 的 candidate_id 集合必须完全一致，
        否则下游做 7.1-7.4 四类样本分析无法 join。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["minute60", "minute60", "minute60"],
                # 顺序故意打乱，让 merge_asof 的 sort_values 一定改变行序
                "datetime": pd.to_datetime(
                    ["2019-01-02 11:00:00", "2019-01-02 09:00:00", "2019-01-02 10:00:00"]
                ),
                "signal_type": ["atr_breakout", "donchian_breakout", "tight_range_breakout"],
                "side": ["short", "long", "long"],
                "candidate_status": ["filled", "filled", "filtered"],
                "filtered_reason": ["", "", "trend_filter_failed"],
                "trigger": [3520.0, 3510.0, 3515.0],
                "entry_price": [3520.0, 3510.0, float("nan")],
                "stop_price": [3530.0, 3500.0, 3525.0],
                "future_mfe_atr": [0.8, 0.9, 0.4],
                "future_mae_atr": [0.3, 0.2, 0.3],
                "atr_warmed": [1, 1, 1],
                "feature_close": [3520.0, 3510.0, 3512.0],
            }
        )
        with tempfile.TemporaryDirectory(prefix="cta_candidate_id_stable_") as td:
            root = Path(td)
            feature_root = root / "feature"
            d = feature_root / "minute60" / "RB0"
            d.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(
                        ["2019-01-02 09:00:00", "2019-01-02 10:00:00", "2019-01-02 11:00:00"]
                    ),
                    "sma_20": [1.0, 2.0, 3.0],
                }
            ).to_parquet(d / "2019-01-02.parquet", index=False)
            out = build_and_save_candidate_training_dataset(
                candidate_df=candidate,
                symbol="RB0",
                interval="60min",
                output_root=root / "model_feature",
                feature_root=feature_root,
                run_tag="20260427",
                generic_columns=("sma_20",),
            )
            ev = pd.read_parquet(out.candidate_events_parquet)
            tr = pd.read_parquet(out.training_samples_parquet)
            self.assertEqual(set(ev["candidate_id"]), set(tr["candidate_id"]))
            self.assertEqual(len(ev), len(tr))

    def test_future_return_atr_stores_real_horizon_return_not_score(self) -> None:
        """C5: future_return_atr 应保存 baseline 给的 future_pnl/atr_v 真实
        horizon 收益，而不是 mfe-0.7*mae 的 opportunity score；后者放在
        opportunity_score 列里。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "candidate_status": ["filled"],
                "trigger": [3510.0],
                "entry_price": [3510.0],
                "stop_price": [3508.0],
                "future_mfe_atr": [1.0],
                "future_mae_atr": [0.4],
                "future_pnl_atr": [0.55],  # baseline 真实 horizon return
                "atr_warmed": [1],
                "feature_close": [3510.0],
            }
        )
        out = standardize_candidate_events(candidate)
        # opportunity_score = mfe - 0.7*mae = 1.0 - 0.28 = 0.72
        self.assertAlmostEqual(float(out.iloc[0]["opportunity_score"]), 0.72, places=6)
        # future_return_atr 必须保留 baseline 的真实 horizon return
        self.assertAlmostEqual(float(out.iloc[0]["future_return_atr"]), 0.55, places=6)

    def test_core_cols_include_label_class_and_atr_warmed(self) -> None:
        """C9: label_class（baseline 决策标签）与 atr_warmed（warmup 标志）
        必须作为稳定 schema 的一部分，避免下游模型 schema 校验在新增 trailing
        列时漂移。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "candidate_status": ["filled"],
                "trigger": [3510.0],
                "entry_price": [3510.0],
                "stop_price": [3508.0],
                "future_mfe_atr": [1.0],
                "future_mae_atr": [0.5],
                "label_class": [1],
                "atr_warmed": [1],
                "feature_close": [3510.0],
            }
        )
        out = standardize_candidate_events(candidate)
        cols_first_30 = list(out.columns[:35])
        self.assertIn("label_class", cols_first_30)
        self.assertIn("atr_warmed", cols_first_30)

    def test_standardize_preserves_future_return_atr_when_input_is_unsorted(self) -> None:
        """D2: 旧实现 ``out["future_return_atr"] = pd.to_numeric(candidate_df["future_return_atr"])``
        在 sort_values 重排后会按 index alignment 把别人家的值贴过来。
        此用例故意 datetime 倒序传入，断言 future_return_atr 跟 row identity 绑定不错位。
        """
        # 注意：故意把 datetime 倒序排，让 sort_values 真的会换行序。
        df = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2020-01-02 09:00:00", "2020-01-01 09:00:00"]),
                "setup_type": ["donchian_breakout", "donchian_breakout"],
                "direction": ["long", "short"],
                "atr_warmed": [1, 1],
                "future_mfe_atr": [1.0, 0.5],
                "future_mae_atr": [0.2, 0.3],
                "future_return_atr": [99.0, 11.0],  # row0=99 跟 2020-01-02 绑定，row1=11 跟 2020-01-01 绑定
                "candidate_status": ["filled", "filled"],
            }
        )
        out = standardize_candidate_events(df)
        # 标准化后 out 已按 datetime 升序：先 2020-01-01 (应是 11.0)，后 2020-01-02 (应是 99.0)
        early = out.loc[out["datetime"] == pd.Timestamp("2020-01-01 09:00:00")]
        late = out.loc[out["datetime"] == pd.Timestamp("2020-01-02 09:00:00")]
        self.assertEqual(len(early), 1)
        self.assertEqual(len(late), 1)
        self.assertAlmostEqual(float(early["future_return_atr"].iloc[0]), 11.0)
        self.assertAlmostEqual(float(late["future_return_atr"].iloc[0]), 99.0)


if __name__ == "__main__":
    unittest.main()
