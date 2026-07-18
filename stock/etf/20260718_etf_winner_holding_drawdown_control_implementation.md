# ETF Winner Holding and Drawdown Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved winner-holding state machine, partial exposure control, dynamic portfolio risk limits and auditable bull-trend capture metrics, then test them on the existing point-in-time A-share ETF history.

**Architecture:** Keep order execution and cost accounting in `portfolio.py`; put pure market-state, breadth, correlation and trim calculations in a new `risk.py`; put trend-episode accounting in a new `capture.py`. Integrate those units through the existing next-open loop in `backtest.py`, preserving the clean ATR5/ADX10 baseline behind disabled-by-default feature flags so staged experiments remain comparable.

**Tech Stack:** Python 3.13, pandas, NumPy, unittest/pytest, Ruff, Pillow, existing local Tushare CSV cache.

**Design:** `stock/etf/20260718_etf_winner_holding_drawdown_control_design.md`

**Workspace:** Modify only `stock/etf/**`. Reuse `stock/etf/data/20260717_2018_20260717_point_in_time_live/`; do not redownload data unless its quality checks fail.

---

### Task 1: Configuration, Position State and Partial Fills

**Files:**
- Modify: `stock/etf/config.py`
- Modify: `stock/etf/portfolio.py`
- Modify: `stock/etf/tests/test_portfolio.py`

- [ ] **Step 1: Add failing configuration and partial-fill tests**

Add tests that assert the combined configuration validates the approved limits and that a partial sell allocates entry commission proportionally without closing the remaining position:

```python
def test_winner_control_parameters_are_validated(self) -> None:
    config = StrategyConfig(
        winner_holding_enabled=True,
        market_state_enabled=True,
        dynamic_risk_enabled=True,
        atr_stop_multiple=3.0,
        exit_rank=20,
        exit_rank_confirmation_days=3,
        winner_promotion_atr_multiple=2.0,
        max_portfolio_stop_risk=0.03,
        max_cluster_stop_risk=0.015,
        caution_max_gross_weight=0.50,
        caution_entry_rank=3,
        caution_risk_fraction=0.50,
        risk_off_breadth_threshold=0.35,
        market_state_confirmation_days=2,
    )
    self.assertEqual(config.exit_rank, 20)
    with self.assertRaises(ValueError):
        StrategyConfig(caution_risk_fraction=0)
    with self.assertRaises(ValueError):
        StrategyConfig(max_cluster_stop_risk=0.04, max_portfolio_stop_risk=0.03)

def test_partial_sell_preserves_position_and_allocates_entry_cost(self) -> None:
    config = StrategyConfig(
        initial_capital=100_000,
        commission_rate=0.001,
        min_commission=0,
        slippage_rate=0,
    )
    portfolio = Portfolio(100_000, config)
    portfolio.buy("A.SH", "2026-01-02", 10.0, 1000, 0.5, "entry")

    trade = portfolio.sell_quantity(
        "A.SH", "2026-01-03", 11.0, 400, "portfolio_trim"
    )

    self.assertEqual(trade.quantity, 400)
    self.assertAlmostEqual(trade.realized_pnl, 391.6)
    self.assertEqual(portfolio.positions["A.SH"].quantity, 600)
    self.assertAlmostEqual(portfolio.positions["A.SH"].entry_commission, 6.0)
```

Add a restoration test asserting that additional shares retain state, highest close and stop while weighted average cost and entry commission update.

- [ ] **Step 2: Run the tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_portfolio.py -q
```

Expected: failures for unknown configuration fields, missing `PositionState`, missing `sell_quantity` and missing `increase`.

- [ ] **Step 3: Add the approved configuration fields and validation**

Extend `StrategyConfig` with disabled-by-default behavior flags and fixed research parameters:

```python
winner_holding_enabled: bool = False
market_state_enabled: bool = False
dynamic_risk_enabled: bool = False
winner_promotion_atr_multiple: float = 2.0
exit_rank_confirmation_days: int = 3
max_portfolio_stop_risk: float | None = None
max_cluster_stop_risk: float | None = None
caution_max_gross_weight: float = 0.50
caution_entry_rank: int = 3
caution_risk_fraction: float = 0.50
risk_off_breadth_threshold: float = 0.35
market_state_confirmation_days: int = 2
```

Validate positive multiples and day counts, fractional values in `(0, 1]`,
`caution_entry_rank <= entry_rank` when market states are enabled, and
`max_cluster_stop_risk <= max_portfolio_stop_risk` when both are enabled. Keep
all old defaults behaviorally unchanged.

- [ ] **Step 4: Extend the position model without breaking old constructors**

Append defaulted fields after the existing five positional fields:

```python
from enum import StrEnum


