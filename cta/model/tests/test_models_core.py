"""Tests for three core model wrappers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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

        top = m.get_top_feature_importance(feature_columns=feats, top_k=3)
        self.assertEqual(len(top), 3)
        self.assertListEqual(list(top.columns), ["feature", "importance"])
        self.assertTrue((top["importance"].to_numpy()[:-1] >= top["importance"].to_numpy()[1:]).all())

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

        top = m.get_top_feature_importance(feature_columns=feats, top_k=3)
        self.assertEqual(len(top), 3)
        self.assertListEqual(list(top.columns), ["feature", "importance"])
        self.assertTrue((top["importance"].to_numpy()[:-1] >= top["importance"].to_numpy()[1:]).all())

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


if __name__ == "__main__":
    unittest.main()
