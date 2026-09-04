import pandas as pd

from cta.analysis.render_trade_opportunity_charts import (
    aggregate_weekly_bars,
    load_symbol_bars,
    make_chart_filename,
    sort_trade_rows,
)


def test_sort_trade_rows_orders_by_net_pnl_descending() -> None:
    rows = [
        {
            "source_row": 2,
            "entry_datetime": "2024-01-03 00:00:00",
            "net_pnl": "10",
            "trade_return_pct": "0.1",
        },
        {
            "source_row": 1,
            "entry_datetime": "2024-01-02 00:00:00",
            "net_pnl": "20",
            "trade_return_pct": "0.2",
        },
        {
            "source_row": 3,
            "entry_datetime": "2024-01-01 00:00:00",
            "net_pnl": "",
            "trade_return_pct": "0.3",
        },
    ]

    result = sort_trade_rows(rows)

    assert [row["source_row"] for row in result] == [1, 2, 3]
    assert [row["sort_rank"] for row in result] == [1, 2, 3]


def test_make_chart_filename_sanitizes_key_fields() -> None:
    row = {
        "sort_rank": 7,
        "symbol": "AL0",
        "entry_datetime": "2024-01-02 21:00:00",
        "side": "long",
        "execution_status": "blocked/final decision",
    }

    filename = make_chart_filename(row)

    assert filename == "000007_AL0_20240102_210000_long_blocked_final_decision.png"


def test_aggregate_weekly_bars_uses_ohlcv_rules() -> None:
    daily = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-08"]),
            "open": [10.0, 11.0, 20.0],
            "high": [12.0, 15.0, 21.0],
            "low": [9.0, 10.0, 19.0],
            "close": [11.0, 14.0, 20.5],
            "volume": [100.0, 150.0, 200.0],
        }
    )

    weekly = aggregate_weekly_bars(daily)

    assert len(weekly) == 2
    assert weekly.iloc[0]["open"] == 10.0
    assert weekly.iloc[0]["high"] == 15.0
    assert weekly.iloc[0]["low"] == 9.0
    assert weekly.iloc[0]["close"] == 14.0
    assert weekly.iloc[0]["volume"] == 250.0
    assert weekly.iloc[1]["open"] == 20.0


def test_load_symbol_bars_can_read_all_symbols_parquet(tmp_path) -> None:
    data_root = tmp_path / "feature"
    interval_dir = data_root / "day"
    interval_dir.mkdir(parents=True)
    all_symbols = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2024-01-02", "2024-01-02"]),
            "open": [10.0, 20.0],
            "high": [11.0, 21.0],
            "low": [9.0, 19.0],
            "close": [10.5, 20.5],
            "volume": [100.0, 200.0],
            "symbol": ["AL0", "CU0"],
        }
    )
    all_symbols.to_parquet(interval_dir / "_all_symbols.parquet")
    load_symbol_bars.cache_clear()

    result = load_symbol_bars(data_root, "day", "AL0")

    assert len(result) == 1
    assert result.iloc[0]["symbol"] == "AL0"
    assert result.iloc[0]["close"] == 10.5
