from __future__ import annotations

import pytest

from cta.strategy.common.sizing import size_for_risk_band


def test_risk_band_uses_the_maximum_risk_quantity() -> None:
    decision = size_for_risk_band(
        equity=100_000.0,
        entry=100.0,
        stop=110.0,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        min_risk_pct=0.002,
        max_risk_pct=0.005,
        max_capital_share=1.0,
        margin_rate=0.1,
    )

    assert decision.quantity == 5
    assert decision.risk_budget == pytest.approx(500.0)
    assert decision.loss_per_lot == pytest.approx(100.0)


def test_capital_cap_uses_margin_when_available() -> None:
    decision = size_for_risk_band(
        equity=100_000.0,
        entry=1_000.0,
        stop=1_010.0,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        min_risk_pct=0.002,
        max_risk_pct=0.005,
        max_capital_share=0.10,
        margin_rate=0.1,
    )

    assert decision.quantity == 5
    assert decision.capital_basis == "margin"
    assert decision.capital_share == pytest.approx(0.05)


def test_capital_cap_falls_back_to_notional_and_can_reject_minimum_risk() -> None:
    decision = size_for_risk_band(
        equity=100_000.0,
        entry=1_000.0,
        stop=1_010.0,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        min_risk_pct=0.002,
        max_risk_pct=0.005,
        max_capital_share=0.10,
        margin_rate=None,
    )

    assert decision.quantity == 0
    assert decision.reason == "RISK_BELOW_MINIMUM"
    assert decision.capital_basis == "notional"


def test_one_lot_above_maximum_risk_is_allowed_when_capital_permits() -> None:
    decision = size_for_risk_band(
        equity=10_000.0,
        entry=100.0,
        stop=110.0,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        min_risk_pct=0.002,
        max_risk_pct=0.005,
        max_capital_share=1.0,
        margin_rate=0.1,
    )

    assert decision.quantity == 1
    assert decision.reason == ""


def test_quantity_is_rounded_up_to_reach_the_minimum_risk() -> None:
    decision = size_for_risk_band(
        equity=100_000.0,
        entry=100.0,
        stop=130.0,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        min_risk_pct=0.002,
        max_risk_pct=0.005,
        max_capital_share=1.0,
        margin_rate=0.1,
    )

    assert decision.quantity == 1
    assert decision.quantity * decision.loss_per_lot >= 200.0


def test_capital_cap_can_still_reject_the_one_lot_fallback() -> None:
    decision = size_for_risk_band(
        equity=10_000.0,
        entry=100_000.0,
        stop=100_010.0,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        min_risk_pct=0.002,
        max_risk_pct=0.005,
        max_capital_share=0.1,
        margin_rate=None,
    )

    assert decision.quantity == 0
    assert decision.reason == "CAPITAL_LIMIT"


# ---------------------------------------------------------------------------
# 一手放宽档：10% 装不下时允许一手占到 15%，超过就不开
# ---------------------------------------------------------------------------
def _size(entry, **overrides):
    from cta.strategy.common.sizing import size_for_risk_band

    kwargs = dict(
        equity=1_000_000.0,
        entry=entry,
        stop=entry * 1.02,
        multiplier=1.0,
        stressed_round_trip_cost=0.0,
        min_risk_pct=0.002,
        max_risk_pct=0.005,
        max_capital_share=0.10,
        margin_rate=None,
        single_lot_capital_share=0.15,
    )
    kwargs.update(overrides)
    return size_for_risk_band(**kwargs)


def test_single_lot_relief_opens_one_lot_between_the_two_caps() -> None:
    decision = _size(120_000.0)          # 一手占 12%
    assert decision.quantity == 1
    assert decision.single_lot_relief is True
    assert decision.capital_share == pytest.approx(0.12)
    assert decision.reason == ""


def test_single_lot_relief_refuses_beyond_the_wider_cap() -> None:
    decision = _size(180_000.0)          # 一手占 18%
    assert decision.quantity == 0
    assert decision.single_lot_relief is False
    assert decision.reason == "CAPITAL_LIMIT"


def test_normal_cap_still_binds_when_one_lot_fits() -> None:
    decision = _size(50_000.0)           # 一手占 5%，常规档能放两手
    assert decision.quantity == 2
    assert decision.single_lot_relief is False
    assert decision.capital_share == pytest.approx(0.10)


def test_relief_is_off_when_the_wider_cap_is_not_given() -> None:
    decision = _size(120_000.0, single_lot_capital_share=None)
    assert decision.quantity == 0
    assert decision.reason == "CAPITAL_LIMIT"


def test_relief_ignores_the_minimum_risk_floor() -> None:
    """放宽档只有"一手"一个选项，不能再用"风险太小"去否掉它。"""
    decision = _size(120_000.0, stop=120_000.0 * 1.0005)   # 一手风险仅 0.006%
    assert decision.quantity == 1
    assert decision.single_lot_relief is True
    assert decision.reason == ""
