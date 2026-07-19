# 创业板 ETF 三档趋势仓位总收益优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个严格训练/验证/样本外隔离的 0%/50%/100% 创业板 ETF 趋势策略，在最大回撤 35%和年化单边换手 8 倍硬约束下提高总收益，并生成可审计交易图。

**Architecture:** 保留现有 EMA5/EMA10 基线不变。新策略模块负责前日指标、纯状态转换、部分加减仓和区间指标；优化模块只评估预注册的 8 个候选并在 2024 年末以前完成选择；运行模块在选参后才读取完整数据计算样本外、发布门槛、基准对比和图形。

**Tech Stack:** Python 3.13、pandas、NumPy、pytest、Ruff、现有 `Portfolio` 和 `render_trade_charts`。

**Design:** `stock/etf/20260719_chuangyeban_total_return_optimization_design.md`

---

### Task 1: 前日趋势条件和三态转换

**Files:**
- Create: `stock/etf/ema_trend_allocation_strategy.py`
- Create: `stock/etf/tests/test_ema_trend_allocation_strategy.py`

- [ ] **Step 1: 写配置、候选值和状态优先级失败测试**

测试必须断言配置只接受预注册值，且纯转换函数遵守风险降低优先级：

```python
def test_config_rejects_non_preregistered_parameters() -> None:
    with pytest.raises(ValueError, match="slow_period"):
        TrendAllocationConfig(slow_period=25)
    with pytest.raises(ValueError, match="confirmation_days"):
        TrendAllocationConfig(confirmation_days=3)
    with pytest.raises(ValueError, match="slope_lookback"):
        TrendAllocationConfig(slope_lookback=4)


@pytest.mark.parametrize(
    ("current", "exit_flat", "reduce_half", "enter_half", "enter_full", "expected"),
    [
        (0.0, True, False, True, True, 0.0),
        (0.0, False, False, True, True, 0.5),
        (0.5, False, False, False, True, 1.0),
        (1.0, False, True, False, False, 0.5),
        (1.0, True, True, False, False, 0.0),
    ],
)
def test_transition_target_weight_uses_risk_first_priority(
    current: float,
    exit_flat: bool,
    reduce_half: bool,
    enter_half: bool,
    enter_full: bool,
    expected: float,
) -> None:
    assert transition_target_weight(
        current,
        exit_flat=exit_flat,
        reduce_half=reduce_half,
        enter_half=enter_half,
        enter_full=enter_full,
    ) == expected
```

- [ ] **Step 2: 运行测试确认红灯**

Run: `python3 -m pytest stock/etf/tests/test_ema_trend_allocation_strategy.py -q`

Expected: FAIL，模块或类型尚不存在。

- [ ] **Step 3: 实现最小配置和纯状态转换**

新增：

```python
@dataclass(frozen=True)
class TrendAllocationConfig:
    symbol: str = "159915.SZ"
    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    slippage_rate: float = 0.0005
    slow_period: int = 20
    confirmation_days: int = 2
    slope_lookback: int = 3


def transition_target_weight(
    current_weight: float,
    *,
    exit_flat: bool,
    reduce_half: bool,
    enter_half: bool,
    enter_full: bool,
) -> float:
    if exit_flat:
        return 0.0
    if current_weight == 1.0 and reduce_half:
        return 0.5
    if current_weight == 0.5 and enter_full:
        return 1.0
    if current_weight == 0.0 and enter_half:
        return 0.5
    return current_weight
```

配置校验必须固定 `lot_size` 为整数 100，并拒绝非 `{20,30}`、`{1,2}`、`{3,5}`。

- [ ] **Step 4: 写前日指标和连续确认失败测试**

构造至少 40 根日线，验证：

```python
signals = build_trend_signals(bars, config)
assert signals.loc[first_ready - 1, "ready"] is False
assert signals.loc[first_ready, "ready"] is True

changed = bars.copy()
changed.loc[target_index, "close"] *= 1.5
changed_signals = build_trend_signals(changed, config)
assert changed_signals.loc[target_index, condition_columns].equals(
    signals.loc[target_index, condition_columns]
)
```

同时构造上涨、快线走弱和慢线破位序列，分别断言 `enter_half`、`enter_full`、
`reduce_half`、`exit_flat`。当前行收盘变化不得改变当前行开盘信号，只能影响后续行。

- [ ] **Step 5: 实现 `build_trend_signals`**

按设计计算 EMA5、EMA10、动态慢线、EMA10 斜率和慢线斜率。连续确认必须采用
`rolling(window).min().shift(1)`，单日开盘比较采用相应 EMA 的 `shift(1)`：

