"""rollover_rules.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.data_backtest.rollover_rules import decide_rollover, execute_rollover


class TestRolloverRules(unittest.TestCase):
    def test_decide_rollover(self) -> None:
        positions = [{"symbol": "rb", "contract": "rb2405", "lots": 3}]
        oi_snapshot = {"rb2405": 1000, "rb2410": 1500}
        dte = {"rb2405": 10, "rb2410": 120}
        actions = decide_rollover(
            positions=positions,
            oi_snapshot=oi_snapshot,
            days_to_expiry=dte,
            rule="oi",
        )
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].to_contract, "rb2410")

    def test_execute_rollover(self) -> None:
        positions = [{"symbol": "rb", "contract": "rb2405", "lots": 2}]
        oi_snapshot = {"rb2405": 900, "rb2410": 1600}
        dte = {"rb2405": 5, "rb2410": 100}
        actions = decide_rollover(positions, oi_snapshot, dte, rule="oi")

        def _exec(action) -> dict:
            return {"status": "ok", "from": action.from_contract, "to": action.to_contract}

        ledger = execute_rollover(actions, _exec)
        self.assertIsInstance(ledger, pd.DataFrame)
        self.assertEqual(len(ledger), 1)
        self.assertEqual(str(ledger["status"].iloc[0]), "ok")


if __name__ == "__main__":
    unittest.main()

