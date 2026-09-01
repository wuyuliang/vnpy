# Trend Exit And Partial Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove executable 2R exits from pullback breakouts, draw a virtual Target on every opportunity chart, and reduce only the minimum required lots ten minutes before the day close.

**Architecture:** Keep one logical trade per candidate while introducing auditable exit legs inside the existing event-driven replay. Every exit leg realizes cash and portfolio scaling immediately; the final leg aggregates the candidate into one `trades.csv` row, while symbol streak state advances only once. Virtual targets are stored separately from executable exits and are used only by plans, trade audit fields, and charts.

**Tech Stack:** Python 3.13, dataclasses, pandas, NumPy, Pillow, pytest, Ruff.

---

### Task 1: Make Both Setups Trailing-Stop Only

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_management.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_management.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Write failing management tests**

Add tests asserting that `open_position_from_fill` returns a non-executable target for `pullback_breakout`, that reaching 2R does not return `ExitReason.TARGET`, and that a later confirmed swing raises the pullback stop exactly as it raises an Always-In stop.

```python
state = open_position_from_fill(candidate, actual_entry_price=101.0, config=config)
assert math.isnan(state.target_price)

updated, decision = manage_position_on_bar(
    state,
    bar_with_high_above_2r_and_confirmed_swing,
    daily_direction=1,
    config=config,
    tick_size=1.0,
)
assert not decision.should_exit
assert updated.stop_price == pytest.approx(102.0)
```

- [ ] **Step 2: Write a failing replay test**

