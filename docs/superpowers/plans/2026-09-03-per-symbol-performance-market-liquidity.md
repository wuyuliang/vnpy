# Per-Symbol Performance And Market Liquidity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一次性为多周期趋势回测增加逐笔入场前市场流动性、分品种绩效 CSV 和每日权益静态图。

**Architecture:** 保持事件回放和交易决策不变，只在报告边界扩展数据。`runner.py` 从现有加载行情中抽取成交入场交易日并传入现有完整日线，`report.py` 负责流动性映射、分品种统计、CSV 写盘和权益图生成，`engine.py` 仅声明新增 `trades.csv` 列序。

**Tech Stack:** Python 3、pandas、NumPy、Matplotlib、pytest。

---

### Task 1: 为逐笔交易增加入场前市场流动性

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py:47-70`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py:460-480`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/report.py:20-130`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: 写入失败测试，固定列序、严格窗口和换月连续链**

在测试文件中增加断言：

```python
def test_market_liquidity_columns_follow_forward_returns() -> None:
    end = trend_engine.TRADE_COLUMNS.index("return_30min")
    assert trend_engine.TRADE_COLUMNS[end + 1:end + 7] == (
        "prior_5d_avg_market_volume",
        "prior_5d_avg_market_turnover",
        "prior_10d_avg_market_volume",
        "prior_10d_avg_market_turnover",
        "prior_20d_avg_market_volume",
        "prior_20d_avg_market_turnover",
    )


def test_market_liquidity_uses_only_complete_days_before_entry() -> None:
    dates = pd.date_range("2026-01-01", periods=21, freq="D").date
    daily = pd.DataFrame({
        "symbol": "AG",
        "exchange_trade_date": dates,
        "contract_code": ["AG2602.SHF"] * 10 + ["AG2604.SHF"] * 11,
        "volume": range(1, 22),
        "turnover": [value * 1000.0 for value in range(1, 22)],
    })
    trades = pd.DataFrame([_logical_trade(candidate_id="AG-1", symbol="AG")])
    entry_dates = pd.DataFrame({
        "candidate_id": ["AG-1"],
        "exchange_trade_date": [date(2026, 1, 21)],
    })

    result = trend_report._with_market_liquidity(
        trades,
        daily_market_bars=daily,
        entry_trade_dates=entry_dates,
    )

    assert result.loc[0, "prior_5d_avg_market_volume"] == pytest.approx(18.0)
    assert result.loc[0, "prior_10d_avg_market_volume"] == pytest.approx(15.5)
    assert result.loc[0, "prior_20d_avg_market_volume"] == pytest.approx(10.5)
    assert result.loc[0, "prior_5d_avg_market_turnover"] == pytest.approx(18_000.0)
