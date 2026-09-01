# Multi-Timeframe Trend Trailing, Scaling, and Chart Design

## Goal

Upgrade the existing multi-timeframe trend backtest with four causal behaviors:

1. Pullback-breakout positions retain their actual-fill 2R target and also use the same confirmed-swing trailing stop as Always-In positions.
2. Daily direction permission additionally requires the candidate trigger to break the prior five completed daily bars.
3. Every daily, hourly, and five-minute opportunity panel displays the applicable final target and keeps the signal at the visual center.
4. New entries are reduced after symbol loss streaks or portfolio realized-equity drawdowns, with independent factors that can combine to 25%.

The implementation remains inside the existing strategy config, event engine, chart renderer, reports, and tests. No new generic risk framework is introduced.

## Causal Time Model

- Daily EMA and five-day extrema use completed daily bars only.
- A candidate is decided after its completed five-minute signal bar.
- Orders activate no earlier than the next realizable minute event.
- Trailing stops advance only after a completed five-minute context bar exposes a confirmed swing.
- Position-scaling state changes only after a trade has closed and its net PnL, including fees, is known.
- At one timestamp, existing-position exits are processed before pending entries. A loss closed at that timestamp can therefore reduce a later entry at the same timestamp, but no future trade affects an earlier fill.

## Daily Five-Bar Breakout Permission

The daily EMA rule remains symmetric:

```text
long_ema_permission  = EMA5 > EMA10 > EMA20
short_ema_permission = EMA5 < EMA10 < EMA20
```

For each five-minute candidate, attach extrema known at the signal time:

```text
prior_5d_high = max(high of the previous 5 completed daily bars)
prior_5d_low  = min(low of the previous 5 completed daily bars)
```

The final candidate permission is:

```text
long_allowed  = long_ema_permission  and trigger >= prior_5d_high
short_allowed = short_ema_permission and trigger <= prior_5d_low
```

The trigger is the candidate stop-order trigger, not a future fill. A long stop fill cannot occur below its trigger and a short stop fill cannot occur above its trigger under the existing adverse gap model. Fewer than five completed daily bars produces `DAILY_FIVE_BAR_HISTORY_UNAVAILABLE`. An EMA-aligned candidate that does not break the corresponding extreme produces `DAILY_FIVE_BAR_BREAKOUT_NOT_MET`. Both remain persisted candidates rather than disappearing from the funnel.

Audit fields include `prior_5d_high`, `prior_5d_low`, and the selected breakout boundary.

## Breakout Exit Management

At entry:

- Always-In continues to have no fixed target.
- Pullback breakout freezes `final_target` from the actual fill price and structural stop at the configured 2R multiple.

At every completed five-minute management event, both setup types call the existing confirmed-swing trailing-stop rule. Stops can move only in the favorable direction. Pullback positions exit on the first realizable protective event among the trailed stop and frozen 2R target. If both are touched in one minute and finer sequencing is unavailable, the stop remains the adverse-first result.

Plans keep the trigger-derived planned target. Trades store `final_target`, allowing reports and charts to distinguish the planned level from the actual-fill target.

## Position-Scaling State

### Configuration

The following values live only in `MultiTimeframeTrendConfig`; no CLI options are added:

```text
symbol_loss_streak = 2
symbol_position_scale = 0.5
portfolio_drawdown_threshold = 0.01
portfolio_position_scale = 0.5
```

The streak is a positive integer. The drawdown threshold is positive. Scale factors are in `(0, 1]`. The run context and summary persist the effective values so a report exposes the configuration used even though the reproduction command does not override them.

### Symbol State

Each symbol has a deterministic state containing its consecutive net-loss values, active flag, and recovery deficit.

- In normal mode, `net_pnl < 0` appends a consecutive loss.
- `net_pnl >= 0` clears the normal-mode loss streak.
- When the configured streak count is reached, activate scaling after that trade closes and set the deficit to the absolute sum of those losses.
- While active, every later closed trade for that symbol updates `deficit = deficit - net_pnl`.
- A later loss increases the deficit. A partial recovery decreases it.
- Recover only when the deficit is strictly below zero, then reset the streak.

The triggering losses themselves use the position size known before they closed. Only subsequent entries are reduced.

### Portfolio State

