import unittest

import numpy as np
import pandas as pd

from stock.etf.narrow_channel_etf_strategy import (
    NarrowChannelConfig,
    _direction_summary,
    aggregate_complete_weeks,
    build_narrow_channel_signals,
    calculate_wilder_atr,
    classify_weekly_channels,
    evaluate_daily_entries,
    prepare_etf_bars,
    run_narrow_channel_backtest,
)


class NarrowChannelDataAndWeeklyTests(unittest.TestCase):
    @staticmethod
    def _daily_bars() -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-05", periods=11)
        close = pd.Series(np.linspace(10.0, 11.0, len(dates)))
        return pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * len(dates),
                "datetime": dates,
                "open": close - 0.05,
                "high": close + 0.10,
                "low": close - 0.10,
                "close": close,
                "volume": [1_000.0] * len(dates),
            }
        )

    @staticmethod
    def _weekly_bars(*, rising: bool) -> pd.DataFrame:
        dates = pd.date_range("2025-01-03", periods=24, freq="W-FRI")
        close = np.arange(10.0, 34.0)
        if not rising:
            close = close[::-1]
        return pd.DataFrame(
            {
                "datetime": dates,
                "open": close - 0.15 if rising else close + 0.15,
                "high": close + 0.30,
                "low": close - 0.30,
                "close": close,
                "volume": [5_000.0] * len(dates),
            }
        )

    def test_prepare_rejects_duplicate_dates_and_foreign_symbol(self) -> None:
        config = NarrowChannelConfig()
        bars = self._daily_bars()
        duplicate = pd.concat([bars, bars.iloc[[0]]], ignore_index=True)

        with self.assertRaisesRegex(ValueError, "duplicate dates"):
            prepare_etf_bars(duplicate, config)

        foreign = bars.assign(symbol="510300.SH")
        with self.assertRaisesRegex(ValueError, "missing symbol"):
            prepare_etf_bars(foreign, config)

    def test_prepare_rejects_invalid_ohlcv(self) -> None:
        bars = self._daily_bars()
        bars.loc[3, "low"] = bars.loc[3, "high"] + 1.0

        with self.assertRaisesRegex(ValueError, "invalid OHLCV"):
            prepare_etf_bars(bars, NarrowChannelConfig())

    def test_aggregate_excludes_final_unfinished_friday_week(self) -> None:
        prepared = prepare_etf_bars(self._daily_bars(), NarrowChannelConfig())

        weekly = aggregate_complete_weeks(prepared)

        self.assertEqual(weekly["datetime"].dt.strftime("%Y-%m-%d").tolist(), [
            "2026-01-09",
            "2026-01-16",
        ])
        self.assertEqual(weekly["source_max_date"].dt.strftime("%Y-%m-%d").tolist(), [
            "2026-01-09",
            "2026-01-16",
        ])

    def test_two_qualifying_windows_confirm_up_channel(self) -> None:
        result = classify_weekly_channels(
            self._weekly_bars(rising=True),
            NarrowChannelConfig(),
        )

        first_qualified = result.index[result["up_qualified"]][0]
        self.assertEqual(result.loc[first_qualified, "weekly_state"], "NEUTRAL")
        self.assertEqual(result.loc[first_qualified + 1, "weekly_state"], "UP_CHANNEL")
        self.assertEqual(result.iloc[-1]["weekly_state"], "UP_CHANNEL")
        self.assertFalse(result["down_qualified"].any())

    def test_two_qualifying_windows_confirm_down_channel(self) -> None:
        result = classify_weekly_channels(
            self._weekly_bars(rising=False),
            NarrowChannelConfig(),
        )

        first_qualified = result.index[result["down_qualified"]][0]
        self.assertEqual(result.loc[first_qualified, "weekly_state"], "NEUTRAL")
        self.assertEqual(
            result.loc[first_qualified + 1, "weekly_state"],
            "DOWN_CHANNEL",
        )
        self.assertEqual(result.iloc[-1]["weekly_state"], "DOWN_CHANNEL")
        self.assertFalse(result["up_qualified"].any())

    def test_wilder_atr_uses_simple_average_seed_then_recursive_smoothing(self) -> None:
        true_ranges = np.arange(1.0, 16.0)
        frame = pd.DataFrame(
            {
                "high": 100.0 + true_ranges / 2,
                "low": 100.0 - true_ranges / 2,
                "close": [100.0] * len(true_ranges),
            }
        )

        atr = calculate_wilder_atr(frame, 14)

        self.assertTrue(atr.iloc[:13].isna().all())
        self.assertAlmostEqual(atr.iloc[13], np.mean(true_ranges[:14]))
        self.assertAlmostEqual(
            atr.iloc[14],
            (np.mean(true_ranges[:14]) * 13 + true_ranges[14]) / 14,
        )


