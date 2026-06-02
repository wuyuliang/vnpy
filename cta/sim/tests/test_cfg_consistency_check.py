"""Tests for cta.sim.cfg_consistency_check."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cta.sim.cfg_consistency_check import compare_cfg_fingerprints


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestCfgConsistencyCheck(unittest.TestCase):
    def test_passes_when_key_fields_match(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_cfg_consistent_") as td:
            root = Path(td)
            payload = {
                "use_portfolio_logic_runtime": True,
                "commission_pct_per_trade": 0.0002,
                "initial_capital": 10000000.0,
                "risk_system": {"enabled": True},
            }
            oot = root / "oot.json"
            sim = root / "sim.json"
            live = root / "live.json"
            _write_json(oot, payload)
            _write_json(sim, payload)
            _write_json(live, payload)
            rep = compare_cfg_fingerprints(oot, sim, live)
            self.assertTrue(rep.passed, msg=f"unexpected mismatches: {rep.mismatches}")
            self.assertEqual(len(rep.mismatches), 0)

    def test_fails_when_key_fields_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_cfg_drift_") as td:
            root = Path(td)
            base = {
                "use_portfolio_logic_runtime": True,
                "commission_pct_per_trade": 0.0002,
                "initial_capital": 10000000.0,
            }
            oot = root / "oot.json"
            sim = root / "sim.json"
            live = root / "live.json"
            _write_json(oot, base)
            _write_json(sim, {**base, "commission_pct_per_trade": 0.0003})
            _write_json(live, base)
            rep = compare_cfg_fingerprints(oot, sim, live)
            self.assertFalse(rep.passed)
            self.assertTrue(any(m.field == "commission_pct_per_trade" for m in rep.mismatches))

    def test_fails_when_any_fingerprint_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_cfg_missing_") as td:
            root = Path(td)
            oot = root / "oot.json"
            sim = root / "sim.json"
            live = root / "live.json"
            _write_json(oot, {"use_portfolio_logic_runtime": True})
            _write_json(sim, {"use_portfolio_logic_runtime": True})
            # live missing
            rep = compare_cfg_fingerprints(oot, sim, live)
            self.assertFalse(rep.passed)
            self.assertTrue(any(m.field == "__file__" for m in rep.mismatches))


if __name__ == "__main__":
    unittest.main()
