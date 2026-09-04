# multi_timeframe_trend_backtest 代码评审（穿越与复用）

评审范围：

```
cta/strategy/multi_timeframe_trend_backtest/engine.py     3498 行
cta/strategy/multi_timeframe_trend_backtest/runner.py     1375 行
cta/strategy/multi_timeframe_trend_rules.py                576 行
cta/strategy/multi_timeframe_trend_strategy.py             545 行
cta/strategy/multi_timeframe_trend_management.py           121 行
cta/config/multi_timeframe_trend_config.py                 402 行
cta/data_code/build_symbol_turnover.py
cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_cache.py
```

重点：**未来函数（穿越）** 与 **代码简洁/复用**。

本文按"可直接交给 Codex 执行"的粒度写：每条给出问题、证据、修法、验收方式。
**每条都要配单元测试**，否则同类问题会再次静默复发（见 R-01 与 R-09）。

---

## 结论摘要

| # | 类别 | 严重度 | 问题 | 状态 |
|---|---|---|---|---|
| R-01 | 功能失效 | 🔴 高 | 高跳空分级永久返回空，`pre_break_high_gap_*` 是死代码 | 实测确认 |
| R-02 | 穿越 | 🟠 中 | 成交额表按自然日归集，夜盘错配，队列用到当日自身夜盘成交额 | 实测确认 |
| R-03 | 穿越 | 🟠 中 | `--include-top-turnover` 用回测区间末端数据选池 | 设计确认 |
| R-04 | 正确性 | 🟡 低 | 虚拟仓不感知换月，影子 R 在换月处失真 | 代码确认 |
| R-05 | 正确性 | 🟡 低 | `load_contract_sizes` 不区分 as-of，乘数变更会被回溯套用 | 代码确认 |
| R-06 | 复用 | 🟠 中 | 两个 replay 函数 59% 逐行重复（243 行） | 实测确认 |
| R-07 | 一致性 | 🟠 中 | 单品种 replay 缺全部组合级闸门，两条路径行为分叉 | 实测确认 |
| R-08 | 简洁 | 🟡 低 | `_minute_of_day` / `minutes_of_day` 同名不同义 | 代码确认 |
| R-09 | 可观测 | 🟠 中 | 大量静默 fail-open 无计数，功能死掉无人知 | 代码确认 |
| R-10 | 结构 | 🟡 低 | engine.py 3498 行 / 94 个顶层定义，职责过载 | 代码确认 |

**没有发现的**（已逐一核查，结论是干净的）：

- `_align_completed_daily` 用 `merge_asof(direction="backward")`，日内 bar 只能拿到
  已收盘日线，日内不会提前拿到当日日线特征。
- `build_intraday_context` 的 `volume_threshold` 用 `shift(1).rolling(...)`，不含当根。
- `attach_session_range` 的 `cummax/cummin` 依赖帧按 `bar_end` 排序，
  而 `_with_bar_end` 与 `_sort_aggregated_bars` 都做了排序，因果成立。
- `turnover_eligible_by_date` 的 `rolling().shift(1)` 本身不含当日（穿越来自 R-02 的日期语义）。
- 影子单在候选循环里创建、下一根 bar 才撮合，与真实挂单同节奏，无同根偷跑。

---

## R-01 🔴 高跳空分级永久失效（`pre_break_high_gap_*` 是死代码）

**问题**

`replay_trend_portfolio` 用 `_high_gap_flags_by_trade_date(item.daily_context, config)`
做高跳空品种分级，而 `runner.py` 传入的 `daily_context=symbol_daily` 是
`_prepare_strategy_data` 返回的 **`daily_actual`**——**聚合出来的原始日线，没有 `daily_atr14` 列**。

`_high_gap_flags_by_trade_date` 开头：

```python
required = {"open", "close", "daily_atr14"}
if not required.issubset(frame.columns):
    return {}          # ← 永远走这里
```

