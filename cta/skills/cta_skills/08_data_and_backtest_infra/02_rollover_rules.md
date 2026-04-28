# 展期规则 / Rollover Rules

> 归属章节：`08_data_and_backtest_infra/` · 前置：`01_continuous_contract.md` · 关联：`cta/data_code/`

## 1. Skill 定义

定义**主力合约如何切换 / 何时展期 / 展期时仓位如何调整**。本 skill 既影响数据（连续合约拼接），也影响实盘（换月前平今仓开远月）。

## 2. 解决什么问题

- 痛点 1：不同品种换月节奏不同，需要统一规则。
- 痛点 2：回测用"持仓量最大"，实盘若执行晚会撞上近月到期。
- 痛点 3：展期过程中的滑点 / 成本被忽略。
- 增量价值：让回测 / 实盘在 roll 这件事上对齐。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：日频执行，但分钟级监控
- **行情状态前提**：任何
- **不适用场景**：无

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| active_by_oi | OI 最大合约 | 日频 | — | 计算 |
| active_by_vol | 成交量最大合约 | 日频 | — | 计算 |
| days_to_expiry | 距离到期日天数 | — | < 30 进入切换窗 | `cta/config/futures_meta.py` 维护 |
| roll_window | 展期时间窗 | 到期前 7-15 日 | — | — |
| roll_cost | 换月手续费 + 滑点估计 | — | — | `cta/config/futures_meta.py` |

## 5. 常见策略映射

### 规则 A：OI 换月 + 固定窗口
- **触发**：`active_by_oi` 切换且距到期 < 15 日。
- **执行**：
  - 在切换日 T 收盘前半小时，按比例把仓位从 old 移到 new
  - 回测：同一 bar 两笔模拟成交（close 旧 + open 新）
  - 实盘：拆分执行，避免大单冲击
- **成本**：2 × fee + 2 × slippage
- **失败模式**：主力尚未完成切换 → 过早 roll 失去流动性优势

### 规则 B：持仓量阈值（ratio）
- 新合约 OI / (old + new) > 0.55 时视为换月生效，当日执行。

## 6. 代码模块设计

```text
cta/data_code/rollover/
├── rules.py             # OI / vol / days_to_expiry 规则
├── executor.py          # 执行展期
└── ledger.py            # 记录 roll 事件
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

@dataclass
class RolloverAction:
    date: pd.Timestamp
    from_contract: str
    to_contract: str
    lots: int
    rule: Literal['oi','vol','days_to_expiry']
    estimated_cost: float

def decide_rollover(
    positions: list[dict],
    oi_snapshot: dict[str, int],
    days_to_expiry: dict[str, int],
    rule: Literal['oi','ratio'] = 'oi',
) -> list[RolloverAction]:
    ...

def execute_rollover(
    actions: list[RolloverAction],
    executor_fn,           # 实盘：下单回调；回测：模拟成交
) -> pd.DataFrame:
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：roll 事件次数 / 平均 roll 成本 / 总 PnL 被 roll 吃掉的比例 / 新合约 OI 占比分布 / 实盘 vs 回测一致率 / 漏 roll 次数
- **分层评估**：按品种 / 按年份
- **稳健性检验**：
  1. OI vs ratio 规则一致率
  2. roll_window 长短影响
  3. 成本敏感性

## 8. 常见错误

- 不做 roll → 近月到期后无行情
- roll 当天 close 同时完成旧开新 → 实盘难复制
- 没记录 roll ledger → 事后无法对账
- 忽略节假日（roll 日是节前最后交易日）

## 9. 迭代方向

- v1：OI + 固定窗口
- v2：多日渐进 roll（分 3 天）
- v3：基于价差的 roll 时点优化
- v4：跨品种同步 roll（组合视角）

## 10. 与其他 Skills 的关系

- **依赖**：`08_data_and_backtest_infra/01_continuous_contract.md`
- **被依赖**：`08_data_and_backtest_infra/03_transaction_cost_model.md`（roll 成本算进成本模型）、`10_live_ops/02_order_execution.md`
- **互补**：`cta/config/futures_meta.py` 元数据维护
