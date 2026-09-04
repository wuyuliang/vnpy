from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest.replay import (
    PlanExecutionEngine,
    SubmittedPlan,
    _decision_rejection_rows,
    _handle_contract_transition,
    _candidate_from_core,
    _raw_execution_features,
    execute_order_plans,
    replay_cycle_strategy,
)
from cta.strategy.brooks.cycle_v1.core.strategy import RejectionAudit
from cta.strategy.brooks.cycle_v1.backtest.scanner import scan_loaded_symbols
from cta.strategy.brooks.cycle_v1.backtest.timeframes import TimeframeSet
from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.instruments.metadata import BlockedMetadataError
from cta.strategy.brooks.cycle_v1.tests.test_execution import _features, _meta, _plan
from cta.strategy.brooks.cycle_v1.tests.test_cycle_scanner import (
    _loaded_symbol,
    _small_config,
)


def _execution_meta():
    return _meta(
        daily=SimpleNamespace(margin_rate_long=0.10, margin_rate_short=0.10),
    )


def test_candidate_rejection_audit_preserves_risk_budget_and_loss_per_lot() -> None:
    decision = SimpleNamespace(
        rejections=(
            "ONE_LOT_EXCEEDS_RISK_BUDGET",
            "ONE_LOT_EXCEEDS_RISK_BUDGET",
        ),
        rejection_audits=(
            RejectionAudit(
                rejection_index=1,
                reason_code="ONE_LOT_EXCEEDS_RISK_BUDGET",
                candidate_id="candidate-cu",
                risk_budget=400.0,
                loss_per_lot=2_554.1,
                detail="large_cycle=BULL_TIGHT_CHANNEL",
            ),
        ),
    )

    rows = _decision_rejection_rows("CU", pd.Timestamp("2026-07-14"), decision)

    assert rows == [
        {
            "root_symbol": "CU",
            "feature_asof": pd.Timestamp("2026-07-14"),
            "reason_code": "ONE_LOT_EXCEEDS_RISK_BUDGET",
            "candidate_id": "",
            "risk_budget": None,
            "loss_per_lot": None,
            "detail": "",
        },
        {
            "root_symbol": "CU",
            "feature_asof": pd.Timestamp("2026-07-14"),
            "reason_code": "ONE_LOT_EXCEEDS_RISK_BUDGET",
            "candidate_id": "candidate-cu",
            "risk_budget": 400.0,
            "loss_per_lot": 2_554.1,
            "detail": "large_cycle=BULL_TIGHT_CHANNEL",
        }
    ]


def test_replay_uses_next_event_actual_contract_and_adverse_ambiguous_exit() -> None:
    plan = _plan()
    bars = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2026-01-05 09:06", "2026-01-05 09:07"]
            ).tz_localize("Asia/Shanghai"),
            "feature_sequence": [100, 101],
            "open": [100.0, 100.0],
            "high": [102.0, 120.0],
            "low": [99.0, 89.0],
            "close": [101.0, 105.0],
            "contract_code": ["RB2605.SHF", "RB2605.SHF"],
            "exchange_trade_date": [
                pd.Timestamp("2026-01-05").date(),
                pd.Timestamp("2026-01-05").date(),
            ],
        }
    )

    result = execute_order_plans(
        submitted=(
            SubmittedPlan(
                plan=plan,
                metadata=_execution_meta(),
                features=_features(),
                root_symbol="RB",
                sector="BLACK",
            ),
        ),
        minute_bars=bars,
        config=load_config(),
        initial_equity=200_000.0,
    )

    assert list(result.fills["fill_kind"]) == ["ENTRY", "EXIT"]
    assert list(result.fills["reason"]) == ["FILLED", "STOP_AMBIGUOUS_ADVERSE"]
    trade = result.trades.iloc[0]
    assert trade["contract_code"] == "RB2605.SHF"
    assert trade["entry_price"] == 101.0
    assert trade["exit_price"] == 89.0
    assert trade["fees"] == 10.0
    assert trade["slippage"] == 40.0
    assert trade["net_pnl"] == -250.0
    assert result.daily_equity.iloc[-1]["equity"] == 199_750.0


