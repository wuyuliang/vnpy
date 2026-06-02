"""Rotation intent -> legacy order mapping tests (P1-7)."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.cross_sectional_rotation_executor import RotationOrderIntent
from cta.sim.adapters.rotation_stepper import intent_to_legacy_order


class TestRotationOrderMapper(unittest.TestCase):
    def _intent(self, **kwargs) -> RotationOrderIntent:
        base = RotationOrderIntent(
            symbol="IF0",
            exchange="CFFEX",
            side="long",
            signal_type="cross_sectional_momentum",
            signal_datetime=pd.Timestamp("2026-01-02"),
            entry_datetime=pd.Timestamp("2026-01-03"),
            planned_exit_datetime=pd.Timestamp("2026-01-10"),
            stop_price=3950.0,
            target_weight=0.2,
            target_notional=200_000.0,
            candidate={"foo": "bar"},
        )
        if not kwargs:
            return base
        payload = dict(base.__dict__)
        payload.update(kwargs)
        return RotationOrderIntent(**payload)

    def test_maps_notional_to_lots(self) -> None:
        # IF 类似：price=4000, contract_size=300 => per-lot notional=1,200,000
        # 200,000 target_notional -> 0.16 lot，向下取整后按 min_lots=1 变成 1
        order = intent_to_legacy_order(
            self._intent(),
            price=4000.0,
            contract_size=300.0,
            lot_size=1,
        )
        self.assertEqual(order["side"], "long")
        self.assertEqual(order["lots"], 1)
        self.assertEqual(order["price"], 4000.0)

    def test_respects_max_lots_cap(self) -> None:
        order = intent_to_legacy_order(
            self._intent(target_notional=9_000_000.0),
            price=3000.0,
            contract_size=10.0,
            lot_size=1,
            max_lots=5,
        )
        self.assertEqual(order["lots"], 5)

    def test_short_side_preserved(self) -> None:
        order = intent_to_legacy_order(
            self._intent(side="short"),
            price=4000.0,
            contract_size=300.0,
            lot_size=1,
        )
        self.assertEqual(order["side"], "short")


if __name__ == "__main__":
    unittest.main()

