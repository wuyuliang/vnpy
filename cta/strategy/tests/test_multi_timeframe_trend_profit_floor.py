"""Tests for the trailing profit floor (跟踪止盈回撤保护)."""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.multi_timeframe_trend_backtest import engine as trend_engine
from cta.strategy.multi_timeframe_trend_backtest.engine import (
    _peak_unrealized_r,
    _price_for_unrealized_r,
    _profit_floor_touched,
    _refresh_profit_floor,
    _update_excursions,
)
from cta.strategy.tests.test_multi_timeframe_trend_backtest import (
    _MetadataStore,
    _candidate,
    _day_sessions,
    _minutes,
)

TZ = "Asia/Shanghai"


class _Meta:
    contract_size = 10.0
    price_tick = 0.1
    stressed_round_trip_slippage_ticks = 0.0
    stressed_entry_slippage_ticks = 0.0


def _position(entry=100.0, stop=99.0, quantity=1, direction=1):
    """1R = |entry-stop| * quantity * contract_size = 10 元/R，价格 1.0 == 1R。"""
    pending = trend_engine._PendingOrder(
        candidate={"direction": direction},
        metadata=_Meta(),
        order_id="o1",
        quantity=quantity,
        risk_budget=0.0,
        loss_per_lot=0.0,
    )
    return trend_engine._Position(
        pending=pending, quantity=quantity, initial_quantity=quantity,
        entry_time=pd.Timestamp("2026-01-05 09:05", tz=TZ), entry_price=entry,
        entry_reference=entry, stop=stop, target=math.nan,
        initial_risk_cash=abs(entry - stop) * quantity * _Meta.contract_size,
        entry_bar_index=0, maximum_favorable_price=entry,
        maximum_adverse_price=entry, current_metadata=_Meta(),
        base_quantity=quantity, symbol_quantity_scale=1.0,
        portfolio_quantity_scale=1.0, quantity_scale=1.0,
        position_scaling_reason="", symbol_recovery_deficit_at_entry=0.0,
        portfolio_recovery_deficit_at_entry=0.0,
        is_first_trade_in_trend_segment=1, trigger_to_prior_5d_high_ratio=0.0,
    )


def _bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c})


# --------------------------------------------------------------------------
# 峰值与地板换算
# --------------------------------------------------------------------------
def test_peak_uses_the_bar_high_for_a_long() -> None:
    p = _position()
    _update_excursions(p, _bar(100, 103.5, 99.5, 101))
    # 峰值用最高价而不是收盘价
    assert _peak_unrealized_r(p) == pytest.approx(3.5)


def test_peak_uses_the_bar_low_for_a_short() -> None:
    p = _position(entry=100.0, stop=101.0, direction=-1)
    _update_excursions(p, _bar(100, 100.5, 97.0, 99))
    assert _peak_unrealized_r(p) == pytest.approx(3.0)


def test_price_for_unrealized_r_round_trips() -> None:
    p = _position()
    assert _price_for_unrealized_r(p, 2.0) == pytest.approx(102.0)
    assert _price_for_unrealized_r(p, 0.0) == pytest.approx(100.0)


@pytest.mark.parametrize(
    "peak_r,expected_floor_r",
    [
        (2.0, 1.0),    # max(1, 0.5)=1  -> 2-1
        (4.0, 3.0),    # max(1, 1.0)=1  -> 4-1
        (8.0, 6.0),    # max(1, 2.0)=2  -> 8-2
        (12.0, 9.0),   # max(1, 3.0)=3  -> 12-3
    ],
)
def test_floor_formula(peak_r: float, expected_floor_r: float) -> None:
    cfg = MultiTimeframeTrendConfig()
    p = _position()
    _update_excursions(p, _bar(100, 100 + peak_r, 100, 100 + peak_r))
    _refresh_profit_floor(p, cfg)
    assert p.profit_floor_price == pytest.approx(100 + expected_floor_r)


def test_floor_is_not_armed_below_the_threshold() -> None:
    cfg = MultiTimeframeTrendConfig()   # arm=0.5R
    p = _position()
    _update_excursions(p, _bar(100, 100.4, 99.8, 100.2))
    _refresh_profit_floor(p, cfg)
    assert np.isnan(p.profit_floor_price)
    assert not _profit_floor_touched(p, _bar(100, 100, 95, 96))


