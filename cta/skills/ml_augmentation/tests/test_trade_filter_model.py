"""trade_filter_model.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.ml_augmentation.trade_filter_model import (
    FilterConfig,
    apply_gate,
    build_dataset,
    train_gate,
)


class TestTradeFilterModel(unittest.TestCase):
    def test_build_dataset(self) -> None:
        trade_log = pd.DataFrame(
            {
                "trade_id": [1, 2, 3],
                "signal_ts": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                "net_pnl": [10.0, -5.0, 6.0],
                "mfe": [2.0, 0.5, 1.5],
                "risk": [1.0, 1.0, 1.0],
            }
        )
        feature_panel = pd.DataFrame(
            {
                "signal_ts": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                "f1": [0.2, -0.1, 0.3],
                "f2": [1.0, 0.5, 1.5],
            }
        )
        X, y = build_dataset(trade_log, feature_panel, FilterConfig())
        self.assertEqual(len(X), 3)
        self.assertEqual(len(y), 3)

    def test_train_and_apply_gate(self) -> None:
        X = pd.DataFrame({"f1": [0.1, 0.2, -0.2, -0.1], "f2": [1.0, 1.2, 0.5, 0.3]})
        y = pd.Series([1, 1, 0, 0])
        model = train_gate(X, y, FilterConfig())
        ok = apply_gate(model, {"f1": 0.15, "f2": 1.1}, threshold=0.5)
        self.assertIsInstance(ok, bool)


if __name__ == "__main__":
    unittest.main()

