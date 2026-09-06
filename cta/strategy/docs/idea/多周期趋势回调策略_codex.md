# 多周期趋势回调策略

## 1. 文档定位

本文定义一套面向中国商品期货的双向趋势回调策略构想。策略采用四层时间框架：

```text
1 日线：决定是否允许做多、做空或停止开仓
30 分钟：判断中周期趋势是否仍处于可延续阶段
5 分钟：识别回调、确认趋势恢复并生成候选
1 分钟：激活订单、模拟成交和执行持仓管理
```

这是一份待实现、待验证的独立策略规格，不是当前
`multi_timeframe_trend` 策略的参数变体，也不继承其历史收益结论。文中参数均为首轮研究种子，
不代表最优参数，不构成收益率、胜率或最大回撤承诺。

策略遵循以下边界：

1. 多头与空头采用镜像价格规则，允许分别统计、验证和关闭。
2. 主力连续合约只用于形成研究信号，真实撮合、手续费和盈亏必须映射到实际合约。
3. 所有特征只使用已经完成的 K 线，信号确认后订单最早从下一 1 分钟事件生效。
4. 任何依赖未来摆动点、未来区间极值、最终主力映射或当前合约参数回填历史的结果均不属于正式结果。
5. 先定义可审计规则，再通过滚动样本外验证判断策略是否具有净成本后的统计优势。

## 2. 策略目标与非目标

### 2.1 目标

1. 只在日线和 30 分钟方向一致时寻找顺势机会。
2. 避免直接追逐趋势扩张 K 线，等待 5 分钟级别出现可量化回调。
3. 在回调未破坏中周期结构、且价格重新恢复趋势方向时入场。
4. 使用回调结构定义止损，以账户风险反推手数。
5. 使用实际合约和 1 分钟事件模拟跳空、滑点、涨跌停、换月和分腿退出。
6. 在多品种组合中限制同板块暴露、总风险和保证金占用。

### 2.2 非目标

1. 不预测趋势顶部或底部，不做逆势抄底摸顶。
2. 不保证每个品种、每个月或每个方向都盈利。
3. 不针对单个品种按最终收益定制形态阈值。
4. 不依靠固定盈利目标制造高胜率。
5. 不使用连续合约价格直接成交。
6. 不在元数据缺失时把假设成交混入正式绩效。

## 3. 核心假设

策略需要验证的假设如下：

1. 大周期方向一致时，中周期回调后的恢复信号比无条件追突破具有更高的净期望。
2. 健康趋势中的回调通常表现为逆向推进效率下降、K 线重叠增加、深度受限，而不是持续的强反向扩张。
3. 回调后的趋势恢复需要同时具备价格结构、收盘位置和最低限度的动量证据；单根颜色反转不足以构成入场。
4. 使用结构止损并按风险定仓，可以在不同价格、乘数和波动水平的品种之间保持可比较风险。
5. 双向镜像规则在逻辑上对称，但多头与空头的真实绩效可能不对称，必须分别报告。

以上均为研究假设。只有锁定参数后的滚动样本外结果，才能支持或否定这些假设。

## 4. 市场、品种与周期

### 4.1 研究市场

- 中国商品期货中具有足够历史、流动性和可核验交易机制的品种。
- 品种进入研究池只依据当时可见的成交额、持仓量、数据完整度和成本占波动比例。
- 不因某品种历史回测亏损而事后删除，也不因少量高收益交易而事后加入。
- 新上市、长期停牌、临近交割或主力迁移不稳定的品种可以输出候选诊断，但不得强制交易。

### 4.2 首轮周期种子

| 层级 | 周期 | 职责 | 最小预热 |
|---|---|---|---:|
| 大周期 | 1 日 | 方向许可、日线障碍、波动环境 | 60 根完整日线 |
| 中周期 | 30 分钟 | 趋势健康度、周期状态、结构失效 | 80 根完整 30 分钟线 |
| 形态周期 | 5 分钟 | 回调状态、恢复信号、结构止损 | 120 根完整 5 分钟线 |
| 执行周期 | 1 分钟 | 订单激活、成交、止损与退出 | 当前实际合约完整分钟流 |

`1d/30m/5m/1m` 是预先声明的首轮研究组合，不是从最终盈亏挑选出的最优组合。后续可以在训练与
验证区间比较 `1d/60m/15m/1m` 等候选，但必须在进入锁定测试区间前冻结选择。

### 4.3 周期可用性

每个品种在每个 walk-forward 折开始前，仅使用此前数据计算：

```text
median_atr_ticks
stressed_cost_to_atr
missing_bar_ratio
zero_volume_ratio
complete_session_ratio
history_length
```

周期组合必须满足最低信息密度和完整度。若首轮周期不可用，则该品种状态为 `UNAVAILABLE`，
不能临时根据测试期收益改用另一个周期。

## 5. 数据与历史交易机制

### 5.1 行情数据

每根 K 线至少包含：

```text
symbol, contract_code, exchange, exchange_trade_date
bar_start, bar_end, open, high, low, close
volume, open_interest, turnover
```

