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



class TestCandidateTrainingDatasetPart03(unittest.TestCase):
    def test_parse_args_supports_top_n_symbols_and_multi_intervals(self) -> None:
        args = _parse_args(
            [
                "--top-n-symbols",
                "3",
                "--symbols-ranking-path",
                "cta/feature/symbols_research_ranking.csv",
                "--interval",
                "day,60min",
                "30min,15min",
                "--trade-side-mode",
                "both",
            ]
        )
        self.assertEqual(int(args.top_n_symbols), 3)
        self.assertEqual(
            tuple(args.interval),
            ("day,60min", "30min,15min"),
        )
        self.assertEqual(str(args.symbols_ranking_path), "cta/feature/symbols_research_ranking.csv")

    def test_build_and_save_candidate_training_dataset_merges_generic_features(self) -> None:
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "signal_datetime": pd.to_datetime(["2019-01-02 08:00:00", "2019-01-02 09:00:00"]),
                "signal_type": ["donchian_breakout", "atr_breakout"],
                "side": ["long", "short"],
                "candidate_status": ["filled", "filtered"],
                "filtered_reason": ["", "trend_filter_failed"],
                "trigger": [3510.0, 3500.0],
                "entry_price": [3510.0, float("nan")],
                "stop_price": [3508.0, 3450.0],
                "future_mfe_atr": [0.9, 0.4],
                "future_mae_atr": [0.2, 0.3],
                "atr_warmed": [1, 1],
                "feature_close": [3510.0, 3505.0],
                "feature_atr14": [20.0, 21.0],
            }
        )

        with tempfile.TemporaryDirectory(prefix="cta_candidate_ds_") as td:
            root = Path(td)
            feature_root = root / "feature"
            generic_dir = feature_root / "minute60" / "RB0"
            generic_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                    "sma_20": [3490.0, 3495.0],
                    "rsi_14": [55.0, 58.0],
                }
            ).to_parquet(generic_dir / "2019-01-02.parquet", index=False)

            out = build_and_save_candidate_training_dataset(
                candidate_df=candidate,
                symbol="RB0",
                interval="60min",
                output_root=root / "model_feature",
                feature_root=feature_root,
                run_tag="20260427",
                generic_columns=("sma_20", "rsi_14"),
            )

            self.assertTrue(out.candidate_events_parquet.exists())
            self.assertTrue(out.training_samples_parquet.exists())
            self.assertTrue(out.summary_parquet.exists())
            self.assertEqual(list(out.dataset_dir.glob("*.csv")), [])

            merged = pd.read_parquet(out.training_samples_parquet)
            self.assertEqual(len(merged), 2)
            self.assertIn("feature_close", merged.columns)
            self.assertIn("generic_sma_20", merged.columns)
            self.assertIn("generic_rsi_14", merged.columns)
            self.assertIn("sample_status", merged.columns)

    def test_entry_price_virtual_falls_back_to_trigger_not_stop_price(self) -> None:
        """C2: 当 entry_price 缺失时，必须用 trigger（突破/触发价）作兜底，
        不能用 stop_price（止损价）。后者在 limit-order/ATR breakout 场景
        与触发价完全不同，会污染未来标签。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_type": ["atr_breakout"],
                "side": ["short"],
                "candidate_status": ["filtered"],
                "filtered_reason": ["quality_gate"],
                "trigger": [3500.0],
                "entry_price": [float("nan")],
                "stop_price": [3450.0],  # 故意离 trigger 50 点远
                "future_mfe_atr": [0.4],
                "future_mae_atr": [0.2],
                "atr_warmed": [1],
                "feature_close": [3505.0],
            }
        )
        out = standardize_candidate_events(candidate)
        self.assertAlmostEqual(float(out.iloc[0]["entry_price_virtual"]), 3500.0, places=6)
        self.assertNotAlmostEqual(float(out.iloc[0]["entry_price_virtual"]), 3450.0, places=6)

    def test_block_reason_treats_string_nan_as_missing(self) -> None:
        """C6: parquet round-trip 后 NaN 会变成字符串 'nan'，必须当作缺失处理，
        否则 block_reason 会被错误填成 'nan'。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout", "atr_breakout"],
                "side": ["long", "long"],
                "candidate_status": ["filled", "filtered"],
                # row 0 不该有 reason；row 1 是 filtered 但 reason 是字符串 'nan'
                "filtered_reason": ["nan", "nan"],
                "trigger": [3510.0, 3500.0],
                "entry_price": [3510.0, float("nan")],
                "stop_price": [3508.0, 3450.0],
                "future_mfe_atr": [1.0, 0.4],
                "future_mae_atr": [0.5, 0.2],
                "atr_warmed": [1, 1],
                "feature_close": [3510.0, 3505.0],
            }
        )
        out = standardize_candidate_events(candidate)
        # executed 行的 block_reason 必须空
        self.assertEqual(out.iloc[0]["block_reason"], "")
        # filtered 行的 reason 是 'nan'，应当回退到 status 默认值，而不是写 'nan'
        self.assertNotEqual(out.iloc[1]["block_reason"], "nan")
        self.assertEqual(out.iloc[1]["block_reason"], "filtered_by_rule")

    def test_empty_candidate_df_writes_schema_complete_empty_parquet(self) -> None:
        """C10: 空候选输入也要能落盘含核心列的空 parquet，避免下游读取
        时缺失 sample_status / candidate_id 等列。
        """
        empty = pd.DataFrame()
        with tempfile.TemporaryDirectory(prefix="cta_empty_candidate_ds_") as td:
            out = build_and_save_candidate_training_dataset(
                candidate_df=empty,
                symbol="RB0",
                interval="60min",
                output_root=Path(td) / "model_feature",
                run_tag="20260427",
            )
            ev = pd.read_parquet(out.candidate_events_parquet)
            self.assertEqual(len(ev), 0)
            self.assertTrue(out.summary_parquet.exists())
            self.assertEqual(list(out.dataset_dir.glob("*.csv")), [])
            for col in (
                "candidate_id",
                "sample_status",
                "block_reason",
                "is_good_opportunity",
                "opportunity_class",
                "future_return_atr",
                "executed_flag",
            ):
                self.assertIn(col, ev.columns)


if __name__ == "__main__":
    unittest.main()
