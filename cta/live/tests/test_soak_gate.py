"""P3-23/24/27: sim soak gate + rollout plan + rollback procedure tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from cta.live.soak_gate import (
    DEFAULT_ROLLBACK_TRIGGERS,
    DEFAULT_ROLLOUT_STAGES,
    DailyParitySnapshot,
    RollbackChecklist,
    RolloutStage,
    SoakReport,
)


def _snap(day_offset: int, *, pnl_diff_bp: float = 1.0, mismatched: int = 0) -> DailyParitySnapshot:
    return DailyParitySnapshot(
        trade_date=date(2024, 3, 1) + timedelta(days=day_offset),
        n_sim_trades=10, n_oot_trades=10,
        matched=10 - mismatched,
        mismatched=mismatched,
        only_in_sim=0, only_in_oot=0,
        cumulative_pnl_diff_bp=pnl_diff_bp,
    )


class TestSoakGate(unittest.TestCase):
    def test_insufficient_days_fails_gate(self) -> None:
        report = SoakReport(required_days=30)
        for i in range(15):
            report.append(_snap(i))
        ok, reason = report.gate_passes()
        self.assertFalse(ok)
        self.assertIn("insufficient_days", reason)

    def test_too_many_failed_days_fails(self) -> None:
        report = SoakReport(required_days=10, allowed_failed_days=1)
        for i in range(10):
            mm = 5 if i < 3 else 0  # 前 3 天 mismatched 多 → 不 pass
            report.append(_snap(i, mismatched=mm))
        ok, reason = report.gate_passes()
        self.assertFalse(ok)
        self.assertIn("too_many_failed", reason)

    def test_pnl_drift_too_large_fails(self) -> None:
        report = SoakReport(required_days=5, max_pnl_diff_pct=0.001)
        for i in range(5):
            # 最后一天 cumulative 大幅漂移
            pnl = 100.0 if i == 4 else 1.0
            report.append(_snap(i, pnl_diff_bp=pnl))
        ok, reason = report.gate_passes()
        # 100bp = 1% > 0.1% threshold
        self.assertFalse(ok)
        self.assertIn("pnl_drift_too_large", reason)

    def test_clean_30_days_passes(self) -> None:
        report = SoakReport(required_days=30)
        for i in range(30):
            report.append(_snap(i, pnl_diff_bp=2.0))
        ok, reason = report.gate_passes()
        self.assertTrue(ok)
        self.assertIn("passed", reason)
        self.assertEqual(report.days_passed, 30)

    def test_save_json_roundtrip(self) -> None:
        report = SoakReport(required_days=3)
        for i in range(3):
            report.append(_snap(i))
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "soak.json"
            report.save_to_json(p)
            self.assertTrue(p.exists())
            data = json.loads(p.read_text())
            self.assertEqual(data["required_days"], 3)
            self.assertEqual(len(data["snapshots"]), 3)


class TestRolloutStage(unittest.TestCase):
    def test_default_stages_exist(self) -> None:
        self.assertGreaterEqual(len(DEFAULT_ROLLOUT_STAGES), 3)
        names = [s.name for s in DEFAULT_ROLLOUT_STAGES]
        self.assertIn("single_symbol", names)
        self.assertIn("single_cluster", names)
        self.assertIn("full_universe", names)

    def test_stage_complete_when_criteria_met(self) -> None:
        stage = RolloutStage(
            name="s", symbols=("RB0",), min_days=5,
            max_daily_loss_pct=0.01, capital_at_risk=10_000.0,
        )
        ok, reason = stage.is_complete(days_run=5, max_daily_loss_seen=0.005)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_stage_incomplete_when_days_short(self) -> None:
        stage = DEFAULT_ROLLOUT_STAGES[0]
        ok, reason = stage.is_complete(days_run=2, max_daily_loss_seen=0.001)
        self.assertFalse(ok)
        self.assertIn("days", reason)

    def test_stage_incomplete_when_loss_breach(self) -> None:
        stage = RolloutStage(
            name="s", symbols=("RB0",), min_days=5,
            max_daily_loss_pct=0.01, capital_at_risk=10_000.0,
        )
        ok, reason = stage.is_complete(days_run=10, max_daily_loss_seen=0.02)
        self.assertFalse(ok)
        self.assertIn("daily_loss", reason)


class TestRollbackProcedure(unittest.TestCase):
    def test_default_triggers_present(self) -> None:
        names = [t.name for t in DEFAULT_ROLLBACK_TRIGGERS]
        # 关键触发器都在
        for required in ("daily_pnl_critical", "max_drawdown_breach", "kill_switch_repeated"):
            self.assertIn(required, names)

    def test_checklist_renders_steps(self) -> None:
        cl = RollbackChecklist()
        text = cl.render()
        self.assertIn("kill_switch.activate", text)
        self.assertIn("cluster_registry.json", text)
        self.assertGreaterEqual(text.count("\n"), 5)


if __name__ == "__main__":
    unittest.main()