class PositionState(StrEnum):
    TRIAL = "trial"
    WINNER = "winner"


@dataclass
class Position:
    symbol: str
    quantity: int
    average_price: float
    stop_price: float
    entry_commission: float
    entry_price: float | None = None
    entry_atr5: float = 0.0
    state: PositionState = PositionState.TRIAL
    highest_close: float = 0.0
    weak_rank_days: int = 0
    restore_eligible: bool = False

    def __post_init__(self) -> None:
        if self.entry_price is None:
            self.entry_price = self.average_price
        if self.highest_close <= 0:
            self.highest_close = self.average_price
```

Set `entry_price`, `entry_atr5` and `highest_close` in `Portfolio.buy`.

- [ ] **Step 5: Implement partial sell and restoration accounting**

Implement `Portfolio.sell_quantity` and make existing `sell` delegate to it. Allocate entry commission in proportion to quantity before mutating the position:

```python
allocated_entry_commission = (
    position.entry_commission * quantity / position.quantity
)
realized_pnl = (
    (fill_price - position.average_price) * quantity
    - allocated_entry_commission
    - commission
)
```

Implement `Portfolio.increase` for an already-held symbol. New shares use the current open and slippage; update weighted average price and entry commission, but preserve `entry_price`, `entry_atr5`, `state`, `highest_close`, `weak_rank_days` and `stop_price`. Emit ordinary `buy` trades with the supplied restoration reason so existing ledgers and charts remain compatible.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
python3 -m pytest stock/etf/tests/test_portfolio.py -q
python3 -m ruff check stock/etf/config.py stock/etf/portfolio.py stock/etf/tests/test_portfolio.py
```

Expected: all portfolio tests pass and Ruff reports no errors.

Commit only these files:

```bash
git add stock/etf/config.py stock/etf/portfolio.py stock/etf/tests/test_portfolio.py
git commit -m "feat: support ETF position states and partial fills"
```

### Task 2: Winner Promotion, Monotonic Stop and Slow Exit

**Files:**
- Modify: `stock/etf/portfolio.py`
- Modify: `stock/etf/backtest.py`
- Modify: `stock/etf/tests/test_portfolio.py`
- Modify: `stock/etf/tests/test_backtest.py`

- [ ] **Step 1: Add failing winner-state unit tests**

Add tests for irreversible promotion and a stop that never loosens:

```python
def test_winner_promotion_and_trailing_stop_are_monotonic(self) -> None:
    config = StrategyConfig(
        initial_capital=100_000,
        commission_rate=0,
        min_commission=0,
        slippage_rate=0,
        atr_stop_multiple=3,
        winner_holding_enabled=True,
    )
    portfolio = Portfolio(100_000, config)
    portfolio.buy("A.SH", "2026-01-02", 10.0, 1000, 1.0, "entry")

    transition = portfolio.update_after_close("A.SH", 12.1, 11.5, 11.0, 1.0)
    first_stop = portfolio.positions["A.SH"].stop_price
    portfolio.update_after_close("A.SH", 11.8, 11.6, 11.1, 2.0)

    self.assertEqual(transition, "winner_promoted")
    self.assertEqual(portfolio.positions["A.SH"].state, PositionState.WINNER)
    self.assertEqual(portfolio.positions["A.SH"].stop_price, first_stop)
```

Add backtest policy tests that call the exit helper directly with synthetic rows:

