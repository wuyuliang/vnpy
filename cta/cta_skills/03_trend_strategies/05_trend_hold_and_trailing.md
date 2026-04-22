# 趋势持仓与移动止损 / Trend Hold & Trailing

> 归属章节：`03_trend_strategies/` · 前置：`02_price_action/06_micro_channel_trend_channel.md`、`01_market_regime/01_trend_detection.md` · 关联：`cta/strategy/brooks/core/risk/stops.py`

## 1. Skill 定义

已开趋势单之后，**如何抓住大行情、不被回撤震出**的技艺集合：分批止盈、移动止损、加仓、失败快退。是把"入场策略"兑现为"大 R 收益"的关键环节。

## 2. 解决什么问题

- 痛点 1：入场做对、出场太早，拿不住大趋势。
- 痛点 2：trailing 过紧 → 被正常回调震出；过松 → 利润回吐。
- 痛点 3：加仓时机 / 规模缺乏系统规则。
- 增量价值：让策略的盈亏比从 1.5 上升到 3+，是长期稳定盈利的分水岭。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：已成立 / 正在进行的趋势
- **不适用场景**：本 skill 不负责"发现趋势"，只管理已开仓

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 最近摆动低/高 | 最近 N bar low/high | N=5/10 | — | `cta/feature/price_action.py::pa_swing_*` |
| Chandelier stop | `max_high_since_entry - k*ATR` | k=3 | — | TODO |
| ATR trailing | 移动 `price - k*ATR` | k=2/3 | — | `cta/strategy/brooks/core/risk/stops.py` |
| R 倍数 | `(price - entry) / initial_risk` | — | 1R 分批、3R trailing | 计算 |
| 结构止损 | 最近 L1/L2 极值外 | — | — | `pa_l1_l2_*` |

## 5. 常见策略映射

### 策略 A：分级 trailing（Brooks 风）
- **入场后 4 阶段**：
  1. `进场 → 1R`：固定止损（initial stop）
  2. `1R → 2R`：止损上移到 break-even，平 50% 仓位
  3. `2R → 3R`：Chandelier `high - 3*ATR`，其它 50% 继续持
  4. `3R 以上`：转 micro channel 跟踪（见 `02_price_action/06_micro_channel_trend_channel.md`）
- **开仓触发**：由其它入场 skill 决定。
- **止损规则**：见上述 4 阶段。
- **平仓规则**：
  - trailing 被击破 → 平
  - 反向结构（L2 for long）出现 → 平
- **仓位规模**：开仓由入场 skill 决定，trailing 阶段不加仓（加仓见策略 B）。
- **失败模式**：假跳空越过 trailing 价位 → 成交于更远位置，风险放大。

### 策略 B：Flag / BP 加仓
- 见 `02_price_action/02_bull_flag_bear_flag.md` 策略 B。
- 原则：加仓仓位 ≤ 基础仓 × 0.5；加仓后整体风险仍 ≤ 0.5% 权益。

### 策略 C：失败快退（failed move exit）
- 进场后 N=5 bar 内未到 +0.5R → 平出场（减小 MAE 时间风险）。
- 见 `cta/strategy/brooks/core/risk/stops.py`。

## 6. 代码模块设计

```text
cta/strategy/common/risk/
├── stops.py             # initial / break-even / chandelier / micro
├── pyramiding.py        # 加仓规则
└── fast_exit.py         # 失败快退
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class TrailingState:
    stop_price: float
    stage: Literal['initial','break_even','chandelier','micro_channel']
    R_multiple: float

def update_trailing(
    state: TrailingState,
    bar: pd.Series,
    entry_price: float,
    entry_stop: float,
    atr: float,
) -> TrailingState:
    """在每根 bar close 时更新 trailing 状态与止损价。"""
    ...

def decide_add_on(
    core_position_R: float,
    fresh_signal: dict,         # 来自 flag / BP
    max_add_count: int = 2,
) -> Optional[dict]:
    """返回 {'size': float} 或 None。"""
    ...

def fast_exit_if_stalled(
    bars_since_entry: int,
    current_R: float,
    max_bars: int = 5,
) -> bool:
    """未达到 +0.5R 且过 N bar → True 表示退。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：平均持仓 bar / 平均 R 倍数 / trailing 阶段保护 PnL / 加仓后 PnL / fast_exit 减少的 MAE / Sharpe / MFE 捕获率 / 大赢单占总 PnL 比
- **分层评估**：按 stage（initial / BE / chand / micro）、按品种、按趋势强度
- **稳健性检验**：
  1. chandelier k = 2/3/4
  2. fast_exit bar 数 3/5/10
  3. 关闭加仓后总 PnL 下降

## 8. 常见错误

- trailing 过紧被随机震出
- 加仓过多 → 回撤时放大亏损
- 未考虑交易成本，trailing 频繁调整多次 → 滑点累积
- 没有失败快退 → 占着仓位浪费机会成本

## 9. 迭代方向

- v1：手写 4 阶段
- v2：`cta/strategy/brooks/core/risk/stops.py` 已有组合版本
- v3：用 `09_ml_augmentation/03_mfe_mae_prediction.md` 的模型预测 MFE，动态决定 trailing k
- v4：场景化 trailing（趋势强 → 松，弱 → 紧）

## 10. 与其他 Skills 的关系

- **依赖**：入场 skill（`03_trend_strategies/01-04`、`02_price_action/01-03`）、`02_price_action/06_micro_channel_trend_channel.md`
- **被依赖**：所有趋势类策略（共用此 hold 层）
- **互补**：`07_position_and_portfolio/04_drawdown_control.md`（组合级别的风控与本 skill 单笔级别互补）
