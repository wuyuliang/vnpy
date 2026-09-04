# Multi-Timeframe Trend Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved daily-direction/5-minute-entry long-short trend strategy as causal, testable candidate-generation and trade-management code.

**Architecture:** Keep the strategy independent of vn.py and split it into a validated configuration, reusable causal rules, and a candidate-generation facade. Reuse tracked EMA/ATR and risk-sizing helpers; expose baseline-compatible candidate columns so the existing model and backtest layers can consume the output.

**Tech Stack:** Python 3.10+, pandas, NumPy, pytest, existing `cta.skills` indicators and Brooks risk helper.

---

### Task 1: Configuration And Causal Features

**Files:**
- Create: `cta/config/multi_timeframe_trend_config.py`
- Create: `cta/strategy/multi_timeframe_trend_rules.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

- [ ] **Step 1: Write failing configuration and feature tests**

```python
def test_config_rejects_risk_above_two_percent():
    with pytest.raises(ValueError, match="risk_per_trade"):
        MultiTimeframeTrendConfig(risk_per_trade=0.021)

def test_daily_context_uses_strict_ema_order():
    context = build_daily_context(daily_bars, config)
    assert context.iloc[-1]["daily_direction"] in {-1, 0, 1}

def test_confirmed_pivot_is_unknown_until_right_bars_close():
    enriched = attach_confirmed_pivots(five_minute_bars, left=2, right=2)
    assert pd.isna(enriched.loc[pivot_position + 1, "latest_swing_low"])
    assert enriched.loc[pivot_position + 2, "latest_swing_low"] == pivot_price
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: import failure because the new modules do not exist.

- [ ] **Step 3: Implement minimal validated config and features**

Implement `MultiTimeframeTrendConfig`, `build_daily_context`, `build_intraday_context`, `attach_confirmed_pivots`, and completed-bar `align_daily_context`. Reuse `cta.skills.trend_strategies._common.ema` and `atr`; do not import untracked `cycle_v1` modules.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: feature tests pass.

### Task 2: Always-In And Pullback-Breakout Candidates

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_rules.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

- [ ] **Step 1: Write failing setup tests**

```python
def test_always_in_long_requires_four_higher_highs_and_uses_swing_stop():
    candidate = detect_always_in(frame, index, direction=1, config=config, tick_size=1)
    assert candidate.trigger == max(frame.iloc[index-5:index+1].high) + 1
    assert candidate.stop_price == latest_swing_low - 0.2 * frame.iloc[index].atr14

def test_pullback_breakout_requires_prior_volume_quantile():
    candidate = detect_pullback_breakout(frame, index, state, config, tick_size=1)
    assert candidate is not None
    assert candidate.setup_type == "pullback_breakout"
```

- [ ] **Step 2: Run setup tests and verify RED**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: missing setup detector failures.

- [ ] **Step 3: Implement symmetric causal detectors**

Add immutable `SignalCandidate` and `PullbackState`. Detect 6-bar/4-progress Always-In continuation and 3-12 bar pullback breakouts. The volume threshold uses only the previous 20 completed bars. All short rules mirror long rules.

- [ ] **Step 4: Run setup tests and verify GREEN**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: long/short and volume exclusion tests pass.

### Task 3: Obstacles, Risk, Targets, And Trailing Stops

**Files:**
- Create: `cta/strategy/multi_timeframe_trend_management.py`
- Modify: `cta/strategy/multi_timeframe_trend_rules.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_management.py`

- [ ] **Step 1: Write failing risk and management tests**

```python
def test_near_daily_resistance_rejects_long_candidate():
    reason = obstacle_rejection(candidate, daily_history, config)
    assert reason == "HTF_OBSTACLE_NEAR"

def test_sizing_includes_stressed_round_trip_cost():
    decision = size_for_risk(equity=100_000, entry=100, stop=98,
                             multiplier=10, stressed_round_trip_cost=5,
                             risk_per_trade=0.01)
    assert decision.quantity == 40

def test_trailing_stop_never_widens():
    assert advance_trailing_stop(100, 1, 99, 2, 0.2, 1) == 100

def test_pullback_target_is_two_r_and_symmetric():
    assert two_r_target(100, 98, 1, 2) == 104
    assert two_r_target(100, 102, -1, 2) == 96
```

- [ ] **Step 2: Run risk tests and verify RED**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: missing risk/management helper failures.

- [ ] **Step 3: Implement minimal risk and management helpers**

Use the tracked Brooks `calc_position_size` by converting stressed costs to an effective price distance. Reject one-lot-over-budget trades and invalid structural stops. Round entry and stop prices outward by tick role.

- [ ] **Step 4: Run risk tests and verify GREEN**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: all risk and management tests pass.

### Task 4: Candidate Generation Facade And Regression Verification

**Files:**
- Create: `cta/strategy/multi_timeframe_trend_strategy.py`
- Modify: `cta/strategy/readme.md`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

- [ ] **Step 1: Write failing end-to-end candidate tests**

```python
def test_generate_candidates_emits_baseline_compatible_audit_fields():
    out = generate_multi_timeframe_candidates(daily, minute5, instrument, config)
    required = {"datetime", "signal_datetime", "signal_type", "side",
                "trigger", "stop_price", "target_price", "candidate_status",
                "filtered_reason", "feature_asof", "known_at", "order_active_at"}
    assert required.issubset(out.columns)

def test_future_daily_rows_do_not_change_earlier_candidates():
    prefix = generate_multi_timeframe_candidates(daily.iloc[:-1], minute5_prefix, instrument, config)
    full = generate_multi_timeframe_candidates(daily, minute5, instrument, config)
    pd.testing.assert_frame_equal(prefix, full.loc[full.datetime <= cutoff].reset_index(drop=True))
```

- [ ] **Step 2: Run facade tests and verify RED**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: strategy facade import/function failure.

- [ ] **Step 3: Implement the generation facade**

Add `InstrumentSpec` and `generate_multi_timeframe_candidates`. Emit accepted and filtered setup candidates, apply pullback-over-Always-In priority, set next-event activation time, and retain rejection reasons. Add the new strategy to the strategy README index.

- [ ] **Step 4: Run focused and adjacent regression tests**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_baseline_split_modules.py cta/skills/trend_strategies/tests/test_ma_trend_following.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Run static checks**

Run: `ruff check cta/config/multi_timeframe_trend_config.py cta/strategy/multi_timeframe_trend_rules.py cta/strategy/multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

Expected: no lint errors.
