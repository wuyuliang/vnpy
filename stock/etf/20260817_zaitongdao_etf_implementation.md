# 159915.SZ Narrow Channel Quick Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and run the research-only `159915.SZ` weekly narrow-channel long/short backtest specified in `stock/etf/20260817_zaitongdao_etf.md`.

**Architecture:** Add an isolated pure strategy module for data validation, weekly/daily signals, T+1 trade simulation, and R-based statistics. Add a Pillow renderer for the single-symbol weekly/daily/volume chart and a CLI runner that atomically writes the four documented artifacts. Existing ETF strategy and data modules remain untouched.

**Tech Stack:** Python 3.10+, pandas, NumPy, Pillow, unittest/pytest, Ruff.

---

### Task 1: Data, Indicators, and Weekly Channels

**Files:**
- Create: `stock/etf/narrow_channel_etf_strategy.py`
- Create: `stock/etf/tests/test_narrow_channel_etf_strategy.py`

- [x] Write tests that reject missing/duplicate/invalid OHLCV rows and foreign symbols.
- [x] Run `python3 -m pytest stock/etf/tests/test_narrow_channel_etf_strategy.py -q`; expect import failure because the strategy module does not exist.
- [x] Implement `NarrowChannelConfig`, `prepare_etf_bars()`, Wilder ATR, complete `W-FRI` aggregation, and weekly EMA10/ATR14 calculations.
- [x] Add tests proving an unfinished final week is excluded and deterministic rising/falling sequences become `UP_CHANNEL`/`DOWN_CHANNEL` only after two qualifying windows.
- [x] Run the focused test file; expect all Task 1 tests to pass.

Required public API:

```python
@dataclass(frozen=True)
class NarrowChannelConfig:
    symbol: str = "159915.SZ"
    channel_weeks: int = 6
    confirmation_windows: int = 2
    weekly_atr_period: int = 14
    daily_atr_period: int = 14
    one_way_cost_rate: float = 0.0008
    short_borrow_rate: float = 0.08

def prepare_etf_bars(daily: pd.DataFrame, config: NarrowChannelConfig) -> pd.DataFrame: ...
def aggregate_complete_weeks(bars: pd.DataFrame) -> pd.DataFrame: ...
def classify_weekly_channels(weekly: pd.DataFrame, config: NarrowChannelConfig) -> pd.DataFrame: ...
```

### Task 2: Daily Signals and T+1 Backtest

**Files:**
- Modify: `stock/etf/narrow_channel_etf_strategy.py`
- Modify: `stock/etf/tests/test_narrow_channel_etf_strategy.py`

- [x] Add failing tests for the mirrored long/short daily rules, next-open gap rejection, one-position-at-a-time behavior, and future-row invariance.
- [x] Implement `build_narrow_channel_signals()` with EMA5/10/20, ATR14, completed-week as-of merge, structure stops, and the two documented signal types.
- [x] Add failing tests for an entry-day stop breach that exits only at the next open, stop-before-2R priority, daily trend exits, weekly opposite-bar exits, trailing-stop monotonicity, and short borrow cost.
- [x] Implement a single-position event loop in `run_narrow_channel_backtest()` and return `NarrowChannelResult(signals, trades, summary)`.
- [x] Implement directional statistics: completed/open trades, win rate, total/average R, Profit Factor, cumulative-R drawdown, holding days, borrow cost, `MFE>=4R` capture, and independent verdicts.
- [x] Run the focused tests and confirm all strategy tests pass.

Required result columns include:

```text
signals: datetime, weekly_state, long_signal, short_signal, signal_type,
         entry_status, entry_date, position_side, active_stop
trades: trade_id, side, signal_type, signal_date, entry_date, entry_fill,
        initial_stop, exit_date, exit_fill, exit_reason, status,
        holding_days, borrow_cost, mfe_r, mae_r, net_r, profit_capture_ratio
```

### Task 3: Chart and Atomic Runner

**Files:**
- Create: `stock/etf/narrow_channel_etf_chart.py`
- Create: `stock/etf/run_narrow_channel_etf.py`
- Create: `stock/etf/tests/test_run_narrow_channel_etf.py`

- [x] Write a failing end-to-end test using a temporary CSV/audit/output directory.
- [x] Implement a Pillow PNG with a weekly channel panel, a latest-two-year daily candlestick panel with EMA5/EMA10/EMA20, all signal dates, long/short entries and exits, active-stop path, and volume.
- [x] Implement `run_and_write()` and CLI defaults for the frozen source and `stock/etf/output/20260817_zaitongdao_etf/`.
- [x] Atomically replace the complete output directory containing `signals.csv`, `trades.csv`, `summary.json`, and `charts/159915.SZ_周线窄通道快速验证.png`; include source/document hashes and fixed parameters in the summary.
- [x] Run both new test files and confirm the PNG is decodable with the expected dimensions.

### Task 4: Regression, Static Checks, and Real Backtest

**Files:**
- Update checkboxes in: `stock/etf/20260817_zaitongdao_etf_implementation.md`
- Generate: `stock/etf/output/20260817_zaitongdao_etf/**`

- [x] Run `python3 -m pytest stock/etf/tests/test_narrow_channel_etf_strategy.py stock/etf/tests/test_run_narrow_channel_etf.py -q`.
- [x] Run `python3 -m pytest stock/etf/tests -q` and compare with the `579 passed, 59 subtests passed` baseline.
- [x] Run `python3 -m ruff check` on the three new modules and two new tests.
- [x] Run `python3 -m compileall` on the three new modules.
- [x] Run `python3 -m stock.etf.run_narrow_channel_etf --overwrite`.
- [x] Validate CSV/JSON row counts, finite metrics, date ordering, PNG decoding, source range, and no-future audit fields.
- [x] Review the actual long/short signal count, trade count, verdict, Profit Factor, total R, drawdown, and large-trend capture before reporting results.
