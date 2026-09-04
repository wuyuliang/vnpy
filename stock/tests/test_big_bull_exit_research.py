from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from stock.analysis import render_symbol_bull_pullback_charts as stock_charts
from stock.backtest.big_bull_exit_research import (
    BigBullExitConfig,
    run_exit_research,
    simulate_big_bull_exit,
)


def _bars(symbol: str = "000001.SZ") -> pd.DataFrame:
    closes = [
        10,
        10.5,
        11,
        12,
        13,
        14,
        16,
        18,
        21,
        24,
        28,
        31,
        35,
        38,
        42,
        45,
        49,
        53,
        58,
        62,
        61,
        60,
        59,
        57,
        55,
        53,
        51,
        49,
        47,
        45,
    ]
    dates = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(closes),
            "exchange": ["SZSE"] * len(closes),
            "datetime": dates.strftime("%Y-%m-%d 00:00:00"),
            "open": [value * 0.99 for value in closes],
            "high": [value * 1.03 for value in closes],
            "low": [value * 0.97 for value in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        }
    )


def test_simulate_big_bull_exit_holds_through_main_rise() -> None:
    result = simulate_big_bull_exit(
        _bars(),
        entry_date="2024-01-03",
        cfg=BigBullExitConfig(initial_stop_pct=0.10, trend_profit_activate_pct=0.20),
    )

    assert result["entry_price"] == 10.5 * 0.99
    assert result["exit_price"] > result["entry_price"] * 3.0
    assert result["holding_days"] >= 20
    assert result["exit_rule"] in {"big_bull_ma20_break", "big_bull_chandelier"}
    assert result["capture_ratio"] >= 0.70


def test_simulate_big_bull_exit_uses_initial_stop_before_trend_mode() -> None:
    bars = _bars().iloc[:8].copy()
    bars.loc[3:, "close"] = [9.8, 9.4, 9.0, 8.8, 8.6]
    bars.loc[3:, "low"] = bars.loc[3:, "close"] * 0.97

    result = simulate_big_bull_exit(
        bars,
        entry_date="2024-01-03",
        cfg=BigBullExitConfig(initial_stop_pct=0.10, trend_profit_activate_pct=0.20),
    )

    assert result["exit_rule"] == "initial_stop"
    assert result["return_pct"] < 0


def test_simulate_big_bull_exit_falls_back_to_close_when_entry_open_is_missing() -> None:
    bars = _bars()
    bars.loc[1, "open"] = pd.NA

    result = simulate_big_bull_exit(bars, entry_date="2024-01-03")

    assert result["entry_price"] == bars.loc[1, "close"]


def test_simulate_big_bull_exit_requires_exact_entry_bar() -> None:
    bars = _bars().drop(index=1).reset_index(drop=True)

    result = simulate_big_bull_exit(bars, entry_date="2024-01-03")

    assert result["exit_rule"] == "missing_entry_bar"
    assert pd.isna(result["entry_price"])


def test_simulate_big_bull_exit_handles_invalid_entry_date() -> None:
    result = simulate_big_bull_exit(_bars(), entry_date="not-a-date")

    assert result["exit_rule"] == "invalid_entry_date"


def test_simulate_big_bull_exit_checks_stop_on_entry_day_without_using_entry_low() -> None:
    bars = _bars()
    bars.loc[1, "low"] = 5.0

    result = simulate_big_bull_exit(
        bars,
        entry_date="2024-01-03",
        cfg=BigBullExitConfig(initial_stop_pct=0.10),
    )

    expected_stop = bars.loc[0, "low"] * 0.97
    assert result["exit_rule"] == "initial_stop"
    assert result["exit_date"] == "2024-01-03"
    assert result["exit_price"] == expected_stop


def test_chandelier_remains_active_after_profit_falls_below_thirty_percent() -> None:
    closes = [10.0] * 10 + [10.5, 11.0, 12.0, 13.0, 14.0, 13.5, 13.0, 12.5]
    bars = pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-02", periods=len(closes), freq="B"),
            "open": [value * 0.99 for value in closes],
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        }
    )

    result = simulate_big_bull_exit(
        bars,
        entry_date="2024-01-15",
        cfg=BigBullExitConfig(chandelier_atr_multiple=3.0),
    )

    assert result["exit_rule"] == "big_bull_chandelier"
    assert result["return_pct"] < 0.30


