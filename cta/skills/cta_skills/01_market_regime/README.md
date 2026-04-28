# 01 市场结构识别 / Market Regime Detection

> 本章目标：在接触任何策略之前，先教会 Claude / Codex「看图」——当前行情**是趋势、震荡、压缩、还是扩张**。策略是否开仓，70% 取决于是否识别对 regime。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_trend_detection.md](01_trend_detection.md) | 趋势识别（方向、强度、持续性） |
| 02 | [02_range_detection.md](02_range_detection.md) | 震荡识别（无方向、价差回归） |
| 03 | [03_breakout_threshold_detection.md](03_breakout_threshold_detection.md) | 突破临界（即将突破的前兆） |
| 04 | [04_volatility_regime.md](04_volatility_regime.md) | 波动率状态（高 / 低 / 压缩 / 扩张） |
| 05 | [05_multi_timeframe_alignment.md](05_multi_timeframe_alignment.md) | 多周期状态对齐（HTF/MTF/LTF 共振） |

## 推荐阅读顺序

`04 → 01 → 02 → 03 → 05`。先看波动率（无方向，最基础），再分趋势/震荡（有方向判断），再看突破临界（在震荡转趋势的边缘），最后串起多周期。

## 上下游关系

- **上游**：`00_overview_methodology/`
- **下游**：
  - 趋势策略 (`03_trend_strategies/`) 必须依赖 `01_trend_detection`
  - 震荡策略 (`04_range_strategies/`) 必须依赖 `02_range_detection`
  - 状态切换 (`05_regime_switch_strategies/`) 全部依赖本章
- **互补**：`06_filtering_and_scoring/03_context_score.md`（把本章输出聚合成单一分数）
