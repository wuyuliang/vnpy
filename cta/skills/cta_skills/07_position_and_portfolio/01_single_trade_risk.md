# 单笔风险 / Single Trade Risk

> 归属章节：`07_position_and_portfolio/` · 前置：`06_filtering_and_scoring/04_risk_reward_score.md` · 关联：`cta/strategy/brooks/core/risk/sizing.py`、`cta/config/futures_meta.py`

## 1. Skill 定义

每笔开仓的**单笔风险额 = 固定 % 权益**（Brooks / Turtle / Vealo 的公共约定）。给定 entry / stop，按风险反推手数。是最基础、最必须的风控规则。

## 2. 解决什么问题

- 痛点 1：固定手数 → 不同品种 / 不同止损距离风险差异巨大。
- 痛点 2：拍脑袋下单 → 单笔亏损过大摧毁资金曲线。
- 痛点 3：各策略风险口径不同 → 无法横向比。
- 增量价值：把"风险"作为第一性原理，统一所有策略。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：所有
- **行情状态前提**：任何
- **不适用场景**：无

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| risk_pct | 单笔风险 / 当前权益 | 0.1%-0.5% | Brooks v3 = 0.1% | `cta/strategy/brooks/core/risk/sizing.py` |
| contract_multiplier | 合约乘数 | — | 见 `cta/config/futures_meta.py` | `cta/config/futures_meta.py` |
| stop_distance_pts | abs(entry - stop) | — | — | 计算 |
| lot_size | 算出的手数 | — | ≥ 1 | 计算 |
| min_unit_risk | 最小 1 手的风险 / 权益 | — | 若已超过 risk_pct 则不开 | 计算 |

## 5. 常见策略映射

### 策略 A：按 risk_pct 算手数
- **公式**：
  ```text
  risk_amount = equity * risk_pct
  stop_distance = abs(entry - stop)          # in price pts
  per_lot_risk = stop_distance * contract_multiplier
  lot_size = floor(risk_amount / per_lot_risk)
  if lot_size < 1: skip_trade
  ```
- **应用**：每笔交易先过此公式得到 lot_size，再送到订单层。
- **失败模式**：
  - stop_distance 极小（假 signal bar）→ lot 非常大，需对手数上限做 cap
  - 合约乘数未维护 → per_lot_risk 算错

## 6. 代码模块设计

```text
cta/strategy/common/risk/
├── sizing.py             # 已在 brooks v3 有参考实现
└── sizing_cap.py         # 手数 cap（避免极端值）
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class SizingResult:
    lot_size: int
    per_lot_risk: float
    total_risk_amount: float
    skip: bool
    skip_reason: str | None

def size_by_risk_pct(
    entry: float,
    stop: float,
    equity: float,
    contract_multiplier: float,
    risk_pct: float = 0.001,
    max_lots: int = 50,
) -> SizingResult:
    """
    单笔风险固定为 risk_pct * equity，反推手数。
    lot < 1 或 > max_lots 则 skip。
    """
    ...

def resolve_multiplier(symbol: str) -> float:
    """从 cta/config/futures_meta.py 拿到乘数。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：每笔最大亏损 / equity / 每笔平均风险 / equity / 最大单日风险 / 单品种仓位占比 / 手数分布（1/2/3+）/ 因手数=0 被 skip 的次数 / Sharpe
- **分层评估**：按品种 / 按策略 / 按年份
- **稳健性检验**：
  1. risk_pct = 0.05% / 0.1% / 0.2% / 0.3% 的最大回撤
  2. max_lots cap 的触发频率
  3. stop_distance 极小时的 skip 率

## 8. 常见错误

- 忘乘合约乘数 → 实盘风险 ×100
- equity 取"期初"而非"动态" → 回撤后仍按原值开
- 没设 max_lots → 小止损时手数爆炸
- 不同币种 / 保证金混算

## 9. 迭代方向

- v1：固定 risk_pct
- v2：动态 risk_pct（信号质量 score × 基准 risk）
- v3：Kelly-lite（按历史胜率 / 盈亏比调整）
- v4：多品种协同（整体权益视角）

## 10. 与其他 Skills 的关系

- **依赖**：`06_filtering_and_scoring/04_risk_reward_score.md`（RR + risk → size）
- **被依赖**：所有策略 skill、`10_live_ops/01_signal_to_order.md`
- **互补**：`07_position_and_portfolio/02_vol_targeting.md`（可替代 / 组合使用）
