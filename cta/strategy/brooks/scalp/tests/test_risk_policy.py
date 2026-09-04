from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.scalp.config import load_config
from cta.strategy.brooks.scalp.risk_policy import (
    AccountSnapshot,
    PortfolioRiskState,
    RiskEvaluationContext,
    RiskSnapshot,
    RuleOnlyRiskAdapter,
    calculate_order_size,
)


NOW = pd.Timestamp("2026-01-05 10:00", tz="Asia/Shanghai").to_pydatetime()


def _candidate(**overrides):
    candidate = {
        "candidate_id": "C1",
        "symbol": "RB0.SHFE",
        "contract_code": "RB2605.SHF",
        "cluster": "black",
        "direction": 1,
        "offset": "open",
        "trigger_price": 3_000.0,
        "net_stop_loss_per_contract": 200.0,
        "margin_rate": 0.12,
        "volume_ratio": 1.0,
        "turnover_activity_ratio": 1.0,
        "realized_vol_pctl_252_30m": 50.0,
        "volatility_sample_count": 252,
        "last_trade_date": date(2026, 5, 15),
        "main_switch_date": date(2025, 12, 1),
        "trading_days_to_expiry": 90,
        "trading_days_since_switch": 20,
        "roll_freeze": False,
        "daily_limit_known": True,
        "market_data_stale": False,
        "marked_equity_daily_pnl": 0.0,
        "marked_equity_daily_pnl_by_cluster": {"black": 0.0},
        "execution_quality_state": "INACTIVE_WARMUP",
        "execution_quality_sample_count": 0,
    }
    candidate.update(overrides)
    return candidate


def _account(**overrides) -> AccountSnapshot:
    values = {
        "marked_equity": 200_000.0,
        "margin_used_and_reserved": 0.0,
        "portfolio_open_risk": 0.0,
        "symbol_open_risk": {},
    }
    values.update(overrides)
    return AccountSnapshot(**values)


def _risk(**overrides) -> RiskSnapshot:
    values = {
        "current_risk_pct": 0.0025,
        "session_return": 0.0,
        "trade_date_return": 0.0,
        "peak_profit": 0.0,
        "giveback_cny": 0.0,
        "drawdown_pct": 0.0,
        "symbol_consecutive_losses": 0,
        "portfolio_consecutive_losses": 0,
        "cooldown_until": None,
        "symbol_stopped": False,
        "portfolio_stopped": False,
        "daily_locked": False,
    }
    values.update(overrides)
    return RiskSnapshot(**values)


def _context(**risk_overrides) -> RiskEvaluationContext:
    return RiskEvaluationContext(
        now=NOW,
        exchange_trade_date=date(2026, 1, 5),
        account=_account(),
        risk=_risk(**risk_overrides),
    )


def test_order_size_uses_500_cny_base_risk_and_floors_lots() -> None:
    spec = SimpleNamespace(contract_size=10.0, lot_step=1)
    decision = calculate_order_size(_candidate(), _account(), spec, _risk())
    assert decision.risk_budget == pytest.approx(500.0)
    assert decision.quantity == 2

    too_risky = calculate_order_size(
        _candidate(net_stop_loss_per_contract=501.0),
        _account(),
        spec,
        _risk(),
    )
    assert too_risky.quantity == 0
    assert too_risky.reason == "qty_zero"


def test_order_size_enforces_symbol_portfolio_risk_and_entry_margin() -> None:
    spec = SimpleNamespace(contract_size=10.0, lot_step=1)
    account = _account(
        portfolio_open_risk=850.0,
        symbol_open_risk={"RB0.SHFE": 450.0},
    )
    decision = calculate_order_size(_candidate(), account, spec, _risk())
    assert decision.quantity == 0

    margin_full = calculate_order_size(
        _candidate(),
        _account(margin_used_and_reserved=60_000.0),
        spec,
        _risk(),
    )
    assert margin_full.quantity == 0


