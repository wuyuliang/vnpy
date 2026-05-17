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



class TestCandidateTrainingDatasetPart01(unittest.TestCase):
    def test_normalize_intervals_supports_mixed_tokens_and_dedup(self) -> None:
        out = _normalize_intervals(["day,60min", " 30min ", "60min", "15min,5min", "min"])
        self.assertEqual(out, ("day", "60min", "30min", "15min", "5min", "min"))

    def test_load_top_n_symbols_from_ranking_orders_by_research_rank(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_rank_topn_") as td:
            path = Path(td) / "symbols_research_ranking.csv"
            pd.DataFrame(
                {
                    "symbol": ["CU0", "RB0", "AU0", "RB0"],
                    "exchange": ["SHFE", "SHFE", "SHFE", "SHFE"],
                    "research_rank": [3, 1, 2, 99],
                }
            ).to_csv(path, index=False, encoding="utf-8-sig")

            out = _load_top_n_symbols_from_ranking(path, top_n=3)
            self.assertEqual(out, [("RB0", "SHFE"), ("AU0", "SHFE"), ("CU0", "SHFE")])

    def test_build_and_save_candidate_training_dataset_merges_macro_features(self) -> None:
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                "signal_datetime": pd.to_datetime(["2019-01-02 08:00:00"]),
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "candidate_status": ["filled"],
                "filtered_reason": [""],
                "trigger": [3510.0],
                "entry_price": [3510.0],
                "stop_price": [3508.0],
                "future_mfe_atr": [0.9],
                "future_mae_atr": [0.2],
                "atr_warmed": [1],
                "feature_close": [3510.0],
                "feature_atr14": [20.0],
            }
        )

        with tempfile.TemporaryDirectory(prefix="cta_candidate_macro_") as td:
            root = Path(td)
            feature_root = root / "feature"
            generic_dir = feature_root / "minute60" / "RB0"
            generic_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2019-01-02 08:00:00"]),
                    "sma_20": [3490.0],
                }
            ).to_parquet(generic_dir / "2019-01-02.parquet", index=False)

            macro_path = root / "feature" / "macro" / "macro_daily.parquet"
            macro_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(["2019-01-02"]),
                    "macro_sse_ret_5d": [0.0123],
                    "macro_csi300_vol_20d": [0.0234],
                }
            ).to_parquet(macro_path, index=False)

            out = build_and_save_candidate_training_dataset(
                candidate_df=candidate,
                symbol="RB0",
                interval="60min",
                output_root=root / "model_feature",
                feature_root=feature_root,
                run_tag="20260427",
                generic_columns=("sma_20",),
                enable_macro_features=True,
                macro_feature_path=macro_path,
            )

            merged = pd.read_parquet(out.training_samples_parquet)
            self.assertIn("candidate_trade_date", merged.columns)
            self.assertIn("macro_sse_ret_5d", merged.columns)
            self.assertIn("macro_csi300_vol_20d", merged.columns)
            self.assertEqual(str(merged.iloc[0]["candidate_trade_date"]), "2019-01-02")
            self.assertAlmostEqual(float(merged.iloc[0]["macro_sse_ret_5d"]), 0.0123, places=9)

    def test_is_good_opportunity_unknown_when_atr_not_warmed(self) -> None:
        """C3: atr_warmed=0 的 warmup 期样本 mfe/mae 不可信（atr_v 由 high-low
        兜底，会大幅高估机会质量）。is_good_opportunity 必须为 0 且
        opportunity_class 必须标记为 U（unknown），future_return_atr 应保留为 NaN。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "candidate_status": ["filled", "filled"],
                "filtered_reason": ["", ""],
                "trigger": [3510.0, 3520.0],
                "entry_price": [3510.0, 3520.0],
                "stop_price": [3508.0, 3518.0],
                # 两行 future_mfe/mae 公式上看都是好机会
                "future_mfe_atr": [3.0, 3.0],
                "future_mae_atr": [0.1, 0.1],
                "atr_warmed": [0, 1],  # row 0 处于 warmup 期
                "feature_close": [3510.0, 3520.0],
            }
        )
        out = standardize_candidate_events(candidate)
        self.assertEqual(int(out.iloc[0]["is_good_opportunity"]), 0)
        self.assertEqual(str(out.iloc[0]["opportunity_class"]), "U")
        self.assertTrue(pd.isna(out.iloc[0]["opportunity_score"]))
        # row 1 atr_warmed=1，应当判好
        self.assertEqual(int(out.iloc[1]["is_good_opportunity"]), 1)
        self.assertNotEqual(str(out.iloc[1]["opportunity_class"]), "U")

    def test_standardize_raises_on_duplicate_primary_key(self) -> None:
        """C7: (symbol, interval, datetime, setup_type, direction) 是候选事件
        的业务自然主键，重复说明上游 ETL 出问题了，必须 raise 而不是用
        seq 兜底掩盖。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00", "2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "candidate_status": ["filled", "filled"],
                "trigger": [3510.0, 3510.0],
                "entry_price": [3510.0, 3510.0],
                "stop_price": [3508.0, 3508.0],
                "future_mfe_atr": [1.0, 1.0],
                "future_mae_atr": [0.5, 0.5],
                "atr_warmed": [1, 1],
                "feature_close": [3510.0, 3510.0],
            }
        )
        with self.assertRaises(ValueError):
            standardize_candidate_events(candidate)

    def test_block_reason_treats_pdNA_and_none_string_as_missing(self) -> None:
        """D5: 旧实现只 ``replace({"nan": ""})``，pd.NA → "<NA>" 字符串、
        Python None → "None" 字符串都漏网。修复后这些都被识别为缺失，
        会回退到状态默认 reason（如 ``filtered_by_rule``）。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["minute60", "minute60", "minute60"],
                "datetime": pd.to_datetime(
                    ["2019-01-02 09:00:00", "2019-01-02 10:00:00", "2019-01-02 11:00:00"]
                ),
                "setup_type": [
                    "donchian_breakout",
                    "donchian_breakout",
                    "donchian_breakout",
                ],
                "direction": ["long", "long", "long"],
                "candidate_status": ["filtered", "filtered", "filtered"],
                # 三种"伪缺失"形态：pd.NA / Python None / 字符串 "None"
                "block_reason": pd.Series([pd.NA, None, "None"], dtype="object"),
                "atr_warmed": [1, 1, 1],
                "future_mfe_atr": [0.0, 0.0, 0.0],
                "future_mae_atr": [0.0, 0.0, 0.0],
            }
        )
        out = standardize_candidate_events(candidate)
        # filtered_by_rule 状态下 block_reason 缺失必须回退到默认值 "filtered_by_rule"
        for reason in out["block_reason"].astype(str).tolist():
            self.assertEqual(reason, "filtered_by_rule")


if __name__ == "__main__":
    unittest.main()
