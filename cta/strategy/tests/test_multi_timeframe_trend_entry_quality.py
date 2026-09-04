"""Tests for the round-2 entry-quality gates (R1/R2/R3)."""
from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd
import pytest

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.multi_timeframe_trend_backtest import engine as trend_engine
from cta.strategy.multi_timeframe_trend_backtest.engine import (
    _entry_blocked_at_match,
    _order_crossed_recess,
)
from cta.strategy.multi_timeframe_trend_rules import (
    attach_session_range,
    entry_range_position,
)
from cta.strategy.multi_timeframe_trend_strategy import (
    _entry_quality_reason,
    _entry_range_position_reason,
)
from cta.strategy.multi_timeframe_trend_rules import SignalCandidate
from cta.strategy.tests.test_multi_timeframe_trend_backtest import (
    _MetadataStore,
    _candidate,
    _day_sessions,
    _minutes,
)

TZ = "Asia/Shanghai"


def _at(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz=TZ)


# --------------------------------------------------------------------------
# R1 撮合时复检
# --------------------------------------------------------------------------
def test_order_crossed_recess_measures_the_gap_since_activation() -> None:
    active = _at("2026-02-25 11:30")
    assert not _order_crossed_recess(active, _at("2026-02-25 11:31"), 90)
    assert not _order_crossed_recess(active, _at("2026-02-25 12:59"), 90)
    assert _order_crossed_recess(active, _at("2026-02-25 13:31"), 90)


def test_order_crossed_recess_is_disabled_at_zero() -> None:
    active = _at("2026-02-25 11:30")
    assert not _order_crossed_recess(active, _at("2026-02-26 09:01"), 0)


def test_match_time_recheck_blocks_a_fill_inside_the_window() -> None:
    config = MultiTimeframeTrendConfig()
    # 11:30 挂出、13:31 才触发：既在禁止窗口内，也跨过了午休
    assert (
        _entry_blocked_at_match(_at("2026-02-25 11:30"), _at("2026-02-25 13:31"), config)
        == "ENTRY_SESSION_WINDOW_BLOCKED"
    )


def test_match_time_recheck_flags_a_stale_order_outside_the_window() -> None:
    config = MultiTimeframeTrendConfig(entry_blocked_session_windows=())
    assert (
        _entry_blocked_at_match(_at("2026-02-25 11:30"), _at("2026-02-25 13:31"), config)
        == "ORDER_CROSSED_SESSION_RECESS"
    )


def test_match_time_recheck_passes_a_normal_same_session_fill() -> None:
    config = MultiTimeframeTrendConfig()
    assert not _entry_blocked_at_match(
        _at("2026-02-25 10:05"), _at("2026-02-25 10:12"), config
    )


def test_replay_cancels_an_order_that_would_fill_after_the_recess() -> None:
    """入场信号在 10:26 产生，触发价直到 14:31 才被打到：应撤单而不是成交。"""
    bars = _minutes(
        [
            ("2026-01-05 10:26", 99.5, 99.5, 99.5, 99.5, "AG2602.SHF"),
            ("2026-01-05 10:27", 99.6, 99.7, 99.5, 99.6, "AG2602.SHF"),
            ("2026-01-05 14:31", 100.0, 101.0, 100.0, 100.5, "AG2602.SHF"),
        ]
    )
    inputs = (
        trend_engine.PortfolioReplayInput(
            root_symbol="AG",
            exchange="SHFE",
            minute_bars=bars,
            five_minute_context=pd.DataFrame(columns=["bar_end", "daily_direction"]),
            candidates=_candidate(
                signal="2026-01-05 10:26",
                active="2026-01-05 10:27",
                expires="2026-01-05 14:45",
                setup="always_in",
                stop=99.0,
                candidate_id="AG-000001",
                symbol="AG",
            ),
            sessions=_day_sessions(),
        ),
    )
    artifacts = trend_engine.replay_trend_portfolio(
        inputs=inputs,
        metadata_store=_MetadataStore(
            contract_size=10.0,
            margin_rate=0.1,
            stressed_round_trip_fee_cash=0.0,
            price_tick=0.1,
            fees_by_date={
                date(2026, 1, 5): {
                    "open_fee_rate": 0.0,
                    "close_fee_rate": 0.0,
                    "close_today_fee_rate": 0.0,
                    "fee_per_lot_open": 0.0,
                    "fee_per_lot_close": 0.0,
                    "fee_per_lot_close_today": 0.0,
                }
            },
        ),
        config=MultiTimeframeTrendConfig(),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )
    assert artifacts.trades.empty
    reasons = set(artifacts.orders["reason"]) | set(artifacts.rejections["reason_code"])
    assert "ENTRY_SESSION_WINDOW_BLOCKED" in reasons


