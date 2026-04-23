"""strategy_iteration_loop.py tests."""
from __future__ import annotations

import os
import tempfile
import unittest

import pandas as pd

from cta.skills.live_ops.strategy_iteration_loop import (
    append_change_log,
    plan_release,
)


class TestStrategyIterationLoop(unittest.TestCase):
    def test_plan_release(self) -> None:
        p = plan_release(
            strategy="brooks_v1",
            current="1.0.0",
            next_ver="1.1.0",
            changes=["improve filter"],
        )
        self.assertEqual(p.from_version, "1.0.0")
        self.assertEqual(p.to_version, "1.1.0")
        self.assertGreaterEqual(len(p.rollout_stages), 1)

    def test_append_change_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "change_log.md")
            old = os.environ.get("CTA_CHANGE_LOG_PATH")
            os.environ["CTA_CHANGE_LOG_PATH"] = path
            try:
                append_change_log(
                    date=pd.Timestamp("2024-01-01"),
                    branch="feature/test",
                    task="unit_test",
                    files=["cta/a.py"],
                    conclusion="ok",
                )
                self.assertTrue(os.path.exists(path))
            finally:
                if old is None:
                    os.environ.pop("CTA_CHANGE_LOG_PATH", None)
                else:
                    os.environ["CTA_CHANGE_LOG_PATH"] = old


if __name__ == "__main__":
    unittest.main()