def test_run_exit_research_writes_non_st_results_and_symbol_chart(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "origin"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    _bars().to_csv(day_dir / "000001_SZ.csv", index=False)

    opportunities = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": "ma5_ma10_big_bull",
                "signal_datetime": "2024-01-02 00:00:00",
                "opportunity_date": "2024-01-03",
                "entry_datetime": "2024-01-03 00:00:00",
                "entry_price": 10.5 * 0.99,
                "total_mv": 100_000.0,
                "circ_mv": 80_000.0,
                "limit_up_count_2y": 2,
                "limit_down_count_2y": 0,
            },
            {
                "symbol": "000002.SZ",
                "exchange": "SZSE",
                "name": "*ST测试",
                "signal_type": "ma5_ma10_big_bull",
                "signal_datetime": "2024-01-02 00:00:00",
                "opportunity_date": "2024-01-03",
            },
            {
                "symbol": "000003.SZ",
                "exchange": "SZSE",
                "name": "其他股票",
                "signal_type": "volume_spike_up",
                "signal_datetime": "2024-01-02 00:00:00",
                "opportunity_date": "2024-01-03",
            },
            {
                "symbol": "000004.SZ",
                "exchange": "SZSE",
                "signal_type": "ma5_ma10_big_bull",
                "signal_datetime": "2024-01-02 00:00:00",
                "opportunity_date": "2024-01-03",
            },
        ]
    )
    opportunity_csv = tmp_path / "opportunities.csv"
    opportunities.to_csv(opportunity_csv, index=False)

    result = run_exit_research(
        opportunity_csv=opportunity_csv,
        data_root=data_root,
        output_dir=tmp_path / "report" / "exit_research",
        analysis_root=tmp_path / "analysis",
        run_id="test_big_bull_exit",
    )

    trades_csv = Path(result["trades_csv"])
    summary_path = Path(result["summary_path"])
    trades = pd.read_csv(trades_csv)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert result["rows"] == 1
    assert set(trades["symbol"]) == {"000001.SZ"}
    assert {"entry_date", "exit_date", "exit_rule", "return_pct", "capture_ratio"}.issubset(
        trades.columns
    )
    assert summary["rows"] == 1
    assert summary["rendered_images"] == 1
    analysis_dir = Path(result["analysis_dir"])
    assert len(list((analysis_dir / "charts").glob("*.png"))) == 1


def test_run_exit_research_renders_untradeable_missing_entry_row(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "origin"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    _bars().drop(index=1).reset_index(drop=True).to_csv(
        day_dir / "000001_SZ.csv",
        index=False,
    )
    opportunity_csv = tmp_path / "opportunities.csv"
    pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": "ma5_ma10_big_bull",
                "signal_datetime": "2024-01-02 00:00:00",
                "opportunity_date": "2024-01-03",
                "entry_datetime": "2024-01-03 00:00:00",
                "entry_price": 10.40,
                "signal_price": 10.00,
            }
        ]
    ).to_csv(opportunity_csv, index=False)

    result = run_exit_research(
        opportunity_csv=opportunity_csv,
        data_root=data_root,
        output_dir=tmp_path / "report" / "exit_research",
        analysis_root=tmp_path / "analysis",
        run_id="missing_entry",
    )

    trades = pd.read_csv(result["trades_csv"])
    assert list(trades["exit_rule"]) == ["missing_entry_bar"]
    assert stock_charts._build_markers(  # noqa: SLF001
        trades.to_dict("records"),
        include_exit_markers=True,
    ) == []
    assert result["rendered_images"] == 1
    assert len(list((Path(result["analysis_dir"]) / "charts").glob("*.png"))) == 1


def test_run_exit_research_clips_charts_to_requested_end_date(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    data_root = tmp_path / "data" / "origin"
    day_dir = data_root / "day"
    day_dir.mkdir(parents=True)
    _bars().to_csv(day_dir / "000001_SZ.csv", index=False)
    opportunity_csv = tmp_path / "opportunities.csv"
    pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": "ma5_ma10_big_bull",
                "signal_datetime": "2024-01-02 00:00:00",
                "opportunity_date": "2024-01-03",
                "entry_datetime": "2024-01-03 00:00:00",
                "entry_price": 10.5 * 0.99,
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
    ) -> object:
        captured["last_bar"] = bars["datetime"].iloc[-1].strftime("%Y-%m-%d")
        captured["display_end_date"] = display_end_date
        return stock_charts.Image.new("RGB", (20, 20), "white")

    monkeypatch.setattr(stock_charts, "render_symbol_card", fake_render_symbol_card)

    result = run_exit_research(
        opportunity_csv=opportunity_csv,
        data_root=data_root,
        output_dir=tmp_path / "report" / "exit_research",
        analysis_root=tmp_path / "analysis",
        run_id="chart_end",
        chart_end_date="2024-01-10",
    )

    assert captured == {
        "last_bar": "2024-01-10",
        "display_end_date": "2024-01-10",
    }
    render_summary = json.loads(
        (Path(result["analysis_dir"]) / "render_summary.json").read_text(encoding="utf-8")
    )
    assert render_summary["chart_end_date"] == "2024-01-10"
