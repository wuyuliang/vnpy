# 创业板 ETF 状态预测叠加 EMA 仓位回测实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `159915.SZ` 在 2017-08-14 至 2026-07-20 的 EMA + 1日/3日状态预测仓位回测，并输出连续的自然半年业绩。

**Architecture:** 原 EMA 策略独立产生每日上限，新增纯状态覆盖层根据 3 日上限、1 日节奏和自身 10 日冷却产生最终目标仓位，再复用现有 Portfolio 执行。运行器从 Tushare 下载 2016-01-01 起的原始数据，构造因果复权行情，完整运行一次后切片计算全期、逐年和半年度指标并事务化发布。

**Tech Stack:** Python 3.10+、pandas、NumPy、Tushare、pytest、Ruff。

---

## 文件结构

新增文件：

```text
stock/etf/regime_overlay_strategy.py
stock/etf/run_regime_overlay_backtest.py
stock/etf/tests/test_regime_overlay_strategy.py
stock/etf/tests/test_run_regime_overlay_backtest.py
stock/etf/20260727_chuangyeban_regime_overlay_results.md
```

不修改：

```text
stock/etf/ema_trend_allocation_strategy.py
stock/etf/regime_rules.py
stock/etf/regime_data.py
stock/etf/data.py
stock/etf/tests/test_data.py
```

## Task 1：3 日上限与 1 日仓位状态机

**Files:**

- Create: `stock/etf/regime_overlay_strategy.py`
- Create: `stock/etf/tests/test_regime_overlay_strategy.py`

- [ ] **Step 1：写 3 日边界失败测试**

```python
@pytest.mark.parametrize(
    ("score", "cap", "entry_allowed"),
    [
        (-3.0, 0.0, False),
        (-2.0, 0.0, False),
        (-1.999, 0.5, False),
        (-1.0, 0.5, False),
        (0.0, 0.5, False),
        (1.0, 0.5, False),
        (1.001, 0.5, True),
        (1.999, 0.5, True),
        (2.0, 1.0, True),
        (3.0, 1.0, True),
    ],
)
def test_map_regime_cap_uses_documented_boundaries(
    score: float,
    cap: float,
    entry_allowed: bool,
) -> None:
    assert map_regime_cap(score) == (cap, entry_allowed)
```

同时测试 `NaN/Infinity` 和超出 `[-3,3]` 立即失败。

- [ ] **Step 2：运行 RED**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_strategy.py -q
```

Expected: `ModuleNotFoundError`。

- [ ] **Step 3：实现上限映射**

```python
def map_regime_cap(score_3d: float) -> tuple[float, bool]:
    score = float(score_3d)
    if not isfinite(score) or not -3.0 <= score <= 3.0:
        raise ValueError("score_3d must be finite and within [-3, 3]")
    if score <= -2.0:
        return 0.0, False
    if score <= 1.0:
        return 0.5, False
    if score < 2.0:
        return 0.5, True
    return 1.0, True
```

- [ ] **Step 4：写完整转换失败测试**

覆盖：

```text
EMA 1.0 -> 0.5 立即降仓
3 日 cap 0.0 立即清仓
1 日 <= -2 在风险压降后再降一档
空仓且 entry_allowed=false 时禁止开仓
score_1d > 1 每次最多升一档
0.0 不能直接到 1.0
风险增加被第 9 个完整交易日冷却阻止
第 10 个完整交易日后允许增加
风险降低不受冷却限制
```

使用接口：

```python
transition = transition_overlay_weight(
    current_weight=0.5,
    ema_target_weight=1.0,
    score_1d=2.2,
    score_3d=2.4,
    days_since_transition=10,
    cooldown_days=10,
)
assert transition.target_weight == 1.0
assert transition.risk_increase_blocked is False
```

- [ ] **Step 5：实现纯转换函数**

```python
@dataclass(frozen=True)
class OverlayTransition:
    regime_cap: float
    entry_allowed: bool
    risk_ceiling: float
    risk_reduced_weight: float
    proposed_weight: float
    target_weight: float
    risk_increase_blocked: bool
    primary_reason: str


