# 目标设定 / Objectives

> 归属章节：`00_overview_methodology/` · 前置技能：无 · 关联：`cta/README.md`、`CLAUDE.md`

## 1. Skill 定义

为整个 CTA 研究线设定**可量化的最终目标与约束**：目标 Sharpe、目标回撤、目标容量、目标换手，以及最低可接受线。不设目标就不能判断策略是否"够好"。

## 2. 解决什么问题

- 痛点 1：没有明确目标，策略越调越多，不知道何时停。
- 痛点 2：目标只说"赚钱"不够，无法比较两个 Sharpe 相近但回撤差 3 倍的策略。
- 痛点 3：忽略容量 / 换手，小资金回测好看上不了大资金。
- 增量价值：把"主观好坏"变成"客观达标 / 不达标"，为淘汰 / 上线提供统一尺子。

## 3. 适用市场 / 适用场景

- **品种类别**：全部（黑色 / 有色 / 能化 / 农产品 / 贵金属）
- **周期**：全部（day / minute60 / minute30 / minute15 / minute5 / minute）
- **行情状态前提**：无
- **不适用场景**：本 skill 只产生目标文档，不产生交易信号

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 年化收益 AnnRet | `(1 + total_ret) ** (252/days) - 1` | 回测 ≥ 5 年 | ≥ 15% 为过门槛线 | `cta/strategy/brooks/backtest/reporter.py` |
| 最大回撤 MaxDD | `max(peak - trough) / peak` | 以日 NAV 算 | ≤ 15% 为过门槛线 | `cta/strategy/brooks/backtest/reporter.py` |
| 夏普 Sharpe | `mean(ret) / std(ret) * sqrt(252)` | 日收益 | ≥ 1.2 | `cta/strategy/brooks/backtest/reporter.py` |
| Calmar | `AnnRet / MaxDD` | — | ≥ 1.0 | `cta/strategy/brooks/backtest/reporter.py` |
| 年换手 Turnover | 年成交名义金额 / 平均权益 | — | ≤ 100 为中低频目标 | TODO：待实现 |
| 容量 Capacity | 在不显著劣化滑点下可承载资金 | 单笔占成交量 ≤ 1% | 估计 ≥ 3000 万 | TODO：待实现 |

## 5. 常见策略映射

不产生策略，但产生**策略准入门槛**：

### 门槛 A（中频趋势）
- AnnRet ≥ 20%、MaxDD ≤ 15%、Sharpe ≥ 1.3、Calmar ≥ 1.3、容量 ≥ 3000 万。
- 样本外（最近 1 年）：性能不差于样本内的 60%。

### 门槛 B（日内反转 / 震荡）
- AnnRet ≥ 25%、MaxDD ≤ 10%、Sharpe ≥ 1.5、日均换手不超过 3 次 / 品种。
- 样本外：胜率、盈亏比均不低于样本内 70%。

### 仓位与风控统一约束
- 单笔风险 ≤ 0.3% 总权益；单品种风险 ≤ 1%；全组合同时在险 ≤ 5%。
- 参考 `07_position_and_portfolio/01_single_trade_risk.md`。

## 6. 代码模块设计

本 skill 不新建 py 代码，但约束回测报告 schema。约定 `cta/strategy/{name}/backtest/reporter.py` 产出的 `summary.csv` 必须包含下列列：

```python
# cta/strategy/brooks/backtest/reporter.py 已有模板，可复用
REQUIRED_COLUMNS: list[str] = [
    "symbol", "interval",
    "total_return", "annual_return",
    "max_drawdown", "sharpe", "calmar",
    "win_rate", "profit_factor", "avg_holding_bars",
    "trade_count", "turnover",
]

def assert_passes_gate_a(row: pd.Series) -> bool:
    """门槛 A（中频趋势）准入判定。"""
    ...

def assert_passes_gate_b(row: pd.Series) -> bool:
    """门槛 B（日内反转）准入判定。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：总收益 / 年化 / 最大回撤 / Sharpe / Calmar / 胜率 / 盈亏比 / 平均持仓 bar
- **分层评估**：按年份（看近 3 年稳定性）、按品种（看跨品种扩展性）、按 regime（看行情状态依赖性）
- **稳健性检验**：
  1. 参数敏感性：核心参数 ±20%，目标退化不超过 25%
  2. 样本外 walk-forward：最后 1 年不回测，只做 OOS 验证
  3. 成本敏感性：滑点 ×2、手续费 ×1.5 仍然达到门槛

## 8. 常见错误

- 只看总收益不看回撤 → 上不了真金
- 只看样本内不做样本外 → 大概率拟合
- 不看容量 → 小资金调完上大资金立刻劣化
- 不看换手 → 被手续费吃光
- 用不同策略比不同指标 → 无法横向对比

## 9. 迭代方向

- v1：本文档（目标约束）
- v2：把门槛 A / B 写成 `cta/config/acceptance.yaml`，回测结束自动判定
- v3：增加容量评估脚本（基于每笔占日成交量比）
- v4：按季度 review 目标门槛是否偏离实际市场收益

## 10. 与其他 Skills 的关系

- **被依赖**：所有策略类 skill（`03/04/05`）都在"是否达标"这件事上回引到本文档。
- **互补**：`00_overview_methodology/02_research_boundary.md`（目标与边界是一对）、`00_overview_methodology/03_backtest_principles.md`（评估方式决定目标是否可信）。
- **下游工程化**：`08_data_and_backtest_infra/05_trade_log_and_evaluation.md`（报告产出）、`10_live_ops/04_daily_review.md`（实盘日常评估）。