要求：

1. K 线按交易所 `SessionSpec` 聚合，不按自然日切分夜盘。
2. 30 分钟和 5 分钟 K 线只能由完整交易时段内的 1 分钟线聚合。
3. 不完整桶不能通过填充未来价格补齐。
4. 集合竞价、时段首根和跨休市跳空必须有明确口径，信号聚合与执行事件不得静默丢失有效成交。
5. 所有派生缓存必须带算法版本、输入文件指纹和交易时段版本。

### 5.2 历史元数据

每个实际合约在订单和成交时点必须具备带 `effective_from` 与 `known_at` 的：

```text
contract_multiplier, price_tick
open_fee, close_fee, close_today_fee
margin_rate_long, margin_rate_short
limit_up, limit_down
session_spec, trading_calendar
last_trade_date, delivery_restriction
supported_order_types, active_contract_mapping
```

正式区间缺少任一必要交易机制时：

```text
official_status = BLOCKED_METADATA
official_performance = null
primary_curve = null
```

可使用 `sim/live` 默认值进行工程冒烟，但该结果必须单独标记为 `NON_CAUSAL_SCENARIO`，不能混入
正式绩效。机制覆盖完整的子区间只能标记为 `COVERED_SUBPERIOD_DIAGNOSTIC`。

## 6. 因果时间模型

### 6.1 时间字段

| 字段 | 含义 |
|---|---|
| `bar_start/bar_end` | K 线真实覆盖区间 |
| `pivot_time` | 摆动高低点实际发生的位置 |
| `feature_asof` | 特征使用的最后一根已完成 K 线 |
| `known_at` | 结构或形态首次可以确认的事件 |
| `signal_time` | 生成候选的已完成 5 分钟事件 |
| `decision_asof` | 方向、风控与元数据完成判断的事件 |
| `order_active_at` | 订单最早可参与成交的 1 分钟事件 |
| `fill_time` | 实际或模拟成交事件 |
| `label_end` | 仅供研究标签使用的未来观察终点 |

每个时间字段同时记录事件序号，强制满足：

```text
(feature_asof, feature_seq)
    <= (known_at, known_seq)
    <= (signal_time, signal_seq)
    <= (decision_asof, decision_seq)
    <  (order_active_at, active_seq)
    <= (fill_time, fill_seq)
```

### 6.2 高周期对齐

在任一 5 分钟信号时刻：

1. 日线快照必须来自当前时刻之前已经完整结束的交易日。
2. 30 分钟快照的 `bar_end` 必须不晚于当前 5 分钟 `bar_end`。
3. 当前尚未完成的 30 分钟桶不能参与趋势状态判断。
4. 摆动点可以画在 `pivot_time`，但只能从 `known_at` 起用于交易。

### 6.3 决策与成交

5 分钟信号在收盘后确认。订单最早在下一根 1 分钟事件生效，不允许：

- 按信号 K 线内部价格成交；
- 用突破 K 线收盘后才知道的成交量条件反推盘中成交；
- 用触发根新形成的峰值反算该触发根的止盈价格；
- 用最终换月映射或未来流动性决定历史订单合约。

## 7. 首轮配置参数

以下数值是共享研究种子，后续只能在训练/验证区间调整：

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `daily_ema_fast/mid/slow` | `10/20/60` | 日线方向 EMA |
| `daily_slope_lookback` | `5` | 日线中速 EMA 斜率窗口 |
| `daily_min_gap_atr` | `0.20` | EMA10 与 EMA60 最小 ATR 间距 |
| `medium_ema_fast/slow` | `20/50` | 30 分钟趋势 EMA |
| `medium_efficiency_lookback` | `12` | 30 分钟路径效率窗口 |
| `medium_min_efficiency` | `0.25` | 趋势状态最低路径效率 |
| `medium_state_confirm_bars` | `2` | 状态切换确认根数 |
| `pullback_min/max_bars` | `2/12` | 5 分钟回调长度 |
| `pullback_min_depth_atr` | `0.30` | 最小回调深度 |
| `pullback_max_depth_atr` | `2.00` | 最大回调深度 |
| `pullback_min/max_retrace` | `0.20/0.70` | 相对前一推动段的回撤比例 |
| `pullback_max_opposite_efficiency` | `0.65` | 回调反向路径效率上限 |
| `signal_min_body_atr` | `0.30` | 恢复信号实体最小值 |
| `signal_min_close_location` | `0.65` | 收盘靠近趋势端的最小位置 |
| `volume_baseline_bars` | `20` | 同时段成交量基线窗口 |
| `signal_min_volume_ratio` | `0.80` | 恢复信号最低量比 |
| `signal_max_volume_ratio` | `2.50` | 排除异常放量的量比上限 |
| `entry_buffer_ticks` | `1` | 停止入场触发缓冲 |
| `stop_buffer_atr` | `0.10` | 回调结构外止损缓冲 |
| `min/max_stop_atr` | `0.30/1.50` | 可接受止损距离区间 |
| `order_expiry_5m_bars` | `3` | 入场单最长有效期 |
| `risk_per_trade` | `0.005` | 单笔权益风险 0.5% |
| `max_total_open_risk` | `0.02` | 组合总初始风险 2% |
| `max_concurrent_positions` | `4` | 最大同时持仓数 |
| `max_positions_per_sector` | `2` | 单板块最大持仓数 |
| `profit_floor_arm_r` | `2.0` | 止盈地板启动峰值 |
| `profit_floor_giveback_r` | `1.0` | 最小允许回吐 R |
| `profit_floor_giveback_pct` | `0.25` | 峰值百分比回吐 |
| `time_stop_bars` | `12` | 无进展时间止损窗口 |
| `time_stop_min_mfe_r` | `0.5` | 时间窗口内最低 MFE |

