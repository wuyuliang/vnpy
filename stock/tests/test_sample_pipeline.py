from pathlib import Path

import pandas as pd

from stock.run import sample_pipeline as pipeline_module
from stock.run.sample_pipeline import (
    build_arg_parser,
    calculate_two_year_limit_counts,
    filter_non_st_opportunities,
    filter_non_st_symbols,
    filter_opportunities_by_date_range,
    resolve_latest_trade_date_range,
    run_pipeline,
    write_breakout_pullback_daily_dirs,
    write_big_bull_daily_dirs,
    write_daily_opportunity_dirs,
    write_volume_spike_up_daily_dirs,
)
from stock.strategy.big_bull_mode import BIG_BULL_SIGNAL_TYPE, BigBullConfig
from stock.strategy.signal_evaluators import BullPullbackConfig


def _bull_frame() -> pd.DataFrame:
    close_values = [
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
        105.0,
        106.0,
        107.0,
        108.0,
        109.0,
        110.0,
        111.0,
        112.0,
        113.0,
        114.0,
        115.0,
        116.0,
        117.0,
        118.0,
        119.0,
        118.0,
        118.5,
    ]
    volumes = [1000.0] * 20 + [2500.0, 1100.0]
    return pd.DataFrame(
        {
            "symbol": ["600519.SH"] * len(close_values),
            "exchange": ["SSE"] * len(close_values),
            "interval": ["d"] * len(close_values),
            "datetime": pd.date_range("2024-01-02", periods=len(close_values), freq="B").strftime("%Y-%m-%d 00:00:00"),
            "open": [value - 0.2 for value in close_values],
            "high": [value + 0.8 for value in close_values],
            "low": [value - 0.8 for value in close_values],
            "close": close_values,
            "volume": volumes,
            "pct_chg": [0.5] * 10 + [10.0, -10.0] + [0.5] * 10,
            "open_interest": [0.0] * len(close_values),
            "turnover": [1.0] * len(close_values),
        }
    )


def _breakout_frame() -> pd.DataFrame:
    close_values = [110.0] * 6 + [100.0, 101.0, 102.0, 103.0, 104.0, 107.8, 106.0, 105.8, 107.5, 108.0]
    return pd.DataFrame(
        {
            "symbol": ["000001.SZ"] * len(close_values),
            "exchange": ["SZSE"] * len(close_values),
            "interval": ["d"] * len(close_values),
            "datetime": pd.date_range("2024-01-02", periods=len(close_values), freq="B").strftime("%Y-%m-%d 00:00:00"),
            "open": [value - 0.2 for value in close_values],
            "high": [240.0] * 6 + [101.0, 102.0, 103.0, 104.0, 105.0, 108.2, 106.7, 106.4, 107.8, 108.4],
            "low": [109.0] * 6 + [99.0, 100.0, 101.0, 102.0, 103.0, 105.4, 104.8, 105.0, 106.6, 107.2],
            "close": close_values,
            "volume": [1000.0] * len(close_values),
            "pct_chg": [0.5] * len(close_values),
            "open_interest": [0.0] * len(close_values),
            "turnover": [1.0] * len(close_values),
        }
    )


def _volume_spike_frame() -> pd.DataFrame:
    close_values = [240.0, 100.5, 101.0, 100.8, 101.2, 102.0, 102.6]
    return pd.DataFrame(
        {
            "symbol": ["300750.SZ"] * len(close_values),
            "exchange": ["SZSE"] * len(close_values),
            "interval": ["d"] * len(close_values),
            "datetime": pd.date_range("2024-01-02", periods=len(close_values), freq="B").strftime("%Y-%m-%d 00:00:00"),
            "open": [value - 0.2 for value in close_values],
            "high": [value + 0.8 for value in close_values],
            "low": [value - 0.8 for value in close_values],
            "close": close_values,
            "volume": [1000.0, 1100.0, 900.0, 1000.0, 1000.0, 2200.0, 1200.0],
            "pct_chg": [0.0, 0.5, 0.5, -0.2, 0.4, 0.8, 0.5],
            "open_interest": [0.0] * len(close_values),
            "turnover": [1.0] * len(close_values),
        }
    )