# --------------------------------------------------------------------------
# R3 区间窗口
# --------------------------------------------------------------------------
def _range_frame() -> pd.DataFrame:
    rows = []
    for day, base in ((date(2026, 1, 5), 100.0), (date(2026, 1, 6), 110.0), (date(2026, 1, 7), 120.0)):
        for k in range(4):
            rows.append(
                {
                    "bar_end": pd.Timestamp(f"{day} 09:{5 * (k + 1):02d}", tz=TZ),
                    "exchange_trade_date": day,
                    "high": base + k,
                    "low": base - k,
                    "close": base,
                }
            )
    return pd.DataFrame(rows)


def test_session_range_uses_two_prior_days_plus_today_so_far() -> None:
    frame = _range_frame()
    attach_session_range(frame, 2)
    third_day = frame[frame["exchange_trade_date"].eq(date(2026, 1, 7))]
    # 前两日区间 [97, 113]，第三日逐根扩展
    first = third_day.iloc[0]
    assert first["range_window_high"] == pytest.approx(120.0)
    assert first["range_window_low"] == pytest.approx(97.0)
    last = third_day.iloc[-1]
    assert last["range_window_high"] == pytest.approx(123.0)
    assert last["range_window_low"] == pytest.approx(97.0)


def test_session_range_is_nan_until_history_is_long_enough() -> None:
    frame = _range_frame()
    attach_session_range(frame, 2)
    early = frame[frame["exchange_trade_date"].isin([date(2026, 1, 5), date(2026, 1, 6)])]
    assert early["range_window_high"].isna().all()
    assert early["range_window_low"].isna().all()


def test_session_range_never_looks_forward() -> None:
    frame = _range_frame()
    attach_session_range(frame, 2)
    third = frame[frame["exchange_trade_date"].eq(date(2026, 1, 7))].reset_index(drop=True)
    assert third["range_window_high"].is_monotonic_increasing
    for i in range(len(third)):
        assert third.loc[i, "range_window_high"] >= third.loc[i, "high"]


def test_session_range_without_trade_date_column_fails_open() -> None:
    frame = _range_frame().drop(columns=["exchange_trade_date"])
    attach_session_range(frame, 2)
    assert frame["range_window_high"].isna().all()


@pytest.mark.parametrize(
    "trigger,expected",
    [(100.0, 0.0), (110.0, 0.5), (120.0, 1.0), (125.0, 1.25)],
)
def test_entry_range_position(trigger: float, expected: float) -> None:
    assert entry_range_position(trigger, 120.0, 100.0) == pytest.approx(expected)


def test_entry_range_position_is_nan_on_a_degenerate_window() -> None:
    assert np.isnan(entry_range_position(100.0, 100.0, 100.0))
    assert np.isnan(entry_range_position(100.0, np.nan, 90.0))


# --------------------------------------------------------------------------
# R2 + R3 候选过滤
# --------------------------------------------------------------------------
def _signal(trigger: float = 110.0, stop: float = 108.0, direction: int = 1) -> SignalCandidate:
    return SignalCandidate(
        setup_type="always_in",
        direction=direction,
        trigger=trigger,
        stop_price=stop,
        signal_time=pd.Timestamp("2026-01-07 10:00", tz=TZ),
        signal_index=10,
        known_at=pd.Timestamp("2026-01-07 10:00", tz=TZ),
    )


def _row(**kwargs) -> pd.Series:
    base = {
        "volume": 100.0,
        "volume_threshold": 100.0,
        "range_window_high": 120.0,
        "range_window_low": 100.0,
    }
    base.update(kwargs)
    return pd.Series(base)


def _cfg(**kwargs) -> MultiTimeframeTrendConfig:
    return MultiTimeframeTrendConfig(**kwargs)


