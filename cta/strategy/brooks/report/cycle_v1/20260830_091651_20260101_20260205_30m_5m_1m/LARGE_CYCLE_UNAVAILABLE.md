# LARGE_CYCLE_UNAVAILABLE

## Result

This run produced 8 candidates. Every candidate was retained in the audit, but no order was created because the configured 30min large cycle was `UNAVAILABLE` at its rejection event.

60min is review context only. The strategy decision used the latest completed 30min snapshot visible at `rejection_feature_asof`.

## Exact Requirement

`assess_direction_permission` checks the large cycle before confidence, direction agreement, sizing, or order creation:

1. `UNAVAILABLE` or `TRANSITION` returns `LARGE_CYCLE_UNAVAILABLE`.
2. A usable directional or trading-range state is required to continue.
3. The medium cycle, minimum confidence, direction, Always-In state, risk, and sizing gates are evaluated only after the large-cycle availability gate.

The causal market-cycle classifier can return `UNAVAILABLE` for three data-completeness reasons:

- `INSUFFICIENT_CAUSAL_HISTORY`: fewer than 252 completed prior 30min bars are available for the configured percentile lookback.
- `NON_FINITE_REQUIRED_STATE_INPUT`: at least one required cycle feature is missing or non-finite.
- `INSUFFICIENT_PRESSURE_HISTORY`: 252 finite prior directional-pressure observations have not yet accumulated after the initial feature history.

With all inputs finite, the first possible classified snapshot is the 505th completed 30min bar: 252 prior feature-history bars plus 252 prior pressure observations. `TRANSITION` remains blocked even after this warm-up because it does not provide stable large-cycle direction permission.

## Candidates

| # | Signal (Asia/Shanghai) | Active | Setup | Direction | Medium cycle | Large reason | Chart |
|---:|---|---|---|---|---|---|---|
| 1 | 2026-01-12 14:25 | 2026-01-12 14:27 | H2 | LONG | BULL_TIGHT_CHANNEL | `INSUFFICIENT_CAUSAL_HISTORY` | [001_20260112_142500_H2_LONG.png](candidate_charts/001_20260112_142500_H2_LONG.png) |
| 2 | 2026-01-16 00:30 | 2026-01-16 00:32 | H2 | LONG | BULL_BROAD_CHANNEL | `INSUFFICIENT_CAUSAL_HISTORY` | [002_20260116_003000_H2_LONG.png](candidate_charts/002_20260116_003000_H2_LONG.png) |
| 3 | 2026-01-19 09:33 | 2026-01-19 09:35 | H1 | LONG | BULL_TIGHT_CHANNEL | `INSUFFICIENT_CAUSAL_HISTORY` | [003_20260119_093300_H1_LONG.png](candidate_charts/003_20260119_093300_H1_LONG.png) |
| 4 | 2026-01-21 23:40 | 2026-01-21 23:42 | H2 | SHORT | BEAR_TIGHT_CHANNEL | `INSUFFICIENT_CAUSAL_HISTORY` | [004_20260121_234000_H2_SHORT.png](candidate_charts/004_20260121_234000_H2_SHORT.png) |
| 5 | 2026-01-22 01:48 | 2026-01-22 01:50 | H1 | SHORT | BEAR_TIGHT_CHANNEL | `INSUFFICIENT_CAUSAL_HISTORY` | [005_20260122_014800_H1_SHORT.png](candidate_charts/005_20260122_014800_H1_SHORT.png) |
| 6 | 2026-01-28 13:47 | 2026-01-28 13:49 | H1 | LONG | BULL_TIGHT_CHANNEL | `INSUFFICIENT_PRESSURE_HISTORY` | [006_20260128_134700_H1_LONG.png](candidate_charts/006_20260128_134700_H1_LONG.png) |
| 7 | 2026-01-28 13:52 | 2026-01-28 13:54 | H2 | LONG | BULL_TIGHT_CHANNEL | `INSUFFICIENT_PRESSURE_HISTORY` | [007_20260128_135200_H2_LONG.png](candidate_charts/007_20260128_135200_H2_LONG.png) |
| 8 | 2026-01-30 14:38 | 2026-01-30 14:40 | H1 | SHORT | BEAR_TIGHT_CHANNEL | `INSUFFICIENT_PRESSURE_HISTORY` | [008_20260130_143800_H1_SHORT.png](candidate_charts/008_20260130_143800_H1_SHORT.png) |

## Run Context

- Requested interval: `2026-01-01..2026-02-05`
- Requested and loaded symbol: `AG0.SHFE`
- Chart panels: exchange-trade-date daily / completed 60min / raw 1min
- Bars right of each SIGNAL marker: `POST-EVENT REVIEW ONLY`
- Orders, fills, and round trips: `0 / 0 / 0`
