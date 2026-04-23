"""context_score.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.filtering_scoring.context_score import (
    combine_final_score,
    compute_context_score,
)


def _mk_tf(n: int, slope: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = 100 + slope * np.arange(n)
    close = base + rng.normal(0, 0.4, size=n)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "close": close,
            "regime": ["trend"] * n,
            "leg_position": ["start"] * n,
            "session": ["open"] * n,
        }
    )


class TestContextScore(unittest.TestCase):
    def test_compute_context_score(self) -> None:
        ltf = _mk_tf(120, slope=0.02, seed=1)
        mtf = _mk_tf(120, slope=0.03, seed=2)
        htf = _mk_tf(120, slope=0.05, seed=3)
        cs = compute_context_score(
            df_ltf=ltf,
            df_mtf=mtf,
            df_htf=htf,
            bar_idx_ltf=80,
            setup_type="tight_range",
            setup_dir="long",
        )
        self.assertTrue(0.0 <= cs.score <= 1.0)
        self.assertIn("s_htf", cs.components)
        self.assertIn("s_regime", cs.components)

    def test_combine_final_score_geometric(self) -> None:
        s = combine_final_score(0.8, 0.6, 0.7)
        self.assertTrue(0.0 <= s <= 1.0)
        self.assertLess(s, 0.8)

    def test_interval_compatibility(self) -> None:
        ltf = _mk_tf(120, slope=0.02, seed=8)
        mtf = _mk_tf(120, slope=0.01, seed=9)
        htf = _mk_tf(120, slope=0.03, seed=10)
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            cs = compute_context_score(
                df_ltf=ltf,
                df_mtf=mtf,
                df_htf=htf,
                bar_idx_ltf=90,
                setup_type="bp",
                setup_dir="long",
                interval=interval,
            )
            self.assertTrue(0.0 <= cs.score <= 1.0, interval)


if __name__ == "__main__":
    unittest.main()

