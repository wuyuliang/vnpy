# 创业板 ETF 状态覆盖与 EMA 基线图形 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `20260727_chuangyeban_regime_overlay/charts/` 中原子生成状态覆盖策略和独立 EMA 基线两张交易图，并在状态图展示 3 日五档背景及 1 日/3 日分数轨道。

**Architecture:** 新模块负责把回测信号转换为日/周状态轨道，并编排两张图片、索引和审计；现有 PIL 绘图器只增加向后兼容的可选状态图层和策略指标。回测运行器在已有 UUID 暂存目录中调用绘图模块，使图形和其他回测产物一起发布或一起回滚。

**Tech Stack:** Python 3.10+、pandas、Pillow、pytest、Ruff、现有 `render_trade_charts` 和回测结果数据结构。

---

## 文件结构

新增：

```text
stock/etf/regime_overlay_charts.py
stock/etf/tests/test_regime_overlay_charts.py
```

修改：

```text
stock/etf/render_trade_charts.py
stock/etf/tests/test_trade_charts.py
stock/etf/run_regime_overlay_backtest.py
stock/etf/tests/test_run_regime_overlay_backtest.py
stock/etf/20260727_chuangyeban_regime_overlay_results.md
```

真实生成但不强制加入 Git：

```text
stock/etf/output/20260727_chuangyeban_regime_overlay/charts/
```

## Task 1：状态边界与日/周轨道

**Files:**

- Create: `stock/etf/regime_overlay_charts.py`
- Create: `stock/etf/tests/test_regime_overlay_charts.py`

- [ ] **Step 1：写五档边界失败测试**

```python
@pytest.mark.parametrize(
    ("score", "state"),
    [
        (-3.0, "趋势向下"),
        (-2.0, "趋势向下"),
        (-1.999, "震荡向下"),
        (-1.0, "无趋势"),
        (0.0, "无趋势"),
        (1.0, "无趋势"),
        (1.001, "震荡向上"),
        (1.999, "震荡向上"),
        (2.0, "趋势向上"),
        (3.0, "趋势向上"),
    ],
)
def test_state_band_uses_strategy_boundaries(score: float, state: str) -> None:
    band = state_band(score)

    assert band.state == state
    assert band.color.startswith("#")
```

同时参数化 `NaN/Infinity/-3.01/3.01/True/"2"`，断言立即抛出 `ValueError`。

- [ ] **Step 2：运行 RED**

```bash
python3 -m pytest \
  stock/etf/tests/test_regime_overlay_charts.py::test_state_band_uses_strategy_boundaries \
  -q
```

Expected: `ModuleNotFoundError` 或 `ImportError: cannot import name 'state_band'`。

- [ ] **Step 3：实现不可变状态样式**

```python
@dataclass(frozen=True)
class StateBand:
    state: str
    color: str


STATE_COLORS = {
    "趋势向下": "#f3c1bc",
    "震荡向下": "#f3dfb1",
    "无趋势": "#e2e8f0",
    "震荡向上": "#c8e8d4",
    "趋势向上": "#9fd8b5",
}


def _validate_score(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float, np.integer, np.floating),
    ):
        raise ValueError(f"{name} must be numeric")
    score = float(value)
    if not isfinite(score) or not -3.0 <= score <= 3.0:
        raise ValueError(f"{name} must be finite and within [-3, 3]")
    return score


def state_band(score: object) -> StateBand:
    state = score_to_state(_validate_score(score, "score")).value
    return StateBand(state=state, color=STATE_COLORS[state])
```

校验函数必须拒绝布尔值、非数值、非有限值和 `[-3,3]` 外值。

- [ ] **Step 4：写日轨道和周聚合失败测试**

构造跨两周且第二周缺少部分交易日的信号，断言：

```python
daily = prepare_daily_state_tracks(signals)
weekly = aggregate_weekly_state_tracks(daily)

assert daily.columns.tolist() == [
    "datetime",
    "score_1d",
    "score_3d",
    "state_3d",
    "state_color",
]
assert weekly.iloc[0]["datetime"] == pd.Timestamp("2026-07-10")
assert weekly.iloc[0]["score_3d"] == signals.loc[4, "score_3d"]
assert weekly.iloc[1]["score_3d"] == signals.iloc[-1]["score_3d"]
```

再修改下一周信号，断言前一周聚合结果完全不变。

- [ ] **Step 5：实现日/周轨道**

