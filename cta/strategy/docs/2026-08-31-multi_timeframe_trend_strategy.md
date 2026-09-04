# 大周期定方向、小周期入场的双向趋势策略

对应实现：

- 配置：`cta/config/multi_timeframe_trend_config.py`
- 因果规则：`cta/strategy/multi_timeframe_trend_rules.py`
- 候选生成入口：`cta/strategy/multi_timeframe_trend_strategy.py`
- 成交后管理：`cta/strategy/multi_timeframe_trend_management.py`
- 单元测试：`cta/strategy/tests/test_multi_timeframe_trend_strategy.py`、`cta/strategy/tests/test_multi_timeframe_trend_management.py`

## 1. 文档定位

本文描述一套“日线确定方向、5 分钟 K 线寻找入场”的双向趋势规则。底层规则支持做多和做空，但当前默认候选黑名单会在候选 DataFrame 创建前删除全部 `pullback_breakout` 和空头机会，因此默认正式回测只保留多头 `always_in`。

本文既保留策略思想，也给出可直接编码和回测的因果规则。文中的参数均为首轮研究参数，不代表最优参数，也不构成收益、胜率或回撤承诺。

黑名单配置位于 `MultiTimeframeTrendConfig.candidate_setup_blacklist` 和 `candidate_direction_blacklist`。被删除机会不会获得 `candidate_id`，也不会进入候选、拒绝、订单、成交、交易或图表文件；需要恢复某类研究机会时直接编辑配置，不增加 CLI 参数。

## 2. 策略假设

1. 日线短、中、长期 EMA 呈严格多头或空头排列时，价格更可能沿该方向延续。
2. 顺应日线方向，在 5 分钟级别参与持续突破或回调后的再次突破，可以减少逆势交易。
3. 临近日线阻力追多或临近日线支撑追空，潜在空间不足，应该放弃入场。
4. 结构止损决定每手风险，仓位由账户允许承担的损失反推，而不是通过缩小止损强行增加手数。

## 3. 适用品种与周期

- 研究对象：中国期货市场中流动性充足、合约元数据完整的品种。
- 大周期：日线，用于方向过滤和阻力/支撑识别。
- 小周期：5 分钟 K 线，用于形态识别、入场和持仓管理。
- 信号研究可以使用连续合约，但成交、盈亏、涨跌停和换月必须映射到当时可交易的实际合约。
- 缺失历史乘数、最小变动价位、交易时段、手续费、涨跌停或主力合约映射时，相关区间不得产生正式回测交易。

## 4. 首轮研究参数

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `daily_ema_fast` | 5 | 日线快速 EMA 周期 |
| `daily_ema_mid` | 10 | 日线中速 EMA 周期 |
| `daily_ema_slow` | 20 | 日线慢速 EMA 周期 |
| `always_in_long_daily_ema_gap_min_ratio` | 2% | Always-In 多头日线 EMA5/EMA20 最小间距 |
| `first_trend_entry_daily_breakout_buffer_ratio` | 0.05% | 多头趋势段首笔开仓的前五日实体高点突破缓冲；`0` 表示关闭 |
| `daily_obstacle_lookback` | 20 | 日线近期高低点及摆动点回看窗口 |
| `daily_pivot_left/right` | 2/2 | 日线摆动点左右确认 K 线数 |
| `obstacle_buffer_atr` | 0.5 | 障碍过滤距离，单位为日线 ATR14 |
| `always_in_window` | 6 | 持续创新高/新低的 5 分钟观察窗口 |
| `always_in_min_progress` | 4 | 窗口内至少出现的同向推进次数 |
| `intraday_pivot_left/right` | 2/2 | 5 分钟摆动点左右确认 K 线数 |
| `trailing_buffer_atr` | 0.2 | 摆动点外侧的 ATR14 止损缓冲 |
| `pullback_min/max_bars` | 3/12 | 回调整理允许持续的 K 线数 |
| `volume_lookback` | 20 | 放量分位数计算窗口 |
| `volume_quantile` | 0.80 | 突破 K 线成交量阈值 |
| `entry_buffer_ticks` | 1 | 入场触发价越过结构的最小跳数 |
| `order_expiry_bars` | 3 | 入场停止单的有效 K 线数 |
| `candidate_setup_blacklist` | `("pullback_breakout",)` | 在候选创建前删除的形态类型 |
| `candidate_direction_blacklist` | `(-1,)` | 在候选创建前删除的方向，`-1` 表示空头 |
| `risk_per_trade` | 1% | 默认单笔账户权益风险 |
| `max_risk_per_trade` | 2% | 单笔账户权益风险硬上限 |
| `pullback_target_r` | 2.0 | 两种入场机会图的虚拟目标倍数，不参与平仓 |

