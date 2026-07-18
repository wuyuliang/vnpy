# ETF Rotation Staged Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every behavior change and superpowers:verification-before-completion before reporting results.

**Goal:** Build a point-in-time, lower-churn A-share index ETF rotation research pipeline, attribute ATR and ADX periods independently, evaluate one pre-registered portfolio optimization, and render final real-data trade charts.

**Architecture:** Keep indicators stateless, separate base scoring from entry-reference ranking, and keep execution-only state such as confirmation counts, benchmark occupancy and correlation capacity in the backtest loop. Add one experiment orchestrator that calls the existing backtester with explicit immutable configs and writes a comparison table; do not create a general optimization framework.

**Tech Stack:** Python 3.13, pandas, NumPy, Pillow, Tushare, unittest/pytest, Ruff.

**Workspace:** Modify only `stock/etf/**`. Preserve prior caches and outputs. Do not commit unless the user explicitly asks.

**Completion status (2026-07-17):** Tasks 1-8 were implemented and verified. The small experiment
entry is named `run_experiment_report.py` rather than the originally proposed
`run_etf_experiments.py`; strategy variants are run with explicit immutable CLI arguments, then this
reporter compares their existing output directories. Real data is stored under
`data/20260717_2018_20260717_point_in_time_live/`, the full comparison under
`output/20260717_full_experiment_report/`, and the reported research-baseline charts under
`output/20260717_full_clean_atr5_adx10_live/charts/`. No variant passed all final selection gates.

---

### Task 1: Market Consistency and Rolling Liquidity

**Files:**
- Modify: `stock/etf/indicators.py`
- Modify: `stock/etf/ranking.py`
- Modify: `stock/etf/backtest.py`
- Test: `stock/etf/tests/test_indicators.py`
- Test: `stock/etf/tests/test_ranking.py`
- Test: `stock/etf/tests/test_backtest.py`

- [ ] Add a failing indicator test asserting `turnover_median20` equals `turnover.rolling(20, min_periods=20).median()`.
- [ ] Add a failing Risk On test where EMA and ADX pass but `close < ema10`; assert Risk On is false.
- [ ] Add ranking fixtures where a held-quality candidate has low current and rolling turnover. Assert it retains a finite `holding_rank`, while `entry_liquidity_eligible` is false and `entry_rank` is missing.
- [ ] Add a ranking test with two liquid candidates and one non-liquid candidate. Assert `entry_rank` is unique among the liquid reference pool and `holding_rank` is the insertion rank against that pool.
- [ ] Run the focused tests and verify failures are caused by the missing rolling field and split ranks.
- [ ] Calculate `turnover_median20` in `add_indicators`.
- [ ] Refactor ranking into base-score, representative/reference-pool and insertion-rank steps. Preserve the approved RS formula and stable tie keys.
- [ ] Make `entry_symbols` use `entry_rank`; make exit checks use `holding_rank`.
- [ ] Add `close > ema10` to `is_risk_on`.
- [ ] Run focused tests and then the full ETF suite.

### Task 2: Point-in-Time Metadata and Delisting Safety

**Files:**
- Modify: `stock/etf/data.py`
- Modify: `stock/etf/backtest.py`
- Modify: `stock/etf/portfolio.py`
- Test: `stock/etf/tests/test_data.py`
- Test: `stock/etf/tests/test_backtest.py`
- Test: `stock/etf/tests/test_portfolio.py`

- [ ] Add a failing data test containing `status=L`, `status=D` and `status=I`; assert L and D survive ETF normalization while I is excluded.
- [ ] Add a fake Tushare API test asserting `fetch_metadata()` calls `fund_basic(market="E")` without `status="L"` and preserves `delist_date`.
- [ ] Add a backtest test asserting a delisted ETF is eligible before `delist_date` and absent afterward.
- [ ] Add a portfolio/backtest test for a held ETF with no bar on or after `delist_date`; assert a zero-price sell with `delisted_writeoff` closes the position.
- [ ] Run focused tests and verify red.
- [ ] Allow listed and delisted statuses in `filter_etf_universe`; normalize `delist_date`.
- [ ] Fetch all exchange funds, retain L/D metadata and merge `status/delist_date` into prepared bars.
- [ ] Filter candidate rows point-in-time and conservatively write off an untradeable delisted holding.
- [ ] Run focused and full tests.

### Task 3: Exact Benchmark Entry Deduplication

**Files:**
- Modify: `stock/etf/data.py`
- Modify: `stock/etf/ranking.py`
- Modify: `stock/etf/backtest.py`
- Test: `stock/etf/tests/test_data.py`
- Test: `stock/etf/tests/test_ranking.py`
- Test: `stock/etf/tests/test_backtest.py`

- [ ] Add a failing `benchmark_key` normalization test covering whitespace and the `指数收益率×100%` suffix.
- [ ] Add a ranking test with two ETFs tracking the same normalized benchmark; assert only the higher `turnover_median20` ETF receives `entry_rank`.
- [ ] Add an end-to-end test where the lower-liquidity clone is already held; assert no second ETF with the same key is bought and the existing holding is not forcibly rotated.
- [ ] Run tests and verify red.
- [ ] Add deterministic `normalize_benchmark_key`, falling back to symbol for missing benchmarks.
- [ ] Mark the highest-liquidity stable-tie representative in each benchmark group before assigning `entry_rank`.
- [ ] Track held benchmark keys at execution and record `buy_skipped/benchmark_duplicate` when otherwise eligible.
- [ ] Run focused and full tests.

### Task 4: Independent ATR and ADX Experiment Parameters

