# Multi-Timeframe Trend AG Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one command that downloads and causally backtests the approved daily-direction/5-minute-entry strategy on AG, then writes performance, reproduction command, audit tables, and one chart per opportunity.

**Architecture:** Add a strategy-owned `multi_timeframe_trend_backtest` package. Reuse cycle_v1's minute download, point-in-time metadata, actual-contract normalization, session aggregation, official-report metrics, and candlestick primitives; keep strategy execution and chart semantics independent from Brooks market-cycle code. Signals use the point-in-time adjusted series, fills use actual-contract 1-minute bars, and missing mechanics or an open position at a newly observed contract mapping blocks official performance.

**Tech Stack:** Python 3.10+, pandas, NumPy, Pillow, pytest, existing CTA cycle_v1 data/metadata adapters.

---

### Task 1: Causal Replay Engine

**Files:**
- Create: `cta/strategy/multi_timeframe_trend_backtest/__init__.py`
- Create: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Write failing execution tests**

Add tests proving that an order cannot fill on its signal bar, a long stop/target ambiguity exits at the stop, pullback targets are recalculated from the actual fill, Always-In trailing stops never widen, and an open position at an observed contract switch raises `BLOCKED_ROLL_EXECUTION_BAR`.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: collection fails because `cta.strategy.multi_timeframe_trend_backtest.engine` does not exist.

- [ ] **Step 3: Implement the minimum replay API**

Implement immutable replay inputs/artifacts plus a single-symbol minute event loop. Accept candidate rows from `generate_multi_timeframe_candidates`, activate at the next minute event, expire after three completed 5-minute bars, map adjusted signal levels to actual levels, obtain an execution snapshot before order creation, recalculate quantity from current marked equity, and produce candidates/plans/orders/fills/trades/daily-equity/rejections tables.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: execution tests pass.

### Task 2: Metrics And Auditable Report Bundle

**Files:**
- Create: `cta/strategy/multi_timeframe_trend_backtest/report.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Write failing report tests**

Add a temporary-directory test asserting that publishing creates `summary.json`, Chinese `report.md`, `RUN_COMMAND.sh`, all audit CSV files, and includes return, maximum drawdown, win rate, profit factor, average win/loss ratio, and the exact reproduction command.

- [ ] **Step 2: Run the report test and verify RED**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: import or missing-file assertion fails.

- [ ] **Step 3: Implement report publication**

Reuse `compute_performance_metrics`, `build_group_report`, and `build_official_summary`. Add `average_win_loss_ratio` without replacing `profit_factor`; create the target directory with `exist_ok=False`; write UTF-8 JSON/Markdown, executable reproduction script, and stable CSV schemas even when no rows exist.

- [ ] **Step 4: Run the report test and verify GREEN**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: report tests pass.

### Task 3: Opportunity Charts And CLI Runner

**Files:**
- Create: `cta/strategy/multi_timeframe_trend_backtest/charts.py`
- Create: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Modify: `cta/strategy/docs/multi_timeframe_trend_strategy.md`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Write failing CLI and chart tests**

Test parser defaults, AG symbol resolution through the reused cycle_v1 preparer, reproduction-command ordering, unique run IDs, and one PNG plus one index row per candidate. Verify chart panels are daily, completed 5-minute, and actual 1-minute bars.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: chart/runner imports fail.

- [ ] **Step 3: Implement runner and charts**

The runner validates dates and equity, downloads warm-up minute data when requested, prepares run-scoped metadata, fail-closes incomplete coverage, loads AG actual-contract bars, aggregates daily/5-minute signal and actual frames, calls the existing strategy candidate facade, runs replay, publishes the report, then renders every persisted candidate to `opportunity_charts/`.

- [ ] **Step 4: Document the final AG command**

Append the exact `python3 -m cta.strategy.multi_timeframe_trend_backtest.runner` command and output directory contents to the approved Chinese strategy document.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: all runner and chart tests pass.

### Task 4: Regression And Real AG Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run strategy regressions**

Run: `pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_management.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Run static checks**

Run: `ruff check cta/strategy/multi_timeframe_trend_backtest cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

Expected: no lint errors.

- [ ] **Step 3: Run the requested AG command**

Run the documented command for `2026-01-01..2026-02-05` and `initial_equity=1000000`. The process must return a new report path; if historical mechanics are incomplete, the persisted status must be exactly `BLOCKED_METADATA` and official performance must remain null.

- [ ] **Step 4: Inspect the generated bundle**

Verify the report includes requested metrics, `RUN_COMMAND.sh` reproduces the invocation, CSV counts reconcile with the funnel, and `opportunity_charts/index.csv` has exactly one row per `candidates.csv` row.