参数应该在训练区间和验证区间内统一选择，再进入锁定的滚动样本外测试。不得根据单个品种的最终收益单独选择参数。

## 5. 因果时间规则

1. 日线方向和障碍位只能使用 5 分钟决策时刻之前已经完整收盘的日线。
2. 5 分钟指标、结构和成交量只能使用已经完整收盘的 5 分钟 K 线。
3. 信号在 5 分钟 K 线收盘后确认，订单最早从下一可交易事件开始生效，不允许按信号 K 线收盘价回填成交。
4. 左右各 2 根 K 线定义的摆动点，必须等右侧第 2 根 K 线收盘后才成为已知结构。图表可以标在摆动发生的位置，但交易信号时间不得回填。
5. 时间顺序必须满足：

```text
feature_asof <= known_at <= signal_time <= decision_asof
decision_asof < order_active_at <= fill_time
```

6. 交易日归属、夜盘边界和节假日按照对应交易所的有效交易日历处理，不使用自然日直接拼接日线。

## 6. 日线方向过滤

在每个 5 分钟决策时刻，只读取当前时刻之前已经完成的日线。先计算：

```text
prior_5d_high = 此前 5 根已完成日线开盘价和收盘价的最大值
prior_5d_low  = 此前 5 根已完成日线开盘价和收盘价的最小值
```

影线高低点和当前尚未完成的日线都不进入上述边界。不足 5 根已完成日线时保留候选，并记录 `DAILY_FIVE_BAR_HISTORY_UNAVAILABLE`。

### 6.1 多头许可

```text
EMA5 > EMA10 > EMA20
候选 trigger >= prior_5d_high
always_in LONG 额外要求：
(EMA5 - EMA20) / EMA20 >= always_in_long_daily_ema_gap_min_ratio
```

`always_in_long_daily_ema_gap_min_ratio` 默认值为 `0.02`，恰好等于阈值时放行。该条件只使用 5 分钟决策时刻之前已经完成并对齐的日线 EMA，只影响 `always_in LONG`，不影响 `pullback_breakout` 和空头。该参数只在 `MultiTimeframeTrendConfig` 中编辑，不新增 CLI 参数。

连续已完成日线满足 `EMA5 > EMA10 > EMA20` 时构成一个多头趋势段。对于 `direction > 0` 且当前 `daily_bull_trend_id` 尚无任何实际开仓成交的候选，额外严格要求：

```text
trigger > prior_5d_high * (1 + first_trend_entry_daily_breakout_buffer_ratio)
```

这里的 `trigger` 和 `prior_5d_high` 均为映射到实际合约后的价格。默认缓冲为 `0.0005`（0.05%）；等于缓冲阈值仍拒绝，并记录 `FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET`。参数设为 `0` 时完全关闭新增规则，恢复 `trigger == prior_5d_high` 可放行的原有行为。该参数仅通过 `MultiTimeframeTrendConfig` 配置，不增加 CLI 参数。

只有 `_open_position` 成功产生真实开仓 fill 后才记录趋势段已有成交；被过滤、未触发、过期、撮合取消或风控拒绝的候选均不改变状态。EMA 多头排列中断并重新形成后会得到新的 `daily_bull_trend_id`，重新应用首笔缓冲。同一时间戳先处理已有订单成交，再处理新候选，因此新候选能看到该时刻更早完成的开仓状态。