@pytest.mark.parametrize(
    "missing_field",
    [
        "volume_ratio",
        "turnover_activity_ratio",
        "realized_vol_pctl_252_30m",
        "last_trade_date",
        "main_switch_date",
        "trading_days_to_expiry",
        "trading_days_since_switch",
        "daily_limit_known",
        "marked_equity_daily_pnl",
        "marked_equity_daily_pnl_by_cluster",
        "execution_quality_state",
    ],
)
def test_missing_or_nonfinite_required_input_rejects_new_entry(missing_field: str) -> None:
    adapter = RuleOnlyRiskAdapter(load_config(), mode="backtest")
    candidate = _candidate()
    candidate.pop(missing_field)
    decision = adapter.evaluate_entry(candidate, _context(), proposed_quantity=2)
    assert decision.allowed is False
    assert decision.quantity == 0
    assert "missing_input" in decision.reason


def test_liquidity_and_live_spread_are_fail_closed() -> None:
    backtest = RuleOnlyRiskAdapter(load_config(), mode="backtest")
    low_volume = backtest.evaluate_entry(
        _candidate(volume_ratio=0.29), _context(), proposed_quantity=2
    )
    assert low_volume.allowed is False
    assert "liquidity_floor" in low_volume.reason

    live = RuleOnlyRiskAdapter(load_config(), mode="live")
    missing_spread = live.evaluate_entry(_candidate(), _context(), proposed_quantity=2)
    assert missing_spread.allowed is False
    assert "bid_ask_spread_ticks" in missing_spread.reason


def test_component_exception_rejects_open_but_never_blocks_close() -> None:
    class ExplodingGuard:
        def check(self, order, ctx):
            del order, ctx
            raise RuntimeError("boom")

    adapter = RuleOnlyRiskAdapter(
        load_config(), mode="backtest", liquidity_guard=ExplodingGuard()
    )
    rejected = adapter.evaluate_entry(_candidate(), _context(), proposed_quantity=2)
    assert rejected.allowed is False
    assert "component_exception" in rejected.reason

    close = adapter.evaluate(
        _candidate(offset="close"), _context(), proposed_quantity=2
    )
    assert close.allowed is True
    assert close.quantity == 2


def test_soft_loss_and_drawdown_scale_quantity_before_hard_stops() -> None:
    adapter = RuleOnlyRiskAdapter(load_config(), mode="backtest")
    soft = adapter.evaluate_entry(
        _candidate(), _context(session_return=-0.006), proposed_quantity=4
    )
    assert soft.allowed is True
    assert soft.quantity == 2

    drawdown = adapter.evaluate_entry(
        _candidate(), _context(drawdown_pct=0.03), proposed_quantity=4
    )
    assert drawdown.allowed is True
    assert drawdown.quantity == 2

    both = adapter.evaluate_entry(
        _candidate(),
        _context(session_return=-0.006, drawdown_pct=0.03),
        proposed_quantity=4,
    )
    assert both.quantity == 1


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"trade_date_return": -0.01}, "daily_hard_loss"),
        ({"drawdown_pct": 0.05}, "drawdown_stop"),
        ({"drawdown_pct": 0.08}, "drawdown_kill"),
        ({"symbol_consecutive_losses": 3}, "symbol_loss_stop"),
        ({"portfolio_consecutive_losses": 4}, "portfolio_loss_stop"),
        ({"daily_locked": True}, "daily_locked"),
    ],
)
def test_hard_risk_states_reject_new_entry(overrides: dict, reason: str) -> None:
    adapter = RuleOnlyRiskAdapter(load_config(), mode="backtest")
    decision = adapter.evaluate_entry(
        _candidate(), _context(**overrides), proposed_quantity=2
    )
    assert decision.allowed is False
    assert reason in decision.reason


def test_fixed_equity_point_giveback_locks_day() -> None:
    adapter = RuleOnlyRiskAdapter(load_config(), mode="backtest")
    decision = adapter.evaluate_entry(
        _candidate(),
        _context(peak_profit=0.01, giveback_cny=800.0),
        proposed_quantity=2,
    )
    assert decision.allowed is False
    assert "profit_giveback" in decision.reason


