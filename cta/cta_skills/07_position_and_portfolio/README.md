# 07 仓位管理与组合风控 / Position & Portfolio Risk

> 本章目标：从**单笔仓位**到**组合仓位**的全栈风控方法。即使信号再好，仓位错了依然爆仓。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_single_trade_risk.md](01_single_trade_risk.md) | 单笔风险（固定 % 权益） |
| 02 | [02_vol_targeting.md](02_vol_targeting.md) | 波动率目标（按 ATR 标准化） |
| 03 | [03_sector_exposure_control.md](03_sector_exposure_control.md) | 板块 / 大类敞口控制 |
| 04 | [04_drawdown_control.md](04_drawdown_control.md) | 回撤控制（组合降仓） |
| 05 | [05_portfolio_allocation.md](05_portfolio_allocation.md) | 组合配置（多策略 / 多品种） |

## 推荐阅读顺序

`01 → 02 → 04 → 03 → 05`。单笔 → vol targeting → 回撤 → 板块敞口 → 最终组合。

## 上下游关系

- **上游**：`06_filtering_and_scoring/`（score → size multiplier）
- **下游**：`10_live_ops/01_signal_to_order.md`（把 size 翻译为手数）
- **互补**：`05_regime_switch_strategies/04_transition_risk_control.md`（regime 层面 × 仓位层面）
