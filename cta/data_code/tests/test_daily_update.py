"""Tests for cta.data_code.daily_update orchestration."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

from cta.data_code.daily_update import DailyUpdateConfig, build_commands, run_daily_update


class TestDailyUpdate(unittest.TestCase):
    def test_build_commands_contains_download_integrity_feature_dataset_steps(self) -> None:
        cfg = DailyUpdateConfig(
            top_n_symbols=18,
            intervals=("day", "60min", "30min"),
            start_date="2010-01-01",
            end_date="2026-05-29",
            symbols_ranking_path=Path("cta/feature/symbols_research_ranking.csv"),
            run_tag="sim_plan3",
        )
        commands = build_commands(cfg)
        self.assertGreaterEqual(len(commands), 4)
        flat = [" ".join(c) for c in commands]
        self.assertTrue(any("cta.data_code.download_all" in s for s in flat))
        self.assertTrue(any("cta.data_code.data_integrity_check" in s for s in flat))
        self.assertTrue(any("cta.feature.run_all_features" in s for s in flat))
        self.assertTrue(any("cta.model.feature.candidate_training_dataset" in s for s in flat))

    def test_run_daily_update_executes_all_steps(self) -> None:
        cfg = DailyUpdateConfig(
            top_n_symbols=5,
            intervals=("day", "60min"),
            start_date="2020-01-01",
            end_date="2020-12-31",
        )
        calls: list[list[str]] = []

        def _runner(cmd: list[str]) -> int:
            calls.append(list(cmd))
            return 0

        executed = run_daily_update(cfg, command_runner=_runner)
        self.assertEqual(len(executed), len(calls))
        self.assertEqual(len(executed), len(build_commands(cfg)))
        self.assertTrue(all(cmd[0] == "python3" for cmd in calls))

    def test_run_daily_update_raises_on_nonzero(self) -> None:
        cfg = DailyUpdateConfig(top_n_symbols=3)
        runner = Mock(side_effect=[0, 7])  # step2 fails
        with self.assertRaises(RuntimeError):
            run_daily_update(cfg, command_runner=runner)


if __name__ == "__main__":
    unittest.main()
