# First Trend Entry Daily Breakout Buffer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable long first-trend-entry daily body breakout buffer and compare five ratios for AG and top5.

**Architecture:** Daily context assigns each causal bullish EMA run a segment id. Candidate conversion carries that id and converts the prior five-day body high to the actual-contract price scale. Replay keeps a set of segment ids with a real opening fill and applies the strict buffer only before the first fill.

**Tech Stack:** Python 3.10+, pandas, pytest, existing event-driven backtest/report pipeline.

---

### Task 1: Configuration And Causal Trend Segment

**Files:**
- Modify: `cta/config/multi_timeframe_trend_config.py`
- Modify: `cta/strategy/multi_timeframe_trend_rules.py`
- Modify: `cta/strategy/multi_timeframe_trend_strategy.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

- [ ] Add failing tests for the `0.0005` default, float normalization, `[0, 1)` validation, and bullish segment ids resetting after an EMA-order break.
- [ ] Run `pytest -q cta/strategy/tests/test_multi_timeframe_trend_strategy.py -k 'first_trend_entry or bull_trend_segment'` and verify the new tests fail for missing behavior.
- [ ] Add `first_trend_entry_daily_breakout_buffer_ratio`, validate it like the existing EMA-gap ratio, compute `daily_bull_trend_id` from completed daily direction, and carry it through candidate rows.
- [ ] Re-run the focused tests and verify they pass.

### Task 2: Actual-Price Conversion And Replay Filter

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] Add failing tests showing that point-in-time adjustment converts `prior_5d_high`, zero disables the new rule, a boundary candidate is rejected before the first fill, a higher candidate can fill, and a later same-segment candidate returns to the existing equality rule after that fill.
- [ ] Run the named new backtest tests and verify failures are caused by the absent conversion/filter.
- [ ] Convert `prior_5d_high` in `_execution_candidates`, add a pure buffer rejection helper, require the new candidate audit fields, and maintain filled bullish segment ids in both single-symbol and portfolio replay.
- [ ] Mark a segment filled only after `_open_position` succeeds; use rejection code `FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET` before order submission.
- [ ] Re-run the focused tests and verify they pass.

### Task 3: Report Context And Regression

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Modify: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`
- Modify: `cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md`
- Modify: `cta/report/change_log.md`

- [ ] Add a failing runner-context assertion for `first_trend_entry_daily_breakout_buffer_ratio` under `daily_filters`.
- [ ] Record the ratio in report context and document the rule, config-only operation, formula, and rejection code.
- [ ] Run `pytest -q cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py` and verify all strategy/backtest tests pass.

### Task 4: AG And Top5 Sensitivity Runs

**Files:**
- Create: `cta/strategy/report/multi_timeframe_trend/20260903_first_trend_entry_breakout_buffer_scan/summary.csv`
- Create: `cta/strategy/report/multi_timeframe_trend/20260903_first_trend_entry_breakout_buffer_scan/README.md`

- [ ] Run the existing backtest pipeline for AG and the locked top5 universe with config ratios `0.0`, `0.0005`, `0.0010`, `0.0015`, and `0.0020`, preserving the same `2025-01-01` to `2026-07-01` data and cost assumptions.
- [ ] Extract status, symbol set, trade count, net PnL, total return, max drawdown, final equity, and new-rule rejection count into `summary.csv`.
- [ ] Label the comparison as in-sample sensitivity analysis and record every output directory in `README.md`; do not choose or claim an OOS-optimal ratio.
- [ ] Run a final focused regression suite and inspect `git diff --check` plus the exact changed-file diff.

Implementation commits are intentionally omitted because the target strategy files are pre-existing untracked user work; staging them would commit unrelated local content.
