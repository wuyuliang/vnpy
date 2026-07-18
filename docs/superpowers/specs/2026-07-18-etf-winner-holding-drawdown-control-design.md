# ETF Winner Holding and Drawdown Control Design

## 1. Background

This design extends the long-horizon `ATR5/ADX10` clean baseline without changing
the approved ETF universe or RS formula. The baseline from 2018-01-02 through
2026-07-17 has:

- Annual return: 4.34%.
- Total return: 41.77%.
- Maximum drawdown: -25.88%.
- Sharpe: 0.385.
- Annual one-way turnover: 11.64 times.

Two diagnosed failures motivate the change:

1. `512400.SH` rose 102.04% during 2025, while four short round trips produced a
   net loss of CNY 77.00. A hard Top10 exit, a fixed `2 * ATR5` stop and the
   binary CSI300 filter repeatedly removed a still-strong ETF.
2. The maximum drawdown ran from the 2024-10-08 high-water mark to the
   2025-07-07 trough. `159949.SZ` and `159915.SZ` contributed 32.20% of the
   peak-to-trough loss while their trailing 60-day return correlation was
   0.998. The baseline correlation controls were implemented but disabled.

The current design therefore separates fast ETF discovery, slow winner holding
and portfolio-level risk reduction. It must not solve portfolio risk by forcing
every strong ETF through the same fast sell rule.

## 2. Goals and Acceptance Gates

The final combined variant must satisfy all of the following on the same full
historical period as the clean baseline:

- Maximum drawdown no worse than -20%.
- Annual one-way turnover no greater than 8 times.
- Annual return at least 4%.
- Sharpe at least 0.50.
- Median bull-trend return capture at least 50%.
- Positive annualized return after doubling both commission and slippage rates.

These are research acceptance gates, not guarantees of live maximum drawdown.
Daily data cannot eliminate overnight gap risk.

## 3. Non-Goals

- Do not change the approved A-share index ETF universe.
- Do not change the Return3/5/10/20 RS formula.
- Do not add leverage, short selling, intraday alpha or hourly bars.
- Do not perform an unrestricted parameter search.
- Do not call the resulting history an untouched out-of-sample test. The design
  was informed by events in 2024 and 2025.

## 4. Three-Layer Architecture

### 4.1 Selection Layer

The selection layer only discovers strong ETFs. It retains the clean
`ATR5/ADX10` baseline behavior:

- Use the approved point-in-time A-share index ETF universe.
- Require 20-day median turnover above CNY 200 million and at least six months
  since listing.
- Rank the approved Return3/5/10/20, EMA5 slope and NormalizedATR5 score.
- Require entry rank in Top5 and the existing ETF trend confirmation.
- Generate the order after signal-day close and execute at the next tradable
  open.

Short-horizon entry rank must not directly control winner liquidation.

### 4.2 Position Layer

Each open position has one of two persistent states:

- `TRIAL`: a newly opened position that has not yet demonstrated a profitable
  trend.
- `WINNER`: a position whose highest close is at least
  `entry_fill + 2 * entry_ATR5` while `close > EMA10 > EMA20` remains true.

The state is stored explicitly. A `WINNER` never returns to `TRIAL`; it remains
a winner until fully closed. Partial trims and restorations preserve its state.

### 4.3 Portfolio Layer

The portfolio layer controls aggregate exposure, correlation, industry weight
and market regime. It may reduce or restore position quantities without
changing the position's `TRIAL` or `WINNER` identity. A market warning must not
be recorded as an ETF trend failure.

## 5. Position Sizing and Stops

### 5.1 Initial Risk

The initial stop distance is widened from `2 * ATR5` to `3 * ATR5`:

```text
initial_stop = entry_fill - 3 * entry_ATR5
risk_budget = prior_equity * 1%
risk_quantity = floor_to_lot(risk_budget / (3 * entry_ATR5))
```

The existing 20% per-position weight cap and all portfolio capacities still
apply. Widening the stop reduces quantity; it does not increase planned CNY risk.

The stop remains based on ATR5. ATR10 must not be substituted for either sizing
or stop calculation.

### 5.2 Trial Exit

A `TRIAL` position exits in full when any of these conditions occurs:

- Its opening price gaps through the current stop or its intraday low touches
  the stop, using the existing stop execution semantics.
- Signal-day close is below EMA20.
- `holding_rank > 20` for three consecutive signal days and signal-day close is
  below EMA10.
- Portfolio state becomes `RISK_OFF`.

Rank deterioration alone never exits a position.

### 5.3 Winner Trailing Stop and Exit

After promotion to `WINNER`, update a monotonic trailing stop after each close:

```text
candidate_stop = highest_close_since_entry - 3 * current_ATR5
trailing_stop = max(previous_stop, candidate_stop)
```

