# cta/skills/data_backtest/

## 主要做什么

**回测基础设施模块**：事件驱动回测引擎 + 连续合约规则 + 换月成本 + 交易成本模型 + 单笔评估。被 [cta/strategy/](../../strategy/) 各策略和 [cta/model/](../../model/) OOT 评估直接调用。

## 关键文件

| 文件 | 作用 |
|---|---|
| [event_driven_backtest.py](event_driven_backtest.py) | **事件驱动回测引擎**：按 bar 推进、fill 模拟、风控集成、PnL 统计 |
| [continuous_contract.py](continuous_contract.py) | 连续合约价格调整：back-adjust / ratio-adjust，处理主连换月 |
| [rollover_rules.py](rollover_rules.py) | 换月规则：什么时候切到次主连、显式 rollover_date |
| [transaction_cost.py](transaction_cost.py) | 手续费 / 滑点 / 印花税建模（按品种从 [cta/config/futures_meta.py](../../config/futures_meta.py) 取） |
| [trade_evaluation.py](trade_evaluation.py) | 单笔成交评估：gross / net PnL、MFE / MAE、持仓时长、ATR 归一化 |

## 详细过程（典型调用链）

```
[策略侧] cta/strategy/skill_tight_range_backtest.py
    └─ from cta.skills.data_backtest.event_driven_backtest import run_backtest
       run_backtest(
           bars=...,           # 单 symbol × interval 的 bar 流
           strategy=...,       # 策略实例
           cost_model=transaction_cost.default_for_symbol(...),
           rollover=rollover_rules.continuous(...),
       )

[OOT 侧] cta/model/oot/oot_intrabar.py
    └─ from cta.skills.data_backtest.trade_evaluation import evaluate_trade
       evaluate_trade(entry, exit, fills, cost_model)
```

## 注意事项

- **连续合约价格 vs 单合约换月**：默认用的是 back-adjusted 连续主连，PnL 已隐含展期价差。不要再叠加 `use_roll_cost=True`，会**双重计算**（见 [cta/config/model_oot_eval_config.py](../../config/model_oot_eval_config.py) 注释）。
- **成本必须从 config 取**：[transaction_cost.py](transaction_cost.py) 默认从 `cta/config/futures_meta.py` 读手续费 / 滑点；测试里手工注入也要从这个 dict 派生，不要硬编码。
- **fill 模拟的口径**：默认 next-bar-open fill，可配 limit / market；改 fill 逻辑要同步更新 [cta/sim/](../../sim/) 与 [cta/live/](../../live/)，否则会产生研究/仿真/实盘漂移。
- **跨周期/跨品种不在本目录处理**：组合层在 [cta/portfolio_logic/](../../portfolio_logic/)；本目录只负责单标的回测。
- **测试**：跑 `pytest cta/skills/data_backtest/tests/ -v`。
