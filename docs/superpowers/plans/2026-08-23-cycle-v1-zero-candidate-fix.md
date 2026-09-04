# Cycle V1 Zero-Candidate Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore causal cycle_v1 candidates by completing the approved point-in-time continuous signal series while preserving actual-contract execution and fail-closed metadata behavior.

**Architecture:** Build additive roll adjustments from the old and new contracts' SHFE `pre_settlement` values that are known no later than the new mapping session open. Store adjusted signal OHLC and immutable adjustment audit fields beside raw actual-contract minute bars, calculate all timeframe features on the adjusted root series without resetting at contract changes, then convert signal price levels back to current-contract prices before planning orders.

**Tech Stack:** Python 3.13, pandas, vn.py Brooks cycle_v1, pytest, Ruff.

---

### Task 1: Point-In-Time Signal Adjustment

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/instruments/rollover.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_sessions_rollover_profile.py`

- [ ] **Step 1: Write the failing adjustment test**

Add a test with two actual-contract epochs and a `RollAdjustmentReference` whose old/new pre-settlements are known before the new epoch. Assert old rows keep offset zero, new rows use `old_pre_settlement - new_pre_settlement`, adjusted OHLC is continuous, raw OHLC is unchanged, and changing future rows cannot alter the prior adjusted prefix.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_sessions_rollover_profile.py -q`

Expected: FAIL because the adjustment reference and bar builder do not exist.

- [ ] **Step 3: Implement the immutable additive adjustment builder**

Add `RollAdjustmentReference` and `build_point_in_time_signal_bars`. Validate one reference per observed contract transition, `known_at <= effective_from`, positive finite reference prices, chronological unique bars, and a stable adjustment version. Output `signal_open/high/low/close`, `adjustment_scale`, `adjustment_offset`, `adjustment_known_at`, and `adjustment_version` without changing raw OHLC.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Task 1 command and expect all tests to pass.

### Task 2: Build References From SHFE Metadata

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/legacy_adapters/scalp.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_cli_legacy_loader.py`

- [ ] **Step 1: Write the failing loader test**

Extend the loader fixture with an actual-contract switch and old/new `DailyTradingSpec` rows. Assert the loaded minute bars contain raw and signal prices, the reference source is point-in-time SHFE pre-settlement, and a missing/late old-contract row raises `BLOCKED_METADATA` instead of falling back.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cli_legacy_loader.py -q`

Expected: FAIL because the loader does not build signal adjustment fields.

- [ ] **Step 3: Attach references during normalized loading**

At each observed contract transition, query both contracts' `daily_spec` for the effective exchange trade date, require both `known_at` values no later than the mapped session open, create one `RollAdjustmentReference`, and call the Task 1 builder. Do not use current metadata, forward fills, last-known defaults, or dates outside the requested loaded prefix.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Task 2 command and expect all tests to pass.

### Task 3: Continuous Multi-Timeframe Features

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/scanner.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_cycle_scanner.py`

- [ ] **Step 1: Write the failing cross-roll cycle test**

Create two short actual-contract epochs whose individual lengths cannot satisfy the cycle lookback but whose combined adjusted root prefix can. Assert the post-roll long/medium cycle becomes classified, feature timestamps remain prefix-invariant, and each output row still carries the active actual contract and adjustment audit fields.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cycle_scanner.py -q`

Expected: FAIL because `_feature_cycle_timeframe` resets features and the state machine at each contract epoch.

- [ ] **Step 3: Calculate features on adjusted root prices**

Aggregate `signal_*` columns as OHLC, merge adjustment audit fields from each bucket's source-max minute, validate a constant root tick, and invoke `add_causal_features` plus `classify_market_cycles` once for the chronological adjusted root stream. Preserve actual `contract_code` on every signal bar.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Task 3 command and expect all tests to pass.

### Task 4: Map Signal Levels Back To Actual Contracts

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/replay.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_cycle_replay.py`

- [ ] **Step 1: Write the failing execution-coordinate test**

Provide adjusted feature prices with a nonzero offset and assert entry, stop, target, range boundaries, swings, and nested obstacle lists passed to the shared core are translated to current-contract raw prices while ATR, ratios, timestamps, and cycle scores remain unchanged.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cycle_replay.py -q`

Expected: FAIL because replay currently passes adjusted signal levels directly to the order planner.

- [ ] **Step 3: Add one audited signal-to-raw boundary conversion**

Translate only price-coordinate feature fields with `(adjusted - adjustment_offset) / adjustment_scale` immediately before `StrategyInput` creation. Keep cycle classification and directional history on adjusted values; keep matching, fees, margin, limits, fills, and PnL on raw actual-contract values.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Task 4 command and expect all tests to pass.

### Task 5: Official Report And Documentation

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/runner.py`
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md`
- Modify: `cta/strategy/brooks/docs/20260814_brooks.md`
- Output: `cta/strategy/brooks/report/cycle_v1/<new-run-id>/`

- [ ] **Step 1: Add report provenance assertions**

Update runner/report tests to require `signal_price_series=PIT_PRE_SETTLEMENT_ADDITIVE_V1`, adjustment coverage counts, and no official performance when any roll reference is absent or late.

- [ ] **Step 2: Run runner/report tests and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py cta/strategy/brooks/cycle_v1/tests/test_reporting_research.py -q`

- [ ] **Step 3: Emit signal-series provenance and update docs**

Add summary fields for the adjustment method and observed adjustment versions. Document the zero-candidate root cause, the PIT pre-settlement repair, raw execution boundary, and the new verified report path.

- [ ] **Step 4: Verify all code and rerun the frozen interval**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests -q
python3 -m pytest cta/strategy/brooks/scalp/tests -q
python3 -m ruff check cta/strategy/brooks/cycle_v1 cta/strategy/brooks/scalp
python3 -m compileall -q cta/strategy/brooks/cycle_v1 cta/strategy/brooks/scalp
python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --symbols RB CU --start 2026-01-01 --end 2026-07-27 --long-tf 1h --medium-tf 30m --short-tf 5m --initial-equity 200000 --run-id 20260823_official_v4_20260101_20260727_1h_30m_5m
```

Expected: tests/lint/compile pass; metadata coverage remains 100%; long/medium classified coverage is nonzero; candidates are greater than zero or the report proves a later named setup/risk gate is the remaining cause. Never lower frozen breakout thresholds merely to force trades.