```python
def test_rank_weakness_requires_three_days_and_ema10_confirmation(self) -> None:
    position = Position("A.SH", 1000, 10.0, 7.0, 0.0, weak_rank_days=2)
    ranking = pd.DataFrame({"holding_rank": [21]}, index=["A.SH"])
    row = pd.Series({"close": 10.5, "ema10": 10.0, "ema20": 9.5})
    reasons = _winner_reasons_for_exit(position, ranking, row, self.winner_config)
    self.assertEqual(reasons, [])

    row["close"] = 9.9
    reasons = _winner_reasons_for_exit(position, ranking, row, self.winner_config)
    self.assertEqual(reasons, ["confirmed_rank_and_ema10_weakness"])
```

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_portfolio.py stock/etf/tests/test_backtest.py -q
```

Expected: failures for missing `update_after_close` and winner exit policy.

- [ ] **Step 3: Implement close-time position updates**

Add `Portfolio.update_after_close(symbol, close, ema10, ema20, atr5)`. Always update `highest_close`. Promote only when:

```python
promotion_price = position.entry_price + (
    self.config.winner_promotion_atr_multiple * position.entry_atr5
)
promote = (
    position.state == PositionState.TRIAL
    and position.highest_close >= promotion_price
    and close > ema10 > ema20
)
```

For a winner, calculate `highest_close - atr_stop_multiple * atr5` and assign the maximum of old and new stop. Return `winner_promoted` only on the transition and `None` otherwise.

- [ ] **Step 4: Add a feature-gated slow exit policy**

Preserve `_reasons_for_exit` exactly for baseline configurations. For `winner_holding_enabled=True`, remove benchmark EMA10 and standalone rank exits. Maintain `position.weak_rank_days` at each close and return reasons in this order:

```python
if close < ema20:
    reasons.append("etf_below_ema20")
if position.weak_rank_days >= config.exit_rank_confirmation_days and close < ema10:
    reasons.append("confirmed_rank_and_ema10_weakness")
```

Missing ranking increments no rank streak; it records `rs_unavailable` in audit but does not silently fabricate a rank. A stop or later `RISK_OFF` remains higher priority.

- [ ] **Step 5: Add a 512400-like integration fixture**

Construct one ETF that becomes Top5, rises enough to promote, briefly ranks 11-20 while staying above EMA10, falls one day below EMA10 but remains above EMA20, and then resumes a large rise. Assert one buy remains open through the temporary weakness and no `rs_out_top10` sell is emitted in winner mode. Run the same fixture with the baseline flag off and assert the old exit still occurs.

- [ ] **Step 6: Run focused and full tests, then commit**

Run:

```bash
python3 -m pytest stock/etf/tests/test_portfolio.py stock/etf/tests/test_backtest.py -q
python3 -m pytest stock/etf/tests -q
```

Expected: all ETF tests pass.

Commit:

```bash
git add stock/etf/portfolio.py stock/etf/backtest.py stock/etf/tests/test_portfolio.py stock/etf/tests/test_backtest.py
git commit -m "feat: hold ETF winners with slower exits"
```

### Task 3: Breadth and Confirmed Market States

**Files:**
- Create: `stock/etf/risk.py`
- Create: `stock/etf/tests/test_risk.py`
- Modify: `stock/etf/backtest.py`

- [ ] **Step 1: Add failing pure risk tests**

Create tests for breadth membership, missing breadth fallback and two-day state confirmation:

```python
def test_breadth_uses_valid_point_in_time_members(self) -> None:
    daily = pd.DataFrame(
        {
            "close": [12.0, 8.0, 9.0],
            "ema20": [10.0, 10.0, np.nan],
            "is_trading": [True, True, True],
        },
        index=["A.SH", "B.SH", "C.SH"],
    )
    self.assertEqual(calculate_breadth(daily), 0.5)

def test_market_state_requires_two_matching_candidate_days(self) -> None:
    tracker = MarketStateTracker(MarketState.CAUTION, confirmation_days=2)
    self.assertEqual(tracker.advance(MarketState.RISK_ON), MarketState.CAUTION)
    self.assertEqual(tracker.advance(MarketState.RISK_ON), MarketState.RISK_ON)
    self.assertEqual(tracker.advance(MarketState.RISK_OFF), MarketState.RISK_ON)