def _big_bull_frame() -> pd.DataFrame:
    closes = (
        [8.0 + index * 0.08 for index in range(55)]
        + [12.2 - index * 0.18 for index in range(8)]
        + [11.0 + index * 0.45 for index in range(15)]
    )
    volumes = [1000.0] * 63 + [2000.0 + index * 50.0 for index in range(15)]
    return pd.DataFrame(
        {
            "symbol": ["000001.SZ"] * len(closes),
            "exchange": ["SZSE"] * len(closes),
            "interval": ["d"] * len(closes),
            "datetime": pd.date_range("2024-01-02", periods=len(closes), freq="B").strftime(
                "%Y-%m-%d 00:00:00"
            ),
            "open": [value * 0.99 for value in closes],
            "high": [value * 1.03 for value in closes],
            "low": [value * 0.97 for value in closes],
            "close": closes,
            "volume": volumes,
            "pct_chg": pd.Series(closes).pct_change().fillna(0.0) * 100.0,
            "open_interest": [0.0] * len(closes),
            "turnover": [
                volume * close for volume, close in zip(volumes, closes, strict=True)
            ],
        }
    )


class _TradeDateDownloader:
    def __init__(self, dates: list[str]) -> None:
        self.dates = dates
        self.calls: list[tuple[str, str]] = []

    def list_open_trade_dates(self, start: str, end: str) -> list[str]:
        self.calls.append((start, end))
        return [date for date in self.dates if start <= date <= end]


def test_resolve_latest_trade_date_range_uses_recent_open_days() -> None:
    downloader = _TradeDateDownloader(["2024-01-02", "2024-01-03", "2024-01-05", "2024-01-08"])

    start, end = resolve_latest_trade_date_range(downloader, latest_days=2, today="2024-01-09")

    assert (start, end) == ("2024-01-05", "2024-01-08")


def test_arg_parser_accepts_latest_days_without_explicit_start_end() -> None:
    args = build_arg_parser().parse_args(["--universe", "all", "--latest-days", "3"])

    assert args.latest_days == 3
    assert args.start is None
    assert args.end is None


def test_arg_parser_accepts_volume_spike_signal_type_and_merge_existing() -> None:
    args = build_arg_parser().parse_args(
        ["--universe", "all", "--signal-type", "volume_spike_up", "--merge-existing"]
    )

    assert args.signal_type == "volume_spike_up"
    assert args.merge_existing is True


def test_arg_parser_accepts_big_bull_signal_type() -> None:
    args = build_arg_parser().parse_args(
        ["--universe", "all", "--signal-type", BIG_BULL_SIGNAL_TYPE, "--merge-existing"]
    )

    assert args.signal_type == BIG_BULL_SIGNAL_TYPE


def test_filter_opportunities_by_date_range_keeps_only_recent_opportunity_dates() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "opportunity_date": ["2024-01-02", "2024-01-05", "2024-01-09"],
            "signal_type": ["volume_spike_up"] * 3,
        }
    )

    result = filter_opportunities_by_date_range(frame, start="2024-01-03", end="2024-01-08")

    assert list(result["symbol"]) == ["000002.SZ"]


def test_non_st_filters_fail_closed_when_name_is_missing() -> None:
    symbols = [
        {"ts_code": "600519.SH", "exchange": "SSE", "name": "贵州茅台"},
        {"ts_code": "000001.SZ", "exchange": "SZSE", "name": ""},
        {"ts_code": "000002.SZ", "exchange": "SZSE", "name": "*ST测试"},
    ]
    opportunities = pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ", "000002.SZ"],
            "name": ["贵州茅台", "", "*ST测试"],
        }
    )

    assert [item["ts_code"] for item in filter_non_st_symbols(symbols)] == ["600519.SH"]
    assert list(filter_non_st_opportunities(opportunities)["symbol"]) == ["600519.SH"]


def test_non_st_opportunity_filter_fails_closed_without_name_column() -> None:
    opportunities = pd.DataFrame({"symbol": ["600519.SH", "000001.SZ"]})

    result = filter_non_st_opportunities(opportunities)

    assert result.empty
    assert list(result.columns) == ["symbol"]


def test_two_year_limit_counts_use_board_specific_threshold() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["300750.SZ"] * 4,
            "datetime": pd.date_range("2024-01-02", periods=4, freq="B"),
            "pct_chg": [10.0, -10.0, 20.0, -20.0],
        }
    )

    assert calculate_two_year_limit_counts(frame, "2024-12-31") == (1, 1)