```python
fast_bull_confirmed = fast_bull.rolling(days, min_periods=days).min().shift(1)
medium_bull_confirmed = medium_bull.rolling(days, min_periods=days).min().shift(1)
fast_bear_confirmed = fast_bear.rolling(days, min_periods=days).min().shift(1)
medium_bear_confirmed = medium_bear.rolling(days, min_periods=days).min().shift(1)

result["enter_half"] = ready & (result["open"] > previous_ema10) & fast_bull_confirmed & (previous_ema10_slope > 0)
result["enter_full"] = ready & (result["open"] > previous_ema5) & medium_bull_confirmed & (previous_slow_slope > 0)
result["reduce_half"] = ready & ((result["open"] < previous_ema10) | fast_bear_confirmed)
result["exit_flat"] = ready & ((result["open"] < previous_slow) | medium_bear_confirmed)
```

最少数据行数为 `slow_period + slope_lookback + 1`。输出保留原始 OHLCV、指标、四个
条件、`target_weight` 和 `action` 审计列。

- [ ] **Step 6: 验证并提交 Task 1**

Run:

```bash
python3 -m pytest stock/etf/tests/test_ema_trend_allocation_strategy.py -q
python3 -m ruff check stock/etf/ema_trend_allocation_strategy.py stock/etf/tests/test_ema_trend_allocation_strategy.py
python3 -m ruff format --check stock/etf/ema_trend_allocation_strategy.py stock/etf/tests/test_ema_trend_allocation_strategy.py
```

Expected: PASS。

Commit:

```bash
git add stock/etf/ema_trend_allocation_strategy.py stock/etf/tests/test_ema_trend_allocation_strategy.py
git commit -m "feat: add three-level ETF trend signals"
```

### Task 2: 三档目标数量、部分交易和区间指标

**Files:**
- Modify: `stock/etf/ema_trend_allocation_strategy.py`
- Modify: `stock/etf/tests/test_ema_trend_allocation_strategy.py`

- [ ] **Step 1: 写目标数量和部分加减仓失败测试**

使用无费用 10 万元手工算例：10 元开盘从 0% 到 50% 应买 5,000 份，从 50% 到
100%应再买 5,000 份；随后在 12 元开盘降回 50%时，目标按交易前权益重新计算，卖出
数量必须是目标差额且为 100 份整数手。测试同时断言：

```python
assert trades["side"].tolist() == ["buy", "buy", "sell"]
assert (trades["quantity"] % 100 == 0).all()
assert signals["target_weight"].isin([0.0, 0.5, 1.0]).all()
assert positions["quantity"].gt(0).all()
```

增加费用算例，验证买入增量不会使现金为负，部分卖出按比例分摊入场佣金；增加末日
持仓算例，验证不强平且最终权益按收盘价估值。

- [ ] **Step 2: 运行测试确认红灯**

Run: `python3 -m pytest stock/etf/tests/test_ema_trend_allocation_strategy.py -q`

Expected: FAIL，回测和目标数量函数不存在。

- [ ] **Step 3: 实现三档回测**

新增 `TrendAllocationResult` 和：

```python
def calculate_target_quantity(equity: float, raw_price: float, weight: float, lot_size: int) -> int:
    return floor(equity * weight / raw_price / lot_size) * lot_size


def run_trend_allocation_backtest(
    bars: pd.DataFrame,
    config: TrendAllocationConfig | None = None,
) -> TrendAllocationResult:
    cfg = config or TrendAllocationConfig()
    signals = build_trend_signals(bars, cfg)
    return execute_target_weights(signals, cfg)
```

同一步实现私有执行核心 `execute_target_weights`，逐日先调用
`transition_target_weight`，再用当日开盘权益计算目标数量。新增仓位使用
`Portfolio.buy`，已有仓位加仓使用 `Portfolio.increase`，减仓使用
`Portfolio.sell_quantity`。买入差额若费用后现金不足，每次减少 100 份直到可承担。
动作值固定为 `flat`、`hold_half`、`hold_full`、`buy_to_half`、`buy_to_full`、
`sell_to_half`、`sell_to_flat`。

- [ ] **Step 4: 写指标复算失败测试**

构造 6 日权益和 3 笔交易，手工断言：

```python
metrics = calculate_period_metrics(equity_curve, trades, start, end)
assert metrics["total_return"] == pytest.approx(last_equity / first_equity - 1)
assert metrics["max_drawdown"] == pytest.approx(expected_drawdown)
assert metrics["sharpe"] == pytest.approx(expected_sharpe)
assert metrics["annual_one_way_turnover"] == pytest.approx(expected_turnover)
```

区间日收益必须从区间权益重新 `pct_change().fillna(0)`，区间回撤必须重置峰值；换手只
统计区间内交易，使用成交价乘数量。

- [ ] **Step 5: 实现 `calculate_period_metrics` 和摘要**

