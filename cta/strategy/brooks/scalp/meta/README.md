# Brooks Scalp Metadata

This directory contains the canonical RB/CU metadata bundle for the audited range
`2018-01-03..2026-04-16`:

- `exchange_calendar.csv`: 2,061 rows including warm-up and forward calendar coverage.
- `contract_specs.csv`: 126 source contracts.
- `contract_daily.csv`: 4,134 contract/trading-day rows.
- `fee_margin_schedule.csv`: 4,134 effective SHFE fee and margin rows.
- `manifest.json`: row, date, and SHA-256 coverage for every canonical CSV.
- `shfe_source_audit.jsonl.gz`: hashes and URLs for 4,070 official SHFE source files.
- `build_summary.json`: build range and source-session availability summary.

Rebuild the bundle with:

```bash
python3 -m cta.strategy.brooks.scalp.shfe_metadata_builder \
  --start 2018-01-03 \
  --end 2026-04-16 \
  --data-root cta/data/origin/minute \
  --output-root cta/strategy/brooks/scalp/meta \
  --cache-root /private/tmp/vnpy_shfe_scalp_metadata \
  --workers 8
```

The builder uses official SHFE daily market (`kx`) and settlement-parameter (`js`)
archives. Tushare is only a mirror for the SHFE trading calendar and contract-basic
archive. A blank `night_session_start` means the local minute source does not contain a
complete, continuous night session; it does not claim that the exchange was closed.

Fees and margins are SHFE exchange parameters only. They do not include broker markups,
so broker statements or a versioned broker schedule remain mandatory before paper/live
approval. Unknown, late, missing, or hash-mismatched metadata fails closed with
`BLOCKED_METADATA`.
