"""Unit tests for runtime portfolio state."""
from __future__ import annotations

import unittest

from cta.portfolio_logic.portfolio_state import PortfolioState


class TestPortfolioState(unittest.TestCase):
    def test_begin_commit_and_rollback_allocation(self) -> None:
        state = PortfolioState(equity=1_000_000.0)
        state.begin_allocation()
        state.tentative_apply(("RB0", "SHFE"), "black", 100_000.0, "Long")
        self.assertEqual(state.tentative_total_positions(), 1)
        state.rollback_allocation()
        self.assertEqual(state.tentative_total_positions(), 0)
        state.tentative_apply(("RB0", "SHFE"), "black", 100_000.0, "Long")
        state.commit_allocation()
        self.assertEqual(state.total_positions, 1)
        self.assertAlmostEqual(state.total_open_notional, 100_000.0, places=6)
        self.assertTrue(state.has_open_or_picked(("RB0", "SHFE"), "long"))

    def test_apply_exits_updates_counters(self) -> None:
        state = PortfolioState(equity=1_000_000.0)
        state.add_position(
            {
                "pos_id": "p1",
                "symbol": "RB0",
                "exchange": "SHFE",
                "cluster": "black",
                "direction": "LONG",
                "notional": 120_000.0,
            }
        )
        state.apply_exits(
            [
                {
                    "symbol": "RB0",
                    "exchange": "SHFE",
                    "cluster": "black",
                    "direction": "long",
                    "notional": 120_000.0,
                    "count": 1,
                }
            ]
        )
        self.assertEqual(state.total_positions, 0)
        self.assertAlmostEqual(state.total_open_notional, 0.0, places=6)
        self.assertFalse(state.has_open_or_picked(("RB0", "SHFE"), "long"))

    def test_add_remove_position_and_trade_log(self) -> None:
        state = PortfolioState(equity=1_000_000.0)
        state.add_position(
            {
                "pos_id": "p2",
                "symbol": "CU0",
                "exchange": "SHFE",
                "cluster": "metal",
                "direction": "short",
                "notional": 90_000.0,
            }
        )
        self.assertEqual(state.total_positions, 1)
        state.record_trades([{"trade_id": "t1"}, {"trade_id": "t2"}])
        self.assertEqual(len(state.trade_log), 2)
        state.remove_position("p2")
        self.assertEqual(state.total_positions, 0)

    def test_to_dict_from_dict_roundtrip(self) -> None:
        state = PortfolioState(equity=500_000.0)
        state.add_position(
            {
                "pos_id": "p3",
                "symbol": "JM0",
                "exchange": "DCE",
                "cluster": "black",
                "direction": "Short",
                "notional": 80_000.0,
            }
        )
        payload = state.to_dict()
        restored = PortfolioState.from_dict(payload)
        self.assertEqual(restored.total_positions, 1)
        self.assertAlmostEqual(restored.total_open_notional, 80_000.0, places=6)
        self.assertTrue(restored.has_open_or_picked(("JM0", "DCE"), "short"))

    def test_rollback_restores_begin_snapshot_even_if_committed_mutated(self) -> None:
        state = PortfolioState(equity=1_000_000.0)
        state.add_position(
            {
                "pos_id": "p1",
                "symbol": "RB0",
                "exchange": "SHFE",
                "cluster": "black",
                "direction": "long",
                "notional": 120_000.0,
            }
        )
        state.begin_allocation()
        state.tentative_apply(("CU0", "SHFE"), "metal", 80_000.0, "short")
        self.assertEqual(state.tentative_total_positions(), 2)

        # 模拟 begin 之后 committed 被外部路径污染；rollback 应回到 begin 快照。
        state.total_positions = 99
        state.total_open_notional = 9_900_000.0
        state.symbol_counts = {}
        state.cluster_counts = {}
        state.symbol_notional = {}
        state.cluster_notional = {}
        state.open_symbol_direction = set()

        state.rollback_allocation()
        self.assertEqual(state.tentative_total_positions(), 1)
        self.assertAlmostEqual(state.tentative_total_notional(), 120_000.0, places=6)
        self.assertTrue(state.has_open_or_picked(("RB0", "SHFE"), "long"))


if __name__ == "__main__":
    unittest.main()
