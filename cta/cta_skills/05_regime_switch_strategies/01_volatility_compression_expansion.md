# 波动率压缩 → 扩张 / Volatility Compression → Expansion

> 归属章节：`05_regime_switch_strategies/` · 前置：`01_market_regime/04_volatility_regime.md`、`01_market_regime/03_breakout_threshold_detection.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

波动率从**长期压缩**（ATR 百分位 < 20%）过渡到**扩张**（ATR 比值 > 1.3），往往伴随大趋势启动。本 skill 识别这一切换时机并**在临界 / 已扩张时切换策略**。

## 2. 解决什么问题

- 痛点 1：压缩期趋势策略空仓，错过扩张初段的暴利。
- 痛点 2：扩张后震荡策略继续跑，被打爆。
- 痛点 3：切换时间点主观判断，不可自动化。
- 增量价值：把"策略切换"的时机量化、工程化。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：day / minute60 / minute30
- **行情状态前提**：无（本 skill 就是识别状态）
- **不适用场景**：数据不足 252 bar 的新合约

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| ATR 百分位 | 过去 252 bar 分位 | — | < 20% = 压缩 | TODO |
| ATR 比值 | ATR(14)/ATR(60) | — | > 1.3 = 扩张 | `pa_atr_*` |
| BB 宽度百分位 | 过去 252 分位 | — | < 20% 压缩 | TODO |
| 压缩持续 bar | 连续满足压缩的 bar 数 | — | ≥ 30 视为"久压" | 计算 |
| 扩张确认 | 价格 close 突破近 20 bar 极值 + ATR 比值 > 1.3 | — | 同时满足 | 计算 |

## 5. 常见策略映射

### 策略 A：状态切换 gate
- **信号定义**：
  ```text
  state = 'compression' if ATR_pct < 0.2 and ATR_ratio < 0.7
           'expansion'   if ATR_ratio > 1.3 and close breaks N-bar extreme
           'normal'      else
  ```
- **应用**：
  - `compression`：关闭趋势策略，开启 tight range + 边界反转
  - `expansion`：关闭震荡策略，开启 Donchian/ATR breakout
  - `normal`：默认配置
- **仓位规模**：切换时新策略仓位仅 50%，确认 5 根 bar 后补齐。
- **失败模式**：扩张假信号（1 bar 扩张后回压缩），应在 N bar 内未跟进则回滚。

### 策略 B：VIX-like 组合指标
- 取研究池的平均 ATR 百分位作为"市场 VIX"，整体 compression/expansion 判断。

## 6. 代码模块设计

```text
cta/strategy/common/regime/
├── vol_state.py          # 已在 01_market_regime/04 的设计中
└── vol_transition.py     # 切换检测 + rollback
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

VolTransition = Literal['compression','expansion','normal']

@dataclass
class VolTransitionEvent:
    timestamp: pd.Timestamp
    new_state: VolTransition
    old_state: VolTransition
    confidence: float       # [0,1]

def detect_vol_transition(
    df: pd.DataFrame,
    pct_window: int = 252,
    persistence_bars: int = 5,
) -> pd.DataFrame:
    """
    返回每根 bar 的 state 列，以及转换事件表。
    要求 df 含 pa_atr_* / rolling_high/low。
    """
    ...

def rollback_if_false_switch(
    state_series: pd.Series,
    max_rollback_bars: int = 10,
) -> pd.Series:
    """短时切换（< 5 bar）回滚为前状态。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：各状态下的策略 PnL / 切换次数 / 假切换率 / 每次切换后 N bar 的 PnL 分布 / Sharpe / 总交易成本 / 策略开关的延迟 bar 数
- **分层评估**：按品种 / 按年份 / 按状态
- **稳健性检验**：
  1. persistence_bars = 3/5/10 的切换频率
  2. 阈值 < 20% / < 15% / < 25% 的状态分布
  3. OOS 状态分布相似性

## 8. 常见错误

- 切换过于敏感（1 bar 切换）→ 策略频繁开关
- 阈值按回测挑最优 → 过拟合
- 忽略品种差异（黑色 vs 农产品的压缩频率不同）
- 实盘时历史 252 bar 内含主力换月跳空，百分位失真

## 9. 迭代方向

- v1：硬阈值 + 持续性
- v2：HMM / 马尔可夫链建模状态转移
- v3：多品种联合判定（大类同步）
- v4：ML 分类（`09_ml_augmentation/02_regime_classifier.md`）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/04_volatility_regime.md`
- **被依赖**：`03_trend_strategies/` 与 `04_range_strategies/` 的 gate
- **互补**：`05_regime_switch_strategies/04_transition_risk_control.md`
