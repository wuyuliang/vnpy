# 06 策略过滤与机会评分 / Filtering & Scoring

> 本章目标：把"入场信号 → 是否真的开仓 / 开多大"解耦成**独立打分层**。所有策略的上游信号通过一套统一的 Filter / Score 才进入执行。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_setup_quality_score.md](01_setup_quality_score.md) | Setup 质量打分（形态质量） |
| 02 | [02_breakout_quality_score.md](02_breakout_quality_score.md) | 突破质量打分 |
| 03 | [03_context_score.md](03_context_score.md) | Context 打分（多周期 / regime 对齐） |
| 04 | [04_risk_reward_score.md](04_risk_reward_score.md) | 风险收益比打分 |
| 05 | [05_ml_opportunity_model.md](05_ml_opportunity_model.md) | ML 机会模型（综合） |

## 推荐阅读顺序

`01 → 02 → 03 → 04 → 05`。单点打分（setup / breakout）→ 上下文打分 → RR 打分 → 最终 ML 整合。

## 上下游关系

- **上游**：`01_market_regime/`、`02_price_action/`、策略识别 skills
- **下游**：`07_position_and_portfolio/`（score 映射到仓位）、`09_ml_augmentation/`（ML 实现）
