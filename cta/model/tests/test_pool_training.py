"""多 symbol 池化训练单测。

验证：
1. ``_build_pooled_feature_df`` 把多 symbol 的 feature_df 拼接、加 ``symbol`` 列。
2. ``run_model_pipeline(pool_symbols=...)`` 走池化分支：
   - 不调单 symbol 的 ``_build_candidate_table``
   - 输出目录命名为 ``..._POOL_..._model_pipeline``
3. CLI ``--pool`` flag 把 top-N 视为池化训练而非循环各自训练。
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd

import cta.model.model_pipeline as mp
import cta.model.orchestration.pipeline_orchestrator as _impl
from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig


def _fake_candidate_df(symbol: str, n: int = 80) -> pd.DataFrame:
    """生成一份足够大的候选样本，覆盖 walk-forward 窗口、ATR warmup、signal 分组要求。"""
    rng = np.random.default_rng(hash(symbol) % (2**32))
    return pd.DataFrame({
        "datetime": pd.date_range("2018-01-01", periods=n, freq="D"),
        "symbol": symbol,
        "exchange": "SHFE",
        "interval": "day",
        "signal_type": ["donchian_breakout"] * (n // 2) + ["atr_breakout"] * (n - n // 2),
        "side": ["long"] * n,
        "entry_price": 100 + rng.normal(0, 1, n).cumsum(),
        "atr14": rng.uniform(1.0, 3.0, n),
        "atr_warmed": 1,
        "label_class": rng.integers(0, 2, n),
        "regime_label": rng.integers(0, 3, n),
        "is_executed": rng.integers(0, 2, n),
        "feature_signal_code": 0,
        "feature_atr14": rng.uniform(1.0, 3.0, n),
        "feature_some_other": rng.normal(0, 1, n),
        # mfe/mae 训练所需
        "future_mfe_atr": rng.uniform(0.5, 3.0, n),
        "future_mae_atr": -rng.uniform(0.5, 3.0, n),
        "horizon_bars": 20,
    })


class TestBuildPooledFeatureDf(unittest.TestCase):
    def test_concats_multi_symbol(self) -> None:
        symbols = [("RB0", "SHFE"), ("HC0", "SHFE"), ("I0", "DCE")]

        def fake_candidate(symbol: str, exchange, interval, start_date, end_date,
                          trade_side_mode, synthetic_periods, **kwargs):
            return _fake_candidate_df(symbol, n=20), str(exchange or "")

        def fake_features(candidate_df: pd.DataFrame, symbol: str, interval: str,
                         feature_root: Any, generic_columns: Any = None):
            df = candidate_df.copy()
            # 模拟 build_training_feature_table 加更多特征列
            df["feature_extra"] = 1.0
            return df

        with mock.patch.object(_impl, "_build_candidate_table", side_effect=fake_candidate), \
             mock.patch.object(_impl, "_build_training_feature_table_with_auto_fallback", side_effect=fake_features):
            pooled_cand, pooled_feat = mp._build_pooled_feature_df(
                pool_symbols=symbols,
                interval="day",  # C1 修复：rotation 默认 off，day pool 不再强制注入 cross_sectional
                start_date="2018-01-01",
                end_date="2019-12-31",
                trade_side_mode="both",
                synthetic_periods=400,
                feature_root=Path("/tmp/no"),
                generic_columns=None,
            )
        self.assertEqual(len(pooled_cand), 60)  # 3 × 20
        self.assertEqual(len(pooled_feat), 60)
        self.assertSetEqual(set(pooled_feat["symbol"].unique()), {"RB0", "HC0", "I0"})
        # datetime 排序
        self.assertTrue(pooled_feat["datetime"].is_monotonic_increasing)
        # feature 列存在
        self.assertIn("feature_extra", pooled_feat.columns)

    def test_skips_failed_symbol(self) -> None:
        """单 symbol 拉数据失败时整体不应崩；池化继续用其他 symbol。"""
        def fake_candidate(symbol, exchange, interval, *a, **kw):
            if symbol.upper() == "BAD":
                raise FileNotFoundError("no data for BAD")
            return _fake_candidate_df(symbol, n=10), str(exchange or "")

        def fake_features(candidate_df, symbol, interval, feature_root, generic_columns=None):
            return candidate_df.copy()

        with mock.patch.object(_impl, "_build_candidate_table", side_effect=fake_candidate), \
             mock.patch.object(_impl, "_build_training_feature_table_with_auto_fallback", side_effect=fake_features):
            _pooled_cand, pooled_feat = mp._build_pooled_feature_df(
                pool_symbols=[("RB0", "SHFE"), ("BAD", "SHFE"), ("HC0", "SHFE")],
                interval="day",  # C1 修复：rotation 默认 off，day pool 行为可控
                start_date="2018-01-01", end_date="2019-12-31",
                trade_side_mode="both", synthetic_periods=0,
                feature_root=Path("/tmp/no"), generic_columns=None,
            )
        self.assertSetEqual(set(pooled_feat["symbol"].unique()), {"RB0", "HC0"})

    def test_day_pool_appends_cross_sectional_candidates(self) -> None:
        """day 池化时应把 cross_sectional_momentum 候选并入主流水线。"""
        base = _fake_candidate_df("RB0", n=12)
        base["signal_type"] = "donchian_breakout"
        base["symbol"] = "RB0"
        base["exchange"] = "SHFE"
        pooled_cand = base.copy()
        pooled_feat = base.copy()

        xsec = pd.DataFrame(
            {
                "datetime": [pd.Timestamp("2018-01-20"), pd.Timestamp("2018-01-20")],
                "signal_datetime": [pd.Timestamp("2018-01-19"), pd.Timestamp("2018-01-19")],
                "symbol": ["RB0", "HC0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["day", "day"],
                "signal_type": ["cross_sectional_momentum", "cross_sectional_momentum"],
                "side": ["long", "short"],
                "entry_price": [101.0, 102.0],
                "future_mfe_atr": [1.2, 1.1],
                "future_mae_atr": [0.4, 0.5],
                "label_class": [1, 0],
                "regime_label": ["trend_up", "trend_down"],
                "candidate_status": ["filled", "filled"],
                "is_executed": [1, 1],
                "is_filtered": [0, 0],
                "is_triggered": [1, 1],
                "atr_warmed": [1, 1],
                "feature_close": [101.0, 102.0],
            }
        )

        def fake_features(candidate_df: pd.DataFrame, symbol: str, interval: str, feature_root: Any, generic_columns: Any = None):
            out = candidate_df.copy()
            out["feature_extra"] = 1.0
            return out

        with mock.patch.object(_impl, "_build_pooled_feature_df_impl", return_value=(pooled_cand, pooled_feat)), \
             mock.patch.object(_impl, "_build_pool_cross_sectional_candidate_table", return_value=xsec, create=True), \
             mock.patch.object(_impl, "_build_training_feature_table_with_auto_fallback", side_effect=fake_features):
            out_cand, out_feat = mp._build_pooled_feature_df(
                pool_symbols=[("RB0", "SHFE"), ("HC0", "SHFE")],
                interval="day",
                start_date="2018-01-01",
                end_date="2019-12-31",
                trade_side_mode="both",
                synthetic_periods=0,
                feature_root=Path("/tmp/no"),
                generic_columns=None,
                # C1 修复后必须显式启用 rotation；不传则默认 off，候选不入池。
                rotation_cfg=CrossSectionalRotationConfig(
                    use_cross_sectional_momentum_rotation=True,
                    enabled_by_cluster_interval={"*|day": True},
                ),
            )

        self.assertGreaterEqual(len(out_cand), len(pooled_cand) + len(xsec))
        self.assertIn("cross_sectional_momentum", set(out_cand["signal_type"].astype(str)))
        self.assertIn("cross_sectional_momentum", set(out_feat["signal_type"].astype(str)))

    def test_non_day_pool_does_not_append_cross_sectional_candidates(self) -> None:
        """非 day 周期默认不并入截面轮动候选，避免分钟级噪音。"""
        base = _fake_candidate_df("RB0", n=12)
        pooled_cand = base.copy()
        pooled_feat = base.copy()

        with mock.patch.object(_impl, "_build_pooled_feature_df_impl", return_value=(pooled_cand, pooled_feat)), \
             mock.patch.object(_impl, "_build_pool_cross_sectional_candidate_table", return_value=pooled_cand.iloc[:1], create=True) as xsec_mock:
            out_cand, out_feat = mp._build_pooled_feature_df(
                pool_symbols=[("RB0", "SHFE"), ("HC0", "SHFE")],
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                trade_side_mode="both",
                synthetic_periods=0,
                feature_root=Path("/tmp/no"),
                generic_columns=None,
            )
        xsec_mock.assert_not_called()
        self.assertEqual(len(out_cand), len(pooled_cand))
        self.assertEqual(len(out_feat), len(pooled_feat))

    def test_day_pool_does_not_append_cross_sectional_when_rotation_cfg_none(self) -> None:
        """C1 回归：day pool 默认不传 rotation_cfg → 不调用 cross_sectional builder。"""
        base = _fake_candidate_df("RB0", n=12)
        pooled_cand = base.copy()
        pooled_feat = base.copy()

        with mock.patch.object(_impl, "_build_pooled_feature_df_impl", return_value=(pooled_cand, pooled_feat)), \
             mock.patch.object(_impl, "_build_pool_cross_sectional_candidate_table", return_value=pooled_cand.iloc[:1], create=True) as xsec_mock:
            out_cand, out_feat = mp._build_pooled_feature_df(
                pool_symbols=[("RB0", "SHFE"), ("HC0", "SHFE")],
                interval="day",
                start_date="2018-01-01",
                end_date="2019-12-31",
                trade_side_mode="both",
                synthetic_periods=0,
                feature_root=Path("/tmp/no"),
                generic_columns=None,
                # 关键：不传 rotation_cfg / 传 None → 默认 off
            )
        xsec_mock.assert_not_called()
        self.assertEqual(len(out_cand), len(pooled_cand))
        self.assertEqual(len(out_feat), len(pooled_feat))

    def test_day_pool_does_not_append_cross_sectional_when_rotation_disabled(self) -> None:
        """C1 回归：显式传 use_*=False 的 rotation_cfg → 也不调用 builder。"""
        base = _fake_candidate_df("RB0", n=12)
        pooled_cand = base.copy()
        pooled_feat = base.copy()

        with mock.patch.object(_impl, "_build_pooled_feature_df_impl", return_value=(pooled_cand, pooled_feat)), \
             mock.patch.object(_impl, "_build_pool_cross_sectional_candidate_table", return_value=pooled_cand.iloc[:1], create=True) as xsec_mock:
            out_cand, _out_feat = mp._build_pooled_feature_df(
                pool_symbols=[("RB0", "SHFE"), ("HC0", "SHFE")],
                interval="day",
                start_date="2018-01-01",
                end_date="2019-12-31",
                trade_side_mode="both",
                synthetic_periods=0,
                feature_root=Path("/tmp/no"),
                generic_columns=None,
                rotation_cfg=CrossSectionalRotationConfig(
                    use_cross_sectional_momentum_rotation=False,
                ),
            )
        xsec_mock.assert_not_called()
        self.assertEqual(len(out_cand), len(pooled_cand))


class TestRunModelPipelinePoolMode(unittest.TestCase):
    def test_uses_pool_dir_name(self) -> None:
        """run_model_pipeline 收到 pool_symbols 时输出目录用 POOL 而非具体 symbol。"""

        def fake_pooled(pool_symbols, *, interval, start_date, end_date, trade_side_mode,
                       synthetic_periods, feature_root, generic_columns, rotation_cfg=None):
            parts = [_fake_candidate_df(sym, n=120) for sym, _ in pool_symbols]
            for p, (sym, _) in zip(parts, pool_symbols):
                p["symbol"] = sym
            pooled = pd.concat(parts, ignore_index=True).sort_values("datetime").reset_index(drop=True)
            return pooled.copy(), pooled.copy()

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(_impl, "_build_pooled_feature_df", side_effect=fake_pooled):
            res = mp.run_model_pipeline(
                pool_symbols=[("RB0", "SHFE"), ("HC0", "SHFE")],
                interval="day", start_date="2018-01-01", end_date="2019-12-31",
                trade_side_mode="both", train_end="2018-09-30", valid_end="2018-11-30",
                output_root=Path(tmp), synthetic_periods=0,
                by_signal_type=False, max_walk_forward_windows=1,
            )
            self.assertIsNotNone(res.report_path)
            self.assertIn("POOL", str(res.report_path))


class TestCliPoolFlag(unittest.TestCase):
    def test_pool_flag_passes_pool_symbols(self) -> None:
        captured: list[Any] = []

        def fake_run(*, symbol, exchange, interval, **kwargs):
            captured.append({"symbol": symbol, "exchange": exchange,
                             "interval": interval,
                             "pool_symbols": kwargs.get("pool_symbols")})
            return mock.MagicMock(report_path="/tmp/x", prediction_path="",
                                  metrics_path="", top_feature_importance_path="")

        argv = ["--top-n-symbols", "3",
                "--symbols-ranking-path", "cta/feature/symbols_research_ranking.csv",
                "--interval", "day",
                "--start", "2018-01-01", "--end", "2019-12-31",
                "--train-end", "2018-09-30", "--valid-end", "2018-11-30",
                "--pool"]
        with mock.patch.object(_impl, "run_model_pipeline", side_effect=fake_run), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            mp.main(argv)
        # pool 模式：每个 interval 调 run_model_pipeline 一次（而非每 symbol×interval）
        self.assertEqual(len(captured), 1)
        call = captured[0]
        self.assertEqual(call["symbol"], "POOL")
        self.assertIsNotNone(call["pool_symbols"])
        self.assertGreaterEqual(len(call["pool_symbols"]), 1)


if __name__ == "__main__":
    unittest.main()
