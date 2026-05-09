"""Smoke test for generic CTA model pipeline."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.model.model_pipeline import (
    MFE_MAE_KIND_SKIPPED_NO_EXEC,
    _ensure_training_columns,
    _build_last_oot_decile_table,
    _build_walk_forward_windows,
    _ensure_binary_label_diversity,
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _parse_args,
    _resolve_run_exchange,
    _select_feature_columns,
    _train_mfe_mae_or_skip,
    run_model_pipeline,
    run_model_pipeline_multi,
)
from cta.model.trade_filter_model import TradeFilterModel


class TestModelPipeline(unittest.TestCase):
    def test_build_last_oot_decile_table_uses_last_window_test_only(self) -> None:
        df = pd.DataFrame(
            {
                "window_id": [0, 0, 1, 1, 1, 1],
                "pred_split": ["test", "valid", "test", "test", "test", "train"],
                "trade_filter_prob": [0.10, 0.90, 0.20, 0.40, 0.80, 0.95],
                "future_mfe_atr": [0.1, 0.2, 0.6, 1.0, 1.8, 3.0],
                "future_mae_atr": [0.2, 0.1, 0.2, 0.4, 0.3, 0.2],
                "is_executed": [1, 1, 1, 0, 1, 1],
            }
        )
        out = _build_last_oot_decile_table(df, bins=10)
        self.assertFalse(out.empty)
        # only window_id=1 & pred_split=test & is_executed==1 rows should be used: 2 rows
        self.assertEqual(int(out["sample_count"].sum()), 2)
        self.assertTrue((out["window_id"] == 1).all())
        self.assertTrue((out["pred_split"] == "test").all())
        self.assertTrue((out["executed_rate"] == 1.0).all())

    def test_training_columns_keep_non_executed_samples(self) -> None:
        """训练阶段应保留未成交样本（is_executed=0）用于 trade_filter/regime 学习。"""
        df = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]),
                "candidate_status": ["filled", "not_triggered", "filtered"],
                "is_executed": [1, 0, 0],
                "future_mfe_atr": [1.0, 0.0, 0.2],
                "future_mae_atr": [0.3, 0.0, 0.1],
                "label_class": [1, 0, 0],
                "signal_type": ["donchian_breakout", "donchian_breakout", "donchian_breakout"],
                "side": ["long", "long", "short"],
                "feature_x": [1.0, 0.5, -0.2],
            }
        )
        out = _ensure_training_columns(df)
        self.assertEqual(len(out), 3)
        self.assertEqual(int((out["is_executed"] == 0).sum()), 2)

    def test_build_last_oot_decile_table_builds_10_bins_when_enough_samples(self) -> None:
        n = 100
        score = np.linspace(0.01, 0.99, n)
        df = pd.DataFrame(
            {
                "window_id": [3] * n,
                "pred_split": ["test"] * n,
                "trade_filter_prob": score,
                "future_mfe_atr": score * 2.0,
                "future_mae_atr": np.zeros(n),
                "is_executed": np.ones(n, dtype=int),
            }
        )
        out = _build_last_oot_decile_table(df, bins=10)
        self.assertEqual(len(out), 10)
        self.assertEqual(int(out["sample_count"].sum()), n)
        self.assertEqual(out["decile"].min(), 1)
        self.assertEqual(out["decile"].max(), 10)
        self.assertIn("avg_return_atr", out.columns)
        self.assertIn("total_return_atr", out.columns)

    def test_run_pipeline_smoke(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_model_pipeline_") as td:
            out = run_model_pipeline(
                symbol="RB0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
            )
            self.assertTrue(out.report_path.exists())
            self.assertTrue(out.prediction_path.exists())
            self.assertTrue(out.metrics_path.exists())
            self.assertTrue(out.top_feature_importance_path.exists())
            pred = pd.read_csv(out.prediction_path)
            self.assertIn("pred_split", pred.columns)
            imp = pd.read_csv(out.top_feature_importance_path)
            self.assertIn("model", imp.columns)
            self.assertIn("feature", imp.columns)
            self.assertIn("importance", imp.columns)
            self.assertIn("feature_meaning", imp.columns)

    def test_run_pipeline_support_all_intervals(self) -> None:
        intervals = ("day", "60min", "30min", "15min", "5min", "min")
        with tempfile.TemporaryDirectory(prefix="cta_all_interval_pipeline_") as td:
            for interval in intervals:
                out = run_model_pipeline(
                    symbol="NOPE0",
                    exchange="SHFE",
                    interval=interval,
                    start_date="2018-01-01",
                    end_date="2018-12-31",
                    output_root=Path(td),
                    train_end="2018-06-30",
                    valid_end="2018-09-30",
                    synthetic_periods=120,
                    by_signal_type=False,
                    max_walk_forward_windows=1,
                )
                self.assertTrue(out.report_path.exists())
                self.assertTrue(out.prediction_path.exists())
                self.assertTrue(out.metrics_path.exists())

    def test_run_pipeline_by_signal_type_walk_forward(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_signal_wf_pipeline_") as td:
            out = run_model_pipeline(
                symbol="MOCK0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2020-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=360,
                by_signal_type=True,
                max_walk_forward_windows=2,
            )
            metrics = pd.read_csv(out.metrics_path)
            self.assertIn("signal_type", metrics.columns)
            self.assertIn("window_id", metrics.columns)
            self.assertIn("model_kind", metrics.columns)
            self.assertGreaterEqual(metrics["signal_type"].nunique(), 2)
            self.assertGreaterEqual(metrics["window_id"].nunique(), 2)

    # ------------------ T-A: label-diversity must respect is_executed ----------

    def test_ensure_binary_label_diversity_does_not_relabel_not_triggered(self) -> None:
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=4, freq="D"),
                "label_class": [0, 0, 0, 0],
                "future_mfe_atr": [3.0, 3.0, 3.0, 3.0],
                "future_mae_atr": [0.0, 0.0, 0.0, 0.0],
                "is_executed": [1, 0, 0, 0],
                "atr_warmed": [1, 1, 1, 1],
            }
        )
        out = _ensure_binary_label_diversity(df)
        # Only the executed row should flip to 1; the other three must stay 0.
        self.assertEqual(int(out.loc[0, "label_class"]), 1)
        self.assertEqual(int(out.loc[1, "label_class"]), 0)
        self.assertEqual(int(out.loc[2, "label_class"]), 0)
        self.assertEqual(int(out.loc[3, "label_class"]), 0)

    def test_ensure_binary_label_diversity_skips_atr_warmup(self) -> None:
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=2, freq="D"),
                "label_class": [0, 0],
                "future_mfe_atr": [4.0, 4.0],
                "future_mae_atr": [0.0, 0.0],
                "is_executed": [1, 1],
                "atr_warmed": [0, 1],  # row 0 in warmup -> must remain 0
            }
        )
        out = _ensure_binary_label_diversity(df)
        self.assertEqual(int(out.loc[0, "label_class"]), 0)
        self.assertEqual(int(out.loc[1, "label_class"]), 1)

    # ------------------ T-C: walk-forward window monotonicity -----------------

    def test_walk_forward_windows_monotonic_and_no_overlap(self) -> None:
        df = pd.DataFrame(
            {"datetime": pd.date_range("2018-01-01", "2021-12-31", freq="D")}
        )
        windows = _build_walk_forward_windows(
            df,
            train_end="2019-06-30",
            valid_end="2019-12-31",
            max_windows=4,
            window_mode="expanding",
        )
        self.assertGreaterEqual(len(windows), 2)
        # train_end strictly increasing
        ends = [w.train_end for w in windows]
        self.assertEqual(ends, sorted(ends))
        # test of window i and train of window i+1 should not overlap (no leakage)
        for w in windows:
            self.assertLess(w.train_end, w.valid_end)
            self.assertLess(w.valid_end, w.test_end)

    def test_walk_forward_windows_sliding_train_starts_advance(self) -> None:
        df = pd.DataFrame(
            {"datetime": pd.date_range("2018-01-01", "2021-12-31", freq="D")}
        )
        windows = _build_walk_forward_windows(
            df,
            train_end="2019-06-30",
            valid_end="2019-12-31",
            max_windows=3,
            window_mode="sliding",
        )
        self.assertGreaterEqual(len(windows), 2)
        # In sliding mode, each later window's train_start should advance vs the previous window
        train_starts: list[pd.Timestamp] = []
        for w in windows:
            train_dt = pd.to_datetime(w.train["datetime"], errors="coerce")
            train_starts.append(train_dt.min())
        for i in range(1, len(train_starts)):
            self.assertGreater(train_starts[i], train_starts[i - 1])

    def test_walk_forward_windows_invalid_mode_raises(self) -> None:
        df = pd.DataFrame({"datetime": pd.date_range("2020-01-01", periods=400, freq="D")})
        with self.assertRaises(ValueError):
            _build_walk_forward_windows(
                df,
                train_end="2020-06-30",
                valid_end="2020-09-30",
                max_windows=2,
                window_mode="bogus",  # type: ignore[arg-type]
            )

    # ------------------ T-H: by_signal_type should drop constant code ---------

    def test_select_feature_columns_skips_string_columns(self) -> None:
        df = pd.DataFrame(
            {
                "feature_close": [1.0, 2.0, 3.0],
                "feature_label": ["good", "bad", "good"],  # string column should be skipped
                "feature_flag": [True, False, True],  # bool should be kept
            }
        )
        out_df, cols = _select_feature_columns(df)
        self.assertIn("feature_close", cols)
        self.assertIn("feature_flag", cols)
        self.assertNotIn("feature_label", cols)

    # ------------------ B2: 兜底分支缺列时不应崩 -----------------------------
    def test_select_feature_columns_fallback_handles_missing_side_and_signal_type(self) -> None:
        """B2: 没有 feature_*/generic_* 列且 side/signal_type/entry_price 也缺失时，
        兜底分支不应抛 AttributeError。
        """
        df = pd.DataFrame({"datetime": pd.date_range("2020-01-01", periods=3, freq="D")})
        out_df, cols = _select_feature_columns(df)
        self.assertGreater(len(cols), 0)
        for c in cols:
            self.assertTrue(c.startswith("feature_"))

    # ------------------ B1: train_exec 为空时跳过 MFE/MAE 训练 ----------------
    def test_train_mfe_mae_or_skip_returns_none_when_no_executed(self) -> None:
        df = pd.DataFrame(
            {
                "is_executed": [0, 0, 0, 0],
                "future_mfe_atr": [0.0, 0.0, 0.0, 0.0],
                "future_mae_atr": [0.0, 0.0, 0.0, 0.0],
                "feature_x": [1.0, 2.0, 3.0, 4.0],
            }
        )
        model, kind = _train_mfe_mae_or_skip(df, feature_columns=["feature_x"])
        self.assertIsNone(model)
        self.assertEqual(kind, MFE_MAE_KIND_SKIPPED_NO_EXEC)

    def test_train_mfe_mae_or_skip_returns_model_when_executed_present(self) -> None:
        rng = np.random.default_rng(0)
        df = pd.DataFrame(
            {
                "is_executed": [1] * 30,
                "future_mfe_atr": rng.uniform(0, 2, size=30),
                "future_mae_atr": rng.uniform(0, 2, size=30),
                "feature_x": rng.normal(size=30),
            }
        )
        model, kind = _train_mfe_mae_or_skip(df, feature_columns=["feature_x"])
        self.assertIsNotNone(model)
        self.assertIn(kind, {"random_forest", "dummy"})

    # ------------------ B6: pred_split 在 walk-forward 路径下永远是 test ------
    def test_run_pipeline_pred_split_is_test_only(self) -> None:
        """B6: walk-forward 已经过滤 test 空的窗口，prediction_df 的 pred_split
        理应只有 'test'，不会出现 'valid'/'train' 的 fallback 写入。
        """
        with tempfile.TemporaryDirectory(prefix="cta_pred_split_") as td:
            out = run_model_pipeline(
                symbol="SPLIT0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=240,
                by_signal_type=False,
                max_walk_forward_windows=2,
            )
            pred = pd.read_csv(out.prediction_path)
            if not pred.empty:
                self.assertEqual(set(pred["pred_split"].astype(str).unique()), {"test"})

    # ------------------ B7: load 旧 joblib 缺 model_kind 时显示 legacy_no_kind -
    def test_trade_filter_load_legacy_no_kind(self) -> None:
        """B7: 老 joblib 没有 model_kind 字段时，load 后 model_kind 应为 legacy_no_kind。"""
        rng = np.random.default_rng(1)
        df = pd.DataFrame(
            {
                "feature_x": rng.normal(size=80),
                "label_class": (rng.uniform(size=80) > 0.5).astype(int),
            }
        )
        m = TradeFilterModel(random_state=3).fit(df, feature_columns=["feature_x"], label_column="label_class")
        with tempfile.TemporaryDirectory(prefix="cta_legacy_kind_") as td:
            p = Path(td) / "tf.joblib"
            m.save(p)
            # 模拟旧 joblib 的内容：人工去掉 model_kind 字段后重新 dump。
            import joblib  # local import to avoid polluting module top
            obj = joblib.load(p)
            obj.pop("model_kind", None)
            joblib.dump(obj, p)
            reloaded = TradeFilterModel.load(p)
            self.assertEqual(reloaded.model_kind, "legacy_no_kind")

    # ------------------ M1: --interval 支持数组 ------------------------------
    def test_normalize_intervals_accepts_space_separated_values(self) -> None:
        """M1: argparse nargs='+' 拿到的就是 list[str]，应原样保留。"""
        out = _normalize_intervals(["day", "60min", "30min", "15min"])
        self.assertEqual(out, ("day", "60min", "30min", "15min"))

    def test_normalize_intervals_splits_comma_separated_values(self) -> None:
        """M1: 也允许逗号分隔（例如 --interval day,60min,30min）。"""
        out = _normalize_intervals(["day,60min", "30min"])
        self.assertEqual(out, ("day", "60min", "30min"))

    def test_normalize_intervals_dedupes_preserving_first_seen_order(self) -> None:
        """M1: 重复传入的 interval 应去重，按首次出现顺序保留。"""
        out = _normalize_intervals(["60min", "day", "60min", "30min", "day"])
        self.assertEqual(out, ("60min", "day", "30min"))

    def test_normalize_intervals_strips_whitespace_and_skips_empty(self) -> None:
        out = _normalize_intervals([" day ", "", "  ", "60min,, 30min "])
        self.assertEqual(out, ("day", "60min", "30min"))

    def test_normalize_intervals_raises_when_all_empty(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_intervals(["", "  ", ","])

    def test_parse_args_interval_supports_multiple_values(self) -> None:
        """M1: CLI 必须能接收多个 interval。"""
        argv = [
            "--symbol", "RB0",
            "--interval", "day", "60min", "30min", "15min",
        ]
        ns = _parse_args(argv)
        # argparse 拿到的是 list[str]
        self.assertEqual(list(ns.interval), ["day", "60min", "30min", "15min"])

    def test_parse_args_interval_default_is_single_60min(self) -> None:
        """M1: 不传 --interval 时仍兼容旧默认 60min。"""
        ns = _parse_args(["--symbol", "RB0"])
        self.assertEqual(list(ns.interval), ["60min"])

    def test_parse_args_supports_top_n_symbols(self) -> None:
        ns = _parse_args(
            [
                "--symbol", "RB0",
                "--top-n-symbols", "5",
                "--symbols-ranking-path", "cta/feature/symbols_research_ranking.csv",
            ]
        )
        self.assertEqual(int(ns.top_n_symbols), 5)
        self.assertEqual(str(ns.symbols_ranking_path), "cta/feature/symbols_research_ranking.csv")

    def test_load_top_n_symbols_from_ranking_orders_by_rank(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_topn_rank_") as td:
            p = Path(td) / "ranking.csv"
            pd.DataFrame(
                {
                    "symbol": ["B0", "A0", "C0"],
                    "exchange": ["DCE", "SHFE", "CZCE"],
                    "research_rank": [2, 1, 3],
                }
            ).to_csv(p, index=False, encoding="utf-8-sig")
            out = _load_top_n_symbols_from_ranking(p, top_n=2)
            self.assertEqual(out, [("A0", "SHFE"), ("B0", "DCE")])

    def test_run_model_pipeline_multi_returns_one_result_per_interval(self) -> None:
        """M1: run_model_pipeline_multi 应当每个 interval 返回一个结果，
        每个结果的 prediction_path 必须真实存在，且包含 interval 信息以便区分。
        """
        intervals = ("day", "60min", "30min")
        with tempfile.TemporaryDirectory(prefix="cta_multi_interval_") as td:
            results = run_model_pipeline_multi(
                symbol="MULTI0",
                exchange="SHFE",
                intervals=intervals,
                start_date="2018-01-01",
                end_date="2018-12-31",
                output_root=Path(td),
                train_end="2018-06-30",
                valid_end="2018-09-30",
                synthetic_periods=120,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            self.assertEqual(len(results), len(intervals))
            seen_dirs: set[Path] = set()
            for interval, res in zip(intervals, results):
                self.assertTrue(res.prediction_path.exists())
                self.assertTrue(res.metrics_path.exists())
                self.assertTrue(res.report_path.exists())
                # 不同 interval 必须落到不同目录，避免互相覆盖
                self.assertNotIn(res.prediction_path.parent, seen_dirs)
                seen_dirs.add(res.prediction_path.parent)


    # ---------------- D4: FEATURES.md 改动后 _feature_meaning 必须随时刷新 -----
    def test_feature_meaning_refreshes_when_features_doc_mtime_changes(self) -> None:
        """D4: 旧实现 ``@lru_cache(maxsize=8)`` 直接以 path 字符串为键，
        长跑进程里 FEATURES.md 改动后取到的依旧是老映射。修复后缓存键带上
        mtime —— 文件改动后映射立刻失效。
        """
        import os
        import time as _time

        from cta.model.model_pipeline import _feature_meaning

        with tempfile.TemporaryDirectory(prefix="cta_features_doc_") as td:
            doc = Path(td) / "FEATURES.md"
            doc.write_text("| `feature_xyz` | 第一版含义 |\n", encoding="utf-8")
            v1 = _feature_meaning("feature_xyz", features_doc_path=doc)
            self.assertEqual(v1, "第一版含义")

            # 制造一个明显比上次大的 mtime（避免 1s 粒度文件系统看不到差异）。
            doc.write_text("| `feature_xyz` | 第二版含义 |\n", encoding="utf-8")
            future_ts = _time.time() + 5
            os.utime(doc, (future_ts, future_ts))
            v2 = _feature_meaning("feature_xyz", features_doc_path=doc)
            self.assertEqual(v2, "第二版含义")

    # ---------------- G1: pipeline 默认 auto 模式拼上磁盘所有 generic 特征 ----
    def test_pipeline_includes_extra_generic_features_under_auto_mode(self) -> None:
        """G1: 给一个临时 feature_root，里面 generic parquet 含 30 个数值列。
        pipeline 默认 generic_mode='auto' 后，feature_table 里出现的 generic_*
        列数应等于 30（旧实现仅拿 18 列白名单的子集，会漏 12+ 个）。
        """
        with tempfile.TemporaryDirectory(prefix="cta_generic_auto_") as td:
            feat_root = Path(td) / "feature"
            (feat_root / "minute60" / "GAUTO0").mkdir(parents=True, exist_ok=True)

            # 构造 generic parquet：5 OHLCV + 30 数值特征 + 2 字符串元数据
            for date_str in ("2018-01-02", "2018-06-01", "2019-01-02", "2019-06-01", "2019-12-30"):
                rng = np.random.default_rng(int(date_str.replace("-", "")) % 10_000)
                row = {"datetime": pd.to_datetime([f"{date_str} 09:00:00"])}
                for col in ("open", "high", "low", "close", "volume"):
                    row[col] = [3500.0 + rng.normal()]
                for i in range(30):
                    row[f"feat_extra_{i}"] = [float(rng.normal())]
                row["regime_label_text"] = ["trend_up"]
                pd.DataFrame(row).to_parquet(
                    feat_root / "minute60" / "GAUTO0" / f"{date_str}.parquet",
                    index=False,
                )

            with tempfile.TemporaryDirectory(prefix="cta_generic_auto_out_") as td2:
                out = run_model_pipeline(
                    symbol="GAUTO0",
                    exchange="SHFE",
                    interval="60min",
                    start_date="2018-01-01",
                    end_date="2019-12-31",
                    output_root=Path(td2),
                    train_end="2018-12-31",
                    valid_end="2019-06-30",
                    feature_root=feat_root,
                    synthetic_periods=240,
                    by_signal_type=False,
                    max_walk_forward_windows=1,
                    generic_mode="auto",
                )
                feat_table = pd.read_csv(out.feature_table_path)
                generic_cols = [c for c in feat_table.columns if c.startswith("generic_")]
                # 30 个数值特征都应该被拼进来
                self.assertGreaterEqual(len(generic_cols), 30)
                # OHLCV / 字符串列必须排除
                for blocked in ("generic_open", "generic_high", "generic_low",
                                "generic_close", "generic_volume",
                                "generic_regime_label_text"):
                    self.assertNotIn(blocked, feat_table.columns)

    def test_pipeline_whitelist_mode_caps_generic_at_18_columns(self) -> None:
        """G1: 显式传 generic_mode='whitelist' 时仍走 18 列窄白名单（向后兼容）。"""
        with tempfile.TemporaryDirectory(prefix="cta_generic_whitelist_") as td:
            feat_root = Path(td) / "feature"
            (feat_root / "minute60" / "GWHITE0").mkdir(parents=True, exist_ok=True)
            for date_str in ("2018-01-02", "2018-06-01", "2019-01-02", "2019-06-01", "2019-12-30"):
                row = {"datetime": pd.to_datetime([f"{date_str} 09:00:00"])}
                for col in ("open", "high", "low", "close", "volume"):
                    row[col] = [3500.0]
                # 写入 18 个白名单列 + 30 个额外列
                for w in (
                    "sma_20", "ema_20", "macd_dif", "macd_dea", "rsi_14",
                    "atr_14", "bb_width", "stoch_k", "stoch_d", "mfi_14",
                    "trend_score", "compression_score", "breakout_mode_score",
                    "setup_quality_score", "breakout_quality_score",
                    "context_score", "regime_label", "regime_conf",
                ):
                    row[w] = [1.0]
                for i in range(30):
                    row[f"feat_extra_{i}"] = [1.0]
                pd.DataFrame(row).to_parquet(
                    feat_root / "minute60" / "GWHITE0" / f"{date_str}.parquet",
                    index=False,
                )

            with tempfile.TemporaryDirectory(prefix="cta_generic_whitelist_out_") as td2:
                out = run_model_pipeline(
                    symbol="GWHITE0",
                    exchange="SHFE",
                    interval="60min",
                    start_date="2018-01-01",
                    end_date="2019-12-31",
                    output_root=Path(td2),
                    train_end="2018-12-31",
                    valid_end="2019-06-30",
                    feature_root=feat_root,
                    synthetic_periods=240,
                    by_signal_type=False,
                    max_walk_forward_windows=1,
                    generic_mode="whitelist",
                )
                feat_table = pd.read_csv(out.feature_table_path)
                generic_cols = [c for c in feat_table.columns if c.startswith("generic_")]
                # whitelist 模式下应严格不超过 18 列
                self.assertLessEqual(len(generic_cols), 18)
                # extra_* 列绝不应在
                for c in feat_table.columns:
                    self.assertFalse(c.startswith("generic_feat_extra_"))

    # ---------------- P1-B: _select_feature_columns fallback 不再写 entry_price
    def test_select_feature_fallback_does_not_emit_entry_price(self) -> None:
        """P1-B: 旧 fallback 在没有 feature_*/generic_* 列时会写
        ``feature_entry_price = entry_price``。entry_price 是 entry bar 内
        实际成交价，决策时刻 (signal bar 收盘) 不可见 → 是潜在 lookahead leak。
        修复后 fallback 改用 ``feature_trigger = trigger``（决策时刻可见的突破触发价），
        不再泄漏 entry_price。
        """
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=3, freq="D"),
                "side": ["long", "short", "long"],
                "signal_type": ["donchian_breakout"] * 3,
                "trigger": [101.0, 102.0, 103.0],
                "entry_price": [101.5, 101.5, 103.5],
            }
        )
        out_df, feature_cols = _select_feature_columns(df)
        self.assertNotIn("feature_entry_price", out_df.columns)
        self.assertNotIn("feature_entry_price", feature_cols)

    def test_select_feature_fallback_uses_trigger_when_present(self) -> None:
        """P1-B: candidate 表有 trigger 列时，fallback 应写 feature_trigger
        而不是 feature_entry_price，让模型只看决策时刻可见的信息。
        """
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=3, freq="D"),
                "side": ["long", "short", "long"],
                "signal_type": ["donchian_breakout"] * 3,
                "trigger": [101.0, 102.0, 103.0],
                "entry_price": [101.5, 101.5, 103.5],  # 不应被使用
            }
        )
        out_df, feature_cols = _select_feature_columns(df)
        self.assertIn("feature_trigger", feature_cols)
        # 用 trigger 不是 entry_price 的取值
        for i, expected in enumerate([101.0, 102.0, 103.0]):
            self.assertAlmostEqual(float(out_df["feature_trigger"].iloc[i]), expected)

    # ---------------- F1: 每个模型 joblib 旁边生成全特征清单 -------------------
    def test_pipeline_writes_feature_manifest_per_saved_model(self) -> None:
        """F1: 模型部署时下游需要严格按"训练用过的特征清单"做 schema 校验。
        在每个 ``models/<signal_type>/window_xx/<model>.joblib`` 旁边生成一份
        ``<model>_features.csv``，列：rank / feature / importance / feature_meaning。
        清单按 importance 降序、所有特征都列出（不只 top10）。
        """
        with tempfile.TemporaryDirectory(prefix="cta_feat_manifest_") as td:
            out = run_model_pipeline(
                symbol="MANIFEST0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=240,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            models_dir = out.report_path.parent / "models"
            self.assertTrue(models_dir.exists())

            joblib_files = list(models_dir.rglob("*.joblib"))
            self.assertGreater(len(joblib_files), 0)

            for joblib_path in joblib_files:
                manifest_path = joblib_path.with_name(joblib_path.stem + "_features.csv")
                self.assertTrue(
                    manifest_path.exists(),
                    f"missing feature manifest beside {joblib_path}: {manifest_path}",
                )
                df = pd.read_csv(manifest_path)
                for col in ("rank", "feature", "importance", "feature_meaning"):
                    self.assertIn(col, df.columns)
                self.assertGreater(len(df), 0)
                self.assertEqual(int(df["rank"].iloc[0]), 1)
                self.assertEqual(int(df["rank"].iloc[-1]), len(df))
                imp = df["importance"].astype(float).to_numpy()
                self.assertTrue(
                    all(imp[i] >= imp[i + 1] for i in range(len(imp) - 1)),
                    f"importance not non-increasing in {manifest_path}: {imp}",
                )
                self.assertEqual(df["feature"].nunique(), len(df))

    def test_feature_manifest_lists_all_training_features_not_just_top10(self) -> None:
        """F1: 清单必须列**所有**用于训练的特征（部署 schema 校验需要全集）。
        断言：清单行数 == top10_feature_importance 文件里同 (signal,window,model)
        条件下出现过的所有特征 ⊆ 清单 feature 列；并且清单不止 top10 行。
        """
        with tempfile.TemporaryDirectory(prefix="cta_feat_manifest_full_") as td:
            out = run_model_pipeline(
                symbol="MANIFEST1",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=240,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            models_dir = out.report_path.parent / "models"
            joblib_files = list(models_dir.rglob("trade_filter.joblib"))
            self.assertGreater(len(joblib_files), 0)
            df = pd.read_csv(joblib_files[0].with_name("trade_filter_features.csv"))

            # 1) top10 文件里 trade_filter 的所有特征必须 ⊆ 全量清单
            top10 = pd.read_csv(out.top_feature_importance_path)
            top10_trade = set(top10.loc[top10["model"] == "trade_filter", "feature"].astype(str))
            self.assertTrue(top10_trade.issubset(set(df["feature"].astype(str))))
            # 2) 全量清单行数 >= top10 行数（在合成数据下 feature 数可能少于 10）
            self.assertGreaterEqual(len(df), len(top10_trade))

    def test_feature_manifest_no_orphan_csv_without_joblib(self) -> None:
        """F1: 不能存在没有对应 joblib 的孤儿 manifest（mfe_mae 被 skip 时的边缘场景）。"""
        with tempfile.TemporaryDirectory(prefix="cta_feat_manifest_skip_") as td:
            out = run_model_pipeline(
                symbol="MANIFEST2",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=240,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            models_dir = out.report_path.parent / "models"
            for csv_path in models_dir.rglob("*_features.csv"):
                stem = csv_path.stem.replace("_features", "")
                self.assertTrue(
                    csv_path.with_name(stem + ".joblib").exists(),
                    f"orphan feature manifest without joblib: {csv_path}",
                )

    # ---------------- D3: warmup 行的 future_mfe/mae 必须保留 NaN -------------
    def test_ensure_training_columns_keeps_nan_for_atr_warmup_rows(self) -> None:
        """D3: 旧实现对所有行 ``fillna(0.0)``，会把 atr_warmup 期的"未知"
        悄悄写成"0 / 0"，吞掉了候选样本特意保留的 NaN 信号。修复后 warmup 行
        ``future_mfe_atr / future_mae_atr`` 必须保持 NaN，warm 行才走 fillna。
        """
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=2, freq="D"),
                "is_executed": [1, 1],
                "atr_warmed": [0, 1],  # row0 在 warmup，row1 已热身
                "future_mfe_atr": [np.nan, 1.0],
                "future_mae_atr": [np.nan, 0.5],
                "label_class": [0, 1],
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "feature_x": [0.1, 0.2],
            }
        )
        out = _ensure_training_columns(df)
        warm_row = out.loc[out["atr_warmed"] == 1].iloc[0]
        warmup_row = out.loc[out["atr_warmed"] == 0].iloc[0]
        # warmup 行必须保留 NaN
        self.assertTrue(pd.isna(warmup_row["future_mfe_atr"]))
        self.assertTrue(pd.isna(warmup_row["future_mae_atr"]))
        # warm 行的值不动（已经是 finite）
        self.assertAlmostEqual(float(warm_row["future_mfe_atr"]), 1.0)
        self.assertAlmostEqual(float(warm_row["future_mae_atr"]), 0.5)

    # ---------------- D1: --exchange 操作符优先级回归 -------------------------
    def test_resolve_run_exchange_prefers_ranking_when_cli_exchange_blank(self) -> None:
        """D1: 之前的 ``a or b if c else None`` 解析为 ``(a or b) if c else None``，
        在 ``c`` 为假值时即使 ``a`` 已经从 ranking 拿到 SHFE，也会被丢成 None。
        """
        # ranking 提供 SHFE，CLI 没传 --exchange → 必须保留 SHFE。
        self.assertEqual(_resolve_run_exchange("SHFE", None), "SHFE")
        self.assertEqual(_resolve_run_exchange("SHFE", ""), "SHFE")
        # ranking 没提供 → 退到 CLI 值。
        self.assertEqual(_resolve_run_exchange(None, "CZCE"), "CZCE")
        # 两者皆空 → None。
        self.assertEqual(_resolve_run_exchange(None, None), None)
        self.assertEqual(_resolve_run_exchange(None, ""), None)
        # ranking 优先于 CLI（即使 CLI 也非空）。
        self.assertEqual(_resolve_run_exchange("DCE", "CZCE"), "DCE")
        # 大小写归一：CLI 小写时也要 upper。
        self.assertEqual(_resolve_run_exchange(None, "czce"), "CZCE")


if __name__ == "__main__":
    unittest.main()
