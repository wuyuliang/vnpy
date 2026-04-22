# 突破后回踩延续 / Breakout Pullback Continuation

> 归属章节：`02_price_action/` · 前置技能：`01_tight_range_breakout.md`、`02_bull_flag_bear_flag.md` · 关联：`cta/feature/price_action_advanced.py`

## 1. Skill 定义

在**初始突破**之后，第一次回踩突破位附近并被接住时做第二次入场（Brooks 所说的 "Breakout Pullback"）。比第一次突破胜率更高、风险更低，是趋势跟随的主要加仓点。

## 2. 解决什么问题

- 痛点 1：第一次突破有假突破风险，single entry PnL 波动大。
- 痛点 2：错过第一次入场后只能"追高"，R 倍数不划算。
- 痛点 3：没规则区分"健康回踩"与"突破失败前的回踩"。
- 增量价值：利用"假突破被否定后市场显现真方向"的逻辑，在更优价位跟进，盈亏比更好。

## 3. 适用市场 / 适用场景

- **品种类别**：趋势性品种（黑色 / 有色 / 能化）
- **周期**：minute60 / minute30 / minute15
- **行情状态前提**：前序必须有一次明确的突破（tight range / Donchian / ATR）
- **不适用场景**：震荡 / 压缩态；突破后立刻出现强反转（见 `05_failed_breakout.md`）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 原突破点 | 首次 close 越过 range 的 bar | — | 锁定价位 | `cta/feature/price_action.py::pa_is_signal_bar_*` |
| 回踩深度 | 回到原突破位的距离 / 突破后 leg | — | 40%-80% | TODO |
| 回踩中的 H1 / L1 | 回踩中出现反向的 H1 / L1 | — | 出现即有效 | `cta/feature/price_action.py::pa_h1_l1_*` |
| 支撑接住强度 | 回踩低点距 `breakout_level` 的距离 | — | < 0.5 ATR | 计算 |
| 成交量回升 | 回踩末期 vol / 回踩中段 vol | — | > 1.3 | 计算 |

## 5. 常见策略映射

### 策略 A：Breakout Pullback（BP）
- **信号定义**：
  ```text
  # 前序突破（锚点）
  brk_bar: 首次突破 range 上/下沿的 bar
  brk_level = range 上沿 (多) 或 下沿 (空)
  brk_direction = +1 / -1
  
  # 回踩识别
  在 brk_bar 之后 1..K=20 bar 内:
    若 回踩低点(多)/高点(空) 接近 brk_level (距离 < 0.5 * ATR14):
      在 LTF 上等待 signal_bar 重新向突破方向 break（出现 H1/L1）
  
  enter = signal_bar 反转成立
  ```
- **开仓触发**：`signal_bar.high / low` 上下一根 bar stop 单。
- **止损规则**：回踩极值 − tick。
- **平仓规则**：
  - 目标 = 原 leg 延伸至 "measured move"
  - 2R trailing
  - 若重新跌回 `brk_level` 另一侧 → 失败
- **仓位规模**：加到已有基础仓（若有）；或独立 0.3% 风险。
- **失败模式**：回踩过深 → 退化为反转；第 2 次突破无量。

### 策略 B：BP 加仓基础仓位
- 首次突破开 0.3% 风险仓；回踩 BP 再加 0.2% 风险仓；总敞口 ≤ 1% 权益。

## 6. 代码模块设计

```text
cta/strategy/breakout_pullback/
├── signals.py
├── pullback.py           # 识别回踩与接住
├── risk.py
└── run_backtest.py
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class PullbackSetup:
    valid: bool
    direction: Literal['long','short']
    breakout_level: float
    pullback_low: float   # 多头记录 low, 空头记录 high
    bars_since_breakout: int
    confirmed: bool       # 出现 H1/L1 反转

def detect_breakout_pullback(
    df: pd.DataFrame,
    breakout_df: pd.DataFrame,     # 前序突破锚点（另由 tight_range 或 Donchian 给出）
    max_bars_since_brk: int = 20,
    max_pullback_atr: float = 1.5,
) -> pd.DataFrame:
    """
    每根 bar 返回 PullbackSetup 各字段列。
    """
    ...

def pullback_entry_trigger(
    setup: PullbackSetup,
    next_bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """已确认 H1/L1 后下一根 bar 的 stop 单触发。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：BP 胜率 / 盈亏比 / 与首次突破对比的 R 改善 / 平均回踩深度 / 假 BP 率 / Sharpe / 平均持仓 / 每笔最大 MFE/MAE
- **分层评估**：按原突破类型（tight range vs Donchian）、按品种、按 HTF 方向
- **稳健性检验**：
  1. `max_bars_since_brk` 10/15/20，信号数变化 < 30%
  2. 回踩深度阈值变化对胜率的线性影响
  3. 合并首突破 + BP 的总 PnL 应高于单独首突破

## 8. 常见错误

- 把任何回到 `brk_level` 的 bar 都当 BP → 缺少反转确认
- 回踩后直接挂限价而不是 stop → 可能在趋势反转时被成交
- 不看 HTF → 本是反转起点却当 BP 做
- 首突破已 2 个 leg 了才找 BP → 趋势末期低胜率

## 9. 迭代方向

- v1：规则识别
- v2：加 setup_quality_score（见 `06_filtering_and_scoring/01_setup_quality_score.md`）
- v3：BP 与 flag 合并为通用 "continuation pattern" 检测
- v4：ML 分类（好 BP / 坏 BP）

## 10. 与其他 Skills 的关系

- **依赖**：`02_price_action/01_tight_range_breakout.md`（前序突破来源）、`02_price_action/04_high1_high2_low1_low2.md`（反转确认）
- **被依赖**：`03_trend_strategies/05_trend_hold_and_trailing.md`（加仓与持仓管理）、`06_filtering_and_scoring/04_risk_reward_score.md`
- **互补**：`02_price_action/05_failed_breakout.md`（BP 失败就是它的反面）
