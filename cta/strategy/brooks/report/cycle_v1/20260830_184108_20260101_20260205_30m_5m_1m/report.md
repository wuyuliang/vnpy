# Brooks Cycle V1 Report

Official status: COMPLETE

## Reproduction Command
- working_directory: /Users/wuyuliang/code/vnpy

```bash
cd /Users/wuyuliang/code/vnpy && python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --symbols AG --start 2026-01-01 --end 2026-02-05 --long-tf 30min --medium-tf 5min --short-tf 1min --initial-equity 200000 --download-minute-data
```

## Trading Summary
- trade_count: 0
- total_net_pnl: 0.00
- total_return: 0.00%
- annualized_return: 0.00%
- win_rate: N/A
- max_drawdown: 0.00%
- profit_factor: N/A
- expectancy_R: N/A
- fees: 0.00
- slippage: 0.00
- final_equity: 200,000.00

## Execution Metadata Update
- status: READY
- cache_hit: True
- cache_key: 920692ac7c0f0df5adeffe1268c6a31ac1bc4abcb0fd13476c9a437595d2fe4d
- contract_date_pairs: 109
- generated_contract_rows: 4
- generated_daily_rows: 109
- generated_fee_rows: 109
- source_queries: 426
- source_audit_sha256: 22769676fe6e8daecae64c51f956d54562e67d095dcba40beb69736d2839f504
- manifest_sha256: 801a4ee3669143657c66bb8256aa6425a8a8ca07b737028169d26b815d6ae5e4

## Funnel
- discovered_symbols: 68
- requested_symbols: 1
- loaded_symbols: 1
- eligible_symbol_days: 11
- cycle_snapshots: 6105
- candidates: 16
- eligible_plans: 0
- orders: 0
- fills: 0
- round_trips: 0

## Official Performance
- trade_count: 0
- total_net_pnl: 0.0
- total_return: 0.0
- final_equity: 200000.0
- win_rate: None
- average_win: 0.0
- average_loss: 0.0
- profit_factor: None
- expectancy_R: None
- annualized_return: 0.0
- sharpe: None
- sortino: None
- max_drawdown: 0.0
- calmar: None
- average_mfe_R: None
- average_mae_R: None
- average_holding_bars: None
- turnover: 0.0
- fees: 0.0
- slippage: 0.0
- cost_to_gross_profit: None
- max_margin_utilization: 0.0
- max_open_risk: 0.0
- tail_daily_return: 0.0
- zero_return_days: 24

## Rejection Summary
- BREAKOUT_GATE_FAILED: 14323
- CYCLE_CONFIDENCE_BLOCKED: 79
- LARGE_DIRECTION_CONFLICT: 35
- PLAN_GEOMETRY_NOT_TRADEABLE: 22
- TRADE_MODE_NO_TRADE: 17
- LARGE_CYCLE_UNAVAILABLE: 10
- SIGNAL_BAR_TOO_LARGE: 5
- SETUP_CYCLE_NOT_ALLOWED: 4
- STRONG_OPPOSITE_SIGNAL_BAR: 3
- ONE_LOT_EXCEEDS_RISK_BUDGET: 3
- MAX_MARGIN_UTILIZATION: 1

## Candidate Rejections
- 7eba59b2afc0d724ec0a71e4 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BULL_TIGHT_CHANNEL
- 6dc93b3fa396b75709c5b126 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BULL_TIGHT_CHANNEL
- a9c44e595cee492bf15765e5 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BULL_TIGHT_CHANNEL
- e96b531024833496fd615933 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BULL_BROAD_CHANNEL
- bbbe71f963287b5844f445f9 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BULL_TIGHT_CHANNEL
- 57bb21b2888b0e1a0dcbea47 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BEAR_TIGHT_CHANNEL
- 238acad4b24e3bedd38696e6 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BEAR_TIGHT_CHANNEL
- 9442642177b26a91d29d3828 ONE_LOT_EXCEEDS_RISK_BUDGET risk_budget=3200.0 loss_per_lot=4185.8316 entry=28662.0;stop=28388.0;contract_multiplier=15.0
- 0d5a9fa32f8ba276865cd9b2 MAX_MARGIN_UTILIZATION risk_budget=3200.0 loss_per_lot=1635.8316 quantity=1;incremental_open_risk=1635.8316;incremental_margin=81789.3
- 5f1385d5c217a9bffa7c1888 ONE_LOT_EXCEEDS_RISK_BUDGET risk_budget=3200.0 loss_per_lot=3720.8316 entry=28837.0;stop=28594.0;contract_multiplier=15.0
- 9549a8b8c4aac7fd8a039ccb CYCLE_CONFIDENCE_BLOCKED large_cycle=BULL_TIGHT_CHANNEL;medium_cycle=BULL_TIGHT_CHANNEL
- 309b006e295793d9e44fc22a CYCLE_CONFIDENCE_BLOCKED large_cycle=BULL_TIGHT_CHANNEL;medium_cycle=BULL_TIGHT_CHANNEL
- 287b17f9c685019bd3d35f14 ONE_LOT_EXCEEDS_RISK_BUDGET risk_budget=3200.0 loss_per_lot=8130.8316 entry=29840.0;stop=29303.0;contract_multiplier=15.0
- c0381040630a232a158a5e67 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BULL_TIGHT_CHANNEL
- 15bf0a1dd0eec1fef7b024b0 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BULL_TIGHT_CHANNEL
- afc35ec99e94836de0fa2be6 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BEAR_TIGHT_CHANNEL
## Candidate Charts
- candidate_count: 16
- [Candidate chart guide](CANDIDATE_CHARTS.md)
- [Candidate chart index](candidate_charts/index.csv)