The stop can tighten but never loosen. A `WINNER` exits in full under the same
conditions as a `TRIAL`: stop execution, one close below EMA20, three confirmed
days of `holding_rank > 20` combined with close below EMA10, or portfolio
`RISK_OFF`.

CSI300 merely falling below EMA10 is not an ETF full-exit condition.

## 6. Portfolio Risk Controls

### 6.1 Duplicate, Industry and Correlation Capacity

- Hold at most one ETF for each normalized `benchmark_key`.
- Cap every classified industry at 35% of prior equity.
- Calculate pairwise return correlation from at most the trailing 60 signal-day
  observations, requiring at least 40 overlapping observations.
- Treat ETFs with correlation at or above 0.90 as one connected cluster.
- Cap each correlation cluster at 30% of prior equity.
- Cap planned stop risk for the whole portfolio at 3% of prior equity.
- Cap planned stop risk for one correlation cluster at 1.5% of prior equity.

Unlike the current entry-only correlation capacity, weight, cluster membership
and stop-risk limits are checked after every close. Any excess creates a trim
order for the next tradable open.

When correlation history is insufficient, apply benchmark and industry caps but
do not invent a correlation edge. Record `insufficient_correlation_history` in
the risk audit.

### 6.2 Market Breadth

For each signal date, ETF breadth is:

```text
breadth = count(close > EMA20) / count(valid breadth members)
```

The denominator contains point-in-time, currently trading A-share index ETFs
with sufficient indicator history. Entry liquidity and Top5 rank are not
required for breadth membership, so the measure represents the market rather
than only the most liquid candidates.

If breadth cannot be calculated, record `breadth_unavailable`. Treat a day with
CSI300 below EMA20 as the `RISK_OFF` candidate and otherwise use the normal
CSI300 `RISK_ON` test or `CAUTION` fallback.

### 6.3 Market States

The portfolio has one persistent market state. First classify each signal day
as a candidate state:

- Candidate `RISK_ON`: the existing CSI300 Risk On filter is true. Maximum
  target gross exposure is 100%.
- Candidate `RISK_OFF`: CSI300 closes below EMA20 and breadth is below 35%.
  Target gross exposure is 0%.
- Candidate `CAUTION`: every condition between `RISK_ON` and `RISK_OFF`.
  Maximum target gross exposure is 50%.

The persistent state changes only after the same candidate state appears on two
consecutive signal days. This confirmation applies in every direction,
including entry into and recovery from `CAUTION` or `RISK_OFF`.

In `CAUTION`:

- Existing positions are trimmed to the 50% gross-exposure target.
- `WINNER` positions are retained before `TRIAL` positions.
- New entries require entry rank in Top3 and use half the normal risk budget.
- Restored quantities must obey all current portfolio capacities.

In `RISK_OFF`, all positions are scheduled for full exit at the next tradable
open. New entries and restorations are disabled.

### 6.4 Trim and Restore Order

When exposure must be reduced, process positions in this order:

1. `TRIAL` before `WINNER`.
2. Worse holding rank before better holding rank.
3. Weaker EMA5 slope before stronger EMA5 slope.
4. Symbol ascending as the deterministic tie-breaker.

When exposure capacity returns, restore eligible partial positions before
opening new symbols. Restore better holding rank first, then stronger EMA5 slope,
then symbol ascending. A restore uses the current open, current stop distance and
remaining portfolio capacities; it never blindly restores the original quantity.
Restored shares inherit the existing position state, highest close and active
stop. Their fill only updates quantity and weighted average cost.

## 7. Execution Sequence and Conflict Priority

All decisions use information available by the signal-day close. The next
tradable session follows this order:

1. Check opening gaps against active ATR stops.
2. Execute pending full exits.
3. Execute pending portfolio trims.
4. Execute eligible restorations.
5. Execute eligible new entries.
6. Check intraday lows against active stops for positions still open.
7. Mark positions at close, update indicators, states and next-session orders.

The logical reason priority is:

1. ATR stop.
2. Portfolio `RISK_OFF`.
3. ETF close below EMA20.
4. Confirmed rank and EMA10 weakness.
5. Portfolio correlation, industry, stop-risk or gross-exposure trim.
6. Position restoration.
7. New entry.

A symbol cannot receive a restore and a new-entry order on the same signal day.
All quantities are rounded down to board lots. A residual quantity smaller than
one lot is logged and skipped rather than over-traded.

## 8. Bull-Trend Capture Metric

A bull-trend episode starts when an ETF first has entry rank in Top5 and passes
ETF trend confirmation. It ends on the first signal-day close below EMA20. The
episode uses the next tradable open after each boundary so that both endpoints
are executable.

An episode qualifies for the headline capture metric when its executable ETF
return exceeds 20%:

```text
episode_return = end_open / start_open - 1
```