返回 `start_date/end_date/trading_days/total_return/annual_return/annual_volatility/sharpe/
max_drawdown/annual_one_way_turnover/trade_count`。全期摘要额外返回最终权益、佣金、滑点、
目标仓位和是否开放持仓。

- [ ] **Step 6: 验证并提交 Task 2**

Run:

```bash
python3 -m pytest stock/etf/tests/test_ema_trend_allocation_strategy.py -q
python3 -m pytest stock/etf/tests/test_portfolio.py -q
python3 -m ruff check stock/etf/ema_trend_allocation_strategy.py stock/etf/tests/test_ema_trend_allocation_strategy.py
```

Expected: PASS。

Commit:

```bash
git add stock/etf/ema_trend_allocation_strategy.py stock/etf/tests/test_ema_trend_allocation_strategy.py
git commit -m "feat: backtest three-level ETF allocation"
```

### Task 3: 预注册优化和发布门槛

**Files:**
- Create: `stock/etf/optimize_ema_trend_allocation.py`
- Create: `stock/etf/tests/test_optimize_ema_trend_allocation.py`

- [ ] **Step 1: 写候选集合和截止日隔离失败测试**

断言候选恰好为笛卡尔积：

```python
configs = preregistered_configs()
assert len(configs) == 8
assert {(c.slow_period, c.confirmation_days, c.slope_lookback) for c in configs} == set(product((20, 30), (1, 2), (3, 5)))
```

向 `optimize_on_train_validation` 传入 `2025-01-02` 行时必须抛出
`ValueError("selection data ends after 2024-12-31")`，证明选参函数不能看到样本外数据。

- [ ] **Step 2: 运行测试确认红灯并实现候选生成/截止日校验**

Run: `python3 -m pytest stock/etf/tests/test_optimize_ema_trend_allocation.py -q`

Expected: FAIL，模块不存在。实现后同一命令 PASS。

- [ ] **Step 3: 写约束过滤和确定性排序失败测试**

将候选指标作为小型 DataFrame 输入纯函数 `select_candidate`，覆盖：

- 训练或验证最大回撤小于 -0.35 时被过滤。
- 训练或验证年化单边换手大于 8 时被过滤。
- 可行候选优先验证总收益，其次回撤绝对值、换手、三个参数升序。
- 无可行候选时返回 `None`，不得放宽约束。

- [ ] **Step 4: 实现候选评估和选择**

`optimize_on_train_validation` 对每个候选运行到 `2024-12-31`，分别调用
`calculate_period_metrics` 计算训练/验证指标，输出固定列 `train_*`、`validation_*`、
`train_feasible`、`validation_feasible`、`selected` 和 `rank`。只允许
`train_feasible & validation_feasible` 进入排序。

- [ ] **Step 5: 写发布门槛失败测试并实现**

纯函数：

```python
def evaluate_release(
    candidate_full: Mapping[str, float],
    candidate_oos: Mapping[str, float],
    baseline_full: Mapping[str, float],
    baseline_oos: Mapping[str, float],
) -> dict[str, object]:
    checks = {
        "full_return_improved": candidate_full["total_return"] > baseline_full["total_return"],
        "full_drawdown_within_limit": candidate_full["max_drawdown"] >= -0.35,
        "full_turnover_within_limit": candidate_full["annual_one_way_turnover"] <= 8.0,
        "oos_return_improved": candidate_oos["total_return"] > baseline_oos["total_return"],
        "oos_drawdown_within_limit": candidate_oos["max_drawdown"] >= -0.35,
        "oos_turnover_within_limit": candidate_oos["annual_one_way_turnover"] <= 8.0,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "status": "accepted" if not failed_checks else "rejected",
        "checks": checks,
        "failed_checks": failed_checks,
    }
```

逐项返回六个布尔条件、`failed_checks` 和 `status`。任何一项失败时必须为 `rejected`，
全部通过才为 `accepted`。

- [ ] **Step 6: 验证并提交 Task 3**

Run:

```bash
python3 -m pytest stock/etf/tests/test_optimize_ema_trend_allocation.py -q
python3 -m ruff check stock/etf/optimize_ema_trend_allocation.py stock/etf/tests/test_optimize_ema_trend_allocation.py
```

Expected: PASS。

Commit:

```bash
git add stock/etf/optimize_ema_trend_allocation.py stock/etf/tests/test_optimize_ema_trend_allocation.py
git commit -m "feat: select preregistered ETF allocation"
```

### Task 4: 端到端输出、基准比较和交易图

**Files:**
- Create: `stock/etf/run_ema_trend_allocation.py`
- Create: `stock/etf/tests/test_run_ema_trend_allocation.py`

- [ ] **Step 1: 写本地端到端失败测试**

临时生成跨越训练、验证和样本外边界的日线、元数据和沪深300 CSV。调用
`run_and_write` 后断言：