```python
def prepare_daily_state_tracks(signals: pd.DataFrame) -> pd.DataFrame:
    required = {"datetime", "score_1d", "score_3d", "state_3d"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"state signals missing columns: {sorted(missing)}")
    frame = signals.loc[:, sorted(required)].copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    if frame["datetime"].dt.tz is not None:
        raise ValueError("state signal dates must be timezone-naive")
    frame["datetime"] = frame["datetime"].dt.normalize()
    if frame["datetime"].duplicated().any():
        raise ValueError("state signals contain duplicate dates")
    for column in ("score_1d", "score_3d"):
        frame[column] = frame[column].map(
            lambda score: _validate_score(score, column)
        )
    expected_states = frame["score_3d"].map(lambda score: state_band(score).state)
    if not frame["state_3d"].astype(str).eq(expected_states).all():
        raise ValueError("state_3d does not match score_3d")
    frame["state_color"] = frame["score_3d"].map(
        lambda score: state_band(score).color
    )
    return frame[
        ["datetime", "score_1d", "score_3d", "state_3d", "state_color"]
    ].sort_values("datetime", ignore_index=True)


def aggregate_weekly_state_tracks(daily: pd.DataFrame) -> pd.DataFrame:
    return (
        daily.set_index("datetime")
        .resample("W-FRI")
        .last()
        .dropna(subset=["score_1d", "score_3d"])
        .reset_index()
    )
```

实现必须验证日期唯一、时区为空、分数有限且 `state_3d` 与分数映射一致；禁止
`bfill/ffill`。

- [ ] **Step 6：验证并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_charts.py -q
python3 -m ruff check \
  stock/etf/regime_overlay_charts.py \
  stock/etf/tests/test_regime_overlay_charts.py
git add \
  stock/etf/regime_overlay_charts.py \
  stock/etf/tests/test_regime_overlay_charts.py
git commit -m "feat: prepare regime chart state tracks"
```

## Task 2：向后兼容的状态卡片

**Files:**

- Modify: `stock/etf/render_trade_charts.py:223-276`
- Modify: `stock/etf/render_trade_charts.py:279-355`
- Modify: `stock/etf/render_trade_charts.py:386-492`
- Modify: `stock/etf/tests/test_trade_charts.py`

- [ ] **Step 1：写默认卡片不变与状态卡片失败测试**

```python
def test_render_symbol_card_keeps_default_size_without_states() -> None:
    dates = pd.bdate_range("2026-01-05", periods=15)
    daily = self._bars(dates)
    trades = pd.DataFrame(
        {
            "datetime": [dates[4], dates[12]],
            "side": ["buy", "sell"],
            "fill_price": [10.0, 11.0],
            "quantity": [100, 100],
        }
    )
    image = render_symbol_card(
        symbol="159915.SZ",
        name="易方达创业板ETF",
        fund_type="股票型ETF",
        daily_bars=daily,
        weekly_bars=aggregate_weekly_bars(daily),
        trades=trades,
        latest_position=None,
        report_start=dates[0],
        report_end=dates[-1],
        daily_state_scores=None,
        weekly_state_scores=None,
    )
    assert image.size == (1680, 1000)


def test_render_symbol_card_adds_state_tracks() -> None:
    dates = pd.bdate_range("2026-01-05", periods=15)
    daily = self._bars(dates)
    trades = pd.DataFrame(
        {
            "datetime": [dates[4], dates[12]],
            "side": ["buy", "sell"],
            "fill_price": [10.0, 11.0],
            "quantity": [100, 100],
        }
    )
    daily_states = pd.DataFrame(
        {
            "datetime": dates,
            "score_1d": np.linspace(-2.5, 2.5, len(dates)),
            "score_3d": [-2.5] * 7 + [2.5] * 8,
            "state_3d": ["趋势向下"] * 7 + ["趋势向上"] * 8,
            "state_color": ["#f3c1bc"] * 7 + ["#9fd8b5"] * 8,
        }
    )
    weekly_states = aggregate_weekly_state_tracks(daily_states)
    image = render_symbol_card(
        symbol="159915.SZ",
        name="易方达创业板ETF",
        fund_type="股票型ETF",
        daily_bars=daily,
        weekly_bars=aggregate_weekly_bars(daily),
        trades=trades,
        latest_position=None,
        report_start=dates[0],
        report_end=dates[-1],
        review_label="状态覆盖策略",
        performance={
            "total_return": 0.25,
            "max_drawdown": -0.10,
            "sharpe": 0.8,
            "annual_one_way_turnover": 3.2,
        },
        daily_state_scores=daily_states,
        weekly_state_scores=weekly_states,
    )

    assert image.size == (1680, 1120)
    assert any(
        image.getpixel((x, y)) == ImageColor.getrgb("#9fd8b5")
        for x in range(500, 1600, 20)
        for y in range(120, 400, 20)
    )
