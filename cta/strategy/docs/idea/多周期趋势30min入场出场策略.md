# 多周期趋势策略 · 30 分钟入场出场版

> 把 `multi_timeframe_trend` 的入场/出场逻辑整体搬到 30 分钟，日线方向层不动。
> 本文只讲**入场**与**出场**，组合风控、选池、数据与元数据全部沿用现有实现。
>
> 参考实现：`cta/strategy/multi_timeframe_trend_strategy.py`、
> `multi_timeframe_trend_rules.py`、`multi_timeframe_trend_backtest/engine.py`

---

## 0. 先看三个实测量（决定了整份文档的取舍）

20 个品种 / 2026H1 / 真实分钟数据，把 1 分钟聚合到 5 分钟和 30 分钟后对比：

| | 5 分钟 | 30 分钟 | 比值 |
| --- | ---: | ---: | ---: |
| 每日 K 线数（中位） | 69.2 | **12.8** | **0.185** |
| ATR（中位，价格点） | 10.0 | **22.0** | **2.35×** |
| (high − close) / ATR | 0.375 | 0.353 | ≈ 1 |
| (high − low) / ATR | 0.859 | 0.849 | ≈ 1 |

三条结论，全文围绕它们展开：

1. **信号机会少 5.4 倍。**现版本 2026H1 / 40 品种成交 57 笔，
   直接搬到 30 分钟大约只剩 **10 笔**。这是本方案最大的风险，
   §6 专门处理。
2. **1R 的价格宽度变成约 2.35 倍**（≈ √6，符合波动随时间开方缩放）。
   于是手数约为原来的 1/2.35，而**成本占 R 的比例下降到约 1/2.35**。
   这是 30 分钟版唯一确定的好处。
3. **K 线形状是尺度无关的**：突破根的 `high−close` 和 `high−low` 相对 ATR
   在两个周期上几乎一样。所以"以 `high + 1 tick` 挂单"这套入场机制
   **不需要重新设计**，只是绝对价差变宽了。

---

## 1. 不动的部分

| 层 | 内容 | 为什么不动 |
| --- | --- | --- |
| 日线方向 | `daily_ema 5/10/20` 排列 + `always_in` 判定 + `daily_atr14` | 它本来就是日线的，与入场周期无关 |
| 障碍过滤 | `assess_obstacle`（近 20 日枢轴 + `obstacle_buffer_atr`） | 同上，日线尺度 |
| 组合风控 | 日内熔断、板块集中度、并发上限、保证金上限、回撤分档 | 全部是墙钟/组合口径 |
| 成交模型 | 1 分钟撮合、涨跌停、跳空、换月 | 执行层永远 1 分钟 |
| 跨休市保护 | `pre_break_*`、隔夜减仓 | 墙钟口径，但**重要性上升**，见 §5.5 |

**执行时间轴仍然是 1 分钟。**30 分钟只用于产生信号、定触发价与结构止损。

---

## 2. 入场

### 2.1 回调状态机（照搬 `advance_pullback_state`，只改窗口）

逻辑一字不改：

```
方向确立（日线多头）后，在 30 分钟图上：
  ① 出现 close < 前一根 close  → 回调开始，记录 range_high / range_low
  ② 后续每根更新 range_high = max(...)、range_low = min(...)
  ③ 某根 close > range_high  → 突破
     且 pullback_min_bars <= 已持续根数 <= pullback_max_bars
     且 volume_expanded 为真
     → 生成候选
  ④ 根数超过 pullback_max_bars 或提前突破 → 状态清零
```

触发价与结构止损同样照搬：

```
trigger = round_up(high(突破根) + entry_buffer_ticks * tick, tick)
stop    = round_down(range_low - tick, tick)
```

### 2.2 窗口参数怎么换算（本方案的核心决策）

现版本的 bar 计数放到 30 分钟上，墙钟含义会放大 6 倍。逐个处理：

