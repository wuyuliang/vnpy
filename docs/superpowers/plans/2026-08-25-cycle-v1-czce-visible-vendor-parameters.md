# Cycle V1 CZCE Visible Vendor Parameters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a CZCE contract use the same-day official settlement parameters when the Jin10 row exists but was published after the session opened.

**Architecture:** Preserve the existing Jin10-first metadata flow. Add one CZCE-only branch at the late-publication boundary that builds the already-validated official vendor row for the same source date; all missing or mismatched official data continues to fail closed.

**Tech Stack:** Python 3.13, pandas, pytest, existing cycle_v1 metadata builder.

---

### Task 1: Reproduce the PF2609 visibility failure

**Files:**
- Test: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`

- [ ] **Step 1: Write the failing integration test**

Add `test_build_extension_frames_uses_czce_official_pf_when_jin10_is_late` next to the existing CZCE PF official-fee test. Its fake client must provide:

```python
jin10_row = {
    **_vendor_row(),
    "日期": "20260713",
    "合约品种": "短纤609",
    "合约代码": "PF609",
    "手续费公布时间": "2026-07-13 23:40:44",
    "价格公布时间": "2026-07-13 23:57:26",
    "现价": 7104.0,
    "涨停板": 7744.0,
    "跌停板": 6464.0,
    "保证金/买开": "10%",
    "保证金/卖开": "10%",
    "开仓": "2元",
    "平昨": "2元",
    "平今": "0元",
}
```

The same client returns this exact official snapshot for 2026-07-13:

```python
_parse_czce_settlement_parameters_text(
    "header\n"
    "header2\n"
    "PF609|7,104.00|N|0|10|±9|2.00|绝对值|0.00|0.00|15550|\n",
    date(2026, 7, 13),
)
```

Build one `ContractDate` for `PF2609.ZCE` on 2026-07-14. Assert the result succeeds, limits are `7744.0/6464.0`, fees are `2.0/2.0/0.0`, and `fee["source"]` contains `CZCE_OFFICIAL_TUSHARE`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m pytest -q cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py::test_build_extension_frames_uses_czce_official_pf_when_jin10_is_late
```

Expected: FAIL because the builder walks from 2026-07-13 to an older Jin10 snapshot and reaches `MISSING_VISIBLE_VENDOR_PARAMETERS` instead of using the visible official CZCE row.

### Task 2: Use the visible CZCE official row

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py:131`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_builder.py:1840-1990`
- Test: `cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py`

- [ ] **Step 1: Add the minimal CZCE late-row fallback**

After determining that `max(price_known_at, fee_known_at) > session_open`, add a branch limited to the prior-open snapshot and CZCE:

```python
if vendor_source_date == prior_open and exchange_token == "CZCE":
    fetch_czce_parameters = getattr(
        source_client,
        "fetch_czce_settlement_parameters",
        None,
    )
    if not callable(fetch_czce_parameters):
        raise MetadataBuildError(
            "MISSING_CZCE_OFFICIAL_SOURCE_CLIENT",
            f"{pair.contract_code} {pair.trade_date}",
        )
    parameters = czce_parameter_sources.get(prior_open)
    if parameters is None:
        parameters = fetch_czce_parameters(prior_open)
        czce_parameter_sources[prior_open] = parameters
    vendor_row = _czce_official_vendor_row(
        local_contract=pair.contract_code,
        root_symbol=pair.root_symbol,
        source_date=prior_open,
        effective_on=pair.trade_date,
        price_tick=price_tick,
        expected_settlement=_positive_float(
            prior_row.get("settle"),
            "PRIOR_SETTLEMENT",
        ),
        parameters=parameters,
    )
    price_known_at = _published_at(
        vendor_row.get("价格公布时间"),
        prior_open,
        "PRICE_PUBLISHED_AT",
    )
    fee_known_at = _published_at(
        vendor_row.get("手续费公布时间"),
        prior_open,
        "FEE_PUBLISHED_AT",
    )
    break
```

Keep exact-contract and settlement checks in `_czce_official_vendor_row`; do not catch or downgrade their failures. Increase `BUILDER_SCHEMA_VERSION` from `57` to `58` so old cache results cannot bypass the new rule.

- [ ] **Step 2: Run the focused test and verify GREEN**

Run the Task 1 command again.

Expected: `1 passed`.

- [ ] **Step 3: Run the metadata-builder tests**

Run:

```bash
python3 -m pytest -q cta/strategy/brooks/cycle_v1/tests/test_vendor_metadata_builder.py
```

Expected: all tests pass.

- [ ] **Step 4: Run the complete cycle_v1 suite**

Run:

```bash
python3 -m pytest -q cta/strategy/brooks/cycle_v1/tests
```

Expected: all tests pass.

### Task 3: Verify the report outcome

**Files:**
- Create: a new run directory under `cta/strategy/brooks/report/cycle_v1/`

- [ ] **Step 1: Rerun the recorded command from the repository root**

```bash
python3 -m cta.strategy.brooks.cycle_v1.backtest.runner \
  --symbols LC \
  --top-n 2 \
  --include-ema-eligible \
  --download-minute-data \
  --start 2026-01-01 \
  --end 2026-07-27 \
  --long-tf 30min \
  --medium-tf 5min \
  --short-tf 1min \
  --initial-equity 200000
```

Expected: the new `report.md`, `summary.json`, and `metadata_gaps.csv` do not contain `MISSING_VISIBLE_VENDOR_PARAMETERS: PF2609.ZCE before 2026-07-13T21:00:00+08:00`. If another independently valid metadata gap appears, retain `BLOCKED_METADATA` and report that next blocker without weakening validation.

Implementation files are currently untracked and contain existing work, so execution must not create a Git commit that would claim those pre-existing contents.
