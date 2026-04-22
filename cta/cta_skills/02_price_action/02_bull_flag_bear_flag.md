# 多旗 / 空旗 / Bull Flag / Bear Flag

> 归属章节：`02_price_action/` · 前置技能：`01_market_regime/01_trend_detection.md`、`04_high1_high2_low1_low2.md` · 关联：`cta/feature/price_action_advanced.py`

## 1. Skill 定义

识别趋势中**短暂回调构成的"旗形"**，在旗形结束向原趋势方向突破时跟进。多头趋势中的回调 = bull flag；空头趋势中的反弹 = bear flag。

## 2. 解决什么问题

- 痛点 1：趋势中的入场点少而贵，错过就追涨杀跌。
- 痛点 2：回调后的加仓时机不清晰，无标准规则。
- 痛点 3：把深回调误认为反转 → 反向开单 → 被趋势抽打。
- 增量价值：提供**高胜率、低风险**的趋势延续入场点，适合做加仓与基础仓。

## 3. 适用市场 / 适用场景

- **品种类别**：趋势性强的品种（黑色、部分能化）
- **周期**：minute60 / minute30 / minute15（日线也可，但次数少）
- **行情状态前提**：必须先是趋势态（trend_score > 0.5 同向）
- **不适用场景**：震荡 / 压缩（没有"原方向"可延续）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| leg 方向 / 强度 | 最近 impulse leg 的斜率 + ATR 标准化长度 | — | leg_len / ATR > 3 | `cta/feature/price_action_advanced.py::pa_leg_*` (若未实现 TODO) |
| flag 深度 | 回调幅度 / 原 leg 幅度 | — | 30%-50% | TODO |
| flag 形态 | H1/H2/L1/L2 系列 | — | 出现 H1 / L1 | `cta/feature/price_action.py::pa_h1_l1_*` |
| 斜率收敛 | 回调中的斜率 vs 原趋势斜率 | — | 反向斜率 < 原斜率/2 | TODO |
| 成交量萎缩 | 回调期 volume vs 原 leg | — | 回调 vol / leg vol < 0.8 | 计算 |

## 5. 常见策略映射

### 策略 A：bull flag breakout（Brooks 经典）
- **信号定义**：
  ```text
  1. 原趋势: 过去 M bar trend_score > 0.5（做多）
  2. 最近 N=5..20 bar 出现 leg 回调：
       - 回调幅度 in [0.3, 0.5] × leg
       - 回调中出现 H1 (higher low 的重新往上 break high)
  3. flag 下沿 = min(low[-N:])
     flag 上沿 = max(high 除了最新 impulse 外)
  4. signal_bar = 向上 break flag 上沿的 bar
  ```
- **开仓触发**：signal_bar 下一根 bar 开盘 stop 单于 `signal_bar.high`。
- **止损规则**：`signal_bar.low - tick`（通常 ≈ 1R）。
- **平仓规则**：
  - 目标 = leg 长度（"measured move"）
  - 若回到 flag 内 → 失败，立即退
  - 2R trailing
- **仓位规模**：0.3% 权益 ÷ 止损距离。
- **失败模式**：趋势末期 → flag 越来越浅但 break 后立刻回落（见 `02_price_action/05_failed_breakout.md`）。

### 策略 B：加仓旗形
- 已持有基础多头仓位，bull flag 出现时加仓 50%。

## 6. 代码模块设计

```text
cta/strategy/flag_trader/
├── __init__.py
├── signals.py          # detect_bull_flag / detect_bear_flag
├── filters.py          # require_htf_bias / require_leg_strength
├── risk.py
└── run_backtest.py
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class FlagSetup:
    valid: bool
    kind: Literal['bull','bear']
    flag_low: float
    flag_high: float
    leg_length_atr: float
    pullback_ratio: float
    structure_tag: str     # 'H1','H2','L1','L2' 等

def detect_flag(
    df: pd.DataFrame,
    lookback_leg: int = 30,
    lookback_pullback: int = 20,
    pullback_min: float = 0.3,
    pullback_max: float = 0.55,
) -> pd.DataFrame:
    """
    返回每根 bar 的 FlagSetup 列。
    依赖 pa_leg_* / pa_h1_l1_* 特征。
    """
    ...

def flag_breakout_trigger(
    setup: FlagSetup,
    next_bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """bull flag 向上突破 or bear flag 向下突破的入场价。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：胜率 / 盈亏比 / 平均持仓 / 目标到达率 / flag 失败率 / 平均 R / Sharpe / 与"裸趋势跟随"相比 PnL 增量
- **分层评估**：按品种、按 H1/H2/L1/L2 结构类型、按 pullback_ratio 桶
- **稳健性检验**：
  1. pullback_min/max ±0.1，胜率稳定
  2. 只用 H1 / H2 时的差异
  3. 日线 vs 30m 的信号一致率

## 8. 常见错误

- 没确认 HTF 方向 → 在下跌趋势中做 bull flag
- 回调太深（> 60%）仍然算 flag → 实为反转初期
- 用 RSI / MACD 确认 flag → 滞后，Brooks 派不用
- 将 3 根 bar 的 micro pullback 当 flag → 噪声

## 9. 迭代方向

- v1：规则识别
- v2：leg / pullback 相似度的 shape matching
- v3：ML 分类（flag vs pullback vs 反转）
- v4：多周期 flag 联动（HTF 的 flag + LTF 的 tight range）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/01_trend_detection.md`、`02_price_action/04_high1_high2_low1_low2.md`
- **被依赖**：`03_trend_strategies/05_trend_hold_and_trailing.md`（flag 是加仓与 trailing 的绝佳点）
- **互补**：`02_price_action/03_breakout_pullback_continuation.md`（flag 是其特例）
