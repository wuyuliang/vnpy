"""cta/cli.py 单测（TDD）。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

from cta.cli import main


class _DummyStrategy:
    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict]:
        if i == 0 and position == 0:
            return [{"side": "long", "lots": 1, "order_type": "market"}]
        if i == 5 and position > 0:
            return [{"side": "flat", "lots": 1, "order_type": "market"}]
        return []


def make_dummy_strategy():  # used by test via factory
    return _DummyStrategy()


def _bars_csv(path: Path, n: int = 30) -> Path:
    rng = np.random.default_rng(0)
    closes = 100 + rng.normal(0.5, 1.0, size=n).cumsum()
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-02", periods=n, freq="D").strftime("%Y-%m-%d %H:%M:%S"),
            "open": closes - 0.3,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": rng.integers(1000, 5000, size=n).astype(float),
        }
    )
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


class TestCliBasics(unittest.TestCase):
    def test_help_exits_zero(self) -> None:
        # argparse 在 -h 时调用 sys.exit(0)，捕获并断言
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["-h"])
        self.assertEqual(ctx.exception.code, 0)

    def test_unknown_subcommand_fails(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["nonexistent"])
        self.assertNotEqual(ctx.exception.code, 0)


class TestCliBacktest(unittest.TestCase):
    def test_runs_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            bars_csv = _bars_csv(tmp_p / "bars.csv")
            out_dir = tmp_p / "out"
            rc = main(
                [
                    "backtest",
                    "--strategy", "cta.tests.test_cli:make_dummy_strategy",
                    "--bars", str(bars_csv),
                    "--out-dir", str(out_dir),
                    "--title", "cli-smoke",
                    "--monte-carlo-iter", "50",
                ]
            )
            self.assertEqual(rc, 0)
            html = list(out_dir.glob("report_*.html"))
            self.assertEqual(len(html), 1)
            self.assertGreater(html[0].stat().st_size, 1000)
            jsons = list(out_dir.glob("metrics_*.json"))
            self.assertEqual(len(jsons), 1)
            metrics = json.loads(jsons[0].read_text(encoding="utf-8"))
            self.assertIn("sortino", metrics)

    def test_backtest_missing_args_fails(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["backtest"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_invalid_strategy_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            bars_csv = _bars_csv(tmp_p / "bars.csv")
            out_dir = tmp_p / "out"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = main(
                    [
                        "backtest",
                        "--strategy", "no.such.module:func",
                        "--bars", str(bars_csv),
                        "--out-dir", str(out_dir),
                    ]
                )
            self.assertNotEqual(rc, 0)


class TestCliValidate(unittest.TestCase):
    def test_validate_subcommand_runs(self) -> None:
        # 构造一个临时 day csv，把 cta DAY_DIR 指向它
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            bars = pd.DataFrame(
                {
                    "datetime": pd.bdate_range("2024-01-02", periods=5).strftime("%Y-%m-%d"),
                    "open": [100, 101, 102, 103, 104],
                    "high": [101, 102, 103, 104, 105],
                    "low": [99, 100, 101, 102, 103],
                    "close": [100.5, 101.5, 102.5, 103.5, 104.5],
                    "volume": [1000, 1100, 1200, 1300, 1400],
                }
            )
            csv_path = tmp_p / "X0.csv"
            bars.to_csv(csv_path, index=False, encoding="utf-8-sig")
            out_csv = tmp_p / "report.csv"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = main(
                    [
                        "validate",
                        "--csv", str(csv_path),
                        "--symbol", "X0",
                        "--out", str(out_csv),
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertTrue(out_csv.exists())
            df = pd.read_csv(out_csv)
            self.assertEqual(len(df), 1)
            self.assertEqual(df.iloc[0]["rows"], 5)


if __name__ == "__main__":
    unittest.main()