## 8. 日线方向许可

### 8.1 日线特征

对已完成日线计算：

```text
EMA10, EMA20, EMA60
ATR14
ema20_slope = (EMA20[t] - EMA20[t-5]) / ATR14[t]
ema_gap = abs(EMA10[t] - EMA60[t]) / ATR14[t]
```

ATR、EMA 和斜率只读取最新完整日线。预热不足时方向为 `UNAVAILABLE`。

### 8.2 多头许可

必须全部满足：

```text
EMA10 > EMA20 > EMA60
ema20_slope > 0
ema_gap >= 0.20
close > EMA20
```

### 8.3 空头许可

完全镜像：

```text
EMA10 < EMA20 < EMA60
ema20_slope < 0
ema_gap >= 0.20
close < EMA20
```

### 8.4 中性与切换

不满足任一方向时为 `DAILY_NEUTRAL`，禁止新开仓但不立即平掉已有仓位。只有当日线明确切换到
持仓反方向，才登记 `DAILY_DIRECTION_REVERSAL`，从下一可交易 1 分钟事件执行退出。

## 9. 30 分钟趋势健康状态

### 9.1 观察量

使用已完成 30 分钟 K 线计算：

```text
EMA20, EMA50, ATR14
normalized_slope = (EMA20[t] - EMA20[t-5]) / ATR14[t]
path_efficiency = abs(close[t] - close[t-12])
                  / sum(abs(close[i] - close[i-1]), i=t-11..t)
close_distance = (close[t] - EMA20[t]) / ATR14[t]
```

`path_efficiency` 分母为零时状态为 `UNAVAILABLE`，不以 0 代替。

### 9.2 状态定义

多头健康证据：

```text
EMA20 > EMA50
normalized_slope > 0
path_efficiency >= 0.25
close_distance >= -0.25
```

空头健康证据镜像：

```text
EMA20 < EMA50
normalized_slope < 0
path_efficiency >= 0.25
close_distance <= 0.25
```

状态枚举：

```text
BULL_HEALTHY
BEAR_HEALTHY
TRADING_RANGE
TRANSITION
UNAVAILABLE
```

若多空均不满足且 `path_efficiency < 0.20`，记为 `TRADING_RANGE`；其余模糊状态记为
`TRANSITION`。

### 9.3 迟滞

新状态连续满足 `medium_state_confirm_bars=2` 根后才切换。状态生效时间是第二根确认 K 线的
`bar_end`，不得回填到第一根或结构发生位置。

允许组合：

| 日线许可 | 30 分钟状态 | 行为 |
|---|---|---|
| 多头 | `BULL_HEALTHY` | 寻找多头回调 |
| 空头 | `BEAR_HEALTHY` | 寻找空头回调 |
| 任意 | `TRADING_RANGE` | 禁止新开仓 |
| 任意 | `TRANSITION` | 禁止新开仓 |
| 任意 | `UNAVAILABLE` | 禁止新开仓 |
| 多空方向冲突 | 任意 | 禁止新开仓 |

## 10. 5 分钟推动段

回调必须依附于一个已完成的同向推动段，不能从任意局部高低点开始。

### 10.1 多头推动段

在日线多头许可和 `BULL_HEALTHY` 下，最近 `3..8` 根完整 5 分钟线必须满足：

```text
impulse_move = impulse_high - impulse_start_close
impulse_move / ATR14 >= 1.0
同向收盘 K 线占比 >= 60%
最后收盘价位于推动区间上半部
```

`impulse_high` 是该推动段截至当前已完成 K 线的最高价。推动段开始位置只用于结构与图表，
其识别不能依赖后续最终涨幅。

### 10.2 空头推动段

完全镜像：

```text
impulse_move = impulse_start_close - impulse_low
impulse_move / ATR14 >= 1.0
同向收盘 K 线占比 >= 60%
最后收盘价位于推动区间下半部
```

### 10.3 推动失效

以下任一情况发生时，不再以该推动段建立新回调：

- 30 分钟趋势状态失效；
- 5 分钟价格反向越过推动段起点；
- 距离推动结束超过 12 根 5 分钟线；
- 实际合约发生不可迁移的换月；
- 数据或元数据状态变为不可用。

## 11. 5 分钟回调状态机

### 11.1 状态

