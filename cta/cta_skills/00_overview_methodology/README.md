# 00 CTA 总纲与研究方法论 / Overview & Methodology

> 本章目标：在动手写任何代码 / 跑任何回测之前，先明确**为什么做 CTA、边界在哪、如何评估、如何迭代**。不写代码，但是后续所有 skill 的"宪法"。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_objectives.md](01_objectives.md) | 目标设定：追求什么 Sharpe / 什么回撤 / 什么容量 |
| 02 | [02_research_boundary.md](02_research_boundary.md) | 研究边界：品种范围 / 周期范围 / 不做什么 |
| 03 | [03_backtest_principles.md](03_backtest_principles.md) | 回测原则：避免未来函数、成本、样本划分 |
| 04 | [04_live_trading_principles.md](04_live_trading_principles.md) | 实盘原则：dry-run、灰度、对账、熔断 |
| 05 | [05_iteration_workflow.md](05_iteration_workflow.md) | 迭代流程：想法 → 小实验 → 回测 → 实盘的节奏 |

## 推荐阅读顺序

`01 → 02 → 03 → 05 → 04`。先定目标与边界，再看回测原则与迭代节奏，最后补实盘原则（实盘比回测晚 1-2 季度再考虑）。

## 上下游关系

- **上游**：无（本章是根）
- **下游**：所有 skill。特别是：
  - `08_data_and_backtest_infra/`（回测原则的工程化落地）
  - `10_live_ops/`（实盘原则的工程化落地）
- **与 `CLAUDE.md` / `cta/README.md` 的关系**：本章是对那两个文件中"开发规则 / 回测规范 / 变更管理"的方法论展开，不重复写，只深化。
