# Vendor Contract Duplicate Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow metadata preparation to select the first vendor row when one normalized contract code is repeated, while retaining errors for multiple distinct matching contract codes.

**Architecture:** Normalize and deduplicate contract-code candidates inside the shared matcher without changing its exact-before-fallback precedence. Then make the shared row selector compare normalized codes and preserve vendor row order by returning the first selected row.

**Tech Stack:** Python, pandas, pytest

---

### Task 1: Deduplicate Matching Contract Codes

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py:1055`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py:1740`

- [ ] **Step 1: Add failing matcher tests**

Replace the case-variant ambiguity assertion and add exact-repeat and true-ambiguity coverage:

```python
def test_match_vendor_contract_selects_first_case_variant() -> None:
    assert match_vendor_contract(
        "TA2609.ZCE",
        ["TA609", "ta609"],
    ) == "TA609"


def test_match_vendor_contract_selects_first_repeated_normalized_code() -> None:
    assert match_vendor_contract(
        "AG2502.SHF",
        ["ag2502", "ag2502"],
    ) == "ag2502"


def test_match_vendor_contract_blocks_distinct_fallback_contracts() -> None:
    with pytest.raises(MetadataBuildError, match="AMBIGUOUS_CONTRACT"):
        match_vendor_contract(
            "TA2609.ZCE",
            ["TA609", "TA2609.CZC"],
        )
```

- [ ] **Step 2: Run matcher tests and verify RED**

Run:

```bash
python3 -m pytest -q \
  cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py::test_match_vendor_contract_selects_first_case_variant \
  cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py::test_match_vendor_contract_selects_first_repeated_normalized_code \
  cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py::test_match_vendor_contract_blocks_distinct_fallback_contracts
```

Expected: the first two tests fail with `AMBIGUOUS_CONTRACT`; the distinct-code protection passes.

- [ ] **Step 3: Deduplicate candidates by normalized code**

Replace the initial candidate rendering in `match_vendor_contract()`:

```python
    first_code_by_identity: dict[str, str] = {}
    for candidate in vendor_codes:
        rendered = str(candidate).strip()
        first_code_by_identity.setdefault(rendered.upper(), rendered)
    rendered_codes = list(first_code_by_identity.values())
```

Keep the existing exact-match and fallback-match logic unchanged after this block.

- [ ] **Step 4: Run matcher tests and verify GREEN**

Run the command from Step 2.

Expected: all three tests pass.

### Task 2: Select the First Duplicate Vendor Row

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py:2803`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`

- [ ] **Step 1: Import the row selector and add a failing row test**

Add `_exact_contract_row` to the test module imports and add:

```python
def test_exact_contract_row_selects_first_duplicate_source_row() -> None:
    frame = pd.DataFrame(
        [
            {"合约代码": "ag2502", "现价": 7500.0},
            {"合约代码": "ag2502", "现价": 7600.0},
        ]
    )

    selected = _exact_contract_row(
        "AG2502.SHF",
        frame,
        code_column="合约代码",
    )

    assert selected["合约代码"] == "ag2502"
    assert selected["现价"] == 7500.0
```

- [ ] **Step 2: Run the row test and verify RED**

Run:

```bash
python3 -m pytest -q cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py::test_exact_contract_row_selects_first_duplicate_source_row
```

Expected: FAIL with `AMBIGUOUS_CONTRACT` because two duplicate rows are selected.

- [ ] **Step 3: Normalize row matching and return the first row**

Replace `_exact_contract_row()` selection and cardinality check with:

```python
    normalized_codes = frame[code_column].astype(str).str.strip().str.upper()
    selected = frame.loc[
        normalized_codes.eq(str(matched_code).strip().upper())
    ]
    return selected.iloc[0].to_dict()
```

The matcher guarantees at least one selected row, and source order is retained.

- [ ] **Step 4: Run the row test and verify GREEN**

Run the command from Step 2.

Expected: PASS.

### Task 3: Regression Verification

**Files:**
- Verify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py`
- Verify: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`

- [ ] **Step 1: Run the full vendor metadata builder tests**

Run:

```bash
python3 -m pytest -q cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py
```

Expected: all tests pass.

- [ ] **Step 2: Run the multi-timeframe metadata preparation tests**

Run:

```bash
python3 -m pytest -q \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py \
  -k "metadata or runner"
```

Expected: tests related to metadata and runner pass, apart from any independently documented pre-existing configuration assertion.

- [ ] **Step 3: Reproduce the reported AG run under a new identifier**

Run:

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --symbols AG \
  --start 2025-01-01 \
  --end 2026-07-01 \
  --initial-equity 1e+06 \
  --download-minute-data \
  --run-id 20260903_ag_duplicate_contract_fix
```

Expected: metadata preparation no longer reports `AMBIGUOUS_CONTRACT` for repeated `ag2502` rows. Any unrelated fail-closed metadata issue is reported separately.

- [ ] **Step 4: Inspect scoped source sections**

Run:

```bash
git status --short -- \
  cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py \
  cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py
```

Expected: only the two pre-existing untracked target files are listed; inspect the edited functions and tests directly because Git has no tracked baseline for these files.