```

另加一个只有 19 个历史交易日的用例，断言 5/10 日字段有值而 20 日字段为 `NaN`。日线中间切换 `contract_code`，证明滚动按 `symbol` 连续而不是按实际合约重置。

- [ ] **Step 2: 运行测试并确认因字段或函数不存在而失败**

Run:

```bash
python3 -m pytest -q \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_market_liquidity_columns_follow_forward_returns \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_market_liquidity_uses_only_complete_days_before_entry
```

Expected: FAIL，原因是六个列名或 `_with_market_liquidity` 尚不存在。

- [ ] **Step 3: 在交易列声明和报告层实现最小流动性计算**

在 `engine.py` 增加常量并插入 `return_30min` 后：

```python
MARKET_LIQUIDITY_WINDOWS = (5, 10, 20)
MARKET_LIQUIDITY_COLUMNS = tuple(
    column
    for days in MARKET_LIQUIDITY_WINDOWS
    for column in (
        f"prior_{days}d_avg_market_volume",
        f"prior_{days}d_avg_market_turnover",
    )
)
```

在 `report.py` 实现：

```python
def _with_market_liquidity(
    trades: pd.DataFrame,
    *,
    daily_market_bars: pd.DataFrame,
    entry_trade_dates: pd.DataFrame,
) -> pd.DataFrame:
    result = trades.copy()
    for column in MARKET_LIQUIDITY_COLUMNS:
        result[column] = math.nan
    if result.empty or daily_market_bars.empty or entry_trade_dates.empty:
        return result.reindex(columns=TRADE_COLUMNS)

    dates = entry_trade_dates.loc[:, ["candidate_id", "exchange_trade_date"]].copy()
    dates["exchange_trade_date"] = pd.to_datetime(
        dates["exchange_trade_date"], errors="raise"
    ).dt.date
    result = result.drop(columns=MARKET_LIQUIDITY_COLUMNS).merge(
        dates, on="candidate_id", how="left", validate="one_to_one"
    )
    daily = daily_market_bars.loc[
        :, ["symbol", "exchange_trade_date", "volume", "turnover"]
    ].copy()
    daily["exchange_trade_date"] = pd.to_datetime(
        daily["exchange_trade_date"], errors="raise"
    ).dt.date
    daily["volume"] = pd.to_numeric(daily["volume"], errors="coerce")
    daily["turnover"] = pd.to_numeric(daily["turnover"], errors="coerce")

    for index, trade in result.iterrows():
        prior = daily.loc[
            daily["symbol"].eq(trade["symbol"])
            & daily["exchange_trade_date"].lt(trade["exchange_trade_date"])
        ].sort_values("exchange_trade_date", kind="stable")
        for days in MARKET_LIQUIDITY_WINDOWS:
            window = prior.tail(days)
            if len(window) != days:
                continue
            for source in ("volume", "turnover"):
                values = window[source]
                if values.notna().all() and np.isfinite(values.to_numpy(float)).all():
                    result.at[index, f"prior_{days}d_avg_market_{source}"] = float(
                        values.mean()
                    )
    return result.drop(columns="exchange_trade_date").reindex(columns=TRADE_COLUMNS)
```

- [ ] **Step 4: 从真实入场分钟抽取交易所交易日**

在 `runner.py` 增加 `_entry_trade_dates`。它按 `symbol` 找到该品种 `loaded.minute_bars` 中 `bar_end == entry_time` 的行，输出唯一的 `candidate_id, exchange_trade_date`；夜盘成交直接沿用行情中的下一交易日归属。`run()` 将 `daily_actual`、该映射以及已加载 root symbols 传给 `publish_backtest_report`。

- [ ] **Step 5: 运行流动性测试并确认通过**

Run:

```bash
python3 -m pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k "market_liquidity or entry_trade_date"
```

Expected: PASS，覆盖夜盘日期、排除入场日、完整窗口、窗口不足和换月连续链。

### Task 2: 生成每品种绩效文件

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/report.py:20-210`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: 写入失败测试，固定指标公式**

增加 AG 三笔交易 `+100, -40, -80` 和 AU 零交易的测试。断言：

```python
assert ag["trade_count"] == 3
assert ag["total_net_pnl"] == pytest.approx(-20.0)
assert ag["total_return_pct"] == pytest.approx(-0.002)
assert ag["max_drawdown_amount"] == pytest.approx(120.0)
assert ag["max_drawdown_pct"] == pytest.approx(0.012)
assert ag["win_rate_pct"] == pytest.approx(100.0 / 3.0)
assert ag["profit_factor"] == pytest.approx(100.0 / 120.0)
assert ag["average_win_loss_ratio"] == pytest.approx(100.0 / 60.0)
assert au["trade_count"] == 0
assert au["total_net_pnl"] == 0.0
assert pd.isna(au["win_rate_pct"])
```

初始资金固定为 `1_000_000`，曲线从零开始，因此 AG 从高点 `+100` 回落到 `-20` 的最大回撤为 `120`。

- [ ] **Step 2: 运行测试并确认因构建函数不存在而失败**

Run:

```bash
python3 -m pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k symbol_performance
```

Expected: FAIL with `_build_symbol_performance` missing。

- [ ] **Step 3: 实现每品种一行统计**

在 `report.py` 定义固定 `SYMBOL_PERFORMANCE_COLUMNS`，实现 `_build_symbol_performance(trades, symbols, initial_equity)`：

