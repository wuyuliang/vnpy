"""Presentation rules for the opportunity charts and trades.csv."""
from __future__ import annotations

import pandas as pd
import pytest

from cta.config.futures_display_names import chinese_name_for_root, labelled_symbol
from cta.strategy.multi_timeframe_trend_backtest import charts as C
from cta.strategy.multi_timeframe_trend_backtest.report import (
    _decimals_for,
    _formatted_trades,
    _with_symbol_name,
)


# --------------------------------------------------------------------------
# 品种中文名
# --------------------------------------------------------------------------
def test_chinese_name_drops_the_continuous_suffix() -> None:
    assert chinese_name_for_root("RB0") == "螺纹钢"
    assert chinese_name_for_root("AG2604.SHF") == "白银"


def test_unknown_root_keeps_the_plain_symbol() -> None:
    assert chinese_name_for_root("ZZZ") == ""
    assert labelled_symbol("ZZZ") == "ZZZ"


# --------------------------------------------------------------------------
# 图表文案
# --------------------------------------------------------------------------
def test_setup_label_is_named_in_chinese(monkeypatch) -> None:
    monkeypatch.setattr(C, "_cjk_font_path", lambda: "/fake/font.ttc")
    assert C._setup_label("always_in", 1) == "趋势回调"
    assert C._setup_label("ALWAYS_IN", -1) == "趋势反弹"


def test_unknown_setup_keeps_the_raw_code(monkeypatch) -> None:
    monkeypatch.setattr(C, "_cjk_font_path", lambda: "/fake/font.ttc")
    assert C._setup_label("breakout", 1) == "breakout LONG"


def test_setup_label_falls_back_without_a_cjk_font(monkeypatch) -> None:
    """没有中文字体时画中文只会出一排豆腐块，不如退回英文。"""
    monkeypatch.setattr(C, "_cjk_font_path", lambda: "")
    assert C._setup_label("always_in", 1) == "always_in LONG"


def test_header_no_longer_carries_outcome_or_the_fourth_line() -> None:
    source = C._render_card.__code__.co_consts
    flat = " ".join(str(item) for item in source)
    assert "outcome=" not in flat
    assert "post-event review" not in flat


# --------------------------------------------------------------------------
# 面板里的时刻定位
# --------------------------------------------------------------------------
def _window(freq: str, periods: int, radius: int) -> pd.DataFrame:
    index = pd.date_range(
        "2026-03-06 09:00", periods=periods, freq=freq, tz="Asia/Shanghai"
    )
    frame = C._chart_frame(pd.DataFrame({
        "bar_end": index, "open": 1.0, "high": 2.0, "low": 0.5,
        "close": 1.5, "volume": 1.0,
    }))
    return C._event_window(frame, index[periods // 2], radius=radius)


def test_slot_x_places_a_later_moment_to_the_right() -> None:
    frame = _window("5min", 60, 10)
    chart = (100.0, 0.0, 900.0, 100.0)
    signal = frame.index[len(frame) // 2]
    first = C._slot_x(frame, signal, chart)
    later = C._slot_x(frame, signal + pd.Timedelta(minutes=25), chart)
    assert first is not None and later is not None
    assert later > first


def test_slot_x_returns_none_past_the_window() -> None:
    frame = _window("5min", 60, 10)
    chart = (100.0, 0.0, 900.0, 100.0)
    assert C._slot_x(frame, frame.index[-1] + pd.Timedelta(days=3), chart) is None


# --------------------------------------------------------------------------
# trades.csv 的列格式
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "column,expected",
    [
        ("return_5min", 4), ("return_30min", 4),
        ("prior_5d_avg_market_volume", 0),
        ("fees", 2), ("net_pnl", 2), ("net_r", 2), ("mfe_r", 2), ("mae_r", 2),
        ("entry_price", None), ("quantity", None), ("total_return", None),
    ],
)
def test_decimal_rules(column, expected) -> None:
    assert _decimals_for(column) == expected


def test_formatting_widths_and_timezone() -> None:
    frame = pd.DataFrame([{
        "entry_time": pd.Timestamp("2026-01-07 10:48", tz="Asia/Shanghai"),
        "exit_time": pd.Timestamp("2026-01-07 11:09", tz="Asia/Shanghai"),
        "return_5min": 0.123456789,
        "prior_5d_avg_market_volume": 20550.4,
        "fees": 4.446375,
        "net_pnl": -1594.446375,
        "net_r": -0.2571409,
        "entry_price": 19797.0,
    }])
    out = _formatted_trades(frame)
    assert out.loc[0, "entry_time"] == "2026-01-07 10:48:00"
    assert out.loc[0, "exit_time"] == "2026-01-07 11:09:00"
    assert out.loc[0, "return_5min"] == "0.1235"
    assert out.loc[0, "prior_5d_avg_market_volume"] == "20550"
    assert out.loc[0, "fees"] == "4.45"
    assert out.loc[0, "net_pnl"] == "-1594.45"
    assert out.loc[0, "net_r"] == "-0.26"
    # 没有规则的列原样保留
    assert out.loc[0, "entry_price"] == 19797.0


def test_formatting_strips_the_offset_from_string_timestamps() -> None:
    frame = pd.DataFrame([{"entry_fee_known_at": "2026-01-06 15:30:00+08:00"}])
    out = _formatted_trades(frame)
    assert out.loc[0, "entry_fee_known_at"] == "2026-01-06 15:30:00"


def test_formatting_keeps_missing_values_empty() -> None:
    frame = pd.DataFrame([{"net_pnl": float("nan"), "return_5min": float("nan")}])
    out = _formatted_trades(frame)
    assert out.loc[0, "net_pnl"] == ""
    assert out.loc[0, "return_5min"] == ""


def test_symbol_name_is_the_second_column() -> None:
    frame = pd.DataFrame([{
        "candidate_id": "AG-1", "contract_code": "AG2604.SHF", "symbol": "AG",
    }])
    out = _with_symbol_name(frame)
    assert out.columns.tolist()[1] == "symbol_name"
    assert out.loc[0, "symbol_name"] == "白银"
