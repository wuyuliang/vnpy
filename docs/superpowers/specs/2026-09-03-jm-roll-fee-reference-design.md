# Historical Same-Contract Roll Fee Reference Design

## Problem

The multi-timeframe trend run for `2026-01-01..2026-07-01` is blocked while
preparing warmup metadata for the `JM2601.DCE -> JM2605.DCE` roll. Jin10 shows
the `JM2605` fee published at `2025-12-03T23:36:52+08:00`, before the relevant
session opened at `2025-12-04T21:00:00+08:00`. The historical fallback removes
the exact local contract from the older snapshot, so it discards this direct
causal evidence and reports `MISSING_ROLL_FEE_REFERENCE`.

## Decision

Update `_validated_historical_roll_fee_reference` to prefer an exact
same-contract row from the historical snapshot when all of these conditions
hold:

- the row's publication timestamp is no later than `session_open`;
- its open, close-yesterday, and close-today fee expressions parse to the same
  fee tuple as the exact target-contract row in the current snapshot;
- the historical snapshot date precedes the current source date.

Return the historical contract and snapshot date as the audit reference. If an
eligible exact historical row is absent, retain the existing same-root fallback.
If fee tuples conflict, the existing failure-closed behavior remains
authoritative. If no causal reference exists after exhausting prior snapshots,
the multi-timeframe trend runner may use current-contract runtime parameters in
the dependency-injection style used by `cta/sim` and `cta/live`. This fallback
is recorded as assumed metadata and may only produce a
`NON_CAUSAL_SCENARIO`; it never populates official performance.

If the immediately preceding trading-day snapshot has no usable reference,
continue through earlier open dates using the existing vendor-history loop.
Stop at the first causal exact-contract or same-root reference. Exhausting the
available calendar without a matching reference remains a metadata block.

## Scope

The historical roll-fee validator, metadata cache audit, source-file boundary,
and scenario report contract change. There is no symbol allowlist. The generic
cycle_v1 metadata builder remains strict by default; only the multi-timeframe
trend runner opts into runtime defaults.

## Verification

Add a regression test reproducing the `JM2605` timeline and assert that the
historical exact-contract row is accepted with its original `known_at` and
snapshot-qualified reference. Add an integration case reproducing `A2605`,
where two intervening snapshots contain no exact contract before an older
visible matching row. Keep the existing cross-contract reference and fee-change
rejection tests green, then run the full vendor metadata builder test module.
Finally rerun the original report command and require a non-blocked metadata
build with no false `MISSING_ROLL_FEE_REFERENCE` for either pattern.
