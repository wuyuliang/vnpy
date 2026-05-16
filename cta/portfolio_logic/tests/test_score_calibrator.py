"""Unit tests for score percentile calibration."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.portfolio_logic.score_calibrator import CalibrationStats, ScoreCalibrator


class TestScoreCalibrator(unittest.TestCase):
    def test_to_percentile_uses_calibration_table(self) -> None:
        stats = CalibrationStats(
            cluster="cluster_black",
            interval="60min",
            model_kind="trade_filter",
            sample_count=1000,
            train_window=("2020-01-01", "2022-12-31"),
            percentile_values=np.linspace(0.0, 1.0, 101),
        )
        cal = ScoreCalibrator({("cluster_black", "60min", "trade_filter"): stats})
        p = cal.to_percentile("cluster_black", "60min", "trade_filter", 0.62)
        self.assertEqual(p, 63.0)

    def test_to_percentile_uses_empirical_distribution_when_values_are_raw_scores(self) -> None:
        stats = CalibrationStats(
            cluster="cluster_black",
            interval="60min",
            model_kind="trade_filter",
            sample_count=9,
            train_window=("2020-01-01", "2022-12-31"),
            percentile_values=np.linspace(0.1, 0.9, 9),
        )
        cal = ScoreCalibrator({("cluster_black", "60min", "trade_filter"): stats})
        p = cal.to_percentile("cluster_black", "60min", "trade_filter", 0.5)
        self.assertAlmostEqual(p, 55.5555555556, places=6)

    def test_transform_adds_pctl_and_fallbacks_when_missing_calibration(self) -> None:
        stats = CalibrationStats(
            cluster="cluster_black",
            interval="60min",
            model_kind="trade_filter",
            sample_count=1000,
            train_window=("2020-01-01", "2022-12-31"),
            percentile_values=np.linspace(0.0, 1.0, 101),
        )
        cal = ScoreCalibrator({("cluster_black", "60min", "trade_filter"): stats})
        df = pd.DataFrame(
            {
                "cluster_name": ["cluster_black", "cluster_metal"],
                "interval": ["60min", "60min"],
                "trade_filter_prob": [0.50, 0.80],
            }
        )
        out = cal.transform(df)
        self.assertIn("trade_filter_prob_pctl", out.columns)
        self.assertEqual(float(out.iloc[0]["trade_filter_prob_pctl"]), 51.0)
        self.assertEqual(float(out.iloc[1]["trade_filter_prob_pctl"]), 80.0)

    def test_fit_save_load_roundtrip(self) -> None:
        df = pd.DataFrame(
            {
                "cluster_name": ["cluster_black"] * 5,
                "interval": ["60min"] * 5,
                "trade_filter_prob": [0.1, 0.2, 0.5, 0.8, 0.9],
                "datetime": pd.to_datetime(
                    ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04", "2020-01-05"]
                ),
            }
        )
        cal = ScoreCalibrator.fit_for_holdout(
            df,
            score_column="trade_filter_prob",
            model_kind="trade_filter",
        )
        with tempfile.TemporaryDirectory(prefix="calibrator_") as td:
            path = Path(td) / "trade_filter_calibration.joblib"
            cal.save(path)
            loaded = ScoreCalibrator.load(path)
            p = loaded.to_percentile("cluster_black", "60min", "trade_filter", 0.5)
            self.assertGreater(float(p), 0.0)
            self.assertLessEqual(float(p), 100.0)


if __name__ == "__main__":
    unittest.main()