Replace the fixed-target expectation with a path that reaches 2R, remains open, then exits through a later confirmed trailing stop. Assert `exit_reason == "STOP"`, no `TARGET` rows exist, and `target_exit_enabled == 0`.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_management.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k 'pullback and (target or trailing)' -q
```

Expected: failures showing a finite pullback target or `TARGET` exit.

- [ ] **Step 4: Implement trailing-only management**

In `open_position_from_fill`, validate both setup names but always initialize `target_price=math.nan`. In `manage_position_on_bar`, remove target-hit evaluation and allow both supported setup types to pass through the confirmed-swing trailing update.

In the replay `_Position`, replace executable `target` with a finite `reference_target` calculated by `two_r_target` for both setup types. Make `_protective_exit_touched` and `_protective_exit` inspect only the stop. Emit `final_target=position.reference_target` and `target_exit_enabled=0` in the final trade.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run the command from Step 3. Expected: all selected tests pass.

### Task 2: Draw Target On Every Opportunity Chart

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_strategy.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/charts.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Write failing candidate and chart tests**

Assert that both `always_in` and `pullback_breakout` candidates have finite `target_price_virtual` while `target_price` remains `NaN`. Add a chart test where an untraded candidate has only `target_price_virtual` and verify `_numeric_level`/chart index resolves it as the target.

```python
assert candidates["target_price"].isna().all()
assert np.isfinite(candidates["target_price_virtual"]).all()
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k 'target or chart' -q
```

Expected: Always-In virtual target is missing or chart fallback is non-finite.

- [ ] **Step 3: Implement virtual target propagation**

Calculate `target_price_virtual` with `two_r_target(candidate.trigger, candidate.stop_price, candidate.direction, config.pullback_target_r)` for every candidate. Keep `target_price=np.nan`.

Make `_plan_row` use candidate `target_price_virtual` as a reference value. In charts, resolve target using this order:

```python
target = _numeric_level(
    candidate,
    "target",
    "final_target",
    "target_price_virtual",
    "target_price",
)
```

Apply a finite `final_target` override to every traded setup, not only pullback breakouts. Pass the same resolved target to all three chart panels.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 3: Add Exit-Leg Accounting Without Duplicating Trades

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/report.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Write failing exit-leg aggregation tests**

Create a position with two exit legs: one same-day partial reduction and one later final exit. Assert immediate leg cash, one final trade row, original total quantity, weighted exit price, summed PnL/fees/slippage/turnover, and distinct close types in `exit_legs`.

```python
assert len(artifacts.exit_legs) == 2
assert artifacts.exit_legs["quantity"].tolist() == [1, 3]
assert artifacts.exit_legs["close_type"].tolist() == ["CLOSE_TODAY", "CLOSE"]
assert len(artifacts.trades) == 1
assert artifacts.trades.iloc[0]["quantity"] == 4
assert artifacts.trades.iloc[0]["net_pnl"] == pytest.approx(
    artifacts.exit_legs["net_pnl"].sum()
)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k 'exit_leg or partial_exit' -q
```

Expected: `ReplayArtifacts` has no `exit_legs` or a partial exit removes the whole position.

- [ ] **Step 3: Implement the exit-leg model**

Add `EXIT_LEG_COLUMNS` and `ReplayArtifacts.exit_legs`. Add `initial_quantity`, `reference_target`, and an `exit_legs` list to `_Position`.

Refactor `_close_position` into a quantity-aware function:

```python
def _close_position(
    position: _Position,
    *,
    quantity: int | None,
    exit_metadata: Any,
    timestamp: pd.Timestamp,
    price: float,
    reference: float,
    reason: str,
    bar_index: int,
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    close_quantity = position.quantity if quantity is None else quantity
    # Build one fee-complete leg, append it, reduce position.quantity.
    # Return an aggregate trade only when position.quantity reaches zero.
```

The aggregate trade must sum every leg and use quantity-weighted exit price. Mixed fee fields become `NaN` or `"MIXED"`; full per-leg values remain in `exit_legs.csv`.

- [ ] **Step 4: Split dynamic-scaling updates by scope**

Add a portfolio-realization helper that receives each leg's actual `net_pnl` and cash immediately. Call symbol scaling once with aggregate trade PnL only on the final leg. This prevents partial exits from counting as multiple symbol wins or losses while keeping portfolio realized-cash recovery causal.

- [ ] **Step 5: Publish exit-leg audit output**

Add `exit_legs.csv` to `publish_backtest_report`. Build `fee_audit.csv` from exit legs so mixed close-today/close schedules remain auditable. Update `_empty_replay` and `_blocked_replay` to preserve the new frame.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 4: Reduce Minimum Lots Ten Minutes Before Close

**Files:**
- Modify: `cta/config/multi_timeframe_trend_config.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Write failing timing and quantity tests**

Change the default-config assertion to `overnight_reduction_minutes == 10`. Build minute bars through 14:51 and a four-lot position where one lot is enough to restore the 20% limit. Assert decision at 14:50, one-lot exit at 14:51, and three lots retained until a later final exit.

Add a multi-symbol case showing newest-entry-first selection and a partially reduced final selected symbol. Add a lock case that retries without duplicating the pending quantity.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k 'overnight_reduction or portfolio_risk_config_defaults' -q
```

Expected: default remains 30 and whole positions are exited.

- [ ] **Step 3: Implement quantity-aware exit intents**

Keep `pending_market_exit` for reasons and add `pending_exit_quantity`. At cutoff, compute:

```python
excess = max(0.0, planned_margin - margin_limit)
margin_per_lot = position_margin / position.quantity
required = math.ceil(excess / margin_per_lot - 1e-12)
close_quantity = min(position.quantity, required)
```

Subtract only pending/selected lots from `planned_margin`. Execute `OVERNIGHT_MARGIN_REDUCTION` with `close_quantity`; retain the position when quantity remains. Recalculate during the cutoff window after each fill. If a protective stop is touched on the same bar, cancel the reduction intent and close all remaining lots through the stop.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 5: Update Chinese Strategy Documentation And Report Assertions

**Files:**
- Modify: `cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md`
- Modify: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Update the strategy rules**

Replace fixed 2R exit language with trailing-only management. Define `target_price_virtual` as a chart reference for both setups. Replace whole-position overnight reduction with close-minus-ten-minutes minimum-lot reduction and document `exit_legs.csv`.

- [ ] **Step 2: Update report-bundle tests**

Assert that reports contain `exit_legs.csv`, that fee audit totals match exit-leg totals, and that one candidate with partial exits contributes one round trip.

- [ ] **Step 3: Run report tests**

Run:

```bash
python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k 'report or runner or fee' -q
```

Expected: all selected tests pass.

### Task 6: Full Verification And AG Backtest Audit

**Files:**
- Verify all modified production and test files.
- Generate a new directory under `cta/strategy/report/multi_timeframe_trend/`.

- [ ] **Step 1: Run the complete related test suite**

```bash
python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_management.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run static and syntax checks**

```bash
python3 -m ruff check cta/config/multi_timeframe_trend_config.py cta/strategy/multi_timeframe_trend_management.py cta/strategy/multi_timeframe_trend_strategy.py cta/strategy/multi_timeframe_trend_backtest cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_management.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py
python3 -m compileall -q cta/config/multi_timeframe_trend_config.py cta/strategy/multi_timeframe_trend_management.py cta/strategy/multi_timeframe_trend_strategy.py cta/strategy/multi_timeframe_trend_backtest
```

Expected: both commands exit 0.

- [ ] **Step 3: Run the AG backtest**

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner --symbols AG --start 2026-01-01 --end 2026-02-05 --initial-equity 1000000 --download-minute-data
```

Expected: report status `COMPLETE` with `exit_legs.csv`, all three chart panels containing finite Target levels, and no pullback trade exiting with `TARGET`.

- [ ] **Step 4: Audit AG-000109 and report deltas**

Verify its cutoff is around 14:50, its first reduction closes only the required lots, retained lots remain in the position, leg totals equal the final trade, and peak overnight utilization returns to the configured limit after executable fills. Compare return, maximum drawdown, trade count, and exit reasons against `20260902_000628_719118_20260101_20260205_1d_5m_1m` without treating the old path as a counterfactual guarantee.
