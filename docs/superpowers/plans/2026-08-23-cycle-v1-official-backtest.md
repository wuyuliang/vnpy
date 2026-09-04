# Cycle V1 Official Backtest Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce an audited RB/CU cycle_v1 backtest report with actual-contract net performance for `2026-01-01..2026-07-27` when full data and metadata coverage exists.

**Architecture:** Add a bounded incremental data updater, a point-in-time adapter for existing SHFE metadata, and an offline replay layer around the existing shared core, conservative matcher and actual-contract ledger. The runner gates official performance on complete coverage and otherwise preserves explicit blockers.

**Tech Stack:** Python 3, pandas, parquet, Tushare, SHFE official kx/js metadata, pytest, Ruff.

---

### Task 1: Bounded RB/CU minute updater

**Files:**
- Create: `cta/strategy/brooks/cycle_v1/backtest/market_data_update.py`
- Create: `cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py`

- [ ] Write tests proving dates outside `2026-01-01..2026-07-27` are rejected,
  existing files are skipped, mapped contracts are retained, and writes are
  atomic.
- [ ] Run the focused tests and observe the expected missing-module failure.
- [ ] Implement the smallest downloader using `FuturesDownloader` and canonical
  source directories.
- [ ] Run focused tests and Ruff.
- [ ] Run the updater for RB and CU, then audit file dates, row schemas and
  duplicate `(contract_code, datetime)` keys.

### Task 2: Rebuild SHFE mechanics through the requested end

**Files:**
- Modify generated bundle: `cta/strategy/brooks/scalp/meta/*`
- Create: `cta/strategy/brooks/cycle_v1/backtest/execution_metadata.py`
- Create: `cta/strategy/brooks/cycle_v1/tests/test_execution_metadata_adapter.py`

- [ ] Write tests for lifecycle, daily status, order capability, fee/margin and
  point-in-time visibility at decision/order timestamps.
- [ ] Run focused tests and confirm missing adapter behavior fails.
- [ ] Rebuild SHFE kx/js metadata through `2026-07-27` after minute download.
- [ ] Implement the adapter without fallback mechanics; every snapshot records
  sources and a stable SHA-256 hash.
- [ ] Validate every potential order date and write field-level coverage rows.

### Task 3: Actual-contract causal replay

**Files:**
- Create: `cta/strategy/brooks/cycle_v1/backtest/replay.py`
- Create: `cta/strategy/brooks/cycle_v1/tests/test_cycle_replay.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/scanner.py`

- [ ] Write tests for completed-bar alignment, next-event activation, actual
  contract fills, adverse stop/target ambiguity, fees, slippage, roll exits and
  zero-trade equity.
- [ ] Run focused tests and observe failures before implementation.
- [ ] Expose causal feature/cycle frames from the scanner without changing scan
  output semantics.
- [ ] Replay candidates, plans, orders, fills and exits through the existing core,
  matcher and ledger.
- [ ] Reconcile round trips and daily marked equity to cash and realized PnL.

### Task 4: Report integration

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/runner.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/reporter.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_reporting_research.py`

- [ ] Write a failing CLI test requiring a complete funnel, performance metrics,
  daily equity and no hard-coded metadata blockers when coverage passes.
- [ ] Replace `_execution_metadata_gaps` with actual coverage results.
- [ ] Emit `candidates.csv`, `plans.csv`, `orders.csv`, `fills.csv`, `trades.csv`,
  `daily_equity.csv`, `performance_by_group.csv` and coverage tables.
- [ ] Keep official performance null whenever any requested date is uncovered.
- [ ] Validate report hashes and non-overwrite behavior.

### Task 5: Real-data verification and documentation

**Files:**
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md`
- Modify: `cta/strategy/brooks/docs/20260814_brooks.md`

- [ ] Run all cycle_v1 and scalp tests, Ruff and compileall.
- [ ] Run the exact `2026-01-01..2026-07-27`, `1hour/30min/5min` CLI command.
- [ ] Review coverage, funnel, trade reconciliation, net return, drawdown, win
  rate and profit/loss ratio without suppressing losing symbols or zero trades.
- [ ] Update both documents with commands, output contracts and observed status.
