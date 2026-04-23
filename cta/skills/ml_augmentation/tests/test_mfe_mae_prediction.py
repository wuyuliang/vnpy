"""mfe_mae_prediction.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.ml_augmentation.mfe_mae_prediction import (
    MFEMAEConfig,
    build_mfe_mae_labels,
    predict_mfe_mae,
    train_quantile_models,
)


class TestMFEMAEPrediction(unittest.TestCase):
    def test_build_labels(self) -> None:
        rng = np.random.default_rng(1)
        close = 100 + np.cumsum(rng.normal(0.0, 0.8, size=240))
        bars = pd.DataFrame(
            {
                "high": close + np.abs(rng.normal(0.6, 0.2, size=240)),
                "low": close - np.abs(rng.normal(0.6, 0.2, size=240)),
                "close": close,
                "atr_14": pd.Series(np.abs(rng.normal(1.0, 0.2, size=240))).rolling(14, min_periods=1).mean(),
            }
        )
        idx = pd.Index([20, 50, 80, 120, 160])
        y = build_mfe_mae_labels(bars, idx, MFEMAEConfig(horizon=20))
        self.assertEqual(len(y), len(idx))
        self.assertIn("mfe", y.columns)

    def test_train_and_predict(self) -> None:
        X = pd.DataFrame({"f1": [0.1, 0.3, -0.2, 0.5], "f2": [1.0, 1.4, 0.7, 1.8]})
        y_mfe = pd.Series([1.2, 2.1, 0.6, 2.8])
        y_mae = pd.Series([0.5, 0.8, 0.4, 1.0])
        cfg = MFEMAEConfig()
        models = train_quantile_models(X, y_mfe, y_mae, cfg)
        p = predict_mfe_mae(models, pd.Series({"f1": 0.2, "f2": 1.2}))
        self.assertIn("mfe_q50", p)
        self.assertIn("mae_q80", p)


if __name__ == "__main__":
    unittest.main()

