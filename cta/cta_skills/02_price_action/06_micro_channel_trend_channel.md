# Micro Channel / Trend Channel

> 归属章节：`02_price_action/` · 前置技能：`01_market_regime/01_trend_detection.md`、`04_high1_high2_low1_low2.md` · 关联：`cta/feature/price_action_advanced.py`

## 1. Skill 定义

**Micro channel**：连续 N 根 bar 全部 higher low（或全部 lower high）形成的极窄通道，极强短线方向性。
**Trend channel**：更宽的斜向通道（由 swing 点拟合的两条平行线）。

两者是 Brooks 对"趋势质量"的关键度量，也是 trailing 止损 / 分批获利的参考。

## 2. 解决什么问题

- 痛点 1：趋势中何时 trailing？何时加仓？缺乏客观锚点。
- 痛点 2：micro channel 过久后突破概率升高，但很少人能量化。
- 痛点 3：trend channel 打满（touch upper）是分批止盈的经典时机。
- 增量价值：给 trailing / 分批止盈 / 加仓提供可量化的几何锚点。

## 3. 适用市场 / 适用场景

- **品种类别**：趋势品种（黑色 / 部分能化）
- **周期**：minute60 / minute30 / minute15
- **行情状态前提**：趋势态（trend_score > 0.4）
- **不适用场景**：震荡、过渡、事件驱动

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| micro_channel_count | 连续 higher_low（或 lower_high）bar 数 | — | ≥ 6 视为 micro | `cta/feature/price_action.py::pa_micro_channel_*` |
| micro_channel_slope | 拟合斜率 | — | — | TODO |
| trend_channel_top | 由 swing high 拟合的上沿 | N=3 swing | — | `cta/feature/price_action_advanced.py::pa_channel_*`（若实现） |
| trend_channel_bot | 由 swing low 拟合的下沿 | — | — | 同上 |
| channel_touch_count | 过去 K bar 内触及通道沿的次数 | K=50 | — | TODO |

## 5. 常见策略映射

### 策略 A：Micro channel 延续
- **信号定义**：
  ```text
  micro_channel_count >= 6 且当前 bar 出现标准 H1 / H2（上涨）或 L1 / L2（下跌）
  ```
- **开仓触发**：H2.high / L2.low 下根 bar stop 单。
- **止损规则**：`micro_channel` 的底部回撤（通常 2-3 bar 反向极值）。
- **平仓规则**：
  - micro channel 被向下打破（出现 failed HL）→ 立刻退
  - 触及 trend_channel 上沿 → 平一半
- **仓位规模**：0.3% 权益。
- **失败模式**：micro channel 过长（count > 15）→ 突破概率上升，持续下注 EV 下降。

### 策略 B：Trend channel 触顶止盈
- 已持多单；当 close 触及 trend_channel_top 时平 50%，回落 1R 平剩余。

## 6. 代码模块设计

```text
cta/strategy/common/pa/
├── channel.py         # micro + trend channel 拟合
└── channel_entry.py
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class ChannelState:
    micro_count: int
    micro_direction: Literal['up','down','none']
    trend_top: float | None
    trend_bot: float | None
    touches_top: int
    touches_bot: int

def compute_channel_state(
    df: pd.DataFrame,
    swing_n: int = 3,
    touch_lookback: int = 50,
) -> pd.DataFrame:
    """每根 bar 返回 ChannelState 字段。"""
    ...

def channel_based_trailing(
    position_side: Literal['long','short'],
    state: ChannelState,
    last_high: float,
    last_low: float,
) -> Optional[float]:
    """
    基于 micro channel 的 trailing 止损价。
    long: 取最近 3 根 bar 的最小 low；若 micro 被破 → 立即平。
    """
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：micro 持续分布 / 平均 R 倍数 / trailing 保护的 PnL 份额 / trend channel 触顶止盈的成功率 / Sharpe / MFE/MAE / 与固定 2R trailing 的对比
- **分层评估**：按品种、按 micro count 桶（6-8 / 9-12 / 13+）、按趋势强度
- **稳健性检验**：
  1. 触顶止盈比例 50% / 70% / 100% 的 Sharpe 差异
  2. swing_n = 3 vs 5 的通道稳定性
  3. micro 定义（count 阈值）变化

## 8. 常见错误

- 误认 channel 为趋势核心 → 趋势本身是基础，channel 只是辅助
- 一出 micro 就加仓 → 末端风险大
- trend channel 用过窄窗口（swing_n=2）→ 通道振荡
- 触顶止盈过贪（等打满再平）→ 经常反转在第 3 次触碰

## 9. 迭代方向

- v1：规则识别
- v2：swing 拟合用 RANSAC / robust regression
- v3：通道的"熟度"score（新 / 成熟 / 末期）进入 sizing
- v4：与 `03_trend_strategies/05_trend_hold_and_trailing.md` 合并

## 10. 与其他 Skills 的关系

- **依赖**：`02_price_action/04_high1_high2_low1_low2.md`（swing 识别基于 H/L）
- **被依赖**：`03_trend_strategies/05_trend_hold_and_trailing.md`、`06_filtering_and_scoring/01_setup_quality_score.md`
- **互补**：`02_price_action/02_bull_flag_bear_flag.md`（flag 是 channel 的简化版）
