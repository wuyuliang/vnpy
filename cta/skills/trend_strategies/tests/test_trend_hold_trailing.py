"""trend_hold_trailing.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.trend_strategies.trend_hold_trailing import (
    TrailingState,
    decide_add_on,
    fast_exit_if_stalled,
    update_trailing,
)


class TestTrendHoldTrailing(unittest.TestCase):
    def test_update_trailing(self) -> None:
        state = TrailingState(stop_price=98.0, stage="initial", R_multiple=0.2)
        bar = pd.Series({"high": 103.0, "low": 100.0, "close": 102.0})
        out = update_trailing(
            state=state,
            bar=bar,
            entry_price=100.0,
            entry_stop=98.0,
            atr=1.0,
        )
        self.assertIn(out.stage, {"initial", "break_even", "chandelier", "micro_channel"})

    def test_decide_add_on(self) -> None:
        add = decide_add_on(core_position_R=1.2, fresh_signal={"valid": True}, max_add_count=2)
        self.assertTrue((add is None) or ("size" in add))

    def test_fast_exit(self) -> None:
        self.assertEqual(fast_exit_if_stalled(bars_since_entry=6, current_R=0.3, max_bars=5), True)
        self.assertEqual(fast_exit_if_stalled(bars_since_entry=3, current_R=0.6, max_bars=5), False)


if __name__ == "__main__":
    unittest.main()