```text
IDLE
IMPULSE_READY
PULLBACK_ACTIVE
RESUMPTION_ARMED
ORDER_WORKING
INVALIDATED
```

状态只随已完成 5 分钟事件推进，订单状态由独立执行模块维护。

### 11.2 回调启动

多头回调在推动完成后，出现第一根满足任一条件的已完成 5 分钟线时启动：

```text
close[t] < close[t-1]
或 high[t] < high[t-1]
或 close[t] < EMA10_5m[t]
```

空头镜像：

```text
close[t] > close[t-1]
或 low[t] > low[t-1]
或 close[t] > EMA10_5m[t]
```

启动 K 线计为回调第 1 根。每个状态记录：

```text
pullback_start_time
pullback_bars
pullback_high, pullback_low
impulse_start, impulse_extreme
pullback_depth_atr
retracement_ratio
opposite_efficiency
```

### 11.3 回调深度

多头：

```text
depth = impulse_high - pullback_low
retrace = depth / (impulse_high - impulse_start_close)
```

空头：

```text
depth = pullback_high - impulse_low
retrace = depth / (impulse_start_close - impulse_low)
```

合格区间：

```text
0.30 <= depth / ATR14_5m <= 2.00
0.20 <= retrace <= 0.70
2 <= pullback_bars <= 12
```

### 11.4 回调质量

反向路径效率定义为：

```text
opposite_efficiency = abs(pullback_last_close - pullback_first_open)
                      / sum(abs(close[i] - close[i-1]))
```

要求 `opposite_efficiency <= 0.65`。该约束用于排除持续、顺畅的反向趋势；分母为零时记为 0，
并保留 `flat_pullback=1` 审计字段。

### 11.5 成交量收缩

回调平均成交量与此前同一 `SessionSpec.segment` 相对位置的 20 个已完成样本比较：

```text
pullback_volume_ratio = mean(pullback_volume) / prior_slot_volume_mean
```

时段首根不进入基线。首轮仅记录该特征，不作为硬过滤；是否加入
`pullback_volume_ratio <= 1.0` 必须通过消融验证。

### 11.6 回调失效

任一条件触发即进入 `INVALIDATED`：

1. 回调超过 12 根。
2. 回撤比例超过 0.70。
3. 回调深度超过 2 ATR。
4. 多头跌破推动段起点，空头突破推动段起点。
5. 日线与 30 分钟方向不再一致。
6. 出现强反向 K 线：实体大于 1 ATR 且收盘位于反向端 20% 内。
7. 进入禁止开仓的时段尾部。

失效后必须等待新的同向推动段，不能沿用旧回调边界。

## 12. 趋势恢复信号

### 12.1 K 线归一化

对当前已完成 5 分钟信号 K 线定义：

```text
range = high - low
body = abs(close - open)
body_atr = body / ATR14_5m
long_close_location = (close - low) / range
short_close_location = (high - close) / range
```

`range <= 0` 时不能形成恢复信号。

### 12.2 多头恢复

必须全部满足：

1. 日线允许做多，30 分钟为 `BULL_HEALTHY`。
2. 回调处于 `PULLBACK_ACTIVE`，长度和深度合格。
3. 当前收盘价严格高于前一根 5 分钟最高价。
4. 当前收盘价严格高于回调最近两根最高价的较大值。
5. `body_atr >= 0.30`。
6. `long_close_location >= 0.65`。
7. 当前成交量比位于 `[0.80, 2.50]`。
8. 从计划入场价到最近日线阻力至少有 `1.5R` 空间。

### 12.3 空头恢复

完全镜像：

1. 日线允许做空，30 分钟为 `BEAR_HEALTHY`。
2. 回调长度和深度合格。
3. 当前收盘价严格低于前一根最低价。
4. 当前收盘价严格低于回调最近两根最低价的较小值。
5. `body_atr >= 0.30`。
6. `short_close_location >= 0.65`。
7. 当前成交量比位于 `[0.80, 2.50]`。
8. 到最近日线支撑至少有 `1.5R` 空间。

### 12.4 成交量基线

量比只使用当前信号之前的已完成 5 分钟线，并按交易时段相对位置计算，不能写死
`09:01`、`21:01` 或固定夜盘收盘时间。基线必须剔除每个 segment 首根，以免集合竞价放量污染。

## 13. 入场计划

### 13.1 触发价

多头：

```text
entry_trigger = signal_high + 1 tick
```

空头：

```text
entry_trigger = signal_low - 1 tick
```

买入停止价向上取整，卖出停止价向下取整。信号在 5 分钟收盘确认，订单从下一根可交易
1 分钟事件生效。

### 13.2 初始止损

多头：

```text
raw_stop = pullback_low - max(1 tick, 0.10 * ATR14_5m)
```

空头：

```text
raw_stop = pullback_high + max(1 tick, 0.10 * ATR14_5m)
```

保护性卖出止损向下取整，保护性买入止损向上取整。计划止损距离必须满足：

```text
0.30 * ATR14_5m <= abs(entry_trigger - stop) <= 1.50 * ATR14_5m
```

