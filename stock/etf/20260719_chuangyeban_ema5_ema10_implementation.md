# 创业板 ETF EMA5/EMA10 趋势持有增量实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 EMA5 全进全出策略改为 EMA5/EMA10 多头排列入场、EMA10 或死叉退出的非对称持仓状态机，并覆盖生成真实回测图。

**Architecture:** 保持已有执行、费用、权益、CLI 和绘图模块不变，只扩展信号表并在逐日回测循环中按当前持仓解释入场/退出信号。所有指标继续使用上一交易日值，输出目录保持 `stock/etf/output/20260719_chuangyeban`。

**Tech Stack:** Python 3.13、pandas、NumPy、pytest、Ruff。

**Design:** `stock/etf/20260719_chuangyeban_ema5_open_design.md`

---

### Task 1: EMA5/EMA10 原始入场和退出信号

**Files:**
- Modify: `stock/etf/ema5_open_strategy.py`
- Modify: `stock/etf/tests/test_ema5_open_strategy.py`

- [ ] **Step 1: 写四种入场组合、EMA10 预热和退出边界测试**

构造至少 12 根 K 线，并断言：

```python
result = build_ema5_open_signals(prepare_symbol_bars(bars, "159915.SZ"))

assert result.loc[:9, "previous_ema10"].isna().all()
assert not result.loc[:9, "entry_signal"].any()
assert result.loc[10, "entry_signal"]  # open > EMA5 and EMA5 > EMA10
assert not result.loc[11, "entry_signal"]  # open <= EMA5
assert result.loc[11, "exit_signal"] is False  # still above EMA10, no dead cross
```

增加独立边界行，验证：

```python
entry = open_price > previous_ema5 and previous_ema5 > previous_ema10
exit_ = open_price < previous_ema10 or previous_ema5 <= previous_ema10
```

特别覆盖 `open == EMA10` 不退出、`EMA5 == EMA10` 退出，以及数据少于 11 行时报错。

- [ ] **Step 2: 运行测试确认红灯**

```bash
python3 -m pytest stock/etf/tests/test_ema5_open_strategy.py -q
```

Expected: 缺少 `ema10/previous_ema10/entry_signal/exit_signal`，且旧函数仍允许 6 行数据。

- [ ] **Step 3: 实现双 EMA 原始信号**

将信号列改为：

```python
SIGNAL_COLUMNS = BAR_COLUMNS + [
    "ema5", "ema10", "previous_ema5", "previous_ema10",
    "entry_signal", "exit_signal", "target_invested", "action",
]
```

实现：

```python
result["ema5"] = result["close"].ewm(
    span=5, adjust=False, min_periods=5
).mean()
result["ema10"] = result["close"].ewm(
    span=10, adjust=False, min_periods=10
).mean()
result["previous_ema5"] = result["ema5"].shift(1)
result["previous_ema10"] = result["ema10"].shift(1)
ready = result[["previous_ema5", "previous_ema10"]].notna().all(axis=1)
result["entry_signal"] = ready & (
    (result["open"] > result["previous_ema5"])
    & (result["previous_ema5"] > result["previous_ema10"])
)
result["exit_signal"] = ready & (
    (result["open"] < result["previous_ema10"])
    | (result["previous_ema5"] <= result["previous_ema10"])
)
result["target_invested"] = False
result["action"] = "flat"
```

`prepare_symbol_bars` 最少行数改为 11，错误信息改成 `at least 11 rows for previous EMA10`。

- [ ] **Step 4: 运行测试和提交**

```bash
python3 -m pytest stock/etf/tests/test_ema5_open_strategy.py -q
python3 -m ruff check stock/etf/ema5_open_strategy.py stock/etf/tests/test_ema5_open_strategy.py
git add stock/etf/ema5_open_strategy.py stock/etf/tests/test_ema5_open_strategy.py
git commit -m "feat: add EMA5 EMA10 trend signals"
```

### Task 2: 非对称持仓状态机

**Files:**
- Modify: `stock/etf/ema5_open_strategy.py`
- Modify: `stock/etf/tests/test_ema5_open_strategy.py`
- Modify: `stock/etf/tests/test_run_ema5_open_strategy.py`

- [ ] **Step 1: 写上涨回踩继续持有和慢线退出失败测试**

使用手工信号或能生成确定 EMA 的 K 线，验证：

