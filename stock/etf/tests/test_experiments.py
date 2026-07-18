import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock.etf.experiments import summarize_run, write_experiment_report
from stock.etf.run_experiment_report import main as report_main


class ExperimentReportTests(unittest.TestCase):
    def test_summarize_run_uses_reproducible_cost_and_turnover_formulas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            self._write_run(run_dir, final_equity=120.0)

            metrics, annual, rolling = summarize_run(
                "candidate", run_dir, trial_count=4
            )

            self.assertAlmostEqual(metrics["gross_profit_static"], 25.0)
            self.assertAlmostEqual(metrics["cost_to_gross_profit"], 0.2)
            self.assertAlmostEqual(
                metrics["annual_one_way_turnover"],
                50.0 / metrics["average_equity"],
            )
            self.assertEqual(metrics["risk_state_flips"], 2)
            self.assertEqual(metrics["median_holding_days"], 351.0)
            self.assertEqual(metrics["max_industry_weight"], 0.30)
            self.assertEqual(metrics["largest_positive_industry"], "semiconductor")
            self.assertEqual(
                metrics["largest_positive_industry_contribution_share"], 1.0
            )
            self.assertEqual(metrics["bull_episode_count"], 1)
            self.assertEqual(metrics["median_bull_capture_ratio"], 0.75)
            self.assertEqual(len(annual), 1)
            self.assertFalse(rolling.empty)
            self.assertGreaterEqual(metrics["deflated_sharpe_probability"], 0.0)
            self.assertLessEqual(metrics["deflated_sharpe_probability"], 1.0)

    def test_write_report_outputs_comparison_and_selection_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            candidate = root / "candidate"
            self._write_run(baseline, final_equity=110.0)
            self._write_run(candidate, final_equity=120.0)
            dates = pd.bdate_range("2025-01-02", periods=252)
            benchmark = pd.DataFrame(
                {
                    "symbol": "000300.SH",
                    "datetime": dates,
                    "close": np.linspace(100.0, 115.0, len(dates)),
                }
            )
            output = root / "report"

            report = write_experiment_report(
                {"baseline": baseline, "candidate": candidate},
                benchmark,
                output,
                baseline_run_id="baseline",
                candidate_run_id="candidate",
            )

            self.assertTrue((output / "experiment_summary.csv").exists())
            self.assertTrue((output / "annual_returns.csv").exists())
            self.assertTrue((output / "rolling_12m_returns.csv").exists())
            self.assertTrue((output / "selection_audit.json").exists())
            summary = pd.read_csv(output / "experiment_summary.csv")
            self.assertEqual(set(summary["run_id"]), {"baseline", "candidate"})
            self.assertEqual(report["trial_count"], 2)
            self.assertIn("all_numeric_gates_pass", report["selection"])
            self.assertEqual(
                report["selection"]["largest_positive_industry_contribution_share"],
                1.0,
            )
            self.assertIn("largest_winning_trade_share", report["selection"])

    def test_report_cli_accepts_named_run_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            candidate = root / "candidate"
            self._write_run(baseline, final_equity=110.0)
            self._write_run(candidate, final_equity=120.0)
            dates = pd.bdate_range("2025-01-02", periods=252)
            benchmark_path = root / "benchmark.csv"
            pd.DataFrame(
                {
                    "symbol": "000300.SH",
                    "datetime": dates,
                    "close": np.linspace(100.0, 115.0, len(dates)),
                }
            ).to_csv(benchmark_path, index=False)
            output = root / "report"

            exit_code = report_main(
                [
                    "--run",
                    f"baseline={baseline}",
                    "--run",
                    f"candidate={candidate}",
                    "--benchmark-csv",
                    str(benchmark_path),
                    "--baseline",
                    "baseline",
                    "--candidate",
                    "candidate",
                    "--output-dir",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((output / "selection_audit.json").exists())

    @staticmethod
    def _write_run(run_dir: Path, *, final_equity: float) -> None:
        run_dir.mkdir(parents=True)
        dates = pd.bdate_range("2025-01-02", periods=252)
        equity = np.linspace(100.0, final_equity, len(dates))
        daily_return = pd.Series(equity).pct_change().fillna(0.0)
        pd.DataFrame(
            {
                "datetime": dates,
                "cash": np.linspace(50.0, 70.0, len(dates)),
                "market_value": equity - np.linspace(50.0, 70.0, len(dates)),
                "equity": equity,
                "daily_return": daily_return,
                "drawdown": equity / np.maximum.accumulate(equity) - 1,
            }
        ).to_csv(run_dir / "equity_curve.csv", index=False)
        pd.DataFrame(
            [
                {
                    "datetime": dates[0],
                    "symbol": "A.SH",
                    "side": "buy",
                    "quantity": 5,
                    "raw_price": 10.0,
                    "fill_price": 10.2,
                    "commission": 1.0,
                    "slippage_cost": 1.0,
                    "primary_reason": "entry",
                    "realized_pnl": 0.0,
                },
                {
                    "datetime": dates[-1],
                    "symbol": "A.SH",
                    "side": "sell",
                    "quantity": 5,
                    "raw_price": 12.0,
                    "fill_price": 11.6,
                    "commission": 1.0,
                    "slippage_cost": 2.0,
                    "primary_reason": "exit",
                    "realized_pnl": 5.0,
                },
            ]
        ).to_csv(run_dir / "trades.csv", index=False)
        actions = ["risk_off"] * 80 + ["risk_on"] * 80 + ["risk_off"] * 92
        pd.DataFrame(
            {
                "signal_date": dates,
                "symbol": "000300.SH",
                "action": actions,
                "reason": "market_filter",
            }
        ).to_csv(run_dir / "daily_signals.csv", index=False)
        pd.DataFrame(
            {
                "datetime": dates,
                "symbol": "A.SH",
                "weight": 0.20,
                "industry": "semiconductor",
                "industry_weight": 0.30,
                "benchmark_key": "index_a",
            }
        ).to_csv(run_dir / "positions.csv", index=False)
        pd.DataFrame(
            {
                "symbol": ["A.SH", "B.SH"],
                "qualifies": [True, False],
                "capture_ratio": [0.75, 2.0],
            }
        ).to_csv(run_dir / "trend_episode_capture.csv", index=False)
        summary = {
            "start_date": str(dates[0].date()),
            "end_date": str(dates[-1].date()),
            "initial_capital": 100.0,
            "final_equity": final_equity,
            "total_return": final_equity / 100.0 - 1,
            "annual_return": final_equity / 100.0 - 1,
            "max_drawdown": 0.0,
            "annual_volatility": float(daily_return.std(ddof=0) * np.sqrt(252)),
            "sharpe": float(
                daily_return.mean() / daily_return.std(ddof=0) * np.sqrt(252)
            ),
            "trade_count": 2,
            "win_rate": 1.0,
            "total_commission": 2.0,
            "total_slippage_cost": 3.0,
            "parameters": {"entry_rank": 3},
        }
        (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