def test_rollover_windows_use_exchange_trading_day_counts() -> None:
    adapter = RuleOnlyRiskAdapter(load_config(), mode="backtest")

    far_by_exchange_days = adapter.evaluate_entry(
        _candidate(
            last_trade_date=date(2026, 1, 7),
            trading_days_to_expiry=8,
        ),
        _context(),
        proposed_quantity=2,
    )
    assert far_by_exchange_days.allowed is True

    force_close = adapter.evaluate_entry(
        _candidate(
            last_trade_date=date(2026, 5, 15),
            trading_days_to_expiry=3,
        ),
        _context(),
        proposed_quantity=2,
    )
    assert force_close.reason == "rollover_force_close_window"

    switched_today = adapter.evaluate_entry(
        _candidate(
            main_switch_date=date(2025, 12, 1),
            trading_days_since_switch=0,
        ),
        _context(),
        proposed_quantity=2,
    )
    assert switched_today.reason == "rollover_post_switch_freeze"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"last_trade_date": "not-a-date"}, "invalid_input:last_trade_date"),
        ({"daily_limit_known": "true"}, "invalid_input:daily_limit_known"),
        ({"roll_freeze": "false"}, "invalid_input:roll_freeze"),
    ],
)
def test_local_risk_inputs_fail_closed_without_raising(
    overrides: dict,
    reason: str,
) -> None:
    adapter = RuleOnlyRiskAdapter(load_config(), mode="backtest")
    decision = adapter.evaluate_entry(
        _candidate(**overrides),
        _context(),
        proposed_quantity=2,
    )
    assert decision.allowed is False
    assert decision.reason == reason


def test_adapter_components_receive_every_frozen_yaml_value() -> None:
    config = load_config()
    adapter = RuleOnlyRiskAdapter(config, mode="backtest")
    assert adapter.liquidity_guard.cfg.volume_floor_ratio == config.risk_components.liquidity.volume_floor_ratio
    assert adapter.liquidity_guard.cfg.min_turnover_ratio == config.risk_components.liquidity.min_turnover_ratio
    assert tuple(adapter.volatility_scaler.cfg.mults) == config.risk_components.volatility.mults
    assert adapter.daily_var_scaler.cfg.account_budget_bp == config.risk_components.daily_var.account_budget_bp
    assert adapter.execution_quality_scaler.cfg.min_trades_for_assessment == (
        config.risk_components.execution_quality.min_trades_for_assessment
    )


def test_invalid_number_is_rejected_not_coerced_to_default() -> None:
    adapter = RuleOnlyRiskAdapter(load_config(), mode="backtest")
    decision = adapter.evaluate_entry(
        _candidate(volume_ratio=float("nan")), _context(), proposed_quantity=2
    )
    assert decision.allowed is False
    assert "nonfinite_input" in decision.reason


def test_active_execution_quality_requires_explicit_causal_metrics() -> None:
    adapter = RuleOnlyRiskAdapter(load_config(), mode="backtest")
    missing = adapter.evaluate_entry(
        _candidate(
            execution_quality_state="ACTIVE",
            execution_quality_sample_count=20,
        ),
        _context(),
        proposed_quantity=4,
    )
    assert missing.allowed is False
    assert "execution_slippage_ratio" in missing.reason

    degraded = adapter.evaluate_entry(
        _candidate(
            execution_quality_state="ACTIVE",
            execution_quality_sample_count=20,
            execution_slippage_ratio=3.1,
            execution_reject_rate=0.0,
            execution_avg_price_deviation_pct=0.0,
        ),
        _context(),
        proposed_quantity=4,
    )
    assert degraded.allowed is True
    assert degraded.quantity == 2


def test_risk_snapshots_are_immutable() -> None:
    snapshot = _risk()
    changed = replace(snapshot, drawdown_pct=0.03)
    assert snapshot.drawdown_pct == 0.0
    assert changed.drawdown_pct == 0.03


def test_portfolio_risk_state_uses_marked_equity_for_session_and_daily_locks() -> None:
    state = PortfolioRiskState(load_config(), symbols=("RB0.SHFE", "CU0.SHFE"))
    state.update_mark(
        now=NOW,
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=200_000.0,
        session_ids={"RB0.SHFE": "20260105:day", "CU0.SHFE": "20260105:day"},
    )
    state.update_mark(
        now=NOW + pd.Timedelta(minutes=1),
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=198_800.0,
        session_ids={"RB0.SHFE": "20260105:day", "CU0.SHFE": "20260105:day"},
    )
    soft = state.snapshot("RB0.SHFE")
    assert soft.session_return == pytest.approx(-0.006)
    assert soft.daily_locked is False

    flatten = state.update_mark(
        now=NOW + pd.Timedelta(minutes=2),
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=198_000.0,
        session_ids={"RB0.SHFE": "20260105:day", "CU0.SHFE": "20260105:day"},
    )
    assert flatten is True
    assert state.snapshot("CU0.SHFE").daily_locked is True


