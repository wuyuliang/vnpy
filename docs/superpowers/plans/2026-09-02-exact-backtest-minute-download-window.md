# Exact Backtest Minute Download Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all integrated minute downloads accept any ordered date range and download exactly the CLI backtest `start..end`, never the strategy warmup interval.

**Architecture:** Keep date ownership in the shared `prepare_minute_data(start, end)` API and remove the `download_start` override from both shared runner orchestration and callers. Strategy runners still compute warmup dates for local loading and metadata, while the downloader records the unmodified requested interval.

**Tech Stack:** Python 3.13, argparse, pandas, pytest, Ruff.

---

### Task 1: Remove The Frozen Downloader Date Window

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/market_data_update.py:20-75`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py:82-105`

- [ ] **Step 1: Replace the frozen-window rejection test with failing acceptance and ordering tests**

Use dates on both sides of the former constants and assert the audit preserves them:

```python
@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2025, 1, 1), date(2025, 1, 1)),
        (date(2026, 7, 28), date(2026, 7, 28)),
    ],
)
def test_update_accepts_dates_outside_former_frozen_window(
    tmp_path: Path,
    start: date,
    end: date,
) -> None:
    summary = update_minute_data(
        start=start,
        end=end,
        data_root=tmp_path,
        downloader=_FakeDownloader(),
        selected_symbols=(),
    )
    assert summary["start"] == start.isoformat()
    assert summary["end"] == end.isoformat()


def test_update_rejects_inverted_download_window(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="end must not precede start"):
        update_minute_data(
            start=date(2025, 1, 2),
            end=date(2025, 1, 1),
            data_root=tmp_path,
            downloader=_FakeDownloader(),
            selected_symbols=(),
        )
```

- [ ] **Step 2: Run the two tests and verify RED**

Run:

```bash
python3 -m pytest \
  cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py::test_update_accepts_dates_outside_former_frozen_window \
  cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py::test_update_rejects_inverted_download_window -q
```

Expected: former-boundary cases fail with `download window must be within ...`; the inverted-date message does not match the new contract.

- [ ] **Step 3: Implement the minimal validation**

Delete `ALLOWED_START` and `ALLOWED_END`. Replace the existing condition with:

```python
if end < start:
    raise ValueError("download end must not precede start")
```

Do not change file skipping, mappings, supplier calls, schema conversion, or audit fields.

- [ ] **Step 4: Run all downloader tests and verify GREEN**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py -q
```

Expected: all tests pass.

### Task 2: Remove The Warmup Download Override From Shared Orchestration

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/market_data_update.py:416-456`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/runner.py:203-233,464-471`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py:505-573`

- [ ] **Step 1: Change the cycle runner test to require exact request dates**

Rename `test_run_downloads_and_prepares_metadata_from_effective_warmup` to `test_run_downloads_exact_requested_interval_and_loads_local_warmup`. Keep the existing captured calls and assert:

```python
assert captured["symbols"] == {
    "start": date(2026, 1, 1),
    "end": date(2026, 2, 5),
}
assert captured["metadata"]["start"] == date(2025, 8, 30)
assert summary["effective_warmup_starts"] == {"AG": "2025-08-30"}
```

This separates exact download dates from the unchanged local warmup and metadata dates.

- [ ] **Step 2: Run the focused cycle test and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py::test_run_downloads_exact_requested_interval_and_loads_local_warmup -q
```

Expected: `captured["symbols"]` still contains `download_start`.

- [ ] **Step 3: Remove `download_start` throughout the shared API**

Change signatures and calls to:

```python
def prepare_minute_data(
    *,
    explicit: Sequence[str],
    top_n: int,
    include_ema_eligible: bool,
    ranking_csv: str | Path,
    day_root: str | Path,
    start: date,
    end: date,
    data_root: str | Path,
    audit_output: str | Path = "",
    rate_limit: int = 450,
    downloader: Any | None = None,
) -> dict[str, object]:
    # selection remains unchanged
    summary = update_minute_data(
        start=start,
        end=end,
        data_root=data_root,
        downloader=active_downloader,
        selected_symbols=selection.selected,
    )
```

And:

```python
def prepare_backtest_symbols(
    args: argparse.Namespace,
    *,
    start: date,
    end: date,
) -> tuple[...]:
```

In cycle `run_from_args`, call only:

```python
discovered, selected, minute_data_update = prepare_backtest_symbols(
    args,
    start=start,
    end=end,
)
```

Keep `warmup_start` for `_effective_warmup_start` and metadata preparation.

- [ ] **Step 4: Run downloader and cycle runner tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py \
  cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py -q
```

