# 均值回归 / Mean Reversion

> 归属章节：`04_range_strategies/` · 前置：`01_market_regime/02_range_detection.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

价格偏离均值（MA / VWAP）过大时，做"回归中枢"的交易。边界反转是几何版，均值回归是统计版，思想相同但可量化到 Z-score。

## 2. 解决什么问题

- 痛点 1：边界定义依赖于"上下沿"几何，均值回归改用统计阈值（Z-score），更稳健。
- 痛点 2：多品种共用一套规则（Z-score 本身归一化）。
- 痛点 3：与基本面因子兼容（VWAP、日内均价）。
- 增量价值：震荡策略的"标准化"版本。

## 3. 适用市场 / 适用场景

- **品种类别**：有色 / 能化；农产品部分品种
- **周期**：minute30 / minute15 / minute5
- **行情状态前提**：`range_score > 0.5`，Hurst < 0.45
- **不适用场景**：趋势、新闻日

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| MA(N) | SMA/EMA | N=20 | — | `pa_ma_*` |
| Z-score | `(close - MA) / std(close - MA, N)` | N=50 | \|Z\| > 2 偏离 | TODO |
| VWAP | 日内累计 `sum(p*v)/sum(v)` | — | — | TODO |
| Hurst | — | 100 | < 0.45 | TODO |
| ATR | — | 14 | — | `pa_atr_*` |

## 5. 常见策略映射

### 策略 A：Z-score 均值回归
- **信号定义**：
  ```text
  mean = MA(close, 20)
  sd   = std(close - mean, 50)
  z    = (close - mean) / sd
  long_entry:  z < -2 且 range_score > 0.5 且 Hurst < 0.45
  short_entry: z >  2 且 ...
  ```
- **开仓触发**：下一根 bar 开盘市价（或限价更低/更高）。
- **止损规则**：Z 继续恶化到 \|Z\| > 3.5 → 平。
- **平仓规则**：
  - \|Z\| < 0.5 → 平（回归完成）
  - 持仓 > K=30 bar 未回归 → 平
- **仓位规模**：0.2% 权益。
- **失败模式**：趋势启动（Z 持续扩大），本 skill 会亏损。

### 策略 B：VWAP 回归（日内）
- 价格偏离 VWAP ±2σ 时反向，目标 VWAP。

## 6. 代码模块设计

```text
cta/strategy/mean_reversion/
├── signals.py
├── filters.py
├── risk.py
├── run_backtest.py
└── config.yaml
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class MRSignal:
    side: Literal['long','short','flat']
    z: float
    mean: float
    sd: float

def compute_zscore(
    df: pd.DataFrame,
    ma_n: int = 20,
    sd_n: int = 50,
) -> pd.DataFrame:
    ...

def mr_decision(
    df: pd.DataFrame,
    bar_idx: int,
    z_threshold: float = 2.0,
    regime_gate: bool = True,
) -> Optional[MRSignal]:
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：胜率 / 盈亏比 / 平均 Z 达到时间 / 未回归占比 / Sharpe / 与趋势策略相关性 / 成本敏感性 / 每日交易次数
- **分层评估**：按 Z 阈值 / 按品种 / 按时段
- **稳健性检验**：
  1. z_threshold = 1.5 / 2 / 2.5
  2. Hurst 过滤开关
  3. 持仓时间限制

## 8. 常见错误

- 不做 regime 过滤 → 趋势中无限亏
- 止损不硬 → 一笔单把历史盈利全吞
- Z 窗口太小 → 噪声 Z
- 忽略主力换月、涨跌停日

## 9. 迭代方向

- v1：Z-score 规则
- v2：+ 状态切换 gate（见 `05_regime_switch_strategies/`）
- v3：配对回归（pair mean reversion，跨品种）
- v4：ML 预测回归概率

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/02_range_detection.md`
- **被依赖**：`07_position_and_portfolio/05_portfolio_allocation.md`
- **互补**：`04_range_strategies/01_range_boundary_reversal.md`、`04_range_strategies/04_noise_filtering.md`
