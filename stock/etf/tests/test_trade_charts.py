import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image

from stock.etf.render_trade_charts import (
    _Fonts,
    _assign_marker_label_levels,
    _draw_chart_panel,
    _draw_metadata,
    aggregate_weekly_bars,
    build_trade_markers,
    make_chart_filename,
    render_all_trade_charts,
    render_symbol_card,
    select_daily_window,
)


class TradeChartTransformTests(unittest.TestCase):
    def test_marker_label_levels_stagger_nearby_x_positions(self) -> None:
        levels = _assign_marker_label_levels(
            [100, 104, 108, 112, 116, 120, 160],
            level_count=6,
            min_spacing=24,
        )

        self.assertEqual(len(set(levels[:6])), 6)
        self.assertEqual(levels[-1], 0)

    def test_aggregate_weekly_bars_uses_friday_ohlcv(self) -> None:
        daily = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    ["2026-05-04", "2026-05-05", "2026-05-08", "2026-05-11"]
                ),
                "open": [10.0, 11.0, 12.0, 20.0],
                "high": [12.0, 14.0, 15.0, 22.0],
                "low": [9.0, 10.0, 11.0, 19.0],
                "close": [11.0, 12.0, 14.0, 21.0],
                "volume": [100.0, 200.0, 300.0, 400.0],
            }
        )

        weekly = aggregate_weekly_bars(daily)

        self.assertEqual(weekly.loc[0, "datetime"], pd.Timestamp("2026-05-08"))
        self.assertEqual(weekly.loc[0, "open"], 10.0)
        self.assertEqual(weekly.loc[0, "high"], 15.0)
        self.assertEqual(weekly.loc[0, "low"], 9.0)
        self.assertEqual(weekly.loc[0, "close"], 14.0)
        self.assertEqual(weekly.loc[0, "volume"], 600.0)

    def test_daily_window_keeps_twenty_bars_before_report(self) -> None:
        dates = pd.bdate_range("2026-03-02", periods=60)
        daily = self._bars(dates)
        report_start = dates[35]

        window = select_daily_window(daily, report_start, dates[-1], pre_bars=20)

        self.assertEqual(window.iloc[0]["datetime"], dates[15])
        self.assertEqual(window.iloc[-1]["datetime"], dates[-1])

    def test_trade_markers_number_sides_and_map_weekly_dates(self) -> None:
        trades = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-05-04", "2026-05-05", "2026-05-08"]),
                "side": ["buy", "buy", "sell"],
                "fill_price": [10.0, 11.0, 12.0],
                "quantity": [100, 200, 300],
            }
        )

        daily_markers = build_trade_markers(trades, weekly=False)
        weekly_markers = build_trade_markers(trades, weekly=True)

        self.assertEqual(daily_markers["label"].tolist(), ["B1", "B2", "S1"])
        self.assertEqual(
            weekly_markers["datetime"].tolist(),
            [pd.Timestamp("2026-05-08")] * 3,
        )

    def test_trade_markers_merge_same_day_and_side(self) -> None:
        trades = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    ["2026-05-04 10:00", "2026-05-04 14:00", "2026-05-05 10:00"]
                ),
                "side": ["buy", "buy", "sell"],
                "fill_price": [10.0, 12.0, 13.0],
                "quantity": [100, 300, 400],
            }
        )

        markers = build_trade_markers(trades, weekly=False)

        self.assertEqual(markers["label"].tolist(), ["B1/B2", "S1"])
        self.assertEqual(markers.loc[0, "datetime"], pd.Timestamp("2026-05-04"))
        self.assertEqual(markers.loc[0, "quantity"], 400)
        self.assertEqual(markers.loc[0, "fill_price"], 11.5)

    def test_trade_markers_append_target_weights_when_same_day_trades_merge(
        self,
    ) -> None:
        trades = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2026-05-04 10:00",
                        "2026-05-04 14:00",
                        "2026-05-05 10:00",
                        "2026-05-05 14:00",
                    ]
                ),
                "side": ["buy", "buy", "sell", "sell"],
                "fill_price": [10.0, 12.0, 13.0, 11.0],
                "quantity": [100, 300, 200, 200],
                "primary_reason": [
                    "target_weight_0_to_0.5",
                    "target_weight_0.5_to_1",
                    "target_weight_1_to_0.5",
                    "target_weight_0.5_to_0",
                ],
            }
        )

        markers = build_trade_markers(trades, weekly=False)

        self.assertEqual(
            markers["label"].tolist(),
            ["B1→50%/B2→100%", "S1→50%/S2→0%"],
        )

    def test_trade_markers_keep_old_labels_for_unmatched_reasons(self) -> None:
        trades = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-05-04", "2026-05-05"]),
                "side": ["buy", "sell"],
                "fill_price": [10.0, 12.0],
                "quantity": [100, 100],
                "primary_reason": ["rs_top5", "target_weight_invalid"],
            }
        )

        markers = build_trade_markers(trades, weekly=False)

        self.assertEqual(markers["label"].tolist(), ["B1", "S1"])

    def test_filename_keeps_chinese_and_sanitizes_punctuation(self) -> None:
        filename = make_chart_filename(1, "159659.SZ", "招商纳斯达克100ETF(QDII)")

        self.assertEqual(filename, "0001_159659_SZ_招商纳斯达克100ETF_QDII.png")

    def test_render_symbol_card_creates_expected_png(self) -> None:
        arguments = self._card_arguments()

        image = render_symbol_card(**arguments)
        explicit_defaults = render_symbol_card(
            **arguments,
            review_label="ETF Rotation Trade Review",
            performance=None,
            daily_state_scores=None,
            weekly_state_scores=None,
        )

        self.assertEqual(image.size, (1680, 1000))
        self.assertEqual(image.tobytes(), explicit_defaults.tobytes())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "chart.png"
            image.save(path)
            self.assertGreater(path.stat().st_size, 10_000)

    def test_render_symbol_card_draws_regime_layers_and_scores(self) -> None:
        arguments = self._card_arguments()
        dates = pd.bdate_range("2026-03-02", periods=60)
        weekly = aggregate_weekly_bars(self._bars(dates))
        daily_states = self._state_scores(dates + pd.Timedelta(hours=15))
        weekly_states = self._state_scores(
            pd.DatetimeIndex(weekly["datetime"]) + pd.Timedelta(hours=15)
        )
        performance = {
            "total_return": 0.2345,
            "max_drawdown": -0.1234,
            "sharpe": 1.25,
            "annual_one_way_turnover": 3.5,
            "target_distribution": {
                "0": {"days": 12, "fraction": 0.2},
                "0.5": {"days": 18, "fraction": 0.3},
                "1": {"days": 30, "fraction": 0.5},
            },
        }

        image = render_symbol_card(
            **arguments,
            review_label="Regime Overlay Review",
            performance=performance,
            daily_state_scores=daily_states,
            weekly_state_scores=weekly_states,
        )
        without_performance = render_symbol_card(
            **arguments,
            review_label="Regime Overlay Review",
            daily_state_scores=daily_states,
            weekly_state_scores=weekly_states,
        )
        legacy = render_symbol_card(**arguments)

        self.assertEqual(image.size, (1680, 1120))
        self.assertNotEqual(
            image.crop((0, 40, 1680, 80)).tobytes(),
            legacy.crop((0, 40, 1680, 80)).tobytes(),
        )
        self.assertNotEqual(
            image.crop((16, 520, 430, 1050)).tobytes(),
            without_performance.crop((16, 520, 430, 1050)).tobytes(),
        )

        pixels = np.asarray(image)
        state_colors = [(243, 193, 188), (159, 216, 181)]
        weekly_price_area = pixels[144:486, 514:1639]
        daily_price_area = pixels[649:905, 514:1639]
        for color in state_colors:
            with self.subTest(area="weekly", color=color):
                count = np.all(weekly_price_area == color, axis=2).sum()
                self.assertGreater(int(count), 1_000)
            with self.subTest(area="daily", color=color):
                count = np.all(daily_price_area == color, axis=2).sum()
                self.assertGreater(int(count), 1_000)

        score_area = pixels[915:997, 514:1639]
        for color in [(37, 99, 235), (217, 119, 6)]:
            with self.subTest(score_color=color):
                mask = np.all(score_area == color, axis=2)
                self.assertGreater(int(mask.sum()), 100)
                self.assertGreater(int(mask.any(axis=1).sum()), 8)

    def test_render_symbol_card_rejects_invalid_review_label(self) -> None:
        arguments = self._card_arguments()

        for review_label in (None, "", "   ", 123):
            with self.subTest(review_label=review_label):
                with self.assertRaisesRegex(ValueError, "review_label"):
                    render_symbol_card(**arguments, review_label=review_label)

    def test_render_symbol_card_rejects_invalid_performance(self) -> None:
        arguments = self._card_arguments()
        valid = {
            "total_return": 0.2,
            "max_drawdown": -0.1,
            "sharpe": 1.0,
            "annual_one_way_turnover": 2.0,
        }

        invalid_performance = [
            {},
            "not-a-mapping",
            {"total_return": 0.2},
            {**valid, "sharpe": np.inf},
            {**valid, "max_drawdown": np.nan},
        ]
        for performance in invalid_performance:
            with self.subTest(performance=performance):
                with self.assertRaisesRegex(ValueError, "performance"):
                    render_symbol_card(**arguments, performance=performance)

    def test_metadata_draws_performance_before_trade_statistics(self) -> None:
        class RecordingDraw:
            def __init__(self) -> None:
                self.texts: list[str] = []

            def rounded_rectangle(self, *_args: object, **_kwargs: object) -> None:
                return None

            def text(self, _position: object, text: object, **_kwargs: object) -> None:
                self.texts.append(str(text))

        trades = self._card_arguments()["trades"]
        self.assertIsInstance(trades, pd.DataFrame)
        draw = RecordingDraw()
        performance = {
            "total_return": 0.2345,
            "max_drawdown": -0.1234,
            "sharpe": 1.25,
            "annual_one_way_turnover": 3.5,
            "target_distribution": {
                "0": {"days": 12, "fraction": 0.2},
                "0.5": {"days": 18, "fraction": 0.3},
                "1": {"days": 30, "fraction": 0.5},
            },
        }

        _draw_metadata(
            draw,
            _Fonts(),
            "159659.SZ",
            "测试ETF",
            "股票型ETF",
            trades,
            None,
            1120,
            performance,
        )

        performance_index = draw.texts.index("Performance")
        for heading in ("Trades", "Cost / PnL", "Latest Position"):
            with self.subTest(heading=heading):
                self.assertLess(performance_index, draw.texts.index(heading))
        self.assertIn("target 0%: 12 days (20.0%)", draw.texts)
        self.assertIn("target 50%: 18 days (30.0%)", draw.texts)
        self.assertIn("target 100%: 30 days (50.0%)", draw.texts)

    def test_chart_panel_uses_draw_score_tracks_keyword(self) -> None:
        parameters = inspect.signature(_draw_chart_panel).parameters

        self.assertIn("draw_score_tracks", parameters)

    def test_render_symbol_card_enables_score_tracks_only_for_daily_panel(
        self,
    ) -> None:
        arguments = self._card_arguments()
        dates = pd.bdate_range("2026-03-02", periods=60)
        weekly = aggregate_weekly_bars(self._bars(dates))
        daily_states = self._state_scores(dates)
        weekly_states = self._state_scores(pd.DatetimeIndex(weekly["datetime"]))

        with patch("stock.etf.render_trade_charts._draw_chart_panel") as draw_panel:
            render_symbol_card(
                **arguments,
                daily_state_scores=daily_states,
                weekly_state_scores=weekly_states,
            )

        self.assertEqual(draw_panel.call_count, 2)
        calls_by_period = {
            str(panel_call.args[2]).split()[0]: panel_call.kwargs
            for panel_call in draw_panel.call_args_list
        }
        self.assertFalse(calls_by_period["Weekly"].get("draw_score_tracks", False))
        self.assertTrue(calls_by_period["Daily"]["draw_score_tracks"])
        self.assertEqual(
            sum(
                bool(panel_call.kwargs.get("draw_score_tracks", False))
                for panel_call in draw_panel.call_args_list
            ),
            1,
        )

    def test_render_symbol_card_rejects_non_dataframe_state_scores(self) -> None:
        for parameter in ("daily_state_scores", "weekly_state_scores"):
            with self.subTest(parameter=parameter):
                arguments = self._card_arguments()
                arguments[parameter] = ["not", "a", "dataframe"]

                with self.assertRaisesRegex(ValueError, parameter):
                    render_symbol_card(**arguments)

    def test_empty_bars_do_not_skip_state_structure_validation(self) -> None:
        empty_bars = pd.DataFrame(
            columns=["datetime", "open", "high", "low", "close", "volume"]
        )
        incomplete_states = pd.DataFrame(
            {
                "datetime": [pd.Timestamp("2026-03-02")],
                "state_3d": ["趋势向上"],
            }
        )

        for bars_parameter, states_parameter in (
            ("daily_bars", "daily_state_scores"),
            ("weekly_bars", "weekly_state_scores"),
        ):
            with self.subTest(states_parameter=states_parameter):
                arguments = self._card_arguments()
                arguments[bars_parameter] = empty_bars
                arguments[states_parameter] = incomplete_states

                with self.assertRaisesRegex(ValueError, "state_scores missing columns"):
                    render_symbol_card(**arguments)

    def test_render_symbol_card_rejects_invalid_state_dates(self) -> None:
        dates = pd.bdate_range("2026-03-02", periods=60)
        invalid_date_frames: list[tuple[str, pd.DataFrame]] = []
        for label, invalid_date in (
            ("unparseable", "not-a-date"),
            ("nat", pd.NaT),
        ):
            states = self._state_scores(dates)
            states["datetime"] = states["datetime"].astype(object)
            states.at[0, "datetime"] = invalid_date
            invalid_date_frames.append((label, states))
        timezone_states = self._state_scores(dates)
        timezone_states["datetime"] = pd.DatetimeIndex(
            timezone_states["datetime"]
        ).tz_localize("UTC")
        invalid_date_frames.append(("timezone", timezone_states))

        for label, states in invalid_date_frames:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "datetime"):
                    render_symbol_card(
                        **self._card_arguments(),
                        daily_state_scores=states,
                    )

    def test_render_symbol_card_rejects_empty_state_labels(self) -> None:
        dates = pd.bdate_range("2026-03-02", periods=60)

        for column in ("state_3d", "state_color"):
            for value in (None, "   "):
                with self.subTest(column=column, value=value):
                    states = self._state_scores(dates)
                    states[column] = states[column].astype(object)
                    states.at[0, column] = value

                    with self.assertRaisesRegex(ValueError, column):
                        render_symbol_card(
                            **self._card_arguments(),
                            daily_state_scores=states,
                        )

    def test_render_symbol_card_rejects_invalid_score_tracks(self) -> None:
        dates = pd.bdate_range("2026-03-02", periods=60)

        for column in ("score_1d", "score_3d"):
            for value in ("bad-score", 99.0, np.nan, np.inf, -np.inf):
                with self.subTest(column=column, value=value):
                    states = self._state_scores(dates)
                    states[column] = states[column].astype(object)
                    states.at[0, column] = value

                    with self.assertRaisesRegex(ValueError, column):
                        render_symbol_card(
                            **self._card_arguments(),
                            daily_state_scores=states,
                        )

    def test_batch_render_records_symbol_with_missing_daily_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dates = pd.bdate_range("2026-03-02", periods=60)
            daily = self._bars(dates).assign(symbol="A.SH")
            metadata = pd.DataFrame(
                {
                    "symbol": ["A.SH", "B.SH"],
                    "name": ["测试ETF甲", np.nan],
                    "fund_type": ["股票型ETF", "股票型ETF"],
                }
            )
            trades = pd.DataFrame(
                {
                    "datetime": [dates[35], dates[36]],
                    "symbol": ["A.SH", "B.SH"],
                    "side": ["buy", "buy"],
                    "fill_price": [15.0, 10.0],
                    "quantity": [1000, 1000],
                    "commission": [5.0, 5.0],
                    "slippage_cost": [2.0, 2.0],
                    "realized_pnl": [0.0, 0.0],
                    "primary_reason": ["rs_top5", "rs_top5"],
                }
            )
            positions = pd.DataFrame(
                {
                    "datetime": [dates[30]],
                    "symbol": ["A.SH"],
                    "quantity": [1000],
                    "average_price": [14.0],
                }
            )
            daily_path = root / "daily.csv"
            metadata_path = root / "metadata.csv"
            trades_path = root / "trades.csv"
            positions_path = root / "positions.csv"
            output_dir = root / "charts"
            daily.to_csv(daily_path, index=False)
            metadata.to_csv(metadata_path, index=False)
            trades.to_csv(trades_path, index=False)
            positions.to_csv(positions_path, index=False)

            summary = render_all_trade_charts(
                daily_csv=daily_path,
                metadata_csv=metadata_path,
                trades_csv=trades_path,
                positions_csv=positions_path,
                output_dir=output_dir,
                report_start=pd.Timestamp("2026-05-17"),
                report_end=pd.Timestamp("2026-07-17"),
                overwrite=True,
            )

            index = pd.read_csv(output_dir / "index.csv")
            self.assertEqual(len(list(output_dir.glob("*.png"))), 1)
            self.assertEqual(len(index), 2)
            self.assertEqual(
                index.set_index("symbol").loc["B.SH", "render_status"],
                "missing_daily_data",
            )
            self.assertEqual(index.set_index("symbol").loc["B.SH", "name"], "B.SH")
            self.assertFalse(bool(index.set_index("symbol").loc["A.SH", "is_open"]))
            self.assertEqual(summary["rendered_images"], 1)
            self.assertEqual(summary["missing_daily_data"], 1)
            self.assertEqual(summary["missing_names"], 1)

    def test_batch_render_writes_header_only_index_without_trades(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dates = pd.bdate_range("2026-03-02", periods=60)
            daily = self._bars(dates).assign(symbol="A.SH")
            metadata = pd.DataFrame(
                {
                    "symbol": ["A.SH"],
                    "name": ["测试ETF甲"],
                    "fund_type": ["股票型ETF"],
                }
            )
            trades = pd.DataFrame(
                columns=[
                    "datetime",
                    "symbol",
                    "side",
                    "fill_price",
                    "quantity",
                    "commission",
                    "slippage_cost",
                    "realized_pnl",
                    "primary_reason",
                ]
            )
            positions = pd.DataFrame(
                columns=["datetime", "symbol", "quantity", "average_price"]
            )
            daily_path = root / "daily.csv"
            metadata_path = root / "metadata.csv"
            trades_path = root / "trades.csv"
            positions_path = root / "positions.csv"
            output_dir = root / "charts"
            daily.to_csv(daily_path, index=False)
            metadata.to_csv(metadata_path, index=False)
            trades.to_csv(trades_path, index=False)
            positions.to_csv(positions_path, index=False)

            summary = render_all_trade_charts(
                daily_csv=daily_path,
                metadata_csv=metadata_path,
                trades_csv=trades_path,
                positions_csv=positions_path,
                output_dir=output_dir,
                report_start=pd.Timestamp("2026-05-17"),
                report_end=pd.Timestamp("2026-07-17"),
                overwrite=True,
            )

            index = pd.read_csv(output_dir / "index.csv")
            self.assertTrue(index.empty)
            self.assertIn("symbol", index.columns)
            self.assertIn("render_status", index.columns)
            self.assertEqual(summary["input_symbols"], 0)

    def test_batch_render_explicit_symbol_without_trades_creates_chart(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dates = pd.bdate_range("2026-03-02", periods=60)
            daily = self._bars(dates).assign(symbol="A.SH")
            metadata = pd.DataFrame(
                {
                    "symbol": ["A.SH"],
                    "name": ["测试ETF甲"],
                    "fund_type": ["股票型ETF"],
                }
            )
            trades = pd.DataFrame(
                columns=[
                    "datetime",
                    "symbol",
                    "side",
                    "fill_price",
                    "quantity",
                    "commission",
                    "slippage_cost",
                    "realized_pnl",
                    "primary_reason",
                ]
            )
            positions = pd.DataFrame(
                columns=["datetime", "symbol", "quantity", "average_price"]
            )
            daily_path = root / "daily.csv"
            metadata_path = root / "metadata.csv"
            trades_path = root / "trades.csv"
            positions_path = root / "positions.csv"
            output_dir = root / "charts"
            daily.to_csv(daily_path, index=False)
            metadata.to_csv(metadata_path, index=False)
            trades.to_csv(trades_path, index=False)
            positions.to_csv(positions_path, index=False)

            summary = render_all_trade_charts(
                daily_csv=daily_path,
                metadata_csv=metadata_path,
                trades_csv=trades_path,
                positions_csv=positions_path,
                output_dir=output_dir,
                report_start=pd.Timestamp("2026-05-17"),
                report_end=pd.Timestamp("2026-07-17"),
                symbols=["A.SH"],
                overwrite=True,
            )

            image_path = output_dir / "0001_A_SH_测试ETF甲.png"
            self.assertTrue(image_path.is_file())
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (1680, 1000))
            index = pd.read_csv(output_dir / "index.csv")
            self.assertEqual(index["symbol"].tolist(), ["A.SH"])
            self.assertEqual(index["trade_count"].tolist(), [0])
            self.assertEqual(summary["input_symbols"], 1)
            self.assertEqual(summary["input_trades"], 0)
            self.assertEqual(summary["rendered_images"], 1)

    def test_batch_render_normalizes_numeric_symbols_across_all_inputs(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dates = pd.bdate_range("2026-03-02", periods=60)
            daily = self._bars(dates).assign(symbol=123456)
            metadata = pd.DataFrame(
                {
                    "symbol": [123456],
                    "name": ["数字ETF"],
                    "fund_type": ["股票型ETF"],
                }
            )
            trades = pd.DataFrame(
                {
                    "datetime": [dates[35]],
                    "symbol": [123456],
                    "side": ["buy"],
                    "fill_price": [15.0],
                    "quantity": [1_000],
                    "commission": [5.0],
                    "slippage_cost": [2.0],
                    "realized_pnl": [0.0],
                    "primary_reason": ["target_weight_0_to_0.5"],
                }
            )
            positions = pd.DataFrame(
                {
                    "datetime": [dates[-1]],
                    "symbol": [123456],
                    "quantity": [1_000],
                    "average_price": [15.0],
                }
            )
            paths = {
                "daily_csv": root / "daily.csv",
                "metadata_csv": root / "metadata.csv",
                "trades_csv": root / "trades.csv",
                "positions_csv": root / "positions.csv",
            }
            daily.to_csv(paths["daily_csv"], index=False)
            metadata.to_csv(paths["metadata_csv"], index=False)
            trades.to_csv(paths["trades_csv"], index=False)
            positions.to_csv(paths["positions_csv"], index=False)
            output_dir = root / "charts"

            summary = render_all_trade_charts(
                **paths,
                output_dir=output_dir,
                report_start=dates[35],
                report_end=dates[-1],
                symbols=[123456],
                overwrite=True,
            )

            self.assertTrue((output_dir / "0001_123456_数字ETF.png").is_file())
            index = pd.read_csv(output_dir / "index.csv")
            self.assertEqual(index.loc[0, "name"], "数字ETF")
            self.assertTrue(bool(index.loc[0, "is_open"]))
            self.assertEqual(summary["missing_daily_data"], 0)
            self.assertEqual(summary["missing_names"], 0)

    @staticmethod
    def _card_arguments() -> dict[str, object]:
        dates = pd.bdate_range("2026-03-02", periods=60)
        daily = TradeChartTransformTests._bars(dates)
        return {
            "symbol": "159659.SZ",
            "name": "招商纳斯达克100ETF(QDII)",
            "fund_type": "股票型ETF",
            "daily_bars": daily,
            "weekly_bars": aggregate_weekly_bars(daily),
            "trades": pd.DataFrame(
                {
                    "datetime": [dates[30], dates[45]],
                    "side": ["buy", "sell"],
                    "fill_price": [15.0, 18.0],
                    "quantity": [1000, 1000],
                    "commission": [5.0, 5.0],
                    "slippage_cost": [2.0, 2.0],
                    "realized_pnl": [0.0, 2990.0],
                    "primary_reason": ["rs_top5", "rs_out_top10"],
                }
            ),
            "latest_position": None,
            "report_start": pd.Timestamp("2026-05-17"),
            "report_end": pd.Timestamp("2026-07-17"),
        }

    @staticmethod
    def _state_scores(dates: pd.DatetimeIndex) -> pd.DataFrame:
        split = len(dates) // 2
        score_1d = np.concatenate(
            [
                np.linspace(-2.8, -0.4, split),
                np.linspace(0.4, 2.8, len(dates) - split),
            ]
        )
        score_3d = np.concatenate(
            [
                np.linspace(-2.5, -1.0, split),
                np.linspace(1.0, 2.5, len(dates) - split),
            ]
        )
        return pd.DataFrame(
            {
                "datetime": dates,
                "score_1d": score_1d,
                "score_3d": score_3d,
                "state_3d": ["趋势向下"] * split + ["趋势向上"] * (len(dates) - split),
                "state_color": ["#f3c1bc"] * split + ["#9fd8b5"] * (len(dates) - split),
            }
        )

    @staticmethod
    def _bars(dates: pd.DatetimeIndex) -> pd.DataFrame:
        close = np.linspace(10.0, 20.0, len(dates))
        return pd.DataFrame(
            {
                "datetime": dates,
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000.0,
            }
        )


if __name__ == "__main__":
    unittest.main()