不允许缩短结构止损来强行满足仓位预算。

### 13.3 有效期与取消

订单最多有效 3 根完整 5 分钟线，并在以下情况立即取消：

- 回调结构失效；
- 日线或 30 分钟许可失效；
- 实际合约主力映射改变；
- 多头价格先跌破结构止损，空头价格先突破结构止损；
- 订单跨越超过配置上限的休市；
- 进入禁止开仓窗口；
- 元数据或订单能力不可用。

未触发订单不会被计为一次回调入场，也不会影响后续形态编号。

## 14. 仓位与组合风险

### 14.1 单笔风险

```text
risk_budget = marked_equity * 0.005

loss_per_lot = abs(estimated_fill - structural_stop) * contract_multiplier
               + stressed_round_trip_cost_per_lot

quantity = floor(risk_budget / loss_per_lot)
```

`quantity < 1` 时记录 `RISK_BELOW_ONE_LOT` 并拒绝，不缩止损、不借用未来盈利、不默认买一手。

### 14.2 组合约束

首轮约束：

```text
max_total_open_risk = 2% marked equity
max_concurrent_positions = 4
max_positions_per_sector = 2
intraday_margin_utilization <= 60%
overnight_margin_utilization <= 20%
single_symbol_margin_utilization <= 30%
```

同一板块的多品种风险不能简单相加后继续满额开仓。若新订单会突破任一限制，先按可用风险向下裁剪；
裁剪后不足一手则拒绝。

### 14.3 信号竞争

同一事件出现多个候选时，使用信号生成前即可计算的质量分排序：

```text
quality_score =
    0.30 * medium_efficiency_rank
  + 0.25 * signal_body_rank
  + 0.20 * close_location_rank
  + 0.15 * obstacle_space_rank
  + 0.10 * pullback_compactness_rank
```

排名只能在同一事件的可用候选之间计算，并用 `symbol` 稳定排序打破同分。权重必须在训练/验证区间冻结。

## 15. 1 分钟成交模型

### 15.1 入场成交

多头停止单：

```text
若 open >= trigger：reference = open
否则若 high >= trigger：reference = trigger
否则：未成交
```

空头镜像：

```text
若 open <= trigger：reference = open
否则若 low <= trigger：reference = trigger
否则：未成交
```

最终成交价在参考价上叠加不利滑点并按 tick 取整。零成交量、封板且无法排队成交或历史订单能力不支持时，
不得假设成交。

### 15.2 同根歧义

如果同一根 1 分钟线既可能触发入场又可能触及止损，但无法从更细数据确定路径，则采用不利顺序：

```text
先入场，再止损
```

并记录 `ADVERSE_INTRABAR_SEQUENCE=1`。也可以在严格模式直接拒绝该成交，但两种模式不能混在同一正式曲线。

### 15.3 涨跌停和跳空

1. 跳过入场触发价时按开盘或首个可成交价成交，不能按理想触发价成交。
2. 跳过止损时按开盘对手价加不利滑点退出。
3. 封死涨跌停且无法成交时保持待执行退出，每根 1 分钟重试。
4. 待退出期间不得更新为更乐观的退出价格。

## 16. 持仓管理与退出

首轮基线不做主动加仓，不做固定比例止盈，不因普通反向 K 线立即退出。

### 16.1 硬止损

初始结构止损从成交后立即有效。后续止损只能向有利方向移动，不能放宽。

### 16.2 5 分钟结构跟踪

使用左右各 `2/2` 根确认的 5 分钟摆动点：

```text
long_stop = max(previous_stop,
                confirmed_swing_low - max(1 tick, 0.10 * ATR14_5m))

short_stop = min(previous_stop,
                 confirmed_swing_high + max(1 tick, 0.10 * ATR14_5m))
```

摆动止损从 `known_at` 后的下一 1 分钟事件生效，不能回填到 `pivot_time`。

### 16.3 止盈地板

以建仓初始手数和初始风险现金冻结 1R：

```text
peak_r = 最大有利价格移动对应的初始风险倍数
giveback_r = max(1.0, 0.25 * peak_r)
floor_r = max(0, peak_r - giveback_r)
```

峰值达到 `2R` 后启用。部分减仓不改变 R 标尺，也不按剩余手数缩小 `giveback_r`。
多头地板只上移，空头地板只下移。

触发时序：

1. 第 t 根 1 分钟线只使用 t-1 收盘后已经确认的地板判断是否击穿。
2. 若击穿，仅登记 `PROFIT_FLOOR` 待退出。
3. 第 t+1 根按开盘对手价和不利滑点成交。
4. 不使用第 t 根新峰值反算第 t 根的成交价。

### 16.4 无进展时间止损

若入场后 12 根完整 5 分钟线内：

```text
MFE < 0.5R
```

则登记 `NO_PROGRESS_TIME_STOP`，下一 1 分钟开盘退出。该模式在入场时冻结，不能因为浮亏而临时延长。

### 16.5 高周期失效

以下事件触发全平：

