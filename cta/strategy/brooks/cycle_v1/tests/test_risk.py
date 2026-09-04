from __future__ import annotations

from dataclasses import replace
from datetime import date
import numpy as np
import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.core.risk.portfolio import (
    PortfolioRiskSnapshot,
    PortfolioRiskGuard,
    calculate_open_risk,
    drawdown_risk_multiplier,
    estimate_correlation_clusters,
    portfolio_allows,
)
from cta.strategy.brooks.cycle_v1.core.risk.sizing import size_position
from cta.strategy.brooks.cycle_v1.core.risk.stops import (
    allow_break_even,
    ratchet_stop,
)
from cta.strategy.brooks.cycle_v1.core.types import MarketCycle


def test_position_size_uses_stop_loss_cost_and_lot_step() -> None:
    decision = size_position(
        equity=200_000.0,
        entry=100.0,
        stop=90.0,
        contract_multiplier=10.0,
        estimated_entry_cost=5.0,
        stressed_exit_cost=10.0,
        lot_step=1,
        cycle=MarketCycle.STRONG_BULL_BREAKOUT,
        large_confidence=0.8,
        medium_confidence=0.8,
        drawdown_multiplier=1.0,
        new_order_risk_multiplier=1.0,
        config=load_config(),
    )

    assert decision.risk_budget == pytest.approx(4_000.0)
    assert decision.loss_per_lot == pytest.approx(115.0)
    assert decision.quantity == 34


def test_production_tight_channel_budget_is_one_point_six_percent() -> None:
    config = load_config()
    decision = size_position(
        equity=200_000.0,
        entry=100.0,
        stop=90.0,
        contract_multiplier=10.0,
        estimated_entry_cost=5.0,
        stressed_exit_cost=10.0,
        lot_step=1,
        cycle=MarketCycle.BULL_TIGHT_CHANNEL,
        large_confidence=0.8,
        medium_confidence=0.8,
        drawdown_multiplier=1.0,
        new_order_risk_multiplier=1.0,
        config=config,
    )

    assert config.risk.risk_per_trade == pytest.approx(0.02)
    assert config.risk.max_trade_risk == pytest.approx(0.02)
    assert decision.risk_budget == pytest.approx(3_200.0)