```

使用最少两种状态颜色和非恒定分数，避免测试只证明画布尺寸变化。

- [ ] **Step 2：运行 RED**

```bash
python3 -m pytest \
  stock/etf/tests/test_trade_charts.py::TradeChartTransformTests::test_render_symbol_card_adds_state_tracks \
  -q
```

Expected: `TypeError`，因为现有函数不接受状态参数。

- [ ] **Step 3：扩展公开渲染接口**

保持所有新参数为可选关键字：

```python
def render_symbol_card(
    *,
    symbol: str,
    name: str,
    fund_type: str,
    daily_bars: pd.DataFrame,
    weekly_bars: pd.DataFrame,
    trades: pd.DataFrame,
    latest_position: pd.Series | dict[str, Any] | None,
    report_start: object,
    report_end: object,
    review_label: str = "ETF Rotation Trade Review",
    performance: Mapping[str, object] | None = None,
    daily_state_scores: pd.DataFrame | None = None,
    weekly_state_scores: pd.DataFrame | None = None,
) -> Image.Image:
```

无状态时继续使用 `CARD_HEIGHT=1000`；有状态时使用
`STATE_CARD_HEIGHT=1120`。副标题使用 `review_label`，不改变已有调用的默认文字。

- [ ] **Step 4：扩展左侧指标**

将 `_draw_metadata` 增加：

```python
card_height: int
performance: Mapping[str, object] | None
```

当 `performance` 非空时，在交易统计前绘制：

```text
Performance
total return
max drawdown
Sharpe
one-way turnover
```

若 `performance["target_distribution"]` 存在，再显示 0%/50%/100% 三档的天数与占比。
EMA 图不传该键。

缺少任一键时明确抛出 `ValueError`，禁止显示 `nan`。

- [ ] **Step 5：绘制状态背景和分数轨道**

给 `_draw_chart_panel` 增加：

```python
state_scores: pd.DataFrame | None = None
draw_score_tracks: bool = False
```

绘制顺序固定为：

```text
面板底色
连续状态背景矩形
网格
K 线与成交量
score_1d/score_3d 和 -2/-1/0/1/2 参考线
买卖点
日期与图例
```

状态背景只覆盖价格区域。`draw_score_tracks=True` 时为日线预留底部轨道，纵轴固定
`[-3,3]`；周线只画背景，不画双轨。

- [ ] **Step 6：运行新旧图形测试**

```bash
python3 -m pytest stock/etf/tests/test_trade_charts.py -q
python3 -m ruff check \
  stock/etf/render_trade_charts.py \
  stock/etf/tests/test_trade_charts.py
```

Expected: 所有既有无状态图形测试和新增状态测试通过。

- [ ] **Step 7：提交**

```bash
git add \
  stock/etf/render_trade_charts.py \
  stock/etf/tests/test_trade_charts.py
git commit -m "feat: render optional ETF regime layers"
```

## Task 3：两张图、索引与严格审计

**Files:**

- Modify: `stock/etf/regime_overlay_charts.py`
- Modify: `stock/etf/tests/test_regime_overlay_charts.py`

- [ ] **Step 1：写双图编排失败测试**

使用小型 `RegimeOverlayResult` 和独立 `TrendAllocationResult` 夹具：

```python
summary = render_regime_comparison_charts(
    symbol="159915.SZ",
    name="易方达创业板ETF",
    overlay_result=overlay_result,
    ema_result=ema_result,
    ema_metrics=ema_metrics,
    output_dir=tmp_path / "charts",
    report_start="2026-01-05",
    report_end="2026-02-27",
)

assert summary["rendered_images"] == 2
assert sorted(path.name for path in (tmp_path / "charts").glob("*.png")) == [
    "0001_159915_SZ_易方达创业板ETF_状态覆盖.png",
    "0002_159915_SZ_易方达创业板ETF_EMA基线.png",
]
```

断言第一张图的成交数来自 `overlay_result.trades`，第二张来自 `ema_result.trades`。

- [ ] **Step 2：运行 RED**

```bash
python3 -m pytest \
  stock/etf/tests/test_regime_overlay_charts.py::test_render_comparison_writes_two_strategy_images \
  -q
