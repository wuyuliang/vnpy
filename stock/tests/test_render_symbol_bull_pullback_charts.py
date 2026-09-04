from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw

from stock.analysis import render_symbol_bull_pullback_charts as charts


def test_add_ema_columns_calculates_expected_columns() -> None:
    bars = pd.DataFrame({"close": [100.0, 101.0, 102.0, 103.0, 104.0]})

    result = charts.add_ema_columns(bars)

    assert {"ema5", "ema10", "ema20"}.issubset(result.columns)
    assert result["ema5"].notna().all()


def test_clip_bars_at_date_removes_future_bars() -> None:
    bars = pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-02", periods=5, freq="B"),
            "close": [10.0, 10.1, 10.2, 10.3, 10.4],
        }
    )

    clipped = charts.clip_bars_at_date(bars, "2024-01-04")

    assert list(clipped["datetime"].dt.strftime("%Y-%m-%d")) == [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
    ]


def test_build_bimonthly_ticks_uses_first_trading_bar_every_two_months() -> None:
    bars = pd.DataFrame(
        {"datetime": pd.date_range("2024-01-02", "2024-07-31", freq="B")}
    )

    ticks = charts._build_bimonthly_ticks(bars)

    assert [label for _, label in ticks] == [
        "2024-01",
        "2024-03",
        "2024-05",
        "2024-07",
    ]
    assert [bars.iloc[index]["datetime"].month for index, _ in ticks] == [1, 3, 5, 7]


def test_draw_vertical_dashed_line_has_colored_segments_and_gaps() -> None:
    image = Image.new("RGB", (20, 70), "white")
    draw = ImageDraw.Draw(image)

    charts._draw_vertical_dashed_line(
        draw,
        x=10,
        top=5,
        bottom=64,
        fill="#16a34a",
        width=1,
        dash_length=6,
        gap_length=4,
    )

    pixels = [image.getpixel((10, y)) for y in range(5, 65)]
    assert (22, 163, 74) in pixels
    assert (255, 255, 255) in pixels


def test_render_symbol_card_draws_dashed_buy_line_and_up_arrow() -> None:
    dates = pd.date_range("2024-01-02", periods=24, freq="B")
    closes = [10.0 + index * 0.1 for index in range(len(dates))]
    bars = charts._normalize_bars(
        pd.DataFrame(
            {
                "datetime": dates,
                "open": [value - 0.05 for value in closes],
                "high": [value + 0.20 for value in closes],
                "low": [value - 0.20 for value in closes],
                "close": closes,
                "volume": [1000.0] * len(dates),
            }
        )
    )
    marker_index = 10

    image = charts.render_symbol_card(
        "000001.SZ",
        [
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": "ma5_ma10_big_bull",
                "entry_datetime": dates[marker_index],
                "entry_price": closes[marker_index],
            }
        ],
        bars,
        display_end_date="2026-08-01",
    )

    x = charts._bar_x_positions(len(bars), (54, 142, 2146, 848))[marker_index]
    marker_pixels = [image.getpixel((x, y)) for y in range(142, 849)]
    colored = sum(pixel == (22, 163, 74) for pixel in marker_pixels)
    assert colored > 100
    assert colored < len(marker_pixels)


def test_build_markers_ignores_exit_fields_and_renders_buy_only() -> None:
    rows = [
        {
            "signal_type": "breakout_pullback_continuation",
            "entry_datetime": "2024-01-10 00:00:00",
            "entry_price": 105.4,
            "exit_datetime": "2024-01-15 00:00:00",
            "exit_price": 108.2,
        }
    ]

    markers = charts._build_markers(rows)

    assert markers == [
        {
            "datetime": pd.Timestamp("2024-01-10 00:00:00"),
            "price": 105.4,
            "kind": "buy",
            "signal_type": "breakout_pullback_continuation",
        }
    ]


def test_build_markers_can_include_sell_for_exit_research() -> None:
    rows = [
        {
            "signal_type": "ma5_ma10_big_bull",
            "entry_datetime": "2024-01-10 00:00:00",
            "entry_price": 105.4,
            "exit_date": "2024-01-15",
            "exit_price": 108.2,
        }
    ]

    markers = charts._build_markers(rows, include_exit_markers=True)

    assert [marker["kind"] for marker in markers] == ["buy", "sell"]
    assert markers[1]["datetime"] == pd.Timestamp("2024-01-15")
    assert markers[1]["price"] == 108.2

