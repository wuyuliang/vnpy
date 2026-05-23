"""Tests for spread arbitrage config."""
from __future__ import annotations

import unittest

from cta.config.spread_arbitrage_config import SpreadArbitrageConfig


class TestSpreadArbitrageConfig(unittest.TestCase):
    def test_defaults_disabled(self) -> None:
        cfg = SpreadArbitrageConfig()
        self.assertFalse(cfg.use_spread_arbitrage)
        self.assertFalse(cfg.is_enabled("rb_hc", "day"))

    def test_post_init_validates_z_order(self) -> None:
        with self.assertRaises(ValueError):
            SpreadArbitrageConfig(z_entry=2.0, z_exit=2.1, z_stop=3.0)
        with self.assertRaises(ValueError):
            SpreadArbitrageConfig(z_entry=2.0, z_exit=0.5, z_stop=2.0)

    def test_post_init_rejects_bad_pair_interval_key(self) -> None:
        with self.assertRaises(ValueError):
            SpreadArbitrageConfig(
                use_spread_arbitrage=True,
                enabled_by_pair_interval={"rb_hc": True},
            )

    def test_post_init_normalizes_interval_alias(self) -> None:
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            enabled_by_pair_interval={"rb_hc|60m": True},
        )
        self.assertTrue(cfg.is_enabled("rb_hc", "60min"))

    def test_enabled_pairs_can_enable_all_intervals(self) -> None:
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            enabled_pairs=("rb_hc",),
        )
        self.assertTrue(cfg.is_enabled("rb_hc", "day"))
        self.assertTrue(cfg.is_enabled("rb_hc", "60min"))
        self.assertFalse(cfg.is_enabled("j_jm", "day"))

    def test_pair_interval_override_has_higher_priority_than_enabled_pairs(self) -> None:
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            enabled_pairs=("rb_hc",),
            enabled_by_pair_interval={"rb_hc|day": False},
        )
        self.assertFalse(cfg.is_enabled("rb_hc", "day"))
        self.assertTrue(cfg.is_enabled("rb_hc", "60min"))


if __name__ == "__main__":
    unittest.main()

