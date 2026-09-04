# 品种连续亏损冷却 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在正式组合回放中实现“同品种两笔亏损在 48 小时内发生后，至少冷却 24 小时并等到下一个日盘 09:00 才允许新开仓”，并给出 AG 完整回测结果。

**Architecture:** 在现有组合事件循环内为每个根品种维护轻量冷却状态，只在完整逻辑交易形成后更新；候选仍保留，冷却期间通过 `SYMBOL_LOSS_COOLDOWN` 拒绝。配置只存在于 `MultiTimeframeTrendConfig`，runner 将配置写入摘要，但不增加 CLI。

**Tech Stack:** Python 3、dataclasses、pandas、pytest、现有 CTA 组合回放与报告模块。

---

### Task 1: 配置契约

**Files:**
- Modify: `cta/config/multi_timeframe_trend_config.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

- [ ] **Step 1: 写默认值失败测试**

```python
def test_config_defaults_symbol_loss_cooldown() -> None:
    config = MultiTimeframeTrendConfig()

    assert config.symbol_loss_cooldown_enabled is True
    assert config.symbol_loss_pair_window_hours == 48
    assert config.symbol_loss_cooldown_hours == 24
```

- [ ] **Step 2: 运行测试并确认因字段缺失而失败**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py::test_config_defaults_symbol_loss_cooldown -q`

Expected: FAIL，提示 `MultiTimeframeTrendConfig` 没有冷却配置字段。

- [ ] **Step 3: 写非法值失败测试**

```python
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"symbol_loss_cooldown_enabled": 1}, "symbol_loss_cooldown_enabled"),
        ({"symbol_loss_pair_window_hours": 0}, "symbol_loss_pair_window_hours"),
        ({"symbol_loss_pair_window_hours": 1.5}, "symbol_loss_pair_window_hours"),
        ({"symbol_loss_cooldown_hours": True}, "symbol_loss_cooldown_hours"),
    ],
)
def test_config_rejects_invalid_symbol_loss_cooldown(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        MultiTimeframeTrendConfig(**overrides)
```

- [ ] **Step 4: 实现最小配置和校验**

在动态仓位配置附近增加：

```python
symbol_loss_cooldown_enabled: bool = True
symbol_loss_pair_window_hours: int = 48
symbol_loss_cooldown_hours: int = 24
```

在 `__post_init__` 中加入严格校验：

```python
if type(self.symbol_loss_cooldown_enabled) is not bool:
    raise ValueError("symbol_loss_cooldown_enabled must be bool")
for name in ("symbol_loss_pair_window_hours", "symbol_loss_cooldown_hours"):
    value = getattr(self, name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
```

- [ ] **Step 5: 运行配置定向测试**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: PASS。

### Task 2: 冷却状态与时间边界

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: 写时间状态失败测试**

测试直接构造 `_SymbolLossCooldownState` 和只含 `exit_time/net_pnl` 的交易，依次覆盖：恰好 `48h` 触发、超过 `48h` 不触发、零收益清除上一笔亏损、`14:30 + 24h` 对齐为隔日后的 `09:00`、恢复时刻恰好放行。

核心断言：

```python
state = trend_engine._SymbolLossCooldownState()
config = MultiTimeframeTrendConfig()
first = {"exit_time": pd.Timestamp("2026-01-05 14:30", tz=TZ), "net_pnl": -100.0}
second = {"exit_time": pd.Timestamp("2026-01-07 14:30", tz=TZ), "net_pnl": -50.0}

trend_engine._advance_symbol_loss_cooldown(state, trade=first, config=config)
trend_engine._advance_symbol_loss_cooldown(state, trade=second, config=config)

assert state.cooldown_release_time == pd.Timestamp("2026-01-09 09:00", tz=TZ)
assert trend_engine._symbol_loss_cooldown_detail(
    state, pd.Timestamp("2026-01-09 08:59", tz=TZ), config
)
assert not trend_engine._symbol_loss_cooldown_detail(
    state, pd.Timestamp("2026-01-09 09:00", tz=TZ), config
)
```

- [ ] **Step 2: 运行测试并确认因状态机缺失而失败**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k "symbol_loss_cooldown_state" -q`

Expected: FAIL，提示冷却状态或函数不存在。

- [ ] **Step 3: 实现最小状态机**

在现有动态仓位状态旁新增：

```python
@dataclass
class _SymbolLossCooldownState:
    last_loss_exit_time: pd.Timestamp | None = None
    cooldown_trigger_time: pd.Timestamp | None = None
    cooldown_release_time: pd.Timestamp | None = None
```

实现三个小函数：

```python
def _next_day_open_after(minimum_time: pd.Timestamp) -> pd.Timestamp:
    release = minimum_time.normalize() + pd.Timedelta(hours=9)
    if release < minimum_time:
        release += pd.Timedelta(days=1)
    return release


def _advance_symbol_loss_cooldown(state, *, trade, config) -> None:
    if not config.symbol_loss_cooldown_enabled:
        return
    exit_time = pd.Timestamp(trade["exit_time"])
    if float(trade["net_pnl"]) >= 0:
        state.last_loss_exit_time = None
        state.cooldown_trigger_time = None
        state.cooldown_release_time = None
        return
    previous = state.last_loss_exit_time
    state.last_loss_exit_time = exit_time
    if previous is None:
        return
    elapsed = exit_time - previous
    if pd.Timedelta(0) <= elapsed <= pd.Timedelta(
        hours=config.symbol_loss_pair_window_hours
    ):
        state.cooldown_trigger_time = exit_time
        minimum = exit_time + pd.Timedelta(hours=config.symbol_loss_cooldown_hours)
        state.cooldown_release_time = _next_day_open_after(minimum)


