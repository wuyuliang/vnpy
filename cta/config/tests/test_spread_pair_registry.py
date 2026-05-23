"""Tests for spread pair registry."""
from __future__ import annotations

import unittest

from cta.config.spread_pair_registry import (
    DEFAULT_CALENDAR_PAIRS,
    DEFAULT_CROSS_INSTRUMENT_PAIRS,
    SpreadPair,
    get_spread_pair_by_key,
)


class TestSpreadPairRegistry(unittest.TestCase):
    def test_default_pairs_contain_expected_keys(self) -> None:
        keys = {pair.pair_key for pair in DEFAULT_CROSS_INSTRUMENT_PAIRS}
        self.assertIn("rb_hc", keys)
        self.assertIn("j_jm", keys)
        cal_keys = {pair.pair_key for pair in DEFAULT_CALENDAR_PAIRS}
        self.assertIn("rb_cal_1_3", cal_keys)

    def test_get_spread_pair_by_key(self) -> None:
        pair = get_spread_pair_by_key("rb_hc")
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertEqual(pair.pair_type, "cross_instrument")
        self.assertEqual(pair.leg1_symbol, "RB")
        self.assertEqual(pair.leg2_symbol, "HC")

    def test_rejects_invalid_pair_type(self) -> None:
        with self.assertRaises(ValueError):
            SpreadPair(
                pair_key="bad",
                pair_type="unknown",
                leg1_symbol="RB",
                leg2_symbol="HC",
                leg1_exchange="SHFE",
                leg2_exchange="SHFE",
            )

    def test_rejects_non_positive_hedge_ratio(self) -> None:
        with self.assertRaises(ValueError):
            SpreadPair(
                pair_key="bad",
                pair_type="cross_instrument",
                leg1_symbol="RB",
                leg2_symbol="HC",
                leg1_exchange="SHFE",
                leg2_exchange="SHFE",
                hedge_ratio=0.0,
            )


if __name__ == "__main__":
    unittest.main()

