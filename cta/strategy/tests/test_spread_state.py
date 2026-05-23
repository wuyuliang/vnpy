"""Tests for SpreadState."""
from __future__ import annotations

import unittest

import numpy as np

from cta.config.spread_pair_registry import SpreadPair
from cta.strategy.spread_state import SpreadState


def _pair() -> SpreadPair:
    return SpreadPair(
        pair_key="rb_hc",
        pair_type="cross_instrument",
        leg1_symbol="RB",
        leg2_symbol="HC",
        leg1_exchange="SHFE",
        leg2_exchange="SHFE",
        hedge_ratio=1.0,
        cluster="black",
    )


class TestSpreadState(unittest.TestCase):
    def test_warmup_and_zscore(self) -> None:
        st = SpreadState(pair=_pair(), rolling_window_days=3)
        st.update(1.0)
        st.update(2.0)
        self.assertFalse(st.is_warmup_done())
        st.update(3.0)
        self.assertTrue(st.is_warmup_done())
        z = st.zscore(4.0)
        exp = (4.0 - 2.0) / np.sqrt(2.0 / 3.0)
        self.assertAlmostEqual(float(z), float(exp), places=10)

    def test_zscore_returns_nan_when_not_warm(self) -> None:
        st = SpreadState(pair=_pair(), rolling_window_days=3)
        st.update(1.0)
        self.assertTrue(np.isnan(float(st.zscore(1.1))))

    def test_zscore_returns_nan_when_std_is_zero(self) -> None:
        st = SpreadState(pair=_pair(), rolling_window_days=3)
        st.update(1.0)
        st.update(1.0)
        st.update(1.0)
        self.assertTrue(np.isnan(float(st.zscore(1.0))))


if __name__ == "__main__":
    unittest.main()