只允许寻找多头入场，禁止新开空仓。

### 6.2 空头许可

```text
EMA5 < EMA10 < EMA20
候选 trigger <= prior_5d_low
```

只允许寻找空头入场，禁止新开多仓。

### 6.3 中性状态

EMA 不满足上述任一严格排列时，不允许新开仓，并取消尚未触发的入场单。

如果持仓方向在新的日线收盘后失去许可，则在下一可交易的 5 分钟事件退出，不等待移动止损。

## 7. 日线阻力与支撑过滤

### 7.1 障碍候选

仅使用最近 `daily_obstacle_lookback=20` 根已完成日线，构造两类障碍：

1. 窗口最高价和最低价。
2. 已经确认的日线摆动高点和摆动低点。摆动点采用左右各 2 根 K 线确认，并记录 `pivot_time` 与 `known_at`。

拟做多时，只检查触发价上方的阻力；拟做空时，只检查触发价下方的支撑。两类障碍同时存在时，取距离拟入场触发价最近的一处。

### 7.2 拒绝规则

多头阻力距离：

```text
resistance_distance = nearest_resistance - long_trigger
```

当：

```text
0 <= resistance_distance <= 0.5 * daily_ATR14
```

拒绝多头入场。

空头支撑距离：

```text
support_distance = short_trigger - nearest_support
```

当：

```text
0 <= support_distance <= 0.5 * daily_ATR14
```

拒绝空头入场。

如果触发价已经越过所有有效障碍，则该障碍不再阻止入场。日线 ATR14 尚未完成预热时，不产生交易候选。

## 8. 入场一：Always-In 趋势延续突破

该规则用于 5 分钟价格沿日线方向持续推进、不断形成新高或新低的场景。

### 8.1 多头候选

必须同时满足：

1. 日线处于多头许可状态。
2. 最近 6 根已完成 5 分钟 K 线的 5 次相邻比较中，至少 4 次满足后一个最高价高于前一个最高价。
3. 第 6 根 K 线收盘价高于第 1 根 K 线收盘价。
4. 价格未跌破最近一个已确认的 5 分钟摆动低点。
5. 已存在可用于初始止损的已确认摆动低点。
6. 通过日线阻力过滤和风险过滤。

多头触发价：

```text
long_trigger = max(high of last 6 completed bars) + 1 tick
```

信号确认后，从下一可交易事件开始挂买入停止单。订单在 3 根 5 分钟 K 线内未触发则失效。

### 8.2 空头候选

规则与多头完全镜像：

1. 日线处于空头许可状态。
2. 最近 6 根 K 线的 5 次相邻比较中，至少 4 次形成更低的最低价。
3. 第 6 根 K 线收盘价低于第 1 根 K 线收盘价。
4. 价格未突破最近一个已确认的 5 分钟摆动高点。
5. 已存在可用于初始止损的已确认摆动高点。
6. 通过日线支撑过滤和风险过滤。

空头触发价：

```text
short_trigger = min(low of last 6 completed bars) - 1 tick
```

信号确认后，从下一可交易事件开始挂卖出停止单，3 根 K 线内未触发则失效。

### 8.3 初始止损与动态止损

多头初始止损：

```text
initial_stop = latest_confirmed_5m_swing_low - 0.2 * 5m_ATR14
```

空头初始止损：

```text
initial_stop = latest_confirmed_5m_swing_high + 0.2 * 5m_ATR14
```

止损价必须按合约最小变动价位向远离持仓的方向取整。止损必须位于入场价的亏损侧，否则拒绝交易。

持仓后，每当新的 5 分钟摆动点完成确认：

```text
long_stop[t] = max(long_stop[t-1], new_swing_low - 0.2 * ATR14)
short_stop[t] = min(short_stop[t-1], new_swing_high + 0.2 * ATR14)
```

