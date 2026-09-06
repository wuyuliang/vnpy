# 15 分钟入场出场策略（multi_timeframe_trend 的周期移植）

> 把 `multi_timeframe_trend` 的入场层从 **5 分钟移到 15 分钟**，出场层同步重标。
> 日线方向层、1 分钟执行层、组合风控层原样不动。
>
> 这不是一个新形态，是**同一套逻辑换一个周期**——所以本文的重点不是"策略是什么"，
> 而是**哪些参数必须跟着改、哪些不能改、改错会怎样**。

---

## 1. 先看清 `multi_timeframe_trend` 现在到底在做什么

读代码而不是读文档，实际生效的是这一条链（2026H1 / 40 品种 / 57 笔 /
+288,998 / PF 4.37）：

```text
日线    EMA5 > EMA10 > EMA20，且 (EMA5-EMA20)/EMA20 >= 2%   → 允许做多
5 分钟  always_in 形态 → 生成候选（trigger / stop）
        三道入场质量门过滤
1 分钟  挂单、成交、持仓管理、出场
```

两个容易被忽略的事实：

1. **只做多。**`candidate_direction_blacklist = (-1,)`。
2. **只做 `always_in`。**`candidate_setup_blacklist = ("pullback_breakout",)`
   ——回调突破形态在实盘配置里是关掉的。57 笔全部是 `always_in`。

### 1.1 `always_in` 形态（`multi_timeframe_trend_rules.detect_always_in`）

在最近 `always_in_window = 6` 根 5 分钟 K 线的窗口里，做多要求：

```python
progresses = (相邻高点递增的次数) >= always_in_min_progress = 4
close[-1] > close[0]                    # 窗口净上行
min(low) >= latest_swing_low            # 窗口没有跌破已确认摆动低点
trigger = max(high) + entry_buffer_ticks(1) * tick
stop    = latest_swing_low - trailing_buffer_atr(0.2) * atr14
```

`latest_swing_low` 来自 `attach_confirmed_pivots`
（`intraday_pivot_left/right = 2/2`，即左右各 2 根确认，**确认滞后 2 根**）。

### 1.2 三道入场质量门

| 门 | 判据 | 默认 |
| --- | --- | --- |
| 量比上限 | `volume / volume_threshold > limit` 则拒 | `max_entry_volume_ratio = 2.0` |
| 止损距离上限 | `abs(trigger-stop) / **daily_atr14** > limit` 则拒 | `max_entry_stop_distance_atr = 0.8` |
| 区间宽度下限 | `(range_high-range_low) / **daily_atr14** < limit` 则拒 | `min_entry_range_width_atr = 2.0` |
| 区间位置上限 | 入场价在近 2 日区间的分位 > limit 则拒 | `max_entry_range_position = 0.85` |

`volume_threshold` 是前 `volume_lookback = 20` 根的 `volume_quantile = 0.80`
分位数。区间窗口是近 `entry_range_lookback_days = 2` 个交易日 + 当日至今。

**注意后三道门的分母是日线 ATR，不是 5 分钟 ATR。**这一点在移植到 15 分钟时
是最大的坑，见 §3.2。

### 1.3 出场（引擎层，与入场周期无关）

实测 57 笔的出场分布：`STOP 31 / PROFIT_FLOOR 21 / PRE_BREAK_NO_BUFFER 5`。
中位持仓 **64 根 1 分钟 K 线**，中位 `mfe_r` 0.74。

```text
保护性止损   初始 = latest_swing_low - 0.2*ATR，之后按已确认摆动点单向上抬
             （advance_trailing_stop，只往有利方向移）
止盈地板     peak_R >= 0.5 后，floor_R = peak_R - max(1.0R, 0.25*peak_R)
             峰值按 1 分钟极值确认，地板下一分钟生效，触发后下根开盘 +1 跳滑点
跨休市保护   休市前 10 分钟浮盈不足 → 减仓/平仓（PRE_BREAK_NO_BUFFER）
挂单有效期   order_expiry_bars = 3（**信号根**），且不得跨越 > 90 分钟的休市
```

