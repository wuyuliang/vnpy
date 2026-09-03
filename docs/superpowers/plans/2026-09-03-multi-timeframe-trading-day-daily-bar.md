# Multi-Timeframe Trading-Day Daily Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `multi_time_frame_trend` explicitly aggregate one daily bar from the prior-open-date 21:00 night session through the target exchange trade date's afternoon close.

**Architecture:** Add a small strategy-local aggregation boundary in the backtest runner and route both signal-price and actual-price daily bars through it. The boundary delegates to the existing session-aware aggregator, whose `exchange_trade_date` grouping preserves weekend and holiday mappings.

**Tech Stack:** Python, pandas, pytest

---

### Task 1: Lock the Trading-Day Boundary With a Regression Test

**Files:**
- Modify: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Write the failing Friday-night-to-Monday test**

Add a test that calls the new strategy-local boundary before it exists:

```python
def test_daily_bar_combines_prior_night_with_exchange_trade_date_day() -> None:
    sessions = (
        SessionSpec(
            session_id="night",
            is_night=True,
            segments=(
                SessionSegment("night", time(21), time(21, 2), time(21)),
            ),
        ),
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(
                SessionSegment("day", time(9), time(9, 2), time(9)),
            ),
        ),
    )
    bar_ends = pd.to_datetime(
        [
            "2026-01-09 21:01",
            "2026-01-09 21:02",
            "2026-01-12 09:01",
            "2026-01-12 09:02",
        ]
    ).tz_localize(TZ)
    minute = pd.DataFrame(
        {
            "bar_end": bar_ends,
            "feature_sequence": [1, 2, 3, 4],
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 102.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 101.5, 103.5, 104.5],
            "volume": [10.0, 20.0, 30.0, 40.0],
            "open_interest": [1_000.0, 1_001.0, 1_002.0, 1_003.0],
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": date(2026, 1, 12),
        }
    )

    daily = trend_runner._aggregate_trading_day_daily_bars(
        minute,
        sessions=sessions,
    )

    assert len(daily) == 1
    assert daily.iloc[0]["exchange_trade_date"] == date(2026, 1, 12)
    assert daily.iloc[0]["bar_end"] == bar_ends[-1]
    assert daily.iloc[0]["feature_asof"] == bar_ends[-1]
    assert daily.iloc[0]["open"] == 100.0
    assert daily.iloc[0]["high"] == 105.0
    assert daily.iloc[0]["low"] == 99.0
    assert daily.iloc[0]["close"] == 104.5
    assert daily.iloc[0]["volume"] == 100.0
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_daily_bar_combines_prior_night_with_exchange_trade_date_day
```

Expected: FAIL because `trend_runner` has no `_aggregate_trading_day_daily_bars` attribute.

### Task 2: Route Strategy Daily Bars Through the Trading-Day Boundary

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Add the minimal strategy-local aggregation function**

Add the boundary near `_prepare_strategy_data`:

```python
def _aggregate_trading_day_daily_bars(
    minute_bars: pd.DataFrame,
    *,
    sessions: tuple[object, ...],
) -> pd.DataFrame:
    """Build one completed daily bar per exchange trading day."""
    return aggregate_completed_daily_bars(minute_bars, sessions=sessions)
```

- [ ] **Step 2: Use the boundary for signal and actual daily bars**

Replace both calls in `_prepare_strategy_data`:

```python
daily_signal = _sort_aggregated_bars(
    _aggregate_trading_day_daily_bars(
        signal_minute,
        sessions=loaded.sessions,
    )
)
daily_actual = _sort_aggregated_bars(
    _aggregate_trading_day_daily_bars(
        minute,
        sessions=loaded.sessions,
    )
)
```

- [ ] **Step 3: Run the focused test and verify GREEN**

Run:

```bash
pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_daily_bar_combines_prior_night_with_exchange_trade_date_day
```

Expected: PASS.

- [ ] **Step 4: Run relevant regression suites**

Run:

```bash
pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py cta/strategy/brooks/cycle_v1/tests/test_sessions_rollover_profile.py
```

Expected: all tests pass.

- [ ] **Step 5: Inspect the scoped diff**

Run:

```bash
git diff -- cta/strategy/multi_timeframe_trend_backtest/runner.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py
```

Expected: only the local aggregation boundary, its two call sites, and the regression test are changed.