def _symbol_loss_cooldown_detail(state, timestamp, config) -> str:
    release = state.cooldown_release_time
    if not config.symbol_loss_cooldown_enabled or release is None or timestamp >= release:
        return ""
    return (
        f"second_loss_exit={state.cooldown_trigger_time.isoformat()};"
        f"release_at={release.isoformat()}"
    )
```

- [ ] **Step 4: 运行状态机测试**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k "symbol_loss_cooldown_state" -q`

Expected: PASS。

### Task 3: 接入正式组合事件循环

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/engine.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: 写同刻退出/候选失败测试**

构造三笔 AG 机会：前两笔在 `48h` 内止损，第三笔信号与第二笔最终平仓同刻。断言第三笔没有计划和成交，且 `rejections.reason_code == "SYMBOL_LOSS_COOLDOWN"`，详情同时含 `second_loss_exit` 和 `release_at`。

- [ ] **Step 2: 写恢复时间失败测试**

第二笔在 `2026-01-06 09:02` 平仓，则自然 24 小时到 `2026-01-07 09:02`，恢复时间应为 `2026-01-08 09:00`。断言 `2026-01-07 09:03` 的候选仍拒绝，`2026-01-08 09:00` 的候选可创建计划。

- [ ] **Step 3: 运行集成测试并确认失败**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k "portfolio_symbol_loss_cooldown" -q`

Expected: FAIL，候选尚未被冷却拒绝。

- [ ] **Step 4: 最小接入组合回放**

初始化：

```python
symbol_loss_cooldowns = {
    root: _SymbolLossCooldownState() for root in roots
}
```

仅在 `_close_position` 返回非空 `trade` 后、移除 `positions[root_symbol]` 前调用：

```python
_advance_symbol_loss_cooldown(
    symbol_loss_cooldowns[root_symbol],
    trade=trade,
    config=config,
)
```

候选通过自身 `filtered_reason` 后、创建计划前检查：

```python
cooldown_detail = _symbol_loss_cooldown_detail(
    symbol_loss_cooldowns[root_symbol], timestamp, config
)
if cooldown_detail:
    rejection_rows.append(
        _rejection(
            root_symbol,
            candidate,
            "SYMBOL_LOSS_COOLDOWN",
            detail=cooldown_detail,
        )
    )
    continue
```

- [ ] **Step 5: 验证部分减仓只更新一次**

在现有隔夜部分减仓测试中捕获 `_advance_symbol_loss_cooldown` 调用，断言两个退出腿只产生一次调用，传入值等于最终 `trades.iloc[0]["net_pnl"]`。

- [ ] **Step 6: 运行全部回放测试**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: PASS。

### Task 4: 摘要与中文策略文档

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Modify: `cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: 写 runner 上下文失败测试**

在现有 `run_from_args` 捕获上下文测试中增加：

```python
assert captured_context["symbol_loss_cooldown_config"] == {
    "enabled": True,
    "pair_window_hours": 48,
    "cooldown_hours": 24,
    "release_time": "09:00",
}
```

- [ ] **Step 2: 运行测试并确认缺少摘要键**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k "run_from_args" -q`

Expected: FAIL，缺少 `symbol_loss_cooldown_config`。

- [ ] **Step 3: 写入回测上下文**

在 runner 的 `context` 中增加上述四个稳定字段，值来自 `config`，其中 `release_time` 固定为 `"09:00"`。

- [ ] **Step 4: 更新中文策略文档**

在动态仓位章节后增加独立“连续亏损冷却”小节，明确最终平仓、实际净盈亏、`<=48h`、自然 `24h`、下一个 `09:00`、部分减仓不计数和无 CLI；拒绝码表增加 `SYMBOL_LOSS_COOLDOWN`，策略流程增加最终平仓后更新冷却和候选入场前检查。

- [ ] **Step 5: 运行两个完整测试文件**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q`

Expected: PASS。

### Task 5: AG 启用/禁用对比回测

**Files:**
- Output only: `cta/strategy/report/multi_timeframe_trend/<run-id>/`

- [ ] **Step 1: 执行启用规则的用户命令**

Run: `python3 -m cta.strategy.multi_timeframe_trend_backtest.runner --symbols AG --start 2025-01-01 --end 2026-07-01 --initial-equity 1e+06 --download-minute-data`

Expected: 生成新的完整报告目录，`summary.json.symbol_loss_cooldown_config.enabled == true`，正式状态不是由本次代码错误导致的阻断。

- [ ] **Step 2: 用同一数据执行禁用规则基线**

不增加 CLI。通过进程内调用 `runner.run_from_args`，仅把 runner 使用的配置构造器替换为 `symbol_loss_cooldown_enabled=False`，指定独立 `run_id`，且不重复下载数据。除启用开关外，其余参数和代码完全一致。

- [ ] **Step 3: 核对报告完整性**

检查两份 `summary.json`、`trades.csv`、`rejections.csv`、`daily_equity.csv` 和 `RUN_COMMAND.sh`。确认启用结果的 `SYMBOL_LOSS_COOLDOWN` 数量大于等于零，并核对 EMA 间距阈值仍为 `0.02`。

- [ ] **Step 4: 输出准确比较**

报告两次回放的完整交易数、净收益、总收益率、最大回撤金额、最大回撤比例，以及启用规则后被冷却拒绝的候选数；说明该结果是当前完整策略配置下的历史回测，不将其表述为样本外保证。