- 日线明确切换到反方向；
- 30 分钟连续两根确认反方向健康状态；
- 合约进入预声明的交割限制窗口；
- 换月后无法按既定规则迁移持仓；
- 回测区间结束。

### 16.6 退出优先级

同一 1 分钟事件按以下顺序处理：

```text
1. 已挂起且可成交的强制退出
2. 合约/交割/换月强制退出
3. 保护性硬止损
4. 止盈地板触发登记
5. 时间止损与高周期失效登记
6. 结构止损更新
7. 新订单成交
8. 新候选生成与提交
```

若同一根 1 分钟线同时触及硬止损和止盈地板且路径未知，采用更不利的硬止损结果。

## 17. 跨休市与隔夜风险

所有时段判断基于 `SessionSpec.segment` 相对位置，不写死具体钟点。

在每个长休市前 `10` 分钟：

1. 停止新开仓。
2. 计算持仓当前未实现 R、隔夜保证金和品种历史跳空分位数。
3. 若组合隔夜保证金超过 20%，按预声明顺序减仓至限制内。
4. 高跳空品种可使用更严格的最低浮盈门槛。
5. 一手仓位若决定减仓且保留量向下取整为 0，则直接清仓。

减仓只改变实际手数，不改变初始 R、历史峰值或后续止盈地板公式。

## 18. 换月与实际合约

### 18.1 信号与成交分离

- 连续合约：用于日线、30 分钟和 5 分钟信号研究。
- 实际合约：用于订单触发、成交、止损、手续费、保证金、涨跌停和盈亏。
- 候选创建时冻结当时已知的实际合约映射和调整参数。

### 18.2 换月处理

默认不把旧合约持仓无成本平移到新合约。首轮采用：

1. 待成交订单映射变化时取消，记录 `ROLL_MAPPING_CHANGED`。
2. 已有持仓在预声明换月窗口按实际旧合约退出。
3. 新合约若仍满足形态，必须重新生成候选，不能继承旧订单触发价。
4. 换月价差、双边手续费和滑点必须进入现金权益。

## 19. 候选与拒绝码

### 19.1 核心拒绝码

| 拒绝码 | 含义 |
|---|---|
| `DAILY_TREND_NEUTRAL` | 日线不允许任何方向 |
| `DAILY_DIRECTION_MISMATCH` | 候选方向与日线许可不一致 |
| `MEDIUM_STATE_UNAVAILABLE` | 30 分钟状态预热或数据不足 |
| `MEDIUM_TREND_NOT_HEALTHY` | 30 分钟不是同向健康趋势 |
| `IMPULSE_NOT_FOUND` | 没有合格同向推动段 |
| `PULLBACK_TOO_SHORT` | 回调不足 2 根 |
| `PULLBACK_TOO_LONG` | 回调超过 12 根 |
| `PULLBACK_TOO_SHALLOW` | 回调小于 0.30 ATR 或 20% |
| `PULLBACK_TOO_DEEP` | 回调大于 2 ATR 或 70% |
| `PULLBACK_OPPOSITE_TOO_STRONG` | 反向路径效率过高 |
| `RESUMPTION_PRICE_NOT_CONFIRMED` | 未突破短期回调结构 |
| `RESUMPTION_BODY_TOO_SMALL` | 恢复 K 线实体不足 |
| `RESUMPTION_CLOSE_WEAK` | 收盘位置不够靠近趋势端 |
| `VOLUME_BASELINE_UNAVAILABLE` | 同时段成交量基线不足 |
| `VOLUME_RATIO_OUT_OF_RANGE` | 恢复信号量比过低或异常过高 |
| `HTF_OBSTACLE_NEAR` | 到日线障碍不足 1.5R |
| `STOP_DISTANCE_OUT_OF_RANGE` | 结构止损距离不在允许区间 |
| `RISK_BELOW_ONE_LOT` | 一手风险超过预算 |
| `TOTAL_OPEN_RISK_LIMIT` | 组合总风险达到上限 |
| `SECTOR_CONCENTRATION_LIMIT` | 板块持仓达到上限 |
| `MARGIN_LIMIT` | 保证金限制不足以容纳一手 |
| `ENTRY_WINDOW_BLOCKED` | 位于时段相对禁止开仓窗口 |
| `ORDER_CROSSED_SESSION_RECESS` | 挂单跨越过长休市 |
| `ORDER_EXPIRED` | 订单超过有效期 |
| `ROLL_MAPPING_CHANGED` | 实际合约映射改变 |
| `BLOCKED_METADATA` | 正式成交所需历史机制缺失 |
| `LIMIT_OR_LIQUIDITY_BLOCKED` | 涨跌停或成交条件不支持撮合 |

### 19.2 状态与拒绝分离

形态未成熟属于状态，不应全部记成拒绝。只有形成可评估候选后被规则挡下，才进入
`rejections.csv`。例如 `PULLBACK_ACTIVE` 但尚无恢复信号，只进入状态审计，不制造大量伪拒绝。

## 20. 审计字段与输出

### 20.1 候选字段

