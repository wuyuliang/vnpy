"""Tests for cta.live.preopen_checklist."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.live.preopen_checklist import PreopenCheckConfig, run_preopen_checklist


class _FakeRegistry:
    def __init__(self, age_days: float) -> None:
        self._age = float(age_days)

    def max_model_age_days(self, *, as_of=None):  # noqa: ANN001, D401
        return self._age


class TestPreopenChecklist(unittest.TestCase):
    def test_passes_when_all_checks_green(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_preopen_ok_") as td:
            pred = Path(td) / "predictions.csv"
            pred.write_text("x", encoding="utf-8")
            ts = pd.Timestamp("2026-05-30 08:00:00").timestamp()
            os.utime(pred, (ts, ts))
            rep = run_preopen_checklist(
                predictions_path=pred,
                model_registry=_FakeRegistry(age_days=2.0),
                kill_switch_active=False,
                available_cash=5_000_000.0,
                required_margin=3_000_000.0,
                now=pd.Timestamp("2026-05-30 09:00:00"),
                cfg=PreopenCheckConfig(max_prediction_stale_hours=12.0, max_model_age_days=10.0),
            )
            self.assertTrue(rep.passed, msg=str(rep.items))

    def test_fails_when_kill_switch_active(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_preopen_kill_") as td:
            pred = Path(td) / "predictions.csv"
            pred.write_text("x", encoding="utf-8")
            rep = run_preopen_checklist(
                predictions_path=pred,
                model_registry=_FakeRegistry(age_days=2.0),
                kill_switch_active=True,
                available_cash=5_000_000.0,
                required_margin=3_000_000.0,
                now=pd.Timestamp("2026-05-30 09:00:00"),
                cfg=PreopenCheckConfig(),
            )
            self.assertFalse(rep.passed)
            self.assertTrue(any(i.name == "kill_switch" and not i.passed for i in rep.items))

    def test_fails_when_predictions_stale(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_preopen_stale_") as td:
            pred = Path(td) / "predictions.csv"
            pred.write_text("x", encoding="utf-8")
            ts = pd.Timestamp("2026-05-28 08:00:00").timestamp()
            os.utime(pred, (ts, ts))
            rep = run_preopen_checklist(
                predictions_path=pred,
                model_registry=_FakeRegistry(age_days=2.0),
                kill_switch_active=False,
                available_cash=5_000_000.0,
                required_margin=3_000_000.0,
                now=pd.Timestamp("2026-05-30 09:00:00"),
                cfg=PreopenCheckConfig(max_prediction_stale_hours=12.0, max_model_age_days=10.0),
            )
            self.assertFalse(rep.passed)
            self.assertTrue(any(i.name == "prediction_staleness" and not i.passed for i in rep.items))


if __name__ == "__main__":
    unittest.main()