于是 `high_gap_by_root` 恒为 `{}`，`pre_break_high_gap_min_unrealized_r`（0.5）
和 `pre_break_gap_risk_scale`（0.5）**从未生效过**，跨休市保护对所有品种一律用基础阈值。

**证据**

```python
# runner.py:_prepare_strategy_data 返回的 daily_actual 列
['bar_end','close','exchange_trade_date','high','low','open','volume']
>>> _high_gap_flags_by_trade_date(daily_actual, MultiTimeframeTrendConfig())
{}
```

且实跑的 `rejections.csv` 里 `PRE_BREAK_GAP_RISK_REDUCTION` 一次都没出现过。

**修法**

日线上下文（含 EMA/ATR）目前只在 `generate_multi_timeframe_candidates` 内部由
`build_daily_context(daily_signal)` 构造，runner 手上没有。二选一：

- **A（推荐）**：`_prepare_strategy_data` 把 `build_daily_context(daily_signal, config)`
  的结果一并返回，runner 用它作为 `PortfolioReplayInput.daily_context`。
  注意必须用 **`daily_signal`**（复权信号价）而不是 `daily_actual`，与候选生成同源。
- **B**：`_high_gap_flags_by_trade_date` 自己用 `cta.strategy.multi_timeframe_trend_rules.atr`
  从 OHLC 现算 ATR，不依赖调用方补列。

无论哪种，**缺列不能再静默返回 `{}`**：见 R-09，要么抛错，要么记一条审计事件。

**验收**

- 新增单测：传入含 `daily_atr14` 的日线上下文时，
  高跳空品种被标记；不含该列时**抛出明确异常**（而不是返回空）。
- 回归：40 品种回测的 `rejections.csv` 里应能看到
  `PRE_BREAK_GAP_RISK_REDUCTION`，或明确解释为何该区间无一触发。

---

## R-02 🟠 成交额表按自然日归集，队列用到当日自身夜盘

**问题**

`build_symbol_turnover.py` 用 **parquet 文件名（自然日）** 作为 `trade_date`：

```python
day = datetime.strptime(path.stem, "%Y-%m-%d").date()
```

但自然日 D 的文件里装的是：**交易日 D 的日盘** + **交易日 D+1 的夜盘**。
而引擎按 `bar["exchange_trade_date"]` 查队列：

```python
turnover_detail = _turnover_share_blocked(root_symbol, bar["exchange_trade_date"], turnover_eligible)
```

`turnover_eligible_by_date` 对交易日 D+1 的队列取 `rolling(5).sum().shift(1)`，
用到自然日 ≤ D 的行——**其中自然日 D 的行包含交易日 D+1 的夜盘成交额**。
也就是说，交易日 D+1 夜盘 21:30 的一笔候选，其准入队列用到了同一个夜盘直到 23:00 的成交额。**这是穿越。**

**证据**

```
RB 2026-06-30 文件: 共 346 根, 夜盘 121 根 (35%), 夜盘成交额占该文件 26.7%
```

按 5 日窗口算，污染量级约 27% / 5 ≈ 5%。不致命，但方向是系统性的。

**修法**

按**交易日**而不是自然日归集。分钟原始 parquet 只有 `datetime`，交易日映射在
`cta.strategy.brooks.cycle_v1.instruments.sessions` 里。两条路：

- **A（推荐）**：`build_symbol_turnover` 复用 `aggregate_completed_daily_bars`
  或其交易日映射逻辑，直接产出 `exchange_trade_date` 维度的成交额。
- **B（下策）**：保持自然日归集，但把 `shift(1)` 改成 `shift(2)`，牺牲一天新鲜度换取无穿越。
  只有在 A 明显不可行时才用，且必须在表里加 `date_semantics` 列注明口径。

**验收**

