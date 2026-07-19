# ETF Industry Turnover-Weighted Offline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the default industry-data build fully offline by replacing market-value weighting with strict previous-industry-trading-day turnover weighting and omitting unavailable market-value fields.

**Architecture:** Keep share-size normalization and downloading isolated in `data.py` for future use, but remove them from the default build path. Prepare ETF daily rows with industry metadata once, then derive both liquidity totals and the turnover-weighted industry OHLC index from that prepared frame before adding existing rolling and technical indicators.

**Tech Stack:** Python 3.13, pandas, NumPy, unittest/pytest, Ruff.

**Design:** `stock/etf/20260719_industry_turnover_weighted_offline_design.md`

**Workspace:** Modify only `stock/etf/**`. Preserve existing backtest outputs and ETF source caches.

---

### Task 1: Previous-Day Turnover-Weighted Industry Index

**Files:**
- Modify: `stock/etf/industry.py`
- Modify: `stock/etf/tests/test_industry.py`

- [ ] **Step 1: Replace the market-value-weight test with a failing turnover-weight test**

Create two ETFs whose previous-day turnover weights are 25% and 75%, while their
share-size values would imply different weights. Call the new share-free API:

```python
result = build_industry_price_index(daily, metadata).set_index("datetime")

self.assertAlmostEqual(result.loc[dates[1], "open"], 101.25)
self.assertAlmostEqual(result.loc[dates[1], "high"], 105.25)
self.assertAlmostEqual(result.loc[dates[1], "low"], 99.25)
self.assertAlmostEqual(result.loc[dates[1], "close"], 102.5)
```

Set day-one turnover to `100` and `300`. Day-two relatives remain 1.05/1.15/1.00/1.10
for ETF A and 1.00/1.02/0.99/1.00 for ETF B.

- [ ] **Step 2: Run the focused test and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_industry.py::IndustryTests::test_industry_index_uses_previous_day_turnover_weights -q
```

Expected: `TypeError` because `build_industry_price_index` still requires
`share_size`.

- [ ] **Step 3: Replace share alignment with prepared daily components**

Add `_prepare_industry_components(daily, metadata)` that validates these columns:

```python
required = {
    "symbol", "datetime", "open", "high", "low", "close",
    "volume", "turnover",
}
```

Parse dates, reject duplicate symbol/date keys, attach one-to-one industry metadata,
coerce volume and turnover to numeric, and add `volume_in_units = volume * 100`.

- [ ] **Step 4: Implement strict previous-industry-day turnover weights**

Change `build_industry_price_index` to accept only `(daily, metadata)`. For each ETF,
shift `turnover` and `datetime`; for each industry calendar, shift its unique dates.
Eligibility must include:

```python
previous_turnover > 0
previous_bar_datetime == previous_industry_datetime
np.isfinite(current_ohlc).all()
```

Normalize `previous_turnover` within each industry/date and reuse the existing base-100
recursive OHLC construction and OHLC-bound enforcement. Use each ETF's strictly
previous industry-trading-day adjusted `close` as the return denominator; the local
lifecycle cache's `pre_close` is unadjusted and must not be mixed with adjusted OHLC.

- [ ] **Step 5: Update new-listing and suspension tests**

Remove share-size arguments from the new-listing and gap fixtures. Assert a new ETF is
excluded on its first day and an ETF missing the immediately previous industry date is
excluded after resuming, even when its stale turnover is much larger.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m pytest stock/etf/tests/test_industry.py -q
```

Expected: all industry tests pass.

### Task 2: Share-Free Aggregation and Stable Output Schema

**Files:**
- Modify: `stock/etf/industry.py`
- Modify: `stock/etf/tests/test_industry.py`

- [ ] **Step 1: Write failing aggregation and schema tests**

Change aggregation calls to `aggregate_industry_daily(daily, metadata)` and assert:

```python
self.assertEqual(first["etf_count"], 2)
self.assertEqual(first["daily_turnover"], 300.0)
self.assertEqual(first["daily_volume"], 300.0)
self.assertNotIn("total_market_value", result.columns)
self.assertNotIn("market_value_etf_count", result.columns)
self.assertNotIn("share_coverage_ratio", result.columns)
```

Update the final schema expectation so base columns are exactly:

```python
[
    "datetime", "industry", "industry_name", "etf_count",
    "open", "high", "low", "close",
]
```

- [ ] **Step 2: Run tests and verify red**

Run `python3 -m pytest stock/etf/tests/test_industry.py -q`.

Expected: failures because aggregation and combined-build functions still require
share-size input and output market-value fields.

- [ ] **Step 3: Implement liquidity-only aggregation**

Add `_aggregate_prepared_components(components)` using grouped named aggregations:

```python
etf_count=("symbol", "nunique")
daily_turnover=("turnover", lambda values: values.sum(min_count=1))
daily_volume=("volume_in_units", lambda values: values.sum(min_count=1))
```

Add `industry_name`, stable sorting, and no market-value fields.

