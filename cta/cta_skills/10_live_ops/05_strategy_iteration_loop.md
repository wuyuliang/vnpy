# 策略迭代闭环 / Strategy Iteration Loop

> 归属章节：`10_live_ops/` · 前置：`04_daily_review.md` · 关联：`cta/report/change_log.md`

## 1. Skill 定义

把"日复盘 → 问题单 → 研究 → 回测 → 上线 → 观察"串成**可追踪的闭环**，每次改动都留痕，每次上线都有回撤预案。

## 2. 解决什么问题

- 痛点 1：策略改动无规范 → 换一个参数又回不去。
- 痛点 2：实盘问题 → 研究 → 上线之间断档。
- 痛点 3：改多版本后忘了当前跑的是哪版。
- 增量价值：长期可维护、可审计。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：实盘
- **行情状态前提**：任何
- **不适用场景**：无

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| iteration_interval | 版本间最短隔 | — | ≥ 2 周观察 | — |
| strategy_version | SemVer | vMAJOR.MINOR.PATCH | — | TODO |
| change_log_entries | 每次改动 1 条 | — | 100% | `cta/report/change_log.md` |
| rollback_plan | 每次上线必备 | — | 100% | TODO |
| live_obs_days | 每版本实盘观察天数 | ≥ 10 | — | — |

## 5. 常见策略映射

### 流程
```text
每日复盘
  ↓ 产出 issue
进入 backlog（标签：bug / enhance / research）
  ↓ 选 1-2 个进入当周
研究 + 回测 → 方案
  ↓
回测报告 + walk-forward → PR
  ↓ 评审
dry-run / paper trade（1-2 周）
  ↓
灰度：1 个品种或 1/3 仓位
  ↓ 观察 ≥ 10 个交易日
全量上线
  ↓
记录 change_log + 下一轮
```

### 回退预案
- 每次上线准备回退版本的启动命令 + 数据
- 实盘表现触发回退条件（连亏 ≥ X / 错单 ≥ Y / 回撤 ≥ Z）立即回退

## 6. 代码模块设计

```text
cta/strategy/common/ops/
├── version.py            # 版本号管理
├── backlog.py            # issue 跟踪
├── rollout.py            # 灰度策略
└── change_log_writer.py  # 写 change_log
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class ReleasePlan:
    strategy: str
    from_version: str
    to_version: str
    rollout_stages: list[dict]   # 灰度阶段 + 条件
    rollback_cmd: str
    observation_days: int = 10

def plan_release(
    strategy: str,
    current: str,
    next_ver: str,
    changes: list[str],
) -> ReleasePlan:
    ...

def append_change_log(
    date: pd.Timestamp,
    branch: str,
    task: str,
    files: list[str],
    conclusion: str,
) -> None:
    """按仓库 change_log 格式追加。"""
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：版本存活时长 / 回退次数 / 实盘 vs 回测衰减 / 每版本净贡献 / 上线周期 / change_log 完整率
- **分层评估**：按策略
- **稳健性检验**：不适用（流程）

## 8. 常见错误

- 直接从研究分支上线 → 无 dry-run
- 灰度跳过 → 一把梭
- change_log 漏记 → 问题追溯链断
- rollback 预案只写不试 → 真出事时用不上

## 9. 迭代方向

- v1：基于文档流程
- v2：GitHub issues / projects 集成
- v3：自动回归（每次 PR 跑完整回测）
- v4：策略注册中心（strategy registry） + A/B

## 10. 与其他 Skills 的关系

- **依赖**：`10_live_ops/04_daily_review.md`、`08_data_and_backtest_infra/05_trade_log_and_evaluation.md`
- **被依赖**：`00_overview_methodology/05_iteration_workflow.md`（宏观）
- **互补**：`09_ml_augmentation/05_walk_forward_validation.md`