def test_one_lot_over_budget_is_rejected_without_narrowing_stop() -> None:
    decision = size_position(
        equity=20_000.0,
        entry=100.0,
        stop=80.0,
        contract_multiplier=100.0,
        estimated_entry_cost=5.0,
        stressed_exit_cost=10.0,
        lot_step=1,
        cycle=MarketCycle.BULL_BROAD_CHANNEL,
        large_confidence=0.8,
        medium_confidence=0.8,
        drawdown_multiplier=1.0,
        new_order_risk_multiplier=1.0,
        config=load_config(),
    )

    assert decision.quantity == 0
    assert decision.reason == "ONE_LOT_EXCEEDS_RISK_BUDGET"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_position_size_rejects_nonfinite_inputs(invalid: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        size_position(
            equity=200_000.0,
            entry=invalid,
            stop=90.0,
            contract_multiplier=10.0,
            estimated_entry_cost=5.0,
            stressed_exit_cost=10.0,
            lot_step=1,
            cycle=MarketCycle.BULL_BROAD_CHANNEL,
            large_confidence=0.8,
            medium_confidence=0.8,
            drawdown_multiplier=1.0,
            new_order_risk_multiplier=1.0,
            config=load_config(),
        )


def test_portfolio_limits_apply_symbol_sector_total_and_margin() -> None:
    snapshot = PortfolioRiskSnapshot(
        equity=200_000.0,
        symbol_open_risk={"RB": 3_900.0},
        sector_open_risk={"BLACK": 7_900.0},
        total_open_risk=9_900.0,
        margin_used_and_reserved=79_500.0,
        day_start_adjusted_equity=200_000.0,
        adjusted_equity=198_000.0,
        high_watermark_equity=220_000.0,
    )

    decision = portfolio_allows(
        snapshot,
        symbol="RB",
        sector="BLACK",
        incremental_open_risk=200.0,
        incremental_margin=1_000.0,
        config=load_config(),
    )

    assert not decision.allowed
    assert set(decision.reason_codes) == {
        "MAX_SYMBOL_OPEN_RISK",
        "MAX_SECTOR_OPEN_RISK",
        "MAX_TOTAL_OPEN_RISK",
        "MAX_MARGIN_UTILIZATION",
    }


def test_drawdown_multiplier_and_stop_management_only_reduce_risk() -> None:
    config = load_config()
    mid = (config.risk.max_drawdown_soft + config.risk.max_drawdown_hard) / 2

    assert drawdown_risk_multiplier(mid, config) == pytest.approx(0.625)
    assert allow_break_even(
        running_mfe_R=1.2,
        favorable_structure_confirmed=True,
        remaining_space_R=1.5,
        config=config,
    )
    assert ratchet_stop(direction=1, current_stop=90.0, proposed_stop=88.0) == 90.0
    assert ratchet_stop(direction=-1, current_stop=110.0, proposed_stop=112.0) == 110.0


def test_unexecutable_stop_keeps_limit_lock_stress_in_open_risk() -> None:
    nominal = calculate_open_risk(
        direction=1,
        quantity=1,
        mark_price=100.0,
        executable_stop_price=90.0,
        contract_multiplier=10.0,
        stressed_exit_cost=5.0,
        stop_executable=True,
        locked_limit_stress_price=80.0,
    )
    locked = calculate_open_risk(
        direction=1,
        quantity=1,
        mark_price=100.0,
        executable_stop_price=90.0,
        contract_multiplier=10.0,
        stressed_exit_cost=5.0,
        stop_executable=False,
        locked_limit_stress_price=80.0,
    )

    assert nominal == 105.0
    assert locked == 205.0


def test_dynamic_correlation_cluster_is_a_second_concentration_gate() -> None:
    snapshot = PortfolioRiskSnapshot(
        equity=200_000.0,
        correlation_cluster_open_risk={"CORR:HC|RB": 9_900.0},
        symbol_to_correlation_cluster={"RB": "CORR:HC|RB", "HC": "CORR:HC|RB"},
        day_start_adjusted_equity=200_000.0,
        adjusted_equity=200_000.0,
        high_watermark_equity=200_000.0,
    )

    decision = portfolio_allows(
        snapshot,
        symbol="RB",
        sector="BLACK",
        incremental_open_risk=200.0,
        incremental_margin=0.0,
        config=load_config(),
    )

    assert not decision.allowed
    assert "MAX_CORRELATION_CLUSTER_OPEN_RISK" in decision.reason_codes


def test_correlation_clusters_use_only_supplied_trailing_observations() -> None:
    rng = np.random.default_rng(7)
    rb = rng.normal(0.0, 0.01, 100)
    returns = pd.DataFrame(
        {
            "RB": rb,
            "HC": rb * 0.95 + rng.normal(0.0, 0.0005, 100),
            "CU": rng.normal(0.0, 0.01, 100),
        }
    )

    clusters = estimate_correlation_clusters(returns, load_config())

    assert clusters["RB"] == clusters["HC"]
    assert clusters["CU"] != clusters["RB"]


def test_correlation_clusters_block_when_pairwise_history_is_insufficient() -> None:
    returns = pd.DataFrame(
        {
            "RB": np.linspace(-0.01, 0.01, 10),
            "HC": np.linspace(-0.02, 0.02, 10),
        }
    )

    with pytest.raises(ValueError, match="BLOCKED_CORRELATION_DATA"):
        estimate_correlation_clusters(returns, load_config())


def test_unavailable_portfolio_risk_snapshot_fails_closed() -> None:
    snapshot = PortfolioRiskSnapshot(
        equity=200_000.0,
        day_start_adjusted_equity=200_000.0,
        adjusted_equity=200_000.0,
        high_watermark_equity=200_000.0,
        risk_available=False,
        risk_block_reason="BLOCKED_RISK_DATA_GAP",
    )

    decision = portfolio_allows(
        snapshot,
        symbol="CU",
        sector="NONFERROUS",
        incremental_open_risk=0.0,
        incremental_margin=0.0,
        config=load_config(),
    )

    assert not decision.allowed
    assert decision.reason_codes == ("BLOCKED_RISK_DATA_GAP",)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_nonfinite_portfolio_risk_never_fails_open(invalid: float) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        PortfolioRiskSnapshot(
            equity=200_000.0,
            total_open_risk=invalid,
            day_start_adjusted_equity=200_000.0,
            adjusted_equity=200_000.0,
            high_watermark_equity=200_000.0,
        )

    snapshot = PortfolioRiskSnapshot(
        equity=200_000.0,
        day_start_adjusted_equity=200_000.0,
        adjusted_equity=200_000.0,
        high_watermark_equity=200_000.0,
    )
    with pytest.raises(ValueError, match="finite and nonnegative"):
        portfolio_allows(
            snapshot,
            symbol="RB",
            sector="BLACK",
            incremental_open_risk=invalid,
            incremental_margin=0.0,
            config=load_config(),
        )


def test_correlation_cluster_rejects_ambiguous_nonstring_symbols() -> None:
    returns = pd.DataFrame({1: [0.01, 0.02], "1": [0.01, 0.02]})

    with pytest.raises(ValueError, match="nonempty strings"):
        estimate_correlation_clusters(returns, load_config())


def test_daily_loss_gate_stays_latched_until_next_exchange_trade_date() -> None:
    guard = PortfolioRiskGuard(load_config())
    base = PortfolioRiskSnapshot(
        equity=200_000.0,
        day_start_adjusted_equity=200_000.0,
        adjusted_equity=193_500.0,
        high_watermark_equity=200_000.0,
    )

    breached = guard.apply(date(2026, 1, 5), base)
    recovered = guard.apply(
        date(2026, 1, 5),
        replace(base, equity=200_000.0, adjusted_equity=200_000.0),
    )
    next_day = guard.apply(
        date(2026, 1, 6),
        replace(
            base,
            equity=200_000.0,
            day_start_adjusted_equity=200_000.0,
            adjusted_equity=200_000.0,
        ),
    )

    assert breached.daily_loss_latched
    assert recovered.daily_loss_latched
    assert not next_day.daily_loss_latched
