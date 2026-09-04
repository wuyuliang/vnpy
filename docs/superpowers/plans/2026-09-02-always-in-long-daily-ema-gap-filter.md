# Always-In 多头日线 EMA 间距过滤 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `always_in LONG` 增加默认 `2%` 的已完成日线 EMA5/EMA20 间距下限，并完整记录过滤原因和回测配置。

**Architecture:** 在冻结配置中定义并校验阈值，在候选行生成时利用已经因果对齐的 `daily_ema5`、`daily_ema20` 判断是否过滤。过滤后的机会继续进入候选和拒绝审计，但不会进入计划和订单；回测摘要和中文策略文档记录参数，不增加 CLI。

**Tech Stack:** Python 3、dataclasses、pandas、NumPy、pytest

---

### Task 1: 配置默认值与校验

**Files:**
- Modify: `cta/config/multi_timeframe_trend_config.py:9-100`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py:35-110`

- [ ] **Step 1: 写默认值失败测试**

在 `test_config_defaults_candidate_blacklist` 附近新增：

```python
def test_config_defaults_always_in_long_daily_ema_gap() -> None:
    config = MultiTimeframeTrendConfig()

    assert config.always_in_long_daily_ema_gap_min_ratio == pytest.approx(0.02)
```

- [ ] **Step 2: 运行测试并确认因缺少字段失败**

Run: `pytest -q cta/strategy/tests/test_multi_timeframe_trend_strategy.py::test_config_defaults_always_in_long_daily_ema_gap`

Expected: FAIL，`MultiTimeframeTrendConfig` 没有 `always_in_long_daily_ema_gap_min_ratio`。

- [ ] **Step 3: 写非法值失败测试**

```python
@pytest.mark.parametrize("value", [-0.01, 1.0, float("nan"), float("inf")])
def test_config_rejects_invalid_always_in_long_daily_ema_gap(value: float) -> None:
    with pytest.raises(
        ValueError,
        match="always_in_long_daily_ema_gap_min_ratio",
    ):
        MultiTimeframeTrendConfig(
            always_in_long_daily_ema_gap_min_ratio=value,
        )
```

- [ ] **Step 4: 实现最小配置字段和校验**

在日线参数附近新增字段：

```python
always_in_long_daily_ema_gap_min_ratio: float = 0.02
```

在 `__post_init__` 中新增：

```python
ema_gap_min = float(self.always_in_long_daily_ema_gap_min_ratio)
if not math.isfinite(ema_gap_min) or not 0 <= ema_gap_min < 1:
    raise ValueError(
        "always_in_long_daily_ema_gap_min_ratio must be finite and in [0, 1)"
    )
```

同时在配置模块导入 `math`。

- [ ] **Step 5: 运行配置测试并确认通过**

Run: `pytest -q cta/strategy/tests/test_multi_timeframe_trend_strategy.py::test_config_defaults_always_in_long_daily_ema_gap cta/strategy/tests/test_multi_timeframe_trend_strategy.py::test_config_rejects_invalid_always_in_long_daily_ema_gap`

Expected: PASS。

### Task 2: 候选阶段过滤

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_strategy.py:205-285`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py:380-520`

- [ ] **Step 1: 写低于阈值和等于阈值的失败测试**

使用现有 `_strategy_inputs()` 生成一份阈值为零的基准候选，从候选审计字段计算实际 EMA 间距，再验证阈值两侧：

```python
def test_always_in_long_requires_configured_daily_ema_gap() -> None:
    daily, minute5, config = _strategy_inputs()
    minute5["open"] = minute5["close"] / 1.001
    baseline = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=replace(
            config,
            always_in_long_daily_ema_gap_min_ratio=0.0,
        ),
        equity=100_000.0,
    )
    first = baseline.loc[baseline["signal_type"] == "always_in"].iloc[0]
    gap = (float(first["daily_ema5"]) - float(first["daily_ema20"])) / float(
        first["daily_ema20"]
    )

    filtered = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=replace(
            config,
            always_in_long_daily_ema_gap_min_ratio=gap + 1e-6,
        ),
        equity=100_000.0,
    )
    filtered_first = filtered.loc[filtered["signal_type"] == "always_in"].iloc[0]
    assert filtered_first["filtered_reason"] == "DAILY_EMA_GAP_BELOW_MIN"
    assert filtered_first["rejection_code"] == "DAILY_EMA_GAP_BELOW_MIN"
    assert filtered_first["quantity"] == 0

    boundary = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=replace(
            config,
            always_in_long_daily_ema_gap_min_ratio=gap,
        ),
        equity=100_000.0,
    )
    boundary_first = boundary.loc[boundary["signal_type"] == "always_in"].iloc[0]
    assert boundary_first["filtered_reason"] == ""
