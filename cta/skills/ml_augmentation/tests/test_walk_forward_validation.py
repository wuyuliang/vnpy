"""walk_forward_validation.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.ml_augmentation.walk_forward_validation import (
    WFConfig,
    run_walk_forward,
    walk_forward_splits,
)


class _DummyModel:
    def __init__(self) -> None:
        self.threshold = 0.0

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "_DummyModel":
        self.threshold = float(X["f1"].mean()) if len(X) else 0.0
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p1 = (X["f1"].to_numpy() > self.threshold).astype(float) * 0.7 + 0.15
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])


class TestWalkForwardValidation(unittest.TestCase):
    def test_walk_forward_splits(self) -> None:
        ts = pd.date_range("2020-01-01", periods=1600, freq="D")
        cfg = WFConfig(mode="rolling", train_months=12, test_months=3, purge_bars=5)
        folds = list(walk_forward_splits(ts, cfg))
        self.assertGreater(len(folds), 1)

    def test_run_walk_forward(self) -> None:
        ts = pd.date_range("2020-01-01", periods=1600, freq="D")
        X = pd.DataFrame({"f1": np.sin(np.linspace(0, 20, len(ts))), "f2": np.random.default_rng(0).normal(size=len(ts))}, index=ts)
        y = (X["f1"] > 0).astype(int)
        cfg = WFConfig(mode="expanding", train_months=12, test_months=3, purge_bars=3)
        out = run_walk_forward(X, y, model_factory=_DummyModel, cfg=cfg)
        self.assertIn("fold", out.columns)
        self.assertIn("pred_prob", out.columns)


if __name__ == "__main__":
    unittest.main()