| 参数 | 5min 默认 | 墙钟含义 | 30min 建议 | 理由 |
| --- | ---: | --- | ---: | --- |
| `pullback_min_bars` | 3 | 15 分钟 | **2** | 30 分钟上至少要 2 根才形成区间；取 1 等于没有回调 |
| `pullback_max_bars` | 12 | 60 分钟 | **4** | 4 根 = 2 小时。**不要取 12**，那是 6 小时，会跨越午休和夜盘边界 |
| `volume_lookback` | 20 | 100 分钟 | **20** | 保持根数：20 根 ≈ 1.5 个交易日，是合理的量能基准 |
| `volume_quantile` | 0.80 | — | 0.80 | 尺度无关 |
| `atr_period` | 14 | 70 分钟 | **14** | 保持根数：14 根 ≈ 1 个交易日 |
| `intraday_pivot_left/right` | 2 / 2 | ±10 分钟 | **2 / 2** | 保持根数，但要知道**枢轴确认滞后 1 小时**，见 §5.2 |
| `entry_buffer_ticks` | 1 | — | 1 | 尺度无关 |
| `order_expiry_bars` | 3 | 15 分钟 | **1** | 1 根 = 30 分钟。取 3 就是 90 分钟，必然撞上 `order_max_recess_minutes` |

> **`pullback_max_bars` 是最关键的一个。**保持 12 根会把策略从"日内回调"
> 变成"跨时段波段"——那是另一个策略，不是本文要做的事。取 4 根让回调
> 大体落在同一个交易时段内。

### 2.3 回调状态跨时段要清零

现版本没有显式处理这件事，因为 5 分钟上 12 根 = 1 小时，很少跨段。
30 分钟上必须明确：

```
回调状态在下列时刻清零：
  - 跨越任一 session segment 边界（午休、日盘/夜盘之间）
  - 换月（contract_code 变化）
```

理由：休市两小时之后的"同一段回调"在交易意义上不成立，
`range_low` 也不该跨越跳空去定止损。

### 2.4 入场质量门（全部保留，阈值不变）

这几道门是 5 分钟版把 PF 从 1.01 抬到 4.37 的主要来源，**一个都不要去掉**：

| 门 | 参数 | 30 分钟上的说明 |
| --- | --- | --- |
| 量比上限 | `max_entry_volume_ratio = 2.0` | 尺度无关，不变 |
| 结构止损距离上限 | `max_entry_stop_distance_atr = 0.8` | 相对同周期 ATR，自动缩放，不变 |
| **区间位置** | `max_entry_range_position = 0.85` | **按交易日算，不是按根数**，所以与周期无关，不变。这是单项贡献最大的一道门（PF 1.15→3.65） |
| 区间宽度 | `min_entry_range_width_atr = 2.0` | 分子分母都用 30 分钟口径，不变 |
| 入场时段窗 | `entry_blocked_session_windows` | **判在下单那一刻**（信号根 +1 分钟），不是信号根本身 |

### 2.5 订单

```
order_type      = stop（触发式）
active_time     = 信号 30 分钟 K 线收盘后的下一根 1 分钟 K 线
order_expire_at = active_time + order_expiry_bars × 30 分钟
成交价          = max(open, trigger) 再按不利方向取整加基础滑点
```

挂单不得跨越长于 `order_max_recess_minutes = 90` 分钟的休市——
配合 `order_expiry_bars = 1` 自然满足。

---

## 3. 仓位

沿用现有 `size_for_risk`：`risk_per_trade = 1%`，结构止损反推手数。

30 分钟版的唯一变化是**手数变小约 2.35 倍**（R 更宽）。两个连带后果：

1. **高价值合约更容易只剩 1 手**（AG / AU / SC / CU）。
   现有的"任何缩放把手数压到不足 1 手时向上取整到 1 手"必须保留，
   否则回撤减仓会优先淘汰这些品种。
2. **资金占用上限更少被触发**（手数少），
   `RISK_BELOW_MINIMUM` 这类拒绝也会减少。

---

## 4. 出场

出场机制**全部沿用现有引擎**，不需要改一行引擎代码；只调参数。

### 4.1 保护性止损

