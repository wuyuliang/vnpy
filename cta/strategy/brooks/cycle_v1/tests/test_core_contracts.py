from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.config import RiskConfig, load_config
from cta.strategy.brooks.cycle_v1.core.types import (
    EventKey,
    MarketCycle,
    SetupCandidate,
    SetupType,
    TradeMode,
    assert_event_order,
)


TZ = "Asia/Shanghai"


def _ts(value: str):
    return pd.Timestamp(value, tz=TZ).to_pydatetime()


def test_default_config_has_frozen_schema_and_expected_v0_setups() -> None:
    config = load_config()

    assert config.schema == "brooks_cycle_v1"
    assert config.version == "cycle_v1"
    assert config.features.atr_period == 14
    assert config.cycle.enter_score == pytest.approx(0.65)
    assert config.profile.small_tf_candidates == ("1m",)
    assert config.profile.medium_tf_candidates == ("5m",)
    assert config.profile.large_tf_candidates == ("30m",)
    assert config.setup.wedge_min_spacing == 2
    assert config.enabled_setups == frozenset(
        {SetupType.BREAKOUT, SetupType.H1, SetupType.H2, SetupType.FAILED_BREAKOUT}
    )
    assert len(config.config_hash) == 64


def test_risk_config_defaults_match_two_percent_production_budget() -> None:
    risk = RiskConfig()

    assert risk.risk_per_trade == pytest.approx(0.02)
    assert risk.max_trade_risk == pytest.approx(0.02)


def test_config_rejects_unknown_schema(tmp_path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("schema: wrong\nversion: cycle_v1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="schema"):
        load_config(config_path)


def test_config_rejects_unsafe_risk_and_cycle_ranges() -> None:
    config = load_config()

    with pytest.raises(ValueError, match="correlation_threshold"):
        replace(
            config,
            risk=replace(config.risk, correlation_threshold=1.1),
        )
    with pytest.raises(ValueError, match="range zones"):
        replace(
            config,
            cycle=replace(config.cycle, range_lower_zone=0.8),
        )


def test_event_order_uses_sequence_when_timestamps_are_equal() -> None:
    end = _ts("2026-01-05 09:05")
    feature = EventKey(end, 10)
    known = EventKey(end, 20)
    decision = EventKey(end, 30)
    active = EventKey(end, 40)
    fill = EventKey(_ts("2026-01-05 09:06"), 1)

    assert_event_order(feature, known, decision, active, fill)

    with pytest.raises(ValueError, match="event ordering"):
        assert_event_order(feature, known, decision, EventKey(end, 30), fill)


def test_candidate_id_is_stable_and_changes_with_rule_inputs() -> None:
    candidate = SetupCandidate.create(
        rule_version="cycle_v1",
        vt_symbol="RB0.SHFE",
        contract_code="RB2605.SHF",
        setup_type=SetupType.BREAKOUT,
        direction=1,
        context_cycle=MarketCycle.STRONG_BULL_BREAKOUT,
        signal_event=EventKey(_ts("2026-01-05 09:05"), 10),
        known_event=EventKey(_ts("2026-01-05 09:05"), 20),
        decision_event=EventKey(_ts("2026-01-05 09:05"), 30),
        active_event=EventKey(_ts("2026-01-05 09:06"), 1),
        entry_type="STOP",
        trigger_price=3510.0,
        initial_stop=3480.0,
        target_price=3570.0,
        trade_mode=TradeMode.SWING,
        expected_holding_bars=(2, 30),
        evidence={"breakout_distance": 0.4},
        invalidation={"lost_level": 3490.0},
    )

    duplicate = replace(candidate, candidate_id="").with_stable_id("cycle_v1")
    changed = replace(candidate, trigger_price=3511.0, candidate_id="").with_stable_id(
        "cycle_v1"
    )

    assert candidate.candidate_id == duplicate.candidate_id
    assert candidate.candidate_id != changed.candidate_id
    assert len(candidate.candidate_id) == 24


def test_candidate_rejects_noncausal_activation() -> None:
    end = _ts("2026-01-05 09:05")

    with pytest.raises(ValueError, match="event ordering"):
        SetupCandidate.create(
            rule_version="cycle_v1",
            vt_symbol="CU0.SHFE",
            contract_code="CU2603.SHF",
            setup_type=SetupType.H1,
            direction=1,
            context_cycle=MarketCycle.BULL_TIGHT_CHANNEL,
            signal_event=EventKey(end, 10),
            known_event=EventKey(end, 20),
            decision_event=EventKey(end, 30),
            active_event=EventKey(end, 30),
            entry_type="STOP",
            trigger_price=80_000.0,
            initial_stop=79_500.0,
            target_price=81_000.0,
            trade_mode=TradeMode.SWING,
            expected_holding_bars=(2, 30),
            evidence={},
            invalidation={},
        )