def test_volume_ratio_gate() -> None:
    cfg = _cfg(max_entry_volume_ratio=2.0)
    assert not _entry_quality_reason(
        candidate=_signal(), row=_row(volume=200.0), daily_atr14=10.0, config=cfg
    )
    assert (
        _entry_quality_reason(
            candidate=_signal(), row=_row(volume=201.0), daily_atr14=10.0, config=cfg
        )
        == "ENTRY_VOLUME_RATIO_TOO_HIGH"
    )


def test_stop_distance_gate() -> None:
    cfg = _cfg(max_entry_stop_distance_atr=0.8)
    assert not _entry_quality_reason(
        candidate=_signal(trigger=110.0, stop=102.0), row=_row(), daily_atr14=10.0, config=cfg
    )
    assert (
        _entry_quality_reason(
            candidate=_signal(trigger=110.0, stop=101.0),
            row=_row(),
            daily_atr14=10.0,
            config=cfg,
        )
        == "ENTRY_STOP_DISTANCE_TOO_WIDE"
    )


def test_range_width_gate() -> None:
    cfg = _cfg(min_entry_range_width_atr=2.0)
    # 区间 20 点：ATR 10 → 2.0 宽度，恰好通过
    assert not _entry_quality_reason(
        candidate=_signal(), row=_row(), daily_atr14=10.0, config=cfg
    )
    # ATR 11 → 宽度 1.82，太窄
    assert (
        _entry_quality_reason(candidate=_signal(), row=_row(), daily_atr14=11.0, config=cfg)
        == "ENTRY_RANGE_TOO_NARROW"
    )


def test_range_position_gate_blocks_the_top_of_the_range() -> None:
    cfg = _cfg(max_entry_range_position=0.85)
    # 区间 [100,120]，0.85 对应 117
    assert not _entry_range_position_reason(
        candidate=_signal(trigger=117.0), row=_row(), config=cfg
    )
    assert (
        _entry_range_position_reason(
            candidate=_signal(trigger=118.0), row=_row(), config=cfg
        )
        == "ENTRY_RANGE_POSITION_TOO_HIGH"
    )
    # 突破区间上沿更要拦
    assert (
        _entry_range_position_reason(
            candidate=_signal(trigger=125.0), row=_row(), config=cfg
        )
        == "ENTRY_RANGE_POSITION_TOO_HIGH"
    )
    # 关闭后放行
    assert not _entry_range_position_reason(
        candidate=_signal(trigger=125.0),
        row=_row(),
        config=_cfg(max_entry_range_position=0.0),
    )


def test_range_position_gate_is_symmetric_for_shorts() -> None:
    cfg = _cfg(max_entry_range_position=0.85)
    # 空头在区间下沿开仓应被拦
    assert (
        _entry_range_position_reason(
            candidate=_signal(trigger=102.0, stop=104.0, direction=-1),
            row=_row(),
            config=cfg,
        )
        == "ENTRY_RANGE_POSITION_TOO_HIGH"
    )
    assert not _entry_range_position_reason(
        candidate=_signal(trigger=118.0, stop=120.0, direction=-1),
        row=_row(),
        config=cfg,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(max_entry_volume_ratio=0.0),
        dict(max_entry_stop_distance_atr=0.0),
        dict(max_entry_range_position=0.0),
        dict(min_entry_range_width_atr=0.0),
    ],
)
def test_each_gate_can_be_switched_off(kwargs: dict) -> None:
    cfg = _cfg(
        max_entry_volume_ratio=0.0,
        max_entry_stop_distance_atr=0.0,
        max_entry_range_position=0.0,
        min_entry_range_width_atr=0.0,
    )
    assert not _entry_quality_reason(
        candidate=_signal(trigger=999.0, stop=0.1),
        row=_row(volume=1e9),
        daily_atr14=10.0,
        config=cfg,
    )


def test_gates_fail_open_on_missing_inputs() -> None:
    cfg = _cfg()
    assert not _entry_quality_reason(
        candidate=_signal(),
        row=_row(volume=np.nan, volume_threshold=np.nan,
                 range_window_high=np.nan, range_window_low=np.nan),
        daily_atr14=np.nan,
        config=cfg,
    )