初始 `stop_price` = 回调区间下沿 − 1 tick（多头）。即 1R。

### 4.2 结构跟踪止损

```
advance_trailing_stop(
    confirmed_swing = 30 分钟已确认枢轴低点,
    buffer_atr      = trailing_buffer_atr = 0.2,
    atr_value       = 30 分钟 ATR14,
)
止损只朝有利方向移动。
```

**注意滞后**：`intraday_pivot_right = 2` 意味着一个 30 分钟枢轴要等
**2 根之后（1 小时）**才被确认。5 分钟版这个滞后是 10 分钟。
所以 30 分钟版的跟踪止损明显更钝——这正是 §4.3 的地板必须留着的原因。

### 4.3 跟踪止盈地板（不变）

```
profit_floor_enabled     = True
profit_floor_arm_r       = 0.5
profit_floor_giveback_r  = 1.0
profit_floor_giveback_pct = 0.25
profit_floor_extra_slippage_ticks = 1

floor_R = max(0, peak_R - max(giveback_r, giveback_pct × peak_R))
```

**峰值仍然按 1 分钟 K 线极值确认，地板自下一分钟起生效。**
这一点不要改成 30 分钟：地板的价值就在于它比结构止损反应快，
改成 30 分钟就和 §4.2 一样钝了。

> 已知缺陷：每个 segment 开盘那一根集合竞价 K 线（21:00 / 09:00）在加载阶段
> 被丢弃，会让跨休市的峰值晚一根确认。见
> `cta/strategy/docs/2026-09-05-profit-floor-session-open-bar-trace.md`。
> 30 分钟版持仓更长、跨休市更多，**受这个缺陷影响比 5 分钟版更大**。

### 4.4 固定目标

`pullback_target_r = 2.0`。是否启用取决于现版本的 `target_exit_enabled`
设置，保持一致即可，不要在换周期的同一轮里改这个。

### 4.5 无进展时间止损

现版本 `no_progress_bars = 0`（关闭）。

30 分钟版**建议先保持关闭**。理由：在 `second_leg_brooks` 上实测过，
把 `no_progress_bars` 从 8 放宽到 20，时间止损从 55 笔降到 12 笔、
止盈地板从 37 升到 45，但净盈亏 −24,843 → **−30,846**。
时间止损的效果高度依赖形态本身的正负期望，**不要在换周期的同时引入它**——
那样两个变量一起动，出了结果分不清是谁的功劳。

### 4.6 跨休市与隔夜（重要性上升）

30 分钟版持仓时间明显更长，跨休市和隔夜从"边缘情况"变成**常规路径**：

- `pre_break_protection_enabled = True`，休市前 `pre_break_lead_minutes = 10`
  分钟检查浮盈，不足 `pre_break_min_unrealized_r` 则减仓/平仓
- 高跳空品种用 `pre_break_high_gap_min_unrealized_r = 0.5` 更严的门槛，
  并按 `pre_break_gap_risk_scale = 0.5` 降杠杆
- 隔夜保证金上限与 `overnight_reduction_minutes = 10` 的减仓照旧

> **每轮回测都要看报告里"闸门降级警告"那一节。**
> `high_gap_classification` 这道门在两个策略上分别失效过一次
> （缺 `daily_atr14` 导致 fail-open 100%）。30 分钟版更依赖它。

### 4.7 出场优先级

同一根 1 分钟 K 线上多条命中时：

```
1. STOP / 跟踪止损（最坏情况优先）
2. PROFIT_FLOOR
3. TARGET
4. 跨休市 / 隔夜减仓 / 收盘强平
```

击穿只挂起，**下一根 1 分钟开盘成交**，不许回填到触发根内。

---

## 5. 30 分钟带来的五个新问题

### 5.1 样本量（最大风险）

5.4× 更少的 K 线 → 预计 2026H1 / 40 品种只有 **~10 笔**。
10 笔没有任何统计意义。处理办法见 §6。

### 5.2 枢轴确认滞后 1 小时

