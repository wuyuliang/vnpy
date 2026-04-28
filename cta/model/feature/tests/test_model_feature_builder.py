"""Tests for model feature builder."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.model.feature.training_feature_builder import (
    _iter_feature_files,
    build_training_feature_table,
    load_generic_feature_frame,
    merge_candidate_and_generic_features,
)


class TestModelFeatureBuilder(unittest.TestCase):
    def test_merge_candidate_and_generic_features(self) -> None:
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout", "atr_breakout"],
                "feature_close": [3500.0, 3510.0],
            }
        )
        generic = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "sma_20": [3490.0, 3495.0],
                "rsi_14": [55.0, 58.0],
            }
        )
        out = merge_candidate_and_generic_features(
            candidate_df=candidate,
            generic_df=generic,
            generic_columns=("sma_20", "rsi_14"),
        )
        self.assertEqual(len(out), 2)
        self.assertIn("generic_sma_20", out.columns)
        self.assertIn("generic_rsi_14", out.columns)

    def test_merge_candidate_and_generic_features_with_second_offset(self) -> None:
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:01"]),
                "signal_type": ["donchian_breakout"],
                "feature_close": [3500.0],
            }
        )
        generic = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                "sma_20": [3490.0],
                "rsi_14": [55.0],
            }
        )
        out = merge_candidate_and_generic_features(
            candidate_df=candidate,
            generic_df=generic,
            generic_columns=("sma_20", "rsi_14"),
        )
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out.iloc[0]["generic_sma_20"]), 3490.0)

    def test_load_generic_feature_frame(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_generic_feat_") as td:
            root = Path(td)
            d = root / "minute60" / "RB0"
            d.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                    "sma_20": [3490.0, 3495.0],
                    "rsi_14": [55.0, 58.0],
                }
            )
            df.to_parquet(d / "2019-01-02.parquet", index=False)
            out = load_generic_feature_frame(
                symbol="RB0",
                interval="60min",
                start_date="2019-01-01",
                end_date="2019-01-03",
                feature_root=root,
            )
            self.assertEqual(len(out), 2)
            self.assertIn("sma_20", out.columns)

    def test_build_training_feature_table(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_training_feat_") as td:
            root = Path(td)
            d = root / "minute60" / "RB0"
            d.mkdir(parents=True, exist_ok=True)
            generic = pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                    "sma_20": [3490.0, 3495.0],
                    "rsi_14": [55.0, 58.0],
                }
            )
            generic.to_parquet(d / "2019-01-02.parquet", index=False)
            candidate = pd.DataFrame(
                {
                    "symbol": ["RB0", "RB0"],
                    "exchange": ["SHFE", "SHFE"],
                    "interval": ["minute60", "minute60"],
                    "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                    "signal_type": ["donchian_breakout", "atr_breakout"],
                    "side": ["long", "short"],
                    "label_class": [1, 0],
                    "future_mfe_atr": [1.2, 0.4],
                    "future_mae_atr": [0.5, 1.0],
                    "feature_close": [3500.0, 3510.0],
                    "feature_atr14": [25.0, 24.0],
                }
            )
            out = build_training_feature_table(
                candidate_df=candidate,
                symbol="RB0",
                interval="60min",
                feature_root=root,
                generic_columns=("sma_20", "rsi_14"),
            )
            self.assertEqual(len(out), 2)
            self.assertIn("generic_sma_20", out.columns)
            self.assertIn("generic_rsi_14", out.columns)
            self.assertIn("label_class", out.columns)

    def test_merge_respects_60min_tolerance_returns_nan_when_too_far(self) -> None:
        """T-E: if candidate datetime is later than the most recent generic > tolerance, expect NaN."""
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["minute60"],
                # 90 minutes after generic — beyond 60min tolerance for backward asof
                "datetime": pd.to_datetime(["2019-01-02 11:30:00"]),
                "signal_type": ["donchian_breakout"],
                "feature_close": [3500.0],
            }
        )
        generic = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "sma_20": [3490.0],
                "rsi_14": [55.0],
            }
        )
        out = merge_candidate_and_generic_features(
            candidate_df=candidate,
            generic_df=generic,
            generic_columns=("sma_20", "rsi_14"),
        )
        self.assertEqual(len(out), 1)
        self.assertTrue(pd.isna(out.iloc[0]["generic_sma_20"]))

    def test_iter_feature_files_raise_when_no_file_in_range(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_iter_feature_files_") as td:
            root = Path(td)
            d = root / "minute60" / "RB0"
            d.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"datetime": pd.to_datetime(["2024-01-01 09:00:00"])}).to_parquet(
                d / "2024-01-01.parquet",
                index=False,
            )
            with self.assertRaises(FileNotFoundError):
                _iter_feature_files(
                    symbol="RB0",
                    interval="60min",
                    start_date="2019-01-01",
                    end_date="2019-01-03",
                    feature_root=root,
                )

    # ---------------- B9: 大量文件名解析失败时主动 raise ----------------
    def test_iter_feature_files_raises_when_majority_filenames_invalid(self) -> None:
        """B9: 当 ≥50% 的 parquet 文件名不能被解析为日期，必须主动 raise，
        避免静默丢光所有 generic 特征。
        """
        with tempfile.TemporaryDirectory(prefix="cta_iter_invalid_names_") as td:
            root = Path(td)
            d = root / "minute60" / "RB0"
            d.mkdir(parents=True, exist_ok=True)
            # 4 个文件，其中 3 个非法名（>=50%）
            for name in ("RB0_20240101.parquet", "junk_a.parquet", "junk_b.parquet"):
                pd.DataFrame({"datetime": pd.to_datetime(["2024-01-01 09:00:00"])}).to_parquet(
                    d / name, index=False
                )
            pd.DataFrame({"datetime": pd.to_datetime(["2024-01-01 09:00:00"])}).to_parquet(
                d / "2024-01-01.parquet", index=False
            )
            with self.assertRaises(ValueError) as cm:
                _iter_feature_files(
                    symbol="RB0",
                    interval="60min",
                    start_date="2024-01-01",
                    end_date="2024-01-02",
                    feature_root=root,
                )
            self.assertIn("non-date stems", str(cm.exception))

    # ---------------- B3: build_training_feature_table 多读前一天 lookback ----
    def test_build_training_feature_table_loads_previous_day_for_lookback(self) -> None:
        """B3: 候选样本最早是 2019-01-02 09:00 时，应同时加载 2019-01-01 的 generic 特征文件
        作为跨日 lookback；否则夜盘衔接的样本会拿不到上一日特征。
        """
        with tempfile.TemporaryDirectory(prefix="cta_lookback_") as td:
            root = Path(td)
            d = root / "minute60" / "RB0"
            d.mkdir(parents=True, exist_ok=True)
            # 2019-01-01 文件（前一日 lookback）
            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2019-01-01 21:00:00"]),
                    "sma_20": [3490.0],
                    "rsi_14": [55.0],
                }
            ).to_parquet(d / "2019-01-01.parquet", index=False)
            # 2019-01-02 文件（候选样本所在日）
            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                    "sma_20": [3495.0],
                    "rsi_14": [58.0],
                }
            ).to_parquet(d / "2019-01-02.parquet", index=False)
            # 候选样本最早就是 2019-01-02 09:00
            candidate = pd.DataFrame(
                {
                    "symbol": ["RB0"],
                    "exchange": ["SHFE"],
                    "interval": ["minute60"],
                    "datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                    "signal_type": ["donchian_breakout"],
                    "side": ["long"],
                    "label_class": [1],
                    "future_mfe_atr": [0.5],
                    "future_mae_atr": [0.2],
                    "feature_close": [3500.0],
                    "feature_atr14": [25.0],
                }
            )
            out = build_training_feature_table(
                candidate_df=candidate,
                symbol="RB0",
                interval="60min",
                feature_root=root,
                generic_columns=("sma_20", "rsi_14"),
            )
            self.assertEqual(len(out), 1)
            # 关键断言：generic_sma_20 必须有值（来自 2019-01-02 09:00 这一行的精确匹配）
            self.assertFalse(pd.isna(out.iloc[0]["generic_sma_20"]))
            # 同时验证 _iter_feature_files 在候选起点为 2019-01-02 时能选中 2019-01-01.parquet
            files = _iter_feature_files(
                symbol="RB0",
                interval="60min",
                start_date=str((pd.Timestamp("2019-01-02") - pd.Timedelta(days=1)).date()),
                end_date=str((pd.Timestamp("2019-01-02") + pd.Timedelta(days=1)).date()),
                feature_root=root,
            )
            self.assertIn(d / "2019-01-01.parquet", files)
            self.assertIn(d / "2019-01-02.parquet", files)


if __name__ == "__main__":
    unittest.main()
