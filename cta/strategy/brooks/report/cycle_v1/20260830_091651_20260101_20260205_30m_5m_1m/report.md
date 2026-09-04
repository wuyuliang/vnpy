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
- cache_hit: False
- cache_key: 2bfce4d0b24a8e3eb436bf98d7624fe790a4da0f8d7fcb2858135c6ebaf12782
- contract_date_pairs: 24
- generated_contract_rows: 1
- generated_daily_rows: 24
- generated_fee_rows: 24
- source_queries: 99
- source_audit_sha256: f99cdb6d9f31631f555d8d60a932f5563ed885a3f90fd9b65d0709331117b65e
- manifest_sha256: e1e698ef5573efedc4db133053062f1afa6931e9e33d8a3648b822e76ba0b50f

## Funnel
- discovered_symbols: 68
- requested_symbols: 1
- loaded_symbols: 1
- eligible_symbol_days: 11
- cycle_snapshots: 6105
- candidates: 8
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
- BREAKOUT_GATE_FAILED: 13873
- DIRECTIONAL_VIEW_UNAVAILABLE: 392
- CYCLE_CONFIDENCE_BLOCKED: 137
- INCOMPLETE_TIMEFRAME_CONTEXT: 29
- PLAN_GEOMETRY_NOT_TRADEABLE: 21
- LARGE_CYCLE_UNAVAILABLE: 8
- SETUP_CYCLE_NOT_ALLOWED: 4
- SIGNAL_BAR_TOO_LARGE: 3
- STRONG_OPPOSITE_SIGNAL_BAR: 2

## Candidate Rejections
- 4efdea0dd5acff7c3fb0eb32 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 490825f73dedd92f6b41f855 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_BROAD_CHANNEL
- 03623fcbc655e57d1b315209 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 19984ba63c49a2e948e7a696 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- f3159fa8eda414a993b44282 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 1853c096f5b71298b55e51e9 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 1fd8c008c0fcfea61cafc0fd LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 6c3c64c73b6531a6efb3c6b8 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