- 单测：构造一个跨夜盘的合成分钟集，断言夜盘成交额落到**次一交易日**。
- 单测：断言交易日 D 的队列不包含任何 `exchange_trade_date >= D` 的成交额。
- 表结构加一列 `date_semantics`，取值 `exchange_trade_date`，让口径显式化。

---

## R-03 🟠 `--include-top-turnover` 用区间末端数据选池

**问题**

`runner.py:_extend_symbols_with_top_turnover` 取的是 `end` 之前最后 20 个交易日：

```python
frame = frame[frame["trade_date"].dt.date <= end]
recent = sorted(frame["trade_date"].unique())[-20:]
```

`end` 是**回测结束日**。也就是用 2026-07 的成交额，决定 2026-01 要交易哪些品种。
"哪些品种在这半年里流动性最好"本身就是事后信息，进而带来幸存者偏差。

现有单测 `test_top_turnover_never_looks_past_the_backtest_end` 只保证不看 `end` **之后**，
没有保证不看 `start` 之后。

**修法**

改成以 **`start`** 为基准取最近 20 个交易日（即回测开始前的流动性），并把参数语义写进 help。
若要保留"整段窗口"口径，必须新增显式开关（如 `--turnover-universe-asof end`）
并在 `report.md` 里标注该次运行的选池含前视，否则绩效不可用于样本外判断。

**验收**

- 单测：`start=2026-01-01, end=2026-07-01` 时，只有 2026-01-01 之前的成交额参与选池；
  某品种仅在 2026-06 放量不得被补入。
- `summary.json` 记录 `turnover_universe_asof` 与实际补入的品种列表。

---

## R-04 🟡 虚拟仓不感知换月

**问题**

`_advance_virtual_positions(virtual_positions, bars_at_event, ...)` 用的是
**`bars_at_event`（原始当日主力 bar）**，而真实持仓走的是 roll 感知的 `position_bars`
（`roll_bar_lookups` 会把持仓映射回原合约）。换月当天，影子仓的价格序列会直接跳到新合约，
`realized_r` 出现与行情无关的跳变，进而污染追高门槛。

**修法**

`_advance_virtual_positions` 改为接收与真实持仓同一套 `position_bars`；
影子仓也记录自己的 `contract_code`，换月时按真实仓的处理方式
（`ROLL_MAPPING_CHANGED`）**作废该影子单**并且**不计入** `chase_state`——
换月不是策略信号，不应该影响门槛。

**验收**

- 单测：影子持仓期间发生换月，断言该影子单被作废且 `chase_state.outcomes` 未增加。

---

## R-05 🟡 `load_contract_sizes` 不区分 as-of

**问题**

```python
if best is None or frame["root_symbol"].nunique() > best["root_symbol"].nunique():
    best = frame
```

在 `meta_cache/*/contract_specs.csv` 里**按 root 数量最多**挑一个 bundle，
完全不看该 bundle 的 as-of 区间。若某品种合约乘数历史上变更过，
会把某个时点的乘数回溯套用到全历史，成交额换算随之失真。

**修法**

按 `known_at` / `effective_from` 取**不晚于目标交易日**的最新一条乘数，
逐日解析而不是全局取一个值。取不到的品种保持现在的行为——排除并列出。

**验收**

- 单测：同一 root 有两条不同 `effective_from` 的乘数时，
  早期日期用旧乘数、晚期日期用新乘数。

---

## R-06 🟠 两个 replay 函数 59% 逐行重复

**问题**

```
replay_trend_strategy   有效行 412   (engine.py 811-1233)
replay_trend_portfolio  有效行 946   (engine.py 1234-2195)
逐行完全相同 243 行 → 占 strategy 的 59%
```

重复的部分包括：候选过滤链、挂单过期/撤单、`_match_entry` 撮合、
仓位缩放快照、`_advance_position_stop`、`_protective_exit` 出场、
`_close_position` 与成交行构造。

