# cta/backtest/

## 主要做什么

历史保留的回测入口占位目录。**当前为空**——回测入口已分散到下面两处，按需直接使用即可，不要再往本目录新建脚本：

| 实际入口 | 位置 | 用途 |
|---|---|---|
| 事件驱动回测引擎 | [cta/skills/data_backtest/event_driven_backtest.py](../skills/data_backtest/event_driven_backtest.py) | 单策略历史回放，含 fill / slippage / margin 完整模拟 |
| 策略级 backtest CLI | [cta/strategy/skill_tight_range_backtest.py](../strategy/skill_tight_range_backtest.py)、[cta/strategy/baseline_backtest_cli.py](../strategy/baseline_backtest_cli.py)、[cta/strategy/backtest_price_action_breakout.py](../strategy/backtest_price_action_breakout.py) | 直接调用上面的 event_driven_backtest，按策略组织参数与产出 |
| 模型 pipeline + OOT | [cta/model/model_pipeline.py](../model/model_pipeline.py) → [cta/model/oot/pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | walk-forward 训练 + OOT real-execution（带 portfolio_logic 运行时） |

## 详细过程

**没有内容**。如果未来要新增脚本，请先评估是否应该放到上述三处之一；目录保留只是为了不打断历史导入路径。

## 注意事项

- **禁止**在本目录新建文件——会让"回测入口在哪"的歧义重新出现。
- 如果发现旧代码或文档仍指向 `cta/backtest/xxx.py`，应当一并改到上面表里对应的真实路径。
- 真实的回测约定（手续费、滑点、成交规则等）见 [cta/skills/data_backtest/](../skills/data_backtest/) 与 [cta/config/](../config/)。