At the episode start, calculate a hypothetical standard position using the same
`3 * ATR5`, 1% risk budget and 20% weight cap, but before portfolio capacity
reductions. Its buy-and-hold profit is the denominator. The numerator is the
ETF-specific, transaction-ledger PnL from all actual buys, partial trims,
restorations, sells, commissions and slippage within the same episode, including
boundary marks.

```text
capture_ratio = actual_episode_pnl / hypothetical_episode_profit
```

An ETF that is skipped because of portfolio constraints receives zero actual
PnL for that episode. Ratios may exceed 100% when tactical execution improves on
buy-and-hold. Report the median, distribution, episode count and individual
audit rows rather than clipping ratios.

## 9. Experiment Design

Run these preregistered variants in order:

1. `clean_baseline`: unchanged ATR5/ADX10 baseline.
2. `risk_controls_only`: Section 6.1 controls with baseline position exits and
   baseline binary market filter.
3. `winner_holding_only`: Section 4.2 and Section 5 position behavior with
   baseline portfolio controls.
4. `market_states_only`: Section 6.2 through 6.4 with baseline position exits.
5. `combined`: all approved rules.

Do not choose a variant by total return alone. Each report includes the six
primary acceptance metrics, average and maximum exposure, median holding days,
exit reason counts, costs, trade count, annual returns, rolling 12-month returns
and bull-trend capture diagnostics.

The main parameters are fixed before the run:

- Stop and trailing distance: `3 * ATR5`.
- Winner promotion: `entry_fill + 2 * entry_ATR5`.
- Confirmed rank exit: rank worse than 20 for three days plus close below EMA10.
- Industry cap: 35%.
- Correlation threshold and cap: 0.90 and 30%.
- Portfolio and cluster stop-risk caps: 3% and 1.5%.
- Caution gross exposure: 50%.
- Caution entries: Top3 at half of the normal risk budget.
- Risk Off breadth threshold: 35%.
- Market-state confirmation: two consecutive candidate days.

After the fixed run, perform a neighborhood robustness report without selecting
a new winner from it. Vary one family at a time around the fixed values and flag
whether conclusions reverse. Also run current costs, doubled costs, yearly
subperiods and rolling windows.

## 10. Audit Outputs

Retain all existing output files and add:

- `position_state_log.csv`: trial and winner transitions, highest close, current
  stop and transition reason.
- `portfolio_risk_log.csv`: market state, breadth, gross target, actual gross,
  total stop risk, cluster and industry capacities, trims and restorations.
- `trend_episode_capture.csv`: every episode boundary, hypothetical profit,
  actual PnL, capture ratio and qualification status.
- `experiment_summary.csv`: baseline and staged variants with all acceptance
  gates.
- `selection_audit.json`: explicit pass or failure for every gate; no automatic
  claim that a failed variant is live-ready.

## 11. Error Handling and Invariants

- Missing or stale candidate data disables new entry and restoration for that
  symbol; it does not fabricate rank or ATR values.
- A held ETF with no tradable bar keeps its state and pending exit until the next
  tradable session.
- Partial trims and restores preserve a valid positive quantity and board-lot
  multiple.
- Cash, position, industry, cluster and risk capacities are recomputed after
  every execution; no later order may spend capacity reserved by an earlier
  fill.
- Stops never decrease after entry.
- No output may use data later than its signal date for ranking, breadth,
  correlation or state transitions.

## 12. Test Strategy

Unit tests must cover:

- `TRIAL` to `WINNER` promotion and irreversible state behavior.
- A monotonic `3 * ATR5` trailing stop.
- Rank deterioration without EMA10 weakness does not exit.
- Three confirmed weak-rank days plus EMA10 weakness exits.
- One close below EMA20 exits.
- Market-state two-day confirmation and boundary values.
- Caution half-risk entries, trims and restorations.
- Exact benchmark deduplication and dynamic 0.90/30% correlation capacity.
- Industry, total stop-risk and cluster stop-risk caps.
- Board-lot rounding, cash safety and conflict priority.
- Bull-trend episode boundaries and transaction-ledger capture reconciliation.

Integration fixtures must include:

- A `512400.SH`-like path where rank briefly falls outside Top10 while price
  remains above EMA10, followed by a large trend. The winner must remain held.
- Two nearly identical growth ETFs with correlation near 1.0. Combined exposure
  must remain within 30% and cluster stop risk within 1.5%.
- A broad-market caution period with an independent Top3 sector ETF. The ETF may
  enter at half risk and remain partially held.
- A gap through stop, a simultaneous Risk Off signal and a pending restore. The
  stop and full exit must win without duplicate trades.

The real-data run is accepted only when calculations reconcile to the trade and
position ledgers and every primary gate passes. If no combined variant passes,
retain the clean baseline as research-only and report the failed gates without
loosening them after seeing results.
