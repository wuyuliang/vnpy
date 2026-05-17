"""Tests for three core model wrappers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from cta.model.mfe_mae_model import MfeMaeModel, evaluate_mfe_mae_model
from cta.model.regime_classifier_model import RegimeClassifierModel, evaluate_regime_model
from cta.model.trade_filter_model import TradeFilterModel, evaluate_trade_filter_model


def _mk_df(n: int = 220, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, size=n)
    x2 = rng.normal(0, 1, size=n)
    x3 = rng.normal(0, 1, size=n)

    y_cls = (x1 + 0.7 * x2 > 0.2).astype(int)
    y_regime = np.where(x1 > 0.8, "trend_up", np.where(x1 < -0.8, "trend_down", "range"))
    y_mfe = np.maximum(0.0, 0.8 * x1 + 0.5 * x2 + rng.normal(0, 0.2, size=n))
    y_mae = np.maximum(0.0, -0.5 * x1 + 0.4 * x3 + rng.normal(0, 0.2, size=n))

    dt = pd.date_range("2018-01-01", periods=n, freq="h")
    return pd.DataFrame(
        {
            "datetime": dt,
            "feature_x1": x1,
            "feature_x2": x2,
            "feature_x3": x3,
            "label_class": y_cls,
            "regime_label": y_regime,
            "future_mfe_atr": y_mfe,
            "future_mae_atr": y_mae,
        }
    )


class TestModelsCore(unittest.TestCase):
    def test_trade_filter_model(self) -> None:
        df = _mk_df()
        train = df.iloc[:160]
        test = df.iloc[160:]
        feats = ["feature_x1", "feature_x2", "feature_x3"]

        m = TradeFilterModel(random_state=7)
        m.fit(train, feature_columns=feats, label_column="label_class")
        metrics = evaluate_trade_filter_model(m, test, feature_columns=feats, label_column="label_class")
        self.assertIn("accuracy", metrics)
        self.assertIn("auc", metrics)

        with tempfile.TemporaryDirectory(prefix="cta_trade_filter_") as td:
            path = Path(td) / "trade_filter.joblib"
            m.save(path)
            loaded = TradeFilterModel.load(path)
            prob = loaded.predict_proba(test, feature_columns=feats)
            self.assertEqual(len(prob), len(test))

        top = m.get_top_feature_importance(train, feature_columns=feats, label_column="label_class", top_k=3)
        self.assertEqual(len(top), 3)
        self.assertListEqual(list(top.columns), ["feature", "importance"])
        self.assertTrue((top["importance"].to_numpy()[:-1] >= top["importance"].to_numpy()[1:]).all())
        # P1.4: trade filter 默认应为“树+线性”融合模型，命名可识别
        self.assertIn("ensemble", str(m.model_kind))
        self.assertIn("histgb", str(m.model_kind))
        self.assertIn("elasticnet", str(m.model_kind))

    def test_trade_filter_load_legacy_joblib_warns_estimator_tree_fallback(self) -> None:
        df = _mk_df(n=80)
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        m = TradeFilterModel(random_state=7).fit(df, feature_columns=feats, label_column="label_class")
        with tempfile.TemporaryDirectory(prefix="cta_trade_filter_legacy_") as td:
            path = Path(td) / "legacy.joblib"
            # 模拟旧格式：没有 estimator_tree 字段
            joblib.dump(
                {
                    "random_state": 7,
                    "model_kind": "legacy_no_tree",
                    "estimator": m.estimator,
                    "estimator_linear": m.estimator_linear,
                },
                path,
            )
            with self.assertLogs("cta.model.trade_filter_model", level="WARNING") as cm:
                loaded = TradeFilterModel.load(path)
            self.assertIsNotNone(loaded.estimator_tree)
            self.assertTrue(any("estimator_tree fallback" in line for line in cm.output))

    def test_regime_classifier_model(self) -> None:
        df = _mk_df()
        train = df.iloc[:160]
        test = df.iloc[160:]
        feats = ["feature_x1", "feature_x2", "feature_x3"]

        m = RegimeClassifierModel(random_state=13)
        m.fit(train, feature_columns=feats, label_column="regime_label")
        metrics = evaluate_regime_model(m, test, feature_columns=feats, label_column="regime_label")
        self.assertIn("accuracy", metrics)
        self.assertIn("macro_f1", metrics)
        self.assertIn("auc", metrics)
        self.assertTrue(np.isfinite(float(metrics["auc"])) or np.isnan(float(metrics["auc"])))

        top = m.get_top_feature_importance(feature_columns=feats, top_k=3)
        self.assertEqual(len(top), 3)
        self.assertListEqual(list(top.columns), ["feature", "importance"])
        self.assertTrue((top["importance"].to_numpy()[:-1] >= top["importance"].to_numpy()[1:]).all())
        model_step = m.estimator.named_steps["model"]  # type: ignore[union-attr]
        self.assertEqual(int(model_step.n_estimators), 200)
        self.assertEqual(int(model_step.max_depth), 5)
        self.assertEqual(int(model_step.min_samples_leaf), 20)

    def test_regime_classifier_warns_on_unexpected_labels(self) -> None:
        df = _mk_df(n=120)
        df.loc[0, "regime_label"] = "weird_state"
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        with self.assertLogs("cta.model.regime_classifier_model", level="WARNING") as cm:
            RegimeClassifierModel(random_state=13).fit(df, feature_columns=feats, label_column="regime_label")
        self.assertTrue(any("unexpected values" in line for line in cm.output))

    def test_regime_classifier_warns_when_class_count_below_min_leaf(self) -> None:
        df = _mk_df(n=180).copy()
        # 强制制造极端稀有类别：trend_down 只有 2 条
        df["regime_label"] = "trend_up"
        df.loc[:1, "regime_label"] = "trend_down"
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        with self.assertLogs("cta.model.regime_classifier_model", level="WARNING") as cm:
            RegimeClassifierModel(random_state=13).fit(df, feature_columns=feats, label_column="regime_label")
        self.assertTrue(any("minority class count below min_samples_leaf" in line for line in cm.output))

    def test_mfe_mae_model(self) -> None:
        df = _mk_df()
        train = df.iloc[:160]
        test = df.iloc[160:]
        feats = ["feature_x1", "feature_x2", "feature_x3"]

        m = MfeMaeModel(random_state=17)
        m.fit(
            train,
            feature_columns=feats,
            mfe_column="future_mfe_atr",
            mae_column="future_mae_atr",
        )
        pred = m.predict(test, feature_columns=feats)
        self.assertEqual(len(pred), len(test))
        self.assertIn("pred_mfe_atr", pred.columns)
        self.assertIn("pred_mae_atr", pred.columns)
        metrics = evaluate_mfe_mae_model(
            m,
            test,
            feature_columns=feats,
            mfe_column="future_mfe_atr",
            mae_column="future_mae_atr",
        )
        self.assertIn("mfe_mae_mae", metrics)
        self.assertIn("mae_mae", metrics)
        self.assertIn("direction_auc", metrics)
        self.assertTrue(np.isfinite(float(metrics["direction_auc"])) or np.isnan(float(metrics["direction_auc"])))

        top = m.get_top_feature_importance(feature_columns=feats, top_k=3)
        self.assertEqual(len(top), 3)
        self.assertListEqual(list(top.columns), ["feature", "importance"])
        self.assertTrue((top["importance"].to_numpy()[:-1] >= top["importance"].to_numpy()[1:]).all())
        mo = m.estimator.named_steps["model"]  # type: ignore[union-attr]
        base = mo.estimator
        self.assertEqual(int(base.n_estimators), 200)
        self.assertEqual(int(base.max_depth), 5)
        self.assertEqual(int(base.min_samples_leaf), 20)

    def test_mfe_mae_model_kind_reflects_dummy_vs_rf(self) -> None:
        """T-G: when training rows < min_samples, MfeMaeModel falls back to dummy and reports model_kind."""
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        df = _mk_df(n=200)

        # Below threshold => dummy
        small = df.iloc[:5].copy()
        m_small = MfeMaeModel(random_state=3, min_samples=10).fit(
            small, feature_columns=feats, mfe_column="future_mfe_atr", mae_column="future_mae_atr",
        )
        self.assertEqual(m_small.model_kind, "dummy")
        pred_small = m_small.predict(small, feature_columns=feats)
        self.assertEqual(pred_small.shape, (len(small), 2))

        # Above threshold => random_forest
        m_big = MfeMaeModel(random_state=3, min_samples=10).fit(
            df, feature_columns=feats, mfe_column="future_mfe_atr", mae_column="future_mae_atr",
        )
        self.assertEqual(m_big.model_kind, "random_forest")

        # Save / load round-trip preserves model_kind
        with tempfile.TemporaryDirectory(prefix="cta_mfe_kind_") as td:
            p = Path(td) / "mfe.joblib"
            m_small.save(p)
            reloaded = MfeMaeModel.load(p)
            self.assertEqual(reloaded.model_kind, "dummy")
            self.assertEqual(reloaded.min_samples, 10)

    def test_dummy_models_handle_nan_features(self) -> None:
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        df = pd.DataFrame(
            {
                "feature_x1": [np.nan, np.nan, 0.1, np.nan, 0.3, np.nan],
                "feature_x2": [np.nan, 1.0, np.nan, 0.2, np.nan, 0.0],
                "feature_x3": [0.0, np.nan, np.nan, 0.1, np.nan, np.nan],
                "label_class": [1, 1, 1, 1, 1, 1],
                "regime_label": ["range", "range", "range", "range", "range", "range"],
                "future_mfe_atr": [0.1, 0.2, 0.3, 0.0, 0.4, 0.2],
                "future_mae_atr": [0.2, 0.1, 0.2, 0.3, 0.1, 0.2],
            }
        )

        trade_model = TradeFilterModel(random_state=1).fit(df, feature_columns=feats, label_column="label_class")
        trade_prob = trade_model.predict_proba(df, feature_columns=feats)
        self.assertEqual(len(trade_prob), len(df))

        regime_model = RegimeClassifierModel(random_state=1).fit(df, feature_columns=feats, label_column="regime_label")
        regime_pred = regime_model.predict(df, feature_columns=feats)
        self.assertEqual(len(regime_pred), len(df))

        small_df = df.iloc[:6].copy()
        mfe_model = MfeMaeModel(random_state=1).fit(
            small_df,
            feature_columns=feats,
            mfe_column="future_mfe_atr",
            mae_column="future_mae_atr",
        )
        pred = mfe_model.predict(small_df, feature_columns=feats)
        self.assertEqual(len(pred), len(small_df))

    def test_mfe_mae_model_handles_nan_targets_without_crash(self) -> None:
        """P0: y 含 NaN 时训练不应报错（winsorize 前先清洗 NaN/inf）。"""
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        df = _mk_df(n=120).copy()
        df.loc[:10, "future_mfe_atr"] = np.nan
        df.loc[5:15, "future_mae_atr"] = np.nan
        df.loc[20, "future_mfe_atr"] = np.inf
        df.loc[21, "future_mae_atr"] = -np.inf

        m = MfeMaeModel(random_state=11)
        m.fit(
            df,
            feature_columns=feats,
            mfe_column="future_mfe_atr",
            mae_column="future_mae_atr",
        )
        pred = m.predict(df.iloc[:20], feature_columns=feats)
        self.assertEqual(pred.shape[0], 20)
        self.assertTrue(np.isfinite(pred["pred_mfe_atr"]).all())
        self.assertTrue(np.isfinite(pred["pred_mae_atr"]).all())

    def test_mfe_mae_predict_clips_negative_outputs(self) -> None:
        """预测阶段必须保证 pred_mfe_atr/pred_mae_atr 非负，避免仓位公式异常。"""
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        df = _mk_df(n=4).copy()

        class _FakeEstimator:
            def predict(self, _x: pd.DataFrame) -> np.ndarray:
                return np.array(
                    [
                        [-1.0, -2.0],
                        [0.5, -0.1],
                        [-0.3, 0.2],
                        [0.0, 0.0],
                    ],
                    dtype=float,
                )

        m = MfeMaeModel(random_state=1)
        m.estimator = _FakeEstimator()  # type: ignore[assignment]
        pred = m.predict(df, feature_columns=feats)
        self.assertTrue((pred["pred_mfe_atr"] >= 0.0).all())
        self.assertTrue((pred["pred_mae_atr"] >= 0.0).all())

    def test_mfe_mae_model_accepts_configurable_winsorize_percentiles(self) -> None:
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        df = _mk_df(n=180).copy()
        # 加一些长尾值，确保 winsorize 参数会被实际用到。
        df.loc[:2, "future_mfe_atr"] = [20.0, 25.0, 30.0]
        df.loc[:2, "future_mae_atr"] = [15.0, 18.0, 22.0]
        model = MfeMaeModel(
            random_state=19,
            model_params={
                "winsorize_pct_low": 0.05,
                "winsorize_pct_high": 0.95,
            },
        )
        model.fit(
            df,
            feature_columns=feats,
            mfe_column="future_mfe_atr",
            mae_column="future_mae_atr",
        )
        pred = model.predict(df.iloc[:10], feature_columns=feats)
        self.assertEqual(pred.shape[0], 10)
        self.assertTrue(np.isfinite(pred["pred_mfe_atr"]).all())
        self.assertTrue(np.isfinite(pred["pred_mae_atr"]).all())

    def test_trade_filter_fit_when_one_class_has_single_sample(self) -> None:
        """极端不平衡样本下 fit 不应因 early_stopping 的分层切分而抛错。"""
        n = 100
        df = pd.DataFrame(
            {
                "feature_x1": np.linspace(-1.0, 1.0, n),
                "feature_x2": np.linspace(0.0, 2.0, n),
                "feature_x3": np.linspace(1.0, 3.0, n),
                "label_class": np.array([1] + [0] * (n - 1), dtype=int),
            }
        )
        feats = ["feature_x1", "feature_x2", "feature_x3"]
        model = TradeFilterModel(random_state=7)
        model.fit(df, feature_columns=feats, label_column="label_class")
        self.assertNotEqual(str(model.model_kind), "uninitialized")


if __name__ == "__main__":
    unittest.main()
