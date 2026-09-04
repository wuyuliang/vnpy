# Brooks Cycle V1 Report

Official status: COMPLETE

## Reproduction Command
- working_directory: /Users/wuyuliang/code/vnpy

```bash
cd /Users/wuyuliang/code/vnpy && python3 -m cta.strategy.brooks.cycle_v1.backtest.runner --symbols LC --start 2026-01-01 --end 2026-02-05 --long-tf 30min --medium-tf 5min --short-tf 1min --initial-equity 200000 --download-minute-data --top-n 2 --include-ema-eligible
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
- cache_key: 2ae2f4a867c94374dcb83bad7d6477f83ce35fccee3f9b3e67c1e205b2af65cc
- contract_date_pairs: 1659
- generated_contract_rows: 98
- generated_daily_rows: 1659
- generated_fee_rows: 1659
- source_queries: 199
- source_audit_sha256: 548614b5bb3626a15d7510b7b400568e4dcd18cec9a84891bc8d00349f600057
- manifest_sha256: aa604d6fc0a8b86583ee5d69c27e0e166eb1950ed9e60f3fd91da5d38f540691

## Funnel
- discovered_symbols: 68
- requested_symbols: 68
- loaded_symbols: 68
- eligible_symbol_days: 442
- cycle_snapshots: 151440
- candidates: 38
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
- BREAKOUT_GATE_FAILED: 355501
- DIRECTIONAL_VIEW_UNAVAILABLE: 20870
- FAILED_BREAKOUT_GATE_FAILED: 5000
- CYCLE_CONFIDENCE_BLOCKED: 2999
- INCOMPLETE_TIMEFRAME_CONTEXT: 2320
- PLAN_GEOMETRY_NOT_TRADEABLE: 648
- STRONG_OPPOSITE_SIGNAL_BAR: 109
- SETUP_CYCLE_NOT_ALLOWED: 105
- LARGE_DIRECTION_CONFLICT: 47
- LARGE_CYCLE_UNAVAILABLE: 38
- SIGNAL_BAR_TOO_LARGE: 28
- TRADE_MODE_NO_TRADE: 16
- TARGET_ROUNDING_REMOVED_REWARD: 1
- NO_CAUSAL_DIRECTIONAL_OBSTACLE: 1

## Candidate Rejections
- 4efdea0dd5acff7c3fb0eb32 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 9bd466a23d4d162f64986e94 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 3dac3069bda884c490c2c6e1 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- e287c8419041a3b27baf7440 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 490825f73dedd92f6b41f855 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_BROAD_CHANNEL
- b5e285b2c906dea34eeab4bf LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 680af443d284fc45d57f6e45 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 03623fcbc655e57d1b315209 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 19984ba63c49a2e948e7a696 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 221a537d07d6977bcd52e534 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- f3159fa8eda414a993b44282 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 3b68a8a675e0b3a8b271f54b LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 42bac19444ba560cffb5876d LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- f1e38b64b560760e033e0341 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- f113dbd1d26d63f3a92db853 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- df0aeb4f243db049e89c2fb4 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- fc8d5dca1c3cf1f081ea52de LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 55b1b4e1a89081b4214ca354 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_BROAD_CHANNEL
- 32f53257678ca7f7b8a06e02 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 4306a2dd6f3ac33335e23148 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 4a4127cc647ca2dae17764c6 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_BROAD_CHANNEL
- 6fffcd530eb721425b0aa8dc LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- d5778b123678114c0f046fe8 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 5f3e1cda60e2b3227d284294 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 1853c096f5b71298b55e51e9 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 1fd8c008c0fcfea61cafc0fd LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 2060adca8e2702888778df2d LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 22938b5d053add16619f33d7 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 4b23c68974a73597e2f0ce8e LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 2479cac32d9f4d7d59d2be35 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BULL_TIGHT_CHANNEL
- 2b1203fcce7afcd2105150e9 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 1b650cfe65242771c4acf7d3 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- a1f5a51ddc5e3c57f69d64f9 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 43cf90d067685430c7d7ee79 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 6c3c64c73b6531a6efb3c6b8 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 8fa55e64f732b30d5f1e53ff LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- 9e0106eafea71e6dd743e904 LARGE_CYCLE_UNAVAILABLE large_cycle=UNAVAILABLE;medium_cycle=BEAR_TIGHT_CHANNEL
- ecf142ce14b6bd56e9b543c2 LARGE_CYCLE_UNAVAILABLE large_cycle=TRANSITION;medium_cycle=BEAR_TIGHT_CHANNEL
