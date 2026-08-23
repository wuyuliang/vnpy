# Cycle V1 Top-N Metadata Repair Design

## Problem

The integrated `cycle_v1` command can select explicit roots, ranking Top-N roots,
and EMA-eligible roots, but the default canonical mechanics bundle currently
covers only RB and CU. The 2026-01-01 through 2026-07-27 run selected 68 roots,
loaded only RB/CU, and correctly returned `BLOCKED_METADATA` for the other 66.
Minute bars are present; the failure is execution-mechanics coverage, not market
data coverage or report rendering.

## Safety Contract

- Official results remain fail-closed. Missing or ambiguous tick, multiplier,
  session, lifecycle, fee, margin, limit, calendar, status, or order capability
  keeps the requested interval at `BLOCKED_METADATA` with null performance.
- No current-value fallback, root default, inferred fee, or silently reduced
  universe is allowed.
- Existing direct SHFE RB/CU rows remain authoritative and take precedence.
- Vendor mirrors must retain raw row content, retrieval time, source URL, and
  SHA256. A mirrored row is usable only after contract identity, settlement,
  and margin reconcile across the independent source tables.

## Data Sources

- Tushare `fut_basic`: actual-contract lifecycle, exchange, multiplier,
  minimum quote unit, and published trading-time description.
- Tushare `trade_cal`: exchange open calendar and adjacent open dates.
- Tushare `fut_settle`: daily settlement, exchange margin, and an independent
  fee reference.
- Jin10 historical futures settlement mirror through AKShare
  `futures_comm_js`: prior-open settlement, next-session price limits, and
  explicit open/close-yesterday/close-today fee expressions.

The account does not have Tushare `ft_limit` permission. Jin10 limit and fee
rows are therefore accepted only as audited exchange-parameter mirrors after
their contract, prior settlement, and margin agree with Tushare. A disagreement
is a metadata gap, not a value-selection rule.

## Architecture

Add a focused `vendor_metadata_builder` module under `cycle_v1/backtest`. It
receives the exact selected `DiscoveredSymbol` set plus the local minute root and
requested dates. It extracts the actual `(contract_code, exchange_trade_date)`
pairs from local partitions, fetches only the required source dates, validates
them, writes a complete canonical extension, and merges it with the immutable
base bundle into a content-addressed cache.

The runner invokes this preparation after minute-data selection and before
`MetadataBundle.load`. The cache key includes selected contract/date pairs,
requested dates, base manifest hash, and builder schema version. A valid cached
manifest avoids network calls. The runner records the build/cache audit under
`summary.json.execution_metadata_update` and renders a compact metadata-update
section in `report.md`.

## Normalization Rules

- Exchange suffix aliases are normalized to `SHFE`, `DCE`, `CZCE`, `INE`,
  `GFEX`, and `CFFEX`, while canonical rows retain the exact contract code used
  by local minute data.
- CZCE/GFEX short vendor symbols are matched by root and delivery month to the
  unique local actual contract. Ambiguous matches block the row.
- `quote_unit_desc` must begin with a positive numeric minimum quote. Contract
  size comes from positive `per_unit`, except financial contracts where the
  explicitly supplied positive `multiplier` is required.
- Trading-time descriptions map only to declared commodity day,
  night-to-23:00, night-to-01:00, or night-to-02:30 templates. Unknown text is
  blocked.
- Fee text is parsed as either a nonnegative per-lot cash fee or a Chinese
  proportional expression such as `1/万分之`. Mixed or unrecognized expressions
  are blocked.
- For trade date D, prior-open mirror values provide D's pre-settlement, limits,
  margin, and fees. Tushare `fut_settle(D)` must agree on margin; Tushare
  `fut_settle(prior_open)` must agree on pre-settlement. D settlement remains a
  post-close field and is never exposed as a D-session decision input.

## Failure Handling

Preparation processes all selected roots and collects exact gaps. Network,
permission, missing-row, ambiguous-code, parse, and reconciliation errors are
stored with root, contract, date, field, source, and reason code. The cache is
published atomically only when all requested mechanics rows are complete.
Otherwise the runner writes a normal blocked report and does not use a partial
extension.

## Verification

- Unit tests cover fee expressions, contract-code matching, session mapping,
  prior-open alignment, cross-source mismatches, cache integrity, and base-row
  precedence.
- Integration tests prove that runner metadata preparation uses the exact
  explicit/Top-N/EMA union and that a failed build remains `BLOCKED_METADATA`.
- Existing cycle_v1 and scalp metadata tests must remain green.
- The real 2026-01-01 through 2026-07-27 command must report every selected root,
  100% mechanics coverage, non-null official performance, and a complete funnel,
  or preserve a precise externally unresolved gap without fabricating results.
