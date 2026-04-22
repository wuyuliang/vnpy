# ATR 通道突破 / ATR Breakout

> 归属章节：`03_trend_strategies/` · 前置：`01_market_regime/04_volatility_regime.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

以**近期中枢价 ± k × ATR** 构造通道，close 突破通道 → 开仓。相比 Donchian 的极值定义，ATR 版对波动率敏感，更适合动态调整参数。

## 2. 解决什么问题

- 痛点 1：Donchian 在不同波动率下表现漂移，ATR 版自带归一化。
- 痛点 2：N 期极值在长期震荡中是近期的极值，触发慢；ATR 通道更早响应。
- 痛点 3：与 vol targeting 天然契合。
- 增量价值：与 Donchian 互补，回测中两者相关性 < 0.7，可组合分散。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：day / minute60 / minute30 / minute15
- **行情状态前提**：`compression → expansion` 的过渡，或已 `expansion`
- **不适用场景**：`low` + `震荡` 双低态

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 中枢 MA | `MA(close, N)` | N=20 | — | `cta/feature/price_action.py::pa_ma_*` |
| ATR(M) | Wilder ATR | M=14 | — | `cta/feature/price_action.py::pa_atr_*` |
| 上轨 | `MA + k * ATR` | k=2..3 | — | TODO |
| 下轨 | `MA - k * ATR` | — | — | TODO |
| 突破确认 | 连续 C 根 close 在通道外 | C=1..2 | — | 计算 |

## 5. 常见策略映射

### 策略 A：ATR channel breakout
- **信号定义**：
  ```text
  upper = MA(close, 20) + 2.5 * ATR(14)
  lower = MA(close, 20) - 2.5 * ATR(14)
  long_entry  = close[t] > upper 且 close[t-1] <= upper_{t-1}
  short_entry = close[t] < lower 且 close[t-1] >= lower_{t-1}
  ```
- **开仓触发**：下一根 bar 开盘市价（Donchian 用 stop，此处建议 open 以避免极端滑点）。
- **止损规则**：MA 另一侧 0.5 ATR，或反向通道。
- **平仓规则**：
  - close 回到 MA → 平仓
  - 达到 3 ATR 收益 → trailing
- **仓位规模**：0.3% 权益 ÷ (ATR × multiplier)。
- **失败模式**：compression 末期频繁假突破。

### 策略 B：ATR channel + HTF 同向
- HTF 的 MA 斜率同向才开。

## 6. 代码模块设计

```text
cta/strategy/atr_breakout/
├── signals.py          # 通道计算 + 触发
├── filters.py          # regime / 斜率过滤
├── risk.py
├── run_backtest.py
└── config.yaml
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class ATRChannelSignal:
    side: Literal['long','short','flat']
    entry_price: float
    stop_price: float
    exit_ma: float

def compute_atr_channel(
    df: pd.DataFrame,
    ma_n: int = 20,
    atr_n: int = 14,
    k: float = 2.5,
) -> pd.DataFrame:
    """添加 ma_* / atr_* / upper / lower 列。"""
    ...

def decide_atr_channel_trade(
    df: pd.DataFrame,
    bar_idx: int,
    current_position: ATRChannelSignal | None,
    filters: dict,
) -> Optional[ATRChannelSignal]:
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：总收益 / 年化 / MDD / Sharpe / 胜率 / 盈亏比 / 与 Donchian 的相关性 / 单独 PnL 是否稳定
- **分层评估**：按品种 / 按 k / 按 ma_n
- **稳健性检验**：
  1. k = 2.0 / 2.5 / 3.0 的稳健性
  2. 与 Donchian 组合的 Sharpe 提升
  3. 成本敏感性

## 8. 常见错误

- 用 close 成交（回测好看 / 实盘难复现）
- k 太小（< 2）→ 频繁触发
- 没 vol targeting → 高波品种单笔风险爆
- 忽略 MA 的"平滑滞后" → 趋势末段反而触发

## 9. 迭代方向

- v1：纯规则
- v2：+ `05_regime_switch_strategies/01_volatility_compression_expansion.md` 的 gate
- v3：k 参数随 `vol_regime` 自适应
- v4：与 Donchian ensemble

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/04_volatility_regime.md`、`01_market_regime/01_trend_detection.md`
- **被依赖**：`07_position_and_portfolio/05_portfolio_allocation.md`（与 Donchian 组合分配）
- **互补 / 替代**：`03_trend_strategies/01_donchian_breakout.md`
