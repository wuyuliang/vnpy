"""continuous_contract.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.data_backtest.continuous_contract import build_continuous, detect_rollover


def _all_contracts() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    rows = []
    for i, d in enumerate(dates):
        rows.append(
            {
                "date": d,
                "contract": "rb2405",
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100 + i,
                "volume": 1000 + i * 10,
                "oi": 1500 - i * 100,
            }
        )
        rows.append(
            {
                "date": d,
                "contract": "rb2410",
                "open": 105 + i,
                "high": 106 + i,
                "low": 104 + i,
                "close": 105 + i,
                "volume": 800 + i * 20,
                "oi": 700 + i * 120,
            }
        )
    return pd.DataFrame(rows)


class TestContinuousContract(unittest.TestCase):
    def test_detect_rollover(self) -> None:
        df = _all_contracts()
        events = detect_rollover(df, rule="oi_max")
        self.assertGreaterEqual(len(events), 1)
        self.assertIn("from_contract", events.columns)
        self.assertIn("to_contract", events.columns)

    def test_build_continuous(self) -> None:
        df = _all_contracts()
        out = build_continuous(df, symbol_root="rb", method="back")
        self.assertEqual(len(out.df), 10)
        self.assertIn("adj_factor", out.df.columns)
        self.assertGreaterEqual(len(out.roll_events), 1)


if __name__ == "__main__":
    unittest.main()

