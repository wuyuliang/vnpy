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
- cache_key: b60a4a90188405b2be8c4b97874fb0cd09e414764f50fe1c7f4add6042b2b8cc
- contract_date_pairs: 9360
- generated_contract_rows: 280
- generated_daily_rows: 9360
- generated_fee_rows: 9360
- source_queries: 907
- source_audit_sha256: b308da5cf5cc7a6bd60f517a475663d304355de808d29614457191e802feedcf
- manifest_sha256: cc28d72b82661a4353ee2236db2fc9d15f2b7d694eb9d5cb3d06c45c15e78a77

## Funnel
- discovered_symbols: 68
- requested_symbols: 68
- loaded_symbols: 22
- eligible_symbol_days: 872
- cycle_snapshots: 236040
- candidates: 0
- eligible_plans: 0
- orders: 0
- fills: 0
- round_trips: 0

## Metadata Gaps
- I normalized_input: MISSING_TRADE_DATE_SESSION I0.DCE 2026-01-12 missing sessions ['night']
- JM normalized_input: MISSING_TRADE_DATE_SESSION JM0.DCE 2026-01-12 missing sessions ['night']
- J normalized_input: MISSING_TRADE_DATE_SESSION J0.DCE 2026-01-12 missing sessions ['night']
- M normalized_input: MISSING_TRADE_DATE_SESSION M0.DCE 2026-01-12 missing sessions ['night']
- P normalized_input: MISSING_TRADE_DATE_SESSION P0.DCE 2026-01-12 missing sessions ['night']
- Y normalized_input: MISSING_TRADE_DATE_SESSION Y0.DCE 2026-01-12 missing sessions ['night']
- OI normalized_input: MISSING_TRADE_DATE_SESSION OI0.CZCE 2026-01-12 missing sessions ['night']
- MA normalized_input: MISSING_TRADE_DATE_SESSION MA0.CZCE 2026-01-12 missing sessions ['night']
- TA normalized_input: MISSING_TRADE_DATE_SESSION TA0.CZCE 2026-01-12 missing sessions ['night']
- EG normalized_input: MISSING_TRADE_DATE_SESSION EG0.DCE 2026-01-12 missing sessions ['night']
- PP normalized_input: MISSING_TRADE_DATE_SESSION PP0.DCE 2026-01-12 missing sessions ['night']
- L normalized_input: MISSING_TRADE_DATE_SESSION L0.DCE 2026-01-12 missing sessions ['night']
- V normalized_input: MISSING_TRADE_DATE_SESSION V0.DCE 2026-01-12 missing sessions ['night']
- AL normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- ZN normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- AU normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- AG normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- SC normalized_input: SESSION_HEAD_GAP session 20260417:night segment night_continuous starts at 2026-04-17 00:00:00+08:00 instead of 2026-04-16 21:01:00+08:00
- LU normalized_input: MISSING_TRADE_DATE_SESSION LU0.INE 2026-01-12 missing sessions ['night']
- NR normalized_input: MISSING_TRADE_DATE_SESSION NR0.INE 2026-01-12 missing sessions ['night']
- CF normalized_input: MISSING_TRADE_DATE_SESSION CF0.CZCE 2026-01-12 missing sessions ['night']
- SR normalized_input: MISSING_TRADE_DATE_SESSION SR0.CZCE 2026-01-12 missing sessions ['night']
- C normalized_input: MISSING_TRADE_DATE_SESSION C0.DCE 2026-01-12 missing sessions ['night']
- CS normalized_input: MISSING_TRADE_DATE_SESSION CS0.DCE 2026-01-12 missing sessions ['night']
- A normalized_input: MISSING_TRADE_DATE_SESSION A0.DCE 2026-01-12 missing sessions ['night']
- B normalized_input: MISSING_TRADE_DATE_SESSION B0.DCE 2026-01-12 missing sessions ['night']
- RM normalized_input: MISSING_TRADE_DATE_SESSION RM0.CZCE 2026-01-12 missing sessions ['night']
- PG normalized_input: MISSING_TRADE_DATE_SESSION PG0.DCE 2026-01-12 missing sessions ['night']
- EB normalized_input: MISSING_TRADE_DATE_SESSION EB0.DCE 2026-01-12 missing sessions ['night']
- FG normalized_input: MISSING_TRADE_DATE_SESSION FG0.CZCE 2026-01-12 missing sessions ['night']
- SA normalized_input: MISSING_TRADE_DATE_SESSION SA0.CZCE 2026-01-12 missing sessions ['night']
- SH normalized_input: MISSING_TRADE_DATE_SESSION SH0.CZCE 2026-01-12 missing sessions ['night']
- PX normalized_input: MISSING_TRADE_DATE_SESSION PX0.CZCE 2026-01-12 missing sessions ['night']
- PF normalized_input: MISSING_TRADE_DATE_SESSION PF0.CZCE 2026-01-12 missing sessions ['night']
- AO normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- SS normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- BC normalized_input: SESSION_HEAD_GAP session 20260417:night segment night_continuous starts at 2026-04-17 00:00:00+08:00 instead of 2026-04-16 21:01:00+08:00
- SN normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- NI normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- PB normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- BZ normalized_input: MISSING_TRADE_DATE_SESSION BZ0.DCE 2026-01-12 missing sessions ['night']
- PL normalized_input: MISSING_TRADE_DATE_SESSION PL0.CZCE 2026-01-12 missing sessions ['night']
- PR normalized_input: MISSING_TRADE_DATE_SESSION PR0.CZCE 2026-01-12 missing sessions ['night']
- CY normalized_input: MISSING_TRADE_DATE_SESSION CY0.CZCE 2026-01-12 missing sessions ['night']
- RR normalized_input: MISSING_TRADE_DATE_SESSION RR0.DCE 2026-01-12 missing sessions ['night']
- AD normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
