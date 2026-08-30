# Cycle V1 Automatic Candidate Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically render one daily/60-minute/1-minute review card for every `cycle_v1` backtest candidate and link the artifacts from the run report.

**Architecture:** Generalize the existing `candidate_charts.py` module so persisted report tables determine each candidate's most advanced outcome and each loaded symbol gets its own chart context. Keep chart generation as a post-report step in `runner.run_from_args`, preserving the canonical CSV/JSON report and failing visibly if candidate and image counts diverge.

**Tech Stack:** Python 3, pandas, Pillow, vn.py cycle_v1 loaders and session aggregation, pytest, Ruff.

---

### Task 1: General Candidate Outcome Index

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py`

- [ ] **Step 1: Write failing outcome tests**

Add tests that call a wished-for `_build_candidate_index` with candidates that
are rejected, planned, ordered, filled, and traded:

```python
index = _build_candidate_index(
    candidates,
    rejections=rejections,
    plans=pd.DataFrame({"candidate_id": ["planned", "ordered", "filled", "traded"]}),
    orders=pd.DataFrame({"candidate_id": ["ordered", "filled", "traded"]}),
    fills=pd.DataFrame({"candidate_id": ["filled", "traded"]}),
    trades=pd.DataFrame({"candidate_id": ["traded"]}),
)
assert index.set_index("candidate_id")["outcome_code"].to_dict() == {
    "rejected": "CYCLE_CONFIDENCE_BLOCKED",
    "planned": "ELIGIBLE_PLAN",
    "ordered": "ORDERED",
    "filled": "FILLED",
    "traded": "ROUND_TRIP",
}
```

Add a second test proving duplicate candidate-level rejection rows raise
`ValueError` instead of selecting one.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py -q
```

Expected: collection or assertion failure because `_build_candidate_index` does
not exist.

- [ ] **Step 3: Implement the minimal index builder**

Implement:

```python
def _build_candidate_index(
    candidates: pd.DataFrame,
    *,
    rejections: pd.DataFrame,
    plans: pd.DataFrame,
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    trades: pd.DataFrame,
) -> pd.DataFrame:
    ...
```

Validate candidate columns and unique nonblank candidate IDs. Sort by
`signal_time`, assign one-based `sequence`, attach at most one candidate-level
rejection, then overwrite the display outcome in this order:
`ELIGIBLE_PLAN`, `ORDERED`, `FILLED`, `ROUND_TRIP`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Task 1 tests and expect all to pass.

### Task 2: Multi-Symbol Causal Chart Context

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py`

- [ ] **Step 1: Write failing context and rendering tests**

Add a test that supplies AG and RB candidates plus two `_ChartContext` objects,
then verifies `render_candidate_card` titles use the row's symbol. Add a loader
test using monkeypatched `load_normalized_symbol` to prove AG and RB are loaded
from their distinct `effective_warmup_starts` dates and converted into daily,
60-minute, and 1-minute contexts.

- [ ] **Step 2: Run the tests and verify RED**

Expected failure: the current `_load_chart_context` enforces one symbol and the
card title is hard-coded to AG.

- [ ] **Step 3: Generalize context loading and card text**

Replace the one-symbol loader with:

```python
def _load_chart_contexts(
    summary: Mapping[str, Any],
    data_root: Path,
    symbols: set[str],
) -> dict[str, _ChartContext]:
    ...
```

Map each candidate root to exactly one `loaded_symbols` vt symbol, use the run
metadata cache, start each load at `effective_warmup_starts[root]`, and aggregate
exchange-trade-date daily plus completed 60-minute bars. Keep the config-hash
guard. Change the card title, outcome line, context label, and footer to use the
actual symbol and `outcome_code`/`outcome_detail`; retain `SIGNAL`, `ACTIVE`, and
`POST-EVENT REVIEW ONLY` overlays.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run `test_candidate_charts.py` and expect all tests to pass.

### Task 3: Idempotent Candidate Chart Bundle

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py`

