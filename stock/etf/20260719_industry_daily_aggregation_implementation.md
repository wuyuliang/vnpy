# ETF Industry Daily Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable daily A-share ETF industry dataset containing true market-price value, rolling liquidity, a point-in-time industry OHLC index, EMA/ATR/ADX indicators, and write it to `stock/etf/data/industry.csv`.

**Architecture:** Keep transformations as pure pandas functions in `industry.py`: normalize Tushare share data, align it without lookahead, aggregate daily fundamentals, construct a prior-day-market-value-weighted price index, then add rolling indicators. Keep network/cache/CLI concerns in `build_industry_data.py`; the CLI may reuse a complete cache offline and must fail clearly rather than fabricate market value when Tushare denies `etf_share_size` access.

**Tech Stack:** Python 3.13, pandas, NumPy, Tushare Pro, unittest/pytest, Ruff.

**Design:** `stock/etf/20260719_industry_daily_aggregation_design.md`

**Workspace:** Modify only `stock/etf/**`. Preserve all existing ETF data and output directories.

---

### Task 1: Normalize and Download Historical ETF Shares

**Files:**
- Create: `stock/etf/industry.py`
- Modify: `stock/etf/data.py`
- Create: `stock/etf/tests/test_industry.py`
- Modify: `stock/etf/tests/test_data.py`

- [ ] **Step 1: Write failing share-normalization tests**

Add tests proving units, dates, deduplication, invalid values and the two price chains:

```python
raw = pd.DataFrame({
    "trade_date": ["20260102", "20260102", "20260105"],
    "ts_code": ["A.SH", "A.SH", "A.SH"],
    "total_share": [10.0, 12.0, 15.0],
    "total_size": [101.0, 121.2, 153.0],
    "close": [1.0, 1.01, 1.02],
})
result = normalize_share_size(raw)
self.assertEqual(result["fund_units"].tolist(), [120_000.0, 150_000.0])
self.assertEqual(result["market_close"].tolist(), [1.01, 1.02])
self.assertEqual(result["reported_total_size"].tolist(), [1_212_000.0, 1_530_000.0])
```

- [ ] **Step 2: Run the focused test and verify red**

Run `python3 -m pytest stock/etf/tests/test_industry.py -q`.

Expected: import failure because `stock.etf.industry` does not exist.

- [ ] **Step 3: Implement minimal share normalization**

Create `normalize_share_size(raw)` returning stable columns
`symbol, datetime, fund_units, market_close, reported_total_size`. Rename Tushare
fields, parse dates, multiply `total_share` and `total_size` by 10,000, require
positive units, coerce nonpositive prices to null, keep the last duplicate, then
sort by symbol/date. A missing price must not discard an otherwise usable share
observation.

- [ ] **Step 4: Write a failing downloader adapter test**

Use a fake Pro client and assert the downloader calls:

```python
pro.etf_share_size(
    ts_code="A.SH",
    start_date="20260101",
    end_date="20260131",
)
```

The adapter must combine symbols deterministically, sleep only between requests,
and return an empty normalized frame when no records exist.

- [ ] **Step 5: Add `TushareEtfDownloader.fetch_share_size`**

Call `_query_tushare` for each sorted symbol and normalize the concatenated result.
The official per-call limit is 5000 rows; one ETF over the requested date range is
below that limit. Preserve the original exception text when access is denied so the
CLI can report the exact missing permission.

- [ ] **Step 6: Run tests and lint**

Run:

```bash
python3 -m pytest stock/etf/tests/test_industry.py stock/etf/tests/test_data.py -q
python3 -m ruff check stock/etf/industry.py stock/etf/data.py stock/etf/tests/test_industry.py stock/etf/tests/test_data.py
```

Expected: all focused tests pass and Ruff reports no errors.

### Task 2: Point-in-Time Share Alignment and Daily Industry Totals

**Files:**
- Modify: `stock/etf/industry.py`
- Modify: `stock/etf/tests/test_industry.py`

- [ ] **Step 1: Write failing no-lookahead and aggregation tests**

Build two ETFs in one industry with share records beginning on different dates.
Assert that `fund_units` is absent before each ETF's first record, forward-filled
after it, and never backfilled. On exact share-report dates assert:

```python
total_market_value = sum(fund_units * market_close)
daily_turnover = sum(turnover)
daily_volume = sum(volume * 100)
share_coverage_ratio = market_value_etf_count / etf_count
```

这里的 `volume` 来自现有前复权策略缓存，所以结果是前复权口径份额；不得标记为交易所
原始成交份额。

Also assert metadata symbols without an `industry` fail with a clear `ValueError`.

- [ ] **Step 2: Run focused tests and verify red**

Run `python3 -m pytest stock/etf/tests/test_industry.py -q`.

Expected: failures for missing `align_share_size` and `aggregate_industry_daily`.

- [ ] **Step 3: Implement point-in-time alignment**

Implement `align_share_size(daily, share_size)` with `merge_asof(...,
direction="backward", by="symbol")` for `fund_units`, then merge `market_close`
and `reported_total_size` by the exact symbol/date key. This makes only shares
forward-fill while market prices remain exact-date values.

- [ ] **Step 4: Implement daily totals and industry names**

Join only metadata fields `symbol, industry`; validate uniqueness and completeness.
Add a fixed `INDUSTRY_NAMES` mapping for every current classification, including
`broad_or_other: 宽基及其他`. Aggregate `etf_count`,
`market_value_etf_count`, `share_coverage_ratio`, `total_market_value`,
`daily_turnover`, and `daily_volume` by `datetime, industry`.

- [ ] **Step 5: Run focused tests**

Run `python3 -m pytest stock/etf/tests/test_industry.py -q`.

