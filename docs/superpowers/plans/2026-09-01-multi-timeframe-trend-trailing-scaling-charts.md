# 多周期趋势策略跟踪止损、动态仓位与图表升级实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有实际合约组合回放中实现日线实体五日突破过滤、回调突破跟踪止损、居中且完整显示目标的三周期图表，以及可审计的品种/组合动态减仓状态。

**Architecture:** 候选层计算信号时可见的五日日线实体边界并保留过滤候选；组合事件引擎在平仓后更新缩放状态、在成交前应用独立系数，并把状态事件写入回放产物。图表继续复用现有蜡烛图工具，只增加可选槽位和价格边界；报告从回放产物直接生成审计表和汇总。

**Tech Stack:** Python 3、pandas、NumPy、Pillow、pytest、Ruff。

---

### Task 1：配置与日线实体五日突破过滤

**Files:**
- Modify: `cta/config/multi_timeframe_trend_config.py`
- Modify: `cta/strategy/multi_timeframe_trend_rules.py`
- Modify: `cta/strategy/multi_timeframe_trend_strategy.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

- [ ] **Step 1：先写失败测试**

增加配置默认值/校验测试，并构造影线突破但实体未突破、实体恰好突破、多空对称和不足五根日线四组候选测试。关键断言：

```python
assert config.symbol_loss_streak == 2
assert config.symbol_position_scale == pytest.approx(0.5)
assert config.portfolio_drawdown_threshold == pytest.approx(0.01)
assert config.portfolio_position_scale == pytest.approx(0.5)
assert candidate["prior_5d_high"] == max(prior[["open", "close"]].to_numpy().ravel())
assert candidate["filtered_reason"] == "DAILY_FIVE_BAR_BREAKOUT_NOT_MET"
```

- [ ] **Step 2：运行聚焦测试确认 RED**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q -k 'five_bar or position_scale or drawdown_threshold'`

Expected: 因配置字段、实体边界字段和拒绝码尚不存在而失败。

- [ ] **Step 3：实现最小候选过滤**

在配置中增加四个仅文件可编辑参数并校验。`build_daily_context` 使用当前行之前的数据：

```python
body_high = result[["open", "close"]].max(axis=1)
body_low = result[["open", "close"]].min(axis=1)
result["prior_5d_high"] = body_high.shift(1).rolling(5, min_periods=5).max()
result["prior_5d_low"] = body_low.shift(1).rolling(5, min_periods=5).min()
```

把两个字段对齐到 5 分钟候选；在 `_candidate_row` 中先计算五日过滤原因，再与元数据、障碍和风险原因按既有优先级组合。扩展 `CANDIDATE_COLUMNS` 和审计行。

- [ ] **Step 4：运行聚焦与前缀不变性测试确认 GREEN**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: 全部通过，追加未来日线不改变历史候选。

### Task 2：回调突破跟踪止损与实际目标审计

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1：先写失败测试**

增加回调突破在 5 分钟管理事件后推进止损的回放测试，并断言目标仍按实际跳空成交冻结：

```python
trade = artifacts.trades.iloc[0]
assert trade["exit_reason"] == "STOP"
assert trade["exit_price"] == pytest.approx(advanced_stop)
assert trade["final_target"] == pytest.approx(actual_fill + 2 * (actual_fill - structural_stop))
```

同时保留同分钟止损与目标触及的止损优先断言。

- [ ] **Step 2：运行聚焦测试确认 RED**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q -k 'pullback and trailing'`

Expected: 回调突破止损未推进或交易表没有 `final_target`。

- [ ] **Step 3：实现最小退出改动**

把单品种和组合回放中：

```python
elif str(position.pending.candidate["setup_type"]) == "always_in":
    _advance_position_stop(...)
```

改为所有持仓均调用 `_advance_position_stop`。`_Position.target` 继续在实际成交时冻结；`TRADE_COLUMNS` 和 `_close_position` 增加 `final_target`，Always-In 写 `NaN`。

- [ ] **Step 4：运行全部趋势回放测试确认 GREEN**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: 全部通过。

### Task 3：品种与组合仓位缩放状态机

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1：为纯状态转换写失败测试**

为品种状态覆盖两亏触发、零收益打断、亏损扩缺口、部分盈利和严格超额恢复；为组合状态覆盖恰好 1% 不触发、超过 1% 触发和收复高水位恢复：

```python
symbol = _SymbolScalingState()
assert _advance_symbol_scaling(symbol, -60.0, config).active is False
assert _advance_symbol_scaling(symbol, -40.0, config).recovery_deficit == 100.0
assert _advance_symbol_scaling(symbol, 100.0, config).active is True
assert _advance_symbol_scaling(symbol, 0.01, config).active is False
```

- [ ] **Step 2：运行状态测试确认 RED**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q -k 'scaling_state'`

Expected: 状态类型和推进函数尚不存在。

- [ ] **Step 3：实现引擎内小型状态类型和事件行**

增加 `_SymbolScalingState`、`_PortfolioScalingState`、`SCALING_EVENT_COLUMNS` 和状态推进函数。事件类型严格使用 `TRIGGER/EXTEND/PROGRESS/RECOVER`；组合高水位基于扣费后已实现 `cash`。