```python
# 买入后 open < previous_ema5，但 open >= previous_ema10 且快线仍在慢线上。
assert result.signals.loc[pullback_index, "action"] == "hold"

# 下一日 open < previous_ema10。
assert result.signals.loc[exit_index, "action"] == "sell"
assert result.trades["side"].tolist() == ["buy", "sell"]
```

再验证快慢线死叉时即使开盘仍较高也卖出，且连续持有日不重复买入。

- [ ] **Step 2: 运行聚焦测试确认红灯**

```bash
python3 -m pytest stock/etf/tests/test_ema5_open_strategy.py -q
```

Expected: 回测循环仍读取旧 `target_invested`，无法按 `entry_signal/exit_signal` 保持仓位。

- [ ] **Step 3: 实现持仓状态转换**

在逐日循环中替换旧目标判断：

```python
invested = cfg.symbol in portfolio.positions
entry_signal = bool(row["entry_signal"])
exit_signal = bool(row["exit_signal"])
action = "hold" if invested else "flat"

if not invested and entry_signal:
    # 保留现有最大整数手买入逻辑
    action = "buy"
elif invested and exit_signal:
    portfolio.sell(
        cfg.symbol,
        row["datetime"],
        float(row["open"]),
        "open_below_previous_ema10_or_ema_dead_cross",
    )
    action = "sell"

signals.loc[index, "action"] = action
signals.loc[index, "target_invested"] = cfg.symbol in portfolio.positions
```

买入原因改为 `open_above_previous_ema5_and_ema_bullish`。交易完成后才写目标状态，确保
`signals.csv` 的目标仓位与每日 `positions.csv` 一致。

- [ ] **Step 4: 更新端到端夹具并运行全部相关测试**

将临时端到端数据扩展到至少 13 根 K 线，产生一买一卖或末日开放仓位；继续验证五个
审计文件和 `1680×1000` PNG。运行：

```bash
python3 -m pytest stock/etf/tests/test_ema5_open_strategy.py stock/etf/tests/test_run_ema5_open_strategy.py -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
```

Expected: 所有相关测试和静态检查通过。

- [ ] **Step 5: 提交状态机**

```bash
git add stock/etf/ema5_open_strategy.py stock/etf/tests/test_ema5_open_strategy.py \
  stock/etf/tests/test_run_ema5_open_strategy.py
git commit -m "feat: hold 创业板 ETF through EMA5 pullbacks"
```

### Task 3: 真实重跑、基线对比和最终图

**Files:**
- Modify: `stock/etf/run_ema5_open_strategy.py`
- Generate: `stock/etf/output/20260719_chuangyeban/**`

- [ ] **Step 1: 更新摘要参数版本**

`summary.json.parameters` 改为：

```python
{
    "ema_fast_period": 5,
    "ema_slow_period": 10,
    "entry_rule": "open>previous_ema5 and previous_ema5>previous_ema10",
    "exit_rule": "open<previous_ema10 or previous_ema5<=previous_ema10",
    # 保留资金、佣金、滑点和手数参数
}
```

增加端到端断言，防止输出仍标记为旧 `ema_period=5`。

- [ ] **Step 2: 覆盖运行真实数据**

```bash
python3 -m stock.etf.run_ema5_open_strategy --overwrite
```

Expected: 实际日期保持 `2017-08-14` 至 `2026-07-17`，生成一张最新交易图。

- [ ] **Step 3: 对比旧基线并审计**

旧基线固定为：590 笔交易、总收益 `-18.5478%`、最大回撤 `-54.2269%`、佣金
`143,737.12` 元、滑点 `239,561.91` 元。读取新 `summary.json`，打印新旧交易次数、
收益、回撤和成本差异；检查主键唯一、金额有限、100 份整数手、最终权益与摘要一致、
图形存在。

- [ ] **Step 4: 视觉检查和最终质量门**

打开 `charts/0001_159915_SZ_易方达创业板ETF.png`，确认中文标题、周线、日线、成交量、
买卖点和持仓状态完整。运行：

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
git diff --check
```

Expected: 全量测试和质量门通过。

- [ ] **Step 5: 提交摘要版本修复**

```bash
git add stock/etf/run_ema5_open_strategy.py stock/etf/tests/test_run_ema5_open_strategy.py
git commit -m "feat: report EMA5 EMA10 trend strategy"
```

不要提交生成的 CSV、JSON 或 PNG；代码提交保留在 `feature` 分支。

