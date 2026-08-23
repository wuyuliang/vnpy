# Cycle V1 Top-N Metadata Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and consume a source-audited execution-metadata cache for the exact cycle_v1 explicit/Top-N/EMA union so the 2026 Top-N run is no longer blocked by RB/CU-only mechanics.

**Architecture:** A focused vendor builder extracts actual contract/date pairs from the selected minute partitions, reconciles Tushare contract/calendar/settlement data with historical Jin10 settlement-mirror rows, and atomically publishes a canonical bundle merged over the immutable RB/CU base. Runner integration reuses a valid content-addressed cache and preserves fail-closed reports for every missing or inconsistent field.

**Tech Stack:** Python 3.13, pandas, Tushare, AKShare, pytest, canonical Brooks metadata CSV/manifest contracts.

---

### Task 1: Deterministic Metadata Parsers

**Files:**
- Create: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py`
- Create: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`

- [ ] **Step 1: Write failing parser tests**

Cover proportional fees (`1/万分之(7.4元)`), per-lot fees (`3元`), zero fees,
malformed fees, CZCE short-code matching, positive quote-unit parsing, and the
four supported commodity session templates.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest -q cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py
```

Expected: collection or assertion failure because the parser API does not exist.

- [ ] **Step 3: Implement the minimal pure functions**

Expose these testable functions without network access:

```python
def parse_fee_expression(value: object) -> tuple[float, float]: ...
def parse_price_tick(value: object) -> float: ...
def match_vendor_contract(local_contract: str, vendor_codes: Sequence[str]) -> str: ...
def session_template_from_description(exchange: str, description: object) -> str: ...
```

Each ambiguity raises `MetadataBuildError` with a stable reason code.

- [ ] **Step 4: Verify GREEN**

Run the Task 1 pytest command and expect all tests to pass.

### Task 2: Cross-Source Daily Mechanics

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`

- [ ] **Step 1: Write failing reconciliation tests**

Build synthetic prior-day Jin10 and Tushare settlement rows. Assert that a valid
row produces canonical daily/fee records and that contract, pre-settlement, or
margin disagreement raises the exact field-level gap.

- [ ] **Step 2: Verify RED**

Run the focused test file and confirm failures are caused by missing
`reconcile_daily_mechanics` behavior.

- [ ] **Step 3: Implement exact reconciliation**

Require a unique local contract match, equal prior settlement within half a
price tick, equal long/short margin within `1e-9`, limits bracketing prior
settlement, and explicit open/close/close-today fees. Do not infer a missing
fee from another fee column.

- [ ] **Step 4: Verify GREEN**

Run the focused tests and expect all parser and reconciliation cases to pass.

### Task 3: Canonical Bundle Cache

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/execution_metadata.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_execution_metadata_adapter.py`

- [ ] **Step 1: Write failing cache tests**

Use fake source clients and temporary parquet partitions to assert exact
contract/date extraction, base-row precedence, manifest verification, raw audit
hashes, cache reuse, and atomic failure without a partial published bundle.

- [ ] **Step 2: Verify RED**

Run both test files and confirm the cache API is missing.

- [ ] **Step 3: Implement source clients and cache publication**

Add `prepare_execution_metadata(...) -> MetadataPreparationResult`. Fetch each
source date once, retain canonical raw JSON in gzip audit files, write a complete
extension through `import_metadata_bundle`, merge it with `merge_canonical_frames`,
and verify the resulting manifest before returning its root.

- [ ] **Step 4: Verify GREEN**

Run both focused test files and expect all tests to pass.

### Task 4: Multi-Exchange Sessions and Runner Integration

**Files:**
- Modify: `cta/strategy/brooks/scalp/research_pipeline.py`
- Modify: `cta/strategy/brooks/scalp/tests/test_data_session.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/runner.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/reporter.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_cli_legacy_loader.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_runner_report.py`

- [ ] **Step 1: Write failing integration tests**

Assert day-only, 23:00, 01:00, and 02:30 templates resolve correctly; source
validation recognizes the row's actual exchange rather than hard-coding SHFE;
runner uses the prepared cache; and preparation failure becomes a normal blocked
report with `execution_metadata_update` audit.

- [ ] **Step 2: Verify RED**

Run the four focused test files and confirm expected failures.

- [ ] **Step 3: Implement runner/report wiring**

When `--download-minute-data` is enabled, prepare execution metadata by default
after selection. Add an explicit cache-root option, preserve `--meta-root` as the
immutable base, load the returned merged root, and render source/cache/coverage
counts without embedding raw rows in `summary.json`.

- [ ] **Step 4: Verify GREEN**

Run focused tests, then all `cycle_v1/tests` and affected scalp metadata/session
tests.

### Task 5: Documentation and Real Run

**Files:**
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md`
- Modify: `cta/strategy/brooks/docs/20260814_brooks.md`

- [ ] **Step 1: Update command and source boundaries**

Document automatic metadata preparation, cache location, raw audit, Tushare
`ft_limit` permission absence, cross-source reconciliation, and unchanged
fail-closed behavior.

- [ ] **Step 2: Run static and regression verification**

Run Ruff on changed Python files, `compileall` for cycle_v1, focused tests, and
the full cycle_v1 suite. Record any pre-existing configuration-driven failures
separately.

- [ ] **Step 3: Run the real union command**

Execute the integrated 2026-01-01 through 2026-07-27 command with `--symbols LC`,
`--top-n 20`, and `--include-ema-eligible`. Reuse minute files and publish a new
run ID.

- [ ] **Step 4: Verify report invariants**

Check that selected roots equal the download union, metadata coverage is 100%,
`Official status` is `COMPLETE`, official performance is non-null, and funnel
counts agree across JSON/CSV/Markdown. If any source row remains unavailable,
verify the report stays `BLOCKED_METADATA` with the exact contract/date/field.

- [ ] **Step 5: Request code review and fix findings**

Review the final diff for lookahead, current-mechanics fallback, silent universe
shrinkage, cache corruption, and report inconsistency; rerun affected tests after
every accepted fix.
