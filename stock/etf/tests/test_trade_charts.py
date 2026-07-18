import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from stock.etf.render_trade_charts import (
    _assign_marker_label_levels,
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

    def test_filename_keeps_chinese_and_sanitizes_punctuation(self) -> None:
        filename = make_chart_filename(1, "159659.SZ", "招商纳斯达克100ETF(QDII)")

        self.assertEqual(filename, "0001_159659_SZ_招商纳斯达克100ETF_QDII.png")

    def test_render_symbol_card_creates_expected_png(self) -> None:
        dates = pd.bdate_range("2026-03-02", periods=60)
        daily = self._bars(dates)
        weekly = aggregate_weekly_bars(daily)
        trades = pd.DataFrame(
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
        )

        image = render_symbol_card(
            symbol="159659.SZ",
            name="招商纳斯达克100ETF(QDII)",
            fund_type="股票型ETF",
            daily_bars=daily,
            weekly_bars=weekly,
            trades=trades,
            latest_position=None,
            report_start=pd.Timestamp("2026-05-17"),
            report_end=pd.Timestamp("2026-07-17"),
        )

        self.assertEqual(image.size, (1680, 1000))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "chart.png"
            image.save(path)
            self.assertGreater(path.stat().st_size, 10_000)

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
