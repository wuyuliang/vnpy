# 假突破反转 / False Breakout Reversal

> 归属章节：`04_range_strategies/` · 前置：`02_price_action/05_failed_breakout.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

`02_price_action/05_failed_breakout.md` 的**策略化版本**：把假突破识别变成完整可交易策略（信号 + 开仓 + 止损 + 平仓 + 仓位）。在震荡主导时段运行。

## 2. 解决什么问题

- 痛点 1：突破追单经常被打回，不如直接做反转。
- 痛点 2：震荡策略需要一个"低频但高质量"的信号源。
- 痛点 3：与边界反转策略高度相关，需要差异化。
- 增量价值：本 skill 专注于"突破失败成立后入场"，比边界反转的"靠近边界即入场"更严格，胜率更高。

## 3. 适用市场 / 适用场景

- **品种类别**：全部（震荡品种尤其）
- **周期**：minute30 / minute15 / minute5
- **行情状态前提**：震荡或趋势末期
- **不适用场景**：已确认的趋势中段

## 4. 核心指标

见 `02_price_action/05_failed_breakout.md` 的指标表。本 skill 额外：

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 二次测试 | K 小时内再破同方向又失败 | K=60m | 出现则更高胜率 | TODO |
| 否定速度 | 突破后 N bar 内关闭回 range 内 | N=1-3 | 越快越强 | 计算 |
| 原震荡强度 | range_score at 突破前 | — | > 0.6 | `02_range_detection` |

## 5. 常见策略映射

### 策略 A：Failed Breakout Reversal（FBR）
- **信号定义**：
  ```text
  range_df = range_detection(df)
  for each bar:
    if bar exceeds upper(range) but close < upper within 3 bars:
      等待 pa_l1 or pa_l2 出现
      signal = L2 成立
  ```
- **开仓触发**：`L2.low - tick` sell stop。
- **止损规则**：`max(high[突破后的 bar..L2 confirm bar]) + tick`。
- **平仓规则**：
  - 目标 1：range 中枢
  - 目标 2：range 下沿
  - trailing：2R 后启动
- **仓位规模**：0.25% 权益。
- **失败模式**：突破后并非假突破，而是延伸趋势 → stop 被扫。

### 策略 B：仅在 HTF 震荡态时运行
- HTF 也必须 `range_score > 0.5`，否则关闭本 skill。

## 6. 代码模块设计

```text
cta/strategy/false_breakout_reversal/
├── signals.py
├── filters.py
├── risk.py
├── run_backtest.py
└── config.yaml
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class FBRTrade:
    side: Literal['long','short']
    entry: float
    stop: float
    targets: list[float]

def build_fbr_trade(
    df: pd.DataFrame,
    range_df: pd.DataFrame,
    bar_idx: int,
    tick_size: float,
    htf_range_ok: bool,
) -> Optional[FBRTrade]:
    ...

def manage_fbr_exit(
    trade: FBRTrade,
    bars_since_entry: int,
    current_price: float,
) -> str:
    """返回 'hold'|'partial'|'full_exit'|'stop_out'。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：胜率 / 盈亏比 / 与正向突破策略的对冲效果 / 每日触发频率 / Sharpe / MAE / 目标到达率 / 成本敏感性
- **分层评估**：按品种 / 按震荡强度 / 按否定速度（1 bar / 2 bar / 3 bar）
- **稳健性检验**：
  1. N_confirm = 1/2/3 胜率单调性
  2. 加入二次测试过滤后胜率提升但次数下降
  3. 关闭 HTF 过滤后对比

## 8. 常见错误

- 把任何 close 回 range 内的 bar 都当假突破 → 没等 L1/L2 确认
- 止损放在范围中枢 → 太近
- 趋势中运行本策略 → 被趋势反复扫
- 盈亏比当作 3+ → 实际 1.3-1.7

## 9. 迭代方向

- v1：规则
- v2：+ 二次测试过滤
- v3：ML 模型打分（failed_breakout 质量）
- v4：与 `02_price_action/05_failed_breakout.md` 合并实现

## 10. 与其他 Skills 的关系

- **依赖**：`02_price_action/05_failed_breakout.md`、`01_market_regime/02_range_detection.md`
- **被依赖**：`07_position_and_portfolio/05_portfolio_allocation.md`
- **互补**：`04_range_strategies/01_range_boundary_reversal.md`
