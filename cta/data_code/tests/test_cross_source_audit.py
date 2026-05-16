"""cta/data_code/cross_source_audit.py 单测。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.data_code import cross_source_audit as A


class TestCrossSourceAudit(unittest.TestCase):
    def _write_day_csv(self, day_dir: Path, symbol: str, rows: list[dict[str, object]]) -> Path:
        day_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(rows)
        path = day_dir / f"{symbol}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def _write_minute_file(self, minute_symbol_dir: Path, trade_date: str, rows: list[dict[str, object]]) -> Path:
        minute_symbol_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(rows)
        path = minute_symbol_dir / f"{trade_date}.parquet"
        df.to_parquet(path, index=False)
        return path

    def test_build_daily_from_minute_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            minute_dir = Path(td) / "minute" / "RB"
            self._write_minute_file(
                minute_dir,
                "2024-01-02",
                [
                    {
                        "datetime": "2024-01-01 21:01:00",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 10,
                        "open_interest": 100,
                        "turnover": 1000,
                    },
                    {
                        "datetime": "2024-01-02 09:01:00",
                        "open": 100.5,
                        "high": 103.0,
                        "low": 100.0,
                        "close": 102.0,
                        "volume": 15,
                        "open_interest": 120,
                        "turnover": 1800,
                    },
                ],
            )
            day_df = A.build_daily_from_minute_files(minute_dir)
            self.assertEqual(len(day_df), 1)
            row = day_df.iloc[0]
            self.assertEqual(str(row["trade_date"]), "2024-01-02")
            self.assertAlmostEqual(float(row["open"]), 100.0, places=9)
            self.assertAlmostEqual(float(row["high"]), 103.0, places=9)
            self.assertAlmostEqual(float(row["low"]), 99.0, places=9)
            self.assertAlmostEqual(float(row["close"]), 102.0, places=9)
            self.assertAlmostEqual(float(row["volume"]), 25.0, places=9)
            self.assertAlmostEqual(float(row["turnover"]), 2800.0, places=9)

    def test_audit_symbol_marks_disabled_when_diff_exceeds_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day_dir = root / "day"
            minute_dir = root / "minute" / "RB"
            symbol = "RB0"

            self._write_day_csv(
                day_dir,
                symbol,
                [
                    {
                        "datetime": "2024-01-02 00:00:00",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 1000.0,
                    }
                ],
            )
            self._write_minute_file(
                minute_dir,
                "2024-01-02",
                [
                    {
                        "datetime": "2024-01-02 09:00:00",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 103.0,  # close 偏差 3%
                        "volume": 1000.0,
                    }
                ],
            )

            summary, detail = A.audit_symbol(
                symbol=symbol,
                day_csv_path=day_dir / f"{symbol}.csv",
                minute_symbol_dir=minute_dir,
                sample_days=5,
                tolerance=0.001,
                random_seed=2026,
                # P0.1: 测试只造了 1 天数据，要保留"单天 mismatch 即 disabled"的旧语义
                # 必须显式把阈值降到 1（新默认是 2，不会因单天夜盘归属日错位被误杀）
                min_mismatch_days_to_disable=1,
            )
            self.assertTrue(summary.disabled)
            self.assertGreater(summary.mismatch_days, 0)
            self.assertGreater(len(detail), 0)
            self.assertIn("close", set(detail["field"].astype(str)))

    def test_audit_symbol_passes_within_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day_dir = root / "day"
            minute_dir = root / "minute" / "RB"
            symbol = "RB0"

            days = pd.bdate_range("2024-01-02", periods=6)
            day_rows: list[dict[str, object]] = []
            for d in days:
                day_rows.append(
                    {
                        "datetime": d.strftime("%Y-%m-%d 00:00:00"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 1000.0,
                    }
                )
                self._write_minute_file(
                    minute_dir,
                    d.strftime("%Y-%m-%d"),
                    [
                        {
                            "datetime": d.strftime("%Y-%m-%d 09:00:00"),
                            "open": 100.02,   # 0.02%
                            "high": 101.02,
                            "low": 98.99,
                            "close": 99.99,
                            "volume": 1000.5,
                        }
                    ],
                )

            self._write_day_csv(day_dir, symbol, day_rows)
            summary, detail = A.audit_symbol(
                symbol=symbol,
                day_csv_path=day_dir / f"{symbol}.csv",
                minute_symbol_dir=minute_dir,
                sample_days=5,
                tolerance=0.001,  # 0.1%
                random_seed=2026,
            )
            self.assertFalse(summary.disabled)
            self.assertEqual(summary.sampled_days, 5)
            self.assertEqual(summary.mismatch_days, 0)
            self.assertTrue((detail["is_mismatch"].astype(int) == 0).all())


if __name__ == "__main__":
    unittest.main()
