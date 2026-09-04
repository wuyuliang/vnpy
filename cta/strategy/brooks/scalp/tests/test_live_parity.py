from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from vnpy.trader.constant import Offset as VnOffset

from cta.strategy.brooks.scalp.data import MinuteBar
from cta.strategy.brooks.scalp.engine import (
    BrokerTradeEvent,
    Offset,
    OrderAction,
    OrderType,
    ScalpEngine,
    TradePlan,
)
from cta.strategy.brooks.scalp.metadata import FeeMarginSpec, InstrumentSpec
from cta.strategy.brooks.scalp.live_strategy import LiveBarAdapter, _vn_offset
from cta.strategy.brooks.scalp.online_runner import main as online_main
from cta.strategy.brooks.scalp.portfolio_coordinator import BrooksScalpPortfolioCoordinator
from cta.strategy.brooks.scalp.regime import RegimeState
from cta.strategy.brooks.scalp.risk_policy import RiskSnapshot
from cta.strategy.brooks.scalp.session import (
    SessionCalendar,
    SessionSegment,
    SessionSpec,
    TradingCalendarEntry,
)
from cta.strategy.brooks.scalp.setups import SetupCandidate


TZ = "Asia/Shanghai"


def _ts(value: str) -> datetime:
    return pd.Timestamp(value, tz=TZ).to_pydatetime()


def _session(session_id: str, start: time, end: time, *, night: bool) -> SessionSpec:
    return SessionSpec(
        session_id=session_id,
        is_night=night,
        segments=(SessionSegment(session_id, start, end, start),),
    )


def _calendar(sessions: tuple[SessionSpec, ...]) -> SessionCalendar:
    return SessionCalendar(
        exchange="SHFE",
        entries=(
            TradingCalendarEntry(
                exchange="SHFE",
                exchange_trade_date=date(2026, 1, 5),
                is_open=True,
                prior_open_date=date(2026, 1, 4),
                next_open_date=date(2026, 1, 6),
                night_session_start=time(21, 0),
                source="fixture",
                known_at=_ts("2025-01-01 00:00"),
            ),
        ),
        sessions=sessions,
        calendar_sha256="fixture-calendar",
    )


def _instrument(root: str, session: SessionSpec) -> InstrumentSpec:
    return InstrumentSpec(
        root_symbol=root,
        exchange="SHFE",
        contract_size=10.0,
        price_tick=1.0,
        lot_step=1,
        slippage_ticks_base=0.0,
        sessions=(session,),
        effective_from=date(2020, 1, 1),
        effective_to=None,
    )


def _fee(root: str, contract: str) -> FeeMarginSpec:
    return FeeMarginSpec(
        schedule_id=f"{root}_fixture",
        root_symbol=root,
        contract_code=contract,
        margin_rate_long=0.10,
        margin_rate_short=0.10,
        open_fee_rate=0.0,
        close_fee_rate=0.0,
        close_today_fee_rate=0.0,
        fee_per_lot_open=0.0,
        fee_per_lot_close=0.0,
        fee_per_lot_close_today=0.0,
        effective_from=_ts("2020-01-01 00:00"),
        effective_to=None,
        source="fixture",
        known_at=_ts("2019-01-01 00:00"),
    )


def _bar(
    start: str,
    *,
    symbol: str,
    root: str,
    contract: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    limit_up: float = 110.0,
    limit_down: float = 90.0,
    session_open: str = "2026-01-05 09:00",
    session_close: str = "2026-01-05 15:00",
    session_id: str = "20260105:day",
) -> MinuteBar:
    begin = _ts(start)
    end = begin + timedelta(minutes=1)
    return MinuteBar(
        source_calendar_date=begin.date(),
        exchange_trade_date=date(2026, 1, 5),
        session_id=session_id,
        session_kind=session_id.split(":")[-1],
        segment_id=session_id.split(":")[-1],
        bar_start=begin,
        bar_end=end,
        root_symbol=root,
        vt_symbol=symbol,
        contract_code=contract,
        exchange="SHFE",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        turnover=100_000.0,
        open_interest=10_000.0,
        pre_settlement=100.0,
        limit_up=limit_up,
        limit_down=limit_down,
        limit_source="fixture",
        limit_known_at=_ts("2025-01-01 00:00"),
        source_path="fixture",
        source_row=0,
        calendar_sha256="fixture-calendar",
        session_open=_ts(session_open),
        session_close=_ts(session_close),
        segment_start=_ts(session_open),
        segment_end=_ts(session_close),
        bucket_anchor=_ts(session_open),
    )


