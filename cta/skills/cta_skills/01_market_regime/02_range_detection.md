# 震荡识别 / Range Detection

> 归属章节：`01_market_regime/` · 前置技能：`01_trend_detection.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

判断当前行情是否处于**无方向、围绕中枢回归**的震荡态，给出震荡上下沿、震荡宽度、震荡年龄。反转 / 均值回归 / 边界卖出策略的前置 gate。

## 2. 解决什么问题

- 痛点 1：趋势策略在震荡中被反复打止损。
- 痛点 2：震荡边界画得过窄 → 太早反手；过宽 → 错过机会。
- 痛点 3：震荡年龄越大，突破概率越高，但无指标提示。
- 增量价值：把"震荡"变成**数值化的 range_score + 上下沿 + 宽度**，让反转策略直接消费。

## 3. 适用市场 / 适用场景

- **品种类别**：有色、部分能化（震荡性好）；黑色少一点
- **周期**：day / minute60 / minute30
- **行情状态前提**：非趋势、非事件驱动
- **不适用场景**：主力切换、涨跌停附近

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| Bollinger 宽度 | `(UB - LB) / MA` | N=20, k=2 | < 历史 30% 分位 → 压缩 | TODO |
| ADX | Wilder ADX | N=14 | < 20 视为震荡 | TODO |
| Hurst 指数 | R/S 或 DFA | window=100 | < 0.45 回归性强 | TODO |
| 通道拟合残差 | `std(close - linreg(close, N)) / close` | N=50 | **> 1% 视为震荡**（残差大=偏离趋势线=乱动） | `cta/skills/market_regime/range.py::_channel_residual_pct` |
| 震荡上下沿 | 过去 N bar 的高低点 | N=20 | — | `cta/feature/price_action.py::pa_rolling_high/low_*` |

## 5. 常见策略映射

### 策略 A：震荡 score + 上下沿
- **信号定义**：
  ```text
  range_score = 0.30 * (adx < 20 ? 1 : 0)
              + 0.25 * (bb_width < Q30 ? 1 : 0)
              + 0.25 * (hurst < 0.45 ? 1 : 0)
              + 0.20 * (|close - linreg| / close > 1% ? 1 : 0)   # 残差大=偏离趋势线=乱动=震荡
  upper = rolling_high(N=20)
  lower = rolling_low(N=20)
  ```
- **开仓触发**：见 `04_range_strategies/01_range_boundary_reversal.md`（本 skill 只提供 score/边界）。
- **止损规则**：N 期新高/新低之外 1 ATR。
- **平仓规则**：回到中枢（(upper+lower)/2）或对侧。
- **仓位规模**：0.2% 权益（震荡胜率高但盈亏比低）。
- **失败模式**：震荡被突破 → 本 skill 的 `range_score` 会快速下降，需立刻退场。

### 策略 B：震荡宽度收缩打分
- 如果 `bb_width` 连续 10 bar 位于历史 20% 分位以下 → 准备迎接突破 → 交给 `03_breakout_threshold_detection.md`。

## 6. 代码模块设计

```text
cta/strategy/common/regime/
├── range.py            # range_score + 上下沿
├── bollinger.py        # 布林带
└── hurst.py            # Hurst 指数
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class RangeState:
    score: float                    # [0, 1]
    upper: float
    lower: float
    width_pct: float                # (upper-lower)/mid
    age_bars: int                   # 震荡持续 bar 数

def compute_range_state(
    df: pd.DataFrame,
    bb_n: int = 20,
    bb_k: float = 2.0,
    adx_n: int = 14,
    hurst_window: int = 100,
) -> pd.DataFrame:
    """每根 bar 返回 RangeState 各字段为列。"""
    ...

def detect_range_age(
    range_score_series: pd.Series,
    threshold: float = 0.6,
) -> pd.Series:
    """返回 range_score 连续高于阈值的 bar 数。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：`range_score > 0.6` 时段的反转胜率、平均盈亏比、震荡边界命中率、边界虚破后真破率、持仓 bar、range_age 分布、误判率
- **分层评估**：按品种（有色 vs 农产品）、按年份、按震荡 age
- **稳健性检验**：
  1. bb_n 参数 15/20/25，range_score 稳定性 ≥ 80%
  2. 去掉 Hurst 指标，score 下降不超过 20%
  3. 日线 → 60m 的 age 计算一致性

## 8. 常见错误

- 用 ADX < 25 就认为震荡 → ADX 滞后严重
- 震荡上下沿用 fixed lookback → 不同品种波动率不同应 adaptive
- 忽略震荡 age → 长震荡下注更容易突破
- 震荡里用趋势仓位规模 → 胜率高但被一两笔突破抹平

## 9. 迭代方向

- v1：加权 score
- v2：换成分类器（震荡/趋势/过渡，见 `09_ml_augmentation/02_regime_classifier.md`）
- v3：上下沿用 KDE 找密度峰（而不是简单 rolling high/low）
- v4：跨品种同震荡的"组合对冲"可能性探索

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/04_volatility_regime.md`（震荡常伴波动率压缩）
- **被依赖**：`04_range_strategies/` 全部 4 个 skill、`05_regime_switch_strategies/01_volatility_compression_expansion.md`
- **互补**：`01_market_regime/03_breakout_threshold_detection.md`（震荡 + 临界 = 突破机会）
- **替代**：`01_market_regime/01_trend_detection.md`（二者互斥，高的那个为主）
