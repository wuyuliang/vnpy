# Brooks Cycle V1 Report

Official status: BLOCKED_METADATA

## Reproduction Command
- working_directory: /Users/wuyuliang/code/vnpy

```bash
cd /Users/wuyuliang/code/vnpy && python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --symbols LC --start 2026-01-01 --end 2026-02-05 --long-tf 30min --medium-tf 5min --short-tf 1min --initial-equity 200000 --download-minute-data --top-n 2 --include-ema-eligible
```

## Execution Metadata Update
- status: READY
- cache_hit: False
- cache_key: 2ae2f4a867c94374dcb83bad7d6477f83ce35fccee3f9b3e67c1e205b2af65cc
- contract_date_pairs: 1659
- generated_contract_rows: 98
- generated_daily_rows: 1659
- generated_fee_rows: 1659
- source_queries: 199
- source_audit_sha256: 548614b5bb3626a15d7510b7b400568e4dcd18cec9a84891bc8d00349f600057
- manifest_sha256: aa604d6fc0a8b86583ee5d69c27e0e166eb1950ed9e60f3fd91da5d38f540691

## Funnel
- discovered_symbols: 68
- requested_symbols: 68
- loaded_symbols: 68
- eligible_symbol_days: 442
- cycle_snapshots: 151440
- candidates: 0
- eligible_plans: 0
- orders: 0
- fills: 0
- round_trips: 0

## Metadata Gaps
- ALL execution_metadata: BLOCKED_METADATA fee schedule GFEX-LC2605-20260105 was not known at session open