- [ ] **Step 4: Reuse preparation once in combined build**

Implement:

```python
def build_industry_daily(daily, metadata):
    components = _prepare_industry_components(daily, metadata)
    totals = _aggregate_prepared_components(components)
    index = _build_industry_price_index_from_components(components)
    return totals.merge(index, validate="one_to_one", ...)
```

Keep public `aggregate_industry_daily` and `build_industry_price_index` as small
wrappers so unit tests remain focused while the million-row production path prepares
and joins metadata only once.

- [ ] **Step 5: Update output columns and indicator requirements**

Remove three market-value columns from `INDUSTRY_BASE_COLUMNS`. Preserve all rolling,
EMA, ATR and ADX columns and existing continuous-OHLC segment reset behavior.

- [ ] **Step 6: Run tests and lint**

Run:

```bash
python3 -m pytest stock/etf/tests/test_industry.py -q
python3 -m ruff check stock/etf/industry.py stock/etf/tests/test_industry.py
```

Expected: all focused tests and Ruff pass.

### Task 3: Fully Offline CLI

**Files:**
- Modify: `stock/etf/build_industry_data.py`
- Modify: `stock/etf/tests/test_build_industry_data.py`

- [ ] **Step 1: Replace share-cache tests with a failing no-network build test**

Delete tests for cache gaps, share downloads and share permission errors. Add a test
that does not create a share cache and patches the downloader constructor:

```python
with patch("stock.etf.data.TushareEtfDownloader") as downloader:
    result = build_industry_csv(
        daily_path=self.daily_path,
        metadata_path=self.metadata_path,
        output_path=self.output_path,
    )

downloader.assert_not_called()
self.assertNotIn("total_market_value", result.columns)
self.assertEqual(len(pd.read_csv(self.output_path)), 3)
```

- [ ] **Step 2: Run the CLI tests and verify red**

Run `python3 -m pytest stock/etf/tests/test_build_industry_data.py -q`.

Expected: signature failures because `share_cache_path` is still mandatory and the
build still enters the share download path.

- [ ] **Step 3: Remove share dependencies from the CLI module**

Remove Tushare, rate-limit, share-normalization and share-cache imports and helpers
from `build_industry_data.py`. Change `build_industry_csv` to accept only:

```python
daily_path, metadata_path, output_path, start=None, end=None
```

Call `build_industry_daily(daily, metadata)` directly, add indicators, and atomically
write output. Reject duplicate ETF symbol/date input. Retain all source history through
`end` for index and indicator calculation, then trim the finished output to `start`.
Preserve unique sibling temporary files.

- [ ] **Step 4: Simplify parser arguments**

Retain only `--daily`, `--metadata`, `--output`, `--start`, and `--end`. Remove
`--share-cache` and `--skip-share-download`; update `main` accordingly.

- [ ] **Step 5: Preserve metadata and concurrency regression tests**

Keep tests proving missing industry metadata fails before output and concurrent atomic
writes use separate temporary files. Update calls to the new signature.

- [ ] **Step 6: Run focused and full tests**

Run:

```bash
python3 -m pytest stock/etf/tests/test_build_industry_data.py -q
python3 -m pytest stock/etf/tests -q
```

Expected: all ETF tests pass without network access.

### Task 4: Real Offline Build and Quality Audit

**Files:**
- Create at runtime: `stock/etf/data/industry.csv`
- Modify: `stock/etf/20260719_industry_turnover_weighted_offline_design.md` only if measured output contradicts the documented schema

- [ ] **Step 1: Execute the default build without network escalation**

Run:

```bash
python3 -m stock.etf.build_industry_data
```

Expected: exit code 0, no Tushare request, and a nonempty
`stock/etf/data/industry.csv`.

- [ ] **Step 2: Audit the generated CSV**

Run a read-only pandas check asserting:

```python
assert not frame.duplicated(["datetime", "industry"]).any()
assert frame["datetime"].is_monotonic_increasing
assert frame["industry"].nunique() == 21
assert not {"total_market_value", "market_value_etf_count",
            "share_coverage_ratio"} & set(frame.columns)
valid = frame[["open", "high", "low", "close"]].dropna()
assert (valid["high"] >= valid[["open", "close"]].max(axis=1)).all()
assert (valid["low"] <= valid[["open", "close"]].min(axis=1)).all()
```

Print row count, date range, industry count, file size, latest rows and null counts for
the 180-day indicators.

- [ ] **Step 3: Run final quality gates**

Run:

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
```

Expected: all tests and static checks pass.

- [ ] **Step 4: Review changed files and commit implementation**

Review only the files listed in this plan, then commit:

```bash
git add stock/etf/industry.py stock/etf/build_industry_data.py \
  stock/etf/tests/test_industry.py stock/etf/tests/test_build_industry_data.py \
  stock/etf/20260719_industry_turnover_weighted_offline_implementation.md
git commit -m "feat: build ETF industry data offline"
```
