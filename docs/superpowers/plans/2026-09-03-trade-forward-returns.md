# Trade Forward Returns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add direction-adjusted total and 5/10/20/30 tradable-minute returns immediately after `gross_pnl` in the multi-timeframe trend backtest `trades.csv`.

**Architecture:** Keep execution and position state unchanged. Build one deterministic observation-bar table per root symbol from validated primary and roll-execution minute bars, then enrich the final logical-trade DataFrame through a pure helper before constructing `ReplayArtifacts`. The helper matches the traded contract, counts only bars strictly after `entry_time`, and leaves unavailable horizons as `NaN`.

**Tech Stack:** Python 3, pandas, NumPy, pytest

---

## File Structure

- Modify `cta/strategy/multi_timeframe_trend_backtest/engine.py`: define the output columns, normalize observation bars, calculate direction-adjusted returns, and wire the helper into single-symbol and portfolio replay results.
- Modify `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`: cover return formulas, tradable-minute counting, data gaps, contract isolation, replay wiring, and CSV column order.

The two implementation files are pre-existing untracked user files in this workspace. Do not create implementation commits that would accidentally commit all of their unrelated existing contents; leave only the surgical working-tree edits for user review.

### Task 1: Lock the return calculation contract with tests

**Files:**
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Add failing tests for column order and direction-adjusted returns**

Add tests that construct minimal logical trades and observation bars, then call the new helper directly:

```python
def _logical_trade(
    *,
    direction: int,
    contract_code: str,
    entry_time: str,
    entry_price: float,
    exit_price: float,
    exit_time: str,
) -> dict[str, object]:
    return {
        "symbol": "AG",
        "contract_code": contract_code,
        "direction": direction,
        "entry_time": pd.Timestamp(entry_time, tz=TZ),
        "exit_time": pd.Timestamp(exit_time, tz=TZ),
        "entry_price": entry_price,
        "exit_price": exit_price,
    }


def test_trade_return_columns_follow_gross_pnl() -> None:
    gross_index = trend_engine.TRADE_COLUMNS.index("gross_pnl")
    assert trend_engine.TRADE_COLUMNS[gross_index + 1:gross_index + 6] == (
        "total_return",
        "return_5min",
        "return_10min",
        "return_20min",
        "return_30min",
    )


def test_trade_returns_are_positive_for_profitable_long_and_short() -> None:
    trades = pd.DataFrame(
        [
            _logical_trade(
                direction=1,
                contract_code="AG2602.SHF",
                entry_time="2026-01-05 09:05",
                entry_price=100.0,
                exit_price=110.0,
                exit_time="2026-01-05 09:06",
            ),
            _logical_trade(
                direction=-1,
                contract_code="AG2604.SHF",
                entry_time="2026-01-05 09:05",
                entry_price=100.0,
                exit_price=90.0,
                exit_time="2026-01-05 09:06",
            ),
        ]
    )
    primary = pd.concat(
        [
            _return_bars("AG2602.SHF", start=101.0, step=1.0),
            _return_bars("AG2604.SHF", start=99.0, step=-1.0),
        ],
        ignore_index=True,
    )

    result = trend_engine._with_trade_returns(
        trades,
        observation_bars_by_symbol={
            "AG": trend_engine._observation_bars(primary, pd.DataFrame())
        },
    )

    assert result["total_return"].tolist() == pytest.approx([10.0, 10.0])
    assert result["return_5min"].tolist() == pytest.approx([5.0, 5.0])
    assert result["return_10min"].tolist() == pytest.approx([10.0, 10.0])
    assert result["return_20min"].tolist() == pytest.approx([20.0, 20.0])
    assert result["return_30min"].tolist() == pytest.approx([30.0, 30.0])
```

Define `_return_bars` in the test file so its first four rows end at `09:06` through `09:09`, and the remaining rows begin at `13:31`. Use close prices `start + step * index`; this makes the fifth observed bar cross the lunch break and proves wall-clock gaps are ignored.

- [ ] **Step 2: Add failing tests for early exit, missing horizons, and source priority**