移动止损只能收紧，不能放宽。新的止损在摆动点 `known_at` 之后生效，不得回填到摆动点发生时刻。该入场模式不设置固定止盈，依靠移动止损退出。

## 9. 入场二：回调后的放量突破

该规则用于日线趋势方向明确、5 分钟价格先回调整理、随后恢复原方向的场景。

### 9.1 回调整理状态

- 多头趋势中，第一根收盘价低于前一根收盘价的 K 线启动多头回调状态。
- 空头趋势中，第一根收盘价高于前一根收盘价的 K 线启动空头回调状态。
- 从回调启动 K 线开始累计区间，区间长度必须为 3 至 12 根已完成 K 线。
- 超过 12 根仍未突破，候选失效并重置。

突破前区间边界为：

```text
range_high = max(high of pullback bars)
range_low = min(low of pullback bars)
```

计算边界时不包含当前突破 K 线。

### 9.2 放量定义

突破 K 线成交量必须满足：

```text
breakout_volume > quantile_80(volume of previous 20 completed 5m bars)
```

分位数窗口不包含突破 K 线自身。ATR14 或成交量窗口未完成预热时，不产生信号。

### 9.3 多头入场

必须同时满足：

1. 日线处于多头许可状态。
2. 当前已完成 5 分钟 K 线的收盘价高于 `range_high`。
3. 突破 K 线满足 80% 成交量分位数条件。
4. 通过日线阻力过滤和风险过滤。

放量只能在突破 K 线收盘后确认，因此不能假设在该 K 线内部提前成交。确认后设置：

```text
entry_trigger = breakout_bar_high + 1 tick
initial_stop = range_low - 1 tick
```

买入停止单从下一可交易事件开始生效，3 根 K 线内未触发则失效。入场前价格先触及 `range_low` 时立即取消订单。

### 9.4 空头入场

规则与多头镜像：

1. 日线处于空头许可状态。
2. 当前已完成 5 分钟 K 线的收盘价低于 `range_low`。
3. 突破 K 线满足 80% 成交量分位数条件。
4. 通过日线支撑过滤和风险过滤。

确认后设置：

```text
entry_trigger = breakout_bar_low - 1 tick
initial_stop = range_high + 1 tick
```

卖出停止单从下一可交易事件开始生效，3 根 K 线内未触发则失效。入场前价格先触及 `range_high` 时立即取消订单。

### 9.5 跟踪止损与虚拟 2R 参考线

以实际成交价和结构止损计算初始风险 `R`：

```text
long_R = actual_entry_price - initial_stop
short_R = initial_stop - actual_entry_price

long_target_virtual = actual_entry_price + 2 * long_R
short_target_virtual = actual_entry_price - 2 * short_R
```

回调突破与 Always-In 都只使用已确认的 5 分钟摆动点跟踪止损，止损只能向有利方向移动。2R 价位不是可执行止盈单，行情到达该价位不会平仓；`final_target` 按实际成交价计算并只保留为交易审计字段，未成交机会按触发价计算 `target_price_virtual`，交易表固定记录 `target_exit_enabled=0`。

图表展示使用另一套明确语义：成交机会的 Target 等于 `trades.csv.exit_price`；若存在多个退出腿，该值为所有退出腿按手数加权的最终退出价。未成交机会没有退出价，Target 继续使用 `target_price_virtual`。图表不会使用 `final_target` 覆盖成交机会。

持仓只会因移动止损、日线方向失效、组合隔夜减仓、合约处理或回测区间结束等可执行事件退出，不使用虚拟 Target 触发平仓。

## 10. 信号冲突与持仓约束

1. 同一合约同一时间最多保留一个方向的一笔待成交订单或持仓，默认不加仓、不金字塔加码。
2. 只在两个形态都未被黑名单删除时，同一时刻的回调突破才优先于 Always-In；默认回调突破已被删除，不会令同刻多头 Always-In 产生重复信号拒绝。
3. 已持有多仓时不接受新的多头候选；空头规则同理。
4. 出现相反日线方向时，先取消待成交订单并退出原持仓，不在同一事件立即反手。

