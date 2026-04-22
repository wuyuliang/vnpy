# 组合配置 / Portfolio Allocation

> 归属章节：`07_position_and_portfolio/` · 前置：`01-04 整章` · 关联：`cta/strategy/brooks/`

## 1. Skill 定义

把**多策略 × 多品种**合并成统一组合，分配风险预算、协调冲突单、记录总体状态。是整个 CTA 的"总指挥"。

## 2. 解决什么问题

- 痛点 1：多策略各自下单 → 总风险超标。
- 痛点 2：信号冲突（趋势多 + 震荡空）无法协调。
- 痛点 3：不同策略的相关性 / 容量不一致，等权分配不合理。
- 增量价值：让多策略组合表现优于任何单策略（分散化红利）。

## 3. 适用市场 / 适用场景

- **品种类别**：全部研究池
- **周期**：组合层日频，品种层各自
- **行情状态前提**：任何
- **不适用场景**：只有 1 个策略的早期阶段

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| strategy_weight | 策略组合中的权重 | equal / Sharpe / risk_parity | — | TODO |
| portfolio_risk | 所有持仓 risk 之和 / equity | — | < 3% | TODO |
| conflict_rule | 多空冲突处理 | net / block | — | TODO |
| rebalance_frequency | 权重再平衡 | 月 / 季 | — | — |
| correlation_matrix | 策略间 PnL 相关 | 60 日 | — | TODO |

## 5. 常见策略映射

### 策略 A：风险平价（risk parity）组合
- **规则**：
  ```text
  每月:
    for each strategy:
      估 std(strategy_daily_pnl) in last 60 days
    weight_i = (1/std_i) / sum(1/std_j)
    total_risk_budget = 3% of equity
    strategy_risk_budget_i = weight_i * total_budget
  每笔单:
    取 strategy 的 risk_budget 作为本策略单笔 risk_pct 的上限
  ```
- **应用**：
  - 按品种合并同 symbol 多空仓位（net）
  - 对齐周期：每月月末重算权重
- **失败模式**：某策略短期高 std（近期震荡剧烈）→ 权重被压低，错过之后的好行情。

### 策略 B：等权 + 冲突规则
- 所有策略等权；相同品种多空冲突 → 以更新的信号为准。

## 6. 代码模块设计

```text
cta/strategy/common/portfolio/
├── allocator.py             # 风险平价 / 等权
├── conflict_resolver.py     # 冲突规则
└── rebalance.py
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

AllocScheme = Literal['equal','risk_parity','sharpe']

@dataclass
class AllocationPlan:
    weights: dict[str, float]    # strategy_name -> weight
    risk_budget: dict[str, float]
    scheme: AllocScheme

def allocate_portfolio(
    strategy_pnl_panel: pd.DataFrame,
    total_risk_pct: float = 0.03,
    scheme: AllocScheme = 'risk_parity',
) -> AllocationPlan:
    ...

def resolve_conflict(
    new_order: dict,
    existing_positions: list[dict],
    policy: Literal['net','block','last_wins'] = 'net',
) -> dict | None:
    """返回调整后的订单或 None（被 block）。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：组合 Sharpe 相比单策略的提升 / 组合 MDD 相比单策略 / 策略间相关性 / 权重变化稳定性 / turnover 增加 / 合并冲突后的信号效率 / 总成本 / 风险利用率
- **分层评估**：按策略 / 按板块 / 按年份
- **稳健性检验**：
  1. 等权 vs risk parity vs sharpe 组合的对比
  2. rebalance 频率（月 / 季 / 年）
  3. 冲突规则（net / block）

## 8. 常见错误

- 不合并 net 仓位 → 实盘 margin 爆
- 权重变化过激 → 组合交易成本飙升
- 相关性只看正常时段 → 事件来临时全部同向
- 不监控策略退化 → 死策略权重仍被分配

## 9. 迭代方向

- v1：risk parity
- v2：加入策略 alpha 信号（Sharpe weighted）
- v3：MVO（均值方差）优化
- v4：机器学习加权（动态）

## 10. 与其他 Skills 的关系

- **依赖**：`07_position_and_portfolio/01-04 整章`、`03_trend_strategies/04_cross_sectional_momentum.md`
- **被依赖**：`10_live_ops/02_order_execution.md`（按组合计划执行）
- **互补**：`07_position_and_portfolio/03_sector_exposure_control.md`
