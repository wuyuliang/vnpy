# Cycle V1 Integrated Minute Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in minute-data preparation stage to the cycle_v1 backtest CLI so one command downloads and backtests the same explicit + Top-N + causal-EMA union.

**Architecture:** Extract a typed `prepare_minute_data` API from the existing downloader CLI, leaving `run_from_args` as a compatibility wrapper. The backtest runner validates download-only flags, calls that API, resolves the returned selected roots against freshly discovered local data, and embeds the full download audit in its report summary.

**Tech Stack:** Python 3.13, argparse, pandas, Tushare adapter, pytest, Ruff.

---

### Task 1: Structured Minute-Data Preparation API

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/market_data_update.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py`

- [ ] **Step 1: Write the failing structured-entry test**

Add a test that calls `prepare_minute_data(explicit=("LC",), top_n=1, include_ema_eligible=True, ...)` with `_FakeDownloader`, then asserts selection, download statistics, and optional audit JSON equal the existing CLI result shape.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py::test_prepare_minute_data_exposes_structured_api -q`

Expected: import failure because `prepare_minute_data` does not exist.

- [ ] **Step 3: Implement the minimal structured API**

Add a keyword-only function accepting `explicit`, `top_n`, `include_ema_eligible`, `ranking_csv`, `day_root`, `start`, `end`, `data_root`, `audit_output`, `rate_limit`, and optional downloader. Construct the current selection, call `update_minute_data`, attach selection/audit paths, and write the optional JSON. Make `run_from_args` delegate to this function without changing standalone CLI behavior.

- [ ] **Step 4: Run downloader tests and verify GREEN**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py -q`

Expected: all tests pass.

### Task 2: Runner Argument Contract and Download Selection

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/runner.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py`

- [ ] **Step 1: Write failing parser and validation tests**

Test these cases:

```python
args = build_parser().parse_args([
    "--start", "2026-01-01", "--end", "2026-07-27",
    "--symbols", "LC", "--download-minute-data",
    "--top-n", "20", "--include-ema-eligible",
])
assert args.download_minute_data
assert args.top_n == 20
```

Also assert download-only flags without `--download-minute-data` fail before discovery, and download mode with default `symbols=all`, `top_n=0`, and EMA disabled fails with a clear message.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py -q`

Expected: parser rejects new options or validation helper is missing.

- [ ] **Step 3: Add parser options and pure validation**

Add `--download-minute-data`, `--top-n`, `--include-ema-eligible`, `--ranking-csv`, `--day-root`, `--download-rate-limit`, and `--download-audit-output`. Add a pure helper that returns explicit download tokens, treating sole `all` as an empty explicit pool only when Top-N or EMA provides a source. Reject negative Top-N, ignored download-only flags, and empty download selection.

- [ ] **Step 4: Run runner tests and verify GREEN**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py -q`

Expected: all tests pass.

### Task 3: Download Then Backtest the Exact Union

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/runner.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py`

- [ ] **Step 1: Write failing integration tests**

Monkeypatch `prepare_minute_data` and `discover_symbols`. Assert the runner calls preparation before discovery, passes the same date/data paths, converts `selection.selected` into the requested backtest roots in stable order, and embeds `minute_data_update.enabled=true` plus the audit in summary. Add a missing-selected-root test that raises before loading/scanning instead of silently shrinking the union.

- [ ] **Step 2: Run integration tests and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py -q`

Expected: preparation is not called and summary lacks download audit.

- [ ] **Step 3: Implement orchestration**

In `run_from_args`, parse dates/timeframes first, validate download arguments, call `prepare_minute_data` when enabled, then discover local symbols. Resolve the selected roots from `minute_data_update.selection.selected`; if any selected root is absent, raise `ValueError` listing all missing roots. Otherwise preserve union order and continue through the existing loader/scanner/replay path. For no-download runs use current `resolve_requested_symbols` behavior and add only `minute_data_update={"enabled": false}` to summary.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py -q`

Expected: all focused tests pass.

### Task 4: Documentation and Regression Verification

**Files:**
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md`
- Modify: `cta/strategy/brooks/docs/20260814_brooks.md`

- [ ] **Step 1: Replace the two-command workflow with the integrated recommended command**

Document opt-in behavior, exact-union backtest semantics, `symbols=all` download control behavior, independent audit option, no-overwrite behavior, and unchanged `BLOCKED_METADATA` mechanics contract.

- [ ] **Step 2: Run static and focused verification**

Run:

```bash
python3 -m ruff check cta/strategy/brooks/cycle_v1/backtest/market_data_update.py cta/strategy/brooks/cycle_v1/backtest/runner.py cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py
python3 -m compileall -q cta/strategy/brooks/cycle_v1/backtest
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_download_universe.py cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py -q
python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --help
git diff --check
```

Expected: all commands pass and help lists the integrated options.

- [ ] **Step 3: Run the cycle_v1 regression suite and compare the known baseline**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests -q`

Expected project baseline: no new failures; the existing eight failures caused by the local untracked `cta/strategy/brooks/config/cycle_v1.yaml` overrides may remain and must be reported exactly rather than hidden.
