# 趋势识别 / Trend Detection

> 归属章节：`01_market_regime/` · 前置技能：`00_overview_methodology/` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

一套**多指标融合**的方法，判断当前行情是否处于趋势状态、趋势方向（多/空）、趋势强度（强/弱）、趋势持续性（新趋势 / 成熟趋势 / 趋势末期）。是所有趋势类策略的入场前置条件。

## 2. 解决什么问题

- 痛点 1：震荡中开趋势单 → 连续被打止损。
- 痛点 2：只用 MA 斜率判断 → 滞后。
- 痛点 3：只用 ADX → 表达力不够，无法区分"强趋势"与"末期趋势"。
- 增量价值：把"这是不是趋势"从主观变成**多指标打分**，后续策略只需"分数 > 阈值"即可。

## 3. 适用市场 / 适用场景

- **品种类别**：全部（尤其黑色 / 能化趋势性好）
- **周期**：day / minute60 / minute30（越低越噪声）
- **行情状态前提**：本 skill 就是判断 regime 本身
- **不适用场景**：事件驱动的跳空行情（新闻单），指标滞后

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| MA 斜率 | `(MA[t] - MA[t-N]) / MA[t-N]` | MA=20/50, N=5 | 日线 \|斜率\| > 1% 视为显著 | `cta/feature/price_action.py::pa_ma_slope_*`（若无则 TODO） |
| ADX(N) | Wilder ADX | N=14 | > 25 有趋势 / > 40 强趋势 | TODO: 待实现 |
| 方向效率比 DER | \|close[t] - close[t-N]\| / sum(\|close.diff()\|) | N=20 | > 0.3 显著方向性 | TODO: 待实现 |
| HH/LL 结构 | 过去 N bar 是否创 N 期新高/新低 | N=20 | 最近 5 bar 内有创新高 → 上升结构 | `cta/feature/price_action.py::pa_new_high_*` 等 |
| 通道斜率 | 线性回归斜率（log price 对 bar 索引） | N=50 | t 检验 p < 0.05 | TODO |

## 5. 常见策略映射

### 策略 A：趋势打分（trend_score ∈ [-1, 1]）
- **信号定义**：
  ```text
  trend_score = 0.35 * sign(ma50_slope)
              + 0.25 * (adx14 > 25 ? sign(di_plus - di_minus) : 0)
              + 0.20 * sign(last_HH_LL_struct)   # +1 HH 结构, -1 LL 结构
              + 0.20 * (der > 0.3 ? sign(close[t] - close[t-20]) : 0)
  ```
- **开仓触发**：`trend_score > 0.5` 做多、`< -0.5` 做空（具体下单由 `03_trend_strategies/` 的子策略决定）。
- **止损规则**：ATR(14) × 2，由信号 bar 的极值算起。
- **平仓规则**：
  - `|trend_score|` 跌破 0.2 → 平仓
  - 或达到目标 RR = 2R → 启动移动止损
- **仓位规模**：单笔 0.3% 权益（由 `07_position_and_portfolio/01_single_trade_risk.md` 决定）。
- **失败模式**：事件驱动跳空、主力切换断点、节假日归来首日。

### 策略 B：MA + 结构双确认
- 只要 `MA20 > MA50` 且最近 10 bar 内出现 HH 结构 → 多头；对称做空。
- 比策略 A 更粗但更稳，适合日线。

## 6. 代码模块设计

```text
cta/strategy/common/regime/
├── __init__.py
├── trend.py            # 趋势打分、方向、强度
├── adx.py              # ADX 实现（若 pa_ 未提供）
└── efficiency_ratio.py
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class TrendState:
    score: float              # [-1, 1]
    direction: int            # -1/0/1
    strength: float           # [0, 1]
    maturity: str             # 'young'|'mature'|'late'

def compute_trend_state(
    df: pd.DataFrame,
    ma_fast: int = 20,
    ma_slow: int = 50,
    adx_n: int = 14,
    der_n: int = 20,
) -> pd.DataFrame:
    """
    输入：含 OHLCV + pa_* 特征的分钟/日线 df。
    输出：每根 bar 一行，列为 ['trend_score','trend_dir','trend_strength','trend_maturity']。
    """
    ...

def classify_maturity(
    score_series: pd.Series,
    lookback: int = 60,
) -> pd.Series:
    """
    根据 trend_score 的历史分布给趋势打"成熟度":
      young  : 进入趋势 < 20 bar
      mature : 20-80 bar
      late   : > 80 bar 且 score 开始回落
    """
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：`trend_score > 阈值`时段的平均收益、Sharpe、胜率、盈亏比、平均持仓 bar、正确识别率（对比 HH/LL 结构）、误判率
- **分层评估**：按品种（黑色 vs 农产品）、按年份、按 maturity（young 的胜率应最高）
- **稳健性检验**：
  1. ADX 权重 ±20% 不改变 top-5 品种的分数排序
  2. 只用 3 个指标（去掉 1 个）结果下降 ≤ 20%
  3. 日线 → 60m 保持方向一致率 ≥ 70%

## 8. 常见错误

- 用 MA 交叉当趋势信号 → 震荡中来回穿越
- 只在 "长期趋势方向" 上开仓但忽略 MTF 反转
- ADX 上升就认为 "趋势开始"，实际 ADX 滞后 10-20 bar
- 参数 MA=20/50 套用所有品种（PP 和 RB 该用不同周期）

## 9. 迭代方向

- v1：指标加权和
- v2：换成 logistic 回归 / XGBoost 打分（见 `09_ml_augmentation/02_regime_classifier.md`）
- v3：把 maturity 引入 sizing（young 重仓 / late 减仓）
- v4：跨周期联合（LTF 用 MTF 的趋势状态作 context）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/04_volatility_regime.md`（趋势需要波动率支持）
- **被依赖**：`03_trend_strategies/` 全部 5 个 skill、`06_filtering_and_scoring/03_context_score.md`
- **互补**：`01_market_regime/05_multi_timeframe_alignment.md`（多周期趋势共振）、`02_price_action/03_breakout_pullback_continuation.md`（趋势中的延续点）
- **替代**：震荡下本 skill 输出 score ≈ 0，应换到 `04_range_strategies/`
