# 02 价格行为与形态理解 / Price Action (Al Brooks)

> 本章目标：把 Al Brooks 的**价格行为语言**翻译为可计算的特征与交易规则。技术核心已在 `cta/feature/price_action.py` 落地（180+ 个 `pa_*` 特征），本章教 Claude / Codex 怎么**组合使用**这些原语。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_tight_range_breakout.md](01_tight_range_breakout.md) | 紧缩区间突破 |
| 02 | [02_bull_flag_bear_flag.md](02_bull_flag_bear_flag.md) | 多旗 / 空旗形态 |
| 03 | [03_breakout_pullback_continuation.md](03_breakout_pullback_continuation.md) | 突破后回踩延续 |
| 04 | [04_high1_high2_low1_low2.md](04_high1_high2_low1_low2.md) | H1/H2/L1/L2 结构 |
| 05 | [05_failed_breakout.md](05_failed_breakout.md) | 假突破反转 |
| 06 | [06_micro_channel_trend_channel.md](06_micro_channel_trend_channel.md) | Micro channel / trend channel |

## 推荐阅读顺序

`04 → 01 → 02 → 03 → 06 → 05`。先学 H1/H2/L1/L2（趋势结构基础），然后是 tight range（压缩后的突破机会），再是 flag / pullback 延续，通道，最后失败反转。

## 与仓库资产的关系

| 资产 | 可被本章引用 |
|------|--------------|
| `cta/feature/price_action.py` | 基础 K 线分类、信号 bar、趋势结构、break bar、tight range、inside bar 等 pa_* 原语 |
| `cta/feature/price_action_advanced.py` | 高级形态（flag、pullback、channel） |
| `cta/feature/price_action_context.py` | 上下文打分（leg、swing、strength） |
| `cta/feature/FEATURES.md` | 已有特征的完整列表 |

## 上下游关系

- **上游**：`01_market_regime/`（形态判断前先确定 regime）
- **下游**：
  - 趋势策略 (`03_trend_strategies/`) 会消费 flag / pullback
  - 震荡策略 (`04_range_strategies/`) 会消费 failed_breakout
  - 状态切换 (`05_regime_switch_strategies/`) 会消费 tight_range_breakout
- **互补**：`06_filtering_and_scoring/01_setup_quality_score.md`（把形态质量打分成 setup_quality）
