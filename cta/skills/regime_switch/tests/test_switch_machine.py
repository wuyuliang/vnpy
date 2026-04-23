"""switch_machine.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.regime_switch.switch_machine import (
    allowed_strategies,
    compute_regime,
)


def _toy_inputs(n: int = 120) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    dt = pd.date_range("2024-01-01", periods=n, freq="D")
    df = pd.DataFrame({"datetime": dt})
    trend = pd.Series(np.zeros(n), index=df.index, dtype=float)
    range_score = pd.Series(np.zeros(n), index=df.index, dtype=float)
    vol_state = pd.Series(["normal"] * n, index=df.index, dtype=object)

    # trend_up
    trend.iloc[20:50] = 0.8
    vol_state.iloc[20:50] = "high"
    # compression
    range_score.iloc[50:70] = 0.8
    vol_state.iloc[50:70] = "compression"
    # expansion trending down
    trend.iloc[70:90] = -0.85
    vol_state.iloc[70:90] = "expansion"
    # range
    range_score.iloc[90:110] = 0.9
    vol_state.iloc[90:110] = "normal"
    return df, trend, range_score, vol_state


class TestSwitchMachine(unittest.TestCase):
    def test_compute_regime_columns(self) -> None:
        df, trend, range_score, vol_state = _toy_inputs()
        out = compute_regime(
            df=df,
            trend_score=trend,
            range_score=range_score,
            vol_state=vol_state,
            interval="day",
            min_switch_bars=2,
        )
        self.assertEqual(len(out), len(df))
        for col in (
            "regime_label",
            "regime_conf",
            "regime_age",
            "transition_flag",
            "transition_risk",
            "next_regime",
            "next_regime_prob",
        ):
            self.assertIn(col, out.columns, col)

    def test_expected_regime_segments_exist(self) -> None:
        df, trend, range_score, vol_state = _toy_inputs()
        out = compute_regime(
            df=df,
            trend_score=trend,
            range_score=range_score,
            vol_state=vol_state,
            interval="minute30",
            min_switch_bars=2,
        )
        labels = set(out["regime_label"].unique())
        self.assertIn("trend_up", labels)
        self.assertIn("compression", labels)
        self.assertIn("range", labels)

    def test_interval_compatibility(self) -> None:
        df, trend, range_score, vol_state = _toy_inputs()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = compute_regime(
                df=df,
                trend_score=trend,
                range_score=range_score,
                vol_state=vol_state,
                interval=interval,
            )
            self.assertEqual(len(out), len(df), interval)

    def test_allowed_strategies(self) -> None:
        self.assertGreater(len(allowed_strategies("trend_up")), 0)
        self.assertEqual(allowed_strategies("transition"), [])


if __name__ == "__main__":
    unittest.main()

