"""hl_structure.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.price_action.hl_structure import HLSignal, detect_hl_signals, resolve_hl_entry


def _bars(n: int = 140, seed: int = 33) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.0, 0.5, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.3
    low = np.minimum(open_, close) - 0.3
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
    )


class TestHLStructure(unittest.TestCase):
    def test_detect_hl_signals(self) -> None:
        df = _bars()
        out = detect_hl_signals(df)
        for c in ("hl_kind", "hl_reference_high", "hl_reference_low"):
            self.assertIn(c, out.columns)

    def test_resolve_hl_entry(self) -> None:
        sig = HLSignal(kind="H2", bar_idx=10, reference_high=102.0, reference_low=100.0)
        bar = pd.Series({"open": 101.5, "high": 102.2, "low": 101.0, "close": 102.0})
        order = resolve_hl_entry(sig, bar, tick_size=1.0)
        self.assertIsNotNone(order)
        self.assertEqual(order["side"], "long")

    def test_interval_compatibility(self) -> None:
        df = _bars()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = detect_hl_signals(df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