def test_session_range_is_immune_to_future_bars() -> None:
    """Perturbing any later bar must not change an earlier bar's window.

    This is the property that matters for causality: if a future high could
    leak into an earlier row the whole filter would be look-ahead.
    """
    base = _range_frame()
    attach_session_range(base, 2)
    reference = base[["range_window_high", "range_window_low"]].copy()

    for cut in range(1, len(base)):
        perturbed = _range_frame()
        perturbed.loc[cut:, "high"] = perturbed.loc[cut:, "high"] + 1_000.0
        perturbed.loc[cut:, "low"] = perturbed.loc[cut:, "low"] - 1_000.0
        attach_session_range(perturbed, 2)
        pd.testing.assert_frame_equal(
            perturbed.loc[: cut - 1, ["range_window_high", "range_window_low"]],
            reference.loc[: cut - 1],
        )


def test_session_range_high_always_covers_the_current_bar() -> None:
    """The signal bar itself is completed and known, so it belongs in the window."""
    frame = _range_frame()
    attach_session_range(frame, 2)
    ready = frame.dropna(subset=["range_window_high"])
    assert not ready.empty
    assert (ready["range_window_high"] >= ready["high"]).all()
    assert (ready["range_window_low"] <= ready["low"]).all()


def test_session_range_ignores_bars_of_other_contracts_order() -> None:
    """Rows must already be time-ordered; the helper relies on it for cummax."""
    frame = _range_frame()
    attach_session_range(frame, 2)
    third = frame[frame["exchange_trade_date"].eq(date(2026, 1, 7))]
    assert third["range_window_high"].is_monotonic_increasing
    assert third["range_window_low"].is_monotonic_decreasing


# --------------------------------------------------------------------------
# 追高影子单与门槛
# --------------------------------------------------------------------------
from cta.strategy.multi_timeframe_trend_backtest.engine import (  # noqa: E402
    _ChaseHighState,
    _VirtualPosition,
    _cancel_virtual_on_roll,
    _chase_high_gate_open,
    _VirtualOrder,
    _virtual_entry_price,
)


def _bar(open_=100.0, high=101.0, low=99.0, close=100.0) -> pd.Series:
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


def test_gate_stays_shut_without_any_virtual_history() -> None:
    allowed, detail = _chase_high_gate_open(
        _ChaseHighState(),
        MultiTimeframeTrendConfig(chase_high_entry_enabled=True),
    )
    assert not allowed
    assert detail == "no_history"


def test_gate_opens_once_recent_virtual_trades_are_profitable() -> None:
    cfg = MultiTimeframeTrendConfig(
        chase_high_entry_enabled=True,
        chase_high_lookback=3, chase_high_min_samples=1, chase_high_min_prior_r=0.0
    )
    state = _ChaseHighState()
    for value in (-1.0, -1.0, 2.5):
        state.record(value)
    # 最近3笔合计 +0.5 > 0
    allowed, _ = _chase_high_gate_open(state, cfg)
    assert allowed
    # 连续三笔亏损把窗口填满后，门槛重新关上
    for _ in range(3):
        state.record(-1.0)
    allowed, _ = _chase_high_gate_open(state, cfg)
    assert not allowed


def test_gate_only_looks_at_the_lookback_window() -> None:
    cfg = MultiTimeframeTrendConfig(
        chase_high_entry_enabled=True,
        chase_high_lookback=2, chase_high_min_samples=1, chase_high_min_prior_r=0.0
    )
    state = _ChaseHighState()
    state.record(10.0)          # 很久以前的大赚，不该继续放行
    state.record(-1.0)
    state.record(-1.0)
    allowed, _ = _chase_high_gate_open(state, cfg)
    assert not allowed


def test_gate_respects_a_stricter_threshold() -> None:
    state = _ChaseHighState()
    state.record(0.5)
    assert _chase_high_gate_open(
        state, MultiTimeframeTrendConfig(
            chase_high_entry_enabled=True,
            chase_high_lookback=5, chase_high_min_samples=1, chase_high_min_prior_r=0.0
        )
    )[0]
    assert not _chase_high_gate_open(
        state, MultiTimeframeTrendConfig(
            chase_high_entry_enabled=True,
            chase_high_lookback=5, chase_high_min_samples=1, chase_high_min_prior_r=1.0
        )
    )[0]


def test_gate_is_shut_by_default() -> None:
    """默认不追高：这是 r4_fixed 实测之后的选择，不是保守猜测。"""
    state = _ChaseHighState()
    for _ in range(10):
        state.record(5.0)
    allowed, detail = _chase_high_gate_open(state, MultiTimeframeTrendConfig())
    assert not allowed
    assert detail == "entry_disabled"