**Files:**
- Modify: `stock/etf/config.py`
- Modify: `stock/etf/indicators.py`
- Modify: `stock/etf/ranking.py`
- Modify: `stock/etf/portfolio.py`
- Modify: `stock/etf/backtest.py`
- Modify: `stock/etf/run_etf_rotation.py`
- Test: `stock/etf/tests/test_indicators.py`
- Test: `stock/etf/tests/test_portfolio.py`
- Test: `stock/etf/tests/test_cli.py`

- [ ] Replace the fixed-period rejection tests with failing tests that allow only periods 5 or 10 and reject all other values.
- [ ] Add an indicator test asserting `atr5` remains five-day for RS while `risk_atr` follows `atr_period` and `trend_adx` follows `adx_period`.
- [ ] Add a CLI test running `--atr-period 10 --adx-period 5` and asserting those values appear in `summary.json`.
- [ ] Run tests and verify red.
- [ ] Keep config defaults at 5/5, validate membership in `{5, 10}`, and add CLI options.
- [ ] Rename strategy data flow to `risk_atr` and `trend_adx`; retain `atr5/normalized_atr5` for RS and gap auditing.
- [ ] Run focused and full tests.

### Task 5: Pre-Registered Entry and Portfolio Controls

**Files:**
- Modify: `stock/etf/config.py`
- Modify: `stock/etf/backtest.py`
- Test: `stock/etf/tests/test_backtest.py`

- [ ] Add config fields `entry_confirmation_days=1`, `max_entry_gap_atr=None`, `correlation_lookback=60`, `min_correlation_observations=40`, `correlation_threshold=None`, and `max_correlation_weight=None` with validation tests.
- [ ] Add a failing test where an ETF is Top3 for only one day; with confirmation set to two, assert no order, then assert an order after the second consecutive valid signal.
- [ ] Add a failing execution test where `open > signal_close + atr5`; assert `buy_skipped/gap_filter` and no trade.
- [ ] Add a failing 60-day correlation-cap test using two highly correlated ETFs; assert combined entry notional is no more than 30% of prior equity.
- [ ] Add a low-overlap test asserting fewer than 40 common returns does not apply a correlation cap.
- [ ] Run tests and verify red.
- [ ] Maintain consecutive entry counts by symbol at signal close, resetting when entry conditions fail.
- [ ] Carry `signal_close` and `atr5` in pending entries and apply the gap gate before quantity sizing.
- [ ] Calculate pairwise correlations only with returns ending on `signal_date`; combine correlation and industry remaining notional by taking the lower capacity.
- [ ] Run focused and full tests.

### Task 6: Experiment Metrics and Orchestration

**Files:**
- Create: `stock/etf/experiments.py`
- Create: `stock/etf/run_etf_experiments.py`
- Test: `stock/etf/tests/test_experiments.py`

- [ ] Add failing tests for paired round trips, static raw-price gross PnL, total cost, annual one-way turnover, median holding days, Risk On flips, yearly returns and benchmark comparison.
- [ ] Add a failing orchestrator test with tiny local CSVs and two configs; assert separate output directories and one deterministic comparison row per config.
- [ ] Run tests and verify red.
- [ ] Implement pure metric helpers over `BacktestResult` and benchmark bars.
- [ ] Implement explicit named configs: `clean_atr5_adx5`, `clean_atr5_adx10`, `clean_atr10_adx5`, `clean_atr10_adx10`, and `preregistered`.
- [ ] Make `preregistered` use the selected period pair plus Top3, two confirmations, one ATR5 gap, 35% industry and 30% correlation capacity at 0.90.
- [ ] Write `experiment_summary.csv` and `experiment_manifest.json` without selecting a winner in code.
- [ ] Run focused and full tests.

### Task 7: Real Historical Data and Staged Runs

**Files:**
- Create: `stock/etf/data/20260717_ashare_etf_point_in_time_live/**`
- Create: `stock/etf/output/20260717_etf_rotation_optimization/**`

- [ ] Run Ruff, compileall and all ETF tests before network execution.
- [ ] Request real Tushare metadata, benchmark, ETF daily bars and adjustment factors for 2018-01-01 through 2026-07-17; retain and report the first date actually returned by each dataset.
- [ ] Validate status/list/delist coverage, unique symbol-date rows, OHLC constraints, adjustment factors and actual date range.
- [ ] Run the clean default and four ATR/ADX combinations using identical data and costs.
- [ ] Compare annual and rolling results. Select a period pair only if it is directionally stable; otherwise retain default5/5.
- [ ] Run the pre-registered portfolio controls once with the selected periods.
- [ ] Validate no market conflicts, no one-day-turnover exits, no duplicate benchmark positions, all weight/cash/lot constraints and complete audit schemas.

### Task 8: Final Selection, Charts and Review

**Files:**
- Create: `stock/etf/output/20260717_etf_rotation_optimization/final/charts/**`
- Modify: `stock/etf/etf_rotation_strategy.md`

- [ ] Apply the design's predeclared gates to the comparison table and document whether any variant qualifies. Do not choose the highest return if it fails stability or cost gates.
- [ ] Update the canonical strategy document to the selected credible configuration, or explicitly retain the clean baseline when no variant qualifies.
- [ ] Render one weekly/daily/volume chart per traded ETF for the final reported configuration.
- [ ] Verify index counts, trade counts, Chinese names, PNG decoding, 1680x1000 dimensions and buy/sell marker colors.
- [ ] Self-review point-in-time filtering, future-data boundaries, ranking insertion, pending-entry state, correlation windows, cash safety and output reproducibility.
- [ ] Run `python3 -m pytest stock/etf/tests -q`, `python3 -m ruff check stock/etf`, and `python3 -m compileall -q stock/etf`.
