# Brooks Cycle V1 Report

Official status: BLOCKED_METADATA

## Reproduction Command
- working_directory: /Users/wuyuliang/code/vnpy

```bash
cd /Users/wuyuliang/code/vnpy && python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --symbols LC --start 2026-01-01 --end 2026-07-27 --long-tf 30min --medium-tf 5min --short-tf 1min --initial-equity 200000 --download-minute-data --top-n 2 --include-ema-eligible
```

## Execution Metadata Update
- status: BLOCKED_METADATA
- reason_code: SHFE_OFFICIAL_LATE_LIMIT_MISMATCH
- reason: SHFE_OFFICIAL_LATE_LIMIT_MISMATCH: AG2606.SHF 2026-03-09: official=18219.0/24152.0 jin10=16948.0/25423.0

## Funnel
- discovered_symbols: 68
- requested_symbols: 68
- loaded_symbols: 0
- eligible_symbol_days: 0
- cycle_snapshots: 0
- candidates: 0
- eligible_plans: 0
- orders: 0
- fills: 0
- round_trips: 0

## Metadata Gaps
- ALL execution_metadata_preparation: SHFE_OFFICIAL_LATE_LIMIT_MISMATCH SHFE_OFFICIAL_LATE_LIMIT_MISMATCH: AG2606.SHF 2026-03-09: official=18219.0/24152.0 jin10=16948.0/25423.0
