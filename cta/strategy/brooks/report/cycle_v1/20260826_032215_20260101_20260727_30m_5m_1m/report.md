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
- cache_key: 478501dfbaec534cd277bfbbb9602ad761ae3ab027bbb1d9835e4e57e83b20b7
- contract_date_pairs: 9360
- generated_contract_rows: 280
- generated_daily_rows: 9082
- generated_fee_rows: 9082
- source_queries: 907
- source_audit_sha256: 1c711e237fbe258016ecc08a21bdf6cb71132e03110cb47f07959cdb52e17dd7
- manifest_sha256: 9a50ece9a1d2d8819c945568bd541081c28b4a848f76b64a17e566fa25be8423

## Funnel
- discovered_symbols: 68
- requested_symbols: 68
- loaded_symbols: 17
- eligible_symbol_days: 698
- cycle_snapshots: 182790
- candidates: 0
- eligible_plans: 0
- orders: 0
- fills: 0
- round_trips: 0

## Metadata Gaps
- RB normalized_input: BLOCKED_METADATA BLOCKED_METADATA: daily spec known late for RB2605.SHF 2026-01-12
- HC normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for HC2605.SHF->HC2610.SHF 2026-04-03
- I normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for I2605.DCE->I2609.DCE 2026-04-09
- JM normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for JM2605.DCE->JM2609.DCE 2026-04-17
- J normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for J2605.DCE->J2609.DCE 2026-04-23
- M normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for M2605.DCE->M2609.DCE 2026-04-09
- P normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for P2605.DCE->P2609.DCE 2026-04-08
- OI normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for OI2605.ZCE->OI2609.ZCE 2026-04-15
- MA normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for MA2605.ZCE->MA2609.ZCE 2026-04-17
- TA normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for TA2605.ZCE->TA2609.ZCE 2026-04-17
- EG normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for EG2605.DCE->EG2609.DCE 2026-04-21
- PP normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for PP2605.DCE->PP2609.DCE 2026-04-16
- L normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for L2605.DCE->L2609.DCE 2026-04-15
- V normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for V2605.DCE->V2609.DCE 2026-04-15
- CU normalized_input: BLOCKED_METADATA Must have equal len keys and value when setting with an ndarray
- AL normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for AL2602.SHF->AL2603.SHF 2026-01-12
- ZN normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for ZN2606.SHF->ZN2607.SHF 2026-05-25
- AG normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for AG2604.SHF->AG2606.SHF 2026-03-09
- FU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for FU2603.SHF->FU2605.SHF 2026-02-06
- BU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for BU2602.SHF->BU2603.SHF 2026-01-07
- SC normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for SC2603.INE->SC2604.INE 2026-02-09
- LU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for LU2603.INE->LU2604.INE 2026-01-23
- RU normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for RU2605.SHF->RU2609.SHF 2026-04-09
- BR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for BR2602.SHF->BR2603.SHF 2026-01-13
- NR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for NR2603.INE->NR2604.INE 2026-02-04
- CF normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for CF2605.ZCE->CF2609.ZCE 2026-04-09
- SR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for SR2605.ZCE->SR2609.ZCE 2026-04-03
- C normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for C2603.DCE->C2605.DCE 2026-02-10
- CS normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for CS2603.DCE->CS2605.DCE 2026-02-13
- A normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for A2605.DCE->A2607.DCE 2026-04-15
- B normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for B2605.DCE->B2607.DCE 2026-04-17
- RM normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for RM2605.ZCE->RM2609.ZCE 2026-04-15
- PG normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for PG2602.DCE->PG2603.DCE 2026-01-14
- EB normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for EB2602.DCE->EB2603.DCE 2026-01-22
- FG normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for FG2605.ZCE->FG2609.ZCE 2026-04-17
- SA normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for SA2605.ZCE->SA2609.ZCE 2026-04-16
- SH normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for SH2603.ZCE->SH2605.ZCE 2026-02-25
- PX normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for PX2603.ZCE->PX2605.ZCE 2026-02-02
- PF normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for PF2602.ZCE->PF2603.ZCE 2026-01-12
- AO normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for AO2605.SHF->AO2609.SHF 2026-04-20
- SS normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for SS2604.SHF->SS2605.SHF 2026-03-09
- SP normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for SP2605.SHF->SP2609.SHF 2026-04-20
- BC normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for BC2603.INE->BC2604.INE 2026-03-02
- NI normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for NI2607.SHF->NI2609.SHF 2026-06-29
- BZ normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for BZ2603.DCE->BZ2604.DCE 2026-02-27
- PL normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for PL2603.ZCE->PL2604.ZCE 2026-02-03
- PR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for PR2603.ZCE->PR2605.ZCE 2026-02-04
- CY normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for CY2603.ZCE->CY2605.ZCE 2026-02-04
- RR normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for RR2602.DCE->RR2603.DCE 2026-01-16
- OP normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for OP2602.SHF->OP2603.SHF 2026-01-06
- AD normalized_input: BLOCKED_METADATA BLOCKED_METADATA: roll pre-settlement reference known late for AD2603.SHF->AD2604.SHF 2026-02-09