```

- [ ] **Step 2: 运行测试并确认过滤行为缺失**

Run: `pytest -q cta/strategy/tests/test_multi_timeframe_trend_strategy.py::test_always_in_long_requires_configured_daily_ema_gap`

Expected: FAIL，低于阈值的候选仍未返回 `DAILY_EMA_GAP_BELOW_MIN`。

- [ ] **Step 3: 实现最小过滤逻辑**

在 `_candidate_row` 中读取已经对齐的日线 EMA，并仅为 `always_in LONG` 计算原因：

```python
daily_ema5 = float(row.get("daily_ema5", np.nan))
daily_ema20 = float(row.get("daily_ema20", np.nan))
daily_ema_gap_reason = ""
if candidate.setup_type == "always_in" and candidate.direction > 0:
    daily_ema_gap_ratio = (daily_ema5 - daily_ema20) / daily_ema20
    if daily_ema_gap_ratio < config.always_in_long_daily_ema_gap_min_ratio:
        daily_ema_gap_reason = "DAILY_EMA_GAP_BELOW_MIN"
```

将原因优先级调整为：

```python
reason = (
    forced_reason
    or metadata_reason
    or daily_ema_gap_reason
    or daily_breakout_reason
    or obstacle.reason
    or risk.reason
)
```

候选输出复用局部变量 `daily_ema5`、`daily_ema20`，避免重复读取。

- [ ] **Step 4: 写非目标形态和方向不受影响的测试**

扩展现有 pullback 与空头测试：给配置设置很高但合法的 `0.99` 阈值，断言 `pullback_breakout LONG` 与 `always_in SHORT` 的 `filtered_reason` 都不是 `DAILY_EMA_GAP_BELOW_MIN`。

```python
assert pullback["filtered_reason"] != "DAILY_EMA_GAP_BELOW_MIN"
assert (shorts["filtered_reason"] != "DAILY_EMA_GAP_BELOW_MIN").all()
```

- [ ] **Step 5: 运行候选生成测试并确认通过**

Run: `pytest -q cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

Expected: PASS。

### Task 3: 回测摘要与中文文档

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py:420-440`
- Modify: `cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md:34-110`
- Modify: `cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md:400-425`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py:1752-1815`

- [ ] **Step 1: 写回测摘要失败测试**

在 `test_trend_runner_reports_candidate_blacklist_context` 中增加：

```python
assert captured_context["daily_filters"] == {
    "always_in_long_daily_ema_gap_min_ratio": 0.02,
}
```

- [ ] **Step 2: 运行测试并确认缺少摘要字段**

Run: `pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_trend_runner_reports_candidate_blacklist_context`

Expected: FAIL，`captured_context` 没有 `daily_filters`。

- [ ] **Step 3: 在回测摘要中记录配置**

在 `run_from_args` 的报告上下文中新增：

```python
"daily_filters": {
    "always_in_long_daily_ema_gap_min_ratio": (
        config.always_in_long_daily_ema_gap_min_ratio
    ),
},
```

- [ ] **Step 4: 更新中文策略文档**

参数表新增：

```markdown
| `always_in_long_daily_ema_gap_min_ratio` | 2% | Always-In 多头日线 EMA5/EMA20 最小间距 |
```

多头许可说明新增：

```text
对于 always_in 多头，还必须满足：
(EMA5 - EMA20) / EMA20 >= 0.02
```

明确该规则只使用已完成日线、恰好等于阈值时放行、不影响 `pullback_breakout` 和空头。拒绝原因表新增：

```markdown
| `DAILY_EMA_GAP_BELOW_MIN` | Always-In 多头日线 EMA5/EMA20 间距低于配置下限 |
```

- [ ] **Step 5: 运行摘要测试并确认通过**

Run: `pytest -q cta/strategy/tests/test_multi_timeframe_trend_backtest.py::test_trend_runner_reports_candidate_blacklist_context`

Expected: PASS。

### Task 4: 回归验证与改动审计

**Files:**
- Verify: `cta/config/multi_timeframe_trend_config.py`
- Verify: `cta/strategy/multi_timeframe_trend_strategy.py`
- Verify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Verify: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`
- Verify: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`
- Verify: `cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md`

- [ ] **Step 1: 运行策略与回测完整测试**

Run: `pytest -q cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

Expected: PASS，无 warning 或 error。

- [ ] **Step 2: 检查配置只存在于配置文件而非 CLI**

Run: `rg -n "always_in_long_daily_ema_gap_min_ratio|DAILY_EMA_GAP_BELOW_MIN" cta/config cta/strategy/multi_timeframe_trend_strategy.py cta/strategy/multi_timeframe_trend_backtest/runner.py cta/strategy/tests cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md`

Expected: 配置、过滤、摘要、测试和文档均有命中，CLI parser 没有新增选项。

- [ ] **Step 3: 检查最终差异只包含本功能所需改动**

Run: `git diff -- cta/config/multi_timeframe_trend_config.py cta/strategy/multi_timeframe_trend_strategy.py cta/strategy/multi_timeframe_trend_backtest/runner.py cta/strategy/tests/test_multi_timeframe_trend_strategy.py cta/strategy/tests/test_multi_timeframe_trend_backtest.py cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md`

Expected: 每一处改动都能对应配置、过滤、审计、测试或文档要求；不包含无关重构。