```

Also test exact boundaries: breadth `0.35` is not below 35%; `close == EMA20` is not below EMA20; a different candidate resets the streak.

- [ ] **Step 2: Run risk tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_risk.py -q
```

Expected: import failure because `stock.etf.risk` does not exist.

- [ ] **Step 3: Implement the pure market-state module**

Create:

```python
class MarketState(StrEnum):
    RISK_ON = "risk_on"
    CAUTION = "caution"
    RISK_OFF = "risk_off"


@dataclass
class MarketStateTracker:
    state: MarketState
    confirmation_days: int
    candidate: MarketState | None = None
    candidate_days: int = 0

    def advance(self, candidate: MarketState) -> MarketState:
        if candidate == self.state:
            self.candidate = None
            self.candidate_days = 0
            return self.state
        if candidate != self.candidate:
            self.candidate = candidate
            self.candidate_days = 1
            return self.state
        self.candidate_days += 1
        if self.candidate_days >= self.confirmation_days:
            self.state = candidate
            self.candidate = None
            self.candidate_days = 0
        return self.state
```

Implement `calculate_breadth(daily)` and `classify_market_candidate(...)`. Candidate priority is existing `is_risk_on`, then CSI300 below EMA20 with breadth below threshold, otherwise caution. If breadth is unavailable, CSI300 below EMA20 alone creates the Risk Off candidate.

- [ ] **Step 4: Integrate market-state audit without changing baseline behavior**

When `market_state_enabled=False`, continue writing existing `risk_on/risk_off` signal records and gating entries exactly as today. When enabled, maintain one tracker and write daily records containing:

```python
{
    "signal_date": date,
    "symbol": cfg.benchmark_symbol,
    "action": f"market_{market_state.value}",
    "reason": "confirmed_market_state",
    "breadth": breadth,
    "candidate_state": candidate_state.value,
    "state_confirmation_days": tracker.candidate_days,
}
```

Allow entries in Risk On up to normal `entry_rank`; allow only Top3 and half risk in Caution; clear entries in Risk Off.

- [ ] **Step 5: Run focused and full tests, then commit**

Run:

```bash
python3 -m pytest stock/etf/tests/test_risk.py stock/etf/tests/test_backtest.py -q
python3 -m pytest stock/etf/tests -q
```

Commit:

```bash
git add stock/etf/risk.py stock/etf/tests/test_risk.py stock/etf/backtest.py
git commit -m "feat: add confirmed ETF market states"
```

### Task 4: Dynamic Correlation, Stop-Risk and Gross-Exposure Trims

**Files:**
- Modify: `stock/etf/risk.py`
- Modify: `stock/etf/tests/test_risk.py`
- Modify: `stock/etf/backtest.py`
- Modify: `stock/etf/tests/test_backtest.py`

- [ ] **Step 1: Add failing correlation-cluster and trim-planner tests**

Define a compact risk input and assert correlated positions are trimmed before constraints are violated:

```python
positions = [
    RiskPosition("A.SH", 2000, 10.0, 9.0, "trial", 25, -0.01, "growth"),
    RiskPosition("B.SH", 2000, 10.0, 9.0, "winner", 2, 0.02, "growth"),
]
plan = plan_trim_quantities(
    positions,
    equity=100_000,
    lot_size=100,
    gross_cap=0.50,
    industry_cap=0.35,
    correlation_clusters=[{"A.SH", "B.SH"}],
    correlation_cap=0.30,
    portfolio_stop_risk_cap=0.03,
    cluster_stop_risk_cap=0.015,
)
self.assertEqual(plan["A.SH"], 1000)
self.assertNotIn("B.SH", plan)
```

Add tests for connected components, exactly 0.90 correlation, 39 observations not creating an edge, total stop-risk capacity, trial-before-winner order, worse-rank tie breaking and board-lot rounding.

