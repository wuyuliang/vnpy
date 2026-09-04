# Cycle V1 Official Backtest Repair Design

## Goal

Repair the independent `cycle_v1` CLI so the requested RB/CU interval can load
audited one-minute data, validate point-in-time execution metadata, replay actual
contracts, and report net performance instead of an empty metadata-blocked scan.

## Frozen Scope

- Download only natural-date one-minute files from `2026-01-01` through
  `2026-07-27`, inclusive.
- Process only RB and CU in this repair. Existing files are skipped and never
  overwritten by default.
- Preserve actual mapped contract codes in every minute row.
- Never remove `BLOCKED_METADATA` unless the entire requested interval has
  complete minute data and effective-dated execution metadata.
- Keep assumed mechanics out of the official curve.

## Components

1. `market_data_update.py` incrementally downloads mapped RB/CU one-minute bars
   into each discovered canonical source directory and writes an audit manifest.
2. `execution_metadata.py` adapts audited SHFE/scalp metadata into cycle_v1
   lifecycle, daily status, order-capability, fee, margin, tick and limit
   snapshots. Every derived row retains source and `known_at` evidence.
3. `replay.py` drives the shared cycle_v1 core on completed small bars, aligns
   completed medium/large context, activates orders on later one-minute events,
   uses adverse OHLC ambiguity, and accounts on actual contracts.
4. `runner.py` performs coverage validation before replay and emits candidates,
   plans, orders, fills, round trips, daily equity, grouped metrics, coverage and
   source audits.

## Status Contract

- Any minute or metadata gap in the requested interval keeps
  `requested_interval_status=BLOCKED_METADATA` and official performance null.
- Complete mechanics plus a completed replay may produce official performance.
- A zero-trade completed replay reports valid zero-trade metrics; it is not
  silently converted into a profitable or blocked run.
- A covered subperiod may be reported only as
  `COVERED_SUBPERIOD_DIAGNOSTIC`; it never replaces the requested interval.

## Verification

- Unit tests cover date-range enforcement, skip/no-overwrite behavior, metadata
  visibility, coverage failure, causal order activation, adverse exits and PnL
  reconciliation.
- Integration tests cover the complete CLI funnel and report bundle.
- The final real-data run targets `2026-01-01..2026-07-27` with
  `1hour/30min/5min` and must retain source hashes and exact coverage evidence.
