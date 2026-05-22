# cta/skills/filtering_scoring/

## 主要做什么

**候选事件过滤与评分**：候选信号生成之后、portfolio_logic 之前的一层"质量评判"。每个文件一个独立打分维度，组合起来形成可叠加的评分体系。

## 关键文件

| 文件 | 维度 |
|---|---|
| [setup_quality.py](setup_quality.py) | setup 自身的形态质量（K 线结构、整理紧度、关键位接近度） |
| [breakout_quality.py](breakout_quality.py) | 突破质量（突破幅度 / 实体占比 / 收盘位置 / 放量倍数） |
| [context_score.py](context_score.py) | 上下文打分（大周期方向 / 波动率环境 / 趋势强度） |
| [risk_reward_score.py](risk_reward_score.py) | 风险收益比（预期 MFE/MAE × 止损宽度 → 综合 R/R） |
| [ml_opportunity_model.py](ml_opportunity_model.py) | ML 打分入口：包装训练好的 trade_filter 模型供 skill 层调用 |

## 详细过程

```
[候选] symbol × datetime × signal_type
    │
    ▼
[skill 评分链]（按需组合）
    setup_quality.evaluate(...)        → score_setup ∈ [0, 1]
    breakout_quality.evaluate(...)      → score_breakout ∈ [0, 1]
    context_score.evaluate(...)         → score_context ∈ [0, 1]
    risk_reward_score.evaluate(...)     → score_rr ∈ [0, 1]
    ml_opportunity_model.score(...)     → trade_filter_prob ∈ [0, 1]
    │
    ▼
[加权综合 → 进入 portfolio_logic / 训练 label]
```

## 注意事项

- **打分维度可解释**：每个文件的 `evaluate(...)` 输出应当是 [0, 1] 范围、含义明确的标量；如果一个分维度需要超 200 行实现，应该再拆。
- **不允许直接发单 / 修改仓位**：本目录只产 score，**不**改 candidate 状态或 portfolio_state。下游决策权在 [cta/portfolio_logic/](../../portfolio_logic/)。
- **ml_opportunity_model 不训练**：本文件只是 inference 包装，模型权重由 [cta/model/training/trade_filter_model.py](../../model/training/trade_filter_model.py) 训练后落盘。skill 层只 `predict`，不 `fit`。
- **shift / 穿越**：评分用到的特征都来自 [cta/data/feature/](../../data/feature/)，已经 shift；不要在 skill 层再调用未 shift 的原始 bar。
- **测试**：`pytest cta/skills/filtering_scoring/tests/ -v`。每个评分函数至少覆盖：极端值、缺失值、空数据三个边界。
