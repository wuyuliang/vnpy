"""volatility_transition.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.regime_switch.volatility_transition import (
    detect_vol_transition,
    rollback_if_false_switch,
)


def _synth_switch_df(n: int = 500, seed: int = 7) -> pd.DataFrame:
    """Low-vol compression then high-vol expansion synthetic bars."""
    rng = np.random.default_rng(seed)
    half = n // 2
    low_vol_ret = rng.normal(0.0, 0.002, half)
    high_vol_ret = rng.normal(0.001, 0.02, n - half)
    ret = np.concatenate([low_vol_ret, high_vol_ret])
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.003, n)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.integers(1000, 5000, n).astype(float)
    dt = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


class TestDetectVolTransition(unittest.TestCase):
    def test_output_columns(self) -> None:
        df = _synth_switch_df()
        out = detect_vol_transition(df, interval="day")
        self.assertEqual(len(out), len(df))
        for col in (
            "vol_transition_state",
            "vol_transition_candidate",
            "vol_transition_confidence",
            "vol_transition_event",
            "vol_transition_age",
            "atr_fast",
            "atr_slow",
            "atr_ratio",
            "atr_pct",
            "bb_width_pct",
        ):
            self.assertIn(col, out.columns, col)

    def test_can_detect_expansion_after_switch(self) -> None:
        df = _synth_switch_df(n=700)
        out = detect_vol_transition(
            df,
            interval="day",
            persistence_bars=3,
            compression_min_bars=15,
            breakout_window=10,
        )
        tail = out.iloc[400:]
        self.assertGreater(
            int((tail["vol_transition_state"] == "expansion").sum()),
            10,
        )

    def test_interval_compatibility(self) -> None:
        df = _synth_switch_df(n=450)
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = detect_vol_transition(
                df,
                interval=interval,
                pct_window=100,
                persistence_bars=2,
                compression_min_bars=8,
            )
            vals = set(out["vol_transition_state"].dropna().unique())
            self.assertTrue(
                vals.issubset({"compression", "expansion", "normal"}),
                f"{interval}: {vals}",
            )


class TestRollback(unittest.TestCase):
    def test_short_blip_can_be_rolled_back(self) -> None:
        s = pd.Series(
            [
                "normal",
                "normal",
                "expansion",
                "expansion",
                "normal",
                "normal",
            ]
        )
        rolled = rollback_if_false_switch(
            s, max_rollback_bars=5, min_hold_bars=3
        )
        self.assertTrue((rolled == "normal").all(), rolled.tolist())

    def test_long_segment_not_rolled_back(self) -> None:
        s = pd.Series(["normal"] * 3 + ["expansion"] * 8 + ["normal"] * 3)
        rolled = rollback_if_false_switch(
            s, max_rollback_bars=5, min_hold_bars=3
        )
        self.assertGreater(int((rolled == "expansion").sum()), 0)


if __name__ == "__main__":
    unittest.main()