## 11. 仓位与单笔风险

默认每笔风险为账户权益的 1%，可以在 1% 至 2% 之间配置，2% 为硬上限。

```text
risk_budget = available_equity * risk_per_trade

estimated_loss_per_lot =
    abs(estimated_entry_price - structural_stop_price) * contract_multiplier
    + stressed_round_trip_cost_per_lot

quantity = floor(risk_budget / estimated_loss_per_lot)
```

其中，`stressed_round_trip_cost_per_lot` 至少包含开平手续费和双边压力滑点。

仓位规则：

1. `quantity < 1` 时拒绝交易，不能缩小结构止损以容纳一手。
2. 实际下单数量还必须满足交易所、经纪商、保证金和组合风险限制。
3. 停板、跳空或流动性不足可能导致实际损失超过计划的 1% 至 2%，风险比例不是损失上限保证。
4. 多品种共享一个账户按分钟顺序回放，不允许各品种重复使用同一份资金。

### 11.1 组合保证金限制

保证金占用按最新可见价格、合约乘数和当日多空方向保证金率计算。默认限制为：

1. 日内组合总保证金不超过盯市净值的 60%。
2. 夜盘和预计隔夜持仓总保证金不超过盯市净值的 20%。
3. 单个品种保证金不超过盯市净值的 40%。
4. 同一时间最多持有 5 个品种，可由 `--max-concurrent-positions` 调整。

成交前先按单笔风险计算手数，再依次按单品种和组合剩余保证金向下裁剪。裁剪后不足一手时记录明确拒绝原因，不调整结构止损。

每个品种在其历史日盘收盘前 10 分钟停止新开仓。若组合预计隔夜保证金超过 20%，从当前可交易的全组合持仓中按最新开仓优先、品种代码稳定排序，按下式计算恢复限额所需平掉的最少整数手数：

```text
required_lots = ceil(
    (portfolio_margin - equity * overnight_margin_utilization)
    / margin_per_lot
)
```

减仓决策使用已完成分钟，退出不回填到决策分钟，而是在下一可交易分钟开盘成交；其余手数继续保留过夜。涨跌停锁板时保留原计划手数并逐分钟重试，不重复累加。若执行分钟同时触及保护性止损，止损优先并退出全部剩余手数。每个退出腿立即按实际手数兑现盈亏和手续费，并写入 `exit_legs.csv`；同一候选完全平仓后才在 `trades.csv` 生成一条汇总交易。

### 11.2 连续亏损与组合回撤动态仓位

以下参数只在 `MultiTimeframeTrendConfig` 中编辑，不增加 CLI 参数：

```text
symbol_loss_streak = 2
symbol_position_scale = 1.0
portfolio_drawdown_threshold = 0.01
portfolio_position_scale = 1.0
```

品种正常状态连续两笔 `net_pnl < 0` 后激活半仓，初始恢复缺口为两笔净亏损绝对值之和；正常状态的 `net_pnl >= 0` 会打断连续亏损。激活后，该品种每笔已平仓交易执行 `deficit = deficit - net_pnl`，后续亏损扩大缺口，盈利缩小缺口，只有 `deficit < 0` 才恢复正常仓位。

组合状态只使用扣费后的已实现现金权益。高水位从初始权益开始，回撤严格大于 1% 时激活半仓并冻结高水位；激活后每个实际退出腿都立即用其真实净盈亏更新恢复缺口，只有现金权益严格超过冻结高水位才恢复。品种连续盈亏状态仍只在一笔逻辑交易完全平仓时更新，部分减仓不会被误计为多笔交易。

入场时先按实际成交价和结构止损计算 `base_quantity`，再执行：

```text
scaled_quantity = floor(base_quantity * symbol_factor * portfolio_factor)
```

品种和组合状态独立叠加，当前配置默认值均为 1.0，因此触发状态只记录审计事件而不缩小仓位；如后续把两项都改为 0.5，同时生效时为 25%。动态缩放后再应用单品种保证金、组合保证金和最大持仓限制；不足一手时记录 `DYNAMIC_RISK_SCALE_BELOW_ONE_LOT`。同一时间戳必须先处理全部平仓并更新状态，再处理新入场。

