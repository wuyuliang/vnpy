# 05 状态切换类策略 / Regime Switch Strategies

> 本章目标：识别"震荡 → 趋势"或"趋势 → 震荡"的**过渡阶段**，在过渡点两侧采用不同策略。是 CTA 最具 alpha 的部分，也最难做。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_volatility_compression_expansion.md](01_volatility_compression_expansion.md) | 波动率压缩 → 扩张 |
| 02 | [02_breakout_mode_scoring.md](02_breakout_mode_scoring.md) | 突破模式打分 |
| 03 | [03_regime_switch_signal.md](03_regime_switch_signal.md) | Regime 切换信号 |
| 04 | [04_transition_risk_control.md](04_transition_risk_control.md) | 过渡期风控 |

## 推荐阅读顺序

`01 → 02 → 03 → 04`。先看最可量化的 vol 压缩 / 扩张，再到突破打分，然后到通用 regime switch，最后是过渡期风控。

## 上下游关系

- **上游**：`01_market_regime/04_volatility_regime.md`、`01_market_regime/03_breakout_threshold_detection.md`
- **下游**：本章的信号会被 `03_trend_strategies/` 与 `04_range_strategies/` 的 **gate** 消费
- **互补**：`07_position_and_portfolio/04_drawdown_control.md`（过渡期仓位应保守）
