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


    # ---------------- G1: 默认 auto-detect 所有数值 generic 列 -----------------
    def test_merge_auto_detects_numeric_generic_columns_when_columns_none(self) -> None:
        """G1: 磁盘 generic parquet 通常有 400+ 列，但旧实现固定用 18 列白名单
        ``DEFAULT_GENERIC_COLUMNS``，95% 特征被丢。修复后 generic_columns=None 时
        自动取所有数值列（排除 OHLCV / 元数据 / 非数值列）。
        """
        from cta.model.feature.training_feature_builder import _auto_detect_generic_columns

        generic = pd.DataFrame(
            {
                # OHLCV + 元数据：必须排除
                "datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                "open": [3500.0],
                "high": [3520.0],
                "low": [3490.0],
                "close": [3510.0],
                "volume": [10000],
                "turnover": [100000.0],
                "open_interest": [50000],
                "ts_code": ["RB0.SHFE"],
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                # 真正的特征列：必须保留
                "sma_20": [3490.0],
                "ema_20": [3495.0],
                "rsi_14": [55.0],
                "atr_14": [12.5],
                "pa_bar_range": [30.0],
                "pa_close_position": [0.7],
                "trend_score": [0.6],
                # 非数值列：必须排除
                "regime_label_text": ["trend_up"],
            }
        )
        cols = _auto_detect_generic_columns(generic)
        # 保留：所有数值特征
        for c in ("sma_20", "ema_20", "rsi_14", "atr_14", "pa_bar_range",
                  "pa_close_position", "trend_score"):
            self.assertIn(c, cols, f"expected {c} kept")
        # 排除：OHLCV / 元数据 / 非数值
        for c in ("datetime", "open", "high", "low", "close", "volume",
                  "turnover", "open_interest", "ts_code", "symbol",
                  "exchange", "regime_label_text"):
            self.assertNotIn(c, cols, f"expected {c} excluded")

    def test_merge_default_uses_all_disk_numeric_features(self) -> None:
        """G1: 不传 generic_columns 时，merge 输出列数应远超过老 18 列白名单。"""
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
            }
        )
        # 模拟磁盘 parquet：50 个数值特征列
        rng_cols = {f"feat_{i}": [float(i) * 1.1] for i in range(50)}
        generic = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                "open": [3500.0],
                "high": [3520.0],
                "low": [3490.0],
                "close": [3510.0],
                "volume": [10000],
                **rng_cols,
            }
        )
        out = merge_candidate_and_generic_features(
            candidate_df=candidate,
            generic_df=generic,
            # 不传 generic_columns → 应自动取所有数值列
        )
        # 50 个 feat_* 必须全部出现在 generic_* 前缀下
        present = [c for c in out.columns if c.startswith("generic_feat_")]
        self.assertEqual(len(present), 50)
        # OHLCV 不应被搬运成 generic_*
        for blocked in ("generic_open", "generic_high", "generic_low",
                        "generic_close", "generic_volume"):
            self.assertNotIn(blocked, out.columns)

    def test_whitelist_mode_still_works_for_back_compat(self) -> None:
        """G1: 显式传 generic_columns=DEFAULT_GENERIC_COLUMNS 仍能锁定 18 列白名单。"""
        from cta.model.feature.training_feature_builder import DEFAULT_GENERIC_COLUMNS

        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                "signal_type": ["donchian_breakout"],
            }
        )
        generic = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2019-01-02 09:00:00"]),
                "sma_20": [3490.0],
                "rsi_14": [55.0],
                "extra_feat": [99.0],   # 不在白名单 → 不会被 merge
            }
        )
        out = merge_candidate_and_generic_features(
            candidate_df=candidate,
            generic_df=generic,
            generic_columns=DEFAULT_GENERIC_COLUMNS,
        )
        self.assertIn("generic_sma_20", out.columns)
        self.assertIn("generic_rsi_14", out.columns)
        self.assertNotIn("generic_extra_feat", out.columns)

    # ---------------- L1: 防 generic 特征 next-bar lookahead leak ----------------
    def test_merge_uses_signal_datetime_to_avoid_next_bar_leak(self) -> None:
        """L1: candidate.datetime 是 entry_bar 时间（i+1），但模型决策时刻是
        signal_bar (i)。merge_asof 必须按 signal_datetime 拼 generic 特征，
        否则会拿到 i+1 时刻的 sma_20 / rsi_14 等，构成 next-bar 穿越。

        实证：把 generic 表里写一个 row_idx 列让来源可追踪。signal bar i=10,
        entry bar i+1=11，期望拼出来的 generic_row_idx == 10（不是 11）。
        """
        n = 100
        dt_seq = pd.date_range("2024-01-02 09:00:00", periods=n, freq="60min")
        generic = pd.DataFrame(
            {
                "datetime": dt_seq,
                "row_idx": range(n),
                "sma_20": [float(x) * 100.0 for x in range(n)],
            }
        )

        # 模拟 candidate scan 输出：signal bar = 10, entry bar = 11
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["minute60"],
                "datetime": [dt_seq[11]],          # entry bar 时间
                "signal_datetime": [dt_seq[10]],   # signal bar 时间（决策时刻）
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
            }
        )
        out = merge_candidate_and_generic_features(
            candidate_df=candidate,
            generic_df=generic,
            generic_columns=("row_idx", "sma_20"),
        )
        self.assertEqual(len(out), 1)
        # 期望拼到 signal bar (i=10) 的 generic 特征，而不是 entry bar (i=11)
        self.assertEqual(int(out.iloc[0]["generic_row_idx"]), 10)
        self.assertAlmostEqual(float(out.iloc[0]["generic_sma_20"]), 1000.0)

    def test_merge_falls_back_to_datetime_when_signal_datetime_missing(self) -> None:
        """L1 兜底：candidate 没有 signal_datetime 时（旧数据 / 第三方 frame），
        仍用 datetime 做 merge key 保持向后兼容。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout"],
            }
        )
        generic = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "sma_20": [3490.0, 3495.0],
            }
        )
        out = merge_candidate_and_generic_features(
            candidate_df=candidate,
            generic_df=generic,
            generic_columns=("sma_20",),
        )
        self.assertEqual(len(out), 1)
        # backward-asof 会拿到 datetime <= 10:00 的最新行 = 10:00
        self.assertAlmostEqual(float(out.iloc[0]["generic_sma_20"]), 3495.0)


if __name__ == "__main__":
    unittest.main()