The portfolio uses realized cash after fees, not intraday unrealized marks, to prevent repeated activation and recovery from open-price fluctuations.

- Start the high-water mark at initial equity.
- In normal mode, update the high-water mark whenever realized cash makes a new high.
- After every close, activate when `(high_water - cash) / high_water` is strictly greater than the configured threshold.
- Set the initial recovery deficit to `high_water - cash` and freeze that high-water mark while active.
- While active, every later portfolio trade updates `deficit = deficit - net_pnl`.
- Recover only when the deficit is strictly below zero. The resulting cash is above the frozen high-water mark, so it becomes the new high-water mark.

### Entry Quantity

At a realizable entry event:

```text
base_quantity = quantity from structural risk sizing at the actual fill
symbol_factor = symbol_position_scale if symbol state is active else 1.0
portfolio_factor = portfolio_position_scale if portfolio state is active else 1.0
quantity_scale = symbol_factor * portfolio_factor
scaled_quantity = floor(base_quantity * quantity_scale)
```

With both default reductions active, the scale is `0.25`. Existing per-symbol margin, portfolio margin, and maximum-position limits are then applied to the scaled quantity. If scaling leaves fewer than one lot, reject with `DYNAMIC_RISK_SCALE_BELOW_ONE_LOT`; the structural stop is never tightened to force a trade.

Trade audit fields include `base_quantity`, `symbol_quantity_scale`, `portfolio_quantity_scale`, `quantity_scale`, active reasons, and entry-time recovery deficits.

## Scaling Event Audit

Create `position_scaling_events.csv` with one row per state transition or recovery update:

```text
event_time, sequence, scope, symbol, event_type, candidate_id,
net_pnl, realized_cash, high_water, drawdown_fraction,
position_scale, recovery_deficit
```

`event_type` is one of `TRIGGER`, `EXTEND`, `PROGRESS`, or `RECOVER`. The report summarizes trigger, recovery, and scaled-entry counts by symbol and portfolio scope.

## Opportunity Charts

Each card continues to contain actual-contract daily, one-hour, and five-minute panels.

- Traded pullback candidates use the trade's actual-fill `final_target`.
- Unfilled pullback candidates use their trigger-derived planned target.
- Always-In displays `N/A` because it has no fixed target.
- Entry, stop, and finite target are included in each panel's price bounds, so all applicable levels remain visible even before price reaches them.
- Every panel reserves equal left and right plot slots around the signal. Missing data at the report interval boundary leaves empty slots rather than shifting the signal, so the signal marker remains at the center.
- Bars right of the marker remain explicitly labeled post-event review data.

The existing shared candlestick helper gains optional plot-slot positions and optional price-bound levels. Existing callers preserve their current behavior when these options are omitted.

## Reporting

Existing output files remain intact. Add:

- `position_scaling_events.csv`;
- scaling fields in `trades.csv`;
- `final_target` in `trades.csv` and chart index rows;
- effective scaling configuration and trigger/recovery counts in `summary.json` and `report.md`.

Missing historical execution mechanics still block the requested official interval as `BLOCKED_METADATA`; the new risk logic never supplies assumed mechanics.

## Test and Acceptance Criteria

1. A pullback position advances its stop only after a completed five-minute context event and retains its frozen actual-fill 2R target.
2. Stop and target ambiguity remains stop-first.
3. Long and short candidates require the symmetric prior-five-completed-day breakout, with no current or future daily bar leakage.
4. Every finite target is visible in all three panels and each signal marker uses the center plot slot.
5. Two consecutive symbol losses trigger scaling; zero or profit breaks a pre-trigger streak.
6. Active-state losses extend the recovery deficit, partial profits do not recover, and only strict excess recovery deactivates scaling.
7. Portfolio scaling triggers only when realized drawdown is strictly greater than the configured threshold.
8. Symbol and portfolio factors combine independently to 25%; sub-one-lot results are explicitly rejected.
9. Same-timestamp exits update scaling state before entries.
10. Candidate, chart, trade, scaling-event, and reproduction artifacts remain one-to-one and deterministic.
11. Focused tests, the affected strategy and metadata suites, Ruff, and compileall pass.
12. Fresh AG and AG+CU reports reconcile opportunity charts, target values, scaled quantities, fees, margin peaks, and scaling event counts.
