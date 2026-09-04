# Brooks Cycle V1 Report

Official status: BLOCKED_METADATA

## Reproduction Command
- working_directory: /Users/wuyuliang/code/vnpy

```bash
cd /Users/wuyuliang/code/vnpy && python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --symbols LC --start 2026-01-01 --end 2026-07-27 --long-tf 30min --medium-tf 5min --short-tf 1min --initial-equity 200000 --download-minute-data --top-n 2 --include-ema-eligible
```

## Execution Metadata Update
- status: READY
- cache_hit: True
- cache_key: e09967da3a10cd7d84a993fddb2ddc128374b4b781db664306c2e1df99778c74
- contract_date_pairs: 9360
- generated_contract_rows: 280
- generated_daily_rows: 9360
- generated_fee_rows: 9360
- source_queries: 907
- source_audit_sha256: 0fba36bda22ffe58b7aeb98c64e40d12151b13137b9ef855ca46a5404770c16d
- manifest_sha256: 9f774bfd3cd98ba491bd33568f46054d41249175be271f7c5b14cf1ead8317d6

## Funnel
- discovered_symbols: 68
- requested_symbols: 68
- loaded_symbols: 66
- eligible_symbol_days: 2727
- cycle_snapshots: 918045
- candidates: 0
- eligible_plans: 0
- orders: 0
- fills: 0
- round_trips: 0

## Metadata Gaps
- RB normalized_input: BLOCKED_METADATA BLOCKED_METADATA: overlapping fee schedule SHFE-RB2605-20260112 for RB2605.SHF at 2026-01-12T09:00:00+08:00
- CU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: overlapping fee schedule SHFE-CU2602-20260112 for CU2602.SHF at 2026-01-12T09:00:00+08:00