### 11.3 品种连续亏损冷却

冷却仅通过 `MultiTimeframeTrendConfig` 配置，不增加 CLI 参数：`symbol_loss_cooldown_enabled = True`、`symbol_loss_pair_window_hours = 48`、`symbol_loss_cooldown_hours = 24`。同一 `symbol` 相邻两笔最终平仓的逻辑交易，若真实扣费后的 `net_pnl < 0`，且两次平仓时间间隔不超过自然 48 小时，则在第二笔亏损时触发；任一最终平仓交易 `net_pnl >= 0` 都会打断连续亏损。部分隔夜减仓腿不计入，只有完整逻辑交易最终平仓才参与统计。

冷却从第二笔亏损的 `exit` 起计算自然 24 小时，随后对齐到不早于该时点的下一个上海日盘 `09:00`；休市日由数据自然跳过，时间恰好等于 `release_time` 时允许开仓。冷却只拒绝新开仓，不影响已有持仓退出，且适用于所有方向语义；拒绝记录使用 `SYMBOL_LOSS_COOLDOWN`，detail 记录第二笔亏损平仓时间和 `release_at`。同一时间戳必须先处理退出并更新品种冷却，再检查候选。

## 12. 订单与成交假设

1. 突破入场使用停止单、停止限价单或网关可实现的等价订单；必须按历史时点确认订单能力。
2. 买入停止价向上取整，卖出停止价向下取整；保护性卖出止损向下取整，保护性买入止损向上取整。
3. 价格跳过触发价时，按下一可成交价格并计入压力滑点，不按理想触发价成交。
4. 涨跌停封单、零成交量或订单能力缺失时，不假设成交。
5. 手续费、平今费用、乘数、最小变动价位、保证金和涨跌停必须从带生效日期的合约元数据读取，不使用未知品种默认值。
6. 合约进入交割限制或换月窗口时，按预先声明的规则取消订单并退出或迁移持仓，不允许连续合约直接成交。

手续费按实际成交类型拆分：开仓使用入场日开仓费率，退出使用退出日费率；入场与退出属于同一交易日时使用平今费，否则使用普通平仓费。成交金额类费率按实际成交价乘合约乘数计算，不能继续用入场时预估的一次往返费用代替正式扣费。`fee_audit.csv` 直接由退出腿生成，并记录入场和退出的费率来源、生效时间、可知时间、费率表编号、元数据哈希和合约乘数。

## 13. 候选审计与拒绝原因

每个候选至少记录：

```text
symbol, contract, direction, setup_type
feature_asof, pivot_time, known_at, signal_time
decision_asof, order_active_at, fill_time
daily_ema5, daily_ema10, daily_ema20, daily_atr14
prior_5d_high, prior_5d_low
nearest_obstacle, obstacle_distance_atr
trigger_price, structural_stop, risk_per_lot, quantity
candidate_status, rejection_code
```

完整交易额外记录 `is_first_trade_in_trend_segment` 和 `trigger_to_prior_5d_high_ratio`：前者标记该笔真实开仓是否为当前多头趋势段首笔成交，后者记录实际合约触发价相对前五日实体高点的百分比距离。两项仅用于审计，不使用实际成交价反向决定候选过滤结果。

建议使用以下拒绝码：

