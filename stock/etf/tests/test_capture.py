import unittest

import pandas as pd

from stock.etf.capture import calculate_trend_capture
from stock.etf.config import StrategyConfig


class TrendCaptureTests(unittest.TestCase):
    def test_capture_reconciles_partial_trims_restores_and_boundaries(self) -> None:
        candidates = self._episode_candidates("A.SH", end_open=13.0)
        trades = pd.DataFrame(
            [
                self._trade("2026-01-05", "buy", 1000, 10.0),
                self._trade("2026-01-20", "sell", 400, 15.0),
                self._trade("2026-01-27", "buy", 200, 11.0),
                self._trade("2026-02-10", "sell", 800, 13.0),
            ]
        )
        equity = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    ["2026-01-02", "2026-01-05", "2026-02-09", "2026-02-10"]
                ),
                "equity": [100_000.0, 100_000.0, 104_000.0, 104_200.0],
            }
        )

        episodes = calculate_trend_capture(
            candidates=candidates,
            trades=trades,
            positions=pd.DataFrame(),
            equity_curve=equity,
            config=StrategyConfig(
                initial_capital=100_000,
                commission_rate=0,
                min_commission=0,
                slippage_rate=0,
            ),
        )

        row = episodes.iloc[0]
        self.assertEqual(row["start_execution_date"], pd.Timestamp("2026-01-05"))
        self.assertEqual(row["end_execution_date"], pd.Timestamp("2026-02-10"))
        self.assertGreater(row["episode_return"], 0.20)
        self.assertEqual(row["actual_episode_pnl"], 4200.0)
        self.assertEqual(row["hypothetical_episode_profit"], 3000.0)
        self.assertAlmostEqual(
            row["capture_ratio"],
            row["actual_episode_pnl"] / row["hypothetical_episode_profit"],
        )
        self.assertGreater(row["capture_ratio"], 1.0)
        self.assertTrue(row["qualifies"])

    def test_skipped_unfinished_and_small_episodes_do_not_qualify(self) -> None:
        skipped = self._episode_candidates("SKIP.SH", end_open=13.0)
        small = self._episode_candidates("SMALL.SH", end_open=11.0)
        unfinished = pd.DataFrame(
            {
                "symbol": ["OPEN.SH", "OPEN.SH"],
                "datetime": pd.to_datetime(["2026-01-02", "2026-02-10"]),
                "open": [10.0, 13.0],
                "close": [10.0, 14.0],
                "ema20": [9.0, 12.0],
                "atr5": [0.5, 0.5],
                "entry_rank": [1, 1],
                "trend_confirmed": [True, True],
            }
        )
        candidates = pd.concat([skipped, small, unfinished], ignore_index=True)
        equity = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    ["2026-01-02", "2026-01-05", "2026-02-09", "2026-02-10"]
                ),
                "equity": 100_000.0,
            }
        )

        episodes = calculate_trend_capture(
            candidates=candidates,
            trades=pd.DataFrame(),
            positions=pd.DataFrame(),
            equity_curve=equity,
            config=StrategyConfig(commission_rate=0, min_commission=0),
        ).set_index("symbol")

        self.assertEqual(episodes.loc["SKIP.SH", "actual_episode_pnl"], 0.0)
        self.assertEqual(episodes.loc["SKIP.SH", "capture_ratio"], 0.0)
        self.assertTrue(episodes.loc["SKIP.SH", "qualifies"])
        self.assertLess(episodes.loc["SMALL.SH", "episode_return"], 0.20)
        self.assertFalse(episodes.loc["SMALL.SH", "qualifies"])
        self.assertFalse(episodes.loc["OPEN.SH", "is_complete"])
        self.assertFalse(episodes.loc["OPEN.SH", "qualifies"])

    @staticmethod
    def _episode_candidates(symbol: str, *, end_open: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": symbol,
                "datetime": pd.to_datetime(
                    ["2026-01-02", "2026-01-05", "2026-02-09", "2026-02-10"]
                ),
                "open": [10.0, 10.0, 12.8, end_open],
                "close": [10.0, 10.2, 12.0, end_open],
                "ema20": [9.0, 9.2, 12.1, 12.2],
                "atr5": [0.5, 0.5, 0.5, 0.5],
                "entry_rank": [1, 1, pd.NA, pd.NA],
                "trend_confirmed": [True, True, False, False],
            }
        )

    @staticmethod
    def _trade(date: str, side: str, quantity: int, fill_price: float) -> dict:
        return {
            "datetime": pd.Timestamp(date),
            "symbol": "A.SH",
            "side": side,
            "quantity": quantity,
            "fill_price": fill_price,
            "commission": 0.0,
        }


if __name__ == "__main__":
    unittest.main()
