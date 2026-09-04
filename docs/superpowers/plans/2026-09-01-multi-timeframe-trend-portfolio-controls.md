# Multi-Timeframe Trend Portfolio Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the trend backtest to a reproducible multi-symbol shared-account replay with margin caps, exact effective-dated fees, and outcome-classified `1d/1h/5min` charts.

**Architecture:** Keep signal generation isolated per symbol, then merge symbol minute streams into one deterministic portfolio event loop. Extend the existing execution metadata snapshot with fee components so the trend replay can charge entry-day open fees and exit-day close/close-today fees without inventing costs.

**Tech Stack:** Python 3, pandas, NumPy, matplotlib, pytest, Ruff.

---

### Task 1: Configuration and CLI contract

**Files:**
- Modify: `cta/config/multi_timeframe_trend_config.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] Add failing tests for defaults and validation of `0.60/0.20/0.40`, maximum five positions, and 30-minute reduction lead time.
- [ ] Run the focused tests and confirm failures are caused by missing fields/options.
- [ ] Add the minimal frozen config fields, CLI arguments, and reproduction-command serialization.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Effective-dated fee components

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/instruments/metadata.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/execution_metadata.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_execution_metadata_adapter.py`

- [ ] Add failing tests proving same-trading-day exits use close-today fees and later exits use the exit-day close fee.
- [ ] Run the tests and confirm the old entry-snapshot round-trip charge fails both cases.
- [ ] Expose six raw fee components on execution costs/snapshots while retaining the conservative round-trip field for risk sizing.
- [ ] Compute entry and exit fees from their actual prices, dates, and effective metadata snapshots; add fee audit columns.
- [ ] Run both test files and confirm they pass.

### Task 3: Shared-account portfolio replay

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] Add failing tests for two symbols competing for shared margin, the 40% symbol cap, the 60% intraday cap, and the five-position cap.
- [ ] Run the tests and confirm failures reflect the existing one-position engine.
- [ ] Replace scalar pending/position state with symbol-keyed state and one stable timestamp event loop while preserving existing single-symbol behavior.
- [ ] Floor fill quantity against risk, per-symbol margin, and remaining portfolio margin; record explicit rejection codes.
- [ ] Run all trend backtest tests and confirm they pass.

### Task 4: Overnight reduction

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] Add a failing session-aware test where positions exceed 20% thirty minutes before day close.
- [ ] Confirm the old engine carries the excess position.
- [ ] Cancel new-entry orders for the affected symbol and close newest positions first until marked overnight utilization is at most 20%, recording `OVERNIGHT_MARGIN_REDUCTION`.
- [ ] Run the overnight and complete trend test suites.

### Task 5: Chart timeframes and outcome directories

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/charts.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] Add failing tests for `1d/1h/5min` panels, `TRADED` and reason-code directories, globally ordered symbol-bearing filenames, and one index row per candidate.
- [ ] Run the chart tests and confirm the current flat `1d/5min/1min` output fails.
- [ ] Aggregate session-aware 60-minute actual bars, classify each candidate from replay artifacts, and write charts/index rows into deterministic outcome paths.
- [ ] Run the chart and full trend tests.

### Task 6: Portfolio reporting and verification

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/report.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Modify: `cta/strategy/docs/multi_timeframe_trend_strategy.md`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] Add failing assertions for portfolio utilization metrics, fee audit output, per-symbol results, and all new parameters in run context.
- [ ] Implement only the report fields and CSVs required by the design.
- [ ] Run `pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q` and the affected Brooks metadata tests.
- [ ] Run Ruff and `compileall` on modified modules.
- [ ] Run the AG command and a small multi-symbol `--top-n` command; reconcile candidate/chart counts, margin peaks, fees, and reproduction commands.
