# 成交日志与评估报告 / Trade Log & Evaluation

> 归属章节：`08_data_and_backtest_infra/` · 前置：`04_event_driven_backtest.md` · 关联：`cta/strategy/brooks/core/trade_log.py`、`cta/strategy/brooks/backtest/reporter.py`

## 1. Skill 定义

回测 / 实盘必须产出**统一的成交日志**和**标准化评估报告**，作为复盘、机器学习标签、对账的唯一真源。

## 2. 解决什么问题

- 痛点 1：每个策略日志格式不同 → 无法横向比较。
- 痛点 2：日志缺字段 → 事后无法复盘（为什么开？止损为何这个价？）。
- 痛点 3：报告只有 PnL 曲线 → 无分层洞察。
- 增量价值：让"复盘"可自动化。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：任何
- **不适用场景**：无

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| trade_log_cols | 标准列集合 | — | 见下 | `cta/strategy/brooks/core/trade_log.py` |
| equity_curve | cumsum PnL | bar 级 | — | `reporter.py` |
| stats_block | 汇总指标 | 总 PnL / Sharpe / MDD / Calmar / 胜率 / 盈亏比 | — | `reporter.py` |
| per_symbol_breakdown | 按品种分解 | — | — | TODO |
| reasons_tag | 开平仓原因分类 | signal / stop / tp / rollover | — | TODO |

### 标准 trade_log 列
```text
trade_id, symbol, interval, open_ts, close_ts,
side, entry_price, exit_price, lots,
stop_price, take_profit, open_reason, close_reason,
gross_pnl, cost, net_pnl, mfe, mae,
feature_snapshot (dict / json)
```

## 5. 常见策略映射

### 报告 A：单策略单品种
- 字段：equity curve / 年度 PnL / 月度胜率 / 最大连亏 / 单笔 PnL 分布 / MFE-MAE 云图
- 输出：`cta/report/{strategy}/{symbol}/{date}.md` + pngs

### 报告 B：组合横评
- 多策略 × 多品种 heatmap
- Sharpe / MDD 排序
- 相关性矩阵
- 权重建议（接 `07_position_and_portfolio/05`）

## 6. 代码模块设计

```text
cta/strategy/common/report/
├── trade_log_schema.py
├── metrics.py
├── plots.py
└── reporter.py
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class ReportConfig:
    out_dir: str
    include_plots: bool = True
    per_symbol: bool = True
    per_year: bool = True

def summarize_trades(
    trade_log: pd.DataFrame,
    equity: pd.Series,
) -> dict:
    """返回 dict: total_pnl, annualized, sharpe, mdd, calmar, winrate, pf, max_consec_loss..."""
    ...

def write_report(
    trade_log: pd.DataFrame,
    equity: pd.Series,
    cfg: ReportConfig,
) -> str:
    """返回报告路径。同时写 markdown + png。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：Sharpe / Calmar / MDD / 年化 / 胜率 / 盈亏比 / 最大连亏 / MFE/MAE 分布
- **分层评估**：按品种 / 年份 / 信号类型 / 月份 / regime
- **稳健性检验**：
  1. 去掉最差 5% 交易后净值曲线
  2. 按年份重算 Sharpe 一致性
  3. feature_snapshot 分层后胜率差异

## 8. 常见错误

- trade_log 没存 feature_snapshot → 无法做 ML 标签
- equity_curve 不 reset on 跨年 → 年度对比失真
- 报告只给数字不给图 → 难以发现结构问题
- close_reason 没枚举值 → 分层评估不了

## 9. 迭代方向

- v1：固定 schema + markdown 报告
- v2：加 MFE/MAE / 特征分层
- v3：自动生成机会 vs 已抓机会 diff
- v4：报告接入 BI（Metabase / Superset）

## 10. 与其他 Skills 的关系

- **依赖**：`08_data_and_backtest_infra/04_event_driven_backtest.md`
- **被依赖**：`09_ml_augmentation/01_trade_filter_model.md`（标签来源）、`10_live_ops/04_daily_review.md`
- **互补**：`06_filtering_and_scoring/05_ml_opportunity_model.md`