见 §4.2。后果是跟踪止损在快速行情里几乎不起作用，
利润保护实际上全靠 §4.3 的地板。**这是 30 分钟版与 5 分钟版最实质的行为差异。**

### 5.3 触发价与止损同时变宽

`high + 1 tick` 的触发价在 30 分钟根上离 close 更远（绝对值上），
`range_low` 的止损也更远。R 变宽 2.35× 是好事（成本占比下降），
但**单笔亏损的绝对金额在手数补偿后应当基本不变**——
如果实测发现单笔亏损明显变大，说明手数换算哪里错了，先查这个。

### 5.4 回调可能跨时段

见 §2.3。必须显式清零，否则 `range_low` 会跨越跳空取值。

### 5.5 隔夜暴露上升

见 §4.6。建议第一轮就把 `summary.json` 里的
`peak_overnight_margin_utilization` 与 5 分钟版对比着看。

---

## 6. 样本量怎么办

按性价比排序，**都不改变形态定义**：

1. **扩品种**：`--top-n 60` + `--include-top-turnover 0.8`。
   横截面线性放大，零副作用，第一个做。
2. **拉长区间**：目前 61/69 个品种的分钟数据只有 2026 年
   （只有 AG / CU / RB 覆盖到 2020 年以前）。补数据是根本解法，
   但周期长，与本方案并行推进。
3. **放开 `pullback_max_bars` 到 6**：会把回调放宽到 3 小时、更常跨段。
   这是**改变了策略性质**的做法，只在 1、2 都做完之后作为单独变量试。
4. **同时跑 5 分钟与 30 分钟两套**，当作两个独立策略并行，
   而不是"换一个更好的周期"。它们的持仓时间、成本结构、
   隔夜暴露都不一样，本来就该分开评估。

**不要**用降低质量门（区间位置、量比、止损距离）来换样本量——
那几道门正是 5 分钟版从 PF 1.01 走到 4.37 的原因。

---

## 7. 实现方式

**不要新建策略目录。**30 分钟版与现版本的差异只有"信号周期"和一组参数：

1. `MultiTimeframeTrendConfig` 增加 `signal_timeframe_minutes: int = 5`
   （`second_leg_down` 已经有同名字段，照它的写法）
2. runner 里用 `common/short_setup_runner.signal_timeframe_bars()`
   把 1 分钟聚合到信号周期，并把合约标识与复权系数按 `bar_end` 贴回
3. 回放上下文（ATR 等）**仍然按 1 分钟算**，与信号周期无关
4. 按根数计的出场窗口用 `for_replay()` 换算成 1 分钟根数

于是 30 分钟版就是一条命令行，不是一份新代码：

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --start 2026-01-01 --end 2026-07-01 --initial-equity 1e+06 \
  --download-minute-data --top-n 40 --include-top-turnover 0.8 \
  --allow-missing-symbols \
  --config-override signal_timeframe_minutes=30 \
  --config-override pullback_min_bars=2 \
  --config-override pullback_max_bars=4 \
  --config-override order_expiry_bars=1 \
  --run-id mtt_30m
```

**验收前提：`signal_timeframe_minutes=5` 时 `trades.csv` 必须与改造前
逐字节一致。**这是判断改造有没有泄漏行为的唯一硬标准。

---

## 8. 第一轮该看什么

跑完 `mtt_30m` 之后，按顺序看：

1. **成交笔数**。低于 8 笔就不用往下看了，先做 §6 的第 1 项。
2. **闸门降级警告**那一节必须为空。
3. **单笔亏损的中位绝对金额**与 5 分钟版对比（§5.3）。差得多说明手数换算错了。
4. **`exit_reason` 分布**。如果 `TRAILING_STOP` 占比明显低于 5 分钟版、
   `PROFIT_FLOOR` 明显更高，那是 §5.2 预期中的现象，不是 bug。
5. **`peak_overnight_margin_utilization`**（§5.5）。
6. 最后才看净收益——**10 笔的净收益不构成任何结论**，
   它只用来判断"有没有明显跑偏"，不用来判断"行不行"。