**没有时间止损**——这一点和 `second_leg_brooks` 不同，MTT 靠移动止损收口。

---

## 2. 为什么值得试 15 分钟

不是因为"周期越大越好"，而是三条可检验的理由：

1. **摆动点确认滞后是按根算的。**`intraday_pivot_right = 2` 在 5 分钟上是
   滞后 10 分钟，在 15 分钟上是 30 分钟。滞后变长意味着**摆动点更可靠**
   （少一些噪音摆动），代价是止损更远、R 更大。
2. **成本占 R 的比例会下降。**MTT 的中位持仓已经是 64 根 1 分钟，
   本来就不是高频；R 变大而手续费/滑点不变，成本占比线性下降。
   `second_leg_brooks` 的教训是成本占 R 超过 15% 之后期望 R 明显转负。
3. **候选数会掉到约 1/3。**这是代价，必须先量（§5）。57 笔 → 约 20 笔的话，
   半年样本就不足以判断，得同时扩品种或扩区间。

**这三条里第 3 条是否决项。**如果 15 分钟把样本压到 20 笔以下，
这个变体不值得单独立项，应该只当作 MTT 的一个 `--config-override` 对照跑。

---

## 3. 参数移植表（本文的核心）

把参数分成三类，**混淆这三类是移植周期最常见的错误**。

### 3.1 A 类：按"根数"定义 → 必须重标

| 参数 | 5min 默认 | 含义（时间） | 15min 建议 | 理由 |
| --- | ---: | --- | ---: | --- |
| `always_in_window` | 6 | 30 分钟 | **4** | 保持 60 分钟量级；取 2 会让 `min_progress` 无从谈起 |
| `always_in_min_progress` | 4 | 6 根里 4 次递增 | **3** | 保持同样的"多数根递增"比例（4/5 → 3/3 太严，3/4 合适）；校验要求 ≤ window−1 |
| `intraday_pivot_left/right` | 2 / 2 | ±10 分钟 | **1 / 1** | 保持 ±15 分钟量级。**保持 2/2 会让确认滞后 30 分钟，摆动点太少** |
| `pullback_min_bars` / `max_bars` | 3 / 12 | 15~60 分钟 | 1 / 4 | 该形态目前被黑名单关掉，改不改都不影响；改了便于将来打开 |
| `volume_lookback` | 20 | 100 分钟 | **20** | **不要除以 3**，见下 |
| `order_expiry_bars` | 3 | 15 分钟 | **1** | 保持 15 分钟量级；取 3 等于挂 45 分钟，跨越半个时段 |

**`volume_lookback` 为什么不除以 3**：它是分位数的样本量，不是时间窗。
除到 7 根之后 `quantile(0.80)` 基本等于"取第二大"，噪声极大。
20 根 15 分钟 = 5 小时，跨时段，可以接受；如果担心跨时段，
用 16（一个完整日盘）比用 7 好。

### 3.2 B 类：按 ATR 归一化 → 分母是哪个 ATR 决定要不要改

| 参数 | 分母 | 15min 处理 |
| --- | --- | --- |
| `trailing_buffer_atr = 0.2` | **5 分钟 `atr14`** | **不改数值**，但含义变了：15 分钟 ATR ≈ √3 × 5 分钟 ATR，所以缓冲实际变宽 1.7 倍。这是**想要的**（配合更可靠的摆动点） |
| `obstacle_buffer_atr = 0.5` | 日线 ATR | 不改 |
| `max_entry_stop_distance_atr = 0.8` | **日线 ATR** | **必须放宽，建议 1.2~1.5** |
| `min_entry_range_width_atr = 2.0` | 日线 ATR | 不改（与入场周期无关，量的是日线级别的区间） |

**`max_entry_stop_distance_atr` 是这次移植最大的坑。**

它量的是 `abs(trigger − stop) / daily_atr14`。15 分钟的摆动低点比 5 分钟的
远得多，`trigger − stop` 会显著变大，而分母（日线 ATR）**完全没变**。
沿用 0.8 会把大量本来合格的 15 分钟信号判成"止损太远"。