def test_zero_trade_replay_still_has_requested_daily_equity() -> None:
    bars = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2026-01-05 15:00", "2026-01-06 15:00"]
            ).tz_localize("Asia/Shanghai"),
            "feature_sequence": [1, 2],
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.0, 101.0],
            "contract_code": ["RB2605.SHF", "RB2605.SHF"],
            "exchange_trade_date": [
                pd.Timestamp("2026-01-05").date(),
                pd.Timestamp("2026-01-06").date(),
            ],
        }
    )

    result = execute_order_plans(
        submitted=(),
        minute_bars=bars,
        config=load_config(),
        initial_equity=200_000.0,
    )

    assert result.trades.empty
    assert {"order_id", "candidate_id", "entry", "stop", "target"}.issubset(
        result.plans
    )
    assert {"order_id", "status", "reason"}.issubset(result.orders)
    assert {"fill_kind", "fill_time", "price"}.issubset(result.fills)
    assert {"symbol", "net_pnl", "fees", "slippage"}.issubset(result.trades)
    assert list(result.daily_equity["date"]) == [
        pd.Timestamp("2026-01-05").date(),
        pd.Timestamp("2026-01-06").date(),
    ]
    assert result.daily_equity["equity"].eq(200_000.0).all()


def test_stateful_executor_exposes_current_portfolio_before_new_decisions() -> None:
    plan = _plan()
    item = SubmittedPlan(
        plan=plan,
        metadata=_execution_meta(),
        features=_features(),
        root_symbol="RB",
        sector="BLACK",
    )
    engine = PlanExecutionEngine(config=load_config(), initial_equity=200_000.0)
    engine.submit(item)

    assert engine.has_root_exposure("RB")
    snapshot = engine.portfolio_snapshot(
        trade_date=pd.Timestamp("2026-01-05").date()
    )

    assert snapshot.equity == 200_000.0
    assert snapshot.margin_used_and_reserved == 0.0
    assert snapshot.total_open_risk == 0.0


def test_stateful_executor_fills_one_oco_side_and_cancels_its_peer() -> None:
    base = _plan()
    long_plan = replace(base, order_id="long", oco_group_id="range-1")
    short_plan = replace(
        base,
        order_id="short",
        oco_group_id="range-1",
        candidate=replace(
            base.candidate,
            candidate_id="short-candidate",
            direction=-1,
            trigger_price=99.0,
            initial_stop=109.0,
            target_price=81.0,
        ),
        geometry=replace(base.geometry, entry=99.0, stop=109.0, target=81.0),
    )
    engine = PlanExecutionEngine(config=load_config(), initial_equity=200_000.0)
    for plan in (long_plan, short_plan):
        engine.submit(
            SubmittedPlan(
                plan=plan,
                metadata=_execution_meta(),
                features=_features(),
                root_symbol="RB",
                sector="BLACK",
            )
        )
    bar = pd.Series(
        {
            "bar_end": pd.Timestamp("2026-01-05 09:06", tz="Asia/Shanghai"),
            "feature_sequence": 100,
            "open": 100.0,
            "high": 102.0,
            "low": 99.5,
            "close": 101.0,
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": pd.Timestamp("2026-01-05").date(),
        }
    )

    engine.on_bar(bar, bar_index=0)

    assert "RB2605.SHF" in engine.ledger.positions
    terminal = pd.DataFrame(engine.order_rows).groupby("order_id").tail(1)
    assert terminal.set_index("order_id").loc["long", "status"] == "FILLED"
    assert terminal.set_index("order_id").loc["short", "status"] == "CANCELLED"


def test_roll_exit_closes_old_actual_contract_and_cancels_pending_orders() -> None:
    plan = _plan()
    item = SubmittedPlan(
        plan=plan,
        metadata=_execution_meta(),
        features=_features(),
        root_symbol="RB",
        sector="BLACK",
    )
    engine = PlanExecutionEngine(config=load_config(), initial_equity=200_000.0)
    engine.submit(item)
    entry_bar = pd.Series(
        {
            "bar_end": pd.Timestamp("2026-01-05 09:06", tz="Asia/Shanghai"),
            "feature_sequence": 100,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": pd.Timestamp("2026-01-05").date(),
        }
    )
    engine.on_bar(entry_bar, bar_index=0)
    roll_bar = entry_bar.copy()
    roll_bar["bar_end"] = pd.Timestamp(
        "2026-01-05 09:07", tz="Asia/Shanghai"
    )
    roll_bar["feature_sequence"] = 101
    roll_bar["close"] = 103.0

    engine.force_close_root(
        "RB",
        bar=roll_bar,
        bar_index=1,
        reason="ROLL_EXIT",
    )
    engine.cancel_root_pending("RB", reason="ROLL_MAPPING_CHANGED")

    assert not engine.ledger.positions
    assert engine.fill_rows[-1]["reason"] == "ROLL_EXIT"
    assert engine.trade_rows[-1]["contract_code"] == "RB2605.SHF"


