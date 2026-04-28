# 事件驱动回测引擎 / Event-Driven Backtest

> 归属章节：`08_data_and_backtest_infra/` · 前置：`03_transaction_cost_model.md` · 关联：`cta/strategy/brooks/backtest/engine.py`

## 1. Skill 定义

用**按 bar 事件**的方式模拟交易：信号 → 订单 → 成交 → 持仓 → PnL。相比向量化回测，更接近实盘、能处理止损 / 挂单 / 多周期共振。

## 2. 解决什么问题

- 痛点 1：向量化回测把所有 close 一次成交 → 与实盘差别大。
- 痛点 2：止损 / 移动止盈需要逐 bar 判定。
- 痛点 3：多周期共振需要时间轴对齐。
- 增量价值：单一引擎既能跑回测，也能以 bar replay 方式做 dry-run。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部（推荐用最小周期 bar 驱动）
- **行情状态前提**：任何
- **不适用场景**：需要 tick 级微结构（本引擎只到 bar）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| fill_rule | 信号 bar 的成交价规则 | next_open / close | next_open 默认 | `cta/strategy/brooks/backtest/engine.py` |
| stop_fill | 止损触发时成交价 | 穿价最差 / 触发价 | 穿价最差 | 同上 |
| multi_tf_sync | 多周期同步规则 | 以 LTF 为时钟 | — | 同上 |
| bar_gap_tolerance | 允许的数据缺失 | — | < 3% | TODO |
| event_order | 事件优先级 | fill → stop → signal | — | 默认 |

## 5. 常见策略映射

### 引擎事件循环（伪码）
```text
for ts, bar in stream_bars():
    update_positions_pnl(bar)
    check_stops(bar)                # 先检查存量仓位的止损 / 止盈
    for sig in strategy.on_bar(bar):  # 生成新信号
        order = orderize(sig)
        simulate_fill(order, bar, next_bar)
    record_state(ts)
```

### 填充规则
- **default**：信号在 bar_t 的 close 产生，用 bar_{t+1} 的 open 成交。
- **stop_order**：挂出 stop，在下一 bar 如果触发则按 stop_price（最差穿价）成交。
- **limit_order**：下一 bar 如果 low ≤ limit ≤ high 则成交。

## 6. 代码模块设计

```text
cta/strategy/common/backtest/
├── engine.py              # 事件循环（已有 brooks 版本）
├── order_simulator.py     # 订单撮合
├── position_tracker.py    # 仓位与 PnL
└── report.py              # 统计
```

```python
from dataclasses import dataclass
from typing import Iterable, Callable
import pandas as pd

@dataclass
class EngineConfig:
    fill_rule: str = 'next_open'
    stop_fill: str = 'worst'
    cost_fn: Callable | None = None
    slippage_ticks: float = 1.5

def run_backtest(
    bars: pd.DataFrame,
    strategy,
    cfg: EngineConfig,
) -> dict:
    """
    返回 dict: trade_log / equity_curve / positions / stats
    """
    ...

def simulate_fill(
    order: dict,
    cur_bar: pd.Series,
    next_bar: pd.Series,
    cfg: EngineConfig,
) -> dict | None:
    """None 表示未成交。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：总 PnL / 胜率 / Sharpe / MDD / 年化 / 交易数 / 单笔最大回撤
- **分层评估**：按品种 / 年份 / signal type
- **稳健性检验**：
  1. fill_rule = next_open vs close 的差异
  2. slippage 加 50% 的鲁棒性
  3. 事件顺序（stop 先 / signal 先）切换

## 8. 常见错误

- 同 bar 信号 + 同 bar 成交 → 含未来函数
- 止损判断用 close 而非 high/low 穿透 → 漏止损
- 多周期 bar 没对齐时间戳 → 信号滞后一个 bar
- 持仓 mark-to-market 时错用 signal_price → equity 假光滑

## 9. 迭代方向

- v1：bar 级 + next_open 成交
- v2：支持 stop / limit / market
- v3：支持部分成交 + 挂单队列
- v4：tick 级引擎（与 vnpy 对齐）

## 10. 与其他 Skills 的关系

- **依赖**：`08_data_and_backtest_infra/03_transaction_cost_model.md`、`01_continuous_contract.md`
- **被依赖**：所有策略的回测、`10_live_ops/02_order_execution.md`（共用 order 模型）
- **互补**：`08_data_and_backtest_infra/05_trade_log_and_evaluation.md`