这就是为什么本轮"撮合时复检时段窗口"必须在两处各写一遍
（`_entry_blocked_at_match` 被调用 2 次，两段几乎一样的 `if blocked:` 分支），
也是 R-07 的根因。

**修法**

抽出共用组件，两个 replay 只保留各自真正不同的部分
（组合层的资金/保证金/槽位 vs 单品种的独立账户）：

- `_process_pending_order(pending, bar, timestamp, config, ...) -> OrderOutcome`
  —— 过期、激活、撮合前复检、撮合，返回结构化结果。
- `_apply_candidate_filters(candidate, ctx) -> str`
  —— 静态 `filtered_reason` + 首笔缓冲 + 冷却 + 时段窗口，返回拒绝码。
  组合层再追加自己的闸门（熔断/板块/成交额）。
- `_manage_open_position(position, bar, context_row, config) -> ExitDecision`
  —— 移动止损推进与保护性出场判定。

**不要**为了消重把组合层的资金逻辑硬塞进单品种路径；目标是共用"每根 bar 对一个 root 做什么"，
账户层保持分离。

**验收**

- 重构前后，同一组输入的 `trades.csv` **逐字节一致**（先落一个金标准产出再动手）。
- 现有 468 个测试全绿。

---

## R-07 🟠 单品种 replay 缺全部组合级闸门

**问题**

实测统计（`engine.py` 811-1233 行内出现次数）：

| 闸门 | 全文件 | 单品种 replay 内 |
|---|---:|---:|
| `DAILY_LOSS_CIRCUIT_BREAKER` | 1 | **0** |
| `SECTOR_CONCENTRATION_LIMIT` | 1 | **0** |
| `TURNOVER_SHARE_NOT_ELIGIBLE` | 1 | **0** |
| `PRE_BREAK_NO_BUFFER` | 1 | **0** |
| `CHASE_HIGH_VIRTUAL_CLOSED` | 1 | **0** |
| `_advance_virtual_positions` | 2 | **0** |

熔断和板块限额是组合级概念，单品种缺席合理；但
**跨休市保护（`PRE_BREAK_NO_BUFFER`）和追高影子单是单品种同样成立的规则**，
缺席会让两条路径对同一份数据给出不同结论，单品种回测不能再用来判断这些规则。

**修法**

R-06 抽出共用组件后，把跨休市保护与追高影子单下沉到共用层，两个 replay 都接。
组合专属的（熔断、板块、成交额、保证金）留在组合层。
在模块 docstring 里明确写下"哪些规则是共用的、哪些是组合专属的"。

**验收**

- 单测：单品种 replay 在浮亏跨休市时产出 `PRE_BREAK_NO_BUFFER`。
- 单测：单品种 replay 会跟踪追高影子单。

---

## R-08 🟡 `_minute_of_day` / `minutes_of_day` 同名不同义

`engine.py:_minute_of_day(value)` 收 time 对象，
`config.py:minutes_of_day(value)` 收 `"HH:MM"` 字符串。名字几乎一样、入参完全不同，
调用点很容易传错且不会报错（都返回 int）。

**修法**：合并成一个放 config，重载或改名为
`minute_of_day_from_time()` / `minute_of_day_from_text()`，engine 直接复用。

---

## R-09 🟠 静默 fail-open 没有计数

`engine.py` 里有 20 处以上 `return ""` / `return {}` 的静默放行分支。
设计意图是对的——**闸门宁可放行也不能因为缺数据把回测搞崩**——
但当前实现下，"闸门正常放行"和"闸门因为缺列彻底死了"在产出里**完全无法区分**。
R-01 正是因此隐藏至今。

**修法**

给每个 fail-open 分支加计数，汇总进 `summary.json`：

```json
"gate_fail_open": {
  "high_gap_classification": 12000,
  "entry_range_position_no_window": 340,
  "turnover_cohort_missing_date": 0
}
```

