"""cta.live.kill_switch 单测。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.live.kill_switch import KillSwitch, KillSwitchRule
from cta.live.risk import RiskContext


def _ctx() -> RiskContext:
    return RiskContext(pos={}, daily_pnl=0.0, capital=1_000_000.0, now=pd.Timestamp.now("UTC"))


class TestKillSwitch(unittest.TestCase):
    def test_inactive_by_default(self) -> None:
        s = KillSwitch()
        active, reason = s.is_active()
        self.assertFalse(active)
        self.assertEqual(reason, "")

    def test_activate_in_memory(self) -> None:
        s = KillSwitch()
        s.activate("manual stop")
        active, reason = s.is_active()
        self.assertTrue(active)
        self.assertEqual(reason, "manual stop")

    def test_deactivate(self) -> None:
        s = KillSwitch()
        s.activate("x")
        s.deactivate()
        self.assertFalse(s.is_active()[0])

    def test_signal_file_activates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sig = Path(tmp) / "kill.signal"
            s = KillSwitch(signal_file=sig)
            self.assertFalse(s.is_active()[0])
            sig.write_text("daily-loss-trip", encoding="utf-8")
            active, reason = s.is_active()
            self.assertTrue(active)
            self.assertIn("daily-loss-trip", reason)

    def test_signal_file_can_be_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sig = Path(tmp) / "kill.signal"
            sig.write_text("x", encoding="utf-8")
            s = KillSwitch(signal_file=sig)
            self.assertTrue(s.is_active()[0])
            sig.unlink()
            self.assertFalse(s.is_active()[0])


class TestKillSwitchRule(unittest.TestCase):
    def test_blocks_when_active(self) -> None:
        s = KillSwitch()
        s.activate("trip")
        rule = KillSwitchRule(s)
        d = rule.check({"vt_symbol": "rb888.SHFE", "direction": "long",
                        "offset": "open", "volume": 1, "price": 100.0}, _ctx())
        self.assertFalse(d.allowed)
        self.assertIn("kill_switch", d.reason)
        self.assertIn("trip", d.reason)

    def test_passes_when_inactive(self) -> None:
        rule = KillSwitchRule(KillSwitch())
        d = rule.check({"vt_symbol": "rb888.SHFE", "direction": "long",
                        "offset": "open", "volume": 1, "price": 100.0}, _ctx())
        self.assertTrue(d.allowed)


if __name__ == "__main__":
    unittest.main()
