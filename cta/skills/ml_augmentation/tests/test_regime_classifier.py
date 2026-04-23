"""regime_classifier.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.ml_augmentation.regime_classifier import (
    RegimeConfig,
    build_regime_labels,
    predict_regime,
)


class TestRegimeClassifier(unittest.TestCase):
    def test_build_regime_labels(self) -> None:
        close = 100 + np.cumsum(np.random.default_rng(0).normal(0.1, 0.8, size=200))
        bars = pd.DataFrame({"close": close})
        y = build_regime_labels(bars, RegimeConfig(horizon=20))
        self.assertEqual(len(y), len(bars))
        self.assertGreater(len(set(y.dropna().unique())), 1)

    def test_predict_regime(self) -> None:
        model = {
            "classes": ["trend_up", "trend_down", "range", "transition"],
            "weights": {"f1": [1.0, -1.0, 0.2, 0.0], "f2": [0.5, 0.5, -0.3, 0.1]},
            "bias": [0.1, 0.1, 0.1, 0.1],
        }
        p = predict_regime(model, pd.Series({"f1": 0.2, "f2": 0.5}))
        self.assertAlmostEqual(sum(p.values()), 1.0, places=6)
        self.assertIn("trend_up", p)


if __name__ == "__main__":
    unittest.main()