def test_portfolio_risk_state_tracks_profit_giveback_and_consecutive_losses() -> None:
    state = PortfolioRiskState(load_config(), symbols=("RB0.SHFE", "CU0.SHFE"))
    state.update_mark(
        now=NOW,
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=200_000.0,
        session_ids={"RB0.SHFE": "20260105:day", "CU0.SHFE": "20260105:day"},
    )
    state.update_mark(
        now=NOW + pd.Timedelta(minutes=1),
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=202_000.0,
        session_ids={"RB0.SHFE": "20260105:day", "CU0.SHFE": "20260105:day"},
    )
    assert state.update_mark(
        now=NOW + pd.Timedelta(minutes=2),
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=201_200.0,
        session_ids={"RB0.SHFE": "20260105:day", "CU0.SHFE": "20260105:day"},
    )
    assert state.snapshot("RB0.SHFE").giveback_cny == pytest.approx(800.0)
    assert state.snapshot("RB0.SHFE").daily_locked

    losses = PortfolioRiskState(load_config(), symbols=("RB0.SHFE", "CU0.SHFE"))
    losses.update_mark(
        now=NOW,
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=200_000.0,
        session_ids={"RB0.SHFE": "20260105:day", "CU0.SHFE": "20260105:day"},
    )
    losses.record_closed_trade("RB0.SHFE", -10.0, NOW)
    losses.record_closed_trade("RB0.SHFE", -10.0, NOW + pd.Timedelta(minutes=1))
    cooled = losses.snapshot("RB0.SHFE")
    assert cooled.symbol_consecutive_losses == 2
    assert cooled.cooldown_until == NOW + pd.Timedelta(minutes=31)
    losses.record_closed_trade("RB0.SHFE", -10.0, NOW + pd.Timedelta(minutes=2))
    losses.record_closed_trade("CU0.SHFE", -10.0, NOW + pd.Timedelta(minutes=3))
    assert losses.snapshot("RB0.SHFE").symbol_stopped
    assert losses.snapshot("CU0.SHFE").portfolio_stopped


def test_drawdown_stop_lasts_five_session_transitions_but_eight_percent_is_hard_kill() -> None:
    state = PortfolioRiskState(load_config(), symbols=("RB0.SHFE",))
    state.update_mark(
        now=NOW,
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=200_000.0,
        session_ids={"RB0.SHFE": "s1"},
    )
    state.update_mark(
        now=NOW + pd.Timedelta(minutes=1),
        exchange_trade_date=date(2026, 1, 5),
        marked_equity=190_000.0,
        session_ids={"RB0.SHFE": "s1"},
    )
    assert state.snapshot("RB0.SHFE").portfolio_stopped

    for offset, session_id in enumerate(("s2", "s3", "s4", "s5", "s6"), start=1):
        state.update_mark(
            now=NOW + pd.Timedelta(days=offset),
            exchange_trade_date=date(2026, 1, 5) + pd.Timedelta(days=offset),
            marked_equity=190_000.0,
            session_ids={"RB0.SHFE": session_id},
        )
    assert state.snapshot("RB0.SHFE").portfolio_stopped is False
    assert state.snapshot("RB0.SHFE").drawdown_pct == pytest.approx(0.05)

    state.update_mark(
        now=NOW + pd.Timedelta(days=6),
        exchange_trade_date=date(2026, 1, 11),
        marked_equity=184_000.0,
        session_ids={"RB0.SHFE": "s7"},
    )
    assert state.snapshot("RB0.SHFE").portfolio_stopped
    state.update_mark(
        now=NOW + pd.Timedelta(days=20),
        exchange_trade_date=date(2026, 1, 25),
        marked_equity=200_000.0,
        session_ids={"RB0.SHFE": "s20"},
    )
    assert state.snapshot("RB0.SHFE").portfolio_stopped
