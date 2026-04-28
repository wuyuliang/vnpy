# 04 震荡类策略 / Range Strategies

> 本章目标：在 `01_market_regime/02_range_detection.md` 判定震荡态时，依赖边界 / 均值回归 / 假突破反转提取稳定收益。震荡策略的**胜率通常高、盈亏比低**，与趋势策略天然互补。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_range_boundary_reversal.md](01_range_boundary_reversal.md) | 边界反转 |
| 02 | [02_mean_reversion.md](02_mean_reversion.md) | 均值回归 |
| 03 | [03_false_breakout_reversal.md](03_false_breakout_reversal.md) | 假突破反转 |
| 04 | [04_noise_filtering.md](04_noise_filtering.md) | 噪声过滤（提升震荡策略质量） |

## 推荐阅读顺序

`04 → 02 → 01 → 03`。先学噪声过滤（所有震荡策略共用），再学均值回归（最朴素），然后是边界反转、假突破。

## 上下游关系

- **上游**：`01_market_regime/02_range_detection.md`、`02_price_action/05_failed_breakout.md`
- **下游**：`06_filtering_and_scoring/`、`07_position_and_portfolio/`
- **互补**：`03_trend_strategies/`（趋势 / 震荡二选一，由 regime 决定）