def _candidate(symbol: str, contract: str) -> SetupCandidate:
    setup = _ts("2026-01-05 09:01")
    return SetupCandidate(
        candidate_id=f"candidate-{symbol}",
        rule_id="strong_breakout_follow_through",
        symbol=symbol,
        contract_code=contract,
        direction=1,
        regime=RegimeState.STRONG_TREND_UP,
        setup_bar_end=setup,
        trigger_price=100.0,
        structural_stop=95.0,
        available_space_price=20.0,
        feature_source_max=setup,
        expires_after_bar_end=setup + timedelta(minutes=3),
        context={},
    )


def test_single_symbol_coordinator_matches_direct_engine_decisions() -> None:
    day = _session("day", time(9, 0), time(15, 0), night=False)
    instrument = _instrument("RB", day)
    direct = ScalpEngine(instrument, _fee("RB", "RB2605.SHF"), auto_match=True)
    coordinated = ScalpEngine(instrument, _fee("RB", "RB2605.SHF"), auto_match=True)
    candidate = _candidate("RB0.SHFE", "RB2605.SHF")
    direct.arm_candidate(candidate, quantity=1, target_price=108.0)
    coordinated.arm_candidate(candidate, quantity=1, target_price=108.0)
    coordinator = BrooksScalpPortfolioCoordinator(initial_equity=200_000.0, mode="historical")
    coordinator.register_symbol("RB0.SHFE", coordinated, _calendar((day,)))
    bars = [
        _bar(
            "2026-01-05 09:01",
            symbol="RB0.SHFE",
            root="RB",
            contract="RB2605.SHF",
            open_=99.0,
            high=101.0,
            low=98.0,
            close=100.0,
        ),
        _bar(
            "2026-01-05 09:02",
            symbol="RB0.SHFE",
            root="RB",
            contract="RB2605.SHF",
            open_=100.0,
            high=108.0,
            low=99.0,
            close=107.0,
        ),
    ]
    for bar in bars:
        direct.on_bar(bar)
        coordinator.on_symbol_bar("RB0.SHFE", bar)

    assert coordinated.snapshot().to_dict() == direct.snapshot().to_dict()
    assert coordinator.snapshot().marked_equity == coordinated.snapshot().marked_equity


def test_shared_account_starts_at_200k_not_sum_of_symbol_engines() -> None:
    day = _session("day", time(9, 0), time(15, 0), night=False)
    coordinator = BrooksScalpPortfolioCoordinator(initial_equity=200_000.0, mode="historical")
    for symbol, root, contract in (
        ("RB0.SHFE", "RB", "RB2605.SHF"),
        ("CU0.SHFE", "CU", "CU2605.SHF"),
    ):
        instrument = _instrument(root, day)
        coordinator.register_symbol(
            symbol,
            ScalpEngine(instrument, _fee(root, contract), initial_cash=200_000.0),
            _calendar((day,)),
        )
    snapshot = coordinator.snapshot()
    assert snapshot.marked_equity == 200_000.0
    assert snapshot.initial_equity == 200_000.0


def test_coordinator_resizes_gap_entry_against_current_shared_account() -> None:
    day = _session("day", time(9, 0), time(15, 0), night=False)
    coordinator = BrooksScalpPortfolioCoordinator(initial_equity=200_000.0, mode="historical")
    engine = ScalpEngine(
        _instrument("RB", day),
        _fee("RB", "RB2605.SHF"),
        initial_cash=200_000.0,
    )
    coordinator.register_symbol("RB0.SHFE", engine, _calendar((day,)))
    candidate = _candidate("RB0.SHFE", "RB2605.SHF")
    plan = TradePlan(
        trigger_price=100.0,
        structural_stop=95.0,
        target_price=130.0,
        obstacle_price=140.0,
        price_risk=5.0,
        net_stop_loss_per_contract=50.0,
        planned_net_payoff=1.10,
        available_space_r=8.0,
        pressure_cost_price=0.0,
    )
    risk = RiskSnapshot(
        current_risk_pct=0.0025,
        session_return=0.0,
        trade_date_return=0.0,
        peak_profit=0.0,
        giveback_cny=0.0,
        drawdown_pct=0.0,
        symbol_consecutive_losses=0,
        portfolio_consecutive_losses=0,
        cooldown_until=None,
        symbol_stopped=False,
        portfolio_stopped=False,
        daily_locked=False,
    )

    intent, decision = coordinator.submit_candidate(
        candidate,
        plan,
        risk,
        {"exchange_trade_date": date(2026, 1, 5)},
    )
    assert intent is not None
    assert decision.quantity == 10

    coordinator.on_symbol_bar(
        "RB0.SHFE",
        _bar(
            "2026-01-05 09:01",
            symbol="RB0.SHFE",
            root="RB",
            contract="RB2605.SHF",
            open_=110.0,
            high=112.0,
            low=109.0,
            close=109.0,
            limit_up=120.0,
        ),
    )

    snapshot = engine.snapshot()
    assert snapshot.position is not None
    assert snapshot.position.quantity == 3
    entry = next(order for order in snapshot.orders if order.order_id == intent.order_id)
    assert entry.quantity == 3