- [ ] **Step 2: Run risk tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_risk.py -q
```

Expected: failures for missing `RiskPosition`, `correlation_clusters` and `plan_trim_quantities`.

- [ ] **Step 3: Move correlation graph calculation into `risk.py`**

Implement `correlation_clusters(symbols, returns, signal_date, config)` using only returns at or before `signal_date`, trailing 60 observations and at least 40 overlapping rows. Return deterministic connected components sorted by their smallest symbol. Replace `_correlated_cluster_symbols` in `backtest.py` with this shared function for both entry and daily checks.

- [ ] **Step 4: Implement deterministic trim planning**

Add:

```python
@dataclass(frozen=True)
class RiskPosition:
    symbol: str
    quantity: int
    price: float
    stop_price: float
    state: str
    holding_rank: int
    ema5_slope: float
    industry: str

    @property
    def market_value(self) -> float:
        return self.quantity * self.price

    @property
    def stop_risk(self) -> float:
        return self.quantity * max(self.price - self.stop_price, 0.0)
```

`plan_trim_quantities` repeatedly finds violated gross, industry, correlation,
portfolio stop-risk and cluster stop-risk capacities. It selects the first
eligible position by `(trial before winner, worse rank, weaker EMA5 slope,
symbol)` and removes the smallest board-lot quantity that resolves at least one
current excess, then recomputes all capacities. It stops when all constraints
pass or no full lot remains. Return sell quantities, never target holdings.

- [ ] **Step 5: Integrate next-open trim orders and restoration eligibility**

At signal close, build `RiskPosition` rows from current holdings and schedule `PendingAdjustment(symbol, quantity, action, reason, signal_date)` for the next open. Merge simultaneous reasons with `|`; one symbol receives only one sell quantity capped at its holding.

After a trim fill, set `restore_eligible=True`. In confirmed Risk On, restore eligible positions before new entries up to the current risk-sized target, while rechecking cash, industry, benchmark, correlation, total stop-risk and cluster stop-risk capacity after every fill. In Caution, never restore above the 50% gross target. In Risk Off, disable restoration and use full exits.

- [ ] **Step 6: Add integration assertions for the diagnosed duplicate exposure**

Use two 0.998-correlated synthetic ETFs. Assert daily combined cluster weight never exceeds `0.300001`, cluster stop risk never exceeds `0.015001`, and the weaker trial is trimmed before the winner. Assert no trim and restore occur on the same execution date for one symbol.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
python3 -m pytest stock/etf/tests/test_risk.py stock/etf/tests/test_backtest.py -q
python3 -m pytest stock/etf/tests -q
```

Commit:

```bash
git add stock/etf/risk.py stock/etf/tests/test_risk.py stock/etf/backtest.py stock/etf/tests/test_backtest.py
git commit -m "feat: enforce dynamic ETF portfolio risk limits"
```

### Task 5: Backtest Execution Priority and Audit Tables

**Files:**
- Modify: `stock/etf/backtest.py`
- Modify: `stock/etf/tests/test_backtest.py`
- Modify: `stock/etf/run_etf_rotation.py`
- Modify: `stock/etf/tests/test_cli.py`

- [ ] **Step 1: Add failing priority and schema tests**

Add a synthetic date where an opening gap stop, a pending Risk Off exit and a pending restore exist for the same symbol. Assert exactly one sell, reason `atr_stop_gap`, no restore and no second sell. Add schema assertions for:

```python
self.assertEqual(
    set(result.position_states.columns),
    {
        "datetime", "symbol", "state", "highest_close", "stop_price",
        "weak_rank_days", "transition", "reason",
    },
)
self.assertIn("market_state", result.portfolio_risk.columns)
self.assertIn("total_stop_risk_weight", result.portfolio_risk.columns)
```

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_backtest.py stock/etf/tests/test_cli.py -q
```

Expected: failures because result audit tables and output files do not exist.

- [ ] **Step 3: Extend result and output schemas**

Extend `BacktestResult` in this stable order:

```python
@dataclass
class BacktestResult:
    candidates: pd.DataFrame
    signals: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity_curve: pd.DataFrame
    position_states: pd.DataFrame
    portfolio_risk: pd.DataFrame
    trend_capture: pd.DataFrame
    summary: dict[str, Any]