```python
ordered = symbol_trades.sort_values(
    ["exit_time", "candidate_id"], kind="stable"
)
pnl = pd.to_numeric(ordered["net_pnl"], errors="raise")
curve = np.concatenate(([0.0], pnl.cumsum().to_numpy(float)))
drawdown = np.maximum.accumulate(curve) - curve
max_drawdown_amount = float(drawdown.max())
```

按设计文档计算交易数、盈利/亏损/持平笔数、百分比胜率、毛净收益、收益率、回撤、利润因子、平均盈亏比、平均盈亏、期望 R、手续费、滑点和策略成交额。品种集合取 `loaded root symbols` 与交易表实际 symbols 的并集，确保零交易品种保留。

- [ ] **Step 4: 把统计接入报告输出**

扩展 `publish_backtest_report` 的关键字参数：

```python
daily_market_bars: pd.DataFrame | None = None,
entry_trade_dates: pd.DataFrame | None = None,
symbols: Sequence[str] = (),
```

写盘前生成带流动性列的 `reported_trades`，并让组合绩效、漏斗和现有分组报告继续读取同一逻辑交易，只忽略新增列。在 `tables` 增加：

```python
"performance_by_symbol.csv": symbol_performance,
```

- [ ] **Step 5: 运行分品种测试并确认通过**

Run:

```bash
python3 -m pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k symbol_performance
```

Expected: PASS。

### Task 3: 生成每日权益静态图并完成集成验证

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/report.py:1-140`
- Modify: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py:2725-2890`

- [ ] **Step 1: 写入失败测试，要求报告目录包含 PNG**

扩展现有 `test_report_contains_metrics_command_and_audit_tables`：

```python
assert "performance_by_symbol.csv" in {path.name for path in output.iterdir()}
chart = output / "daily_equity_curve.png"
assert chart.is_file()
assert chart.stat().st_size > 0
```

另建空 `daily_equity` 用例，调用图形函数后同样断言图片存在且非空。

- [ ] **Step 2: 运行两个图形测试并确认失败**

Run:

```bash
python3 -m pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k daily_equity_chart
```

Expected: FAIL，原因是 `daily_equity_curve.png` 尚未生成。

- [ ] **Step 3: 用无界面 Matplotlib 实现静态图**

在 `report.py` 使用 `Agg` 后端，增加 `_render_daily_equity_chart(path, daily_equity, initial_equity)`：

```python
dates = pd.to_datetime(daily_equity["date"], errors="raise")
equity_pct = 100.0 * pd.to_numeric(
    daily_equity["equity"], errors="raise"
) / initial_equity
ax.plot(dates, equity_pct, color="#136f63", linewidth=2.0)
ax.axhline(100.0, color="#8b5e34", linestyle="--", linewidth=1.0)
ax.xaxis.set_major_locator(mdates.DayLocator(interval=5))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
ax.yaxis.set_major_locator(MaxNLocator(nbins=8))
ax.yaxis.set_major_formatter(PercentFormatter(xmax=100.0))
```

增加水平点状网格、标签旋转和紧凑布局。空表时保留坐标轴并写入 `No daily equity data`。保存到 `daily_equity_curve.png` 后关闭 figure。

- [ ] **Step 4: 扩展报告集成测试**

读取 `performance_by_symbol.csv`，核对完整列序和 AG 指标；读取 `trades.csv`，核对六个流动性字段的位置和值；重新断言 `summary["official_performance"]["total_return"]` 等于 `daily_equity` 推导值，证明新增同名统计没有改变组合绩效。

- [ ] **Step 5: 运行目标测试和完整回归**

Run:

```bash
python3 -m pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py
python3 -m pytest -q \
  cta/strategy/tests/test_multi_timeframe_trend_strategy.py \
  cta/strategy/tests/test_multi_timeframe_trend_management.py \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py
python3 -m py_compile \
  cta/strategy/multi_timeframe_trend_backtest/engine.py \
  cta/strategy/multi_timeframe_trend_backtest/report.py \
  cta/strategy/multi_timeframe_trend_backtest/runner.py
```

Expected: 所有测试 PASS、语法检查退出码为 `0`。最后运行 `git diff --check`，并确认没有提交或覆盖工作树中与本需求无关的修改。
