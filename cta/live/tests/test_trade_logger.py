"""Tests for cta.live.trade_logger."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.live.trade_logger import OotStyleTradeLogger


class TestOotStyleTradeLogger(unittest.TestCase):
    def test_records_blocked_and_filled_rows_then_flushes_csv_and_jsonl(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_trade_logger_") as td:
            out_dir = Path(td)
            logger = OotStyleTradeLogger(out_dir=out_dir, run_tag="sim_plan3")

            logger.record_decision(
                {
                    "datetime": "2026-05-30 09:31:00",
                    "symbol": "RB0",
                    "exchange": "SHFE",
                    "interval": "day",
                    "side": "long",
                    "signal_type": "donchian_breakout",
                    "trade_filter_prob": 0.61,
                    "trade_filter_prob_pctl": 68.0,
                },
                passed=False,
                block_reason="blocked_trade_filter",
                risk_block_reason="blocked_trade_filter",
                adjusted_lots=0,
            )
            logger.record_fill(
                {
                    "datetime": "2026-05-30 10:01:00",
                    "symbol": "RB0",
                    "exchange": "SHFE",
                    "interval": "day",
                    "side": "long",
                    "signal_type": "donchian_breakout",
                    "entry_fill_price": 3521.0,
                    "entry_lots": 2,
                    "trade_filter_prob": 0.82,
                    "trade_filter_prob_pctl": 77.0,
                }
            )

            csv_path, jsonl_path = logger.flush()
            self.assertTrue(csv_path.exists())
            self.assertTrue(jsonl_path.exists())

            df = pd.read_csv(csv_path, encoding="utf-8-sig")
            self.assertEqual(len(df), 2)
            for col in (
                "datetime",
                "symbol",
                "side",
                "execution_status",
                "block_reason",
                "risk_block_reason",
                "trade_filter_prob",
                "trade_filter_prob_pctl",
            ):
                self.assertIn(col, df.columns)
            blocked = df[df["execution_status"] == "blocked"]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(str(blocked.iloc[0]["block_reason"]), "blocked_trade_filter")
            filled = df[df["execution_status"] == "filled"]
            self.assertEqual(len(filled), 1)
            self.assertAlmostEqual(float(filled.iloc[0]["entry_fill_price"]), 3521.0, places=6)

    def test_flush_without_rows_creates_empty_schema_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_trade_logger_empty_") as td:
            logger = OotStyleTradeLogger(out_dir=Path(td), run_tag="sim_plan3")
            csv_path, jsonl_path = logger.flush()
            self.assertTrue(csv_path.exists())
            self.assertTrue(jsonl_path.exists())
            df = pd.read_csv(csv_path, encoding="utf-8-sig")
            self.assertEqual(len(df), 0)
            self.assertIn("execution_status", df.columns)


if __name__ == "__main__":
    unittest.main()
