"""Tests for CrossSectionalRotationConfig."""
from __future__ import annotations

import unittest

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig


class TestCrossSectionalRotationConfig(unittest.TestCase):
    def test_defaults_disabled(self) -> None:
        cfg = CrossSectionalRotationConfig()
        self.assertFalse(cfg.use_cross_sectional_momentum_rotation)
        self.assertFalse(cfg.is_enabled("index", "day"))
        self.assertEqual(cfg.lookback_days, 60)
        self.assertEqual(cfg.skip_recent_days, 5)

    def test_is_enabled_explicit_cluster(self) -> None:
        cfg = CrossSectionalRotationConfig(
            use_cross_sectional_momentum_rotation=True,
            enabled_by_cluster_interval={"index|day": True, "black|day": True},
        )
        self.assertTrue(cfg.is_enabled("index", "day"))
        self.assertTrue(cfg.is_enabled("black", "day"))
        self.assertFalse(cfg.is_enabled("metal", "day"))
        self.assertFalse(cfg.is_enabled("index", "60min"))

    def test_is_enabled_wildcard(self) -> None:
        cfg = CrossSectionalRotationConfig(
            use_cross_sectional_momentum_rotation=True,
            enabled_by_cluster_interval={"*|day": True},
        )
        self.assertTrue(cfg.is_enabled("index", "day"))
        self.assertTrue(cfg.is_enabled("agri", "day"))
        self.assertFalse(cfg.is_enabled("index", "60min"))

    def test_is_enabled_use_flag_off(self) -> None:
        cfg = CrossSectionalRotationConfig(
            use_cross_sectional_momentum_rotation=False,
            enabled_by_cluster_interval={"*|day": True},
        )
        self.assertFalse(cfg.is_enabled("index", "day"))

    def test_post_init_rejects_bad_quantile_order(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(top_quantile=0.50, bottom_quantile=0.60)

    def test_post_init_rejects_bad_quantile_range(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(top_quantile=1.5)
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(bottom_quantile=-0.1)

    def test_post_init_rejects_bad_cluster_quantile(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(top_quantile_in_cluster=1.5)
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(bottom_quantile_in_cluster=0.0)

    def test_post_init_rejects_lookback_le_skip(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(lookback_days=5, skip_recent_days=10)
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(lookback_days=5, skip_recent_days=5)

    def test_post_init_rejects_bad_weekday(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(rebalance_weekday=7)
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(rebalance_weekday=-1)

    def test_post_init_rejects_max_gross_le_target(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(
                gross_exposure_target=1.0, max_gross_exposure=0.5,
            )

    def test_post_init_rejects_bad_max_symbol_notional(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(max_symbol_notional_pct=0.0)
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(max_symbol_notional_pct=1.5)

    def test_post_init_rejects_bad_kill_switch(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(kill_switch_dd_pct=0.0)
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(kill_switch_dd_pct=1.5)

    def test_post_init_rejects_negative_stop_loss(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(stop_loss_pct=0.0)
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(stop_loss_pct=-0.05)

    def test_post_init_rejects_bad_keys(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(
                use_cross_sectional_momentum_rotation=True,
                enabled_by_cluster_interval={"foo": True},
            )
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(
                use_cross_sectional_momentum_rotation=True,
                enabled_by_cluster_interval={"foo|day|extra": True},
            )
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(
                use_cross_sectional_momentum_rotation=True,
                enabled_by_cluster_interval={"|day": True},
            )

    def test_post_init_rejects_non_bool_value(self) -> None:
        with self.assertRaises(ValueError):
            CrossSectionalRotationConfig(
                use_cross_sectional_momentum_rotation=True,
                enabled_by_cluster_interval={"index|day": "yes"},  # type: ignore[dict-item]
            )

    def test_universe_clusters_normalized_to_lower(self) -> None:
        cfg = CrossSectionalRotationConfig(universe_clusters=("INDEX", "Black", " metal "))
        self.assertEqual(cfg.universe_clusters, ("index", "black", "metal"))

    def test_interval_alias_normalized(self) -> None:
        cfg = CrossSectionalRotationConfig(
            use_cross_sectional_momentum_rotation=True,
            enabled_by_cluster_interval={"index|60min": True},
        )
        # 别名也应该命中
        self.assertTrue(cfg.is_enabled("index", "60min"))


if __name__ == "__main__":
    unittest.main()
