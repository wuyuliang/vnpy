# 信号到订单 / Signal to Order

> 归属章节：`10_live_ops/` · 前置：`07_position_and_portfolio/ 整章` · 关联：`cta/strategy/brooks/core/`

## 1. Skill 定义

把**策略层的"开多 RB 1 手"**转成**可下单的订单对象**（合约代码 / 数量 / 价格 / 止损单 / 开平标志），并保证回测 / 实盘用同一套代码。

## 2. 解决什么问题

- 痛点 1：回测里 signal 直接改 position，实盘必须走订单。
- 痛点 2：没处理"开/平/昨/今"标志 → 手续费多付。
- 痛点 3：止损挂单 vs 市价单的选择不统一。
- 增量价值：回测对账实盘一致性 ≥ 99%。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：任何
- **不适用场景**：纯研究阶段（无需）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| order_type | market / limit / stop | 止损用 stop，开仓用 limit | — | TODO |
| open_close_flag | 开 / 平 / 平昨 / 平今 | 按交易所规则 | — | `cta/config/futures_meta.py` 可扩展 |
| lots | 来自 sizing skill | — | ≥ 1 | — |
| contract_code | 近月主力 | 按 rollover 决策 | — | `08/02_rollover_rules.md` |
| valid_tif | time in force | GTC / IOC / FOK | — | TODO |

## 5. 常见策略映射

### 规则 A：双向订单生成
- **开仓**：limit @ 最近 3 根 bar high/low 中性价（减滑点）
- **止损**：stop @ 1.2 × ATR（随仓位同时挂）
- **止盈**：limit @ 2 × ATR（可选）

### 规则 B：开平昨今
- 上期所要求区分平昨 / 平今；大连 / 郑州 / 中金一般不要求。
- 订单生成时读 `config/futures_meta.py` 的 `exchange_rule` 字段。

## 6. 代码模块设计

```text
cta/strategy/common/live/
├── signal.py              # 策略输出的 Signal 对象
├── orderize.py            # Signal -> Order list
└── open_close_rule.py     # 开/平/昨/今
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

Side = Literal['long','short','flat']

@dataclass
class Signal:
    ts: pd.Timestamp
    symbol: str
    side: Side
    lots: int
    reason: str
    stop_price: float | None = None
    take_profit: float | None = None

@dataclass
class Order:
    ts: pd.Timestamp
    contract: str
    side: Side
    order_type: Literal['market','limit','stop']
    price: float | None
    lots: int
    open_close: Literal['open','close','close_today','close_yesterday']
    ref_signal_id: str

def orderize(
    sig: Signal,
    existing_positions: dict,
    meta: dict,
    active_contract: str,
) -> list[Order]:
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：订单类型分布 / 止损挂单触发率 / 实盘 vs 回测 PnL diff / 订单拒绝率 / 开平错分次数 / 挂单成交率
- **分层评估**：按品种 / 按订单类型
- **稳健性检验**：
  1. limit vs market 开仓的成本差
  2. 止损挂单 vs 事后市价的滑点差
  3. 不同 TIF 的成交率

## 8. 常见错误

- 平仓用 "open" 标志 → 被拒单
- 止损不同步挂 → 行情跳空时失控
- 下一 bar 才下单的回测改成当前 bar → 未来函数
- 合约代码写死主力 → 没展期

## 9. 迭代方向

- v1：market 开 + stop 平
- v2：limit 开 + OCO（one-cancel-other）
- v3：冰山单 / TWAP
- v4：基于 orderbook 的价位优化

## 10. 与其他 Skills 的关系

- **依赖**：`07_position_and_portfolio/ 整章`、`08_data_and_backtest_infra/02_rollover_rules.md`
- **被依赖**：`10_live_ops/02_order_execution.md`
- **互补**：`08_data_and_backtest_infra/03_transaction_cost_model.md`