Expected: all share-alignment and total tests pass.

### Task 3: Construct the Prior-Day-Weighted Industry OHLC Index

**Files:**
- Modify: `stock/etf/industry.py`
- Modify: `stock/etf/tests/test_industry.py`

- [ ] **Step 1: Write failing index tests**

Create two ETFs with unequal previous-day market values and known OHLC relatives.
Assert each date uses normalized previous-day market-value weights and a base of
100. Add regression tests proving:

```text
price unchanged + share count changed => industry close unchanged
newly listed ETF => excluded until it has a previous-day market value
high >= max(open, close), low <= min(open, close)
```

- [ ] **Step 2: Run the focused tests and verify red**

Run `python3 -m pytest stock/etf/tests/test_industry.py -q`.

Expected: failure for missing `build_industry_price_index`.

- [ ] **Step 3: Implement index constituent eligibility**

Within each symbol, shift market value by one row and require it to come from the
immediately preceding ETF bar. For date T require positive previous market value,
positive `pre_close`, and valid current OHLC. Normalize eligible weights within
industry/date; never use current-day market value as a weight.

- [ ] **Step 4: Implement recursive index OHLC**

For the first valid weighted day set prior industry close to 100 and apply that
day's weighted OHLC relatives. For later valid days multiply the last valid industry
close by the current weighted relatives. If a date has no eligible constituents,
leave OHLC null and retain the last valid close only as internal state for the next
valid date. Enforce OHLC bounds against open and close to absorb floating-point
roundoff.

- [ ] **Step 5: Join index and totals**

Implement `build_industry_daily(daily, metadata, share_size)` to combine the daily
totals and index on `datetime, industry`, add `industry_name`, and sort with a unique
key.

- [ ] **Step 6: Run focused tests**

Run `python3 -m pytest stock/etf/tests/test_industry.py -q`.

Expected: all index tests pass.

### Task 4: Rolling Liquidity and Technical Indicators

**Files:**
- Modify: `stock/etf/industry.py`
- Modify: `stock/etf/tests/test_industry.py`

- [ ] **Step 1: Write failing rolling-window tests**

Generate 200 industry rows and assert `turnover_sum_N` and `volume_sum_N` equal
`rolling(N, min_periods=N).sum()` for N in `1,3,5,10,20,60,180`. Assert the row
immediately before each full window is null.

- [ ] **Step 2: Write failing indicator tests**

Compare `ema_N` to pandas EWM and `atr_N`/`adx_N` to existing
`calculate_atr`/`calculate_adx` for each approved period. Assert `adx_1` is absent,
the final schema is stable, and calculations never cross industry boundaries.

- [ ] **Step 3: Run focused tests and verify red**

Run `python3 -m pytest stock/etf/tests/test_industry.py -q`.

Expected: missing rolling and indicator columns.

- [ ] **Step 4: Implement `add_industry_indicators`**

For each sorted industry group, add rolling sums for periods
`(1, 3, 5, 10, 20, 60, 180)`, close EMA with `adjust=False` and
`min_periods=period`, Wilder ATR for the same periods, and Wilder ADX for
`(3, 5, 10, 20, 60, 180)`. Return columns in the exact design order.

- [ ] **Step 5: Run focused tests and lint**

Run:

```bash
python3 -m pytest stock/etf/tests/test_industry.py -q
python3 -m ruff check stock/etf/industry.py stock/etf/tests/test_industry.py
```

Expected: all tests pass and Ruff reports no errors.

### Task 5: CLI, Cache Safety and Real Dataset Build

**Files:**
- Create: `stock/etf/build_industry_data.py`
- Create: `stock/etf/tests/test_build_industry_data.py`
- Create at runtime: `stock/etf/data/etf_share_size.csv`
- Create at runtime: `stock/etf/data/industry.csv`

- [ ] **Step 1: Write failing CLI tests**

Patch the downloader and test three flows: a complete cache with
`--skip-share-download`; an incomplete cache merged with downloaded rows; and an
access-denied exception that exits nonzero without overwriting an existing cache or
output. Assert atomic CSV writes use a sibling temporary file followed by replace.

- [ ] **Step 2: Run CLI tests and verify red**

Run `python3 -m pytest stock/etf/tests/test_build_industry_data.py -q`.

Expected: module import failure.

- [ ] **Step 3: Implement the CLI**

Support `--daily`, `--metadata`, `--share-cache`, `--output`,
`--skip-share-download`, `--start`, and `--end`. Default to the approved lifecycle
cache and `stock/etf/data/industry.csv`. Derive symbols and date bounds from inputs,
merge normalized old/new share rows by symbol/date, and write cache/output
atomically. Log row counts, dates, industry count and latest coverage.

- [ ] **Step 4: Run CLI tests and all ETF tests**

Run:

```bash
python3 -m pytest stock/etf/tests/test_build_industry_data.py -q
python3 -m pytest stock/etf/tests -q
```

Expected: all tests pass.

- [ ] **Step 5: Execute the real build**

Run:

```bash
python3 -m stock.etf.build_industry_data
```

Expected with an authorized token: the two runtime CSV files are nonempty and span
the ETF cache dates. With the currently verified unauthorized token: the command
must stop with Tushare's `etf_share_size` permission error and preserve all prior
files; rerun unchanged after upgrading the token permission or supplying an
authorized `etf_share_size.csv` cache.

- [ ] **Step 6: Validate generated data**

Check key uniqueness, sorted dates, no invalid OHLC, no values before first share
records, rolling-window warmups, latest-date coverage by industry, and reconcile a
sample industry's market value to its ETFs. Print these checks in a compact summary.

- [ ] **Step 7: Run final quality gates**

Run:

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
```

Expected: all tests and static checks pass.
