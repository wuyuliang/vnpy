# 03 趋势类策略 / Trend Strategies

> 本章目标：把 `01_market_regime/01_trend_detection.md` 的识别结果，转成**5 种经典趋势类策略骨架**。新策略都可以从其中一种派生。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_donchian_breakout.md](01_donchian_breakout.md) | 唐奇安突破 |
| 02 | [02_atr_breakout.md](02_atr_breakout.md) | ATR 通道突破 |
| 03 | [03_ma_trend_following.md](03_ma_trend_following.md) | 均线趋势跟随 |
| 04 | [04_cross_sectional_momentum.md](04_cross_sectional_momentum.md) | 横截面动量（多品种） |
| 05 | [05_trend_hold_and_trailing.md](05_trend_hold_and_trailing.md) | 趋势持仓与移动止损 |

## 推荐阅读顺序

`03 → 01 → 02 → 05 → 04`。先学最容易理解的 MA，再学两种突破变体，再学持仓管理，最后是多品种动量。

## 上下游关系

- **上游**：`01_market_regime/`、`02_price_action/`
- **下游**：`06_filtering_and_scoring/`（过滤）、`07_position_and_portfolio/`（仓位）、`09_ml_augmentation/`（ML 增强）
- **互补**：`04_range_strategies/`（震荡时关闭本章，换震荡策略）