```

Expected: `ImportError: cannot import name 'render_regime_comparison_charts'`。

- [ ] **Step 3：实现固定命名和末日持仓选择**

```python
def _latest_position(
    positions: pd.DataFrame,
    report_end: pd.Timestamp,
) -> pd.Series | None:
    rows = positions.loc[positions["datetime"].eq(report_end)]
    return None if rows.empty else rows.iloc[-1]
```

文件名使用 `make_chart_filename`，把策略后缀并入名称，确保跨平台安全且排序固定。

- [ ] **Step 4：实现两图渲染**

```python
target_counts = overlay_result.signals["target_weight"].value_counts()
target_distribution = {
    f"{weight:g}": {
        "days": int(target_counts.get(weight, 0)),
        "fraction": float(target_counts.get(weight, 0)) / len(overlay_result.signals),
    }
    for weight in (0.0, 0.5, 1.0)
}
overlay_performance = {
    **overlay_result.summary,
    "target_distribution": target_distribution,
}
overlay_image = render_symbol_card(
    symbol=symbol,
    name=name,
    fund_type="股票型ETF",
    daily_bars=overlay_result.signals,
    weekly_bars=aggregate_weekly_bars(overlay_result.signals),
    trades=overlay_result.trades,
    latest_position=_latest_position(overlay_result.positions, end_date),
    report_start=start_date,
    report_end=end_date,
    review_label="状态覆盖策略",
    performance=overlay_performance,
    daily_state_scores=daily_states,
    weekly_state_scores=weekly_states,
)
ema_image = render_symbol_card(
    symbol=symbol,
    name=name,
    fund_type="股票型ETF",
    daily_bars=overlay_result.signals,
    weekly_bars=aggregate_weekly_bars(overlay_result.signals),
    trades=ema_result.trades,
    latest_position=_latest_position(ema_result.positions, end_date),
    report_start=start_date,
    report_end=end_date,
    review_label="EMA 基线",
    performance=ema_metrics,
)
```

两图均使用 `overlay_result.signals` 中相同的因果 OHLCV；成交和末日持仓分别使用各自
结果，不能共享。

- [ ] **Step 5：写索引和严格 JSON**

`index.csv` 使用设计文档固定列和固定顺序。`render_summary.json` 使用：

```python
json.dumps(
    summary,
    ensure_ascii=False,
    allow_nan=False,
    indent=2,
    sort_keys=True,
)
```

保存后用 PIL `verify()` 重新打开两张图；校验 `index.csv` 的每个 `image_path` 均存在。

- [ ] **Step 6：补充拒绝覆盖和损坏输出测试**

覆盖：

```text
非空 charts 目录且 overwrite=False 时失败
非法状态分数时不写 index/render_summary
图片保存失败时调用方能收到异常
严格 JSON 不含 NaN/Infinity
```

- [ ] **Step 7：验证并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_overlay_charts.py -q
python3 -m ruff check \
  stock/etf/regime_overlay_charts.py \
  stock/etf/tests/test_regime_overlay_charts.py
git add \
  stock/etf/regime_overlay_charts.py \
  stock/etf/tests/test_regime_overlay_charts.py
git commit -m "feat: render regime and EMA comparison charts"
```

## Task 4：接入事务化真实回测

**Files:**

- Modify: `stock/etf/run_regime_overlay_backtest.py:39-49`
- Modify: `stock/etf/run_regime_overlay_backtest.py:115-135`
- Modify: `stock/etf/run_regime_overlay_backtest.py:229-413`
- Modify: `stock/etf/tests/test_run_regime_overlay_backtest.py`

- [ ] **Step 1：扩展输出失败测试**

把 `charts` 加入顶层预期，并断言：

```python
charts = output_dir / "charts"
assert {path.name for path in charts.iterdir()} == {
    "0001_159915_SZ_易方达创业板ETF_状态覆盖.png",
    "0002_159915_SZ_易方达创业板ETF_EMA基线.png",
    "index.csv",
    "render_summary.json",
}
assert len(pd.read_csv(charts / "index.csv")) == 2
```

重新读取两个 PNG，断言状态图尺寸 `(1680, 1120)`、EMA 图尺寸 `(1680, 1000)`。

- [ ] **Step 2：写图形失败回滚测试**

```python
def test_chart_failure_preserves_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "overlay"
    output_dir.mkdir()
    marker = output_dir / "previous-success.txt"
    marker.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "render_regime_comparison_charts",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("chart failed")),
    )
    with pytest.raises(RuntimeError, match="chart failed"):
        _run(output_dir, overwrite=True)
    assert marker.read_text(encoding="utf-8") == "previous"
```