```

Write `position_state_log.csv`, `portfolio_risk_log.csv` and, after Task 6,
`trend_episode_capture.csv`. Baseline runs write header-only or daily-compatible
audit tables rather than omitting files, keeping CLI output deterministic.

- [ ] **Step 4: Reorder the daily execution loop explicitly**

Refactor the loop into focused private helpers that execute in this exact order:

```python
_execute_gap_stops(...)
_execute_pending_full_exits(...)
_execute_pending_trims(...)
_execute_pending_restores(...)
_execute_pending_entries(...)
_execute_intraday_stops(...)
_mark_and_plan_next_session(...)
```

Each helper receives and returns explicit pending dictionaries. Do not create a
general event engine. A symbol removed by an earlier helper is ignored by all
later helpers on that date.

Split stop checks into `Portfolio.check_gap_stop` and
`Portfolio.check_intraday_stop`; retain `check_stop` as a backward-compatible
wrapper for existing callers. This prevents an intraday low from being executed
before a pending next-open full exit.

- [ ] **Step 5: Expose CLI flags and rule version**

Add explicit CLI arguments for the approved fields, including feature flags,
stop multiple, exit rank, risk caps and market-state thresholds. Update
`STRATEGY_RULE_VERSION` to `2026-07-18.winner-holding-v1`. Extend the CLI test to
assert all values are serialized in `summary.json` and that nine deterministic
output files exist after Task 6.

- [ ] **Step 6: Run tests, Ruff and commit**

Run:

```bash
python3 -m pytest stock/etf/tests/test_backtest.py stock/etf/tests/test_cli.py -q
python3 -m ruff check stock/etf/backtest.py stock/etf/run_etf_rotation.py stock/etf/tests/test_backtest.py stock/etf/tests/test_cli.py
```

Commit:

```bash
git add stock/etf/backtest.py stock/etf/run_etf_rotation.py stock/etf/tests/test_backtest.py stock/etf/tests/test_cli.py
git commit -m "feat: audit ETF state and risk executions"
```

### Task 6: Bull-Trend Episode Capture

**Files:**
- Create: `stock/etf/capture.py`
- Create: `stock/etf/tests/test_capture.py`
- Modify: `stock/etf/backtest.py`
- Modify: `stock/etf/experiments.py`
- Modify: `stock/etf/tests/test_experiments.py`

- [ ] **Step 1: Add failing episode and ledger-reconciliation tests**

Build one ETF with a Top5/trend start, a 25% executable rise and an EMA20 end.
Include a full buy, partial trim, restoration and final sell. Assert exact
boundaries and capture ratio:

```python
episodes = calculate_trend_capture(
    candidates=candidates,
    trades=trades,
    positions=positions,
    equity_curve=equity,
    config=config,
)
row = episodes.iloc[0]
self.assertEqual(row["start_execution_date"], pd.Timestamp("2026-01-05"))
self.assertEqual(row["end_execution_date"], pd.Timestamp("2026-02-10"))
self.assertGreater(row["episode_return"], 0.20)
self.assertAlmostEqual(
    row["capture_ratio"],
    row["actual_episode_pnl"] / row["hypothetical_episode_profit"],
)
```

Add cases for a skipped ETF receiving zero PnL, an unfinished episode marked to
the report end, a sub-20% episode excluded from the headline median and a ratio
above 1.0 remaining unclipped.

- [ ] **Step 2: Run capture tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_capture.py -q
```

Expected: import failure because `stock.etf.capture` does not exist.

- [ ] **Step 3: Implement executable episode boundaries**

Group candidates by symbol and sort by date. Start an episode on the first row
with `entry_rank <= config.entry_rank` and `trend_confirmed=True`. End it on the
first later row with `close < ema20`. Resolve both boundaries to the next row's
open; use report-end close only for unfinished episodes and flag
`is_complete=False`.

- [ ] **Step 4: Implement transaction-ledger PnL and hypothetical profit**

Use boundary market values and signed fill cash flows:

```python
actual_pnl = (
    end_market_value
    - start_market_value
    + sell_fill_cash_after_commission
    - buy_fill_cash_with_commission
)
hypothetical_quantity = calculate_order_quantity(
    start_equity,
    start_equity,
    start_open * (1 + config.slippage_rate),
    start_atr5,
    config,
)
hypothetical_entry_fill = start_open * (1 + config.slippage_rate)
hypothetical_exit_fill = end_open * (1 - config.slippage_rate)
hypothetical_profit = (
    hypothetical_quantity * (hypothetical_exit_fill - hypothetical_entry_fill)
    - entry_commission
    - exit_commission
)
capture_ratio = actual_pnl / hypothetical_profit
```