def test_two_year_limit_counts_exclude_bars_after_backtest_end() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["300750.SZ"] * 4,
            "datetime": ["2024-06-03", "2024-06-04", "2025-01-02", "2025-01-03"],
            "pct_chg": [20.0, -20.0, 20.0, -20.0],
        }
    )

    assert calculate_two_year_limit_counts(frame, "2024-12-31") == (1, 1)


def test_run_pipeline_scans_local_data_and_renders_outputs(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "origin"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)

    _bull_frame().to_csv(day_dir / "600519_SH.csv", index=False)
    _breakout_frame().to_csv(day_dir / "000001_SZ.csv", index=False)
    _volume_spike_frame().to_csv(day_dir / "300750_SZ.csv", index=False)
    st_frame = _volume_spike_frame()
    st_frame["symbol"] = "000002.SZ"
    st_frame.to_csv(day_dir / "000002_SZ.csv", index=False)

    report_root = tmp_path / "report"
    analysis_root = tmp_path / "analysis"
    result = run_pipeline(
        start="2024-01-01",
        end="2024-01-31",
        skip_download=True,
        symbols=[
            {"ts_code": "600519.SH", "exchange": "SSE", "name": "贵州茅台"},
            {"ts_code": "000001.SZ", "exchange": "SZSE", "name": "平安银行"},
            {"ts_code": "300750.SZ", "exchange": "SZSE", "name": "宁德时代"},
            {"ts_code": "000002.SZ", "exchange": "SZSE", "name": "*ST测试"},
        ],
        data_root=data_root,
        report_root=report_root,
        analysis_root=analysis_root,
        volume_spike_daily_root=tmp_path / "report" / "opportunities_date_spike_up",
        breakout_daily_root=tmp_path / "report" / "opportunities_date_break_out",
        big_bull_daily_root=tmp_path / "report" / "opportunities_date_ma5_ma10_big",
        run_id="test_run",
        cfg=BullPullbackConfig(
            pullback_pct=0.08,
            breakout_window=5,
            breakout_pullback_lookback=3,
            breakout_pullback_pct=0.02,
            breakout_reclaim_pct=0.01,
            volume_spike_window=5,
        ),
        latest_daily_basic=pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ", "300750.SZ", "000002.SZ"],
                "total_mv": [1_234_567.0, 900_000.0, 800_000.0, 10_000.0],
                "circ_mv": [1_000_000.0, 700_000.0, 600_000.0, 8_000.0],
            }
        ),
    )

    assert result["opportunity_count"] >= 1
    assert Path(result["opportunity_csv"]).exists()
    opportunities = pd.read_csv(result["opportunity_csv"])
    assert {"name", "total_mv", "circ_mv", "limit_up_count_2y", "limit_down_count_2y", "close_price"}.issubset(opportunities.columns)
    maotai = opportunities[opportunities["symbol"] == "600519.SH"].iloc[0]
    assert maotai["name"] == "贵州茅台"
    assert maotai["total_mv"] == 1_234_567.0
    assert maotai["limit_up_count_2y"] == 1
    assert maotai["limit_down_count_2y"] == 1
    assert "exit_datetime" not in opportunities.columns
    assert "000002.SZ" not in set(opportunities["symbol"])
    assert not opportunities["name"].astype(str).str.contains("ST", case=False, regex=False).any()
    assert {"bull_pullback_continuation", "breakout_pullback_continuation", "volume_spike_up"}.issubset(set(opportunities["signal_type"]))
    assert "volume_5_avg" in opportunities.columns
    assert Path(result["analysis_dir"]).exists()
    assert (analysis_root / "test_run" / "index.csv").exists()
    daily_root = Path(result["daily_opportunity_dir"])
    date_dirs = sorted(path.name for path in daily_root.iterdir() if path.is_dir())
    assert date_dirs
    assert not list(daily_root.glob("**/*.csv"))
    assert any("breakout_pullback_continuation" in path.name for path in daily_root.glob("**/*.png"))
    assert any("volume_spike_up" in path.name for path in daily_root.glob("**/*.png"))
    spike_daily_root = Path(result["volume_spike_daily_opportunity_dir"])
    assert "opportunities_date_spike_up" in str(spike_daily_root)
    assert any("volume_spike_up" in path.name for path in spike_daily_root.glob("**/*.png"))
    breakout_daily_root = Path(result["breakout_daily_opportunity_dir"])
    assert "opportunities_date_break_out" in str(breakout_daily_root)
    breakout_png_names = [path.name for path in breakout_daily_root.glob("**/*.png")]
    assert breakout_png_names
    assert all("breakout_pullback_continuation" in name for name in breakout_png_names)
    assert "opportunities_date_ma5_ma10_big" in result["big_bull_daily_opportunity_dir"]