- [ ] **Step 3：运行 RED**

```bash
python3 -m pytest stock/etf/tests/test_run_regime_overlay_backtest.py -q
```

Expected: 缺少 `charts` 或找不到绘图函数。

- [ ] **Step 4：在暂存目录渲染**

在所有绩效计算完成后、`_validate_staged_outputs` 之前调用：

```python
render_regime_comparison_charts(
    symbol=symbol,
    name="易方达创业板ETF",
    overlay_result=result,
    ema_result=result.ema_result,
    ema_metrics=ema_metrics,
    output_dir=staging_dir / "charts",
    report_start=start_date,
    report_end=end_date,
)
```

顶层 `OUTPUT_ENTRIES` 包含 `charts`；`summary.json.output_files` 列出四个相对图形路径。

- [ ] **Step 5：扩展暂存校验**

```python
chart_files = (
    "0001_159915_SZ_易方达创业板ETF_状态覆盖.png",
    "0002_159915_SZ_易方达创业板ETF_EMA基线.png",
    "index.csv",
    "render_summary.json",
)
```

校验文件非空、两张图可打开、索引恰好两行、审计严格 JSON 且图片数量等于 2。

- [ ] **Step 6：验证并提交**

```bash
python3 -m pytest \
  stock/etf/tests/test_regime_overlay_charts.py \
  stock/etf/tests/test_run_regime_overlay_backtest.py \
  stock/etf/tests/test_trade_charts.py \
  -q
python3 -m ruff check stock/etf
git add \
  stock/etf/run_regime_overlay_backtest.py \
  stock/etf/tests/test_run_regime_overlay_backtest.py
git commit -m "feat: publish regime charts with backtest output"
```

## Task 5：真实生成、视觉验收与结果文档

**Files:**

- Generate: `stock/etf/output/20260727_chuangyeban_regime_overlay/charts/**`
- Modify: `stock/etf/20260727_chuangyeban_regime_overlay_results.md`
- Modify only task files when fixing verified findings

- [ ] **Step 1：正式重跑**

```bash
python3 -m stock.etf.run_regime_overlay_backtest \
  --symbol 159915.SZ \
  --data-start 2016-01-01 \
  --start 2017-08-14 \
  --end 2026-07-20 \
  --download \
  --output-dir stock/etf/output/20260727_chuangyeban_regime_overlay \
  --overwrite
```

不得根据图形或结果重新调整状态阈值、EMA 参数或交易逻辑。

- [ ] **Step 2：独立核对索引**

从 CSV 复算并断言：

```text
状态覆盖图 trade_count = trades.csv 行数 = 125
EMA 图 trade_count = 独立 EMA 结果成交数
两图日期范围均为 2017-08-14 至 2026-07-20
状态图 state_background_driver = score_3d
状态轨道只含 score_1d/score_3d
```

- [ ] **Step 3：视觉检查两张原始分辨率图片**

使用 `view_image(detail="original")` 逐张确认：

```text
中文和策略名称没有截断
状态颜色图例与五档背景一致
分数线和 -2/-1/0/1/2 阈值可辨认
买卖点与 K 线没有整体错位
EMA 图无状态背景和分数轨道
左侧指标没有越界
```

若只存在拥挤而数据正确，优先减少标签密度或调整布局；不得删除成交点或状态数据。

- [ ] **Step 4：更新结果文档**

在结果文档增加“交易图”段落，列出两张相对路径和视觉口径，并注明图片来自同一轮真实
Tushare 回测。

- [ ] **Step 5：完整验证**

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
git diff --check
```

Expected: 所有命令退出码为 0。

- [ ] **Step 6：只提交任务文件**

```bash
git add \
  stock/etf/regime_overlay_charts.py \
  stock/etf/render_trade_charts.py \
  stock/etf/run_regime_overlay_backtest.py \
  stock/etf/tests/test_regime_overlay_charts.py \
  stock/etf/tests/test_trade_charts.py \
  stock/etf/tests/test_run_regime_overlay_backtest.py \
  stock/etf/20260727_chuangyeban_regime_overlay_results.md
git commit -m "feat: deliver regime and EMA backtest charts"
```

提交前确认当前分支为 `feature`，并确认未暂存：

```text
stock/etf/data.py
stock/etf/tests/test_data.py
.superpowers/
stock/etf/output/
```