def test_contract_switch_never_retroactively_closes_old_contract() -> None:
    plan = _plan()
    engine = PlanExecutionEngine(config=load_config(), initial_equity=200_000.0)
    engine.submit(
        SubmittedPlan(
            plan=plan,
            metadata=_execution_meta(),
            features=_features(),
            root_symbol="RB",
            sector="BLACK",
        )
    )
    entry_bar = pd.Series(
        {
            "bar_end": pd.Timestamp("2026-01-05 09:06", tz="Asia/Shanghai"),
            "feature_sequence": 100,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": pd.Timestamp("2026-01-05").date(),
        }
    )
    engine.on_bar(entry_bar, bar_index=0)
    active_contracts = {"RB": "RB2605.SHF"}

    with pytest.raises(BlockedMetadataError, match="BLOCKED_ROLL_EXECUTION_BAR"):
        _handle_contract_transition(
            engine,
            root_symbol="RB",
            contract_code="RB2610.SHF",
            active_contracts=active_contracts,
        )

    assert "RB2605.SHF" in engine.ledger.positions
    assert engine.trade_rows == []


def test_adjusted_signal_levels_are_mapped_back_to_actual_contract_prices() -> None:
    signal = {
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "prior_high": 101.0,
        "prior_low": 95.0,
        "range_high": 104.0,
        "range_low": 94.0,
        "range_mid": 99.0,
        "latest_confirmed_swing_high": 103.0,
        "latest_confirmed_swing_low": 96.0,
        "confirmed_directional_swing_obstacles": {1: (105.0,), -1: (93.0,)},
        "completed_higher_tf_obstacles": {1: (106.0,), -1: (92.0,)},
        "atr": 2.5,
        "body": 2.0,
        "adjustment_scale": 1.0,
        "adjustment_offset": -20.0,
    }

    actual = _raw_execution_features(signal)

    assert actual["open"] == 120.0
    assert actual["close"] == 122.0
    assert actual["range_mid"] == 119.0
    assert actual["latest_confirmed_swing_low"] == 116.0
    assert actual["confirmed_directional_swing_obstacles"] == {
        1: (125.0,),
        -1: (113.0,),
    }
    assert actual["completed_higher_tf_obstacles"] == {
        1: (126.0,),
        -1: (112.0,),
    }
    assert actual["atr"] == 2.5
    assert actual["body"] == 2.0
    assert signal["close"] == 102.0


def test_candidate_audit_contains_executable_geometry() -> None:
    candidate = _plan().candidate

    row = _candidate_from_core("RB", candidate)

    assert row["entry"] == candidate.trigger_price
    assert row["stop"] == candidate.initial_stop
    assert row["target"] == candidate.target_price
    assert row["trade_mode"] == candidate.trade_mode.value


def test_strategy_replay_preserves_zero_trade_daily_denominator() -> None:
    loaded = _loaded_symbol()
    loaded.minute_bars["close"] = 100.0
    timeframes = TimeframeSet.from_values("15min", "5min", "1min")
    config = _small_config()
    start = pd.Timestamp("2026-01-05").date()
    end = pd.Timestamp("2026-01-16").date()
    scan = scan_loaded_symbols(
        (loaded,),
        config=config,
        timeframes=timeframes,
        start=start,
        end=end,
    )

    result = replay_cycle_strategy(
        (loaded,),
        metadata_store=object(),
        universe_daily=scan.universe_daily,
        config=config,
        timeframes=timeframes,
        start=start,
        end=end,
        initial_equity=200_000.0,
    )

    assert result.candidates.empty
    assert result.trades.empty
    assert len(result.daily_equity) == 10
    assert result.daily_equity["equity"].eq(200_000.0).all()
