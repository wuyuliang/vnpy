# 回测性能优化：Codex 执行任务书（P-02 ~ P-05）

配套：`2026-09-04-multi-timeframe-trend-backtest-code-review.md` 附录（性能剖析原始数据）。
P-01（出图只保留 TRADED）已完成，本文只覆盖剩下四项。

## 零、总原则

**这四项全部是零行为变更的纯加速。** 任何一项如果改变了回测产出，就是改错了。

统一验收标准：**金标准逐字节一致**。动手前先落一份基准：

```bash
cd /Users/wuyuliang/code/vnpy
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --symbols BR0.SHFE EB0.DCE --start 2026-03-01 --end 2026-04-01 \
  --initial-equity 1e6 --chart-outcomes all \
  --output-root /tmp/perf_golden
```

每项改完后重跑同一条命令到 `/tmp/perf_after`，然后：

```bash
for f in trades.csv fills.csv orders.csv plans.csv exit_legs.csv \
         daily_equity.csv rejections.csv candidates.csv \
         performance_by_symbol.csv performance_by_group.csv; do
  diff <(cat /tmp/perf_golden/*/$f) <(cat /tmp/perf_after/*/$f) >/dev/null \
    && echo "OK   $f" || echo "DIFF $f"
done
```

**全部 OK 才算通过。** 出现 DIFF 就回退重做，不要"看着差不多"。
（注意用 `--chart-outcomes all`：金标准要覆盖出图路径。）

同时每项都要记录加速比，用同一条命令计时：

```bash
time python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --symbols BR0.SHFE --start 2026-03-01 --end 2026-04-01 \
  --initial-equity 1e6 --output-root /tmp/perf_timing
```

基线参考（本机 Linux VM，单品种单月）：`--chart-outcomes all` 41s，`traded` 23s。

其它约定：

- 每项**独立提交**，提交信息写明 `P-0X`。
- 只改 `cta/**`（见 `cta/AGENTS.md`）。
- `cta/strategy/tests/` 现有 **478** 个测试必须保持全绿。
- 已知历史遗留失败：`test_gateway_exception_keeps_pos_zero`（缺 vnpy 环境），不要动。

---

## P-04 聚合结果落盘缓存（先做，收益最大、风险最低）

### 为什么先做

不碰任何算法，纯粹避免重复计算。调参重跑时 5 分钟/日线聚合是完全确定性的重复劳动，
做完之后"改个参数再跑一遍"从小时级降到分钟级——**迭代效率上收益最大的一项**。

### 做什么

给 `aggregate_completed_bars` / `aggregate_completed_daily_bars` 的结果加一层磁盘缓存。

缓存键必须覆盖**所有影响输出的输入**，任何一项变了都要 miss：

- 输入分钟数据的内容哈希（对 `contract_code, bar_end, open, high, low, close, volume,
  open_interest, exchange_trade_date` 这几列的规范化字节做 sha256，**不要**用文件 mtime）
- `minutes`（或 daily 标识）
- 会话模板：`sessions` 的完整结构（session_id / is_night / 每个 segment 的
  segment_id、start、end、bucket_anchor），序列化后进哈希
- 聚合实现的版本号常量 `AGGREGATION_CACHE_VERSION`，**改算法时必须手动 +1**

落盘位置 `cta/data/origin/aggregated_cache/<key前2位>/<key>.parquet`，
分两级目录避免单目录文件过多。

### 硬性要求

- **写盘用临时文件 + `os.replace`**，多进程并发安全（参考已有的
  `cta/strategy/brooks/cycle_v1/backtest/vendor_metadata_cache.py`）。
- 缓存目录不可写、读取失败、parquet 损坏 → **静默降级为直接计算**，绝不能让回测崩。
  但要计数（见下条）。
- **加计数并汇总进 `summary.json`**：`{"aggregation_cache": {"hits": n, "misses": n, "errors": n}}`。
  这是评审 R-09 的要求：静默降级必须可观测，否则缓存整个失效也没人知道。
- 新增 CLI `--aggregation-cache-root`，传空字符串关闭缓存。默认开启。

### 测试

- 同一份输入连续调用两次，第二次命中缓存，且**两次结果 DataFrame 完全相等**。
- 改 `minutes`、改 sessions 任一字段、改输入数据任一格 → 都必须 miss。
- 缓存目录设成一个**文件**（不可写）→ 仍返回正确结果，`errors` 计数递增。
- 手动损坏一个缓存 parquet → 回退重算，结果正确。
- `AGGREGATION_CACHE_VERSION` +1 后旧缓存不再命中。

---

## P-02 向量化 `aggregate_completed_bars`（最大单点瓶颈）

### 证据

`cta/strategy/brooks/cycle_v1/instruments/sessions.py:89`：

```python
assignments = [_assign_segment(value, sessions, minutes) for value in ends]   # ← 逐根 bar
...
for _, group in bars.groupby(keys, sort=True, dropna=False):
    ...
    "open":  float(group.iloc[0]["open"]),      # ← 逐组标量取值
    "close": float(group.iloc[-1]["close"]),
```

剖析（单品种单月）：`_assign_segment` 调用 91,125 次；
`DataFrame._ixs` 351,296 次 / cum 14.9s；`managers.fast_xs` 157,735 次；
`datetimes._box_func` 703,792 次（每个 datetime64 都被装箱成 Timestamp）。
`load_normalized_symbol` + `_prepare_strategy_data` 合计占总耗时 **68%**。

### 怎么改

**第一步：向量化段号归属。** 段边界只有寥寥几个，且只取决于 time-of-day。