Add a test where `exit_time` is earlier than the fifth post-entry bar and assert `return_5min` is still populated. Provide only nine post-entry bars and assert `return_10min`, `return_20min`, and `return_30min` are `NaN`. Add overlapping primary and supplemental bars for the same `contract_code + bar_end` with different closes and assert the primary close wins; add a supplemental-only later bar and assert it fills the missing timestamp. Include a trade for another contract and assert no other contract is used to fill a missing horizon.

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
pytest -q \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_trade_return_columns_follow_gross_pnl \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_trade_returns_are_positive_for_profitable_long_and_short \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_trade_returns_continue_after_exit_and_respect_market_data_boundaries
```

Expected: failures because the five columns and `_with_trade_returns` / `_observation_bars` do not exist yet.

### Task 2: Implement the pure return enrichment helpers

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Add the new columns immediately after `gross_pnl`**

Define:

```python
FORWARD_RETURN_HORIZONS = (5, 10, 20, 30)
TRADE_RETURN_COLUMNS = (
    "total_return",
    *(f"return_{minutes}min" for minutes in FORWARD_RETURN_HORIZONS),
)
```

Insert `*TRADE_RETURN_COLUMNS` immediately after `"gross_pnl"` in `TRADE_COLUMNS`.

- [ ] **Step 2: Implement deterministic observation-bar normalization**

Add `_observation_bars(primary, supplemental)` near the existing minute validation helpers. It must select `bar_end`, `contract_code`, and `close`; assign source priorities `0` and `1`; concatenate; sort stably by `contract_code`, `bar_end`, and priority; drop duplicate `contract_code + bar_end` rows keeping the primary row; and return rows sorted by contract and time. An empty supplemental frame is valid.

```python
def _observation_bars(
    primary: pd.DataFrame,
    supplemental: pd.DataFrame,
) -> pd.DataFrame:
    columns = ["bar_end", "contract_code", "close"]
    frames: list[pd.DataFrame] = []
    for priority, frame in enumerate((primary, supplemental)):
        if frame.empty:
            continue
        selected = frame.loc[:, columns].copy()
        selected["_source_priority"] = priority
        frames.append(selected)
    if not frames:
        return pd.DataFrame(columns=columns)
    result = pd.concat(frames, ignore_index=True).sort_values(
        ["contract_code", "bar_end", "_source_priority"], kind="stable"
    )
    return (
        result.drop_duplicates(["contract_code", "bar_end"], keep="first")
        .sort_values(["contract_code", "bar_end"], kind="stable")
        .drop(columns="_source_priority")
        .reset_index(drop=True)
    )
```

- [ ] **Step 3: Implement direction-adjusted trade enrichment**

Add `_with_trade_returns(trades, *, observation_bars_by_symbol, default_symbol=None)`. Copy the input frame; initialize the four forward columns to `math.nan`; calculate `total_return` vectorially; build a lookup keyed by `(symbol, contract_code)`; and for each trade use `searchsorted(entry_time, side="right")` so the entry bar is excluded. For each horizon use row `start + horizon - 1`; accept only finite positive closes, otherwise leave `NaN`. Resolve a blank single-symbol trade with `default_symbol`, but never fall back across contracts.

```python
def _with_trade_returns(
    trades: pd.DataFrame,
    *,
    observation_bars_by_symbol: dict[str, pd.DataFrame],
    default_symbol: str | None = None,
) -> pd.DataFrame:
    result = trades.copy()
    for column in TRADE_RETURN_COLUMNS:
        result[column] = math.nan
    if result.empty:
        return result.reindex(columns=TRADE_COLUMNS)

    direction = pd.to_numeric(result["direction"], errors="coerce")
    entry_price = pd.to_numeric(result["entry_price"], errors="coerce")
    exit_price = pd.to_numeric(result["exit_price"], errors="coerce")
    result["total_return"] = (
        100.0 * direction * (exit_price - entry_price) / entry_price
    )

    lookup: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol, bars in observation_bars_by_symbol.items():
        for contract, contract_bars in bars.groupby("contract_code", sort=False):
            lookup[(str(symbol), str(contract))] = contract_bars.reset_index(drop=True)

    for row_index, trade in result.iterrows():
        symbol = str(trade.get("symbol", "") or "").strip()
        if not symbol:
            symbol = str(default_symbol or "")
        contract_bars = lookup.get((symbol, str(trade["contract_code"])))
        if contract_bars is None:
            continue
        entry_time = pd.Timestamp(trade["entry_time"])
        start = int(contract_bars["bar_end"].searchsorted(entry_time, side="right"))
        for horizon in FORWARD_RETURN_HORIZONS:
            offset = start + horizon - 1
            if offset >= len(contract_bars):
                continue
            observed_price = float(contract_bars.iloc[offset]["close"])
            if not math.isfinite(observed_price) or observed_price <= 0:
                continue
            result.at[row_index, f"return_{horizon}min"] = (
                100.0
                * float(trade["direction"])
                * (observed_price - float(trade["entry_price"]))
                / float(trade["entry_price"])
            )
    return result.reindex(columns=TRADE_COLUMNS)
