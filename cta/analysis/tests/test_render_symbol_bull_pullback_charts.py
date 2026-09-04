from cta.analysis import render_symbol_bull_pullback_charts as charts


def test_filter_bull_pullback_rows_keeps_only_target_signal_type() -> None:
    rows = [
        {"signal_type": "bull_pullback_continuation", "symbol": "AL0"},
        {"signal_type": "breakout_pullback_continuation", "symbol": "CU0"},
    ]

    result = charts.filter_bull_pullback_rows(rows)

    assert result == [{"signal_type": "bull_pullback_continuation", "symbol": "AL0"}]


def test_half_year_key_splits_calendar_year() -> None:
    assert charts.half_year_key("2024-06-30 21:00:00") == "2024H1"
    assert charts.half_year_key("2024-07-01 09:00:00") == "2024H2"


def test_symbol_summary_uses_three_k_line_panels() -> None:
    assert hasattr(charts, "CHART_PANELS")
    assert [panel["key"] for panel in charts.CHART_PANELS] == ["weekly", "daily", "hourly"]


def test_build_trade_markers_distinguishes_long_and_short_actions() -> None:
    long_row = {
        "entry_fill_datetime": "2024-01-02 09:00:00",
        "entry_fill_price": "100.5",
        "side": "long",
        "entry_action": "buy",
        "final_exit_datetime": "2024-01-10 15:00:00",
        "final_exit_price": "120.5",
        "exit_action": "sell",
        "execution_status": "executed",
    }
    short_row = {
        "entry_fill_datetime": "2024-02-02 09:00:00",
        "entry_fill_price": "220.5",
        "side": "short",
        "entry_action": "short",
        "final_exit_datetime": "2024-02-10 15:00:00",
        "final_exit_price": "200.5",
        "exit_action": "cover",
        "execution_status": "executed",
    }

    markers = charts.build_trade_markers(long_row) + charts.build_trade_markers(short_row)

    assert [marker["kind"] for marker in markers] == [
        "long_buy",
        "long_sell",
        "short_sell",
        "short_buy",
    ]
    assert [marker["color"] for marker in markers] == ["#16a34a", "#dc2626", "#f97316", "#2563eb"]
    assert [marker["price"] for marker in markers] == [100.5, 120.5, 220.5, 200.5]


def test_symbol_expected_returns_sum_net_pnl_by_symbol() -> None:
    rows = [
        {"symbol": "AL0", "net_pnl": "12.5"},
        {"symbol": "CU0", "net_pnl": "20"},
        {"symbol": "AL0", "net_pnl": "-2.5"},
    ]

    assert hasattr(charts, "symbol_expected_returns")
    result = charts.symbol_expected_returns(rows)

    assert result == {"AL0": 10.0, "CU0": 20.0}


def test_trade_rows_expected_return_sums_current_group_only() -> None:
    rows = [
        {"symbol": "AL0", "half_year": "2024H1", "net_pnl": "12.5"},
        {"symbol": "AL0", "half_year": "2024H1", "net_pnl": "-2.5"},
    ]

    assert hasattr(charts, "trade_rows_expected_return")
    assert charts.trade_rows_expected_return(rows) == 10.0


def test_executed_marker_y_is_lifted_above_price_area() -> None:
    price_rect = (100, 80, 500, 320)
    price_y = charts._scale_price(100.0, 0.0, 200.0, price_rect[1], price_rect[3])

    assert hasattr(charts, "_marker_y")
    assert charts._marker_y(
        {"status": "blocked_ranker"},
        price=100.0,
        low=0.0,
        high=200.0,
        price_rect=price_rect,
    ) == price_y
    assert charts._marker_y(
        {"status": "executed"},
        price=100.0,
        low=0.0,
        high=200.0,
        price_rect=price_rect,
    ) < price_rect[1]