def transition_overlay_weight(
    *,
    current_weight: float,
    ema_target_weight: float,
    score_1d: float,
    score_3d: float,
    days_since_transition: int | None,
    cooldown_days: int,
) -> OverlayTransition:
    cap, entry_allowed = map_regime_cap(score_3d)
    risk_ceiling = min(ema_target_weight, cap)
    risk_reduced = min(current_weight, risk_ceiling)
    if score_1d <= -2.0:
        proposed = max(0.0, risk_reduced - 0.5)
        reason = "score_1d_reduce"
    elif score_1d > 1.0 and risk_reduced == current_weight:
        can_enter = current_weight > 0.0 or entry_allowed
        proposed = (
            min(current_weight + 0.5, risk_ceiling)
            if can_enter
            else current_weight
        )
        reason = "score_1d_increase" if proposed > current_weight else "hold"
    else:
        proposed = risk_reduced
        reason = "risk_ceiling_reduce" if proposed < current_weight else "hold"
    blocked = (
        proposed > current_weight
        and days_since_transition is not None
        and days_since_transition < cooldown_days
    )
    target = current_weight if blocked else proposed
    return OverlayTransition(
        cap,
        entry_allowed,
        risk_ceiling,
        risk_reduced,
        proposed,
        target,
        blocked,
        "cooldown_block" if blocked else reason,
    )
```

实现前先校验所有仓位属于 `{0.0, 0.5, 1.0}`、冷却为正整数、分数有限。

- [ ] **Step 6：验证并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_strategy.py -q
git add stock/etf/regime_overlay_strategy.py stock/etf/tests/test_regime_overlay_strategy.py
git commit -m "feat: add regime overlay state machine"
```

## Task 2：严格因果的预测对齐

**Files:**

- Modify: `stock/etf/regime_overlay_strategy.py`
- Modify: `stock/etf/tests/test_regime_overlay_strategy.py`

- [ ] **Step 1：写对齐失败测试**

构造带周末和停牌缺口的实际行情行：

```python
bars = pd.DataFrame(
    {
        "symbol": ["159915.SZ"] * 4,
        "datetime": pd.to_datetime(
            ["2017-08-10", "2017-08-11", "2017-08-14", "2017-08-16"]
        ),
    }
)
```

断言：

```text
2017-08-11 收盘的 1d/3d 分数均映射到 2017-08-14 开盘
2017-08-14 收盘分数映射到下一条实际行情 2017-08-16
不使用自然日或 prediction_for_date 对齐
末行没有下一条行情时不生成执行信号
每个 feature date 必须恰有 1d 和 3d 两行
max_feature_source_date <= feature_asof_date < execution_date
```

- [ ] **Step 2：运行 RED**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_strategy.py -q
```

Expected: `align_regime_predictions` 不存在。

- [ ] **Step 3：实现对齐**

```python
def align_regime_predictions(
    predictions: pd.DataFrame,
    bars: pd.DataFrame,
) -> pd.DataFrame:
    wide = predictions.pivot(
        index=["symbol", "feature_asof_date", "max_feature_source_date"],
        columns="prediction_horizon",
        values=["score", "state"],
    )
    wide.columns = ["_".join(reversed(column)) for column in wide.columns]
    next_rows = bars[["symbol", "datetime"]].copy()
    next_rows["feature_asof_date"] = next_rows.groupby("symbol")[
        "datetime"
    ].shift(1)
    aligned = next_rows.merge(
        wide.reset_index(),
        on=["symbol", "feature_asof_date"],
        how="inner",
        validate="one_to_one",
    )
    return aligned.rename(columns={"datetime": "execution_date"})