```

- [ ] **Step 4: Run the focused tests**

Run the three tests from Task 1.

Expected: all three pass.

### Task 3: Wire both replay paths and verify final CSV output

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: Add failing single-symbol and portfolio integration assertions**

Add `_replay_return_bars(contract_code, step)` to build a `09:05` signal bar, a `09:06` entry bar at `100.0`, and exactly 30 later minute bars ending `09:07` through `09:36`, with closes `100.0 + step * minute_number`. Use lows above the default `98.0` stop.

Add `test_single_replay_populates_trade_return_columns` using `step=1.0`. The interval-end exit is `130.0`; assert `total_return == 30.0` and forward returns equal `5.0`, `10.0`, `20.0`, and `30.0`.

Add `test_portfolio_replay_isolates_forward_returns_by_symbol_and_contract`. Replay AG with `step=1.0` and AU with `step=2.0`, using separate candidates and `PortfolioReplayInput` values. Assert AG `return_5min == 5.0` and AU `return_5min == 10.0`; these deliberately different same-timestamp prices detect cross-symbol or cross-contract leakage.

In the existing report publishing test, read `trades.csv` and assert:

```python
written_trades = pd.read_csv(output / "trades.csv")
gross_index = written_trades.columns.get_loc("gross_pnl")
assert written_trades.columns[gross_index + 1:gross_index + 6].tolist() == [
    "total_return",
    "return_5min",
    "return_10min",
    "return_20min",
    "return_30min",
]
```

- [ ] **Step 2: Run the new integration tests and verify they fail**

Run:

```bash
pytest -q \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_single_replay_populates_trade_return_columns \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_portfolio_replay_isolates_forward_returns_by_symbol_and_contract \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_report_contains_metrics_command_and_audit_tables
```

Expected: replay artifacts still contain unpopulated return columns because neither replay path calls `_with_trade_returns`.

- [ ] **Step 3: Wire single-symbol replay**

Before returning `ReplayArtifacts` in `replay_trend_strategy`, construct:

```python
trades = _with_trade_returns(
    pd.DataFrame(trade_rows, columns=TRADE_COLUMNS),
    observation_bars_by_symbol={
        root_symbol: _observation_bars(bars, roll_bars)
    },
    default_symbol=root_symbol,
)
```

Pass `trades=trades` into `ReplayArtifacts`.

- [ ] **Step 4: Wire portfolio replay without revalidating data**

Retain each root's validated roll frame in `roll_frames_by_root` and derive `roll_bar_lookups` from that mapping. While validating primary minute bars, build `observation_bars_by_symbol[root_symbol] = _observation_bars(bars, roll_frames_by_root[root_symbol])`. Before returning `ReplayArtifacts`, call `_with_trade_returns` with that mapping and pass the enriched frame as `trades`.

- [ ] **Step 5: Run integration and report tests**

Run:

```bash
pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py
```

Expected: the complete backtest module passes.

### Task 4: Regression verification

**Files:**
- Verify only; no planned edits.

- [ ] **Step 1: Run the multi-timeframe trend strategy suite**

Run:

```bash
pytest -q \
  cta/strategy/tests/test_multi_timeframe_trend_strategy.py \
  cta/strategy/tests/test_multi_timeframe_trend_management.py \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py
```

Expected: all tests pass; the prior baseline was `144 passed` before adding the new tests.

- [ ] **Step 2: Inspect the surgical diff**

Because the implementation files are untracked, use `git diff --no-index` against temporary snapshots made before editing, or inspect the exact edited regions with `sed`. Confirm every changed line implements one of: output column order, return calculation, replay wiring, or tests. Confirm no config, candidate, execution, risk, equity, chart, or report-metric behavior changed.

- [ ] **Step 3: Report verification evidence**

Summarize the test command and pass count, the exact five column names/order, the direction-adjusted formula, and the `NaN` behavior at the data boundary. Do not claim a full historical AG rerun unless it was actually executed.
