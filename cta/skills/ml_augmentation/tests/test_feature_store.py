"""feature_store.py tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.skills.ml_augmentation.feature_store import (
    FeatureMeta,
    load_features_as_of,
    register_feature,
    verify_online_offline,
)


class TestFeatureStore(unittest.TestCase):
    def test_register_feature(self) -> None:
        register_feature(
            FeatureMeta(
                name="f_test",
                version="v1",
                interval="day",
                depends_on=["close"],
                code_ref="cta/feature/f_test.py",
            )
        )

    def test_load_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "day" / "RB0.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                    "f1": [1.0, 1.2, 1.1],
                    "f2": [0.3, 0.2, 0.4],
                }
            )
            df.to_csv(path, index=False)
            old = os.environ.get("CTA_FEATURE_ROOT")
            os.environ["CTA_FEATURE_ROOT"] = tmpdir
            try:
                s = load_features_as_of("RB0", "day", pd.Timestamp("2024-01-02"), cols=["f1"])
                self.assertAlmostEqual(float(s["f1"]), 1.2, places=6)
                diff = verify_online_offline("RB0", "day", sample_size=2)
                self.assertIn("diff_abs_max", diff.columns)
            finally:
                if old is None:
                    os.environ.pop("CTA_FEATURE_ROOT", None)
                else:
                    os.environ["CTA_FEATURE_ROOT"] = old


if __name__ == "__main__":
    unittest.main()

