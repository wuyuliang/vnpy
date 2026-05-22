# cta/skills/data_backtest/tests/

## 主要做什么

[cta/skills/data_backtest/](..) 回测引擎与数据规则的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_event_driven_backtest.py](test_event_driven_backtest.py) | 事件驱动回测引擎：fill / 风控 / PnL 全链路 |
| [test_continuous_contract.py](test_continuous_contract.py) | back-adjust / ratio-adjust 价格调整正确性 |
| [test_rollover_rules.py](test_rollover_rules.py) | 换月规则：什么时候切到次主连 |
| [test_transaction_cost.py](test_transaction_cost.py) | 手续费 / 滑点 / 印花税模型，按品种从 [cta/config/futures_meta.py](../../../config/futures_meta.py) 读 |
| [test_trade_evaluation.py](test_trade_evaluation.py) | 单笔 gross / net PnL、MFE / MAE、ATR 归一化 |

## 注意事项

- **回测口径漂移会污染所有下游**：本目录测试挂了意味着 strategy / OOT / sim / live 全部对账不上。**禁止 skip**。
- **连续合约价格 vs roll_cost 双重计算**：默认连续主连已含展期价差，`use_roll_cost=True` 会双扣；测试要覆盖这条边界。
- **跑测试**：`pytest cta/skills/data_backtest/tests/ -v`。
