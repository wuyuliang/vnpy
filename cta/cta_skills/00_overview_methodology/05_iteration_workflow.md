# 迭代流程 / Iteration Workflow

> 归属章节：`00_overview_methodology/` · 前置技能：`01-04` · 关联：`cta/report/change_log.md`

## 1. Skill 定义

从"一个想法"到"实盘获利"的**标准化流程**：hypothesize → micro-experiment → backtest → paper-trade → live。每一步都有交付物、有 review、有退出条件。避免边想边写、东跳西跳。

## 2. 解决什么问题

- 痛点 1：每个想法花 1 周重复写数据管道。
- 痛点 2：没做 micro-experiment 直接上完整回测，3 天后发现想法本来就行不通。
- 痛点 3：想法多，记录少；半年后忘了哪些试过。
- 增量价值：把研究节奏标准化，能并行跑多个想法且不混乱。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：无
- **不适用场景**：紧急 hotfix（实盘出事时不走本流程）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 单个想法耗时 | 从 issue 创建到结论 | — | 新手 ≤ 2 周 / 熟练 ≤ 5 天 | TODO |
| 想法通过率 | 通过 micro-experiment 的想法 / 总想法 | — | ≤ 30% 为正常 | TODO |
| 策略交付周期 | 从 micro-experiment 通过到实盘稳态 | — | ≤ 2 个月 | TODO |

## 5. 常见策略映射

本 skill 不产生策略，但定义**每个策略必须走的 5 阶段**：

### 阶段 1：hypothesize（假设）
- **交付物**：一份 < 1 页 md，写清：
  - 假设（"若 A 则 B"）
  - 预期效应大小（年化提升 % / Sharpe 提升）
  - 关键信号定义 / 关键指标
  - 已知反例
- **产物位置**：`cta/cta_skills/0X_.../{name}.md` 或临时 `cta/report/ideas/{YYYYMMDD}_{name}.md`

### 阶段 2：micro-experiment（最小实验）
- **目的**：用 1-2 天、1-2 品种、最简指标，验证"信号本身和收益有正相关"
- **方式**：直接算 IC、分组收益、分层测试（不跑完整回测）
- **交付物**：一张图 + 一句结论
- **通过条件**：与预期一致 → 进阶段 3；否则丢弃或回 1

### 阶段 3：backtest（完整回测）
- **遵循** `03_backtest_principles.md` 的全部铁律
- **交付物**：`cta/strategy/{name}/` 骨架 + 完整回测报告 + 达标判定
- **通过条件**：达到 `01_objectives.md` 的门槛 A 或 B

### 阶段 4：paper-trade（纸上交易 / dry-run）
- 按 `04_live_trading_principles.md` 的阶段 1-2 执行
- **通过条件**：信号一致率 ≥ 99% 且持续 ≥ 5 交易日

### 阶段 5：live（实盘）
- 按 `04_live_trading_principles.md` 的阶段 3-6 灰度上线

### 每阶段必须做的记录
- 在 `cta/report/change_log.md` 追加一条
- 标明阶段、分支、结论、下一步

## 6. 代码模块设计

不新增代码，但约定**想法目录结构**（轻量）：

```text
cta/report/
├── change_log.md                          # 总账
└── ideas/
    └── {YYYYMMDD}_{short_name}/
        ├── hypothesis.md                  # 阶段 1
        ├── micro_experiment.ipynb         # 阶段 2
        └── decision.md                    # 通过/丢弃的原因
```

```python
# 想法追踪可用的 helper（未来可选实现）
from dataclasses import dataclass
from typing import Literal

@dataclass
class IdeaRecord:
    id: str                                 # 20260420_tight_range_filter
    name: str
    stage: Literal["hypothesize","micro","backtest","paper","live","dropped"]
    created_at: str
    owner: str
    hypothesis_doc: str                     # path
    decision: str | None

def list_open_ideas() -> list[IdeaRecord]:
    """扫描 cta/report/ideas/ 返回进行中的想法。"""
    ...
```

## 7. 回测评估重点

评估的是"流程"本身：

- **必看指标（6）**：想法数量 / 通过率 / 阶段平均耗时 / 回测达标率 / 实盘达标率 / 回滚次数
- **分层评估**：按想法类型（趋势 / 震荡 / ML / 组合）、按 owner
- **稳健性检验**：
  1. 季度 review：流程是否有缩短？阻塞在哪一阶段？
  2. 被否的想法复盘：若 1 年后行情变化，是否需要重开？
  3. 上线后 3 个月：实盘表现 vs 预期效应

## 8. 常见错误

- 跳过 micro-experiment 直接回测
- 不记录，半年后重复造同一个想法
- 想法阶段不写"反例"，导致后期才发现策略假设不成立
- 回滚后不 post-mortem，下次重蹈
- 纸上交易时间过短（< 3 天）
- 阶段之间 commit message 混乱，无法 bisect

## 9. 迭代方向

- v1：手工在 change_log.md 记录
- v2：`cta/report/ideas/` 目录结构 + helper 脚本
- v3：看板（e.g. Linear / Notion 集成）
- v4：阶段门自动化（如 micro-experiment 的 IC 计算脚本化）

## 10. 与其他 Skills 的关系

- **依赖**：`01_objectives.md`、`03_backtest_principles.md`、`04_live_trading_principles.md`
- **被依赖**：所有策略 skill（所有新策略都走本流程）
- **互补**：`10_live_ops/05_strategy_iteration_loop.md`（实盘阶段的迭代循环）、`09_ml_augmentation/05_walk_forward_validation.md`（ML 模型的持续迭代）
