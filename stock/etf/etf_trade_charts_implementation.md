# ETF Trade Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate one 1680x1000 weekly/daily OHLCV trade-review PNG for every ETF traded in the `20260717_last2months_live` run.

**Architecture:** A standalone Pillow renderer reads the existing combined ETF daily CSV, metadata, trades, and positions. Pure transformation functions prepare weekly bars, display windows, trade markers, and symbol summaries; one batch function renders deterministic PNGs plus index and summary files.

**Tech Stack:** Python 3.13, pandas, Pillow, unittest/pytest, Ruff.

---

### Task 1: Bar and marker transformations

**Files:**
- Create: `stock/etf/tests/test_trade_charts.py`
- Create: `stock/etf/render_trade_charts.py`

- [ ] Write failing tests that call these public APIs:

```python
weekly = aggregate_weekly_bars(daily)
window = select_daily_window(daily, report_start, pre_bars=20)
markers = build_trade_markers(trades, weekly=True)
```

- [ ] Assert weekly open/high/low/close/volume use first/max/min/last/sum and `W-FRI` dates.
- [ ] Assert the daily window includes exactly 20 valid bars before the first report bar.
- [ ] Assert markers number buys and sells independently and map weekly markers to Friday.
- [ ] Run `python3 -m pytest stock/etf/tests/test_trade_charts.py -q`; expect failure because `render_trade_charts` does not exist.
- [ ] Implement the three pure functions with normalized, sorted, duplicate-free inputs.
- [ ] Rerun the focused tests; expect all transformation tests to pass.

### Task 2: Card renderer

**Files:**
- Modify: `stock/etf/tests/test_trade_charts.py`
- Modify: `stock/etf/render_trade_charts.py`

- [ ] Add a failing smoke test:

```python
image = render_symbol_card(symbol_row, daily, weekly, trades, position)
assert image.size == (1680, 1000)
image.save(path)
assert path.stat().st_size > 0
```

- [ ] Add a failing filename test for Chinese names and punctuation.
- [ ] Run focused tests and confirm failure because rendering and filename APIs are absent.
- [ ] Implement a Pillow card with header, metadata column, weekly panel, daily panel, volume subplots, candlesticks, date labels, and blue/orange transaction markers.
- [ ] Load a Chinese-capable system font before Latin fallbacks.
- [ ] Rerun focused tests; expect renderer and filename tests to pass.

### Task 3: Batch output and CLI

**Files:**
- Modify: `stock/etf/tests/test_trade_charts.py`
- Modify: `stock/etf/render_trade_charts.py`

- [ ] Add a failing batch test using two symbols where one lacks daily bars.
- [ ] Assert one PNG is rendered, both symbols appear in `index.csv`, and the missing symbol is marked `missing_daily_data` in both index and summary.
- [ ] Implement `render_all_trade_charts(...)`, deterministic PnL sorting, `index.csv`, `render_summary.json`, `--limit`, and `--overwrite`.
- [ ] Rerun focused and full ETF tests.

### Task 4: Real render and visual QA

**Files:**
- Generate: `stock/etf/output/20260717_last2months_live/charts/*.png`
- Generate: `stock/etf/output/20260717_last2months_live/charts/index.csv`
- Generate: `stock/etf/output/20260717_last2months_live/charts/render_summary.json`

- [ ] Run:

```bash
python3 -m stock.etf.render_trade_charts \
  --daily-csv stock/etf/data/20260717_last2months_live/etfs.csv \
  --metadata-csv stock/etf/data/20260717_last2months_live/metadata.csv \
  --trades-csv stock/etf/output/20260717_last2months_live/trades.csv \
  --positions-csv stock/etf/output/20260717_last2months_live/positions.csv \
  --output-dir stock/etf/output/20260717_last2months_live/charts \
  --report-start 2026-05-17 \
  --report-end 2026-07-17 \
  --overwrite
```

- [ ] Verify 26 PNG files, 26 index rows, non-empty image sizes, symbol/name coverage, and markers for all 76 trades.
- [ ] Inspect highest-PnL, lowest-PnL, and open-position images with the image viewer; revise clipping or marker layout if needed.
- [ ] Run `python3 -m pytest stock/tests stock/etf/tests -q`, `python3 -m ruff check stock/etf`, `python3 -m compileall -q stock/etf`, and `git diff --check -- stock/etf`.
