# cta/strategy/output_price_action_breakout/

## 主要做什么

**历史产出归档**：[cta/strategy/price_action_breakout_*.py](..) 价格行为突破策略的**旧版**逐品种回测结果。新的产物已经统一落到 [cta/report/backtest/](../../report/backtest/)，本目录保留供历史对账。

## 目录结构

```
output_price_action_breakout/
└── per_symbol/<SYM>_<EXCHANGE>/
    ├── trades.csv
    ├── summary.csv
    └── （其他历史产物）
```

当前归档的品种：A0_DCE / AL0_SHFE / AU0_SHFE / BC0_INE / CU0_SHFE / OI0_CZCE / PB0_SHFE / PK0_CZCE / RR0_DCE / SR0_CZCE。

## 注意事项

- **不可写**：本目录是历史产物归档，**禁止**新跑批往这里落盘。新产物一律落 [cta/report/backtest/<run_tag>/](../../report/backtest/)。
- **不可读到生产代码**：本目录数据只供人工 inspect / 离线对比，禁止 import 到 strategy / model / portfolio_logic / runner。
- **删除前检查**：如果磁盘紧张要删，先确认 [cta/report/change_log.md](../../report/change_log.md) 已记录对应实验的结论。