并在 `report.md` 里对**放行率异常高**的闸门（例如某闸门 100% 走 fail-open）打印警告。
判据可以简单粗暴：某闸门 fail-open 占比 > 95% 就提示"疑似未生效"。

**验收**

- 单测：缺列时对应计数器递增。
- 单测：某闸门 100% fail-open 时，报告出现警告行。

---

## R-10 🟡 engine.py 职责过载

3498 行、94 个顶层定义，同时承担：配置闸门、虚拟仓、组合记账、保证金、
换月、手续费/元数据快照、审计行构造。

**建议拆分**（在 R-06 之后做，否则冲突面太大）：

```
engine/gates.py         入场闸门与拒绝码（时段/熔断/板块/成交额/区间/量比/止损距离）
engine/virtual.py       影子单与追高门槛
engine/positions.py     持仓、移动止损、出场、成交行
engine/portfolio.py     资金、保证金、槽位、逐日权益
engine/replay.py        事件循环编排
```

拆分必须**纯移动、零行为变更**，同样用"`trades.csv` 逐字节一致"验收。

---

## 建议执行顺序

1. **R-01**（死代码，影响真实风控）→ **R-02**、**R-03**（穿越）
2. **R-09**（可观测性，先做能防止 R-01 类问题复发）
3. **R-06** → **R-07**（消重后再补齐闸门，顺序反了会重复劳动）
4. **R-04**、**R-05**、**R-08**
5. **R-10**（最后做，纯搬运）

R-01/R-02/R-03 会改变回测结果，**每条单独提交、单独重跑对比**，
不要和 R-06/R-10 这类零行为变更的重构混在一个提交里。

---

## 给 Codex 的执行约定

- 每条改动**独立提交**，提交信息写明 `R-0X`。
- 会改变回测结果的（R-01/R-02/R-03/R-04/R-05）与纯重构（R-06/R-07/R-08/R-09/R-10）
  **不得混在同一提交**。
- 纯重构提交必须证明 `trades.csv` 逐字节一致：动手前先跑一次金标准并存档
  （建议 `--symbols BR0.SHFE EB0.DCE --start 2026-03-01 --end 2026-04-01`，约 2 分钟）。
- 每条都要补单测；`cta/strategy/tests/` 现有 468 个测试必须保持全绿。
- 遵守 `cta/AGENTS.md`：只改 `cta/**`，不动 vn.py 主框架，
  不覆盖 `cta/data/` 下的原始数据。
- 已知的历史遗留失败（与本次无关，不要顺手"修"掉）：
  `test_gateway_exception_keeps_pos_zero` 需要 vnpy 环境。

---

# 附：回测性能剖析（2026-09-04）

`cProfile` 实测，单品种（BR0）单月，总耗时 59.9s：

| 阶段 | cumtime | 占比 |
|---|---:|---:|
| `load_normalized_symbol`（加载+归一化+聚合） | 22.9s | 38% |
| `_prepare_strategy_data`（再聚合日线/5分钟） | 17.7s | 30% |
| `render_opportunity_charts` | 13.8s | 23% |
| **`replay_trend_portfolio`（事件循环）** | **2.6s** | **4%** |

**事件循环不是瓶颈，只占 4%。** 瓶颈是 K 线聚合和出图。

热点函数（tottime）：

```
265,581 次 pandas indexing.__getitem__   cum 17.5s   ← 行式 .iloc
351,296 次 DataFrame._ixs                cum 14.9s
157,735 次 managers.fast_xs              tot  1.63s
 91,125 次 sessions.py:241 _assign_segment tot 1.30s   ← 每根 bar 调一次
    183 次 PIL ImagingEncoder.encode     tot  4.54s
  5,660 次 Font.render                   tot  2.24s
```

## P-01 ✅ 已修：出图只保留 TRADED

`r4_turnover` 产出 **5,660 张 PNG / 272 MB**，而成交只有 98 笔。