def test_post_fill_slippage_breach_cancels_remainder_and_submits_immediate_exit() -> None:
    day = _session("day", time(9, 0), time(15, 0), night=False)
    coordinator = BrooksScalpPortfolioCoordinator(
        initial_equity=200_000.0,
        mode="historical",
    )
    engine = ScalpEngine(
        _instrument("RB", day),
        _fee("RB", "RB2605.SHF"),
        initial_cash=200_000.0,
        auto_match=False,
    )
    coordinator.register_symbol("RB0.SHFE", engine, _calendar((day,)))
    candidate = _candidate("RB0.SHFE", "RB2605.SHF")
    plan = TradePlan(
        trigger_price=100.0,
        structural_stop=95.0,
        target_price=130.0,
        obstacle_price=140.0,
        price_risk=5.0,
        net_stop_loss_per_contract=50.0,
        planned_net_payoff=1.10,
        available_space_r=8.0,
        pressure_cost_price=0.0,
    )
    risk = RiskSnapshot(
        current_risk_pct=0.0025,
        session_return=0.0,
        trade_date_return=0.0,
        peak_profit=0.0,
        giveback_cny=0.0,
        drawdown_pct=0.0,
        symbol_consecutive_losses=0,
        portfolio_consecutive_losses=0,
        cooldown_until=None,
        symbol_stopped=False,
        portfolio_stopped=False,
        daily_locked=False,
    )
    entry, decision = coordinator.submit_candidate(candidate, plan, risk, {})
    assert entry is not None
    assert decision.quantity == 10
    bar = _bar(
        "2026-01-05 09:01",
        symbol="RB0.SHFE",
        root="RB",
        contract="RB2605.SHF",
        open_=99.0,
        high=101.0,
        low=98.0,
        close=100.0,
    )
    coordinator.on_symbol_bar("RB0.SHFE", bar)

    fill = BrokerTradeEvent(
        trade_id="late-adverse-fill",
        order_id=entry.order_id,
        candidate_id=entry.candidate_id,
        symbol="RB0.SHFE",
        contract_code="RB2605.SHF",
        datetime=bar.bar_end,
        direction=1,
        offset=Offset.OPEN,
        quantity=4,
        reference_price=100.0,
        fill_price=109.0,
        source_bar_end=bar.bar_end,
        exchange_trade_date=date(2026, 1, 5),
    )
    intents = coordinator.on_trade("RB0.SHFE", fill)

    assert any(
        intent.order_id == entry.order_id and intent.action is OrderAction.CANCEL
        for intent in intents
    )
    assert any(intent.order_type is OrderType.MARKET_EXIT for intent in intents)
    post_fill = coordinator.snapshot().risk_decisions[-1]
    assert post_fill["phase"] == "post_fill"
    assert post_fill["reason"] == "entry_risk_breach"
    assert post_fill["planned_open_risk"] > post_fill["risk_budget"]
    decision_count = len(coordinator.snapshot().risk_decisions)
    assert coordinator.on_trade("RB0.SHFE", fill) == []
    assert len(coordinator.snapshot().risk_decisions) == decision_count


