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
- cache_key: dd1e8d5cc372e13712b8e69ed280b72b3e50a8cfd6c5360f511d2af1598f67e7
- contract_date_pairs: 9360
- generated_contract_rows: 280
- generated_daily_rows: 9082
- generated_fee_rows: 9082
- source_queries: 885
- source_audit_sha256: 45461112167747952f164cb6e9c9d9d9bf741d37959d3e2b3eabba3e940ceb4d
- manifest_sha256: 0866845abe00486297fcc6442d358258405466d130061a77a5926fa1bb695dd3

## Funnel
- discovered_symbols: 68
- requested_symbols: 68
- loaded_symbols: 12
- eligible_symbol_days: 511
- cycle_snapshots: 122415
- candidates: 0
- eligible_plans: 0
- orders: 0
- fills: 0
- round_trips: 0

## Metadata Gaps
- RB normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for RB2601.SHF->RB2605.SHF 2025-12-03
- HC normalized_input: MISSING_TRADE_DATE_BARS HC0.SHFE missing complete open dates: ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05', '2025-09-08', '2025-09-09', '2025-09-10', '2025-09-11', '2025-09-12']
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
- FU normalized_input: MISSING_TRADE_DATE_BARS FU0.SHFE missing complete open dates: ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05', '2025-09-08', '2025-09-09', '2025-09-10', '2025-09-11', '2025-09-12']
- BU normalized_input: MISSING_TRADE_DATE_BARS BU0.SHFE missing complete open dates: ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05', '2025-09-08', '2025-09-09', '2025-09-10', '2025-09-11', '2025-09-12']
- SC normalized_input: SESSION_HEAD_GAP session 20260417:night segment night_continuous starts at 2026-04-17 00:00:00+08:00 instead of 2026-04-16 21:01:00+08:00
- LU normalized_input: MISSING_TRADE_DATE_SESSION LU0.INE 2026-01-12 missing sessions ['night']
- RU normalized_input: MISSING_TRADE_DATE_BARS RU0.SHFE missing complete open dates: ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05', '2025-09-08', '2025-09-09', '2025-09-10', '2025-09-11', '2025-09-12']
- BR normalized_input: MISSING_TRADE_DATE_BARS BR0.SHFE missing complete open dates: ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05', '2025-09-08', '2025-09-09', '2025-09-10', '2025-09-11', '2025-09-12']
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
- SP normalized_input: MISSING_TRADE_DATE_BARS SP0.SHFE missing complete open dates: ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05', '2025-09-08', '2025-09-09', '2025-09-10', '2025-09-11', '2025-09-12']
- BC normalized_input: SESSION_HEAD_GAP session 20260417:night segment night_continuous starts at 2026-04-17 00:00:00+08:00 instead of 2026-04-16 21:01:00+08:00
- SN normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- NI normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- PB normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- BZ normalized_input: MISSING_TRADE_DATE_SESSION BZ0.DCE 2026-01-12 missing sessions ['night']
- PL normalized_input: MISSING_TRADE_DATE_SESSION PL0.CZCE 2026-01-12 missing sessions ['night']
- PR normalized_input: MISSING_TRADE_DATE_SESSION PR0.CZCE 2026-01-12 missing sessions ['night']
- CY normalized_input: MISSING_TRADE_DATE_SESSION CY0.CZCE 2026-01-12 missing sessions ['night']
- RS normalized_input: NEGATIVE_ACTIVITY volume/turnover/open_interest must be nonnegative
- RR normalized_input: MISSING_TRADE_DATE_SESSION RR0.DCE 2026-01-12 missing sessions ['night']
- OP normalized_input: MISSING_TRADE_DATE_BARS OP0.SHFE missing complete open dates: ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05', '2025-09-08', '2025-09-09', '2025-09-10', '2025-09-11', '2025-09-12']
- AD normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- FB normalized_input: INVALID_OHLC source contains invalid OHLC
