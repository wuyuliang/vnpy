"""P2-17: CTP 单据 6 种状态全覆盖测试."""
from __future__ import annotations

import unittest

from cta.live.order_lifecycle import (
    OrderLifecycle,
    OrderLifecycleEvent,
    STATUSES,
    TERMINAL,
)


def _ev(order_id: str, status: str, traded: float = 0.0, reason: str = "") -> OrderLifecycleEvent:
    return OrderLifecycleEvent(
        order_id=order_id, new_status=status,
        traded_volume=traded, rejection_reason=reason,
    )


class TestOrderLifecycle(unittest.TestCase):
    def test_initial_state(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        self.assertEqual(ol.status, "submitting")
        self.assertEqual(ol.traded_volume, 0.0)
        self.assertFalse(ol.is_terminal())
        self.assertEqual(ol.history, [("submitting", 0.0)])

    def test_normal_full_path_submit_to_alltraded(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "nottraded"))
        ol.apply_event(_ev("O1", "parttraded", traded=2.0))
        ol.apply_event(_ev("O1", "alltraded", traded=5.0))
        self.assertEqual(ol.status, "alltraded")
        self.assertEqual(ol.traded_volume, 5.0)
        self.assertTrue(ol.is_terminal())

    def test_submit_to_rejected(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "rejected", reason="insufficient_margin"))
        self.assertEqual(ol.status, "rejected")
        self.assertEqual(ol.rejection_reason, "insufficient_margin")
        self.assertTrue(ol.is_terminal())

    def test_submit_to_cancelled_before_nottraded(self) -> None:
        """快速撤单：order 还没进交易所就被撤。"""
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "cancelled"))
        self.assertEqual(ol.status, "cancelled")
        self.assertTrue(ol.is_terminal())

    def test_nottraded_to_cancelled(self) -> None:
        """挂单后超时未成交 → 撤单。"""
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "nottraded"))
        ol.apply_event(_ev("O1", "cancelled"))
        self.assertEqual(ol.status, "cancelled")

    def test_parttraded_to_cancelled(self) -> None:
        """部分成交 + 撤单（撤掉剩余）。"""
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "nottraded"))
        ol.apply_event(_ev("O1", "parttraded", traded=3.0))
        ol.apply_event(_ev("O1", "cancelled", traded=3.0))
        self.assertEqual(ol.status, "cancelled")
        self.assertEqual(ol.traded_volume, 3.0)

    # ── 非法转移 ────────────────────────────────────────────────

    def test_illegal_transition_alltraded_to_nottraded(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "nottraded"))
        ol.apply_event(_ev("O1", "alltraded", traded=5.0))
        with self.assertRaises(RuntimeError):
            ol.apply_event(_ev("O1", "nottraded"))

    def test_illegal_transition_rejected_to_alltraded(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "rejected"))
        with self.assertRaises(RuntimeError):
            ol.apply_event(_ev("O1", "alltraded", traded=5.0))

    def test_illegal_transition_cancelled_to_parttraded(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "cancelled"))
        with self.assertRaises(RuntimeError):
            ol.apply_event(_ev("O1", "parttraded", traded=2.0))

    # ── 成交量校验 ────────────────────────────────────────────

    def test_traded_volume_cannot_decrease(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "nottraded"))
        ol.apply_event(_ev("O1", "parttraded", traded=3.0))
        with self.assertRaises(ValueError):
            ol.apply_event(_ev("O1", "parttraded", traded=2.0))  # 倒退

    def test_traded_volume_cannot_exceed_total(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "nottraded"))
        with self.assertRaises(ValueError):
            ol.apply_event(_ev("O1", "parttraded", traded=6.0))

    def test_alltraded_auto_sets_traded_to_total(self) -> None:
        """alltraded 时即使 event 传 0 也自动补齐。"""
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        ol.apply_event(_ev("O1", "nottraded"))
        ol.apply_event(_ev("O1", "alltraded", traded=4.999))
        self.assertEqual(ol.traded_volume, 5.0)

    # ── ID / status 错误 ──────────────────────────────────────

    def test_order_id_mismatch_raises(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        with self.assertRaises(ValueError):
            ol.apply_event(_ev("O_OTHER", "nottraded"))

    def test_unknown_status_rejected(self) -> None:
        ol = OrderLifecycle(order_id="O1", total_volume=5.0)
        with self.assertRaises(ValueError):
            ol.apply_event(_ev("O1", "not_a_real_status"))

    def test_invalid_initial_total_volume(self) -> None:
        with self.assertRaises(ValueError):
            OrderLifecycle(order_id="O1", total_volume=0.0)
        with self.assertRaises(ValueError):
            OrderLifecycle(order_id="O1", total_volume=-1.0)

    # ── 6 种状态全覆盖 ────────────────────────────────────────

    def test_all_6_statuses_reachable(self) -> None:
        """6 种状态都至少有一条路径可达。"""
        reached: set[str] = set()
        # path 1: submitting → nottraded → parttraded → alltraded
        ol = OrderLifecycle(order_id="A", total_volume=5)
        reached.add(ol.status)
        ol.apply_event(_ev("A", "nottraded"))
        reached.add(ol.status)
        ol.apply_event(_ev("A", "parttraded", traded=2))
        reached.add(ol.status)
        ol.apply_event(_ev("A", "alltraded", traded=5))
        reached.add(ol.status)
        # path 2: submitting → rejected
        ol2 = OrderLifecycle(order_id="B", total_volume=5)
        ol2.apply_event(_ev("B", "rejected"))
        reached.add(ol2.status)
        # path 3: submitting → cancelled
        ol3 = OrderLifecycle(order_id="C", total_volume=5)
        ol3.apply_event(_ev("C", "cancelled"))
        reached.add(ol3.status)
        self.assertEqual(reached, set(STATUSES))


if __name__ == "__main__":
    unittest.main()