| 拒绝码 | 含义 |
|---|---|
| `DAILY_TREND_NEUTRAL` | 日线 EMA 未形成严格排列 |
| `DAILY_EMA_GAP_BELOW_MIN` | Always-In 多头日线 EMA5/EMA20 间距低于配置下限 |
| `DAILY_FIVE_BAR_HISTORY_UNAVAILABLE` | 信号前不足 5 根已完成日线 |
| `DAILY_FIVE_BAR_BREAKOUT_NOT_MET` | 候选触发价未突破此前五日日线实体边界 |
| `FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET` | 多头趋势段尚无开仓成交且候选未严格超过配置后的前五日实体高点 |
| `DIRECTION_MISMATCH` | 5 分钟候选方向与日线方向不一致 |
| `HTF_OBSTACLE_NEAR` | 临近日线阻力或支撑 |
| `ALWAYS_IN_STRUCTURE_FAIL` | 持续创新高/新低条件不足 |
| `NO_CONFIRMED_SWING` | 缺少可用的已确认摆动止损 |
| `PULLBACK_LENGTH_INVALID` | 回调整理不足 3 根或超过 12 根 |
| `VOLUME_NOT_EXPANDED` | 突破量未超过 80% 分位数 |
| `INVALID_STRUCTURAL_STOP` | 止损位不在入场价亏损侧 |
| `RISK_BELOW_ONE_LOT` | 一手风险已经超过预算 |
| `DUPLICATE_SIGNAL` | 已有同方向订单、持仓或更高优先级候选 |
| `ORDER_EXPIRED` | 入场单超过 3 根 K 线未触发 |
| `METADATA_MISSING` | 历史合约或交易机制元数据不完整 |
| `LIMIT_OR_LIQUIDITY_BLOCKED` | 涨跌停或流动性条件不支持成交 |
| `MAX_CONCURRENT_POSITIONS` | 已达到组合最大同时持仓品种数 |
| `SYMBOL_MARGIN_LIMIT` | 单品种 40% 保证金限额不足一手 |
| `PORTFOLIO_MARGIN_LIMIT` | 日内 60% 或隔夜 20% 组合限额不足一手 |
| `DYNAMIC_RISK_SCALE_BELOW_ONE_LOT` | 品种和组合动态系数缩放后不足一手 |
| `SYMBOL_LOSS_COOLDOWN` | 品种连续两笔亏损触发的冷却期内，不允许新开仓 |
| `OVERNIGHT_ENTRY_WINDOW` | 已进入日盘收盘前 10 分钟降仓窗口 |
| `OVERNIGHT_MARGIN_LIMIT_BREACH` | 降仓决策至实际成交之间仍超过隔夜保证金限额 |
| `OVERNIGHT_REDUCTION_LIMIT_LOCKED` | 降仓单遇到涨跌停锁板，等待下一可交易分钟重试 |

## 14. 可能失效的市场环境

1. 日线 EMA 频繁缠绕的震荡市，趋势许可反复切换，5 分钟假突破增多。
2. 趋势已经进入末端但 EMA 仍保持排列，策略可能在衰竭阶段追高或追空。
3. 突发政策、宏观数据或隔夜事件造成跳空，实际止损损失可能显著高于预算。
4. 低流动性、换月或临近交割阶段的成交量变化可能被误判为有效放量。
5. 直接使用最近 20 根 5 分钟成交量会受到日内时段季节性影响，应在后续消融中与“相同时段成交量分位数”方案比较。
6. 回调区间过宽时，虽然风险仓位会自动下降，但跟踪止损距离可能较大，持仓时间和利润回吐可能增加。

## 15. 回测与验证要求

1. 对日线聚合、夜盘交易日、EMA、ATR、成交量分位数和摆动点确认分别做前缀不变性测试。
2. 检查任一历史截断点之前的信号不因追加未来数据而变化。
3. 对实际启用的形态、方向、品种和板块分别报告候选数、拒绝数、订单数、成交数和完整交易数；默认候选集只有多头 Always-In。
4. 回测必须计入实际合约手续费、平今费用、滑点、涨跌停、换月和无法成交情况。
5. 使用滚动 walk-forward：训练和验证区间用于选择共享参数，锁定测试区间只用于最终评估。
6. 至少进行 1 倍、1.5 倍、2 倍和 3 倍成本压力测试，以及延迟一根 K 线入场测试。
7. 对关键参数做邻域稳定性测试，避免只在单一数值或单一品种上有效。
8. 正式评价关注扣费后的组合期望、最大回撤、品种和板块覆盖度、收益集中度及参数稳定性，不要求每个品种都盈利。

