# Historical Roll Fee Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept causal same-contract historical fee evidence for every futures root, use audited runtime defaults only for true gaps, and regenerate the blocked multi-timeframe trend report.

**Architecture:** Extend the existing historical roll-fee validator without symbol-specific rules. Search causal exact-contract and same-root evidence first. If history is exhausted, the multi-timeframe runner injects a current-contract runtime default, records it in the cache/report, and publishes only `NON_CAUSAL_SCENARIO` performance. Conflicting fee evidence still blocks.

**Tech Stack:** Python 3.13, pandas, pytest, existing audited Jin10/Tushare metadata builder.

---

### Task 1: Add Generic Same-Contract Regression Coverage

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`

- [ ] **Step 1: Write a parameterized failing test**

Add a test that calls `_validated_historical_roll_fee_reference` for rate-based
`JM2605` and cash-based `A2605` examples. Each current snapshot contains the
exact contract after session open, while the historical snapshot contains the
same fee tuple before session open plus a different same-root contract with a
different fee tuple. Assert that the older exact contract is returned as
`<contract>@<snapshot date>`.

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py -k historical_roll_fee_reference_accepts_visible_exact_contract -q
```

Expected: FAIL with `ROLL_FEE_VALIDATION_FAILED`, proving the current code
discards the exact historical contract.

- [ ] **Step 3: Add a causal guard test**

Add a second test where the historical exact-contract publication timestamp is
after `session_open`. Assert that validation still raises
`MISSING_ROLL_FEE_REFERENCE`; this test should pass before and after the fix.

### Task 2: Implement the Minimal Validator Change

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py:4882`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`

- [ ] **Step 1: Prefer visible matching exact-contract history**

Inside `_validated_historical_roll_fee_reference`, identify the exact local
contract row in both frames. Parse `开仓`, `平昨`, and `平今` through the existing
`parse_fee_expression`. If the historical row was published no later than
`session_open` and both fee tuples match, return its publication timestamp and
the snapshot-qualified contract reference. If not, execute the existing
different-contract fallback unchanged.

- [ ] **Step 2: Run focused tests to verify GREEN**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py -k 'historical_roll_fee_reference or validated_roll_fee_reference' -q
```

Expected: all selected tests PASS.

- [ ] **Step 3: Run the full metadata builder test module**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py -q
```

Expected: all tests PASS with no warnings introduced by this change.

### Task 3: Rebuild and Audit the Report

Before rebuilding, add an integration regression for a roll-entry contract
whose immediately preceding vendor snapshots are empty but whose next earlier
open-date snapshot contains a visible exact-contract fee row. Verify RED, then
change the roll validation exception path to resume the existing historical
vendor loop from that earlier date instead of raising after one empty snapshot.
Verify that the first matching visible row is used and exhaustion still blocks.

Also exclude source partitions whose filename trade date precedes the requested
load start. The downloader partitions by exchange trade date; loading the old
seven-day cushion only validates data that will later be discarded and creates
false unknown-contract/session gaps.

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`
- Create: a new immutable directory under `cta/strategy/report/multi_timeframe_trend/`
- Verify: generated `report.md`, `summary.json`, `metadata_gaps.csv`, and `fee_audit.csv`

- [ ] **Step 1: Write and run the empty-intermediate-snapshot regression**

Use a focused fake source client with an exact roll-entry contract published
late on the prior open date, no matching contract on the next earlier open
date, and a matching exact-contract fee row on the following earlier open date.
Run the new test alone and require it to fail with
`MISSING_ROLL_FEE_REFERENCE` before implementation.

- [ ] **Step 2: Resume the existing historical vendor loop**

When the one-snapshot historical validation also raises
`MISSING_ROLL_FEE_REFERENCE`, retain the late row for audit, set
`vendor_source_date` to the already resolved earlier open date, and continue
the enclosing vendor-history loop. Do not catch fee mismatch errors.

- [ ] **Step 3: Run metadata builder tests**

Run the new integration test, the focused roll-reference tests, and then the
full `test_vendor_metadata_builder.py` module. Require all to pass.

- [ ] **Step 4: Run the original reproduction command**

Run:

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner --start 2026-01-01 --end 2026-07-01 --initial-equity 1e+06 --download-minute-data --top-n 40
```

Expected: metadata preparation no longer reports
`MISSING_ROLL_FEE_REFERENCE: JM2605.DCE before 2025-12-04T21:00:00+08:00`.

- [ ] **Step 5: Classify any newly surfaced symbol**

For each remaining `MISSING_ROLL_FEE_REFERENCE`, query only the source dates
named by the error. Apply the same generic code only if an earlier exact row is
visible and fee-equivalent; otherwise retain `BLOCKED_METADATA` and report the
genuine uncovered mechanic instead of adding a default or assumed fee.

- [ ] **Step 6: Verify report contracts and audit evidence**

Require `summary.json` and `report.md` to agree on
`requested_interval_status`; require successful runs to have no metadata gaps,
a non-empty candidate funnel, and fee audit rows with source references and
effective timestamps. Keep the original blocked report unchanged as an audit
artifact.

- [ ] **Step 7: Commit only implementation files**

Stage the validator, focused tests, and this plan explicitly. Do not stage
unrelated working-tree changes or generated report data unless the user asks
for generated artifacts to be versioned.