```text
candidate_id, symbol, contract_code, direction, setup_type
daily_state, medium_state, medium_state_age
impulse_start_time, impulse_end_time, impulse_atr
pullback_start_time, pullback_bars, pullback_depth_atr
retracement_ratio, opposite_efficiency, pullback_volume_ratio
signal_body_atr, signal_close_location, signal_volume_ratio
nearest_obstacle, obstacle_space_r
feature_asof, known_at, signal_time, decision_asof
order_active_at, expires_at
entry_trigger, structural_stop, risk_per_lot, planned_quantity
candidate_status, rejection_code, rejection_detail
```

### 20.2 交易字段

```text
entry_time, entry_price, entry_reference, entry_slippage
initial_quantity, initial_risk_cash, initial_risk_price
maximum_favorable_price, maximum_adverse_price, mfe_r, mae_r
profit_floor_price, trailing_stop, pending_exit_reason
exit_time, exit_price, exit_reason
gross_pnl, fees, slippage, net_pnl, net_r
holding_1m_bars, holding_5m_bars
entry_metadata_hash, exit_metadata_hash
```

### 20.3 标准产出

```text
summary.json
report.md
candidates.csv
rejections.csv
plans.csv
orders.csv
fills.csv
exit_legs.csv
trades.csv
daily_equity.csv
position_trace.csv
metadata_coverage.csv
metadata_gaps.csv
source_files.csv
performance_by_symbol.csv
performance_by_sector.csv
performance_by_direction.csv
performance_by_market_state.csv
```

报告必须展示完整漏斗：

```text
raw setups -> qualified pullbacks -> candidates -> eligible plans
-> active orders -> fills -> round trips
```

## 21. 单元测试设计

### 21.1 因果性

1. 对日线、30 分钟和 5 分钟特征执行前缀不变性测试。
2. 追加未来 K 线后，历史 `known_at` 事件流完全不变。
3. 日线与 30 分钟快照的 `bar_end` 不晚于信号时间。
4. 5 分钟信号完成前不能创建订单。
5. 订单激活事件严格晚于决策事件。
6. 摆动点图示时间可以早于确认时间，但交易只能使用确认时间。

### 21.2 多空镜像

对价格做 `p' = constant - p` 镜像后验证：

- 多头日线许可变为空头许可；
- 多头推动、回调、恢复信号变为空头对应事件；
- 止损距离、R、候选数量和拒绝原因保持镜像一致；
- 仅手续费方向差异可导致最终净值不同。

### 21.3 回调状态机

覆盖：

- 2 根与 12 根边界；
- 13 根失效；
- 20% 与 70% 回撤边界；
- 推动段被破坏；
- 强反向 K 线失效；
- 新推动出现后旧状态完全重置；
- 未触发订单不改变形态计数。

### 21.4 执行

覆盖：

- 触发价内成交与跳空穿越；
- 同根入场和止损的不利排序；
- 涨跌停锁板；
- 零成交量；
- 平今与普通平仓手续费；
- 夜盘交易日；
- 不同夜盘收盘时刻；
- 换月取消与强制退出；
- 止盈地板触发后下一分钟成交；
- 部分减仓后地板仍按初始仓位计算。

### 21.5 组合

覆盖：

- 同时多候选的稳定排序；
- 总风险、板块和保证金上限；
- 同一事件先退出释放资金，再处理入场；
- 一手风险超过预算时拒绝；
- 多个退出腿的手续费与现金逐腿更新；
- 结果不依赖品种输入顺序。

## 22. 回测验证方案

### 22.1 数据切分

建议采用滚动 walk-forward：

```text
训练：12 个月
验证：3 个月
锁定测试：3 个月
向前滚动：3 个月
```

训练与验证用于确定共享阈值，锁定测试只运行一次。相邻区间之间按最大特征窗口和最大订单/持仓标签周期
设置 purge 与 embargo，避免跨边界泄漏。

### 22.2 必做消融

1. 仅日线许可 vs 日线加 30 分钟状态。
2. 任意回调恢复 vs 深度与回撤比例过滤。
3. 不使用量比 vs 最低量比 vs 最低加最高量比。
4. 无障碍过滤 vs `1R/1.5R/2R` 空间。
5. 仅结构止损 vs 结构止损加止盈地板。
6. 双向、仅多、仅空，但参数保持一致。
7. 固定周期 vs 预先声明的替代周期，选择只能发生在锁定测试前。

### 22.3 压力测试

- 手续费与滑点：`1x / 1.5x / 2x / 3x`。
- 入场延迟：额外延迟 `1/2/3` 根 1 分钟线。
- 止损滑点：普通、跳空和封板后首次可成交三种情景。
- 参数邻域：所有核心阈值上下扰动 10% 至 20%。
- 留一品种、留一板块和去除最大盈利交易。
- 收益集中度、相关品种同时亏损和夜盘跳空压力。

### 22.4 评价指标

正式报告至少包含：

