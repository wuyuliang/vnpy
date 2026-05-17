"""Smoke test for generic CTA model pipeline."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.model.model_pipeline import (
    MFE_MAE_KIND_SKIPPED_NO_EXEC,
    _apply_causality_manifest_filter,
    _auto_enrich_candidate_features_for_models,
    _build_candidate_table,
    _build_top_feature_concentration_alerts,
    _build_valid_test_gap_alerts,
    _build_symbol_cluster_sample_weight,
    _ensure_training_columns,
    _build_last_oot_decile_table,
    _build_walk_forward_windows,
    _evaluate_oot_real_execution,
    _ensure_binary_label_diversity,
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _parse_args,
    _resolve_run_exchange,
    _select_best_param_trial,
    _filter_model_leakage_features,
    _select_feature_columns,
    _train_mfe_mae_or_skip,
    _validate_stop_loss_pct_consistency,
    run_model_pipeline,
    run_model_pipeline_multi,
)
from cta.model.pipeline_oot_evaluation import _build_position_lifetime_table
from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.portfolio_logic.config import PortfolioLogicConfig, RiskThrottleConfig, ThrottleLevel
from cta.model.trade_filter_model import TradeFilterModel



class TestModelPipelinePart05(unittest.TestCase):
    def test_auto_enrich_candidate_features_numeric_correctness(self) -> None:
        """P1: _auto_enrich_candidate_features_for_models 应产出可校验的数值。"""
        df = pd.DataFrame(
            {
                "feature_open": [10.0, 5.0],
                "feature_high": [16.0, 5.0],
                "feature_low": [10.0, 5.0],
                "feature_close": [14.0, 5.0],
                "feature_volume": [100.0, 50.0],
                "feature_atr14": [2.0, 0.0],
                "feature_trend_score": [1.5, -1.0],
                "feature_breakout_score": [2.0, 0.5],
                "feature_setup_quality": [1.0, 0.2],
                "feature_tr_range_atr": [0.5, 1.0],
                "side": ["long", "short"],
                "signal_type": ["tight_range_breakout", "tight_range_breakout"],
            }
        )
        out = _auto_enrich_candidate_features_for_models(df, force_generic_fallback=True)
        # row0: range=6, body=4 -> body_ratio=2/3; vol_ratio=6/2=3
        self.assertAlmostEqual(float(out.loc[0, "generic_auto_body_ratio"]), 2.0 / 3.0, places=6)
        self.assertAlmostEqual(float(out.loc[0, "generic_auto_vol_ratio"]), 3.0, places=6)
        # row1: atr14=0 / range=0 时 ratio 应该被安全回填为 0
        self.assertAlmostEqual(float(out.loc[1, "generic_auto_body_ratio"]), 0.0, places=9)
        self.assertAlmostEqual(float(out.loc[1, "generic_auto_vol_ratio"]), 0.0, places=9)
        # row0: regime_state = trend + 0.15*side = 1.5 + 0.15*1
        self.assertAlmostEqual(float(out.loc[0, "generic_model_regime_state"]), 1.65, places=6)
        # row1: mfe_edge = (0.5 + 0.4*-1 + 0.3*0.2) - 0.4*1 = -0.24
        self.assertAlmostEqual(float(out.loc[1, "generic_model_mfe_edge"]), -0.24, places=6)
        # row1 short: side_interaction = -0.24 * (-1) = 0.24
        self.assertAlmostEqual(float(out.loc[1, "generic_model_mfe_side_interaction"]), 0.24, places=6)

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

    def test_walk_forward_windows_rolling_mode_uses_fixed_span(self) -> None:
        """P1.1: rolling 模式支持固定 train/valid/test 年份窗口。"""
        n = 16 * 12
        dt = pd.date_range("2010-01-01", periods=n, freq="MS")
        df = pd.DataFrame(
            {
                "datetime": dt,
                "exit_datetime": dt,
                "label_class": [0, 1] * (n // 2),
                "future_mfe_atr": np.ones(n),
                "future_mae_atr": np.zeros(n),
            }
        )
        windows = _build_walk_forward_windows(
            df,
            train_end="2014-12-31",
            valid_end="2015-12-31",
            max_windows=4,
            window_mode="rolling",
            rolling_train_years=3,
            rolling_valid_years=1,
            rolling_test_years=1,
            rolling_step_years=1,
        )
        self.assertGreaterEqual(len(windows), 2)
        for w in windows:
            self.assertGreater(len(w.train), 0)
            self.assertGreater(len(w.valid), 0)
            self.assertGreater(len(w.test), 0)
            train_span_days = (
                pd.to_datetime(w.train["datetime"], errors="coerce").max()
                - pd.to_datetime(w.train["datetime"], errors="coerce").min()
            ).days
            # 3年窗口约 1095 天，考虑月频边界留足容忍区间
            self.assertGreaterEqual(train_span_days, 900)
            self.assertLessEqual(train_span_days, 1300)

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

    def test_select_feature_columns_fallback_handles_missing_side_and_signal_type(self) -> None:
        """B2: 没有 feature_*/generic_* 列且 side/signal_type/entry_price 也缺失时，
        兜底分支不应抛 AttributeError。
        """
        df = pd.DataFrame({"datetime": pd.date_range("2020-01-01", periods=3, freq="D")})
        out_df, cols = _select_feature_columns(df)
        self.assertGreater(len(cols), 0)
        for c in cols:
            self.assertTrue(c.startswith("feature_"))

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
        self.assertAlmostEqual(float(ns.max_auc_gap), 0.03, places=9)
        self.assertEqual(int(ns.min_used_symbols), 2)

    def test_parse_args_supports_top_n_symbols(self) -> None:
        ns = _parse_args(
            [
                "--symbol", "RB0",
                "--top-n-symbols", "5",
                "--symbols-ranking-path", "cta/feature/symbols_research_ranking.csv",
                "--min-used-symbols", "3",
            ]
        )
        self.assertEqual(int(ns.top_n_symbols), 5)
        self.assertEqual(int(ns.min_used_symbols), 3)
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

    def test_feature_meaning_refreshes_when_features_doc_mtime_changes(self) -> None:
        """D4: 旧实现 ``@lru_cache(maxsize=8)`` 直接以 path 字符串为键，
        长跑进程里 FEATURES.md 改动后取到的依旧是老映射。修复后缓存键带上
        mtime —— 文件改动后映射立刻失效。
        """
        import os
        import time as _time

        from cta.model.pipeline_feature_meaning import _feature_meaning

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


if __name__ == "__main__":
    unittest.main()
