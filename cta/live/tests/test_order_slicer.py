"""Tests for cta.live.order_slicer."""
from __future__ import annotations

import unittest

from cta.live.order_slicer import OrderSliceConfig, slice_order


class TestOrderSlicer(unittest.TestCase):
    def test_iceberg_respects_max_order_volume(self) -> None:
        cfg = OrderSliceConfig(max_order_volume=3, method="iceberg")
        child = slice_order(total_lots=8, adv_lots=None, cfg=cfg)
        self.assertEqual(sum(child), 8)
        self.assertTrue(all(v <= 3 for v in child))

    def test_adv_participation_cap_applies(self) -> None:
        cfg = OrderSliceConfig(max_order_volume=10, adv_participation_rate=0.05, method="iceberg")
        # adv=100 -> 每子单上限 5 手
        child = slice_order(total_lots=12, adv_lots=100, cfg=cfg)
        self.assertEqual(sum(child), 12)
        self.assertTrue(all(v <= 5 for v in child))

    def test_twap_roughly_even_split(self) -> None:
        cfg = OrderSliceConfig(max_order_volume=100, method="twap", twap_child_count=4)
        child = slice_order(total_lots=10, adv_lots=None, cfg=cfg)
        self.assertEqual(sum(child), 10)
        self.assertEqual(len(child), 4)
        self.assertLessEqual(max(child) - min(child), 1)


if __name__ == "__main__":
    unittest.main()