## 16. 策略流程

```text
读取最近已完成日线
    -> 判断 EMA 排列和此前五日日线实体突破许可
    -> 更新已确认的日线阻力/支撑
    -> 读取已完成的 5 分钟 K 线
    -> 识别 Always-In 或回调突破形态
    -> 按配置彻底删除 pullback_breakout 和空头候选
    -> 检查方向、障碍、元数据、风险和品种冷却
    -> 下一可交易事件激活停止单
    -> 实际合约成交
    -> 两种入场均推进已确认摆动止损，虚拟 2R 只作未成交机会 Target 和交易审计
    -> 每个退出腿更新现金和组合动态仓位，完全平仓后更新品种状态和品种冷却
    -> 记录候选、拒绝、订单、成交、退出腿和仓位缩放审计字段
```

## 17. 白银 AG 一键回测命令

策略周期固定为“日线定方向、5 分钟生成入场计划、1 分钟模拟实际成交”，不通过命令行改变周期：

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --symbols AG \
  --download-minute-data \
  --start 2026-01-01 \
  --end 2026-02-05 \
  --initial-equity 1000000
```

默认单笔风险为账户权益的 1%。如需研究 2% 风险，可以增加 `--risk-per-trade 0.02`，但不同风险参数的结果必须分目录比较，不能混合权益曲线。

按外部排名选择前 N 个品种时可以省略 `--symbols`，不会隐式加入 AG：

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --download-minute-data \
  --top-n 20 \
  --start 2025-01-01 \
  --end 2026-06-30 \
  --initial-equity 1000000
```

下载器接受任意 `end >= start` 的日期区间，并严格只下载 CLI 的 `start..end`。更早的策略预热行情只从本地可用分钟文件读取，特征和元数据继续使用既有可用性与阻断规则；下载器不会扩大请求范围或伪造行情。

每次运行会在 `cta/strategy/report/multi_timeframe_trend/` 下创建不可覆盖的新目录，主要文件包括：

- `report.md`：中文回测结果，包含收益、最大回撤、胜率、利润因子、平均盈亏比等。
- `summary.json`：机器可读的正式状态、绩效、漏斗、元数据审计和复现命令。
- `RUN_COMMAND.sh`：本次运行的完整复现命令。
- `candidates.csv`、`plans.csv`、`orders.csv`、`fills.csv`、`exit_legs.csv`、`trades.csv`：从机会到分腿退出及完整交易的审计链。
- `rejections.csv`、`fee_audit.csv`：拒绝原因、组合风控事件和逐退出腿手续费溯源。
- `position_scaling_events.csv`：品种和组合动态仓位的触发、扩缺口、恢复进度和恢复事件。
- `daily_equity.csv`、`performance_by_group.csv`：逐日权益和按策略、方向等维度拆分的绩效。
- `metadata_gaps.csv`、`metadata_coverage.csv`、`source_files.csv`：历史机制覆盖和数据来源。
- `opportunity_charts/index.csv`：每次机会与图片路径的索引。
- `opportunity_charts/TRADED/*.png`：最终成交机会的日线、1 小时、5 分钟三周期图。
- `opportunity_charts/<原因码>/*.png`：每个未成交原因一个目录，文件名按全局时间顺序编号并包含品种名称。

三周期机会图都把 Signal 固定在横轴中央；回测边界缺失的数据保留为空槽。日线、1 小时和 5 分钟面板均显示同一组 Entry、Stop 和 Target。成交机会的 Target 是最终加权 `exit_price`；未成交机会的 Target 是虚拟 2R `target_price_virtual`，即使尚未被行情触及也纳入纵轴。Target 线只用于复盘，不参与回测平仓。

若历史手续费、合约乘数、最小变动价位、交易状态、涨跌停或实际合约映射不完整，目录仍会保留审计结果，但正式状态为 `BLOCKED_METADATA`，`official_performance` 和正式权益曲线为空。
