# Cycle V1 CZCE Visible Vendor Parameters Fix

## Problem

The report run for `2026-01-01..2026-07-27` is blocked with
`MISSING_VISIBLE_VENDOR_PARAMETERS: PF2609.ZCE before
2026-07-13T21:00:00+08:00`.

On 2026-07-13 the Jin10 PF609 row exists, but its fee and price timestamps are
23:40:44 and 23:57:26, after the 21:00 night-session open. The same day's CZCE
official settlement parameters contain PF609 and are modeled as published at
15:30. The builder currently uses that official source only when the Jin10 row
is absent, not when the row exists but is not yet visible.

## Design

When a prior-open-date Jin10 row for a CZCE contract is later than the order
session open, the builder will try the existing CZCE official settlement
parameter path for that same source date. The official row must continue to
pass exact contract identity, exact settlement, fee type, margin, limit, and
publication-time validation. If it is absent or mismatched, metadata remains
blocked; no current values or assumed mechanics are substituted.

The existing behavior for non-CZCE exchanges and already-visible Jin10 rows
will remain unchanged. `cycle_v1.yaml` will remain unchanged. The metadata
builder schema version will increase so caches built under the old visibility
logic cannot be reused.

## Verification

Add a regression test for PF2609 on the 2026-07-14 exchange trade date where:

- the 2026-07-13 Jin10 PF609 row is published after 21:00;
- the 2026-07-13 CZCE official PF609 row is available at 15:30;
- extension building succeeds with CZCE official provenance and does not walk
  backward to an older vendor snapshot.

Run the focused metadata-builder test, then the complete `cycle_v1` test suite.
Finally rerun the report command and verify that this PF2609 reason code is no
longer emitted. A later, unrelated genuine metadata gap may still correctly
leave the new report as `BLOCKED_METADATA`.
