# 交易成本模型 / Transaction Cost Model

> 归属章节：`08_data_and_backtest_infra/` · 前置：`02_rollover_rules.md` · 关联：`cta/config/futures_meta.py`

## 1. Skill 定义

把**手续费 / 滑点 / 冲击成本 / 展期成本**统一到一个模型，回测里用它扣除，实盘用它校验。没有这层，所有 PnL 都在讲故事。

## 2. 解决什么问题

- 痛点 1：只扣固定手续费，忽略滑点 → 高频策略"纸面盈利"。
- 痛点 2：合约乘数 / 最小跳动不同，错算成本。
- 痛点 3：换月的 2 倍成本经常漏扣。
- 增量价值：回测 PnL 更接近实盘，净收益更可信。

## 3. 适用市场 / 适用场景

- **品种类别**：全部（不同交易所手续费结构不同）
- **周期**：全部
- **行情状态前提**：任何
- **不适用场景**：无

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| commission_rate | 交易所+期货公司费率 | — | 万 0.5 - 万 3 | `cta/config/futures_meta.py` |
| tick_size | 最小跳动 | — | 1-5 元 | `cta/config/futures_meta.py` |
| slippage_ticks | 每单滑点 tick | 回测默认 1-2 | — | TODO |
| impact_bps | 冲击成本 bps | vol × lots | 大单加成 | TODO |
| roll_cost_per_event | 展期成本 | 2 × (fee + slippage) | — | — |

## 5. 常见策略映射

### 模型 A：线性成本
- **公式**：
  ```text
  cost_per_trade = lots × multiplier × (
    price × commission_rate
    + slippage_ticks × tick_size
  )
  ```
- **应用**：日内 / 短线 / 中线全通用。
- **失败模式**：大单时冲击成本被低估。

### 模型 B：带冲击的非线性成本
- **公式**：
  ```text
  impact = k × sqrt(lots / adv) × price
  cost = commission + slippage + impact
  ```
- **应用**：组合规模上来后启用。
- **参数**：`k` 用历史大单回归校准；`adv` = 平均日成交额。

## 6. 代码模块设计

```text
cta/strategy/common/cost/
├── commission.py          # 手续费
├── slippage.py            # 滑点
├── impact.py              # 冲击成本
└── cost_model.py          # 总入口
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class CostComponents:
    commission: float
    slippage: float
    impact: float
    total: float

def estimate_cost(
    symbol: str,
    price: float,
    lots: int,
    side: str,
    multiplier: float,
    commission_rate: float,
    tick_size: float,
    slippage_ticks: float = 1.5,
    adv: float | None = None,
) -> CostComponents:
    ...

def apply_cost_to_pnl(
    trade_log: pd.DataFrame,
    cost_fn,
) -> pd.DataFrame:
    """返回增加 cost / net_pnl 列的 trade_log。"""
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：总成本 / 成本占 gross PnL 比 / 成本分布 / 实盘 vs 回测成本一致率 / 单品种成本排名 / 换月成本占比
- **分层评估**：按品种 / 按策略 / 按年份
- **稳健性检验**：
  1. slippage_ticks 1 vs 2 vs 3 下的净 PnL
  2. 固定 vs 带冲击模型的差
  3. 手续费率上调 20% 的鲁棒性

## 8. 常见错误

- 用收盘价撮合 + 0 滑点 → 净值假
- 忽略 multiplier → 小品种误差小，大品种巨大
- 展期当 1 次成交算 → 漏一半
- 实盘用市价单但回测按 limit 成交价 → 不一致

## 9. 迭代方向

- v1：固定 slippage_ticks
- v2：按品种 / 时段差异化 slippage
- v3：加冲击成本 sqrt 模型
- v4：学习型成本模型（XGBoost 回归 slippage）

## 10. 与其他 Skills 的关系

- **依赖**：`cta/config/futures_meta.py`
- **被依赖**：`08_data_and_backtest_infra/04_event_driven_backtest.md`、`07_position_and_portfolio/01_single_trade_risk.md`
- **互补**：`08_data_and_backtest_infra/02_rollover_rules.md`
