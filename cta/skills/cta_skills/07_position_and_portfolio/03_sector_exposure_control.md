# 板块 / 大类敞口控制 / Sector Exposure Control

> 归属章节：`07_position_and_portfolio/` · 前置：`02_vol_targeting.md` · 关联：`cta/config/futures_meta.py`

## 1. Skill 定义

限制每一**商品大类**（黑色 / 有色 / 能化 / 农产品 / 贵金属）的同向总风险敞口。避免多品种同向开仓放大单一宏观事件的风险。

## 2. 解决什么问题

- 痛点 1：多品种同时看多黑色 → 1 次事件全部亏损。
- 痛点 2：vol targeting 只看单品种，不看板块。
- 痛点 3：事件发生后各品种相关性骤升（变成 1）。
- 增量价值：降低尾部事件风险，提升组合 Calmar。

## 3. 适用市场 / 适用场景

- **品种类别**：全部板块
- **周期**：所有
- **行情状态前提**：任何
- **不适用场景**：单品种策略

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| sector_net_risk | 板块内所有品种净风险 | — | < 1% equity | TODO |
| sector_gross_risk | 板块内 gross 风险 | — | < 1.5% | TODO |
| sector_same_direction_count | 同向品种数 | — | ≤ 3 | TODO |
| cross_sector_correlation | 板块间 60-day 相关 | — | — | TODO |
| portfolio_net_risk | 组合总净风险 | — | < 3% | TODO |

## 5. 常见策略映射

### 策略 A：板块敞口 cap
- **规则**：
  ```text
  for sector in portfolio.sectors:
    if sector.net_risk > 1.0% * equity:
      跳过新开同向仓位
    if sector.same_direction_count >= 3:
      提高新开仓 ML gate 阈值（p > 0.6 才能进）
  ```
- **应用**：在 `10_live_ops/01_signal_to_order.md` 订单前检查。
- **仓位规模**：若超标，新开仓自动缩减到合规线以内。
- **失败模式**：多板块同向（黑色 + 有色同涨）→ 组合净风险仍超标，需组合级 gate 补刀。

## 6. 代码模块设计

```text
cta/strategy/common/risk/
├── sector_cap.py
└── sector_map.py          # symbol -> sector（用 cta/config/futures_meta.py）
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

Sector = Literal['black','nonferrous','chemicals','agri','precious']

@dataclass
class SectorExposure:
    sector: Sector
    net_risk_pct: float
    gross_risk_pct: float
    same_dir_count: int

def compute_portfolio_exposure(
    positions: list[dict],
    equity: float,
) -> dict[Sector, SectorExposure]:
    ...

def sector_cap_gate(
    new_order: dict,
    current_exposure: dict[Sector, SectorExposure],
    max_sector_net: float = 0.01,
    max_same_dir: int = 3,
) -> bool:
    """True 表示通过 gate，可以下单。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：板块峰值敞口 / 超标触发次数 / 被 gate 拒绝的单数 / 拒绝后 PnL 变化 / 组合 MDD 改善 / 总 PnL 减少 / Sharpe 变化
- **分层评估**：按板块 / 按年份
- **稳健性检验**：
  1. max_sector_net = 0.5% / 1% / 1.5% 的权衡
  2. 关闭 gate 的对照 PnL
  3. 事件窗口（OPEC / 中央政策）时的效果

## 8. 常见错误

- 板块分类用经验而非数据驱动
- 相关性只看正常时段 → 事件发生时失效
- 只看 net 忽略 gross → 对冲时仍过度集中
- 忽略跨板块共振（黑色+有色宏观联动）

## 9. 迭代方向

- v1：硬板块 cap
- v2：动态 cap（板块相关性高时收紧）
- v3：风险平价 / 协方差约束
- v4：宏观事件日历驱动的临时收紧

## 10. 与其他 Skills 的关系

- **依赖**：`07_position_and_portfolio/02_vol_targeting.md`、`cta/config/futures_meta.py`
- **被依赖**：`07_position_and_portfolio/05_portfolio_allocation.md`、`10_live_ops/01_signal_to_order.md`
- **互补**：`07_position_and_portfolio/04_drawdown_control.md`
