"""cta/data_code/validate.py 单测。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.data_code import validate as V


class TestValidateDayCsv(unittest.TestCase):
    def _write_csv(self, dirp: Path, name: str, df: pd.DataFrame) -> Path:
        p = dirp / f"{name}.csv"
        df.to_csv(p, index=False, encoding="utf-8-sig")
        return p

    def test_clean_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            df = pd.DataFrame(
                {
                    "symbol": ["X0"] * 5,
                    "exchange": ["DCE"] * 5,
                    "interval": ["d"] * 5,
                    "datetime": pd.bdate_range("2024-01-02", periods=5).strftime("%Y-%m-%d %H:%M:%S"),
                    "open": [100, 101, 102, 103, 104],
                    "high": [101, 102, 103, 104, 105],
                    "low": [99, 100, 101, 102, 103],
                    "close": [100.5, 101.5, 102.5, 103.5, 104.5],
                    "volume": [1000, 1100, 1200, 1300, 1400],
                }
            )
            p = self._write_csv(d, "X0", df)
            rep = V.validate_day_csv("X0", p)
            self.assertEqual(rep.rows, 5)
            self.assertEqual(rep.duplicates, 0)
            self.assertEqual(rep.ohlc_bad, 0)
            self.assertEqual(rep.zero_volume, 0)
            self.assertEqual(rep.missing_business_days, 0)

    def test_missing_business_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # 跳过中间一个工作日
            dates = pd.bdate_range("2024-01-02", periods=5).tolist()
            dates = dates[:2] + dates[3:]  # 删掉第 3 个工作日
            df = pd.DataFrame(
                {
                    "datetime": [t.strftime("%Y-%m-%d %H:%M:%S") for t in dates],
                    "open": [100] * 4,
                    "high": [101] * 4,
                    "low": [99] * 4,
                    "close": [100] * 4,
                    "volume": [1000] * 4,
                }
            )
            p = self._write_csv(d, "X0", df)
            rep = V.validate_day_csv("X0", p)
            self.assertEqual(rep.missing_business_days, 1)

    def test_ohlc_bad_and_zero_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            df = pd.DataFrame(
                {
                    "datetime": pd.bdate_range("2024-01-02", periods=3).strftime("%Y-%m-%d %H:%M:%S"),
                    "open": [100, 100, 100],
                    "high": [99, 101, 101],   # 第一行 high < open → 不一致
                    "low": [99, 99, 99],
                    "close": [100, 100, 100],
                    "volume": [1000, 0, 1000],  # 第二行 volume=0
                }
            )
            p = self._write_csv(d, "X0", df)
            rep = V.validate_day_csv("X0", p)
            self.assertGreaterEqual(rep.ohlc_bad, 1)
            self.assertEqual(rep.zero_volume, 1)

    def test_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            df = pd.DataFrame(
                {
                    "datetime": ["2024-01-02 00:00:00", "2024-01-02 00:00:00", "2024-01-03 00:00:00"],
                    "open": [100, 100, 101],
                    "high": [101, 101, 102],
                    "low": [99, 99, 100],
                    "close": [100, 100, 101],
                    "volume": [1000, 1000, 1100],
                }
            )
            p = self._write_csv(d, "X0", df)
            rep = V.validate_day_csv("X0", p)
            self.assertEqual(rep.duplicates, 1)

    def test_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rep = V.validate_day_csv("ZZ0", Path(tmp) / "ZZ0.csv")
            self.assertEqual(rep.rows, 0)
            self.assertTrue(any("missing" in n for n in rep.notes))

    def test_validate_minute_dir_session_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "origin" / "minute60" / "RB"
            base.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame(
                {
                    "datetime": [
                        "2024-01-02 09:00:00",  # in session
                        "2024-01-02 03:00:00",  # out of session
                    ],
                    "open": [100, 101],
                    "high": [101, 102],
                    "low": [99, 100],
                    "close": [100, 101],
                    "volume": [10, 10],
                }
            )
            df.to_parquet(base / "2024-01-02.parquet", index=False)
            old_data_dir = V.DATA_DIR
            try:
                V.DATA_DIR = Path(tmp)
                rep = V.validate_minute_dir("RB0", "minute60")
            finally:
                V.DATA_DIR = old_data_dir
            self.assertGreaterEqual(rep.rows, 2)
            self.assertGreaterEqual(rep.out_of_session_bars, 1)
            self.assertGreaterEqual(rep.short_session_days, 1)


if __name__ == "__main__":
    unittest.main()
