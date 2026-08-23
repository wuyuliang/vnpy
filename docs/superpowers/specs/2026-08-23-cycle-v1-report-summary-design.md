# Cycle V1 Report Summary Design

## Objective

Put a concise trading summary at the top of every cycle_v1 `report.md`, before
the funnel and detailed performance sections.

## Metrics

`compute_performance_metrics` will add these authoritative fields:

- `total_net_pnl`: sum of trade-level `net_pnl`.
- `total_return`: final daily equity divided by initial equity minus one.
- `final_equity`: final value in `daily_equity.csv`.

The top Markdown summary will show, in this order:

1. trade count
2. total net PnL
3. total return
4. annualized return
5. win rate
6. maximum drawdown
7. profit factor
8. expectancy R
9. fees
10. slippage
11. final equity

## Formatting

- Currency and cash values use two decimal places.
- Returns, win rate, and drawdown use percentage formatting with two decimals.
- Profit factor and expectancy R use four decimal places.
- Undefined values render as `N/A`.
- Zero-trade reports still show trade count 0, net PnL 0.00, total return 0.00%,
  and final equity when an official performance curve exists.

## Report Order

The generated Markdown order is official status, `Trading Summary`, funnel,
complete official performance, rejection summary, candidate rejections, and
metadata gaps. `summary.json` remains the source of truth for Markdown values.

## Compatibility And Testing

Existing CSV schemas remain unchanged. Tests will cover the three new metrics,
summary placement before the funnel, percentage/cash formatting, and zero-trade
behavior. Existing report bundles can be regenerated from their saved CSV and
summary data; the current 30m/5m/1m report will be updated after verification.
