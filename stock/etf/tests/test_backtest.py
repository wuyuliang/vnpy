import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import stock.etf.backtest as etf_backtest
from stock.etf.backtest import (
    _correlated_cluster_symbols,
    run_backtest,
    write_backtest_outputs,
)
from stock.etf.config import StrategyConfig
from stock.etf.portfolio import Position


class BacktestTests(unittest.TestCase):
    def test_enabled_market_state_writes_caution_and_confirmed_risk_on(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        benchmark.loc[:99, "open"] = benchmark.loc[:99, "close"] - 2.0
        benchmark.loc[:99, "low"] = benchmark.loc[:99, "open"] - 0.1
        etfs = self._bars("A.SH", dates, np.linspace(10.0, 20.0, len(dates)))
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH"],
                "name": ["中证A股ETF"],
                "list_date": [pd.Timestamp("2020-01-01")],
                "fund_type": ["股票型ETF"],
                "benchmark": ["中证A股指数收益率×100%"],
            }
        )
        config = StrategyConfig(
            initial_capital=100_000,
            market_state_enabled=True,
            commission_rate=0,
            min_commission=0,
            slippage_rate=0,
        )

        result = run_backtest(benchmark, etfs, metadata, config)

        market_actions = result.signals.loc[
            result.signals["symbol"] == "000300.SH",
            "action",
        ]
        self.assertIn("market_caution", market_actions.values)
        self.assertIn("market_risk_on", market_actions.values)
        caution_plans = result.signals.loc[
            (result.signals["action"] == "buy_planned")
            & (result.signals["risk_fraction"] == 0.5)
        ]
        self.assertFalse(caution_plans.empty)

    def test_winner_rank_exit_requires_confirmation_and_ema10_weakness(self) -> None:
        config = StrategyConfig(
            winner_holding_enabled=True,
            exit_rank=20,
            exit_rank_confirmation_days=3,
        )
        ranking = pd.DataFrame({"holding_rank": [21]}, index=["A.SH"])
        position = Position(
            "A.SH",
            1000,
            10.0,
            7.0,
            0.0,
            weak_rank_days=2,
        )

        strong_price = pd.Series({"close": 10.5, "ema10": 10.0, "ema20": 9.5})
        reasons = etf_backtest._winner_reasons_for_exit(
            position,
            ranking,
            strong_price,
            config,
        )

        self.assertEqual(reasons, [])
        self.assertEqual(position.weak_rank_days, 3)

        position.weak_rank_days = 2
        weak_price = pd.Series({"close": 9.9, "ema10": 10.0, "ema20": 9.5})
        reasons = etf_backtest._winner_reasons_for_exit(
            position,
            ranking,
            weak_price,
            config,
        )

        self.assertEqual(reasons, ["confirmed_rank_and_ema10_weakness"])

    def test_winner_exit_uses_ema20_even_when_rank_is_strong(self) -> None:
        config = StrategyConfig(winner_holding_enabled=True, exit_rank=20)
        ranking = pd.DataFrame({"holding_rank": [1]}, index=["A.SH"])
        position = Position("A.SH", 1000, 10.0, 7.0, 0.0)
        row = pd.Series({"close": 9.4, "ema10": 10.0, "ema20": 9.5})

        reasons = etf_backtest._winner_reasons_for_exit(
            position,
            ranking,
            row,
            config,
        )

        self.assertEqual(reasons, ["etf_below_ema20"])

    def test_512400_like_rank_churn_keeps_absolute_trend_winner(self) -> None:
        config = StrategyConfig(
            winner_holding_enabled=True,
            exit_rank=20,
            exit_rank_confirmation_days=3,
        )
        position = Position("512400.SH", 1000, 10.0, 7.0, 0.0, state="winner")
        observations = [
            (29, 12.0, 11.0, 10.0),
            (11, 12.2, 11.2, 10.2),
            (14, 10.9, 11.0, 10.0),
        ]

        for rank, close, ema10, ema20 in observations:
            ranking = pd.DataFrame(
                {"holding_rank": [rank]},
                index=["512400.SH"],
            )
            row = pd.Series({"close": close, "ema10": ema10, "ema20": ema20})
            reasons = etf_backtest._winner_reasons_for_exit(
                position,
                ranking,
                row,
                config,
            )
            self.assertEqual(reasons, [])

    def test_correlation_cap_requires_minimum_point_in_time_overlap(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=80)
        candidate = pd.Series(np.linspace(-0.01, 0.01, len(dates)), index=dates)
        held = candidate.copy()
        held.iloc[:50] = np.nan
        returns = pd.DataFrame({"A.SH": candidate, "B.SH": held})
        config = StrategyConfig(
            correlation_lookback=60,
            min_correlation_observations=40,
            correlation_threshold=0.90,
            max_correlation_weight=0.30,
        )

        cluster = _correlated_cluster_symbols(
            "A.SH",
            {"B.SH"},
            returns,
            dates[-1],
            config,
        )

        self.assertEqual(cluster, set())

    def test_future_correlation_does_not_change_signal_date_cluster(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=100)
        candidate = pd.Series(np.tile([0.01, -0.01], 50), index=dates)
        held = pd.Series(np.tile([0.01, 0.01, -0.01, -0.01], 25), index=dates)
        held.iloc[60:] = candidate.iloc[60:]
        returns = pd.DataFrame({"A.SH": candidate, "B.SH": held})
        config = StrategyConfig(
            correlation_lookback=60,
            min_correlation_observations=40,
            correlation_threshold=0.90,
            max_correlation_weight=0.30,
        )

        cluster = _correlated_cluster_symbols(
            "A.SH",
            {"B.SH"},
            returns,
            dates[59],
            config,
        )

        self.assertEqual(cluster, set())

    def test_end_to_end_trades_next_day_and_writes_outputs(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark_close = np.linspace(100.0, 160.0, len(dates))
        benchmark_close[-3:] = [130.0, 100.0, 90.0]
        benchmark = self._bars("000300.SH", dates, benchmark_close, turnover=0)
        etfs = pd.concat(
            [
                self._bars("A.SH", dates, np.linspace(10.0, 20.0, len(dates))),
                self._bars("B.SH", dates, np.linspace(10.0, 18.0, len(dates))),
                self._bars("C.SH", dates, np.linspace(10.0, 16.0, len(dates))),
            ],
            ignore_index=True,
        )
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH", "B.SH", "C.SH"],
                "name": ["A", "B", "C"],
                "list_date": [pd.Timestamp("2020-01-01")] * 3,
                "fund_type": ["股票型ETF"] * 3,
                "benchmark": [
                    "中证A股大盘指数收益率×100%",
                    "中证A股中盘指数收益率×100%",
                    "中证A股小盘指数收益率×100%",
                ],
            }
        )
        config = StrategyConfig(
            initial_capital=100_000,
            warmup_bars=80,
            commission_rate=0,
            min_commission=0,
            slippage_rate=0,
        )

        result = run_backtest(benchmark, etfs, metadata, config)

        buys = result.trades.loc[result.trades["side"] == "buy"]
        sells = result.trades.loc[result.trades["side"] == "sell"]
        self.assertFalse(buys.empty)
        self.assertTrue((buys["quantity"] % 100 == 0).all())
        entry_positions = result.positions.merge(
            buys[["datetime", "symbol"]],
            on=["datetime", "symbol"],
            how="inner",
        )
        self.assertTrue((entry_positions["weight"] <= 0.200001).all())
        self.assertFalse(sells.empty)
        self.assertTrue(
            sells["primary_reason"]
            .isin(["market_below_ema10", "atr_stop_gap", "atr_stop_intraday"])
            .any()
        )
        self.assertIn("buy_planned", result.signals["action"].values)
        self.assertIn("hold", result.signals["action"].values)
        self.assertEqual(result.summary["start_date"], str(dates[0].date()))
        self.assertEqual(result.summary["end_date"], str(dates[-1].date()))
        self.assertIn("total_slippage_cost", result.summary)

        with tempfile.TemporaryDirectory() as directory:
            write_backtest_outputs(result, Path(directory), config)
            expected = {
                "daily_candidates.csv",
                "daily_signals.csv",
                "trades.csv",
                "positions.csv",
                "equity_curve.csv",
                "summary.json",
            }
            self.assertEqual(
                {path.name for path in Path(directory).iterdir()}, expected
            )

    def test_report_start_uses_earlier_bars_only_as_warmup(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH", dates, np.linspace(100.0, 160.0, len(dates)), turnover=0
        )
        etfs = self._bars("A.SH", dates, np.linspace(10.0, 20.0, len(dates)))
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH"],
                "name": ["A"],
                "list_date": [pd.Timestamp("2020-01-01")],
                "fund_type": ["股票型ETF"],
                "benchmark": ["中证A股指数收益率×100%"],
            }
        )
        report_start = dates[100]

        result = run_backtest(
            benchmark,
            etfs,
            metadata,
            StrategyConfig(
                initial_capital=100_000, commission_rate=0, min_commission=0
            ),
            start_date=report_start,
            end_date=dates[-1],
        )

        self.assertEqual(result.equity_curve.iloc[0]["datetime"], report_start)
        self.assertFalse(
            result.candidates.loc[
                result.candidates["signal_date"] == report_start
            ].empty
        )

    def test_no_trade_result_keeps_output_schemas(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=20)
        benchmark = self._bars(
            "000300.SH", dates, np.full(len(dates), 100.0), turnover=0
        )
        etfs = self._bars("A.SH", dates, np.full(len(dates), 10.0))
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH"],
                "name": ["A"],
                "list_date": [pd.Timestamp("2020-01-01")],
                "fund_type": ["股票型ETF"],
                "benchmark": ["中证A股指数收益率×100%"],
            }
        )

        result = run_backtest(benchmark, etfs, metadata)

        self.assertTrue(result.trades.empty)
        self.assertIn("primary_reason", result.trades.columns)
        self.assertIn("weight", result.positions.columns)

    def test_classified_industry_entries_respect_fifty_percent_cap(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        symbols = ["A.SH", "B.SH", "C.SH", "D.SH"]
        etfs = pd.concat(
            [
                self._bars(symbol, dates, np.linspace(10.0, 20.0, len(dates)))
                for symbol in symbols
            ],
            ignore_index=True,
        )
        metadata = pd.DataFrame(
            {
                "symbol": symbols,
                "name": [f"半导体ETF{symbol}" for symbol in symbols],
                "list_date": [pd.Timestamp("2020-01-01")] * len(symbols),
                "fund_type": ["股票型ETF"] * len(symbols),
                "benchmark": [
                    "中证半导体指数收益率×100%",
                    "国证芯片指数收益率×100%",
                    "上证科创板芯片指数收益率×100%",
                    "中证半导体材料设备主题指数收益率×100%",
                ],
            }
        )
        config = StrategyConfig(
            initial_capital=100_000,
            warmup_bars=80,
            risk_per_trade=1,
            commission_rate=0,
            min_commission=0,
            slippage_rate=0,
        )

        result = run_backtest(benchmark, etfs, metadata, config)

        filled = result.signals.loc[result.signals["action"] == "buy_filled"]
        classified = filled.loc[filled["industry"] == "semiconductor"]
        self.assertGreaterEqual(classified["symbol"].nunique(), 3)
        self.assertLessEqual(
            classified["industry_weight_after_entry"].max(),
            0.500001,
        )
        self.assertIn("industry_cap", result.signals["reason"].values)
        self.assertIn("industry", result.positions.columns)
        self.assertIn("industry_weight", result.positions.columns)

    def test_broad_index_entries_do_not_share_an_industry_cap(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        symbols = ["A.SH", "B.SH", "C.SH"]
        etfs = pd.concat(
            [
                self._bars(symbol, dates, np.linspace(10.0, 20.0, len(dates)))
                for symbol in symbols
            ],
            ignore_index=True,
        )
        metadata = pd.DataFrame(
            {
                "symbol": symbols,
                "name": ["沪深300ETF", "中证500ETF", "中证1000ETF"],
                "list_date": [pd.Timestamp("2020-01-01")] * len(symbols),
                "fund_type": ["股票型ETF"] * len(symbols),
                "benchmark": [
                    "沪深300指数收益率×100%",
                    "中证500指数收益率×100%",
                    "中证1000指数收益率×100%",
                ],
            }
        )
        config = StrategyConfig(
            initial_capital=100_000,
            warmup_bars=80,
            risk_per_trade=1,
            commission_rate=0,
            min_commission=0,
            slippage_rate=0,
        )

        result = run_backtest(benchmark, etfs, metadata, config)

        filled = result.signals.loc[result.signals["action"] == "buy_filled"]
        self.assertEqual(filled["symbol"].nunique(), 3)
        self.assertTrue((filled["industry"] == "broad_or_other").all())
        self.assertFalse((result.signals["reason"] == "industry_cap").any())

    def test_entry_requires_configured_consecutive_qualified_days(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        etfs = self._bars("A.SH", dates, np.linspace(10.0, 20.0, len(dates)))
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH"],
                "name": ["沪深300ETF"],
                "list_date": [pd.Timestamp("2020-01-01")],
                "fund_type": ["股票型ETF"],
                "benchmark": ["沪深300指数收益率×100%"],
            }
        )
        common = {
            "initial_capital": 100_000,
            "commission_rate": 0,
            "min_commission": 0,
            "slippage_rate": 0,
        }

        immediate = run_backtest(
            benchmark,
            etfs,
            metadata,
            StrategyConfig(entry_confirmation_days=1, **common),
        )
        confirmed = run_backtest(
            benchmark,
            etfs,
            metadata,
            StrategyConfig(entry_confirmation_days=2, **common),
        )

        immediate_date = immediate.signals.loc[
            immediate.signals["action"] == "buy_planned",
            "signal_date",
        ].min()
        confirmed_date = confirmed.signals.loc[
            confirmed.signals["action"] == "buy_planned",
            "signal_date",
        ].min()
        self.assertEqual(
            dates.get_loc(confirmed_date) - dates.get_loc(immediate_date), 1
        )

    def test_entry_skips_next_open_above_signal_close_plus_atr_limit(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        etfs = self._bars("A.SH", dates, np.linspace(10.0, 20.0, len(dates)))
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH"],
                "name": ["沪深300ETF"],
                "list_date": [pd.Timestamp("2020-01-01")],
                "fund_type": ["股票型ETF"],
                "benchmark": ["沪深300指数收益率×100%"],
            }
        )
        common = {
            "initial_capital": 100_000,
            "commission_rate": 0,
            "min_commission": 0,
            "slippage_rate": 0,
        }
        reference = run_backtest(
            benchmark,
            etfs,
            metadata,
            StrategyConfig(**common),
        )
        first_plan = reference.signals.loc[
            reference.signals["action"] == "buy_planned"
        ].iloc[0]
        execution_date = pd.Timestamp(first_plan["execution_date"])
        signal_date = pd.Timestamp(first_plan["signal_date"])
        signal_close = float(etfs.loc[etfs["datetime"] == signal_date, "close"].iloc[0])
        gapped = etfs.copy()
        execution_row = gapped["datetime"] == execution_date
        gapped.loc[execution_row, "open"] = signal_close + 10.0
        gapped.loc[execution_row, "high"] = signal_close + 10.2
        gapped.loc[execution_row, "low"] = signal_close + 9.8

        result = run_backtest(
            benchmark,
            gapped,
            metadata,
            StrategyConfig(max_entry_gap_atr=1.0, **common),
        )

        skipped = result.signals.loc[
            (result.signals["action"] == "buy_skipped")
            & (result.signals["reason"] == "gap_filter")
        ]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(
            pd.Timestamp(skipped.iloc[0]["execution_date"]), execution_date
        )
        buys_on_gap = result.trades.loc[
            (result.trades["side"] == "buy")
            & (result.trades["datetime"] == execution_date)
        ]
        self.assertTrue(buys_on_gap.empty)

    def test_correlated_positions_share_thirty_percent_cluster_cap(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        symbols = ["A.SH", "B.SH", "C.SH"]
        identical_close = np.linspace(10.0, 20.0, len(dates))
        etfs = pd.concat(
            [self._bars(symbol, dates, identical_close) for symbol in symbols],
            ignore_index=True,
        )
        metadata = pd.DataFrame(
            {
                "symbol": symbols,
                "name": ["沪深300ETF", "中证500ETF", "中证1000ETF"],
                "list_date": [pd.Timestamp("2020-01-01")] * len(symbols),
                "fund_type": ["股票型ETF"] * len(symbols),
                "benchmark": [
                    "沪深300指数收益率×100%",
                    "中证500指数收益率×100%",
                    "中证1000指数收益率×100%",
                ],
            }
        )
        config = StrategyConfig(
            initial_capital=100_000,
            entry_rank=3,
            risk_per_trade=1,
            commission_rate=0,
            min_commission=0,
            slippage_rate=0,
            correlation_lookback=60,
            min_correlation_observations=40,
            correlation_threshold=0.90,
            max_correlation_weight=0.30,
        )

        result = run_backtest(benchmark, etfs, metadata, config)

        first_entries = result.signals.loc[
            result.signals["action"].isin(["buy_filled", "buy_skipped"])
        ].sort_values(["execution_date", "symbol"])
        first_execution = first_entries.iloc[0]["execution_date"]
        first_entries = first_entries.loc[
            first_entries["execution_date"] == first_execution
        ]
        filled = first_entries.loc[first_entries["action"] == "buy_filled"]
        self.assertEqual(len(filled), 2)
        self.assertLessEqual(
            filled["correlation_weight_after_entry"].max(),
            0.300001,
        )
        self.assertIn("correlation_cap", first_entries["reason"].values)

    def test_dynamic_risk_trims_correlated_exposure_without_same_day_restore(
        self,
    ) -> None:
        dates = pd.bdate_range("2025-01-02", periods=140)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 170.0, len(dates)),
            turnover=0,
        )
        symbols = ["A.SH", "B.SH", "C.SH"]
        identical_close = np.linspace(10.0, 22.0, len(dates))
        etfs = pd.concat(
            [self._bars(symbol, dates, identical_close) for symbol in symbols],
            ignore_index=True,
        )
        etfs["open"] = etfs["close"]
        metadata = pd.DataFrame(
            {
                "symbol": symbols,
                "name": ["沪深300ETF", "中证500ETF", "中证1000ETF"],
                "list_date": [pd.Timestamp("2020-01-01")] * len(symbols),
                "fund_type": ["股票型ETF"] * len(symbols),
                "benchmark": [
                    "沪深300指数收益率×100%",
                    "中证500指数收益率×100%",
                    "中证1000指数收益率×100%",
                ],
            }
        )
        config = StrategyConfig(
            initial_capital=100_000,
            entry_rank=3,
            risk_per_trade=1,
            atr_stop_multiple=3.0,
            max_industry_weight=1.0,
            correlation_threshold=0.90,
            max_correlation_weight=0.30,
            max_portfolio_stop_risk=0.03,
            max_cluster_stop_risk=0.015,
            dynamic_risk_enabled=True,
            commission_rate=0,
            min_commission=0,
            slippage_rate=0,
        )

        result = run_backtest(benchmark, etfs, metadata, config)

        trims = result.signals.loc[result.signals["action"] == "trim_filled"]
        self.assertFalse(trims.empty)
        daily_cluster_weight = result.positions.groupby("datetime")["weight"].sum()
        self.assertLessEqual(daily_cluster_weight.max(), 0.300001)
        position_stop_risk = (
            result.positions["market_value"] / result.positions["quantity"]
            - result.positions["stop_price"]
        ).clip(lower=0) * result.positions["quantity"]
        stop_risk_by_date = position_stop_risk.groupby(
            result.positions["datetime"]
        ).sum()
        equity_by_date = result.equity_curve.set_index("datetime")["equity"]
        cluster_stop_risk_weight = stop_risk_by_date / equity_by_date
        breach_dates = set(
            cluster_stop_risk_weight.loc[
                cluster_stop_risk_weight > config.max_cluster_stop_risk
            ].index
        )
        planned_trim_dates = set(
            result.signals.loc[
                result.signals["action"] == "trim_planned", "signal_date"
            ]
        )
        self.assertTrue(breach_dates)
        self.assertTrue(breach_dates.issubset(planned_trim_dates))
        restores = result.signals.loc[result.signals["action"] == "restore_filled"]
        trim_keys = set(zip(trims["execution_date"], trims["symbol"], strict=True))
        restore_keys = set(
            zip(restores["execution_date"], restores["symbol"], strict=True)
        )
        self.assertTrue(trim_keys.isdisjoint(restore_keys))

    def test_untradeable_delisted_position_is_written_off(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        last_etf_bar = 105
        etfs = self._bars(
            "A.SH",
            dates[:last_etf_bar],
            np.linspace(10.0, 18.0, last_etf_bar),
        )
        delist_date = dates[last_etf_bar + 2]
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH"],
                "name": ["中证A股历史ETF"],
                "list_date": [pd.Timestamp("2020-01-01")],
                "delist_date": [delist_date],
                "status": ["D"],
                "fund_type": ["股票型ETF"],
                "benchmark": ["中证A股指数收益率×100%"],
            }
        )

        result = run_backtest(
            benchmark,
            etfs,
            metadata,
            StrategyConfig(
                initial_capital=100_000,
                commission_rate=0,
                min_commission=0,
                slippage_rate=0,
            ),
        )

        writeoffs = result.trades.loc[
            result.trades["primary_reason"] == "delisted_writeoff"
        ]
        self.assertEqual(len(writeoffs), 1)
        self.assertEqual(writeoffs.iloc[0]["datetime"], delist_date)
        self.assertEqual(writeoffs.iloc[0]["raw_price"], 0.0)

    def test_prelisting_bars_do_not_count_toward_indicator_warmup(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=120)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        etfs = self._bars("A.SH", dates, np.linspace(10.0, 20.0, len(dates)))
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH"],
                "name": ["沪深300ETF"],
                "list_date": [dates[100]],
                "fund_type": ["股票型ETF"],
                "benchmark": ["沪深300指数收益率×100%"],
            }
        )

        result = run_backtest(
            benchmark,
            etfs,
            metadata,
            StrategyConfig(
                initial_capital=100_000,
                min_listing_months=0,
                warmup_bars=80,
                commission_rate=0,
                min_commission=0,
                slippage_rate=0,
            ),
        )

        self.assertTrue(result.trades.empty)

    def test_liquidity_leader_change_does_not_add_same_benchmark_clone(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=130)
        benchmark = self._bars(
            "000300.SH",
            dates,
            np.linspace(100.0, 160.0, len(dates)),
            turnover=0,
        )
        a = self._bars("A.SH", dates, np.linspace(10.0, 20.0, len(dates)))
        b = self._bars("B.SH", dates, np.linspace(10.0, 19.0, len(dates)))
        a.loc[a.index >= 100, "turnover"] = 250_000_000
        b.loc[b.index < 100, "turnover"] = 250_000_000
        b.loc[b.index >= 100, "turnover"] = 400_000_000
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH", "B.SH"],
                "name": ["中证A股ETF甲", "中证A股ETF乙"],
                "list_date": [pd.Timestamp("2020-01-01")] * 2,
                "fund_type": ["股票型ETF"] * 2,
                "benchmark": ["中证A股指数收益率×100%"] * 2,
            }
        )

        result = run_backtest(
            benchmark,
            pd.concat([a, b], ignore_index=True),
            metadata,
            StrategyConfig(
                initial_capital=100_000,
                commission_rate=0,
                min_commission=0,
                slippage_rate=0,
            ),
        )

        buys = result.trades.loc[result.trades["side"] == "buy"]
        self.assertEqual(buys["symbol"].nunique(), 1)
        self.assertIn("benchmark_duplicate", result.signals["reason"].values)

    @staticmethod
    def _bars(
        symbol: str,
        dates: pd.DatetimeIndex,
        close: np.ndarray,
        turnover: float = 300_000_000,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": symbol,
                "datetime": dates,
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
                "volume": 10_000_000,
                "turnover": turnover,
                "is_trading": True,
            }
        )


if __name__ == "__main__":
    unittest.main()
