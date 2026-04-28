"""Integration tests for RB0 backtest and symbol compatibility."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cta.config.skill_tight_range_breakout_config import (
    BacktestConfig,
    StrategyConfig,
)
from cta.strategy.skill_tight_range_backtest import (
    build_contract_spec,
    load_bars,
    normalize_interval,
    resolve_exchange,
    run_symbol_backtest,
)


class TestSkillTightRangeBacktestRB0(unittest.TestCase):
    def test_backtest_config_uses_origin_data_root(self) -> None:
        cfg = BacktestConfig()
        self.assertEqual(cfg.data_root.name, "origin")
        self.assertEqual(cfg.data_day_dir.parent.name, "origin")

    def test_normalize_interval_aliases(self) -> None:
        expect = {
            "day": "day",
            "60min": "minute60",
            "30min": "minute30",
            "15min": "minute15",
            "5min": "minute5",
            "min": "minute",
        }
        for raw, canon in expect.items():
            self.assertEqual(normalize_interval(raw), canon)

    def test_resolve_exchange(self) -> None:
        exchange = resolve_exchange("RB0")
        self.assertEqual(exchange, "SHFE")

    def test_build_contract_spec_fallback(self) -> None:
        contract = build_contract_spec("ZZ_TEST", "TESTEX")
        self.assertEqual(contract.symbol, "ZZ_TEST")
        self.assertEqual(contract.exchange, "TESTEX")
        self.assertGreater(contract.multiplier, 0.0)
        self.assertGreater(contract.tick_size, 0.0)

    def test_run_rb0_backtest_and_metrics(self) -> None:
        day_csv = Path("cta/data/origin/day/RB0.csv")
        if not day_csv.exists():
            self.skipTest(f"missing input file: {day_csv}")

        with tempfile.TemporaryDirectory(prefix="cta_rb0_bt_") as td:
            result = run_symbol_backtest(
                symbol="RB0",
                exchange="SHFE",
                start_date="2018-01-01",
                end_date="2020-12-31",
                strategy_cfg=StrategyConfig(
                    lookback=10,
                    alpha=1.8,
                    min_count=4,
                    min_breakout_score=0.25,
                    lots=1,
                    risk_per_trade_pct=0.005,
                    initial_stop_atr_mult=1.2,
                    trailing_stop_atr_mult=2.0,
                    max_holding_bars=20,
                    align_trend_direction=False,
                ),
                backtest_cfg=BacktestConfig(output_root=Path(td)),
            )

            for k in (
                "total_pnl",
                "total_return",
                "annualized",
                "mdd",
                "sharpe",
                "calmar",
                "winrate",
                "pf",
                "trade_count",
            ):
                self.assertIn(k, result.metrics)

            self.assertTrue(result.summary_path.exists())
            self.assertTrue(result.trade_log_path.exists())
            self.assertTrue(result.equity_path.exists())

    def test_load_bars_minute60(self) -> None:
        cfg = BacktestConfig(interval="60min")
        df = load_bars(
            symbol="RB0",
            backtest_cfg=cfg,
            start_date="2020-01-01",
            end_date="2020-01-31",
        )
        self.assertFalse(df.empty)
        for col in ("datetime", "open", "high", "low", "close", "volume"):
            self.assertIn(col, df.columns)

    def test_run_rb0_backtest_minute60(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_rb0_bt_60m_") as td:
            result = run_symbol_backtest(
                symbol="RB0",
                exchange="SHFE",
                start_date="2020-01-01",
                end_date="2020-01-31",
                strategy_cfg=StrategyConfig(
                    lookback=10,
                    alpha=1.8,
                    min_count=4,
                    min_breakout_score=0.25,
                    lots=1,
                    risk_per_trade_pct=0.005,
                    initial_stop_atr_mult=1.2,
                    trailing_stop_atr_mult=2.0,
                    max_holding_bars=20,
                    align_trend_direction=False,
                    trade_side_mode="both",
                ),
                backtest_cfg=BacktestConfig(
                    output_root=Path(td),
                    interval="60min",
                    periods_per_year=252 * 4,
                ),
            )
            self.assertEqual(result.metrics.get("trade_count") is not None, True)
            self.assertTrue(result.summary_path.exists())
            self.assertIn("_minute60", str(result.summary_path.parent))


if __name__ == "__main__":
    unittest.main()