def test_peak_between_arm_and_giveback_yields_a_breakeven_floor() -> None:
    """峰值在 0.5R~1R 之间时 floor = max(0, peak-1R) = 0，等于保本止损。
    这是启用阈值 0.5R 配 1R 回吐上限的直接后果，行为是有意的。"""
    cfg = MultiTimeframeTrendConfig()
    p = _position()
    _update_excursions(p, _bar(100, 100.7, 100, 100.5))
    _refresh_profit_floor(p, cfg)
    assert p.profit_floor_price == pytest.approx(100.0)


def test_floor_never_moves_down() -> None:
    cfg = MultiTimeframeTrendConfig()
    p = _position()
    _update_excursions(p, _bar(100, 104, 100, 103))
    _refresh_profit_floor(p, cfg)
    first = p.profit_floor_price
    # 价格回落不会拉低地板（峰值不变）
    _update_excursions(p, _bar(103, 103, 101, 101))
    _refresh_profit_floor(p, cfg)
    assert p.profit_floor_price == pytest.approx(first)


def test_partial_reduction_scales_giveback_by_remaining_quantity() -> None:
    cfg = MultiTimeframeTrendConfig()
    p = _position(quantity=10)
    p.quantity = 5
    p.maximum_favorable_price = 102.0

    assert _peak_unrealized_r(p) == pytest.approx(2.0)
    _refresh_profit_floor(p, cfg)
    # 半仓把默认 1R 回吐缩为 0.5R，2R 峰值对应 1.5R 地板。
    assert p.profit_floor_price == pytest.approx(101.5)


def test_disabled_config_never_arms_the_floor() -> None:
    cfg = MultiTimeframeTrendConfig(profit_floor_enabled=False)
    p = _position()
    _update_excursions(p, _bar(100, 110, 100, 109))
    _refresh_profit_floor(p, cfg)
    assert np.isnan(p.profit_floor_price)


# --------------------------------------------------------------------------
# 触发判定
# --------------------------------------------------------------------------
def test_floor_touch_uses_the_bar_low_for_a_long() -> None:
    cfg = MultiTimeframeTrendConfig()
    p = _position()
    _update_excursions(p, _bar(100, 104, 100, 103))
    _refresh_profit_floor(p, cfg)          # 地板 103.0
    assert not _profit_floor_touched(p, _bar(103, 103.5, 103.1, 103.3))
    assert _profit_floor_touched(p, _bar(103, 103.2, 102.9, 103.0))


def test_floor_touch_is_symmetric_for_a_short() -> None:
    cfg = MultiTimeframeTrendConfig()
    p = _position(entry=100.0, stop=101.0, direction=-1)
    _update_excursions(p, _bar(100, 100, 96, 97))     # 峰值 4R
    _refresh_profit_floor(p, cfg)                     # 地板 97.0
    assert not _profit_floor_touched(p, _bar(97, 96.9, 96.5, 96.7))
    assert _profit_floor_touched(p, _bar(97, 97.1, 96.8, 97.0))


# --------------------------------------------------------------------------
# 组合回放集成
# --------------------------------------------------------------------------
def _replay(bars, config):
    inputs = (
        trend_engine.PortfolioReplayInput(
            root_symbol="AG",
            exchange="SHFE",
            minute_bars=bars,
            five_minute_context=pd.DataFrame(columns=["bar_end", "daily_direction"]),
            candidates=_candidate(
                signal="2026-01-05 09:05", active="2026-01-05 09:06",
                expires="2026-01-05 09:40", setup="always_in", stop=99.0,
                candidate_id="AG-000001", symbol="AG",
            ),
            sessions=_day_sessions(),
        ),
    )
    return trend_engine.replay_trend_portfolio(
        inputs=inputs,
        metadata_store=_MetadataStore(
            contract_size=10.0, margin_rate=0.1,
            stressed_round_trip_fee_cash=0.0, price_tick=0.1,
            fees_by_date={date(2026, 1, 5): {
                "open_fee_rate": 0.0, "close_fee_rate": 0.0,
                "close_today_fee_rate": 0.0, "fee_per_lot_open": 0.0,
                "fee_per_lot_close": 0.0, "fee_per_lot_close_today": 0.0}},
        ),
        config=config,
        start=date(2026, 1, 5), end=date(2026, 1, 5), initial_equity=1_000_000.0,
    )


_NEUTRAL = dict(
    entry_blocked_session_windows=(),
    daily_circuit_breaker_enabled=False,
    max_positions_per_sector=99,
    pre_break_protection_enabled=False,
)


