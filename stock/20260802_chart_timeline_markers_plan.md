# Stock Chart Timeline And Buy Markers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将股票 PNG 改成截至运行截止日的 symbol 级回看汇总图，每两个月显示时间竖线和日期，并用彩色竖向虚线加向上箭头标出全部买点。

**Architecture:** `render_symbol_bull_pullback_charts.py` 负责两月时间刻度、虚线和箭头绘制；`sample_pipeline.py` 仍按机会日创建目录，但每个目录只按 symbol 输出一张图，图内 rows 来自该 symbol 在本次运行中的全部机会。pipeline 将 `end` 作为统一图表截止日传入，非交易日只显示此前已有 K 线，同时在标题信息中保留请求截止日。

**Tech Stack:** Python、pandas、Pillow、pytest。

---

## Confirmed Semantics

- 每日目录仍表示“该日有哪些 symbol 出现机会”。
- 每日目录中的 PNG 不再是严格 point-in-time 快照，而是截至本次运行 `end` 的回看汇总图。
- 同一 symbol 的全部 signal rows 合并到一张 PNG，不再按 `signal_type|symbol` 拆图。
- 买点使用对应 signal_type 颜色的竖向虚线和向上箭头。
- 卖点只在独立退出研究图中保留红色向下箭头。
- 时间辅助线从首月开始每两个月一条，标签使用 `YYYY-MM`。
- `end=2026-08-01` 时，图表请求截止日显示为 `2026-08-01`，实际最后一根交易 K 线为 `2026-07-31`。

## Task 1: Add Failing Renderer Tests

**Files:**

- Modify: `stock/tests/test_render_symbol_bull_pullback_charts.py`

- [x] 测试两月刻度索引和 `YYYY-MM` 标签。
- [x] 测试竖向虚线存在实线段与间隔段。
- [x] 测试买点图包含信号色竖向虚线和向上箭头。
- [x] 测试 `render_all(..., bars_end_date=...)` 截断未来 K 线并记录请求截止日。
- [x] 运行 renderer tests，确认因行为尚未实现而失败。

## Task 2: Add Failing Daily Report Tests

**Files:**

- Modify: `stock/tests/test_sample_pipeline.py`

- [x] 将旧 point-in-time 测试改成回看汇总语义：每个日期调用只传当天 symbol 集合，但 `cache_rows_by_key[symbol]` 包含该 symbol 全部机会。
- [x] 断言所有日期使用同一个 `chart_end_date`。
- [x] 断言同日同 symbol 的多种 signal_type 只生成一张 PNG。
- [x] 运行 pipeline tests，确认旧实现无法满足新断言。

## Task 3: Implement Renderer Behavior

**Files:**

- Modify: `stock/analysis/render_symbol_bull_pullback_charts.py`

- [x] 增加两月刻度计算和竖向虚线绘制小函数。
- [x] 在价格和成交量面板绘制两月辅助线，在价格面板下方绘制日期。
- [x] 对每个 buy marker 绘制信号色竖向虚线和向上箭头。
- [x] `render_opportunity_rows_to_dir()` 改为按 symbol 分组，并从 symbol 级全量 rows cache 取图表标记。
- [x] `render_all()` 支持 `bars_end_date`，标题同时区分实际 K 线终点和请求截止日。
- [x] 运行 renderer tests，确认通过。

## Task 4: Implement Pipeline Aggregation

**Files:**

- Modify: `stock/run/sample_pipeline.py`

- [x] `write_daily_opportunity_dirs()` 构建 symbol 级全量 rows 和跨日期图片 cache。
- [x] 所有逐日专用目录函数透传 `chart_end_date`。
- [x] `run_pipeline()` 将 `end` 传给逐日报告和普通 analysis。
- [x] 保持大牛股单信号通用日报到专用日报的硬链接复用。
- [x] 运行 sample pipeline tests，确认通过。

## Task 5: Documentation And Delivery

**Files:**

- Modify: `stock/AGENTS.md`
- Modify: `stock/stock.md`
- Modify: `stock/20260801_ma5_ma10_big_bull_mode.md`

- [x] 用回看汇总图规则覆盖旧的逐日截断规则。
- [x] 记录两月刻度、虚线箭头、symbol 全买点汇总和 `chart_end_date`。
- [ ] 运行完整 `stock/tests`、Ruff 和 compileall。
- [ ] 先生成样本图并目视检查，再重建 `20260801_ma5_ma10_big_bull` 全量图表。
- [ ] 审计每个日期目录同一 symbol 只有一张图、全部图截至统一截止日。