1. 把 `ends` 转成 `minute_of_day = ends.dt.hour * 60 + ends.dt.minute`（整数数组，不装箱）。
2. 对每个 (session, segment) 预先算出 `[start_mod, end_mod]`，用布尔掩码
   或 `np.searchsorted` 一次性给全部 bar 打上段号；跨零点的段拆成两个区间处理。
3. 锚定日：`anchor_date = ends.dt.date`，跨零点且 `current <= segment.end` 的
   整体减一天（向量化 `np.where`）。
4. `bucket_end` 用整数分钟算：
   `bucket_number = ceil((minute_of_day - anchor_mod) / minutes)`，
   再由 anchor + `bucket_number * minutes` 反推，全程 int64，最后一次性转回 datetime。

**第二步：向量化分组聚合。**

```python
grouped = bars.groupby(keys, sort=True, dropna=False)
agg = grouped.agg(
    open=("open", "first"), high=("high", "max"), low=("low", "min"),
    close=("close", "last"), volume=("volume", "sum"),
    open_interest=("open_interest", "last"),
    bar_count=("bar_end", "size"),
    span_start=("bar_end", "min"), span_end=("bar_end", "max"),
)
```

**第三步：完整性判定的等价改写。** 现实现是
`len(group) == minutes and actual_ends.equals(expected_ends)`。
上游已经保证 `(contract_code, bar_end)` 唯一、且都是 1 分钟 bar，所以

```
bar_count == minutes  且  (span_end - span_start) == (minutes - 1) 分钟
```

与逐根比对**等价**（计数相同 + 首尾跨度相同 ⇒ 连续无缺口）。
请在代码注释里写明这个等价性依赖"上游唯一性校验"这个前提，
并保留那条唯一性校验，不要顺手删掉。

### 注意

- `cta/strategy/brooks/scalp/data.py:322` 有**第二个** `aggregate_completed_bars`
  （剖析里 cum 10.5s），`load_normalized_symbol` 走的是它，
  第 349-354 行同样是 `group.iloc[0/-1]`。两个都要改。
- 同文件 `scalp/data.py:254` 的 `for position, (_, raw) in enumerate(source.iterrows())`
  （`_normalized_frames`，剖析 cum 6.3s）也是同类问题，一并向量化。
- **两个聚合器本身就是重复实现**（评审 R-06 的同类问题）。
  如果两者语义确实一致，优先合并成一个；确有差异就在各自 docstring 里写清差在哪，
  不要默默留两份。

### 测试

- 用现有真实数据，对比新旧实现的输出 DataFrame **完全相等**（至少覆盖 5min / 60min / daily）。
- 边界：不完整的桶（缺 bar）被丢弃；跨零点夜盘段；单根 bar 的桶；空输入。
- 金标准 diff 全 OK。

---

## P-03 消除重复聚合

### 证据

`_prepare_strategy_data` 里做了 4 次聚合（`daily_signal`、`five_signal`、
`daily_actual`、`five_actual`），runner 循环里为出图再做第 5 次（`hourly`），
而 `load_normalized_symbol` 内部已经聚合过一轮。

### 做什么

1. **`hourly` 只有出图用。** `grep -rn hourly cta/strategy/multi_timeframe_trend_backtest/`
   除 `charts.py` 外无消费方。
   - `--chart-outcomes none` → 完全跳过 hourly 聚合。
   - `--chart-outcomes traded` → 只为**真正会出图的品种**聚合。
     成交品种在回放结束后才知道，所以把 hourly 改成**惰性计算**：
     把每个品种的 `symbol_minute` 和 sessions 传进出图层，
     在 `_contexts_for(symbol)` 里按需聚合（那里已经有 per-symbol 缓存）。
2. **signal / actual 两套复用段号。** 两者差别只在用 `signal_*` 列还是原始 OHLC，
   段号归属与桶边界完全相同。P-02 做完后段号是一个独立的向量化步骤，
   算一次给两套用。

### 测试

- `--chart-outcomes all` 时出图内容与金标准一致（hourly 面板不能因为改惰性而画错）。
- `--chart-outcomes none` 时不产生任何 hourly 聚合调用（用 monkeypatch 计数断言）。
- 金标准 diff 全 OK。

---

## P-05 事件循环微优化（最后做）

`replay_trend_portfolio` 目前只占 **4%**，现在动它性价比很低。
但 P-02/P-03/P-04 做完后它会变成新的大头，那时再做：

- `event_rows.iterrows()` → `zip(*[df[c].to_numpy() for c in cols])`，避免每行建 Series。
- `_portfolio_marked_equity` 每根 bar 全量重算 → 改成增量维护
  （持仓变化或 mark 变化时更新）。**注意浮点累加漂移**：
  增量维护必须定期（例如每个交易日收盘）与全量重算对账，偏差超阈值就报错，
  否则会悄悄改变回测结果。
- `_is_overnight_reduction_time` 每根 bar 重新 `pd.Timestamp.combine` + `tz_localize`，
  结果只取决于 (sessions, date, time-of-day) → 按 (sessions_id, date) 缓存。

**这一项最容易改出行为差异**（尤其增量权益），金标准 diff 必须过。

---

## 执行顺序与提交

```
P-04 缓存        → 提交 → 计时 + 金标准 diff
P-02 向量化聚合  → 提交 → 计时 + 金标准 diff（缓存版本号 +1）
P-03 去重复聚合  → 提交 → 计时 + 金标准 diff
P-05 事件循环    → 提交 → 计时 + 金标准 diff
```

P-02 改了聚合算法，**必须把 `AGGREGATION_CACHE_VERSION` +1**，
否则会命中 P-04 阶段用旧算法写下的缓存，结果对不上还很难查。

每项提交请在描述里附上：加速比（同一条计时命令的前后耗时）+ 金标准 diff 全 OK 的确认。