新增 `--chart-outcomes {traded,all,none}`，默认 `traded`。
`index.csv` 与 `OPPORTUNITY_CHARTS.md` **仍然列出全部候选**及其 `outcome_code`，
未出图的行 `chart_path` 为空、表格显示 `—`，**漏斗审计不受影响**。

同时把出图循环里的 `_symbol_bars` + `_chart_frame` 提到循环外按品种缓存——
原本每个候选都要在包含全部品种的 K 线表上过滤一遍并排序，
40 品种 1.5 万候选就是 4.5 万次全表过滤。

实测（BR0 单月，不带 profiler）：

| 模式 | 耗时 | PNG |
|---|---:|---:|
| `all`（旧行为） | 41s | 183 |
| **`traded`（新默认）** | **23s** | 6 |

单品种就快 44%。按剖析里 ~75ms/张估算，40 品种那一跑
1.5 万张 ≈ **19 分钟**纯出图，降到 98 张后 ≈ 7 秒。

## P-02 🔴 `aggregate_completed_bars` 逐行迭代（最大剩余瓶颈）

`sessions.py:_assign_segment` 每根 bar 调用一次（单品种单月就 9.1 万次），
配合 `fast_xs` / `_ixs` 的行式取值，构成 `load_normalized_symbol` 与
`_prepare_strategy_data` 合计 **68%** 的耗时。

**修法**：把交易时段归属向量化。时段边界是固定的少数几个区间，
用 `np.searchsorted` 对 `bar_end` 的 time-of-day 一次性定位段号，
再用 `groupby(段号).agg()` 出 OHLCV，取代逐行 `_assign_segment` + 逐行拼装。

**验收**：聚合结果与现实现逐字节一致（同一份分钟数据，比较 5 分钟/日线输出的 `to_csv`）。

## P-03 🟠 同一份数据被聚合四到五遍

`_prepare_strategy_data` 里做了 4 次聚合（`daily_signal`、`five_signal`、
`daily_actual`、`five_actual`），runner 循环里还为出图做第 5 次（`hourly`），
而 `load_normalized_symbol` 内部已经聚合过一轮。

**修法**：

- `hourly` 只有出图用（`grep hourly` 除 charts 外无引用）。
  `--chart-outcomes none` 时直接跳过；`traded` 时只为真正出图的品种算。
- signal/actual 两套差别只在用 `signal_*` 还是原始 OHLC 列，
  段号归属完全相同——段号算一次，两套复用。

## P-04 🟠 聚合结果落盘缓存

5 分钟/日线聚合对 (品种, 日期区间, 时段模板, 数据内容哈希) 是确定性的。
调参重跑时这部分完全是重复计算。

**修法**：按内容哈希缓存到 `cta/data/origin/aggregated_cache/`，
命中直接读 parquet。与已有的 vendor 元数据缓存同一个思路。
预计让"改个参数再跑一遍"从小时级降到分钟级——**这是迭代效率上收益最大的一项**。

## P-05 🟡 事件循环的小优化（收益有限，最后再做）

只占 4%，但如果 P-02/P-03/P-04 都做完了，它会变成新的大头：

- `event_rows.iterrows()` 换成 `zip(*[df[c].to_numpy() for c in cols])`
- `_portfolio_marked_equity` 每根 bar 全量重算，可增量维护
- `_is_overnight_reduction_time` 每根 bar 重新构造 `pd.Timestamp.combine`，
  结果只取决于 time-of-day，可按 (sessions, date) 缓存

## 建议顺序

1. **P-01 已完成**
2. **P-04**（缓存，收益最大且风险最低，纯加速不改语义）
3. **P-02**（向量化聚合，需要逐字节比对验收）
4. **P-03**（去重复聚合）
5. **P-05**（最后）

P-02 到 P-05 全部属于**零行为变更**，验收标准统一为
`trades.csv` 与聚合输出逐字节一致。