```

生产实现需显式整理为 `score_1d/state_1d/score_3d/state_3d`，并验证日期不变量。

- [ ] **Step 4：增加未来篡改测试**

把执行日及以后所有预测分数改成极端值，断言该执行日使用的上一交易日分数和最终对齐行
不变。

- [ ] **Step 5：验证并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_strategy.py -q
git add stock/etf/regime_overlay_strategy.py stock/etf/tests/test_regime_overlay_strategy.py
git commit -m "feat: align regime forecasts to next open"
```

## Task 3：独立 EMA 上限与组合成交

**Files:**

- Modify: `stock/etf/regime_overlay_strategy.py`
- Modify: `stock/etf/tests/test_regime_overlay_strategy.py`

- [ ] **Step 1：写集成失败测试**

测试使用 320 条预热行情和 30 条执行行情，断言：

```text
EMA 从执行区间首日空仓开始
ema_target_weight 不受 overlay 当前仓位影响
overlay target 始终 <= ema target 和 regime cap
最终目标只取 0/0.5/1
成交发生在 execution_date 开盘
数量是 100 股整数倍
佣金、滑点、现金和收盘权益可复算
期末不强制平仓
```

接口：

```python
config = TrendAllocationConfig(
    symbol="159915.SZ",
    confirmation_days=1,
    slow_period=20,
    slope_lookback=3,
    risk_increase_cooldown_days=10,
)
result = run_regime_overlay_backtest(
    bars,
    predictions,
    start="2017-08-14",
    end="2026-07-20",
    config=config,
)
```

- [ ] **Step 2：运行 RED**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_strategy.py -q
```

Expected: `run_regime_overlay_backtest` 不存在。

- [ ] **Step 3：实现结果与信号数据结构**

```python
@dataclass
class RegimeOverlayResult:
    signals: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: dict[str, object]
    ema_result: TrendAllocationResult
```

`signals` 至少包含：

```text
datetime
regime_feature_asof_date
score_1d
state_1d
score_3d
state_3d
ema_target_weight
regime_cap
entry_allowed
risk_ceiling
risk_reduced_weight
proposed_weight
risk_increase_blocked
days_since_transition
target_weight
action
primary_reason
```

- [ ] **Step 4：实现独立 EMA 目标**

```python
all_ema_signals = build_trend_signals(bars, config)
execution_signals = all_ema_signals.loc[
    all_ema_signals["datetime"].between(start_date, end_date)
].copy()
ema_result = execute_target_weights(execution_signals, config)
ema_targets = ema_result.signals[["datetime", "target_weight"]].rename(
    columns={"target_weight": "ema_target_weight"}
)
```

EMA 指标在完整预热数据上计算，但 `execute_target_weights` 只接收执行区间行，从而在
2017-08-14 空仓重启。

- [ ] **Step 5：实现 overlay Portfolio 循环**

复用：

```text
Portfolio
calculate_target_quantity
TrendAllocationConfig 交易成本
```

本地实现现金约束买入数量，逐日按顺序：

```text
读取独立 EMA target
计算 days_since_transition
调用 transition_overlay_weight
按开盘价交易
仅成功的目标变化更新 last_transition_ordinal
按收盘价记录 equity/drawdown
```

不得导入其他模块以下划线开头的私有函数。

- [ ] **Step 6：实现完整区间指标**

新增：

```python
def calculate_continuous_metrics(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    initial_equity: float,
    start: object,
    end: object,
) -> dict[str, object]:
```

首日收益使用 `equity_first / initial_equity - 1`，最大回撤的初始峰值包含
`initial_equity`。返回总收益、年化收益、波动、Sharpe、最大回撤及起止日、年化单边
换手、交易数和成本。

- [ ] **Step 7：验证并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_strategy.py -q
git add stock/etf/regime_overlay_strategy.py stock/etf/tests/test_regime_overlay_strategy.py
git commit -m "feat: backtest EMA regime overlay"
```

## Task 4：自然半年、逐年与对照组

**Files:**

