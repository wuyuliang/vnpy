# Brooks Cycle V1 Report

Official status: BLOCKED_METADATA

## Reproduction Command
- working_directory: /Users/wuyuliang/code/vnpy

```bash
cd /Users/wuyuliang/code/vnpy && python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --symbols LC --start 2026-01-01 --end 2026-07-27 --long-tf 30min --medium-tf 5min --short-tf 1min --initial-equity 200000 --download-minute-data --top-n 2 --include-ema-eligible
```

## Execution Metadata Update
- status: READY
- cache_hit: False
- cache_key: 43a865c165a6aadde3df7d2ac09f3c2f9114624dc690314f54498be905558a90
- contract_date_pairs: 9360
- generated_contract_rows: 280
- generated_daily_rows: 9360
- generated_fee_rows: 9360
- source_queries: 907
- source_audit_sha256: 84dfd6d4ea29f5aeef101976fd624a5a329c7a1659d888bc1962802ac2878261
- manifest_sha256: 884d55a11d0cf916e17ab803cde845553c7726233f0bf99a4ad288576b264a42

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
- RB normalized_input: MISSING_TRADE_DATE_SESSION RB0.SHFE 2026-04-20 missing sessions ['night']
- CU normalized_input: SESSION_HEAD_GAP session 20260417:night segment night_continuous starts at 2026-04-17 00:00:00+08:00 instead of 2026-04-16 21:01:00+08:00