```text
net_profit, annual_return, max_drawdown
Sharpe, Calmar, profit_factor
win_rate, average_win_loss_ratio, expectancy_r
trade_count, turnover, fees, slippage
long/short expectancy
symbol/sector breadth
top_1/top_5 trade contribution
monthly and yearly consistency
```

不能以“所有品种盈利”为通过条件。更合理的晋级门槛是组合净期望为正、收益不过度集中、成本压力下仍有
余量，并且多折 walk-forward 方向一致。

## 23. 可能失效的市场环境

1. 日线和 30 分钟看似同向、实际已进入趋势末端时，回调入场可能成为最后一批接盘交易。
2. 宽幅震荡中的假趋势会反复形成推动与回调，恢复信号容易失败。
3. 政策、库存、天气和海外市场事件可能造成跨休市跳空，使实际损失超过计划 1R。
4. 涨跌停附近的信号可能无法入场或无法止损。
5. 主力迁移期间成交量变化可能伪装成趋势恢复放量。
6. 高频均值回归品种中，5 分钟恢复信号可能过迟，成本占潜在空间比例过高。
7. 多个产业链品种同时出现同向信号时，表面分散但实际风险高度相关。
8. 参数在单一行情阶段选择后，可能只适合当时的波动和趋势持续时间。

## 24. 建议实现边界

若进入开发阶段，建议新建独立目录，不修改现有策略默认行为：

```text
cta/config/multi_timeframe_pullback_config.py
cta/strategy/multi_timeframe_pullback/
    __init__.py
    daily_context.py
    medium_cycle.py
    impulse.py
    pullback_state.py
    candidates.py
    rules.py
cta/strategy/multi_timeframe_pullback_backtest/
    runner.py
    report.py
```

优先复用：

- `cta/strategy/common/` 的 K 线形态、时段相对位置和仓位原语；
- `multi_timeframe_trend_backtest` 的数据加载、实际合约元数据、组合回放、报表和图表能力；
- 共享 `BaseReplayConfig` 中与执行和组合风险有关的字段。

不得通过修改现有 `MultiTimeframeTrendConfig` 默认值来实现新策略，也不得放宽现有策略的风险校验。

## 25. 分阶段实施顺序

1. **时间与数据层**：完成 30 分钟聚合、SessionSpec 边界和高周期快照因果测试。
2. **日线与中周期状态**：实现双向许可、状态迟滞和前缀不变性测试。
3. **推动与回调状态机**：实现纯函数事件流和多空镜像测试。
4. **候选与订单计划**：实现恢复信号、止损、风险手数和拒绝码。
5. **1 分钟回放**：接入实际合约成交、涨跌停、手续费、换月和退出优先级。
6. **单品种冒烟**：选择数据与元数据完整的多头、空头各一个样本，只验证审计链。
7. **组合回测**：输出完整漏斗、分方向和分状态绩效。
8. **walk-forward**：冻结参数后运行锁定测试、成本压力和集中度检查。

任何阶段出现因果差异、元数据缺口或执行歧义，都应先修复该层，不带着未解释差异进入下一阶段。

## 26. 首版验收标准

### 26.1 工程验收

- 所有新代码只位于 `cta/**`。
- 不改变现有 `multi_timeframe_trend` 的候选、交易和默认配置。
- 日线、30 分钟、5 分钟特征通过前缀不变性测试。
- 多空镜像测试通过。
- 订单时间严格满足因果顺序。
- 实际合约、手续费、滑点、保证金、涨跌停和换月均可追溯。
- 每个候选都能从报告追溯到输入文件和配置版本。

### 26.2 研究晋级

首版不预设收益目标。只有同时满足以下证据，才进入下一轮参数研究：

1. 锁定样本外净期望为正，且不依赖单笔极端盈利。
2. `2x` 成本压力下结果没有结构性崩溃。
3. 多数 walk-forward 折的期望方向一致。
4. 收益覆盖多个品种或板块，而不是单一产业链贡献全部利润。
5. 参数邻域表现平滑，不只在一个精确阈值有效。
6. 多头和空头分别披露；任一方向不合格时可以关闭该方向，但必须保留失败记录。

## 27. 策略流程摘要

```text
读取最新已完成日线
    -> 计算双向日线许可
    -> 读取最新已完成 30 分钟状态
    -> 仅保留同向健康趋势
    -> 在 5 分钟上确认同向推动段
    -> 启动并维护回调状态
    -> 检查长度、深度、回撤比例和反向效率
    -> 已完成 5 分钟线确认趋势恢复
    -> 检查量比、障碍空间、元数据和组合风险
    -> 下一根 1 分钟激活实际合约停止单
    -> 按 1 分钟路径、涨跌停和不利滑点撮合
    -> 使用结构止损、止盈地板和时间止损管理
    -> 逐腿兑现现金、手续费和风险
    -> 输出候选、拒绝、订单、成交、交易和权益审计
```

该流程的核心不是“看到上涨就买、看到下跌就卖”，而是只在大周期许可、中周期趋势健康、
小周期回调受控且恢复信号已经完成后，承担一个预先量化、能够真实执行的结构风险。