Expected: all tests pass.

### Task 3: Enforce Exact Dates In The Multi-Timeframe Trend Runner

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py:219-231`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py:1490-1610`

- [ ] **Step 1: Add a failing orchestration test and strengthen the Top-20 command test**

Import the runner module for monkeypatching:

```python
from cta.strategy.multi_timeframe_trend_backtest import runner as trend_runner
```

Add a runner test using the existing parser and lightweight monkeypatches. Capture `prepare_backtest_symbols` and assert its keyword arguments are exactly:

```python
assert captured["symbols"] == {
    "start": date(2025, 1, 1),
    "end": date(2026, 6, 30),
}
```

Update the Top-N reproduction test to use `--top-n 20`, `2025-01-01..2026-06-30`, and assert:

```python
assert "--symbols" not in reproduction["argv"]
assert reproduction["argv"][reproduction["argv"].index("--top-n") + 1] == "20"
```

- [ ] **Step 2: Run the focused trend tests and verify RED**

Run:

```bash
python3 -m pytest \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_trend_runner_downloads_exact_requested_interval \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_top_n_without_explicit_symbols_does_not_add_ag -q
```

Expected: orchestration capture includes `download_start`; the reproduction assertion remains independent and passes.

- [ ] **Step 3: Remove the warmup argument from the trend runner call**

Use:

```python
discovered, selected, minute_update = prepare_backtest_symbols(
    args,
    start=start,
    end=end,
)
```

Do not alter `warmup_start`, `effective_starts`, metadata coverage, strategy preparation, or report context.

- [ ] **Step 4: Run the complete trend tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  cta/strategy/tests/test_multi_timeframe_trend_strategy.py \
  cta/strategy/tests/test_multi_timeframe_trend_management.py \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q
```

Expected: all tests pass.

### Task 4: Update Contracts And Verify The Final Command

**Files:**
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md:34-45`
- Modify: `docs/superpowers/specs/2026-08-23-cycle-v1-integrated-minute-download-design.md:55-65,110-112`
- Modify: `docs/superpowers/specs/2026-08-23-cycle-v1-ranked-ema-minute-download-design.md:60-72,108-110`
- Modify: `cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md:470-490`

- [ ] **Step 1: Replace frozen-window and warmup-download language**

State consistently that integrated downloads accept any ordered interval and download exactly CLI `start..end`; warmup is local-only and can still block official output when missing. Preserve no-overwrite and fail-closed metadata language.

- [ ] **Step 2: Scan for contradictory contracts**

Run:

```bash
rg -n "download window must be within|冻结日期区间|允许区间冻结|download_start|预热.*下载" \
  cta/strategy/brooks/docs cta/strategy/docs docs/superpowers/specs docs/superpowers/plans
```

Expected: only historical explanation explicitly marked as superseded may remain; no active contract contradicts exact `start..end` semantics.

- [ ] **Step 3: Run static and regression verification**

Run:

```bash
python3 -m ruff check \
  cta/strategy/brooks/cycle_v1/backtest/market_data_update.py \
  cta/strategy/brooks/cycle_v1/backtest/runner.py \
  cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py \
  cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py \
  cta/strategy/multi_timeframe_trend_backtest/runner.py \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py
python3 -m compileall -q \
  cta/strategy/brooks/cycle_v1/backtest \
  cta/strategy/multi_timeframe_trend_backtest
python3 -m pytest \
  cta/strategy/brooks/cycle_v1/tests/test_download_universe.py \
  cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py \
  cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py \
  cta/strategy/tests/test_multi_timeframe_trend_strategy.py \
  cta/strategy/tests/test_multi_timeframe_trend_management.py \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q
```

Expected: all selected tests pass, Ruff reports no errors, and compileall exits 0.

- [ ] **Step 4: Verify the final Top-20 command without starting a large download**

Run the parser/reproduction tests plus CLI help rather than the expensive network backtest:

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner --help
```

Confirm the runnable command remains:

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --download-minute-data \
  --top-n 20 \
  --start 2025-01-01 \
  --end 2026-06-30 \
  --initial-equity 1000000
```

Do not run the full Top-20 download automatically because it performs many external API calls and can take substantial time. Leave implementation files uncommitted because this worktree already contains relevant untracked user changes.
