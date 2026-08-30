# Cycle V1 AG Candidate Charts Design

## Goal

Add review artifacts for the eight AG candidates produced by backtest run
`20260830_091651_20260101_20260205_30m_5m_1m`. The artifacts must show each
candidate's exact signal and activation times and explain why every candidate
was rejected with `LARGE_CYCLE_UNAVAILABLE`.

## Outputs

Write all generated artifacts inside the existing backtest directory:

- `candidate_charts/001_20260112_142500_H2_LONG.png` through
  `candidate_charts/008_20260130_143800_H1_SHORT.png`.
- `candidate_charts/index.csv` with one row per candidate.
- `LARGE_CYCLE_UNAVAILABLE.md` with the rule definition and per-candidate
  diagnostic reason.

The eight PNG files are ordered by `signal_time`. Each file is a 1680 x 1240
card with daily, 60-minute, and 1-minute candlestick panels.

A small reusable CLI module accepts a report directory and data root, then
generates these artifacts without changing the original CSV or JSON files. The
current run invokes that CLI once after its behavior is covered by focused
tests.

## Chart Content

Reuse the established Pillow candlestick visual language from the Brooks scalp
reports: warm neutral background, red up candles, green down candles, compact
labels, and three vertically stacked panels.

Each chart header shows:

- candidate sequence and ID;
- AG contract, H1/H2 setup, direction, and medium cycle;
- `signal_time`, `active_time`, entry, stop, and target;
- rejection code and the underlying large-cycle diagnostic reason;
- a note that 60-minute bars are visual context while the strategy's configured
  large timeframe for this run is 30 minutes.

The panels use exchange-trade-date daily bars, completed 60-minute bars, and raw
1-minute bars. The daily panel shows up to 20 bars before and 5 bars after the
signal trade date. The 60-minute panel shows up to 36 completed bars before and
12 bars after the signal. The 1-minute panel shows up to 80 bars before and 40
bars after the signal. Available report boundaries may shorten either side.

The signal and activation events are marked separately; entry, stop, and target
are shown as reference levels where the panel scale makes them visible. Bars to
the right of the signal marker are labeled `POST-EVENT REVIEW ONLY` and are not
presented as decision inputs. No fill marker is drawn because no order was
created.

## Diagnostic Semantics

`LARGE_CYCLE_UNAVAILABLE` is emitted before sizing when the configured large
cycle is either `UNAVAILABLE` or `TRANSITION`. In this run the large cycle is
actually `UNAVAILABLE` for all eight candidates.

The report explains the causal classifier requirements without changing them:

- `INSUFFICIENT_CAUSAL_HISTORY`: the 30-minute classifier has fewer than the
  configured 252 completed prior bars required by `percentile_lookback`.
- `NON_FINITE_REQUIRED_STATE_INPUT`: a required cycle feature is unavailable or
  non-finite, so classification fails closed.
- `INSUFFICIENT_PRESSURE_HISTORY`: after the initial feature history exists, the
  classifier still requires 252 finite prior directional-pressure observations.
- Only after those gates pass can the state machine classify a usable large
  cycle. A `TRANSITION` state also remains unavailable for direction permission.

For this run, the candidates through 2026-01-22 are diagnosed as
`INSUFFICIENT_CAUSAL_HISTORY`; the three candidates on 2026-01-28 and
2026-01-30 are diagnosed as `INSUFFICIENT_PRESSURE_HISTORY`.

## Data Flow

Read `candidates.csv`, candidate-linked rows from `rejections.csv`, and actual AG
minute parquet partitions. Reload normalized bars with the exact audited
metadata cache recorded by `summary.json`. Aggregate each bar only from source
minutes at or before that bar end. Use `exchange_trade_date` for daily
aggregation and session-aware completed buckets for 60-minute aggregation. Do
not alter the backtest outputs used as inputs.

The rejection diagnosis uses the actual completed 30-minute snapshot visible at
the rejection's `feature_asof`. The 60-minute chart is diagnostic context only
and never replaces that decision snapshot. If an early ineligible period is not
exported in `cycle_snapshots.csv`, a later
`INSUFFICIENT_CAUSAL_HISTORY` snapshot may establish the same reason for the
earlier candidate because completed-history counts are monotonic; the index
records this diagnostic source explicitly.

## Verification

Automated checks must verify:

- exactly eight index rows and eight PNG files exist;
- every candidate ID, signal time, and active time matches `candidates.csv`;
- every index row has `LARGE_CYCLE_UNAVAILABLE` and a non-empty underlying
  reason;
- all three chart panels contain bars and do not include bars ending after the
  selected display window;
- every PNG opens successfully and has the expected dimensions;
- post-event bars are visually labeled and never used to derive the rejection
  reason;
- existing cycle_v1 tests remain passing.