def test_virtual_tracking_is_independent_of_real_entries() -> None:
    """影子记录默认继续跑：不花钱也能持续观察追高灵不灵。"""
    cfg = MultiTimeframeTrendConfig()
    assert cfg.chase_high_virtual_enabled
    assert not cfg.chase_high_entry_enabled
    state = _ChaseHighState()
    state.record(1.0)
    assert state.outcomes == [1.0]
    assert not _chase_high_gate_open(state, cfg)[0]


def test_virtual_state_ignores_non_finite_outcomes() -> None:
    state = _ChaseHighState()
    state.record(float("nan"))
    state.record(float("inf"))
    assert state.outcomes == []


def _order(direction: int = 1, trigger: float = 100.0) -> _VirtualOrder:
    return _VirtualOrder(
        root_symbol="AG",
        candidate_id="AG-1",
        contract_code="AG2602.SHF",
        direction=direction,
        trigger=trigger,
        stop_price=trigger - 2.0 * direction,
        expires_at=_at("2026-01-05 14:00"),
        active_at=_at("2026-01-05 10:00"),
        tick_size=0.1,
    )


def test_virtual_position_is_discarded_on_roll_without_recording_r() -> None:
    order = _order()
    orders = {"AG": order}
    positions = {
        "AG": _VirtualPosition(
            root_symbol="AG",
            candidate_id=order.candidate_id,
            contract_code=order.contract_code,
            direction=order.direction,
            entry_time=_at("2026-01-05 10:01"),
            entry_price=100.0,
            stop_price=98.0,
            initial_risk=2.0,
            tick_size=0.1,
        )
    }
    state = _ChaseHighState()

    assert _cancel_virtual_on_roll(
        "AG",
        "AG2603.SHF",
        orders,
        positions,
    )

    assert orders == {}
    assert positions == {}
    assert state.outcomes == []


def test_virtual_stop_order_does_not_fill_below_its_trigger() -> None:
    assert np.isnan(_virtual_entry_price(_order(), _bar(high=99.5)))


def test_virtual_long_fills_at_the_worse_of_trigger_and_open() -> None:
    # 平开：按触发价成交
    assert _virtual_entry_price(_order(), _bar(open_=99.0, high=101.0)) == pytest.approx(100.0)
    # 跳空高开：按开盘价成交，不给凭空的好价格
    assert _virtual_entry_price(_order(), _bar(open_=100.8, high=101.0)) == pytest.approx(100.8)


def test_virtual_short_fills_symmetrically() -> None:
    order = _order(direction=-1, trigger=100.0)
    assert np.isnan(_virtual_entry_price(order, _bar(low=100.5)))
    assert _virtual_entry_price(order, _bar(open_=101.0, low=99.0)) == pytest.approx(100.0)
    assert _virtual_entry_price(order, _bar(open_=99.2, low=99.0)) == pytest.approx(99.2)


def test_chase_high_candidate_flag_marks_only_otherwise_clean_signals() -> None:
    from cta.strategy.multi_timeframe_trend_strategy import _entry_quality_reason

    cfg = MultiTimeframeTrendConfig()
    # 量比超标的候选即使也在区间高端，也不是干净的追高信号
    assert (
        _entry_quality_reason(
            candidate=_signal(trigger=125.0),
            row=_row(volume=1e6),
            daily_atr14=10.0,
            config=cfg,
        )
        == "ENTRY_VOLUME_RATIO_TOO_HIGH"
    )


def test_gate_needs_a_minimum_number_of_virtual_samples() -> None:
    cfg = MultiTimeframeTrendConfig(
        chase_high_entry_enabled=True, chase_high_lookback=10, chase_high_min_samples=5
    )
    state = _ChaseHighState()
    for _ in range(4):
        state.record(2.0)          # 全赢，但只有 4 笔
    allowed, detail = _chase_high_gate_open(state, cfg)
    assert not allowed
    assert "insufficient_samples" in detail
    state.record(2.0)              # 第 5 笔到位
    allowed, detail = _chase_high_gate_open(state, cfg)
    assert allowed
    assert "prior_r=10" in detail


def test_min_samples_cannot_exceed_the_lookback() -> None:
    with pytest.raises(ValueError):
        MultiTimeframeTrendConfig(chase_high_lookback=3, chase_high_min_samples=5)


