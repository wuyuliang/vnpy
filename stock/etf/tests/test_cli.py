import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock.etf.run_etf_rotation import calculate_download_start, main


class CliTests(unittest.TestCase):
    def test_download_start_includes_indicator_warmup(self) -> None:
        start = pd.Timestamp("2026-05-17")

        download_start = calculate_download_start(start, warmup_bars=80)

        self.assertLessEqual(download_start, start - pd.offsets.BDay(80))

    def test_offline_cli_writes_complete_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dates = pd.bdate_range("2025-01-02", periods=90)
            benchmark = self._bars(
                "000300.SH", dates, np.linspace(100, 140, len(dates))
            )
            etfs = self._bars("A.SH", dates, np.linspace(10, 15, len(dates)))
            metadata = pd.DataFrame(
                {
                    "symbol": ["A.SH"],
                    "name": ["A"],
                    "list_date": ["2020-01-01"],
                    "fund_type": ["股票型ETF"],
                    "benchmark": ["中证A股指数收益率×100%"],
                }
            )
            benchmark_path = root / "benchmark.csv"
            etf_path = root / "etfs.csv"
            metadata_path = root / "metadata.csv"
            output_path = root / "output"
            benchmark.to_csv(benchmark_path, index=False)
            etfs.to_csv(etf_path, index=False)
            metadata.to_csv(metadata_path, index=False)

            exit_code = main(
                [
                    "--start",
                    str(dates[0].date()),
                    "--end",
                    str(dates[-1].date()),
                    "--skip-download",
                    "--benchmark-csv",
                    str(benchmark_path),
                    "--etf-csv",
                    str(etf_path),
                    "--metadata-csv",
                    str(metadata_path),
                    "--output-dir",
                    str(output_path),
                    "--atr-period",
                    "10",
                    "--adx-period",
                    "5",
                    "--entry-rank",
                    "3",
                    "--entry-confirmation-days",
                    "2",
                    "--max-entry-gap-atr",
                    "1",
                    "--max-industry-weight",
                    "0.35",
                    "--correlation-lookback",
                    "60",
                    "--min-correlation-observations",
                    "40",
                    "--correlation-threshold",
                    "0.9",
                    "--max-correlation-weight",
                    "0.3",
                    "--winner-holding-enabled",
                    "--market-state-enabled",
                    "--dynamic-risk-enabled",
                    "--atr-stop-multiple",
                    "3",
                    "--risk-per-trade",
                    "0.01",
                    "--exit-rank",
                    "20",
                    "--winner-promotion-atr-multiple",
                    "2",
                    "--exit-rank-confirmation-days",
                    "3",
                    "--max-portfolio-stop-risk",
                    "0.03",
                    "--max-cluster-stop-risk",
                    "0.015",
                    "--caution-max-gross-weight",
                    "0.5",
                    "--caution-entry-rank",
                    "3",
                    "--caution-risk-fraction",
                    "0.5",
                    "--risk-off-breadth-threshold",
                    "0.35",
                    "--market-state-confirmation-days",
                    "2",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_path / "summary.json").exists())
            self.assertEqual(len(list(output_path.iterdir())), 8)
            with (output_path / "summary.json").open(encoding="utf-8") as file:
                summary = json.load(file)
            self.assertEqual(summary["parameters"]["atr_period"], 10)
            self.assertEqual(summary["parameters"]["adx_period"], 5)
            self.assertEqual(summary["parameters"]["entry_rank"], 3)
            self.assertEqual(summary["parameters"]["entry_confirmation_days"], 2)
            self.assertEqual(summary["parameters"]["max_entry_gap_atr"], 1.0)
            self.assertEqual(summary["parameters"]["max_industry_weight"], 0.35)
            self.assertEqual(summary["parameters"]["correlation_threshold"], 0.9)
            self.assertEqual(summary["parameters"]["max_correlation_weight"], 0.3)
            self.assertTrue(summary["parameters"]["winner_holding_enabled"])
            self.assertTrue(summary["parameters"]["market_state_enabled"])
            self.assertTrue(summary["parameters"]["dynamic_risk_enabled"])
            self.assertEqual(summary["parameters"]["atr_stop_multiple"], 3.0)
            self.assertEqual(summary["parameters"]["risk_per_trade"], 0.01)
            self.assertEqual(summary["parameters"]["exit_rank"], 20)
            self.assertEqual(
                summary["parameters"]["winner_promotion_atr_multiple"], 2.0
            )
            self.assertEqual(summary["parameters"]["max_portfolio_stop_risk"], 0.03)
            self.assertEqual(summary["parameters"]["max_cluster_stop_risk"], 0.015)
            self.assertEqual(summary["parameters"]["caution_max_gross_weight"], 0.5)
            self.assertEqual(summary["parameters"]["caution_entry_rank"], 3)
            self.assertEqual(
                summary["strategy_rule_version"], "2026-07-18.winner-holding-v1"
            )
            self.assertEqual(summary["data_source"], "local_csv")
            self.assertEqual(summary["benchmark_rows"], len(benchmark))
            self.assertEqual(summary["etf_rows"], len(etfs))
            self.assertEqual(summary["metadata_symbols"], 1)
            self.assertIn(str(root), summary["data_cache_identifier"])

    @staticmethod
    def _bars(symbol: str, dates: pd.DatetimeIndex, close: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": symbol,
                "datetime": dates,
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1_000_000,
                "turnover": 300_000_000,
                "is_trading": True,
            }
        )


if __name__ == "__main__":
    unittest.main()
