"""live_principles.py 单元测试."""
from __future__ import annotations

import unittest

from cta.skills.overview.live_principles import (
    DEFAULT_CIRCUIT_CFG,
    CircuitAction,
    LiveGateState,
    check_circuit,
    reconcile_positions,
)


class TestLiveGateState(unittest.TestCase):
    def test_default_ok(self) -> None:
        s = LiveGateState()
        self.assertEqual(s.stage, 1)

    def test_invalid_stage(self) -> None:
        with self.assertRaises(ValueError):
            LiveGateState(stage=0)
        with self.assertRaises(ValueError):
            LiveGateState(stage=7)

    def test_match_rate_range(self) -> None:
        with self.assertRaises(ValueError):
            LiveGateState(signal_match_rate=-0.1)
        with self.assertRaises(ValueError):
            LiveGateState(signal_match_rate=1.5)

    def test_dd_negative(self) -> None:
        with self.assertRaises(ValueError):
            LiveGateState(daily_drawdown=-0.01)

    def test_consec_neg(self) -> None:
        with self.assertRaises(ValueError):
            LiveGateState(consecutive_losses=-1)


class TestCheckCircuit(unittest.TestCase):
    def test_no_trigger(self) -> None:
        s = LiveGateState(daily_drawdown=0.01, consecutive_losses=2,
                          signal_match_rate=0.999)
        self.assertEqual(check_circuit(s), [])

    def test_warn_dd(self) -> None:
        s = LiveGateState(daily_drawdown=0.035)  # 介于 warn(3%) 和 hard(5%)
        acts = check_circuit(s)
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0].reason, "daily_dd")
        self.assertEqual(acts[0].severity, "warn")

    def test_halt_dd(self) -> None:
        s = LiveGateState(daily_drawdown=0.06)
        acts = check_circuit(s)
        self.assertEqual(acts[0].severity, "halt")

    def test_stop_consecutive_loss(self) -> None:
        s = LiveGateState(consecutive_losses=10)
        acts = check_circuit(s)
        self.assertEqual(acts[0].reason, "consecutive_loss")
        self.assertEqual(acts[0].severity, "stop")

    def test_stop_signal_mismatch(self) -> None:
        s = LiveGateState(signal_match_rate=0.90)
        acts = check_circuit(s)
        self.assertEqual(acts[0].reason, "signal_mismatch")
        self.assertEqual(acts[0].severity, "stop")

    def test_multiple_sorted_by_severity(self) -> None:
        s = LiveGateState(
            daily_drawdown=0.035,       # warn
            consecutive_losses=10,      # stop
            signal_match_rate=0.96,     # warn
        )
        acts = check_circuit(s)
        # stop 应排首位
        self.assertEqual(acts[0].severity, "stop")
        self.assertEqual(acts[-1].severity, "warn")

    def test_cfg_override(self) -> None:
        s = LiveGateState(daily_drawdown=0.04)
        # 调低 warn 阈值到 1%，调高 hard 到 10%
        acts = check_circuit(s, cfg={"daily_dd_warn": 0.01, "daily_dd_hard": 0.10})
        # 仅触发 warn，不触发 hard
        severities = [a.severity for a in acts if a.reason == "daily_dd"]
        self.assertEqual(severities, ["warn"])

    def test_default_cfg_constants(self) -> None:
        # 防止意外修改默认配置
        self.assertEqual(DEFAULT_CIRCUIT_CFG["daily_dd_hard"], 0.05)
        self.assertEqual(DEFAULT_CIRCUIT_CFG["consecutive_loss_cap"], 10)


class TestReconcilePositions(unittest.TestCase):
    def test_all_match(self) -> None:
        s = {"RB0.SHFE": 2, "CU0.SHFE": 0}
        b = {"RB0.SHFE": 2, "CU0.SHFE": 0}
        self.assertEqual(reconcile_positions(s, b), [])

    def test_missing_key(self) -> None:
        s = {"RB0.SHFE": 2}
        b = {"RB0.SHFE": 2, "CU0.SHFE": 1}   # 券商多出 CU0
        diffs = reconcile_positions(s, b)
        self.assertIn("CU0.SHFE", diffs)

    def test_ignore_zero(self) -> None:
        s = {"RB0.SHFE": 0}
        b = {"RB0.SHFE": 0, "CU0.SHFE": 0}
        self.assertEqual(reconcile_positions(s, b), [])
        # ignore_zero=False 时，仅存在差集就算不一致（但都是 0 不算）
        self.assertEqual(reconcile_positions(s, b, ignore_zero=False), [])

    def test_sign_diff(self) -> None:
        s = {"RB0.SHFE": 2}
        b = {"RB0.SHFE": -2}
        self.assertEqual(reconcile_positions(s, b), ["RB0.SHFE"])


if __name__ == "__main__":
    unittest.main()
