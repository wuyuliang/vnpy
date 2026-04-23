"""monitoring_alerting.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.live_ops.monitoring_alerting import check_rules, heartbeat_loop, notify


class TestMonitoringAlerting(unittest.TestCase):
    def test_check_rules(self) -> None:
        state = {
            "ts": pd.Timestamp("2024-01-01"),
            "equity_drawdown": 0.06,
            "position_delta": 1,
            "data_freshness_sec": 400,
            "reject_rate_5m": 0.03,
            "heartbeat_missed": True,
        }
        alerts = check_rules(state)
        self.assertGreaterEqual(len(alerts), 1)
        levels = {a.level for a in alerts}
        self.assertIn("critical", levels)

    def test_notify_and_heartbeat(self) -> None:
        calls: list[str] = []

        def _on_miss() -> None:
            calls.append("miss")

        heartbeat_loop(interval_sec=0, on_miss=_on_miss, max_loops=2, miss_at=1)
        self.assertIn("miss", calls)
        # notify should be side-effect free in test mode
        alerts = check_rules({"ts": pd.Timestamp("2024-01-01"), "equity_drawdown": 0.0})
        if alerts:
            notify(alerts[0], channels=["log"])


if __name__ == "__main__":
    unittest.main()