- Modify: `stock/etf/regime_overlay_strategy.py`
- Modify: `stock/etf/tests/test_regime_overlay_strategy.py`

- [ ] **Step 1：写半年连续性失败测试**

构造跨越 2017-H2、2018-H1、2018-H2 的权益和成交，断言：

```text
2017-H2 标记 partial
中间完整半年不标记 partial
最后不足半年标记 partial
下一半年 initial_equity 等于上一交易日 final_equity
半年首日收益计入
半年边界持仓和冷却不重启
成交只计入成交日期所在半年
所有权益日期恰好属于一个半年
```

- [ ] **Step 2：实现期间生成**

```python
def calendar_periods(
    start: pd.Timestamp,
    end: pd.Timestamp,
    frequency: Literal["year", "half_year"],
) -> list[PerformancePeriod]:
    if frequency == "half_year":
        label = f"{year}-H{1 if month <= 6 else 2}"
```

每段实际起止日裁剪到总回测区间，`is_partial_period` 根据是否覆盖自然边界确定。

- [ ] **Step 3：实现分段指标**

```python
def calculate_period_table(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    initial_capital: float,
    periods: Sequence[PerformancePeriod],
    strategy: str,
) -> pd.DataFrame:
```

每段的初始权益取段首日前一条完整权益；第一段取 `initial_capital`。用完整曲线已有的
`daily_return` 计算 Sharpe，不把首日改为 0。

- [ ] **Step 4：实现买入持有权益与比较表**

```python
def build_buy_hold_equity(
    bars: pd.DataFrame,
    initial_capital: float,
) -> pd.DataFrame:
    first_close = float(bars.iloc[0]["close"])
    equity = initial_capital * bars["close"] / first_close
```

生成 `regime_overlay/ema_only/buy_hold` 全期、逐年和半年指标。

- [ ] **Step 5：验证并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_strategy.py -q
git add stock/etf/regime_overlay_strategy.py stock/etf/tests/test_regime_overlay_strategy.py
git commit -m "feat: report semiannual overlay performance"
```

## Task 5：事务化真实回测运行器

**Files:**

- Create: `stock/etf/run_regime_overlay_backtest.py`
- Create: `stock/etf/tests/test_run_regime_overlay_backtest.py`

- [ ] **Step 1：写输出失败测试**

端到端夹具断言生成：

```text
signals.csv
trades.csv
positions.csv
equity_curve.csv
comparison.csv
annual_metrics.csv
semiannual_metrics.csv
summary.json
source_audit.json
```

并测试：

```text
非空目录未指定 overwrite 时失败
生成中失败保留上一轮成功目录
JSON 严格禁止 NaN/Infinity
summary 日期严格为 2017-08-14 至 2026-07-20
semiannual_metrics 从 2017-H2 连续到 2026-H2
```

- [ ] **Step 2：运行 RED**

```bash
python3 -m pytest stock/etf/tests/test_run_regime_overlay_backtest.py -q
```

Expected: 运行器模块不存在。

- [ ] **Step 3：实现 CLI**

参数：

```text
--symbol             默认 159915.SZ
--data-start         默认 2016-01-01
--start              默认 2017-08-14
--end                默认 2026-07-20
--download
--daily-csv
--factors-csv
--calendar-csv
--output-dir         默认 stock/etf/output/20260727_chuangyeban_regime_overlay
--overwrite
```

`--download` 和本地 CSV 互斥。真实模式调用：

```python
inputs = download_regime_inputs(symbol, data_start, end)
bars = build_causal_bars(
    inputs.daily,
    inputs.factors,
    "point_in_time_adjusted",
)
predictions = predict_regime(
    bars,
    inputs.calendar,
    RegimeConfig(price_adjustment_mode="point_in_time_adjusted"),
)
```

- [ ] **Step 4：实现事务发布**

在目标目录同级 UUID 暂存目录完成计算、写出、重新读取和校验；成功后替换目标目录。
覆盖时先把旧目录改名为备份，发布失败必须恢复备份。

- [ ] **Step 5：实现 summary 审计**

必须包含：

```text
参数与成本
数据实际起止和预热行数
三组全期指标
半年数量和首尾标签
仓位天数/占比
EMA/3日压降、1日降档、冷却阻止次数
最大回撤区间
回撤 <=35% 与换手 <=8x 检查
无穿越日期检查
```

- [ ] **Step 6：验证并提交**

```bash
python3 -m pytest \
  stock/etf/tests/test_regime_overlay_strategy.py \
  stock/etf/tests/test_run_regime_overlay_backtest.py -q
