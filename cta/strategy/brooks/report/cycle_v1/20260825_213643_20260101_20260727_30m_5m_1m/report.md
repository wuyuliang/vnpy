# Brooks Cycle V1 Report

Official status: BLOCKED_METADATA

## Reproduction Command
- working_directory: /Users/wuyuliang/code/vnpy

```bash
cd /Users/wuyuliang/code/vnpy && python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --symbols LC --start 2026-01-01 --end 2026-07-27 --long-tf 30min --medium-tf 5min --short-tf 1min --initial-equity 200000 --download-minute-data --top-n 2 --include-ema-eligible
```

## Execution Metadata Update
- status: BLOCKED_METADATA
- reason_code: ROLL_FEE_VALIDATION_FAILED
- reason: ROLL_FEE_VALIDATION_FAILED: PG2607.DCE 2026-04-15: late=((0.0, 6.0), (0.0, 6.0), (0.0, 6.0))

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
- ALL execution_metadata_preparation: ROLL_FEE_VALIDATION_FAILED ROLL_FEE_VALIDATION_FAILED: PG2607.DCE 2026-04-15: late=((0.0, 6.0), (0.0, 6.0), (0.0, 6.0))
