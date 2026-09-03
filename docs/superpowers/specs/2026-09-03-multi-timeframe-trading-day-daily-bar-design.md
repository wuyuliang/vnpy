# Multi-Timeframe Trend Trading-Day Daily Bar Design

## Goal

`multi_time_frame_trend` must build each futures daily bar from the complete
exchange trading day, not from a natural calendar date. For a product with a
night session, the daily bar starts at 21:00 on the prior open date and ends at
the afternoon close of the target exchange trade date.

## Trading-Day Boundary

- Minute bars are grouped by `contract_code` and `exchange_trade_date`.
- The first tradable minute after the prior-date 21:00 open supplies the daily
  open.
- All configured night and day session segments contribute to high, low, and
  volume.
- The final minute at the target trade date's afternoon close supplies the
  daily close, `bar_end`, and `feature_asof`.
- The daily bar is unavailable to strategy decisions until that afternoon
  close, preserving causal alignment.
- Exchange-calendar mappings handle weekends and holidays. In particular,
  Friday night and Monday daytime activity belong to Monday's daily bar.
- Products without a night session retain their declared daytime-only daily
  boundary.

## Implementation

Keep `aggregate_completed_daily_bars()` as the single aggregation path used by
`multi_time_frame_trend`. It already groups normalized minute bars by exchange
trade date, so production code changes are required only if the regression test
exposes a mismatch.

Add a strategy-level regression test with shortened synthetic session segments.
The test assigns Friday-night and Monday-day minutes the same Monday
`exchange_trade_date`, then verifies that exactly one completed daily bar is
emitted with the night open, combined extrema and volume, Monday afternoon
close, and Monday close timestamp.

## Verification

Run the new regression test first and confirm that it fails for the intended
missing guarantee if implementation work is needed. Apply only the smallest
aggregation correction necessary, rerun the focused test, and then run the
relevant multi-timeframe trend and shared session test suites.
