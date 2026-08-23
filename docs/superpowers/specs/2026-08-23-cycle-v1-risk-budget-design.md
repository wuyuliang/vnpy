# Cycle V1 2% Risk Budget Design

## Objective

Change the cycle_v1 per-trade base risk budget from 0.25% to 2% of current
account equity while preserving the existing market-cycle risk multipliers.

## Frozen Semantics

- `risk_per_trade = 0.02`.
- `max_trade_risk = 0.02`, so the previous 0.5% hard cap does not silently
  override the new base budget.
- Existing cycle multipliers remain unchanged: strong breakout 1.0, tight
  channel 0.8, broad channel 0.6, trading range 0.4.
- For 200,000 equity before other risk reductions, the budgets are 4,000,
  3,200, 2,400, and 1,600 respectively.
- Confidence, drawdown, direction-permission, portfolio, sector, margin, and
  one-lot risk gates remain unchanged.
- Structural stops must not be narrowed to force a trade.

## Changes

Update the cycle_v1 YAML configuration and the Brooks/cycle CLI documents.
Add a sizing regression test that loads the production configuration and
verifies the 3,200 tight-channel budget for a 200,000 account. Re-run the same
RB/CU interval and publish a new non-overwriting report directory.

## Acceptance

- The production configuration loads 2% for both the base risk and hard cap.
- Tight-channel sizing reports a 3,200 budget before other reductions.
- Existing cycle_v1 and legacy scalp tests remain green.
- The new report is `COMPLETE` and records candidate-level budget, one-lot
  loss, plans, orders, fills, and trades without fabricating execution.