def _runup_then_fade():
    return _minutes(
        [
            ("2026-01-05 09:05", 99.5, 99.5, 99.5, 99.5, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 100.5, 99.9, 100.4, "AG2602.SHF"),
            ("2026-01-05 09:07", 100.4, 106.0, 100.4, 105.8, "AG2602.SHF"),  # 峰值 6R
            ("2026-01-05 09:08", 105.8, 105.9, 104.6, 104.8, "AG2602.SHF"),  # 地板 5R=105
            ("2026-01-05 09:09", 104.8, 104.9, 100.5, 100.6, "AG2602.SHF"),
            ("2026-01-05 09:10", 100.2, 100.3, 100.0, 100.1, "AG2602.SHF"),
        ]
    )


def test_replay_exits_on_the_profit_floor() -> None:
    artifacts = _replay(_runup_then_fade(), MultiTimeframeTrendConfig(**_NEUTRAL))
    assert artifacts.trades["exit_reason"].tolist() == ["PROFIT_FLOOR"]
    trade = artifacts.trades.iloc[0]
    # 09:09 击穿上一根已确认的地板，09:10 开盘才发出对手价平仓。
    assert trade["exit_time"] == pd.Timestamp("2026-01-05 09:10", tz=TZ)
    assert trade["exit_price"] == pytest.approx(100.1)


def test_trigger_bar_new_peak_cannot_improve_the_next_open_exit() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.5, 99.5, 99.5, 99.5, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 100.5, 99.9, 100.4, "AG2602.SHF"),
            ("2026-01-05 09:07", 100.4, 104.0, 100.4, 103.8, "AG2602.SHF"),
            # 旧地板 103 被击穿，同时又创出 10R 新峰值。
            ("2026-01-05 09:08", 105.0, 110.0, 102.0, 103.0, "AG2602.SHF"),
            ("2026-01-05 09:09", 101.0, 101.2, 100.8, 101.0, "AG2602.SHF"),
        ]
    )

    trade = _replay(bars, MultiTimeframeTrendConfig(**_NEUTRAL)).trades.iloc[0]

    assert trade["exit_time"] == pd.Timestamp("2026-01-05 09:09", tz=TZ)
    assert trade["exit_price"] == pytest.approx(100.9)


def test_extra_slippage_tick_makes_the_fill_exactly_one_tick_worse() -> None:
    """--profit_floor_extra_slippage_ticks 在既有滑点模型之上再加一档。"""
    with_extra = _replay(
        _runup_then_fade(),
        MultiTimeframeTrendConfig(profit_floor_extra_slippage_ticks=1, **_NEUTRAL),
    ).trades["exit_price"].iloc[0]
    without = _replay(
        _runup_then_fade(),
        MultiTimeframeTrendConfig(profit_floor_extra_slippage_ticks=0, **_NEUTRAL),
    ).trades["exit_price"].iloc[0]
    assert without - with_extra == pytest.approx(0.1)   # price_tick = 0.1

    two = _replay(
        _runup_then_fade(),
        MultiTimeframeTrendConfig(profit_floor_extra_slippage_ticks=2, **_NEUTRAL),
    ).trades["exit_price"].iloc[0]
    assert without - two == pytest.approx(0.2)


def test_replay_without_the_floor_gives_the_profit_back() -> None:
    artifacts = _replay(
        _runup_then_fade(),
        MightBeDisabled := MultiTimeframeTrendConfig(
            profit_floor_enabled=False, **_NEUTRAL
        ),
    )
    assert "PROFIT_FLOOR" not in set(artifacts.trades["exit_reason"])
    assert artifacts.trades["exit_price"].iloc[0] < 105.0


def test_floor_cannot_fire_on_the_bar_that_set_the_peak() -> None:
    """同一根 K 线内冲高后回落，地板不得在这根成交——它要到下一分钟才生效。"""
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.5, 99.5, 99.5, 99.5, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 100.5, 99.9, 100.4, "AG2602.SHF"),
            # 这一根既创出 6R 峰值又跌回 100.6，地板若在本根生效就会成交
            ("2026-01-05 09:07", 100.4, 106.0, 100.5, 100.6, "AG2602.SHF"),
            ("2026-01-05 09:08", 100.6, 100.7, 100.4, 100.5, "AG2602.SHF"),
        ]
    )
    artifacts = _replay(bars, MultiTimeframeTrendConfig(**_NEUTRAL))
    trades = artifacts.trades
    if not trades.empty and trades["exit_reason"].iloc[0] == "PROFIT_FLOOR":
        # 只允许在 09:08 之后成交，不能是 09:07
        assert trades["exit_time"].iloc[0] > pd.Timestamp("2026-01-05 09:07", tz=TZ)