> **实现时必须先量这个门的拒绝率。**如果 `ENTRY_STOP_DISTANCE_TOO_WIDE`
> 的占比从 5 分钟的水平明显上升，就是这个原因，不要误以为是形态不行。

### 3.3 C 类：按"分钟"或"天"定义 → 一律不改

```text
entry_blocked_session_windows   (('13:00','15:00'), ('22:00','02:30'))
order_max_recess_minutes        90
overnight_reduction_minutes     10
entry_range_lookback_days       2
atr_period                      14      ← 在各自周期上算，不用改
daily_ema_fast/mid/slow         5/10/20
always_in_long_daily_ema_gap_min_ratio  0.02
daily_obstacle_lookback         20
```

组合风控层（`max_concurrent_positions`、`max_positions_per_sector`、
`daily_circuit_breaker_*`、`risk_per_trade`、保证金利用率）**全部不改**。

### 3.4 出场参数

| 参数 | 5min | 15min | 说明 |
| --- | ---: | ---: | --- |
| `profit_floor_arm_r` | 0.5 | 0.5 | R 的定义变了但比例不变 |
| `profit_floor_giveback_r` | 1.0 | 1.0 | 同上 |
| `profit_floor_giveback_pct` | 0.25 | 0.25 | 同上 |

**出场三个参数一个都不改。**它们全部以 R 为单位，而 R 已经随周期自动放大。
峰值仍按 **1 分钟**极值确认——出场层本来就在 1 分钟上跑，与入场周期无关。

> 反过来说：如果 15 分钟版本的出场分布明显偏离
> `STOP 54% / PROFIT_FLOOR 37%`，说明 R 的放大幅度和预期不符，
> 要回头查 §3.2 的 `trailing_buffer_atr` 实际效果，而不是先去调地板参数。

---

## 4. 周期可行性检查（已验证）

15 分钟能否整除各交易时段——这决定聚合器能不能产出完整 K 线
（`_complete_minute_segment` 要求每段恰好 `duration` 根）：

| 时段 | 分钟 | ÷15 | |
| --- | ---: | ---: | --- |
| 早盘 09:00–11:30 | 150 | 10 | ✔ |
| 午盘 13:30–15:00 | 90 | 6 | ✔ |
| 夜盘 21:00–23:00 | 120 | 8 | ✔ |
| 夜盘至 01:00 | 240 | 16 | ✔ |
| 夜盘至 02:30 | 330 | 22 | ✔ |

**全部整除，没有残根。**每天约 24 根（日盘 16 + 夜盘 8），
对比 5 分钟的约 72 根。

---

## 5. 先量再实现

**不要直接改配置跑全量。**先回答两个问题：

### 5.1 候选量还剩多少

用 `cta/analysis/second_leg_down_funnel_scan.py` 的框架
（`--bar-minutes 15`）复刻 `always_in` 的四个条件，数一下 40 品种半年能出多少
候选。判据：

- **≥ 40** → 值得作为独立变体立项
- 20~40 → 只能当 MTT 的对照跑，不单独立项
- **< 20** → 放弃，或同时把品种池扩到 60

### 5.2 三道质量门各自的拒绝率

尤其是 `ENTRY_STOP_DISTANCE_TOO_WIDE`（§3.2）。先在原型里算出
`abs(trigger-stop)/daily_atr14` 的分布，据此定 `max_entry_stop_distance_atr`
的初值，而不是先拿 0.8 跑一遍再回来调。

---

## 6. 实现方式：不要新建策略包

15 分钟版本与 5 分钟版本**只差参数**，所以：

1. 给 `MultiTimeframeTrendConfig` 加一个 `entry_timeframe_minutes: int = 5`
   字段，`__post_init__` 校验它能整除所有 segment 长度。
2. runner 里把 `aggregate_five(minutes=5)` 改成
   `minutes=config.entry_timeframe_minutes`。当前代码里 5 是硬编码的
   （`runner.py` 有 6 处 `minutes=5`），需要一并参数化。
   **注意区分**：其中给报表和图表用的那两处 5 分钟聚合**保持 5 分钟**
   （图表的中间那张图是审阅用的，不是信号），只改喂给策略的那一路。
