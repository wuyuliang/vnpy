# Trade Trend Entry Audit Columns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add trend-segment first-fill and trigger-to-prior-five-day-high percentage audit fields to final multi-timeframe trend `trades.csv` rows.

**Architecture:** Reuse the replay engine's existing per-root `filled_bull_trend_ids` state. Determine first-fill status immediately before a successful open, freeze it and the candidate-price ratio on `_Position`, and copy both values into the final logical trade without post-processing or changing execution behavior.

**Tech Stack:** Python 3, pandas, pytest

---

## File Structure

- Modify `cta/strategy/multi_timeframe_trend_backtest/engine.py`: add output columns, calculate and freeze open-time audit values, and emit them from final trade aggregation.
- Modify `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`: add column-order, first-fill state, ratio, portfolio isolation, invalid-price, and CSV assertions.

Both implementation files are existing untracked user files. Do not create an implementation commit that would absorb their unrelated history.

### Task 1: Define and test the open-time audit values

**Files:**
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`

- [ ] **Step 1: Add failing tests**

Add assertions that `TRADE_COLUMNS` contains the following immediately after `cycle`:

```python
(
    "is_first_trade_in_trend_segment",
    "trigger_to_prior_5d_high_ratio",
)
```

Extend `test_replay_breakout_buffer_tracks_fills_and_restarts_for_new_trend` so its two completed trades in the same `daily_bull_trend_id=1` assert first-fill values `[1, 0]`. Assert their ratios are approximately `[0.100001, 0.0]` from `100 * (trigger / prior_5d_high - 1)`.

Add a focused test with buffer ratio `0` and nonzero entry slippage. Use `trigger=100.1`, `prior_5d_high=100.0`, then assert the audit ratio is approximately `0.1` even though actual `entry_price` differs from `trigger`. This proves the ratio uses the candidate trigger.

Add a new-trend replay with one completed trade in trend id `1` followed by one completed trade in trend id `2`; assert both first-fill values are `1`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run the exact new/extended test nodes with `python3 -m pytest -q`. Expected failures: the new trade columns do not exist.

- [ ] **Step 3: Add fields and pure calculations**

In `TRADE_COLUMNS`, insert the two fields immediately after `cycle`. Add to `_Position`:

```python
is_first_trade_in_trend_segment: int
trigger_to_prior_5d_high_ratio: float
```

Add a small first-fill helper near `_first_trend_entry_breakout_buffer_reason`:

```python
def _is_first_trade_in_trend_segment(
    candidate: dict[str, Any],
    filled_bull_trend_ids: set[int],
) -> int:
    return int(
        int(candidate["direction"]) > 0
        and int(candidate["daily_bull_trend_id"]) not in filled_bull_trend_ids
    )
```

In `_open_position`, accept `is_first_trade_in_trend_segment`. Calculate the ratio from `pending.candidate["trigger"]` and `pending.candidate["prior_5d_high"]`; return `math.nan` when either is non-finite or the prior high is non-positive. Store both values on `_Position`.

- [ ] **Step 4: Run the focused tests**

Expected: ratio and column tests may pass, while replay tests remain RED until both open call sites and final aggregation are wired.

### Task 2: Wire single and portfolio replay and final CSV

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Pass first-fill state at both open call sites**

Immediately before each `_open_position` call, calculate first-fill status using the candidate and that root's existing set. Pass it to `_open_position`. Keep the existing `filled_bull_trend_ids.add(...)` statement after `_open_position` succeeds, so rejected or quantity-zero candidates never consume first status.

- [ ] **Step 2: Emit frozen values from final logical trades**

In `_close_position` final `trade` dictionary, add:

```python
"is_first_trade_in_trend_segment": position.is_first_trade_in_trend_segment,
"trigger_to_prior_5d_high_ratio": position.trigger_to_prior_5d_high_ratio,
```

Do not add the fields to exit legs or calculate them during report publishing.

- [ ] **Step 3: Cover portfolio isolation and final CSV**

Extend `test_replay_trend_portfolio_isolates_returns_by_symbol_and_contract` to assert both AG and AU trades are first trades in their independent trend state. In `test_report_contains_metrics_command_and_audit_tables`, assert the two written `trades.csv` columns immediately follow `cycle` and contain the same values as `artifacts.trades`.

Add invalid trigger/prior-high cases at the smallest pure boundary available and assert ratio `NaN`; do not relax candidate validation or execution filters.

- [ ] **Step 4: Run the target test module**

Run:

```bash
python3 -m pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py
```

Expected: all tests pass.

### Task 3: Regression verification

**Files:**
- Verify only.

- [ ] **Step 1: Run all multi-timeframe trend tests**

```bash
python3 -m pytest -q \
  cta/strategy/tests/test_multi_timeframe_trend_strategy.py \
  cta/strategy/tests/test_multi_timeframe_trend_management.py \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py
```

Expected: all tests pass; before this feature the fresh baseline is `173 passed`.

- [ ] **Step 2: Inspect output order and scope**

Print the `TRADE_COLUMNS` slice around `cycle` and confirm exact ordering. Inspect edited regions to confirm no changes to signal generation, filters, fills, stops, position sizing, equity, fees, or report metrics.

- [ ] **Step 3: Report evidence**

Report the final test count, field names, `1/0` semantics, and percentage formula. State that no historical backtest was rerun unless one was actually executed.