class NarrowChannelSignalTests(unittest.TestCase):
    @staticmethod
    def _entry_features(*, long_side: bool) -> pd.DataFrame:
        dates = pd.bdate_range("2026-04-01", periods=6)
        if long_side:
            close = [10.0, 9.8, 9.6, 9.7, 9.8, 10.5]
            open_ = [10.0, 9.9, 9.7, 9.6, 9.7, 10.0]
            high = [10.2, 10.0, 9.8, 9.9, 10.0, 10.6]
            low = [9.8, 9.6, 9.4, 9.5, 9.6, 9.9]
            ema5 = [9.5] * 5 + [10.3]
            ema10 = [9.4] * 5 + [10.0]
            ema20 = [9.2] * 5 + [9.6]
            state = "UP_CHANNEL"
        else:
            close = [10.0, 10.2, 10.4, 10.3, 10.2, 9.5]
            open_ = [10.0, 10.1, 10.3, 10.4, 10.3, 10.0]
            high = [10.2, 10.4, 10.6, 10.5, 10.4, 10.1]
            low = [9.8, 10.0, 10.2, 10.1, 10.0, 9.4]
            ema5 = [10.5] * 5 + [9.7]
            ema10 = [10.6] * 5 + [10.0]
            ema20 = [10.8] * 5 + [10.4]
            state = "DOWN_CHANNEL"
        return pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * 6,
                "datetime": dates,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": [1_000.0] * 6,
                "ema5": ema5,
                "ema10": ema10,
                "ema20": ema20,
                "atr14d": [1.0] * 6,
                "weekly_state": [state] * 6,
            }
        )

    @staticmethod
    def _long_history(periods: int) -> pd.DataFrame:
        dates = pd.bdate_range("2025-01-02", periods=periods)
        trend = np.linspace(10.0, 24.0, periods)
        wave = np.sin(np.arange(periods) / 4.0) * 0.20
        close = trend + wave
        open_ = close - np.sin(np.arange(periods)) * 0.05
        return pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * periods,
                "datetime": dates,
                "open": open_,
                "high": np.maximum(open_, close) + 0.15,
                "low": np.minimum(open_, close) - 0.15,
                "close": close,
                "volume": [1_000.0] * periods,
            }
        )

    def test_daily_long_and_short_entry_rules_are_mirrored(self) -> None:
        long_result = evaluate_daily_entries(self._entry_features(long_side=True))
        short_result = evaluate_daily_entries(self._entry_features(long_side=False))

        self.assertTrue(long_result.iloc[-1]["long_signal"])
        self.assertFalse(long_result.iloc[-1]["short_signal"])
        self.assertEqual(
            long_result.iloc[-1]["signal_type"],
            "up_channel_pullback_long",
        )
        self.assertTrue(short_result.iloc[-1]["short_signal"])
        self.assertFalse(short_result.iloc[-1]["long_signal"])
        self.assertEqual(
            short_result.iloc[-1]["signal_type"],
            "down_channel_rally_short",
        )

    def test_appending_future_rows_does_not_change_historical_signals(self) -> None:
        config = NarrowChannelConfig()
        base = prepare_etf_bars(self._long_history(220), config)
        future = self._long_history(225).iloc[220:].copy()
        future[["open", "high", "low", "close"]] *= 10.0
        extended = prepare_etf_bars(pd.concat([base, future], ignore_index=True), config)

        base_weekly = classify_weekly_channels(aggregate_complete_weeks(base), config)
        extended_weekly = classify_weekly_channels(
            aggregate_complete_weeks(extended),
            config,
        )
        base_signals = build_narrow_channel_signals(base, base_weekly, config)
        extended_signals = build_narrow_channel_signals(
            extended,
            extended_weekly,
            config,
        )
        comparable = extended_signals.loc[
            extended_signals["datetime"] <= base_signals["datetime"].max()
        ]

        pd.testing.assert_frame_equal(
            base_signals[["datetime", "weekly_state", "long_signal", "short_signal"]],
            comparable[["datetime", "weekly_state", "long_signal", "short_signal"]]
            .reset_index(drop=True),
        )

    def test_holiday_week_state_is_available_on_actual_last_trading_day(self) -> None:
        dates = list(pd.bdate_range("2026-03-02", "2026-05-07"))
        dates.append(pd.Timestamp("2026-05-11"))
        close = np.linspace(10.0, 12.0, len(dates))
        bars = pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * len(dates),
                "datetime": dates,
                "open": close - 0.05,
                "high": close + 0.10,
                "low": close - 0.10,
                "close": close,
                "volume": [1_000.0] * len(dates),
            }
        )
        weekly = pd.DataFrame(
            {
                "datetime": [pd.Timestamp("2026-05-08")],
                "source_max_date": [pd.Timestamp("2026-05-07")],
                "weekly_state": ["UP_CHANNEL"],
                "strong_bear_week": [True],
                "strong_bull_week": [False],
                "weekly_trail_long": [10.0],
                "weekly_trail_short": [12.0],
            }
        )

        result = build_narrow_channel_signals(
            bars,
            weekly,
            NarrowChannelConfig(),
        )
        thursday = result.loc[result["datetime"].eq(pd.Timestamp("2026-05-07"))].iloc[0]
        monday = result.loc[result["datetime"].eq(pd.Timestamp("2026-05-11"))].iloc[0]

        self.assertEqual(thursday["weekly_state"], "UP_CHANNEL")
        self.assertTrue(thursday["strong_bear_event"])
        self.assertFalse(monday["strong_bear_event"])


