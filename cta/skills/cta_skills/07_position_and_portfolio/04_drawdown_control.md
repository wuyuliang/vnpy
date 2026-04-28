# 回撤控制 / Drawdown Control

> 归属章节：`07_position_and_portfolio/` · 前置：`01-03` · 关联：`cta/strategy/brooks/core/risk/portfolio.py`

## 1. Skill 定义

组合出现**特定程度回撤**时**自动降仓 / 停策略**。把"不能爆仓"写成可运行的规则，而不是 equity 跌破阈值后靠人为决断。

## 2. 解决什么问题

- 痛点 1：回撤到 5% 才开始思考"是不是该降仓"，已晚。
- 痛点 2：策略连亏时继续满仓 → 情绪化止损。
- 痛点 3：降仓节奏无规则，导致"降了又加"。
- 增量价值：让资金曲线拥有"自愈"能力。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：组合级，通常日频
- **行情状态前提**：任何
- **不适用场景**：单品种测试阶段（样本不够）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| current_drawdown | (peak - cur_equity) / peak | — | ≥ 3% / 5% / 8% 分级 | `cta/strategy/brooks/core/risk/portfolio.py` |
| peak_equity | running max | — | — | 计算 |
| consecutive_losing_days | 连续亏损天数 | — | ≥ 5 | 计算 |
| trailing_sharpe | 滚动 30 日 Sharpe | — | < 0 预警 | TODO |
| max_allowed_risk_pct | 根据 DD 降级 | — | 100% / 70% / 50% | TODO |

## 5. 常见策略映射

### 策略 A：三级降仓
- **规则**：
  ```text
  dd_level:
    dd < 3%:  max_risk = 100% baseline
    3% <= dd < 5%: max_risk = 70%
    5% <= dd < 8%: max_risk = 50%
    dd >= 8%: freeze new orders, manage existing only
  ```
- **应用**：每日 on market close 更新 dd_level，次日新仓按对应风险系数。
- **恢复规则**：equity 回到 dd_start 后逐级升回。
- **失败模式**：连续跳档 → 仓位不稳，需 hysteresis（上/下档阈值不同）。

### 策略 B：连亏天数 gate
- 5 连亏 → 降仓 70%；10 连亏 → 策略下线 review（参考 `10_live_ops/04_daily_review.md`）。

## 6. 代码模块设计

```text
cta/strategy/common/risk/
├── drawdown.py              # 状态机
└── drawdown_hysteresis.py   # 缓冲
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

DDLevel = Literal['normal','mild','moderate','severe','freeze']

@dataclass
class DDState:
    dd: float
    level: DDLevel
    max_risk_multiplier: float   # 1.0 / 0.7 / 0.5 / 0.3 / 0
    freeze_new: bool

def compute_dd_state(
    equity_curve: pd.Series,
    thresholds: tuple[float,...] = (0.03, 0.05, 0.08),
) -> pd.DataFrame:
    ...

def apply_dd_to_sizing(
    base_lots: int,
    dd_state: DDState,
) -> int:
    """按 max_risk_multiplier 缩放 base_lots（向下取整）。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：最大回撤下降 / 回撤恢复时长 / 降仓触发次数 / 漏掉的行情数 / Sharpe / Calmar / 总 PnL 相对无 DD gate 损失
- **分层评估**：按年份 / 按 DD 等级
- **稳健性检验**：
  1. 阈值 (3,5,8) vs (2,4,6) vs (5,8,12)
  2. hysteresis 阈值 vs 无 hysteresis 的切换频率
  3. OOS 的 MDD 和 IS 一致性

## 8. 常见错误

- 从"实时权益"算 DD 而非"日终" → 日内波动误触发
- 降仓后不恢复或恢复过快
- 与 `transition_risk_control` 重叠计算 → 双重缩放
- 不做 hysteresis → 档位反复跳

## 9. 迭代方向

- v1：三级线性降仓
- v2：hysteresis + 滚动 Sharpe
- v3：按"DD 根因"差异化（是大盘原因还是策略原因）
- v4：Kelly / 风险预算动态分配

## 10. 与其他 Skills 的关系

- **依赖**：`07_position_and_portfolio/01_single_trade_risk.md`、`07_position_and_portfolio/02_vol_targeting.md`
- **被依赖**：`10_live_ops/03_monitoring_and_alerting.md`（DD 等级变化 → 告警）
- **互补**：`05_regime_switch_strategies/04_transition_risk_control.md`、`10_live_ops/04_daily_review.md`
