# 均线趋势跟随 / MA Trend Following

> 归属章节：`03_trend_strategies/` · 前置：`01_market_regime/01_trend_detection.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

用快慢两条均线交叉 / 同向 / 价格相对位置来判定趋势并持仓。最经典的趋势跟随原型，教学必备、基线必选。

## 2. 解决什么问题

- 痛点 1：需要一个**简单可解释**的策略 benchmark。
- 痛点 2：MA 参数选多少？本 skill 给出挑参的评估框架。
- 痛点 3：交叉频繁 → 本 skill 提出过滤与平滑方案。
- 增量价值：作为 benchmark；任何新趋势策略必须显著优于本 skill 才有意义。

## 3. 适用市场 / 适用场景

- **品种类别**：趋势性品种（黑色、有色、能化）
- **周期**：day / minute60（低周期滞后严重）
- **行情状态前提**：明显趋势
- **不适用场景**：震荡、过渡

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| MA 快 | SMA or EMA | 10/20 | — | `cta/feature/price_action.py::pa_ma_*` |
| MA 慢 | SMA or EMA | 40/60 | — | 同上 |
| MA 斜率 | (MA[t]-MA[t-N])/MA[t-N] | N=5 | > 0.5% 强向上 | 计算 |
| 价格 / MA 距离 | (close - MA) / ATR | — | > 2 偏离过大 | 计算 |
| 交叉确认 bar | 连续 C 根同向 | C=1-2 | — | 计算 |

## 5. 常见策略映射

### 策略 A：双均线 + 斜率过滤
- **信号定义**：
  ```text
  long_entry:  ma_fast > ma_slow 且 slope(ma_fast) > 0 且 slope(ma_slow) > 0
  short_entry: ma_fast < ma_slow 且 slope 两者均 < 0
  ```
- **开仓触发**：下一根 bar 开盘市价。
- **止损规则**：`entry - 2 * ATR(14)` 或 `ma_slow` 另一侧。
- **平仓规则**：快慢均线反交叉或价格穿越 `ma_slow`。
- **仓位规模**：0.3% 权益 vol target。
- **失败模式**：震荡市，两均线频繁交叉。

### 策略 B：EMA 组（3 条或多条）
- `ema_fast > ema_med > ema_slow` 才多头，反之空头。
- 减少单次反交叉的概率，但反应更慢。

### 策略 C：价格 + MA 距离 + 斜率
- 只在 `close > ma_slow` 且斜率 > 0 时做多，基础版 + 简单过滤。

## 6. 代码模块设计

```text
cta/strategy/ma_trend/
├── signals.py          # 多种 MA 模式
├── filters.py          # 震荡过滤、斜率阈值
├── risk.py
├── run_backtest.py
└── config.yaml
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class MASignal:
    side: Literal['long','short','flat']
    fast: float
    slow: float
    slope_fast: float
    slope_slow: float

def compute_ma_features(
    df: pd.DataFrame,
    n_fast: int = 20,
    n_slow: int = 60,
    slope_lookback: int = 5,
    kind: Literal['sma','ema'] = 'ema',
) -> pd.DataFrame:
    ...

def ma_decision(
    df: pd.DataFrame,
    bar_idx: int,
    require_slope: bool = True,
) -> Optional[MASignal]:
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：总收益 / 年化 / MDD / Sharpe / Calmar / 胜率（20-30%）/ 盈亏比（2.0+）/ 震荡段的回撤幅度
- **分层评估**：按品种 / 按 fast-slow 组合 / 按是否加斜率过滤
- **稳健性检验**：
  1. (fast, slow) = (10,40), (20,60), (30,90) 单调性
  2. SMA vs EMA 差异
  3. 成本 ×2 下可行性

## 8. 常见错误

- 只看交叉 → 震荡被抽打
- 用 close 触发 → 应用下根 bar 成交
- 参数用美股股票经验（5/20/60）直接搬 → 商品不一定合适
- 不加 vol targeting → 不同品种风险差异巨大

## 9. 迭代方向

- v1：双均线
- v2：+ 斜率过滤 + 震荡 gate
- v3：用 `01_trend_detection.md` 的 `trend_score` 替换硬规则
- v4：参数自适应（按品种 / 按 vol）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/01_trend_detection.md`、`01_market_regime/04_volatility_regime.md`
- **被依赖**：`06_filtering_and_scoring/`（作为基础，被过滤器叠加）、benchmark
- **互补**：`03_trend_strategies/01_donchian_breakout.md`、`03_trend_strategies/02_atr_breakout.md`