class NarrowChannelBacktestTests(unittest.TestCase):
    @staticmethod
    def _signals(rows: list[dict[str, object]]) -> pd.DataFrame:
        defaults: dict[str, object] = {
            "symbol": "159915.SZ",
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 1_000.0,
            "ema5": 10.2,
            "ema10": 10.0,
            "ema20": 9.8,
            "atr14d": 2 / 3,
            "weekly_state": "UP_CHANNEL",
            "weekly_event_date": pd.NaT,
            "strong_bear_event": False,
            "strong_bull_event": False,
            "weekly_trail_long": 9.8,
            "weekly_trail_short": 10.2,
            "long_signal": False,
            "short_signal": False,
            "signal_type": "",
            "long_structure_stop": 9.0,
            "short_structure_stop": 11.0,
            "max_entry": 10.75,
            "min_entry": 9.25,
            "long_trend_exit": False,
            "short_trend_exit": False,
        }
        return pd.DataFrame([{**defaults, **row} for row in rows])

    @staticmethod
    def _zero_cost_config() -> NarrowChannelConfig:
        return NarrowChannelConfig(one_way_cost_rate=0.0, short_borrow_rate=0.0)

    def test_entry_day_stop_waits_for_next_open_and_beats_same_day_two_r(self) -> None:
        dates = pd.bdate_range("2026-05-04", periods=3)
        signals = self._signals(
            [
                {
                    "datetime": dates[0],
                    "long_signal": True,
                    "signal_type": "up_channel_pullback_long",
                },
                {
                    "datetime": dates[1],
                    "open": 10.0,
                    "high": 12.5,
                    "low": 8.5,
                    "close": 11.0,
                },
                {"datetime": dates[2], "open": 8.0, "high": 8.5, "low": 7.8},
            ]
        )

        result = run_narrow_channel_backtest(signals, self._zero_cost_config())

        trade = result.trades.iloc[0]
        self.assertEqual(trade["entry_date"], dates[1])
        self.assertEqual(trade["exit_date"], dates[2])
        self.assertEqual(trade["exit_reason"], "deferred_t1_stop")
        self.assertTrue(trade["entry_day_stop_breached"])
        self.assertEqual(trade["initial_stop"], 9.0)
        self.assertEqual(trade["exit_fill"], 8.0)
        self.assertEqual(trade["net_r"], -2.0)

    def test_gap_stop_counts_exit_open_but_not_later_daily_extremes(self) -> None:
        dates = pd.bdate_range("2026-05-04", periods=3)
        signals = self._signals(
            [
                {
                    "datetime": dates[0],
                    "long_signal": True,
                    "signal_type": "up_channel_pullback_long",
                },
                {"datetime": dates[1], "open": 10.0, "high": 10.5, "low": 9.5},
                {"datetime": dates[2], "open": 8.0, "high": 20.0, "low": 7.0},
            ]
        )

        trade = run_narrow_channel_backtest(
            signals,
            self._zero_cost_config(),
        ).trades.iloc[0]

        self.assertEqual(trade["exit_reason"], "hard_stop_gap")
        self.assertEqual(trade["mfe_r"], 0.5)
        self.assertEqual(trade["mae_r"], 2.0)

    def test_intraday_stop_uses_stop_first_path_for_mfe_and_mae(self) -> None:
        dates = pd.bdate_range("2026-05-04", periods=3)
        signals = self._signals(
            [
                {
                    "datetime": dates[0],
                    "long_signal": True,
                    "signal_type": "up_channel_pullback_long",
                },
                {"datetime": dates[1], "open": 10.0, "high": 10.5, "low": 9.5},
                {"datetime": dates[2], "open": 10.0, "high": 15.0, "low": 8.5},
            ]
        )

        trade = run_narrow_channel_backtest(
            signals,
            self._zero_cost_config(),
        ).trades.iloc[0]

        self.assertEqual(trade["exit_reason"], "hard_stop")
        self.assertEqual(trade["mfe_r"], 0.5)
        self.assertEqual(trade["mae_r"], 1.0)

    def test_gap_filter_skips_committed_signal(self) -> None:
        dates = pd.bdate_range("2026-05-04", periods=2)
        signals = self._signals(
            [
                {
                    "datetime": dates[0],
                    "long_signal": True,
                    "signal_type": "up_channel_pullback_long",
                    "max_entry": 10.5,
                },
                {"datetime": dates[1], "open": 11.0, "high": 11.2},
            ]
        )

        result = run_narrow_channel_backtest(signals, self._zero_cost_config())

        self.assertTrue(result.trades.empty)
        self.assertEqual(result.signals.loc[0, "entry_status"], "skipped_gap_or_invalidated")

    def test_combined_summary_has_no_strategy_verdict(self) -> None:
        dates = pd.bdate_range("2026-05-04", periods=2)
        signals = self._signals(
            [{"datetime": dates[0]}, {"datetime": dates[1]}]
        )

        result = run_narrow_channel_backtest(signals, self._zero_cost_config())

        self.assertEqual(result.summary["combined"]["verdict"], "NOT_APPLICABLE")

    def test_trailing_stop_activates_after_two_r_and_never_moves_down(self) -> None:
        dates = pd.bdate_range("2026-05-04", periods=4)
        signals = self._signals(
            [
                {
                    "datetime": dates[0],
                    "long_signal": True,
                    "signal_type": "up_channel_pullback_long",
                },
                {"datetime": dates[1], "open": 10.0, "high": 10.8, "low": 9.4},
                {
                    "datetime": dates[2],
                    "high": 12.2,
                    "low": 9.5,
                    "weekly_trail_long": 9.8,
                },
                {
                    "datetime": dates[3],
                    "open": 10.5,
                    "high": 11.0,
                    "low": 10.2,
                    "weekly_trail_long": 8.0,
                },
            ]
        )

        result = run_narrow_channel_backtest(signals, self._zero_cost_config())

        self.assertEqual(result.signals.loc[2, "active_stop"], 10.0)
        self.assertEqual(result.signals.loc[3, "active_stop"], 10.0)
        self.assertEqual(result.trades.iloc[0]["status"], "OPEN")

    def test_daily_and_weekly_exit_reasons_execute_at_next_open(self) -> None:
        for flag, reason in (
            ("long_trend_exit", "daily_trend_exit"),
            ("strong_bear_event", "strong_opposite_week"),
        ):
            with self.subTest(flag=flag):
                dates = pd.bdate_range("2026-05-04", periods=3)
                signals = self._signals(
                    [
                        {
                            "datetime": dates[0],
                            "long_signal": True,
                            "signal_type": "up_channel_pullback_long",
                        },
                        {
                            "datetime": dates[1],
                            "open": 10.0,
                            "high": 10.8,
                            "low": 9.4,
                            flag: True,
                        },
                        {"datetime": dates[2], "open": 10.4, "low": 10.0},
                    ]
                )

                result = run_narrow_channel_backtest(
                    signals,
                    self._zero_cost_config(),
                )

                self.assertEqual(result.trades.iloc[0]["exit_date"], dates[2])
                self.assertEqual(result.trades.iloc[0]["exit_reason"], reason)

    def test_short_trade_accrues_calendar_day_borrow_cost(self) -> None:
        dates = pd.to_datetime(["2026-05-08", "2026-05-11", "2026-05-12"])
        signals = self._signals(
            [
                {
                    "datetime": dates[0],
                    "weekly_state": "DOWN_CHANNEL",
                    "short_signal": True,
                    "signal_type": "down_channel_rally_short",
                },
                {
                    "datetime": dates[1],
                    "weekly_state": "DOWN_CHANNEL",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "short_trend_exit": True,
                },
                {
                    "datetime": dates[2],
                    "weekly_state": "DOWN_CHANNEL",
                    "open": 9.0,
                    "high": 9.5,
                    "low": 8.5,
                },
            ]
        )
        config = NarrowChannelConfig(
            one_way_cost_rate=0.0,
            short_borrow_rate=0.365,
        )

        result = run_narrow_channel_backtest(signals, config)

        trade = result.trades.iloc[0]
        self.assertAlmostEqual(trade["borrow_cost"], 0.01)
        self.assertAlmostEqual(trade["net_pnl"], 0.99)
        self.assertEqual(trade["exit_reason"], "daily_trend_exit")

    def test_short_trailing_break_even_includes_exit_day_borrow_cost(self) -> None:
        dates = pd.bdate_range("2026-05-04", periods=3)
        signals = self._signals(
            [
                {
                    "datetime": dates[0],
                    "weekly_state": "DOWN_CHANNEL",
                    "short_signal": True,
                    "signal_type": "down_channel_rally_short",
                },
                {
                    "datetime": dates[1],
                    "weekly_state": "DOWN_CHANNEL",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 7.5,
                },
                {
                    "datetime": dates[2],
                    "weekly_state": "DOWN_CHANNEL",
                    "open": 9.8,
                    "high": 10.2,
                    "low": 9.5,
                },
            ]
        )
        config = NarrowChannelConfig(
            one_way_cost_rate=0.0,
            short_borrow_rate=0.365,
        )

        trade = run_narrow_channel_backtest(signals, config).trades.iloc[0]

        self.assertEqual(trade["exit_reason"], "hard_stop")
        self.assertAlmostEqual(trade["borrow_cost"], 0.01)
        self.assertAlmostEqual(trade["net_pnl"], 0.0)

    def test_summary_distinguishes_infinite_profit_factor_and_small_trend_sample(
        self,
    ) -> None:
        trades = pd.DataFrame(
            {
                "side": ["LONG"] * 10,
                "status": ["CLOSED"] * 10,
                "net_r": [1.0] * 10,
                "exit_date": pd.bdate_range("2026-01-02", periods=10),
                "mfe_r": [3.0] * 10,
                "profit_capture_ratio": [1 / 3] * 10,
                "holding_days": [5] * 10,
                "borrow_cost": [0.0] * 10,
            }
        )
        signals = pd.DataFrame(
            {
                "long_signal": [True] * 10,
                "short_signal": [False] * 10,
                "entry_status": ["filled"] * 10,
            }
        )

        summary = _direction_summary(trades, signals, "LONG")

        self.assertIsNone(summary["profit_factor"])
        self.assertEqual(summary["profit_factor_status"], "INFINITE_NO_LOSSES")
        self.assertEqual(
            summary["big_trend_capture_status"],
            "INSUFFICIENT_SAMPLE",
        )

    def test_summary_treats_floating_point_residuals_as_break_even(self) -> None:
        trades = pd.DataFrame(
            {
                "side": ["SHORT"] * 10,
                "status": ["CLOSED"] * 10,
                "net_r": [1e-15, -1e-15] + [0.0] * 8,
                "exit_date": pd.bdate_range("2026-01-02", periods=10),
                "mfe_r": [0.0] * 10,
                "profit_capture_ratio": [np.nan] * 10,
                "holding_days": [5] * 10,
                "borrow_cost": [0.0] * 10,
            }
        )
        signals = pd.DataFrame(
            {
                "long_signal": [False] * 10,
                "short_signal": [True] * 10,
                "entry_status": ["filled"] * 10,
            }
        )

        summary = _direction_summary(trades, signals, "SHORT")

        self.assertEqual(summary["win_rate"], 0.0)
        self.assertEqual(summary["total_net_r"], 0.0)
        self.assertIsNone(summary["profit_factor"])
        self.assertEqual(summary["profit_factor_status"], "NO_GAIN_OR_LOSS")


if __name__ == "__main__":
    unittest.main()
