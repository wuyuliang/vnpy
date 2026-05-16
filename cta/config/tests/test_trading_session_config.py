"""Tests for trading session config."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.trading_session_config import (
    BOND_FUTURES,
    COMMODITY_NIGHT_01,
    COMMODITY_NIGHT_0230,
    COMMODITY_NIGHT_23,
    INDEX_FUTURES,
    get_session_for_symbol,
)


class TestTradingSessionConfig(unittest.TestCase):
    def test_get_session_for_symbol_routes_by_cluster(self) -> None:
        self.assertEqual(get_session_for_symbol("IF0").name, INDEX_FUTURES.name)
        self.assertEqual(get_session_for_symbol("T0").name, BOND_FUTURES.name)
        self.assertEqual(get_session_for_symbol("RB0").name, COMMODITY_NIGHT_23.name)
        self.assertEqual(get_session_for_symbol("RU0").name, COMMODITY_NIGHT_01.name)
        self.assertEqual(get_session_for_symbol("AU0").name, COMMODITY_NIGHT_0230.name)

    def test_expected_bar_count_for_60min(self) -> None:
        self.assertEqual(INDEX_FUTURES.expected_bar_count(60), 4)
        self.assertEqual(BOND_FUTURES.expected_bar_count(60), 4)

    def test_is_within(self) -> None:
        ts_in = pd.Timestamp("2025-01-02 09:45:00")
        ts_out = pd.Timestamp("2025-01-02 12:00:00")
        self.assertTrue(INDEX_FUTURES.is_within(ts_in))
        self.assertFalse(INDEX_FUTURES.is_within(ts_out))

    def test_night_session_segments_are_symbol_specific(self) -> None:
        # RB: 夜盘到 23:00，01:30 不应在 session 内
        self.assertFalse(get_session_for_symbol("RB0").is_within(pd.Timestamp("2025-01-03 01:30:00")))
        # RU: 夜盘到 01:00，00:30 在，01:30 不在
        self.assertTrue(get_session_for_symbol("RU0").is_within(pd.Timestamp("2025-01-03 00:30:00")))
        self.assertFalse(get_session_for_symbol("RU0").is_within(pd.Timestamp("2025-01-03 01:30:00")))
        # AU: 夜盘到 02:30，01:30 在
        self.assertTrue(get_session_for_symbol("AU0").is_within(pd.Timestamp("2025-01-03 01:30:00")))


if __name__ == "__main__":
    unittest.main()