def test_coordinator_cancels_entry_when_risk_stops_before_trigger() -> None:
    day = _session("day", time(9, 0), time(15, 0), night=False)
    coordinator = BrooksScalpPortfolioCoordinator(initial_equity=200_000.0, mode="historical")
    engine = ScalpEngine(
        _instrument("RB", day),
        _fee("RB", "RB2605.SHF"),
        initial_cash=200_000.0,
    )
    coordinator.register_symbol("RB0.SHFE", engine, _calendar((day,)))
    candidate = _candidate("RB0.SHFE", "RB2605.SHF")
    plan = TradePlan(100.0, 95.0, 130.0, 140.0, 5.0, 50.0, 1.10, 8.0, 0.0)
    risk = RiskSnapshot(
        current_risk_pct=0.0025,
        session_return=0.0,
        trade_date_return=0.0,
        peak_profit=0.0,
        giveback_cny=0.0,
        drawdown_pct=0.0,
        symbol_consecutive_losses=0,
        portfolio_consecutive_losses=0,
        cooldown_until=None,
        symbol_stopped=False,
        portfolio_stopped=False,
        daily_locked=False,
    )
    intent, _ = coordinator.submit_candidate(
        candidate,
        plan,
        risk,
        {"exchange_trade_date": date(2026, 1, 5)},
    )
    assert intent is not None
    coordinator.update_risk_snapshot(
        "RB0.SHFE",
        replace(risk, portfolio_stopped=True),
    )

    coordinator.on_symbol_bar(
        "RB0.SHFE",
        _bar(
            "2026-01-05 09:01",
            symbol="RB0.SHFE",
            root="RB",
            contract="RB2605.SHF",
            open_=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
        ),
    )

    snapshot = engine.snapshot()
    assert snapshot.position is None
    entry = next(order for order in snapshot.orders if order.order_id == intent.order_id)
    assert entry.status.value == "CANCELLED"
    assert coordinator.snapshot().risk_decisions[-1]["reason"] == "trigger_risk_stop"


def test_closed_rb_does_not_block_active_cu_night_watermark() -> None:
    day = _session("day", time(9, 0), time(15, 0), night=False)
    night = _session("night", time(21, 0), time(1, 0), night=True)
    coordinator = BrooksScalpPortfolioCoordinator(initial_equity=200_000.0, mode="historical")
    coordinator.register_symbol(
        "RB0.SHFE",
        ScalpEngine(_instrument("RB", day), _fee("RB", "RB2605.SHF")),
        _calendar((day,)),
    )
    coordinator.register_symbol(
        "CU0.SHFE",
        ScalpEngine(_instrument("CU", night), _fee("CU", "CU2605.SHF")),
        _calendar((night,)),
    )
    bar = _bar(
        "2026-01-04 23:00",
        symbol="CU0.SHFE",
        root="CU",
        contract="CU2605.SHF",
        open_=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        session_open="2026-01-04 21:00",
        session_close="2026-01-05 01:00",
        session_id="20260105:night",
    )
    expected = coordinator.expected_active_symbols(bar.bar_start, bar.bar_end)
    assert expected == ("CU0.SHFE",)

    coordinator.on_symbol_bar("CU0.SHFE", bar)
    snapshot = coordinator.snapshot()
    assert snapshot.pending_watermarks == ()
    assert snapshot.stale_symbols == ()


def test_live_bar_adapter_requires_daily_metadata_and_preserves_limits() -> None:
    day = _session("day", time(9, 0), time(15, 0), night=False)
    instrument = _instrument("RB", day)
    calendar = _calendar((day,))
    known_at = _ts("2025-01-01 00:00")
    daily = SimpleNamespace(
        contract_code="RB2605.SHF",
        exchange_trade_date=date(2026, 1, 5),
        pre_settlement=100.0,
        limit_up=110.0,
        limit_down=90.0,
        source="fixture",
        known_at=known_at,
    )
    adapter = LiveBarAdapter(
        symbol="RB2605.SHFE",
        contract_code="RB2605.SHF",
        instrument=instrument,
        calendar=calendar,
        daily_spec_provider=lambda contract, trade_date: daily,
    )
    raw = SimpleNamespace(
        datetime=_ts("2026-01-05 09:00"),
        open_price=100.0,
        high_price=101.0,
        low_price=99.0,
        close_price=100.5,
        volume=10.0,
        turnover=10_000.0,
        open_interest=1_000.0,
    )
    bar = adapter.convert(raw)
    assert bar.bar_end == _ts("2026-01-05 09:01")
    assert bar.exchange_trade_date == date(2026, 1, 5)
    assert bar.limit_up == 110.0
    assert bar.limit_known_at == known_at


def test_online_dry_run_fails_closed_without_manifest(tmp_path: Path, capsys) -> None:
    exit_code = online_main(
        [
            "--symbols",
            "RB0.SHFE",
            "CU0.SHFE",
            "--meta-root",
            str(tmp_path / "missing-meta"),
            "--dry-run",
        ]
    )
    assert exit_code == 2
    assert "BLOCKED_METADATA" in capsys.readouterr().out


def test_live_close_offsets_preserve_shfe_today_yesterday_inventory() -> None:
    assert _vn_offset(Offset.CLOSETODAY) is VnOffset.CLOSETODAY
    assert _vn_offset(Offset.CLOSEYESTERDAY) is VnOffset.CLOSEYESTERDAY
    assert _vn_offset(Offset.CLOSE) is VnOffset.CLOSE