def test_real_chase_trades_feed_back_so_the_gate_can_close_again() -> None:
    """开闸后如果没有回灌，门槛就成了闩锁：开一次就永远开着。

    实跑里这正是回撤的来源——5 笔一月份的虚拟单把门槛焊死了 5 个月。
    """
    cfg = MultiTimeframeTrendConfig(
        chase_high_entry_enabled=True, chase_high_lookback=10, chase_high_min_samples=5
    )
    state = _ChaseHighState()
    for _ in range(5):
        state.record(1.0)
    assert _chase_high_gate_open(state, cfg)[0]
    # 开闸后放行的真实单持续亏损，必须能把门槛重新关上
    for _ in range(6):
        state.record(-1.0)
    assert not _chase_high_gate_open(state, cfg)[0]


def test_default_min_samples_requires_most_of_the_window() -> None:
    cfg = MultiTimeframeTrendConfig(chase_high_entry_enabled=True)
    assert cfg.chase_high_min_samples == 8
    assert cfg.chase_high_lookback == 10
    state = _ChaseHighState()
    for _ in range(7):
        state.record(5.0)
    allowed, detail = _chase_high_gate_open(state, cfg)
    assert not allowed
    assert "insufficient_samples" in detail


# --------------------------------------------------------------------------
# 机会图表出图口径
# --------------------------------------------------------------------------
def _chart_inputs():
    import pandas as _pd

    bars = _pd.DataFrame(
        {
            "bar_end": _pd.date_range("2026-01-05 09:05", periods=40, freq="5min", tz=TZ),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "volume": 10.0, "symbol": "AG",
        }
    )
    candidates = _pd.DataFrame(
        [
            {"candidate_id": "AG-1", "symbol": "AG",
             "signal_time": _pd.Timestamp("2026-01-05 09:05", tz=TZ),
             "setup_type": "always_in", "direction": 1, "filtered_reason": ""},
            {"candidate_id": "AG-2", "symbol": "AG",
             "signal_time": _pd.Timestamp("2026-01-05 09:10", tz=TZ),
             "setup_type": "always_in", "direction": 1,
             "filtered_reason": "ENTRY_RANGE_TOO_NARROW"},
        ]
    )
    trades = _pd.DataFrame([{"candidate_id": "AG-1", "exit_price": 100.5}])
    return bars, candidates, trades


@pytest.mark.parametrize(
    "mode,expected_pngs",
    [("traded", 1), ("all", 2), ("none", 0)],
)
def test_chart_render_modes(tmp_path, mode: str, expected_pngs: int) -> None:
    import pandas as _pd
    from cta.strategy.multi_timeframe_trend_backtest.charts import (
        render_opportunity_charts,
    )

    bars, candidates, trades = _chart_inputs()
    index = render_opportunity_charts(
        tmp_path,
        candidates=candidates,
        daily_bars=bars,
        hourly_bars=bars,
        five_minute_bars=bars,
        trades=trades,
        orders=_pd.DataFrame(),
        rejections=_pd.DataFrame(),
        render_outcomes=mode,
    )
    pngs = list((tmp_path / "opportunity_charts").rglob("*.png"))
    assert len(pngs) == expected_pngs
    # 无论出不出图，index.csv 都要列全所有候选，漏斗审计不能缺
    assert len(index) == 2
    assert set(index["outcome_code"]) == {"TRADED", "ENTRY_RANGE_TOO_NARROW"}
    rendered = index.loc[index["chart_path"].astype(str).ne(""), "outcome_code"]
    assert len(rendered) == expected_pngs
    if mode == "traded":
        assert rendered.tolist() == ["TRADED"]


def test_chart_render_mode_is_validated(tmp_path) -> None:
    import pandas as _pd
    from cta.strategy.multi_timeframe_trend_backtest.charts import (
        render_opportunity_charts,
    )

    bars, candidates, trades = _chart_inputs()
    with pytest.raises(ValueError, match="render_outcomes"):
        render_opportunity_charts(
            tmp_path,
            candidates=candidates,
            daily_bars=bars,
            hourly_bars=bars,
            five_minute_bars=bars,
            trades=trades,
            orders=_pd.DataFrame(),
            rejections=_pd.DataFrame(),
            render_outcomes="TRADED",
        )
