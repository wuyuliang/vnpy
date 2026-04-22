# 订单执行 / Order Execution

> 归属章节：`10_live_ops/` · 前置：`01_signal_to_order.md` · 关联：vnpy gateway（本仓库外）

## 1. Skill 定义

把 **Order 对象** 通过 vnpy gateway 发到交易所，管理回报、撤单、重试、断线重连。保证"发出去的单都有交代"。

## 2. 解决什么问题

- 痛点 1：断网 / gateway 掉线 → 仓位状态未知。
- 痛点 2：挂单长时间不成交 → 价格已变。
- 痛点 3：回报延迟 → 重复下单。
- 增量价值：把"执行"从 best-effort 变成可对账过程。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：实盘
- **行情状态前提**：任何
- **不适用场景**：回测

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| fill_rate | 已成交 / 已挂出 | — | ≥ 95% | TODO |
| avg_latency | 下单到成交毫秒 | — | < 500ms | TODO |
| reject_rate | 被拒 / 挂出 | — | < 0.5% | TODO |
| retry_policy | 被拒 / 超时处理 | 最多 3 次 | — | TODO |
| reconcile_ts | 定时对账 | 每 5 分钟 | — | TODO |

## 5. 常见策略映射

### 规则 A：市价开 + 止损挂单
- 开仓：`market`，立即成交；记录成交价。
- 止损：立即 `stop` 挂出；成交即平仓。
- 超时 60s 未成交 → 撤单重试（挂限价）

### 规则 B：限价挂单 + 定时移价
- 挂 limit；每 30s 如果未成交且距最新价超 2 ticks，撤单重挂最新价 ± 1 tick。
- 总重试 ≤ 5 次，然后转 market。

### 对账 / Reconciliation
- 每 N 分钟：`local_positions ≡ broker_positions`
- 不一致 → 触发告警 + 暂停新单

## 6. 代码模块设计

```text
cta/strategy/common/live/exec/
├── executor.py            # 发送 / 重试
├── event_router.py        # on_order / on_trade / on_error
├── reconciler.py          # 本地 vs 券商
└── kill_switch.py         # 紧急下线
```

```python
from dataclasses import dataclass
from typing import Callable
import pandas as pd

@dataclass
class ExecConfig:
    limit_retry_seconds: int = 30
    max_retries: int = 5
    market_fallback: bool = True
    reconcile_interval_sec: int = 300

def submit_order(order, gateway, cfg: ExecConfig) -> str:
    """返回 broker_order_id。"""
    ...

def on_order_event(evt, state):
    ...

def reconcile(
    local_positions: dict,
    broker_positions: dict,
) -> list[dict]:
    """返回差异条目。"""
    ...

def kill_switch(reason: str) -> None:
    """撤所有单，禁止新单，保留已有仓位等人工决定。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：fill_rate / 被拒率 / 平均成交延迟 / 撤单重挂次数 / 差异修正次数 / kill_switch 触发次数 / 市价兜底比例
- **分层评估**：按品种 / 时段 / 交易所
- **稳健性检验**：
  1. 网络断 30s 恢复后仓位一致
  2. gateway 重启后订单状态同步
  3. 交易所拒单后重试路径

## 8. 常见错误

- on_trade 没做幂等 → 重复加仓
- 撤单后没确认就重发 → 双倍成交
- kill_switch 不撤 stop → 单边裸仓
- reconcile 频率太低 → 差异累积

## 9. 迭代方向

- v1：market + stop 简单执行
- v2：限价重挂 + fallback
- v3：执行成本优化（TWAP / VWAP / adaptive）
- v4：统一 OMS 层（多账户 / 多策略）

## 10. 与其他 Skills 的关系

- **依赖**：`10_live_ops/01_signal_to_order.md`
- **被依赖**：`10_live_ops/03_monitoring_and_alerting.md`
- **互补**：`08_data_and_backtest_infra/04_event_driven_backtest.md`（共用 order 模型）