def test_render_all_outputs_one_symbol_png_index_and_summary(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)

    day_bars = pd.DataFrame(
        {
            "symbol": ["600519.SH"] * 12,
            "exchange": ["SSE"] * 12,
            "interval": ["d"] * 12,
            "datetime": pd.date_range("2024-01-02", periods=12, freq="B").strftime("%Y-%m-%d 00:00:00"),
            "open": [100, 101, 102, 103, 104, 103, 105, 106, 107, 108, 109, 110],
            "high": [101, 102, 103, 104, 105, 104, 106, 107, 108, 109, 110, 111],
            "low": [99, 100, 101, 102, 103, 102, 104, 105, 106, 107, 108, 109],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5, 103.8, 105.4, 106.2, 107.1, 108.2, 109.1, 110.2],
            "volume": [1000] * 12,
            "open_interest": [0.0] * 12,
            "turnover": [1.0] * 12,
        }
    )
    day_bars.to_csv(day_dir / "600519_SH.csv", index=False)

    opportunities = pd.DataFrame(
        [
            {
                "symbol": "600519.SH",
                "exchange": "SSE",
                "name": "贵州茅台",
                "signal_type": "bull_pullback_continuation",
                "entry_datetime": "2024-01-10 00:00:00",
                "entry_price": 105.4,
                "total_mv": 1234567.0,
                "circ_mv": 1000000.0,
                "limit_up_count_2y": 1,
                "limit_down_count_2y": 0,
                "trigger": "bull_pullback_long",
            },
            {
                "symbol": "600519.SH",
                "exchange": "SSE",
                "name": "贵州茅台",
                "signal_type": "breakout_pullback_continuation",
                "entry_datetime": "2024-01-11 00:00:00",
                "entry_price": 106.2,
                "total_mv": 1234567.0,
                "circ_mv": 1000000.0,
                "limit_up_count_2y": 1,
                "limit_down_count_2y": 0,
                "trigger": "bull_pullback_long",
            },
            {
                "symbol": "600519.SH",
                "exchange": "SSE",
                "name": "贵州茅台",
                "signal_type": "volume_spike_up",
                "entry_datetime": "2024-01-12 00:00:00",
                "entry_price": 107.1,
                "total_mv": 1234567.0,
                "circ_mv": 1000000.0,
                "limit_up_count_2y": 1,
                "limit_down_count_2y": 0,
                "trigger": "volume_spike_up_long",
            },
            {
                "symbol": "600519.SH",
                "exchange": "SSE",
                "name": "贵州茅台",
                "signal_type": "ma5_ma10_big_bull",
                "entry_datetime": "2024-01-15 00:00:00",
                "entry_price": 108.2,
                "total_mv": 1_234_567.0,
                "circ_mv": 1_000_000.0,
                "limit_up_count_2y": 1,
                "limit_down_count_2y": 0,
                "trigger": "ma5_cross_ma10_big_bull_long",
            },
        ]
    )
    opportunity_csv = tmp_path / "opportunities.csv"
    opportunities.to_csv(opportunity_csv, index=False)

    output_dir = tmp_path / "analysis"
    summary = charts.render_all(
        opportunity_csv=opportunity_csv,
        output_dir=output_dir,
        data_root=data_root,
    )

    assert summary["rendered_images"] == 1
    assert summary["symbols"] == 1
    assert summary["input_rows"] == 4
    assert (output_dir / "index.csv").exists()
    assert (output_dir / "render_summary.json").exists()
    assert len(list((output_dir / "charts").glob("*.png"))) == 1
    assert "bull_pullback_continuation" in (output_dir / "index.csv").read_text()
    assert "breakout_pullback_continuation" in (output_dir / "index.csv").read_text()
    assert "volume_spike_up" in (output_dir / "index.csv").read_text()
    assert "ma5_ma10_big_bull" in (output_dir / "index.csv").read_text()


