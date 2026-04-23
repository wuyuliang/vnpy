"""ml_opportunity_model.py tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.skills.filtering_scoring.ml_opportunity_model import (
    build_training_dataset,
    predict_ml_gate,
)


class TestMLOpportunityModel(unittest.TestCase):
    def test_predict_ml_gate_with_linear_model_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "linear_model.json"
            payload = {
                "type": "linear",
                "intercept": -0.2,
                "weights": {"setup_q": 2.0, "rr": 1.0},
            }
            model_path.write_text(json.dumps(payload), encoding="utf-8")
            feat = pd.Series({"setup_q": 0.5, "rr": 1.0})
            res = predict_ml_gate(feat, str(model_path))
            self.assertTrue(0.0 <= res.probability <= 1.0)
            self.assertIn(res.size_multiplier, {0.0, 1.0, 1.5})

    def test_build_training_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            trade_path = Path(tmpdir) / "trades.csv"
            trade_log = pd.DataFrame(
                {
                    "trade_id": [1, 2],
                    "symbol": ["RB0", "RB0"],
                    "interval": ["day", "minute60"],
                    "signal_ts": ["2024-01-05", "2024-01-08"],
                    "entry": [100.0, 102.0],
                    "stop": [99.0, 101.0],
                    "mfe": [3.0, 1.0],
                    "mae": [0.5, 1.5],
                }
            )
            trade_log.to_csv(trade_path, index=False)

            def _loader(symbol: str, interval: str, ts: pd.Timestamp) -> pd.Series:
                return pd.Series(
                    {"symbol": symbol, "interval": interval, "setup_q": 0.6, "rr": 1.8},
                    name=ts,
                )

            ds = build_training_dataset(
                trade_log_parquet=str(trade_path),
                feature_loader_fn=_loader,
                target_rr=2.0,
                max_bars=20,
            )
            self.assertEqual(len(ds), 2)
            self.assertIn("label", ds.columns)
            self.assertIn("setup_q", ds.columns)


if __name__ == "__main__":
    unittest.main()

