# 连续合约构造 / Continuous Contract

> 归属章节：`08_data_and_backtest_infra/` · 前置：无 · 关联：`cta/data_code/futures_downloader.py`

## 1. Skill 定义

把每个商品的**近月合约序列**拼接为一条"连续合约"价格序列，供策略回测使用。中国商品主力换月频繁，如果直接用单合约会在到期后失去流动性。

## 2. 解决什么问题

- 痛点 1：单合约在到期前流动性下降 → 策略失真。
- 痛点 2：主力切换日有巨大跳空，若不处理会产生假信号。
- 痛点 3：不同拼接方法（back-adjusted / ratio / none）对回测 PnL 影响巨大。
- 增量价值：提供可靠、可复现、可与实盘对账的连续合约。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：所有
- **行情状态前提**：任何
- **不适用场景**：策略就是做"换月套利"

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| active_contract | 持仓量最高的合约 | 每日更新 | — | `cta/data_code/futures_downloader.py` |
| rollover_date | 主力切换日期 | — | — | 由 active_contract 判定 |
| adj_method | 拼接方法 | back-adjusted 默认 | — | TODO |
| gap_size | 换月日 old-close - new-open | — | 累加到历史 | 计算 |
| continuity_score | 拼接后价格序列的无断点检查 | — | 0 断点 | TODO |

## 5. 常见策略映射

本 skill 不是策略，而是**所有策略的数据层前置**：

### 做法 A：back-adjusted（推荐）
- **公式**：
  ```text
  在 rollover_date T:
    gap = old_contract_close_T - new_contract_open_T
    for all dates <= T:
      adjusted_price += gap（按 ratio 或绝对差调整）
  ```
- **应用**：策略看到的是连续光滑序列，PnL 可直接汇总。
- **注意**：报告时要存原始合约价 + 调整后价，便于对账。

### 做法 B：ratio adjusted（按比例）
- 适合跨越数年的长序列，避免价格变负。

### 做法 C：no-adjust（仅拼接不调整）
- 只用于人眼回放，不用于 PnL 计算。

## 6. 代码模块设计

```text
cta/data_code/continuous/
├── build_continuous.py
├── rollover_detect.py
└── adjust.py
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

AdjMethod = Literal['back','ratio','none']

@dataclass
class ContinuousSeries:
    df: pd.DataFrame             # 含 date, open, high, low, close, volume, contract_code, adj_factor
    roll_events: pd.DataFrame    # 换月事件表

def build_continuous(
    daily_all_contracts: pd.DataFrame,   # columns: date, contract, OHLCV, oi
    symbol_root: str,
    method: AdjMethod = 'back',
) -> ContinuousSeries:
    """
    输入: 每日各合约行情 (含 OI)
    输出: 连续合约 + 换月事件
    """
    ...

def detect_rollover(
    all_contracts: pd.DataFrame,
    rule: Literal['oi_max','vol_max'] = 'oi_max',
    min_days_in_contract: int = 5,
) -> pd.DataFrame:
    """返回每个 roll 日期：from_contract, to_contract, gap, method."""
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：gap 数量与大小分布 / 不同 adj_method 下 PnL 差异 / 换月日当日 PnL / 断点数 / 实盘对账一致性 / 存储占用
- **分层评估**：按品种 / 按年份 / 按 adj_method
- **稳健性检验**：
  1. back vs ratio 的 PnL 差
  2. roll 规则（oi vs vol）对齐
  3. 不同起始日期的拼接序列一致性

## 8. 常见错误

- 用简单"拼接"不调整 → PnL 含假跳空
- roll 日回测成交在 close 或 open → 与实盘不一致
- 不记录原始合约代码 → 无法对账 / 回溯
- adj_factor 丢失 → 次日重新计算时漂移

## 9. 迭代方向

- v1：back-adjusted + OI 切换
- v2：合约切换用近 3 日 OI 平滑
- v3：与分钟级数据对齐（roll 发生在 day，minute 数据需同步）
- v4：按持仓量加权的合成近月

## 10. 与其他 Skills 的关系

- **依赖**：无（底层）
- **被依赖**：所有回测 / 实盘 skill
- **互补**：`08_data_and_backtest_infra/02_rollover_rules.md`（规则细化）
