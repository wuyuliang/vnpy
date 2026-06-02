"""Tests for cta.data_code.data_integrity_check."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from cta.data_code.data_integrity_check import run_data_integrity_check


class TestDataIntegrityCheck(unittest.TestCase):
    def _write_day_csv(self, root: Path, symbol: str, dates: list[str]) -> None:
        day_dir = root / "day"
        day_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            {
                "symbol": [symbol] * len(dates),
                "exchange": ["SHFE"] * len(dates),
                "interval": ["d"] * len(dates),
                "datetime": [f"{d} 00:00:00" for d in dates],
                "open": [100.0] * len(dates),
                "high": [101.0] * len(dates),
                "low": [99.0] * len(dates),
                "close": [100.5] * len(dates),
                "volume": [1000.0] * len(dates),
                "open_interest": [500.0] * len(dates),
                "turnover": [0.0] * len(dates),
            }
        )
        df.to_csv(day_dir / f"{symbol}.csv", index=False, encoding="utf-8-sig")

    def _write_minute_parquet(self, root: Path, interval_dir: str, prefix: str, d: str, ts: list[str]) -> None:
        out = root / interval_dir / prefix
        out.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            {
                "datetime": ts,
                "open": [100.0] * len(ts),
                "high": [101.0] * len(ts),
                "low": [99.0] * len(ts),
                "close": [100.5] * len(ts),
                "volume": [10.0] * len(ts),
                "open_interest": [20.0] * len(ts),
                "turnover": [1000.0] * len(ts),
                "symbol": ["RB0"] * len(ts),
                "exchange": ["SHFE"] * len(ts),
            }
        )
        df.to_parquet(out / f"{d}.parquet", index=False)

    def test_passes_on_clean_origin_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_integrity_ok_") as td:
            root = Path(td) / "origin"
            self._write_day_csv(root, "RB0", ["2026-05-28", "2026-05-29"])
            self._write_minute_parquet(
                root,
                "minute60",
                "RB",
                "2026-05-29",
                ["2026-05-29 09:00:00", "2026-05-29 10:00:00"],
            )
            rep = run_data_integrity_check(
                origin_root=root,
                intervals=("day", "60min"),
                required_symbols=("RB0",),
                now=pd.Timestamp("2026-05-30 10:00:00"),
                expected_last_date=date(2026, 5, 29),
            )
            self.assertTrue(rep.passed, msg=f"unexpected issues: {rep.issues}")
            self.assertGreaterEqual(rep.checked_files, 2)

    def test_flags_duplicate_day_datetime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_integrity_dup_") as td:
            root = Path(td) / "origin"
            self._write_day_csv(root, "RB0", ["2026-05-29", "2026-05-29"])
            rep = run_data_integrity_check(
                origin_root=root,
                intervals=("day",),
                required_symbols=("RB0",),
                now=pd.Timestamp("2026-05-30 10:00:00"),
                expected_last_date=date(2026, 5, 29),
            )
            self.assertFalse(rep.passed)
            codes = {i.code for i in rep.issues}
            self.assertIn("duplicate_datetime", codes)

    def test_flags_future_minute_datetime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_integrity_future_") as td:
            root = Path(td) / "origin"
            self._write_day_csv(root, "RB0", ["2026-05-29"])
            self._write_minute_parquet(
                root,
                "minute60",
                "RB",
                "2026-05-29",
                ["2026-05-29 09:00:00", "2026-05-30 15:00:00"],
            )
            rep = run_data_integrity_check(
                origin_root=root,
                intervals=("day", "minute60"),
                required_symbols=("RB0",),
                now=pd.Timestamp("2026-05-30 10:00:00"),
                expected_last_date=date(2026, 5, 29),
            )
            self.assertFalse(rep.passed)
            codes = {i.code for i in rep.issues}
            self.assertIn("future_datetime", codes)

    def test_flags_stale_latest_date(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_integrity_stale_") as td:
            root = Path(td) / "origin"
            self._write_day_csv(root, "RB0", ["2026-05-28"])
            rep = run_data_integrity_check(
                origin_root=root,
                intervals=("day",),
                required_symbols=("RB0",),
                now=pd.Timestamp("2026-05-30 10:00:00"),
                expected_last_date=date(2026, 5, 29),
            )
            self.assertFalse(rep.passed)
            codes = {i.code for i in rep.issues}
            self.assertIn("stale_latest_date", codes)


if __name__ == "__main__":
    unittest.main()
