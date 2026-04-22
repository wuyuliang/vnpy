# H1/H2/L1/L2 结构

> 归属章节：`02_price_action/` · 前置技能：无 · 关联：`cta/feature/price_action.py::pa_h1_l1_*`

## 1. Skill 定义

Al Brooks 的**价格微结构基石**：
- **H1**：向上反弹过程中，第一次高于前一个 bar 高点的 bar。
- **H2**：H1 之后再次高于某个高点的 bar（通常是反弹第二次尝试）。
- **L1 / L2**：对称，向下用法。

H1/H2/L1/L2 是判定"回调是否结束"、"趋势是否延续"、"反转是否成立"的最小语义单位。

## 2. 解决什么问题

- 痛点 1：回调里在哪个 bar 入场？有没有客观标准？
- 痛点 2：不知道是"二次尝试失败"还是"反转成立"。
- 痛点 3：看图判断 → 不可自动化。
- 增量价值：所有回调 / 反转策略都能用 H1/H2/L1/L2 作为**触发器**。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：minute60 / minute30 / minute15 / minute5（日线也有但更稀）
- **行情状态前提**：无（中性原语）
- **不适用场景**：数据断层 / 主力切换首根 bar

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| pa_h1_signal | 本 bar 是 H1 | — | True/False | `cta/feature/price_action.py::pa_h1_signal_*` |
| pa_h2_signal | 本 bar 是 H2（前序已有 H1） | — | True/False | `cta/feature/price_action.py::pa_h2_signal_*` |
| pa_l1_signal | 本 bar 是 L1 | — | True/False | `cta/feature/price_action.py::pa_l1_signal_*` |
| pa_l2_signal | 本 bar 是 L2 | — | True/False | `cta/feature/price_action.py::pa_l2_signal_*` |
| H2 深度 | H2 距离 H1 高点的距离 / ATR | — | > 0 即"破 H1 高" | 计算 |
| 结构序列 | 最近 N bar 的 H1/H2/L1/L2 序列 | N=20 | — | 聚合 pa_ 特征 |

（具体列名以 `cta/feature/price_action.py` 为准，若只有 `pa_h1_*` 而无 `pa_h2_*` 则标 TODO）

## 5. 常见策略映射

### 策略 A：H2 多头延续（bull flag 微结构）
- **信号定义**：
  ```text
  1. HTF 多头 (trend_score > 0.5)
  2. 最近 5..15 bar 有回调
  3. 出现 pa_h1_signal (bar t0)
  4. bar t1..tK 之间又出现 pa_h2_signal
  enter = H2 成立的下一根 bar stop 单于 H2.high
  ```
- **开仓触发**：`H2.high + tick` 的 stop 单，入场即 1R ≈ H2 自身范围。
- **止损规则**：H2 的 low − tick。
- **平仓规则**：标准 2R 平半、trailing 余下；若出现 L2 → 退出。
- **仓位规模**：0.3% 权益。
- **失败模式**：H2 出现但随后 L1 出现并 break → 反转概率大。

### 策略 B：L2 空头延续（bear flag 微结构）
- 对称。

### 策略 C：H2 后的 L1/L2 反转（见 `05_failed_breakout.md`）

## 6. 代码模块设计

```text
cta/strategy/common/pa/
├── hl_structure.py            # H1/H2/L1/L2 聚合与序列
└── hl_entry.py                # 触发价与止损
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class HLSignal:
    kind: Literal['H1','H2','L1','L2']
    bar_idx: int
    reference_high: float       # H2 break 掉的 prior high
    reference_low: float

def detect_hl_signals(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    消费 pa_h1_signal / pa_h2_signal / pa_l1_signal / pa_l2_signal 等列，
    返回带有 kind / reference 的结构化表。
    """
    ...

def resolve_hl_entry(
    signal: HLSignal,
    next_bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """根据 kind 返回 {'side', 'trigger', 'stop'} 或 None。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：H1/H2/L1/L2 各自的命中率 / 胜率 / 平均盈亏比 / 平均持仓 / 信号次数分布 / 与 HTF 方向一致性 / Sharpe
- **分层评估**：按 HTF 方向 / 按形态上下文（flag / pullback / 反转）/ 按品种
- **稳健性检验**：
  1. 只用 H2/L2（更强信号）vs 用 H1/L1 的差异
  2. 不同周期结果一致率
  3. 在无 HTF 过滤下的稳健性

## 8. 常见错误

- 混淆"bar 是 H1"与"pattern 完成" → H1 一出现就入场会太早
- 连续多个 H1 不等 H2 → 胜率偏低
- 忽略前序结构（如已经 3 个 H）→ H4 胜率骤降
- 低流动性 bar 产生的假 H/L

## 9. 迭代方向

- v1：特征已经在仓库
- v2：把 H1-H4 序列作为 ML 特征喂入（见 `09_ml_augmentation/01_trade_filter_model.md`）
- v3：加入 ii / i 下 micro pattern（见 `cta/feature/price_action.py` 中的 inside bar 特征）
- v4：跨周期 HL 一致性打分

## 10. 与其他 Skills 的关系

- **依赖**：无（基础原语）
- **被依赖**：`02_price_action/02_bull_flag_bear_flag.md`、`02_price_action/03_breakout_pullback_continuation.md`、`02_price_action/05_failed_breakout.md`、`03_trend_strategies/05_trend_hold_and_trailing.md`
- **互补**：`01_market_regime/05_multi_timeframe_alignment.md`（HL 在 LTF 上用）
