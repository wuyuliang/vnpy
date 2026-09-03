# Vendor Contract Duplicate Selection Design

## Goal

Prevent metadata preparation from stopping when a vendor returns the same
contract code more than once. The fix applies to every caller of the shared
contract matcher, including `multi_time_frame_trend` metadata preparation.

## Root Cause

`match_vendor_contract()` currently counts every matching list element. A
response such as `['ag2502', 'ag2502']` therefore raises
`AMBIGUOUS_CONTRACT`, even though both entries identify the same normalized
contract. `_exact_contract_row()` independently requires exactly one selected
row, so duplicate rows would still block after fixing the matcher.

## Selection Rules

- Normalize candidate codes with surrounding whitespace removed and
  case-folded to uppercase for identity comparisons.
- Preserve the existing preference for a full exact local-contract match over
  a root and delivery-month fallback match.
- When every matching candidate has the same normalized code, return the first
  candidate in vendor response order.
- In `_exact_contract_row()`, select all rows with that normalized code and
  return the first row in vendor response order, even if duplicate rows contain
  different non-code fields.
- Continue raising `MISSING_CONTRACT` when no code matches.
- Continue raising `AMBIGUOUS_CONTRACT` when fallback matching finds multiple
  distinct normalized contract codes. This preserves protection against a real
  cross-contract or cross-exchange ambiguity.

## Scope

Change only the shared matching and row-selection functions in
`vendor_metadata_builder.py` and their focused unit tests. Do not modify report
artifacts, cached metadata, vendor responses, or unrelated strategy rules.

## Tests

Add or update focused tests proving that:

1. Repeated identical values such as `['ag2502', 'ag2502']` select the first.
2. Case variants such as `['TA609', 'ta609']` select the first.
3. Duplicate rows with conflicting non-code fields return the first source row.
4. Multiple distinct fallback contract codes remain `AMBIGUOUS_CONTRACT`.

Run the focused matcher tests, the full vendor metadata builder test module,
and the metadata preparation tests used by `multi_time_frame_trend`. If network
access and source availability permit, rerun the reported AG command under a
new run identifier to confirm metadata preparation proceeds beyond this error.
