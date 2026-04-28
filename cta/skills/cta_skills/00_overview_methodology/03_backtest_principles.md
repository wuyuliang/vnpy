# 回测原则 / Backtest Principles

> 归属章节：`00_overview_methodology/` · 前置技能：`01_objectives.md`、`02_research_boundary.md` · 关联：`cta/strategy/brooks/backtest/`

## 1. Skill 定义

回测的**方法论底线**：如何避免未来函数、如何建模成本、如何划分样本、如何做稳健性检验。不是具体回测引擎实现（那在 `08_data_and_backtest_infra/04_event_driven_backtest.md`），而是"在写引擎时该遵守的铁律"。

## 2. 解决什么问题

- 痛点 1：回测曲线漂亮 → 实盘立刻掉头，原因常是未来函数 / 成本低估。
- 痛点 2：每个策略的回测报告格式不同，无法横向比。
- 痛点 3：样本内调参，样本外不测，策略上线即失效。
- 增量价值：把"靠谱回测"从个人经验变成项目级清单。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：无
- **不适用场景**：非回测任务（如实盘 dry-run 使用的原则见 `10_live_ops/`）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 信息泄漏检测 | 对比 shift(1) 与 shift(0) 信号后的收益差 | — | 差距 > 20% 即疑似未来函数 | TODO: 待实现 |
| 成交价模式 | open / close / next_open | 推荐 next_open | — | `cta/strategy/brooks/backtest/engine.py` |
| 滑点模型 | tick × N + 波动率倍数 | N=2 | `cta/config/futures_meta.py` | `cta/config/futures_meta.py` |
| OOS/IS 比 | 样本外 Sharpe / 样本内 Sharpe | — | ≥ 0.6 才算稳健 | TODO：待实现自动计算 |

## 5. 常见策略映射

本 skill 不产生策略，但是**所有策略必须遵循的 5 条铁律**：

### 铁律 1：时间对齐
- 所有信号计算必须使用**至截止 bar 为止已收盘的数据**。
- 公式层面：`signal[t] = f(bar[:t])`，不含 `bar[t].close`。
- 违反典型：在 `bar[t]` 上用 `close[t]` 算 MA 后决定"当前 bar 入场"。

### 铁律 2：成交延迟
- 回测默认 `next_open` 成交，不假设能在信号 bar 的 close 成交。
- 分钟级可以 `next_minute_open`，日线必须 `next_day_open`。

### 铁律 3：完整成本
- 每笔成本 = 手续费 + 滑点 + 冲击成本 + （长期持仓）资金成本。
- 参考 `08_data_and_backtest_infra/03_transaction_cost_model.md`。

### 铁律 4：样本划分
- 至少 3 块：
  - 训练（调参 / 训练 ML）
  - 验证（early stop / 超参选择）
  - 测试（一次性 OOS，绝不反向调参）
- 推荐 walk-forward：滚动训练，每个 OOS 窗口只用前一段样本的参数。

### 铁律 5：复现性
- 随机数固定 seed
- 版本控制：策略 py 改动要有 commit hash
- 记录：特征 snapshot 写入成交日志（见 `cta/strategy/brooks/core/trade_log.py`）

## 6. 代码模块设计

不新增文件，但约定所有 `cta/strategy/{name}/backtest/engine.py` 必须实现：

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class BacktestConfig:
    fill_model: str = "next_open"        # 'close' 禁用，'next_open' 默认
    slippage_ticks: int = 2
    fee_rate: float = 2e-4
    start: str = "2018-01-01"
    end: str = "2024-12-31"
    oos_start: str = "2024-01-01"        # OOS 起点

def run_backtest(
    df: pd.DataFrame,
    signals: pd.DataFrame,
    cfg: BacktestConfig,
) -> dict:
    """
    返回: {
        'summary': pd.DataFrame,   # 单行，符合 01_objectives REQUIRED_COLUMNS
        'trades': pd.DataFrame,    # 成交明细（含 MFE/MAE/exit_reason）
        'daily_equity': pd.Series, # 日净值曲线
    }
    要求: 信号在 t 使用，成交在 t+1 开盘。
    """
    ...

def assert_no_lookahead(df: pd.DataFrame, signal_col: str) -> None:
    """静态检测：shift(0) 与 shift(1) 收益差过大则报警。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：总收益 / 年化 / 最大回撤 / Sharpe / Calmar / 胜率 / 盈亏比 / 最长连亏 bar 数
- **分层评估**：按年份 / 按品种 / 按 regime / 按 setup 类型 / 按持仓方向（多 vs 空）
- **稳健性检验**：
  1. 参数敏感性（核心参数 ±20% 仍达目标的 80%）
  2. 成本 ×2（滑点翻倍仍然盈利）
  3. 样本外（OOS）胜率、盈亏比、Sharpe 均不低于 IS 的 60%
  4. 不同 fill_model（`next_open` vs `close`）结果差距 < 30%，否则策略依赖成交假设

## 8. 常见错误

- 用 `close[t]` 决定 `bar[t]` 的入场 → 未来函数
- 回测成交默认 close 价 → 实盘无法复现
- 只跑一次回测就上 → 没做参数敏感性
- 样本外发现不行 → 回去调参 → 再看"OOS" → 本质还是样本内
- 数据含主力切换断点没处理 → 跳空虚假信号
- 手续费乘数用错（如 RB 每手 10 吨忘记乘）→ 风险计算错

## 9. 迭代方向

- v1：口头 / 文档铁律
- v2：把"禁用 close 成交"、"OOS 必须存在"写入 `cta/config/backtest_gate.yaml`
- v3：`assert_no_lookahead` 自动扫描每个策略
- v4：walk-forward 框架内建（参考 `09_ml_augmentation/05_walk_forward_validation.md`）

## 10. 与其他 Skills 的关系

- **依赖**：`00_overview_methodology/01_objectives.md`（目标 → 评估指标）
- **被依赖**：所有策略 skill + `08_data_and_backtest_infra/04_event_driven_backtest.md`
- **互补**：`08_data_and_backtest_infra/03_transaction_cost_model.md`、`09_ml_augmentation/05_walk_forward_validation.md`
