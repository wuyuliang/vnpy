# 假突破反转 / Failed Breakout

> 归属章节：`02_price_action/` · 前置技能：`01_tight_range_breakout.md`、`04_high1_high2_low1_low2.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

识别**已经突破 range 但迅速被打回**的反转场景，利用"突破失败被否定"的心理劣势反向入场。典型 Brooks 反转 setup，在震荡中尤其有效。

## 2. 解决什么问题

- 痛点 1：追突破反复被抽打 → 现在反向做。
- 痛点 2：震荡中反转信号多，无客观标准。
- 痛点 3：假突破的止损应放哪？缺统一规则。
- 增量价值：把"追突破被抽打的那帮人"的钱赚过来，在震荡市尤其稳定。

## 3. 适用市场 / 适用场景

- **品种类别**：全部，尤其是震荡强的有色、部分能化
- **周期**：minute30 / minute15 / minute5
- **行情状态前提**：震荡态（`01_market_regime/02_range_detection.md` 的 `range_score > 0.6`）或趋势末期
- **不适用场景**：明确趋势中段、事件驱动初期

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 突破失败 bar | close 回到 range 内，虽 high/low 超出过 | — | True/False | TODO: `pa_failed_break_*`（建议补） |
| 回撤速度 | K bar 内回到 range 内的 K | — | K ≤ 3 | 计算 |
| 反向 signal bar | 向 range 内方向的 pa_h1/l1 | — | 出现 | `cta/feature/price_action.py::pa_h1_l1_*` |
| 失败幅度 | 突破后 bar 的 max/min 到 range 边界的距离 / ATR | — | < 1.5 ATR | 计算 |
| 二次测试标志 | 在 K 小时内再次尝试突破并失败 | — | 出现 → 反转概率升高 | TODO |

## 5. 常见策略映射

### 策略 A：High-2 failed breakout（顶部假突破反转）
- **信号定义**：
  ```text
  1. 震荡态 range_score > 0.5
  2. bar t0: close > range_upper (突破)
  3. bar t1..t3 之一: close < range_upper (被否定)
  4. 反向出现 L1 / L2
  enter = L2.high 下一根 bar 的 sell stop 单
  ```
- **开仓触发**：`L2.low − tick` sell stop。
- **止损规则**：`max(high[t0..tL2])` + tick（即突破高点上方）。
- **平仓规则**：
  - 目标 1：range 中枢
  - 目标 2：range 另一沿
  - 1R trailing
- **仓位规模**：0.25% 权益（反转胜率高但盈亏比 1.2-1.5，仓位略小）。
- **失败模式**：真突破后的回踩被误识别为"假突破"。

### 策略 B：Low-2 failed breakout（底部假突破反转）
- 对称，做多。

## 6. 代码模块设计

```text
cta/strategy/failed_breakout/
├── signals.py
├── filters.py        # 避开强趋势、事件窗
├── risk.py
└── run_backtest.py
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class FailedBreakoutSetup:
    valid: bool
    side: Literal['long','short']    # 反转方向
    extreme_level: float              # 突破后最极端价
    range_boundary: float
    confirm_bar_idx: int              # 否定 bar 索引

def detect_failed_breakout(
    df: pd.DataFrame,
    range_df: pd.DataFrame,
    max_confirm_bars: int = 3,
) -> pd.DataFrame:
    """
    输入: 行情 df + 已经由 range_detection 给出的 range 上下沿。
    输出: 每根 bar 的 FailedBreakoutSetup 字段列。
    """
    ...

def failed_breakout_entry(
    setup: FailedBreakoutSetup,
    next_bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """给出反向入场的 stop 单信息。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：胜率 / 盈亏比 / 平均持仓 / 真假反转正确率 / 与正向突破策略的互补性（对冲效果）/ Sharpe / 最大连亏 / 每日触发次数
- **分层评估**：按品种、按震荡强度（range_score 桶）、按失败幅度
- **稳健性检验**：
  1. max_confirm_bars = 2/3/5 的胜率变化
  2. 在纯趋势时段本 skill 应显著亏损（用于 regime 一致性验证）
  3. 成本 ×2 后仍盈利

## 8. 常见错误

- 在趋势中做 failed breakout → 反向被趋势碾压
- 止损没放在突破极值外 → 反复被二次测试打掉
- 没过滤事件驱动（OPEC 日）→ 假突破率被扭曲
- 把真突破的小幅回踩当假突破

## 9. 迭代方向

- v1：规则版
- v2：与 `03_trend_strategies/` 的突破策略做 **portfolio combine**（一边做趋势一边做反转，自动对冲）
- v3：ML 模型学"哪些失败是反转信号"
- v4：二次测试（double-top / double-bottom）识别增强

## 10. 与其他 Skills 的关系

- **依赖**：`02_price_action/04_high1_high2_low1_low2.md`、`01_market_regime/02_range_detection.md`
- **被依赖**：`04_range_strategies/03_false_breakout_reversal.md`（本 skill 的策略版本）
- **互补 / 反面**：`02_price_action/01_tight_range_breakout.md`、`03_trend_strategies/01_donchian_breakout.md`
