# 实盘原则 / Live Trading Principles

> 归属章节：`00_overview_methodology/` · 前置技能：`03_backtest_principles.md` · 关联：`cta/strategy/brooks/online/`

## 1. Skill 定义

从回测通过到实盘生效之间的**方法论约束**：dry-run 验证、灰度上线、对账、熔断、应急响应。不做这些就是把回测成绩当彩票。

## 2. 解决什么问题

- 痛点 1：直接用回测结果 all-in 实盘 → 一个工程 bug 就亏大。
- 痛点 2：无 dry-run → 实盘和回测信号不一致才发现。
- 痛点 3：无对账 → 策略认为持有 2 手，实际只有 1 手。
- 痛点 4：无熔断 → 策略连续亏损但不会自己停。
- 增量价值：回测 → 实盘的安全通道，把"未知的未知"降到可控。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：无（实盘原则与行情无关）
- **不适用场景**：纯研究阶段不适用；只有准备让一个策略上实盘时才引入

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 信号一致率 | 实盘信号 vs 回测信号匹配数 / 总数 | T+1 回放对比 | ≥ 99% | TODO: 待实现 |
| 成交一致率 | 实盘成交价 vs 预期 next_open 的偏差 | 滑点 tick 数 | ≤ 配置的 slippage_ticks + 1 | TODO |
| 持仓一致率 | 策略认为的 pos vs 券商 pos | 每 5 分钟校验 | = 100% | TODO |
| 最大日内回撤 | 日内 peak-to-trough NAV | — | ≥ 3% 触发预警，≥ 5% 熔断 | TODO |
| 连亏次数 | 连续亏损交易数 | — | ≥ 10 熔断 | TODO |

## 5. 常见策略映射

不产生策略，但约定**上线流程的 6 个阶段**：

### 阶段 1：dry-run（纸上交易）
- 用实时行情、实时信号，但不下单
- 持续 ≥ 5 交易日
- 验证：信号一致率、成交模拟一致率（假设 next_open 成交）

### 阶段 2：最小仓（1 手）灰度
- 只开 1 手，不分品种
- 持续 ≥ 10 交易日
- 验证：成交一致率、持仓一致率、对账

### 阶段 3：单品种小仓
- 回测仓位的 10%
- 持续 ≥ 1 个月

### 阶段 4：单品种目标仓
- 回测目标仓位
- 持续 ≥ 1 个月，观察相对 IS/OOS 的表现

### 阶段 5：多品种灰度
- 按研究池 A 档依次加入，每周加 1-2 个

### 阶段 6：满仓稳态
- 进入 daily_review 循环（`10_live_ops/04_daily_review.md`）

### 熔断规则
- 单日 NAV 回撤 ≥ 5% → 当日不再开新仓，仅管理已有持仓
- 连续 10 笔亏损 → 策略下线 review
- 策略信号一致率 < 95% → 立刻停策略

## 6. 代码模块设计

```text
cta/strategy/{name}/online/
├── runner.py           # dry-run / live 统一入口
├── live_strategy.py    # vnpy CtaTemplate 实现
├── reconciler.py       # 对账
├── circuit_breaker.py  # 熔断
└── alerter.py          # 飞书 / 邮件 通知
```

```python
from dataclasses import dataclass
from datetime import date

@dataclass
class LiveGateState:
    stage: int                       # 1..6
    trade_days_in_stage: int
    consecutive_losses: int
    signal_match_rate: float
    daily_drawdown: float

def check_circuit(state: LiveGateState, cfg: dict) -> str | None:
    """
    返回 None 表示正常；返回字符串表示触发的熔断原因。
    可能值：'daily_dd' / 'consecutive_loss' / 'signal_mismatch'。
    """
    ...

def reconcile_positions(
    strategy_positions: dict[str, int],
    broker_positions: dict[str, int],
) -> list[str]:
    """返回不一致的 symbol 列表。"""
    ...
```

## 7. 回测评估重点

实盘阶段的评估（与回测不同的点）：

- **必看指标（7）**：日收益 / 累计收益 / 当日回撤 / 信号一致率 / 成交一致率 / 持仓一致率 / 熔断触发次数
- **分层评估**：按品种、按时段（开盘 / 午盘 / 收盘）
- **稳健性检验**：
  1. 与回测同期对比：实盘 PnL 与用当日行情回测的 PnL 差距应 < 20%
  2. 断网重连后：策略能否恢复持仓认知
  3. 宕机重启后：dump / load 状态能否对齐

## 8. 常见错误

- dry-run 跳过直接上最小仓
- 没有对账：持仓错位 3 天才发现
- 熔断阈值按回测日波动设 → 回测未覆盖极端日则设偏松
- 飞书通知关掉未恢复 → 出事没人知道
- 策略代码改了没重启 dry-run → 认为还是原逻辑
- 实盘和回测用不同的 feature 计算路径 → 信号不一致的根源

## 9. 迭代方向

- v1：dry-run + 手工对账
- v2：自动对账 + 飞书告警
- v3：熔断进代码（`circuit_breaker.py`）
- v4：多策略 / 多账户编排（`10_live_ops/02_order_execution.md`）
- v5：A/B test：同策略两参数小仓并行跑，自动选优

## 10. 与其他 Skills 的关系

- **依赖**：`00_overview_methodology/03_backtest_principles.md`（没靠谱回测，谈不上实盘）
- **被依赖**：`10_live_ops/`（全章都是本 skill 的工程化）
- **互补**：`07_position_and_portfolio/04_drawdown_control.md`（熔断与回撤控制互相呼应）
