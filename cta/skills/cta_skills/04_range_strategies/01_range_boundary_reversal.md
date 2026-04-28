# 边界反转 / Range Boundary Reversal

> 归属章节：`04_range_strategies/` · 前置：`01_market_regime/02_range_detection.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

识别震荡态的上下沿，**价格触及沿后反向入场**，目标回到中枢或另一侧。典型 mean reversion 的几何版本。

## 2. 解决什么问题

- 痛点 1：震荡中趋势策略反复被抽打。
- 痛点 2：看似边界，但突破时若不立刻退，会被一把抽干。
- 痛点 3：止损该放在哪？盈亏比难设计。
- 增量价值：提供高胜率（55-65%）、小盈亏比（1.0-1.3）的稳定收入来源。

## 3. 适用市场 / 适用场景

- **品种类别**：有色（震荡性强）、部分能化
- **周期**：minute60 / minute30 / minute15
- **行情状态前提**：`range_score > 0.6` 且 `range_age > 20 bar`
- **不适用场景**：趋势、压缩末期（突破概率高）、事件驱动

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 上下沿 | 由 `02_range_detection` 给出 | N=20 rolling | — | `cta/feature/price_action.py::pa_rolling_*` |
| 边界距离 | \|close - upper\| / width 或 / lower | — | < 0.1 视为接近边界 | 计算 |
| 反向 signal bar | pa_h1/l1 反向 | — | 出现 | `pa_h1_l1_*` |
| 成交量萎缩 | 到边界时 vol / MA(vol) | — | < 1 更好 | 计算 |
| RSI / 随机指标 | 超买超卖辅助 | 14 | > 70 / < 30 | TODO |

## 5. 常见策略映射

### 策略 A：边界反转基本版
- **信号定义**：
  ```text
  1. range_score > 0.6 且 range_age > 20
  2. close[t] / upper - 1 < -0.5% (即接近上沿)
  3. 当前 bar 是 pa_h2_signal 对应的反向 L1 或上影线长
  ```
- **开仓触发**：`close[t]` 下方挂限价卖单（不等 stop，因为边界附近波动大）。
- **止损规则**：`upper + 0.3 * ATR + tick`（允许边界被试探但不能成立）。
- **平仓规则**：
  - 回到 range 中枢 (upper+lower)/2 → 平 50%
  - 到达下沿 → 平剩余
  - 边界被有效击穿 → 立刻平
- **仓位规模**：0.2% 权益（震荡胜率高，仓位小）。
- **失败模式**：震荡突破日反复被打。

### 策略 B：RSI 叠加过滤
- 仅在 RSI(14) > 70 / < 30 时入场。

## 6. 代码模块设计

```text
cta/strategy/range_boundary/
├── signals.py
├── filters.py        # range_score / range_age gate
├── risk.py
├── run_backtest.py
└── config.yaml
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class BoundaryReversalSetup:
    valid: bool
    side: Literal['long','short']
    boundary: float
    stop: float
    mid: float
    far_boundary: float

def detect_boundary_reversal(
    df: pd.DataFrame,
    range_df: pd.DataFrame,
    proximity_pct: float = 0.005,
) -> pd.DataFrame:
    ...

def place_boundary_order(
    setup: BoundaryReversalSetup,
    tick_size: float,
) -> Optional[dict]:
    """返回 {'side','limit_price','stop','targets':[mid,far]}。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：胜率 / 盈亏比 / 平均持仓 / 真假反转率 / 目标到达率（mid / far）/ Sharpe / 单日最大亏损 / 成本敏感性
- **分层评估**：按 range_age 桶、按品种、按时段
- **稳健性检验**：
  1. proximity_pct 0.3%/0.5%/0.8% 的信号数 vs 胜率
  2. 震荡判定阈值变化
  3. 成本 ×2 仍盈利

## 8. 常见错误

- range 判定刚成立就进场 → range_age 不够
- 止损放在边界上（不留缓冲）→ 被扫掉
- 盈亏比当成 2+ → 震荡策略应接受 1.0-1.3
- 忽略事件日 → 边界失效

## 9. 迭代方向

- v1：规则版
- v2：+ RSI / 随机指标过滤
- v3：动态止损（按 vol_regime）
- v4：ML 过滤

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/02_range_detection.md`
- **被依赖**：`07_position_and_portfolio/05_portfolio_allocation.md`（与趋势策略组合）
- **互补**：`04_range_strategies/02_mean_reversion.md`、`04_range_strategies/03_false_breakout_reversal.md`
