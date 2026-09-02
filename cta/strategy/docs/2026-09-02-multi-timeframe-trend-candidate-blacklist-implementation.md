# 多周期趋势候选黑名单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 默认从多周期趋势策略候选数据中彻底删除 `pullback_breakout` 和全部空头，仅保留多头 `always_in`。

**Architecture:** 黑名单保存在 `MultiTimeframeTrendConfig`，候选生成器在创建候选行之前按方向和形态过滤。回测 runner 将生效配置写入报告上下文；下载与执行逻辑保持不变。

**Tech Stack:** Python 3.10+、dataclasses、pandas、pytest、Ruff。

---

### Task 1: 配置默认值与校验

**Files:**
- Modify: `cta/config/multi_timeframe_trend_config.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

- [ ] **Step 1: 写默认值和非法配置的失败测试**

```python
def test_config_defaults_candidate_blacklist() -> None:
    config = MultiTimeframeTrendConfig()
    assert config.candidate_setup_blacklist == ("pullback_breakout",)
    assert config.candidate_direction_blacklist == (-1,)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"candidate_setup_blacklist": ("unknown",)}, "candidate_setup_blacklist"),
        ({"candidate_setup_blacklist": ("always_in", "always_in")}, "candidate_setup_blacklist"),
        ({"candidate_direction_blacklist": (0,)}, "candidate_direction_blacklist"),
        ({"candidate_direction_blacklist": (-1, -1)}, "candidate_direction_blacklist"),
    ],
)
def test_config_rejects_invalid_candidate_blacklist(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        MultiTimeframeTrendConfig(**overrides)
```

- [ ] **Step 2: 运行测试并确认因字段不存在或未校验而失败**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -k 'candidate_blacklist' -q`

Expected: FAIL，默认配置缺少 `candidate_setup_blacklist`。

- [ ] **Step 3: 增加最小配置实现**

```python
candidate_setup_blacklist: tuple[str, ...] = ("pullback_breakout",)
candidate_direction_blacklist: tuple[int, ...] = (-1,)

setup_blacklist = tuple(self.candidate_setup_blacklist)
if (
    len(setup_blacklist) != len(set(setup_blacklist))
    or any(value not in {"always_in", "pullback_breakout"} for value in setup_blacklist)
):
    raise ValueError("candidate_setup_blacklist contains duplicate or unknown setup")
direction_blacklist = tuple(self.candidate_direction_blacklist)
if (
    len(direction_blacklist) != len(set(direction_blacklist))
    or any(value not in {-1, 1} for value in direction_blacklist)
):
    raise ValueError("candidate_direction_blacklist contains duplicate or invalid direction")
```

- [ ] **Step 4: 运行配置测试并确认通过**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -k 'candidate_blacklist' -q`

Expected: PASS。

### Task 2: 候选生成前彻底删除黑名单机会

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_strategy.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_strategy.py`

- [ ] **Step 1: 写默认删除和清空后恢复的失败测试**

```python
def test_default_candidate_blacklist_keeps_only_long_always_in() -> None:
    daily, minute5, config = _strategy_inputs()
    out = generate_multi_timeframe_candidates(
        daily, minute5, instrument=_instrument(), config=config, equity=100_000.0
    )
    assert not out.empty
    assert set(out["signal_type"]) == {"always_in"}
    assert set(out["direction"]) == {"long"}


def test_clearing_candidate_blacklist_restores_pullback_and_short() -> None:
    config = MultiTimeframeTrendConfig(
        candidate_setup_blacklist=(), candidate_direction_blacklist=()
    )
    assert config.candidate_setup_blacklist == ()
    assert config.candidate_direction_blacklist == ()
```

现有 `test_pullback_target_remains_virtual_until_actual_fill` 和空头生成测试显式构造空黑名单，继续验证原始两类形态及多空对称性。

- [ ] **Step 2: 运行候选测试并确认默认输出仍含黑名单机会而失败**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -k 'default_candidate_blacklist or pullback_target or generate_short' -q`

Expected: FAIL，默认生成器尚未读取黑名单。

- [ ] **Step 3: 在候选行创建前应用方向与形态黑名单**

```python
if direction in cfg.candidate_direction_blacklist:
    continue

allowed_pullback = (
    pullback is not None
    and pullback.setup_type not in cfg.candidate_setup_blacklist
)
if allowed_pullback:
    candidates.append((pullback, ""))
if (
    always_in is not None
    and always_in.setup_type not in cfg.candidate_setup_blacklist
):
    duplicate_reason = "DUPLICATE_SIGNAL" if allowed_pullback else ""
    candidates.append((always_in, duplicate_reason))
```

- [ ] **Step 4: 增加同刻回调不压制 Always-In 的测试**

使用 `monkeypatch` 令 `advance_pullback_state` 和 `detect_always_in` 在同一时刻返回候选，断言默认黑名单输出的 `always_in.filtered_reason == ""`，证明已删除回调不会产生 `DUPLICATE_SIGNAL`。

- [ ] **Step 5: 运行策略测试并确认通过**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_strategy.py -q`

Expected: PASS。

### Task 3: 报告记录生效黑名单

**Files:**
- Modify: `cta/strategy/multi_timeframe_trend_backtest/runner.py`
- Test: `cta/strategy/tests/test_multi_timeframe_trend_backtest.py`

- [ ] **Step 1: 写报告上下文失败测试**

在 runner 的空品种回放测试中捕获传给 `publish_backtest_report` 的 `context`，加入：

```python
assert captured_context["candidate_blacklist"] == {
    "setup_types": ["pullback_breakout"],
    "directions": [-1],
}
```

- [ ] **Step 2: 运行该测试并确认字段缺失**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k 'candidate_blacklist_context' -q`

Expected: FAIL with `KeyError: 'candidate_blacklist'`。

- [ ] **Step 3: 将配置写入回测上下文**

```python
"candidate_blacklist": {
    "setup_types": list(config.candidate_setup_blacklist),
    "directions": list(config.candidate_direction_blacklist),
},
```

- [ ] **Step 4: 运行上下文测试并确认通过**

Run: `python3 -m pytest cta/strategy/tests/test_multi_timeframe_trend_backtest.py -k 'candidate_blacklist_context' -q`

Expected: PASS。

### Task 4: 中文文档、变更日志与全量验证

**Files:**
- Modify: `cta/strategy/docs/2026-08-31-multi_timeframe_trend_strategy.md`
- Modify: `cta/report/change_log.md`

- [ ] **Step 1: 更新策略文档**

写明当前默认候选集合为多头 `always_in`，`pullback_breakout` 和空头在候选生成前删除；说明恢复方式是编辑 `MultiTimeframeTrendConfig` 两个黑名单字段。保留原有 1 分钟下载命令，不增加任何下载参数。

- [ ] **Step 2: 更新变更日志**

记录修改文件、黑名单输出语义、运行命令、输出目录、验证结果和“历史回测交易数会下降”的行为变化。

- [ ] **Step 3: 运行聚焦和回归测试**

Run:

```bash
python3 -m pytest \
  cta/strategy/tests/test_multi_timeframe_trend_strategy.py \
  cta/strategy/tests/test_multi_timeframe_trend_management.py \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py -q
```

Expected: 全部 PASS。

- [ ] **Step 4: 运行静态与编译检查**

Run:

```bash
python3 -m ruff check \
  cta/config/multi_timeframe_trend_config.py \
  cta/strategy/multi_timeframe_trend_strategy.py \
  cta/strategy/multi_timeframe_trend_backtest/runner.py \
  cta/strategy/tests/test_multi_timeframe_trend_strategy.py \
  cta/strategy/tests/test_multi_timeframe_trend_backtest.py
python3 -m compileall -q \
  cta/config/multi_timeframe_trend_config.py \
  cta/strategy/multi_timeframe_trend_strategy.py \
  cta/strategy/multi_timeframe_trend_backtest/runner.py
```

Expected: Ruff 输出 `All checks passed!`，compileall 退出码为 0。

- [ ] **Step 5: 检查改动范围**

Run: `git status --short`

Expected: 本任务只修改上述 CTA 配置、策略、runner、测试和中文文档；不包含下载器或新 CLI 参数。