- [ ] **Step 4：运行状态测试确认 GREEN**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q -k 'scaling_state'`

Expected: 全部通过。

### Task 4：把动态缩放接入组合成交与回放产物

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1：写组合回放失败测试**

构造确定的多品种交易序列，断言同一时间戳先平仓触发状态、后入场读取系数；再断言两个系数叠加和不足一手拒绝：

```python
assert scaled_trade["base_quantity"] == 8
assert scaled_trade["symbol_quantity_scale"] == pytest.approx(0.5)
assert scaled_trade["portfolio_quantity_scale"] == pytest.approx(0.5)
assert scaled_trade["quantity_scale"] == pytest.approx(0.25)
assert scaled_trade["quantity"] == 2
assert "DYNAMIC_RISK_SCALE_BELOW_ONE_LOT" in set(artifacts.rejections["reason_code"])
```

- [ ] **Step 2：运行组合测试确认 RED**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q -k 'scales_entry or same_timestamp_scaling'`

Expected: 成交手数未缩放且缺少审计字段。

- [ ] **Step 3：接入事件顺序和手数裁剪**

在每次 `_close_position` 后立即用交易 `net_pnl` 更新对应品种状态和组合状态。在 `_match_entry` 得到基础手数后、保证金裁剪前计算：

```python
scaled_quantity = math.floor(base_quantity * symbol_factor * portfolio_factor)
```

把基础手数、两个系数、总系数、两个恢复缺口冻结到 `_Position`，并写入交易行。缩放不足一手写订单取消和拒绝行。

- [ ] **Step 4：扩展回放产物**

`ReplayArtifacts` 增加 `position_scaling_events`。单品种回放使用相同状态语义；`_empty_replay`、`_blocked_replay` 和所有构造点返回带固定列的空表或事件表。

- [ ] **Step 5：运行全部回放测试确认 GREEN**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: 全部通过，原保证金与隔夜减仓测试无回归。

### Task 5：Target 全面板可见与 Signal 固定居中

**Files:**
- Modify: `cta/strategy/brooks/scalp/report.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/charts.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`
- Test: `cta/strategy/brooks/scalp/tests/test_metrics_report.py`

- [ ] **Step 1：写纯窗口和价格边界失败测试**

断言信号始终位于固定中央槽位，区间边界只产生空槽；断言目标超出行情高低时仍进入价格边界：

```python
window = _event_window(frame, signal, radius=24)
assert window.attrs["signal_slot"] == 24
assert window.attrs["plot_slot_count"] == 49
assert window.loc[signal, "_plot_slot"] == 24
assert _panel_price_bounds(window, (entry, stop, target))[1] >= target
```

- [ ] **Step 2：运行图表测试确认 RED**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q -k 'chart and (center or target)'`

Expected: 现有不对称窗口和行情范围会失败。

- [ ] **Step 3：扩展共享蜡烛图工具**

为 `_draw_candlestick_panel` 增加可选 `plot_slots`、`plot_slot_count` 和 `price_levels`；未提供时保持现有行为。计算纵轴时把所有有限 `price_levels` 与 K 线高低共同取极值，计算横轴时按槽位而非行号定位。

- [ ] **Step 4：更新机会图表数据流**

从 `trades` 按 `candidate_id` 建立 `final_target` 映射；成交回调图覆盖候选计划目标，未成交图保留计划目标。日线、1 小时和 5 分钟分别使用固定半径，并向绘图函数传中央槽位和 Entry/Stop/Target。

- [ ] **Step 5：运行图表和共享报告测试确认 GREEN**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py cta/strategy/brooks/scalp/tests -q`

Expected: 新旧图表调用均通过。

### Task 6：报告、主策略文档和端到端验证

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/report.py`
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Modify: `cta/strategy/docs/multi_timeframe_trend_strategy.md`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1：先写报告失败测试**

断言目录包含 `position_scaling_events.csv`，摘要包含四个有效配置值、触发/恢复/缩放入场计数，交易与费用审计不丢失原字段。

- [ ] **Step 2：运行报告测试确认 RED**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q -k 'report'`

Expected: 新 CSV 和汇总字段缺失。

- [ ] **Step 3：实现报告与上下文**

发布新增事件表；从事件和交易缩放字段计算汇总。在 runner 上下文中写配置文件的四个值，但不修改 parser 和复现命令参数。

- [ ] **Step 4：更新中文主策略文档**

在 `cta/strategy/docs/multi_timeframe_trend_strategy.md` 第 6 节加入：

```text
多头许可：EMA5 > EMA10 > EMA20，且候选触发价 >= 此前五根已完成日线开盘价和收盘价的最大值。
空头许可：EMA5 < EMA10 < EMA20，且候选触发价 <= 此前五根已完成日线开盘价和收盘价的最小值。
```

同步说明回调突破跟踪止损、配置文件动态仓位和新增审计文件。

- [ ] **Step 5：运行完整相关验证**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_management.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py cta/strategy/brooks/cycle_v1/tests/test_execution_metadata_adapter.py -q`

Run: `python3 -m ruff check cta/config/multi_timeframe_trend_config.py cta/strategy/multi_timeframe_trend_rules.py cta/strategy/multi_timeframe_trend_strategy.py cta/strategy/multi_timeframe_trend_backtest cta/strategy/tests/test_multi_timeframe_trend_backtest.py cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

Run: `python3 -m compileall -q cta/config/multi_timeframe_trend_config.py cta/strategy/multi_timeframe_trend_rules.py cta/strategy/multi_timeframe_trend_strategy.py cta/strategy/multi_timeframe_trend_backtest`

Expected: 全部退出码为 0。

- [ ] **Step 6：重新生成并核对真实报告**

分别以新的 `run-id` 运行 AG 和 AG+CU。核对 `COMPLETE`、候选数等于图表索引行数、所有成交回调图有 `final_target`、信号居中、手续费与保证金峰值可解释、缩放事件和交易手数一致。