```python
for name in (
    "candidate_results.csv", "selected_parameters.json", "summary.json",
    "signals.csv", "trades.csv", "positions.csv", "equity_curve.csv",
):
    assert (output_dir / name).is_file()
assert (output_dir / "charts/0001_159915_SZ_易方达创业板ETF.png").is_file()
assert summary["selection_cutoff"] == "2024-12-31"
assert summary["candidate_count"] == 8
```

测试摘要必须同时包含 `candidate/current_strategy/etf_buy_hold/csi300_buy_hold`，以及
`training/validation/oos/full` 分段。图形尺寸保持 `1680x1000`。

- [ ] **Step 2: 运行测试确认红灯**

Run: `python3 -m pytest stock/etf/tests/test_run_ema_trend_allocation.py -q`

Expected: FAIL，运行模块不存在。

- [ ] **Step 3: 实现基准指标和原子写出**

实现 `calculate_buy_hold_metrics`，按分段首尾收盘价计算总收益并重算回撤与 Sharpe，
不计费用。复用 `run_ema5_open_backtest` 生成当前策略连续基线。新增 CSV/JSON 原子写出，
不得导入现有运行器的私有函数。

- [ ] **Step 4: 实现 `run_and_write` 和 CLI**

默认路径：

```text
daily_csv     = stock/etf/data/20260717_2018_20260717_point_in_time_live/etfs_lifecycle_clean.csv
metadata_csv  = stock/etf/data/20260717_2018_20260717_point_in_time_live/metadata.csv
benchmark_csv = stock/etf/data/20260717_2018_20260717_point_in_time_live/benchmark.csv
output_dir    = stock/etf/output/20260719_chuangyeban_optimized
```

先把日线截断到选择截止日传给优化器，选定参数后才运行全数据。写出候选、参数、摘要和
四个审计 CSV，再调用 `render_all_trade_charts`。若训练/验证无可行候选，写出候选表和
拒绝摘要后明确失败，不伪造最终策略。

- [ ] **Step 5: 验证并提交 Task 4**

Run:

```bash
python3 -m pytest stock/etf/tests/test_run_ema_trend_allocation.py -q
python3 -m pytest stock/etf/tests/test_ema_trend_allocation_strategy.py stock/etf/tests/test_optimize_ema_trend_allocation.py -q
python3 -m ruff check stock/etf/run_ema_trend_allocation.py stock/etf/tests/test_run_ema_trend_allocation.py
```

Expected: PASS。

Commit:

```bash
git add stock/etf/run_ema_trend_allocation.py stock/etf/tests/test_run_ema_trend_allocation.py
git commit -m "feat: output 创业板 allocation optimization"
```

### Task 5: 真实运行、独立复核和最终交付

**Files:**
- Generate: `stock/etf/output/20260719_chuangyeban_optimized/**`
- Modify only if a verified defect is found: files created in Tasks 1-4 and their tests

- [ ] **Step 1: 覆盖运行真实本地数据**

Run:

```bash
python3 -m stock.etf.run_ema_trend_allocation --overwrite
```

Expected: 读取 `2017-08-14` 至 `2026-07-17`，评估恰好 8 个候选并输出唯一选参结果。

- [ ] **Step 2: 审计参数隔离和指标**

只读复算并断言：

- 候选表恰好 8 行，只有 1 行 `selected=True`。
- 选参输入结束日为 `2024-12-31` 或此前最后交易日。
- 交易数量均为正的 100 份整数倍，不融资、不透支。
- 目标仓位只含 0、0.5、1，所有交易动作与状态转换一致。
- 全期和样本外收益、最大回撤、Sharpe、换手可由 CSV 复算。
- 发布状态与六个硬门槛逐项一致。
- 最终权益、交易数、佣金和滑点与摘要一致。

- [ ] **Step 3: 视觉检查最终图**

打开 `charts/0001_159915_SZ_易方达创业板ETF.png`，确认中文名称、周线、日 K、成交量、
部分加仓/减仓点和最新仓位可读。若候选被拒绝，最终报告必须明确图形仅为被拒绝候选
审计，不得称为正式替代策略。

- [ ] **Step 4: 请求独立代码审查并按 TDD 修复有效问题**

审查重点：前视偏差、分段泄漏、状态优先级、部分交易会计、换手公式、发布门槛和图形
成交数量一致性。每个有效问题先补失败测试，再修复并重跑相关测试。

- [ ] **Step 5: 运行最终质量门**

Run:

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
git diff --check
```

Expected: 全部通过。

- [ ] **Step 6: 保留分支并交付**

代码和文档提交保留在 `feature`，不合并主分支，不提交生成的 CSV、JSON 或 PNG。最终
报告给出选定参数、训练/验证/样本外/全期指标、与三个基准的差异、发布状态和最终交易
图绝对路径。