- [ ] **Step 1: Write failing bundle tests**

Update the report fixture to include `plans.csv`, `orders.csv`, `fills.csv`, and
`trades.csv`. Add assertions that generation:

```python
assert len(index) == len(pd.read_csv(report_dir / "candidates.csv"))
assert len(list((report_dir / "candidate_charts").glob("*.png"))) == len(index)
assert (report_dir / "CANDIDATE_CHARTS.md").is_file()
assert "CANDIDATE_CHARTS.md" in (report_dir / "report.md").read_text()
```

Add a zero-candidate test expecting a header-only index and no PNG files. Add a
regeneration test that creates `candidate_charts/stale.png` and proves it is
removed.

- [ ] **Step 2: Run the tests and verify RED**

Expected failures: generation still requires a large-cycle rejection, rejects
empty candidates, and does not write general chart markdown or report links.

- [ ] **Step 3: Implement bundle generation**

Read all six persisted funnel tables, build the general index, clear only stale
`candidate_charts/*.png`, load one context per candidate symbol, and write one
PNG per index row. Include the symbol in filenames. Write a general
`CANDIDATE_CHARTS.md` table with sequence, signal, active time, symbol, setup,
direction, outcome, and PNG link. Append or replace one idempotent Candidate
Charts section in `report.md`. For zero candidates, write the empty index and
markdown without loading metadata or bars.

Update the standalone CLI result so `explanation` points to
`CANDIDATE_CHARTS.md`.

- [ ] **Step 4: Run chart tests and verify GREEN**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py -q
```

Expected: all candidate chart tests pass.

### Task 4: Runner Integration

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/runner.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py`

- [ ] **Step 1: Write a failing runner integration test**

Monkeypatch `write_report_bundle` and `generate_candidate_charts`, invoke
`run_from_args`, and assert chart generation occurs after report writing with:

```python
assert events == ["report", "charts"]
assert captured == {
    "report_dir": expected_output,
    "data_root": Path(args.data_root),
}
```

Patch chart generation in existing boundary-only runner tests so they remain
focused on warm-up behavior.

- [ ] **Step 2: Run the test and verify RED**

Expected failure: runner returns immediately after `write_report_bundle`.

- [ ] **Step 3: Add the automatic call**

Import `generate_candidate_charts` and call:

```python
generate_candidate_charts(output, data_root=Path(args.data_root))
```

immediately after `write_report_bundle` and before returning.

- [ ] **Step 4: Run runner and chart tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py \
  cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py -q
```

Expected: all focused tests pass.

### Task 5: Full And Real-Data Verification

**Files:**
- Verify: `cta/strategy/brooks/cycle_v1`
- Generate: `cta/strategy/brooks/report/cycle_v1/<new-run>/candidate_charts/`

- [ ] **Step 1: Run static and full test verification**

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests -q
python3 -m ruff check \
  cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py \
  cta/strategy/brooks/cycle_v1/backtest/runner.py \
  cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py \
  cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py
python3 -m compileall -q cta/strategy/brooks/cycle_v1
```

Expected: zero failures and zero static errors.

- [ ] **Step 2: Rerun the AG backtest**

```bash
python3 -m cta.strategy.brooks.cycle_v1.backtest.runner \
  --symbols AG \
  --download-minute-data \
  --start 2026-01-01 \
  --end 2026-02-05 \
  --long-tf 30min \
  --medium-tf 5min \
  --short-tf 1min \
  --initial-equity 200000
```

Expected: official status remains `COMPLETE` and a new report directory is
printed.

- [ ] **Step 3: Verify artifacts and causal labels**

Check that candidate CSV rows, index rows, and PNG counts are equal; open at
least the first and last PNG with `view_image`; verify daily/60min/1min panels,
actual symbol title, signal/active markers, outcome text, and post-event label.
Confirm `metadata_gaps.csv` has only its header and `cycle_v1.yaml` was not
modified.