def test_render_all_clips_bars_to_requested_end_date(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    data_root = tmp_path / "data"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    pd.DataFrame(
        {
            "datetime": dates,
            "open": range(10, 20),
            "high": range(11, 21),
            "low": range(9, 19),
            "close": [value + 0.5 for value in range(10, 20)],
            "volume": [1000] * 10,
        }
    ).to_csv(day_dir / "000001_SZ.csv", index=False)
    opportunity_csv = tmp_path / "opportunities.csv"
    pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "signal_type": "ma5_ma10_big_bull",
                "entry_datetime": "2024-01-03",
                "entry_price": 11.5,
            }
        ]
    ).to_csv(opportunity_csv, index=False)
    captured: dict[str, object] = {}

    def fake_render_symbol_card(
        symbol: str,
        rows: list[dict[str, object]],
        bars: pd.DataFrame,
        *,
        include_exit_markers: bool = False,
        display_end_date: str | None = None,
    ) -> Image.Image:
        captured["last_bar"] = bars["datetime"].iloc[-1].strftime("%Y-%m-%d")
        captured["display_end_date"] = display_end_date
        return Image.new("RGB", (20, 20), "white")

    monkeypatch.setattr(charts, "render_symbol_card", fake_render_symbol_card)

    summary = charts.render_all(
        opportunity_csv=opportunity_csv,
        output_dir=tmp_path / "analysis",
        data_root=data_root,
        bars_end_date="2024-01-08",
    )

    assert captured == {
        "last_bar": "2024-01-08",
        "display_end_date": "2024-01-08",
    }
    assert summary["bars_end_date"] == "2024-01-08"


def test_daily_renderer_groups_by_symbol_and_uses_all_symbol_rows(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    data_root = tmp_path / "data"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    pd.DataFrame(
        {
            "datetime": dates,
            "open": range(10, 20),
            "high": range(11, 21),
            "low": range(9, 19),
            "close": [value + 0.5 for value in range(10, 20)],
            "volume": [1000] * 10,
        }
    ).to_csv(day_dir / "000001_SZ.csv", index=False)
    current_rows = [
        {
            "symbol": "000001.SZ",
            "signal_type": "ma5_ma10_big_bull",
            "entry_datetime": "2024-01-08",
            "entry_price": 14.5,
        },
        {
            "symbol": "000001.SZ",
            "signal_type": "volume_spike_up",
            "entry_datetime": "2024-01-08",
            "entry_price": 14.5,
        },
    ]
    all_rows = current_rows + [
        {
            "symbol": "000001.SZ",
            "signal_type": "ma5_ma10_big_bull",
            "entry_datetime": "2024-01-03",
            "entry_price": 11.5,
        }
    ]
    captured: dict[str, object] = {}

    def fake_render_symbol_card(
        symbol: str,
        rows: list[dict[str, object]],
        bars: pd.DataFrame,
        *,
        include_exit_markers: bool = False,
        display_end_date: str | None = None,
    ) -> Image.Image:
        captured["symbol"] = symbol
        captured["row_count"] = len(rows)
        captured["display_end_date"] = display_end_date
        return Image.new("RGB", (20, 20), "white")

    monkeypatch.setattr(charts, "render_symbol_card", fake_render_symbol_card)

    summary = charts.render_opportunity_rows_to_dir(
        current_rows,
        output_dir=tmp_path / "daily",
        data_root=data_root,
        cache_rows_by_key={"000001.SZ": all_rows},
        bars_end_date="2024-01-12",
    )

    assert summary["symbols"] == 1
    assert summary["rendered_images"] == 1
    assert captured == {
        "symbol": "000001.SZ",
        "row_count": 3,
        "display_end_date": "2024-01-12",
    }
    assert len(list((tmp_path / "daily").glob("*.png"))) == 1


def test_big_bull_signal_has_dedicated_chart_label_and_color() -> None:
    assert charts.SIGNAL_TYPE_LABELS["ma5_ma10_big_bull"] == "MA5/MA10 Big Bull"
    assert charts.SIGNAL_TYPE_COLORS["ma5_ma10_big_bull"] == "#16a34a"
