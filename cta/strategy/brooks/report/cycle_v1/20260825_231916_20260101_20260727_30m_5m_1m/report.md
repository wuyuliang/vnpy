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
- cache_key: bccde35be49c77be1d5a7559f501d083cb475ba3766c8f4956de791505321636
- contract_date_pairs: 9180
- generated_contract_rows: 280
- generated_daily_rows: 8910
- generated_fee_rows: 8910
- source_queries: 884
- source_audit_sha256: 88506aa53c37af4d2ac84d51d33e798d6c6f5c6f105d5879ad16fb703192aa32
- manifest_sha256: 5035b8de9aa4271f17cb4d53eea5892b297cbb00a37b89eb077d18d3439663c7

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
- LC normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for GFEX 2026-01-05
- RB normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for SHFE 2026-01-05
- HC normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for HC2605.SHF 2026-04-03, got 0
- I normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for I2605.DCE 2026-04-09, got 0
- JM normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for DCE 2026-01-05
- J normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for J2605.DCE 2026-04-23, got 0
- M normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for M2605.DCE 2026-04-09, got 0
- P normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for P2605.DCE 2026-04-08, got 0
- Y normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for DCE 2026-01-05
- OI normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for OI2605.ZCE 2026-04-15, got 0
- MA normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- TA normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- EG normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for EG2605.DCE 2026-04-21, got 0
- PP normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for PP2605.DCE 2026-04-16, got 0
- L normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for L2605.DCE 2026-04-15, got 0
- V normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for V2605.DCE 2026-04-15, got 0
- CU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for SHFE 2026-01-05
- AL normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for AL2603.SHF 2026-02-25, got 0
- ZN normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for ZN2602.SHF 2026-01-14, got 0
- AU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for AU2602.SHF 2026-01-20, got 0
- AG normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- FU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for FU2603.SHF 2026-02-06, got 0
- BU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for BU2602.SHF 2026-01-07, got 0
- SC normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for SC2602.INE 2026-01-16, got 0
- LU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for LU2603.INE 2026-01-23, got 0
- RU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for RU2605.SHF 2026-04-09, got 0
- BR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for BR2602.SHF 2026-01-13, got 0
- NR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for NR2603.INE 2026-02-04, got 0
- CF normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for CF2605.ZCE 2026-04-09, got 0
- SR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for SR2605.ZCE 2026-04-03, got 0
- C normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for C2603.DCE 2026-02-10, got 0
- CS normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for CS2603.DCE 2026-02-13, got 0
- A normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for A2605.DCE 2026-04-15, got 0
- B normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for B2607.DCE 2026-06-24, got 0
- RM normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for RM2605.ZCE 2026-04-15, got 0
- PG normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for PG2602.DCE 2026-01-14, got 0
- EB normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for EB2602.DCE 2026-01-22, got 0
- FG normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- SA normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for SA2605.ZCE 2026-04-16, got 0
- SH normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for SH2603.ZCE 2026-02-25, got 0
- UR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- PX normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for PX2605.ZCE 2026-04-21, got 0
- PF normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for PF2603.ZCE 2026-02-05, got 0
- PK normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- AO normalized_input: OUTSIDE_SESSION bar 2026-04-16T23:59:00+08:00 -> 2026-04-17T00:00:00+08:00 is outside configured session segments
- SS normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for SS2602.SHF 2026-01-08, got 0
- SP normalized_input: MISSING_TRADE_DATE_BARS SP0.SHFE missing complete open dates: ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05', '2025-09-08', '2025-09-09', '2025-09-10', '2025-09-11', '2025-09-12']
- BC normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for BC2602.INE 2026-01-29, got 0
- CJ normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- AP normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- JD normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for DCE 2026-01-05
- LH normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for DCE 2026-01-05
- SN normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for SN2602.SHF 2026-01-23, got 0
- NI normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for NI2602.SHF 2026-01-29, got 0
- PB normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for PB2602.SHF 2026-01-14, got 0
- SF normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- SM normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for CZCE 2026-01-05
- BZ normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for BZ2603.DCE 2026-02-27, got 0
- PL normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for PL2603.ZCE 2026-02-03, got 0
- PR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for PR2603.ZCE 2026-02-04, got 0
- CY normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for CY2603.ZCE 2026-02-04, got 0
- RS normalized_input: NEGATIVE_ACTIVITY volume/turnover/open_interest must be nonnegative
- RR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for RR2602.DCE 2026-01-16, got 0
- LG normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for DCE 2026-01-05
- OP normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for OP2602.SHF 2026-01-06, got 0
- AD normalized_input: BLOCKED_METADATA BLOCKED_METADATA: expected one contract_daily row for AD2604.SHF 2026-03-27, got 0
- FB normalized_input: INVALID_OHLC source contains invalid OHLC
- EC normalized_input: BLOCKED_METADATA BLOCKED_METADATA: ambiguous exchange calendar row for INE 2026-01-05