python3 -m ruff check \
  stock/etf/regime_overlay_strategy.py \
  stock/etf/run_regime_overlay_backtest.py \
  stock/etf/tests/test_regime_overlay_strategy.py \
  stock/etf/tests/test_run_regime_overlay_backtest.py
git add stock/etf/regime_overlay_strategy.py stock/etf/run_regime_overlay_backtest.py stock/etf/tests/test_regime_overlay_strategy.py stock/etf/tests/test_run_regime_overlay_backtest.py
git commit -m "feat: add regime overlay backtest runner"
```

## Task 6：代码审查、真实运行与结果文档

**Files:**

- Create directory: `stock/etf/output/20260727_chuangyeban_regime_overlay/`
- Create: `stock/etf/20260727_chuangyeban_regime_overlay_results.md`
- Modify only new Task 1-5 files when fixing findings

- [ ] **Step 1：正式运行前审查**

搜索并禁止：

```bash
rg -n "shift\\(-|bfill|backfill|center\\s*=\\s*True" \
  stock/etf/regime_overlay_strategy.py \
  stock/etf/run_regime_overlay_backtest.py
```

逐项确认：

```text
EMA 目标独立运行
overlay 每日目标不超过两个上限
预测日期严格早于执行日期
半年边界没有重启状态
真实参数冻结后才运行
```

- [ ] **Step 2：运行真实 Tushare 回测**

```bash
python3 -m stock.etf.run_regime_overlay_backtest \
  --symbol 159915.SZ \
  --data-start 2016-01-01 \
  --start 2017-08-14 \
  --end 2026-07-20 \
  --download \
  --output-dir stock/etf/output/20260727_chuangyeban_regime_overlay
```

不根据结果修改状态权重、阈值或 EMA 参数。

- [ ] **Step 3：独立复算**

从 CSV 独立验证：

```text
final_equity / 1_000_000 - 1 == summary total_return
所有 target_weight <= ema_target_weight
所有 target_weight <= regime_cap
所有 feature_asof_date < datetime
每个半年只出现一次且日期无遗漏
半年 final/initial 收益与 CSV 一致
成交数量均为 100 股整数倍
```

- [ ] **Step 4：生成结果文档**

文档包含：

```text
组合/EMA/买入持有全期对比
2017-H2 至 2026-H2 每半年结果
逐年结果
最大回撤区间
仓位分布、交易数、换手和成本
状态覆盖层相对 EMA 的增减收益
风险约束检查
防穿越审计
研究性结果、不恢复实盘准入的说明
```

- [ ] **Step 5：完整验证**

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
git diff --check
```

- [ ] **Step 6：只提交本任务文件**

```bash
git add \
  stock/etf/regime_overlay_strategy.py \
  stock/etf/run_regime_overlay_backtest.py \
  stock/etf/tests/test_regime_overlay_strategy.py \
  stock/etf/tests/test_run_regime_overlay_backtest.py \
  stock/etf/20260727_chuangyeban_regime_overlay_results.md
git commit -m "feat: deliver chuangyeban regime overlay backtest"
```

提交前确认未暂存用户已有的 `stock/etf/data.py`、`stock/etf/tests/test_data.py` 或其他
无关文件。输出目录作为本机可复现产物保留，不强制加入 Git。