Include actual commissions and fill-price slippage. Do not apply portfolio
capacity reductions to the hypothetical quantity. Qualify only complete episodes
with `episode_return > 0.20` and positive hypothetical profit.

- [ ] **Step 5: Integrate output and experiment summary metrics**

Populate `BacktestResult.trend_capture`, write
`trend_episode_capture.csv`, and add to `summarize_run`:

```python
qualified = capture.loc[capture["qualifies"]]
metrics["bull_episode_count"] = int(len(qualified))
metrics["median_bull_capture_ratio"] = (
    float(qualified["capture_ratio"].median())
    if not qualified.empty
    else float("nan")
)
```

Update experiment test fixtures to write the capture CSV.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
python3 -m pytest stock/etf/tests/test_capture.py stock/etf/tests/test_experiments.py stock/etf/tests/test_backtest.py -q
python3 -m pytest stock/etf/tests -q
```

Commit:

```bash
git add stock/etf/capture.py stock/etf/tests/test_capture.py stock/etf/backtest.py stock/etf/experiments.py stock/etf/tests/test_experiments.py
git commit -m "feat: measure ETF bull trend capture"
```

### Task 7: Staged Experiment Runner and Acceptance Gates

**Files:**
- Create: `stock/etf/run_winner_holding_experiments.py`
- Create: `stock/etf/tests/test_winner_experiments.py`
- Modify: `stock/etf/experiments.py`
- Modify: `stock/etf/tests/test_experiments.py`
- Modify: `stock/etf/etf_rotation_strategy.md`

- [ ] **Step 1: Add failing staged-config and gate tests**

Assert the runner creates these immutable configurations:

```python
expected = {
    "clean_baseline",
    "risk_controls_only",
    "winner_holding_only",
    "market_states_only",
    "combined",
    "combined_cost2x",
}
self.assertEqual(set(build_experiment_configs()), expected)
```

Assert `combined` uses ATR5/ADX10, `3 * ATR5`, exit rank20, three-day weak-rank
confirmation, 35% industry, 0.90/30% correlation, 3%/1.5% stop-risk caps and all
feature flags. Assert cost stress doubles only commission and slippage.

Add selection tests where one gate at a time fails. The audit must contain:

```python
{
    "max_drawdown_at_least_minus_20": True,
    "annual_one_way_turnover_at_most_8": True,
    "annual_return_at_least_4_percent": True,
    "sharpe_at_least_point_50": True,
    "median_bull_capture_at_least_50_percent": True,
    "double_cost_annual_return_positive": True,
    "all_numeric_gates_pass": True,
}
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_winner_experiments.py stock/etf/tests/test_experiments.py -q
```

Expected: failures for missing staged runner and old gate names.

- [ ] **Step 3: Implement explicit staged configurations**

`build_experiment_configs()` returns ordered immutable `StrategyConfig` values.
The baseline remains the exact ATR5/ADX10 clean configuration. Each intermediate
variant enables only its named layer. `combined_cost2x` copies `combined` with
`commission_rate=0.0006` and `slippage_rate=0.001`.

- [ ] **Step 4: Implement local-cache orchestration**

The CLI accepts benchmark, ETF and metadata CSV paths, start/end, output root and
`--skip-existing`. Read normalized inputs once, call `run_backtest` for every
config, write each run directory, then call `write_experiment_report` with
`clean_baseline`, `combined` and `combined_cost2x` roles. Do not download or
select a winner inside this command.

- [ ] **Step 5: Replace selection gates with the approved six gates**

Read the double-cost annual return from its named row. Preserve concentration,
annual, rolling and deflated-Sharpe diagnostics, but make
`all_numeric_gates_pass` depend only on the six approved gates. Report missing
or zero qualifying bull episodes as a failed capture gate, not as success.

- [ ] **Step 6: Update canonical documentation and run tests**

Document the new research variant, audit outputs, feature flags and acceptance
gates in `etf_rotation_strategy.md`; keep the current clean baseline labeled as
research-only until a combined run passes every gate.

Run:

```bash
python3 -m pytest stock/etf/tests/test_winner_experiments.py stock/etf/tests/test_experiments.py -q
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m compileall -q stock/etf
```

Commit:

```bash
git add stock/etf/run_winner_holding_experiments.py stock/etf/tests/test_winner_experiments.py stock/etf/experiments.py stock/etf/tests/test_experiments.py stock/etf/etf_rotation_strategy.md
git commit -m "feat: run staged ETF winner experiments"
```

### Task 8: Real Historical Runs, Review and Trading Charts

**Files:**
- Create: `stock/etf/output/20260718_winner_holding_experiments/**`
- Create: `stock/etf/output/20260718_winner_holding_final/charts/**`
- Modify only if defects are found: `stock/etf/**`

- [ ] **Step 1: Verify code before the expensive run**

Run:

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
```

Expected: zero test failures, zero lint errors, no formatting differences and
successful compilation.

- [ ] **Step 2: Validate the existing real cache**

Check requested and actual date ranges, duplicate symbol-date rows, OHLC
constraints, A-share-only metadata, lifecycle boundaries and the availability
of both `512400.SH` and CSI300. Abort the real run on a quality failure; do not
silently substitute synthetic data.

- [ ] **Step 3: Run the six staged variants**

Run:

```bash
python3 -m stock.etf.run_winner_holding_experiments \
  --start 2018-01-01 \
  --end 2026-07-17 \
  --benchmark-csv stock/etf/data/20260717_2018_20260717_point_in_time_live/benchmark.csv \
  --etf-csv stock/etf/data/20260717_2018_20260717_point_in_time_live/etfs_lifecycle_clean.csv \
  --metadata-csv stock/etf/data/20260717_2018_20260717_point_in_time_live/metadata.csv \
  --output-root stock/etf/output/20260718_winner_holding_experiments
```

Expected: six run directories plus one comparison report. Record wall time and
do not overwrite the 20260717 baseline outputs.

- [ ] **Step 4: Reconcile and review the result**

Programmatically assert:

- Every equity change reconciles to cash and marked positions.
- Partial trade quantities reconcile to the position ledger.
- Stops never decrease for an open position.
- No duplicate benchmark holdings occur.
- Industry, cluster, gross and stop-risk weights remain within tolerance after
  scheduled next-open adjustments.
- No same-symbol duplicate execution violates reason priority.
- Trend capture PnL reconciles to episode transaction ledgers.
- `selection_audit.json` agrees with raw metrics.

If a defect appears, reproduce it with a failing unit test before changing code,
then rerun all staged variants under a new output identifier.

- [ ] **Step 5: Produce focused diagnostics and final trade charts**

Render a 2025 focused chart for `512400.SH`, focused charts for the largest
combined-run drawdown contributors, and one weekly/daily/volume chart per traded
ETF in the reported configuration. If `combined` passes all gates, report it;
otherwise retain the clean baseline as research-only and clearly list every
failed gate.

Use:

```bash
python3 -m stock.etf.render_trade_charts \
  --daily-csv stock/etf/data/20260717_2018_20260717_point_in_time_live/etfs_lifecycle_clean.csv \
  --metadata-csv stock/etf/data/20260717_2018_20260717_point_in_time_live/metadata.csv \
  --trades-csv stock/etf/output/20260718_winner_holding_experiments/combined/trades.csv \
  --positions-csv stock/etf/output/20260718_winner_holding_experiments/combined/positions.csv \
  --output-dir stock/etf/output/20260718_winner_holding_final/charts \
  --report-start 2018-01-01 \
  --report-end 2026-07-17 \
  --overwrite
```

- [ ] **Step 6: Final verification and delivery**

Run the full tests, Ruff, format check, compile check, PNG decoding, chart-index
count check and a fresh calculation of all six gates. Report actual metrics,
failed gates, output paths and residual risks. Do not claim the strategy is
live-ready unless every approved gate passes.