def test_run_pipeline_filters_start_end_opportunity_dates_without_latest_days(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "origin"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    _volume_spike_frame().to_csv(day_dir / "300750_SZ.csv", index=False)

    result = run_pipeline(
        start="2024-01-11",
        end="2024-01-31",
        skip_download=True,
        symbols=[{"ts_code": "300750.SZ", "exchange": "SZSE", "name": "宁德时代"}],
        data_root=data_root,
        report_root=tmp_path / "report" / "opportunities",
        daily_opportunity_root=tmp_path / "report" / "opportunity_date",
        volume_spike_daily_root=tmp_path / "report" / "opportunities_date_spike_up",
        breakout_daily_root=tmp_path / "report" / "opportunities_date_break_out",
        big_bull_daily_root=tmp_path / "report" / "opportunities_date_ma5_ma10_big",
        analysis_root=tmp_path / "analysis",
        run_id="test_date_filter",
        cfg=BullPullbackConfig(volume_spike_window=5, volume_spike_ratio=2.0),
        signal_type="volume_spike_up",
    )

    assert result["opportunity_count"] == 0
    assert pd.read_csv(result["opportunity_csv"]).empty


def test_run_pipeline_scans_and_scores_big_bull_signal(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "origin"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    _big_bull_frame().to_csv(day_dir / "000001_SZ.csv", index=False)

    result = run_pipeline(
        start="2024-01-01",
        end="2024-12-31",
        skip_download=True,
        symbols=[{"ts_code": "000001.SZ", "exchange": "SZSE", "name": "平安银行"}],
        data_root=data_root,
        report_root=tmp_path / "report" / "opportunities",
        daily_opportunity_root=tmp_path / "report" / "opportunity_date",
        volume_spike_daily_root=tmp_path / "report" / "opportunities_date_spike_up",
        breakout_daily_root=tmp_path / "report" / "opportunities_date_break_out",
        big_bull_daily_root=tmp_path / "report" / "opportunities_date_ma5_ma10_big",
        analysis_root=tmp_path / "analysis",
        run_id="test_big_bull",
        signal_type=BIG_BULL_SIGNAL_TYPE,
        big_bull_cfg=BigBullConfig(
            min_history_bars=60,
            min_close=1.0,
            min_turnover_20_avg=0.0,
            min_rs_60d_pct=0.0,
            min_score=0.0,
            min_limit_up_count_120d=0,
            min_big_volume_up_count_60d=0,
        ),
    )

    opportunities = pd.read_csv(result["opportunity_csv"])
    assert set(opportunities["signal_type"]) == {BIG_BULL_SIGNAL_TYPE}
    assert opportunities["big_bull_score"].notna().all()
    assert opportunities["rs_60d_pct"].eq(1.0).all()
    assert opportunities["candidate_reason"].str.contains("score=").all()
    big_bull_root = Path(result["big_bull_daily_opportunity_dir"])
    assert "opportunities_date_ma5_ma10_big" in str(big_bull_root)
    assert list(big_bull_root.glob("**/*ma5_ma10_big_bull*.png"))
    generic_png = next(Path(result["daily_opportunity_dir"]).glob("**/*.png"))
    dedicated_png = next(big_bull_root.glob("**/*.png"))
    assert generic_png.stat().st_ino == dedicated_png.stat().st_ino


def test_run_pipeline_ranks_big_bull_against_non_candidate_symbols(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "origin"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    candidate = _big_bull_frame()
    candidate.to_csv(day_dir / "000001_SZ.csv", index=False)
    stronger = candidate.copy()
    stronger["symbol"] = "000002.SZ"
    stronger["close"] = [8.0 + index * 0.20 for index in range(len(stronger))]
    stronger["open"] = stronger["close"] * 0.99
    stronger["high"] = stronger["close"] * 1.03
    stronger["low"] = stronger["close"] * 0.97
    stronger["pct_chg"] = stronger["close"].pct_change().fillna(0.0) * 100.0
    stronger.to_csv(day_dir / "000002_SZ.csv", index=False)

    result = run_pipeline(
        start="2024-01-01",
        end="2024-12-31",
        skip_download=True,
        symbols=[
            {"ts_code": "000001.SZ", "exchange": "SZSE", "name": "平安银行"},
            {"ts_code": "000002.SZ", "exchange": "SZSE", "name": "万科A"},
        ],
        data_root=data_root,
        report_root=tmp_path / "report" / "opportunities",
        daily_opportunity_root=tmp_path / "report" / "opportunity_date",
        volume_spike_daily_root=tmp_path / "report" / "opportunities_date_spike_up",
        breakout_daily_root=tmp_path / "report" / "opportunities_date_break_out",
        big_bull_daily_root=tmp_path / "report" / "opportunities_date_ma5_ma10_big",
        analysis_root=tmp_path / "analysis",
        run_id="test_market_rs",
        signal_type=BIG_BULL_SIGNAL_TYPE,
        big_bull_cfg=BigBullConfig(
            min_history_bars=60,
            min_close=1.0,
            min_turnover_20_avg=0.0,
            min_rs_60d_pct=0.70,
            min_score=0.0,
            min_limit_up_count_120d=0,
            min_big_volume_up_count_60d=0,
        ),
    )

    assert result["opportunity_count"] == 0


def test_write_daily_opportunity_dirs_writes_descending_date_png_dirs(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    day_bars = pd.DataFrame(
        {
            "symbol": ["600519.SH"] * 24,
            "exchange": ["SSE"] * 24,
            "interval": ["d"] * 24,
            "datetime": pd.date_range("2024-01-02", periods=24, freq="B").strftime("%Y-%m-%d 00:00:00"),
            "open": [100 + index for index in range(24)],
            "high": [101 + index for index in range(24)],
            "low": [99 + index for index in range(24)],
            "close": [100.5 + index for index in range(24)],
            "volume": [1000] * 24,
            "open_interest": [0.0] * 24,
            "turnover": [1.0] * 24,
        }
    )
    day_bars.to_csv(day_dir / "600519_SH.csv", index=False)
    merged = pd.DataFrame(
        [
            {
                "symbol": "600519.SH",
                "exchange": "SSE",
                "name": "贵州茅台",
                "signal_type": "breakout_pullback_continuation",
                "opportunity_date": "2024-02-02",
                "entry_datetime": "2024-02-01 00:00:00",
                "entry_price": 122.5,
                "total_mv": 1_234_567.0,
                "circ_mv": 1_000_000.0,
                "limit_up_count_2y": 1,
                "limit_down_count_2y": 0,
            },
            {
                "symbol": "600519.SH",
                "exchange": "SSE",
                "name": "贵州茅台",
                "signal_type": "bull_pullback_continuation",
                "opportunity_date": "2024-01-31",
                "entry_datetime": "2024-01-30 00:00:00",
                "entry_price": 120.5,
                "total_mv": 1_234_567.0,
                "circ_mv": 1_000_000.0,
                "limit_up_count_2y": 1,
                "limit_down_count_2y": 0,
            },
        ]
    )

    output_root = write_daily_opportunity_dirs(
        merged,
        tmp_path / "report" / "opportunity_date",
        "test_run",
        data_root=data_root,
    )

    assert [path.name for path in sorted(output_root.iterdir()) if path.is_dir()] == ["0001_2024-02-02", "0002_2024-01-31"]
    assert not list(output_root.glob("**/*.csv"))
    assert len(list((output_root / "0001_2024-02-02").glob("*breakout_pullback_continuation*.png"))) == 1
    assert (output_root / "index.md").read_text(encoding="utf-8").splitlines()[0] == "# Daily Opportunities"


def test_write_daily_dirs_uses_all_symbol_rows_through_shared_chart_end(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_render(
        rows: list[dict[str, object]],
        *,
        output_dir: Path,
        data_root: Path,
        render_cache: object = None,
        cache_rows_by_key: object = None,
        bars_end_date: str | None = None,
    ) -> dict[str, int]:
        calls.append(
            {
                "rows": rows,
                "bars_end_date": bars_end_date,
                "cache_rows_by_key": cache_rows_by_key,
                "render_cache": render_cache,
            }
        )
        return {"symbols": 1, "rendered_images": 1, "missing_bars": 0}

    monkeypatch.setattr(pipeline_module, "render_opportunity_rows_to_dir", fake_render)
    merged = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "signal_type": "ma5_ma10_big_bull",
                "opportunity_date": "2024-02-02",
            },
            {
                "symbol": "000001.SZ",
                "signal_type": "ma5_ma10_big_bull",
                "opportunity_date": "2024-01-31",
            },
        ]
    )

    write_daily_opportunity_dirs(
        merged,
        tmp_path / "report",
        "retrospective",
        data_root=tmp_path / "data",
        chart_end_date="2026-08-01",
    )

    assert [call["bars_end_date"] for call in calls] == ["2026-08-01", "2026-08-01"]
    assert [[row["opportunity_date"] for row in call["rows"]] for call in calls] == [
        ["2024-02-02"],
        ["2024-01-31"],
    ]
    for call in calls:
        rows_by_symbol = call["cache_rows_by_key"]
        assert isinstance(rows_by_symbol, dict)
        assert [row["opportunity_date"] for row in rows_by_symbol["000001.SZ"]] == [
            "2024-01-31",
            "2024-02-02",
        ]
    assert calls[0]["render_cache"] is calls[1]["render_cache"]


def test_write_volume_spike_up_daily_dirs_filters_other_signal_types(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    day_bars = _volume_spike_frame()
    day_bars.to_csv(day_dir / "300750_SZ.csv", index=False)
    merged = pd.DataFrame(
        [
            {
                "symbol": "300750.SZ",
                "exchange": "SZSE",
                "name": "宁德时代",
                "signal_type": "volume_spike_up",
                "opportunity_date": "2024-01-10",
                "entry_datetime": "2024-01-09 00:00:00",
                "entry_price": 102.0,
            },
            {
                "symbol": "300750.SZ",
                "exchange": "SZSE",
                "name": "宁德时代",
                "signal_type": "bull_pullback_continuation",
                "opportunity_date": "2024-01-10",
                "entry_datetime": "2024-01-09 00:00:00",
                "entry_price": 102.0,
            },
        ]
    )

    output_root = write_volume_spike_up_daily_dirs(
        merged,
        tmp_path / "report" / "opportunities_date_spike_up",
        "test_run",
        data_root=data_root,
    )

    png_names = [path.name for path in output_root.glob("**/*.png")]
    assert png_names
    assert all("volume_spike_up" in name for name in png_names)


def test_write_breakout_pullback_daily_dirs_filters_other_signal_types(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    day_bars = _breakout_frame()
    day_bars.to_csv(day_dir / "000001_SZ.csv", index=False)
    merged = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": "breakout_pullback_continuation",
                "opportunity_date": "2024-01-23",
                "entry_datetime": "2024-01-22 00:00:00",
                "entry_price": 107.5,
            },
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": "volume_spike_up",
                "opportunity_date": "2024-01-23",
                "entry_datetime": "2024-01-22 00:00:00",
                "entry_price": 107.5,
            },
        ]
    )

    output_root = write_breakout_pullback_daily_dirs(
        merged,
        tmp_path / "report" / "opportunities_date_break_out",
        "test_run",
        data_root=data_root,
    )

    png_names = [path.name for path in output_root.glob("**/*.png")]
    assert png_names
    assert all("breakout_pullback_continuation" in name for name in png_names)


def test_write_big_bull_daily_dirs_filters_other_signal_types(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    _big_bull_frame().to_csv(day_dir / "000001_SZ.csv", index=False)
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "signal_datetime": "2024-04-03 00:00:00",
                "opportunity_date": "2024-04-04",
                "entry_datetime": "2024-04-04 00:00:00",
                "entry_price": 12.7,
            },
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": "volume_spike_up",
                "signal_datetime": "2024-04-03 00:00:00",
                "opportunity_date": "2024-04-04",
                "entry_datetime": "2024-04-04 00:00:00",
                "entry_price": 12.7,
            },
        ]
    )

    output_root = write_big_bull_daily_dirs(
        rows,
        tmp_path / "report" / "opportunities_date_ma5_ma10_big",
        "run1",
        data_root=data_root,
    )

    png_names = [path.name for path in output_root.glob("**/*.png")]
    assert png_names
    assert all(BIG_BULL_SIGNAL_TYPE in name for name in png_names)