3. 15 分钟的参数集合通过 `--config-override` 传，不写死默认值。

这样一条命令就能 A/B：

```bash
# 基准（5 分钟）
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --start 2026-01-01 --end 2026-07-01 --initial-equity 1e+06 \
  --download-minute-data --top-n 40 --include-top-turnover 0.8 \
  --allow-missing-symbols --run-id tf5_base

# 15 分钟
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --start 2026-01-01 --end 2026-07-01 --initial-equity 1e+06 \
  --download-minute-data --top-n 40 --include-top-turnover 0.8 \
  --allow-missing-symbols \
  --config-override entry_timeframe_minutes=15 \
  --config-override always_in_window=4 \
  --config-override always_in_min_progress=3 \
  --config-override intraday_pivot_left=1 \
  --config-override intraday_pivot_right=1 \
  --config-override order_expiry_bars=1 \
  --config-override max_entry_stop_distance_atr=1.3 \
  --run-id tf15_v1
```

**验收前提：`entry_timeframe_minutes=5` 时 `trades.csv` 与改造前逐字节一致。**

---

## 7. 结果怎么读

样本会从 57 笔掉到约 20 笔，所以**不能直接比净收益**。要看的是：

| 指标 | 怎么读 |
| --- | --- |
| 候选数 / 成交数 | 先确认样本够不够，不够就别看后面 |
| **每笔期望 R** | 唯一可跨样本量比较的指标 |
| 成本 / R 的中位 | 移植 15 分钟的主要理由就是这个要下降 |
| 出场分布 | 偏离 `STOP 54% / FLOOR 37%` 说明 R 的放大不符预期（§3.4） |
| `ENTRY_STOP_DISTANCE_TOO_WIDE` 占比 | 高就是 §3.2 没调够 |
| 中位 `mfe_r` | 基准 0.74；明显下降说明 15 分钟入得太晚 |

**并且**：2026H1 是 88% 品种上涨的单边行情，任何做多变体在这半年的绝对收益
都不能当结论。15min vs 5min 的**相对比较**在同区间同品种下成立；
"15 分钟能赚钱"这类绝对判断不成立。

---

## 8. 已知地雷

1. **时段开盘那根集合竞价 K 线仍然被丢**（21:00 / 09:00，2026-03 全品种
   1,474 根 / 268 万手）。15 分钟聚合会把这个缺失摊进第一根里，影响比 5 分钟小，
   但没有消失。见 `../2026-09-05-profit-floor-session-open-bar-trace.md`。
2. **`runner.py` 里 `minutes=5` 有 6 处**，其中给图表和报表用的不要一起改。
3. **`always_in_min_progress <= always_in_window - 1`** 是配置层的硬校验，
   改 window 时要同步改，否则直接抛错。
4. **闸门降级警告要看。**`second_leg_brooks` 上出现过
   `high_gap_classification: fail-open 40/40（100%）`，同类缺陷在 MTT 上修过
   一次。每轮回测报告都要确认这一节为空。
5. **`intraday_pivot_left/right` 改成 1/1 之后摆动点会变多、变噪**。
   如果 `STOP` 占比明显上升（>65%），说明摆动点太敏感，止损被反复打到，
   回到 2/2 再试一次——这是本移植里最需要 A/B 的一个参数。

---

## 9. 与现有文档的关系

| 文档 | 关系 |
| --- | --- |
| `2026-08-31-multi_timeframe_trend_strategy.md` | 5 分钟原版规格 |
| `多周期趋势回调策略_codex.md` | 四层架构 + 回调状态机（另一条路线，不是本文） |
| `多周期趋势回调策略_v2.md` | 数据阻塞与证伪方法论，本文 §7 的行情警告同源 |
| **本文** | 同一套逻辑的**周期移植**，改动面最小、最快能出对照结果 |

在数据补齐之前（`_v2.md` §0），本文是**唯一一个不需要新数据就能跑出有意义
对照**的方向：它比的是同一区间、同一品种、同一逻辑下 5min vs 15min，
相对比较不受行情单边影响。
