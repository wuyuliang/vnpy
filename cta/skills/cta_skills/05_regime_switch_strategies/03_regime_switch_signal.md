# Regime 切换信号 / Regime Switch Signal

> 归属章节：`05_regime_switch_strategies/` · 前置：`01-04 整章` · 关联：`cta/strategy/brooks/core/signal/`

## 1. Skill 定义

把趋势 / 震荡 / 压缩 / 扩张四种 regime 的识别聚合为**状态机 + 切换信号**，给出：
- 当前 regime（单一标签）
- 下一可能 regime 及概率
- 自上次切换以来的 bar 数（regime age）

给所有上游策略提供统一的"开关信号"。

## 2. 解决什么问题

- 痛点 1：各个 regime 指标输出分散，策略需自己聚合。
- 痛点 2：regime 切换的"时间戳"不明确，策略反应延迟。
- 痛点 3：没有 regime age 概念，无法感知成熟度。
- 增量价值：单点、单一 source of truth 的 regime 信号。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：所有
- **行情状态前提**：无
- **不适用场景**：新合约 < 100 bar

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| regime_label | 聚合 trend_score / range_score / vol_state | — | trend/range/compression/expansion/transition | TODO |
| regime_conf | 最大 score - 第二 score | — | > 0.2 视为确定 | TODO |
| regime_age | 自上次切换以来的 bar 数 | — | — | 计算 |
| transition_risk | 当前 regime_conf 和波动率变化组合 | — | > 0.5 进入 transition 状态 | TODO |
| HMM state | 隐马模型的后验 | K=4 状态 | — | TODO |

## 5. 常见策略映射

### 策略 A：简单规则状态机
- **信号定义**：
  ```text
  if vol_state == 'compression' and range_score > 0.5:
      regime = 'compression'
  elif vol_state == 'expansion' and abs(trend_score) > 0.5:
      regime = 'expansion_trending'
  elif trend_score > 0.5 and vol_state in {'high','normal'}:
      regime = 'trend_up'
  elif trend_score < -0.5:
      regime = 'trend_down'
  elif range_score > 0.6:
      regime = 'range'
  else:
      regime = 'transition'
  
  regime_age = bars since regime 发生变化
  ```
- **应用**：
  - `trend_up/down` → 启用 `03_trend_strategies/`
  - `range` → 启用 `04_range_strategies/`
  - `compression` → 启用 tight range 预埋（`02_price_action/01`）
  - `expansion_trending` → Donchian / ATR 突破
  - `transition` → 全部减半仓位，参考 `04_transition_risk_control.md`
- **仓位规模**：regime_age < 10 → 基础仓；10-40 → 增仓；> 40 → 减仓（成熟期风险）
- **失败模式**：regime 识别错误 → 错开策略。需日终 review 正确率。

### 策略 B：HMM 平滑
- 用 4 状态 HMM 学 `(trend_score, range_score, atr_pct)` 的后验，避免硬阈值跳变。

## 6. 代码模块设计

```text
cta/strategy/common/regime/
├── switch_machine.py
├── hmm.py                # 可选
└── registry.py           # regime -> 策略白名单
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

RegimeLabel = Literal[
    'trend_up','trend_down','range','compression',
    'expansion_trending','transition'
]

@dataclass
class RegimeState:
    label: RegimeLabel
    confidence: float
    age_bars: int
    last_switch_bar: int

def compute_regime(
    df: pd.DataFrame,
    trend_score: pd.Series,
    range_score: pd.Series,
    vol_state: pd.Series,
) -> pd.DataFrame:
    """每根 bar 返回 RegimeState 字段列。"""
    ...

def allowed_strategies(label: RegimeLabel) -> list[str]:
    """regime → 策略白名单。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：各 regime 持续 bar 分布 / 切换次数 / 切换误差率（跟后续 20 bar 比较）/ 每个 regime 下允许策略的 PnL / Sharpe / 转换期亏损 / 总成本
- **分层评估**：按品种、按年份、按 regime
- **稳健性检验**：
  1. 阈值 ±20% 的 regime 分布变化
  2. HMM vs 规则一致性
  3. 不同时间窗的 age 分布稳定

## 8. 常见错误

- 让 regime 单 bar 切换 → 噪声转 regime
- 策略白名单只写 trend / range 不覆盖 transition
- 忘记在实盘中记录 regime 轨迹，事后无法 debug
- regime 权重硬编码，不按品种调

## 9. 迭代方向

- v1：规则状态机
- v2：HMM
- v3：多周期 regime 融合（HTF regime 统摄 LTF）
- v4：ML 分类（`09_ml_augmentation/02_regime_classifier.md`）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/01-04 全部`
- **被依赖**：所有策略 skill 的 gate
- **互补**：`05_regime_switch_strategies/04_transition_risk_control.md`
