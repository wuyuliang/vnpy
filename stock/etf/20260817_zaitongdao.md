# 周线窄通道多空交易策略设计（159915.SZ 示例，可扩展 CTA 品种）

## 0. 文档状态

- 文件名和后续运行编号按用户指定使用 `20260817_zaitongdao`。
- 本设计实际编写基准日为 `2026-08-07`。
- 当前状态：**待评审，禁止开始代码实现**。
- 本阶段只定义策略、数据口径、成交假设、输出和验收标准，不新增或修改 Python 代码。
- 本次修订把原“单一 ETF、只做多”方案升级为“统一信号内核、按品种能力执行多空”；
  `159915.SZ` 仍是第一版完整示例，不代表规则只能用于 ETF。
- 文中的 `AI Brooks` 按 `Al Brooks` 价格行为体系理解。本方案是可量化的
  `Brooks-inspired` 规则，不声称完整复刻人工逐根 K 线判断。

## 1. 目标

以 `159915.SZ` 易方达创业板 ETF 为首个可复核样例，建立不依赖具体 symbol 的策略内核：

1. 只使用已经完成的周 K，识别周维度的上升窄通道和下降窄通道。
2. 顺势交易同时覆盖上升通道回调做多和下降通道反弹做空。
3. 反转交易同时覆盖下降通道确认反转做多和上升通道确认反转做空；不猜第一段反转。
4. 每次开多或开空时同步确定可执行的初始止损、数量和最坏计划风险。
5. 退出不设置固定止盈，使用方向对称的结构失败、强反向周、日线失效和慢速跟踪止损，
   目标是尽量持有大趋势的主要阶段。
6. 后续回测必须输出周线通道、四类日线信号、止损轨迹、开平仓记录、净值和复盘 PNG。
7. 同一内核可以扩展到中国 CTA 期货品种，但成交、成本、保证金、夜盘和换月必须由品种
   adapter 处理，不能把 ETF 参数直接套到期货。

策略层允许 `LONG` 和 `SHORT`。执行层采用失败关闭：品种配置或逐日能力数据不允许某一
方向时，仍可输出 `research_only` 信号，但不得生成该方向的成交、持仓或可执行业绩。

## 2. 当前数据审计

### 2.1 默认冻结输入

第一版冻结以下现有可审计输出：

```text
stock/etf/output/20260727_chuangyeban_regime_overlay/signals.csv
stock/etf/output/20260727_chuangyeban_regime_overlay/source_audit.json
```

只使用其中以下原始行情列，不读取已有 EMA、状态预测或交易信号列：

```text
symbol, datetime, open, high, low, close, volume
```

当前文件中 `159915.SZ` 有 `2,166` 根日 K，范围是 `2017-08-14` 至
`2026-07-20`。对应 `source_audit.json` 记录价格为 Tushare
`point_in_time_adjusted` 口径。

冻结文件校验值：

```text
signals.csv      sha256=ad3625be0d7b3b979e5275f9e930313c511564e42038be08638d74265bddad2a
source_audit.json sha256=c2efd094bc4a01c8ceecc282b076e4797aab18b5dd741117ef6f2ad22bd69b0a
```

`source_audit.json` 只保留了交易日历摘要和上游哈希，没有保留日历逐日记录。评审通过后的
数据准备步骤必须把同范围 SZSE `trade_cal` 持久化到
`stock/etf/data/20260817_zaitongdao/trade_calendar.csv`；严格回测缺少该文件时失败关闭，
不能仅凭 ETF 恰好有行情的日期推断完整交易周。

冻结 `signals.csv` 只有复权 OHLCV，没有同日未复权 OHLC 和逐日复权因子，因此它只能
生成 `signal_research`，不能单独生成可执行限价、止损、手续费或 PnL。评审通过后的数据
准备必须另外冻结 `raw_daily.csv` 与 `adjust_factors.csv`，并证明复权信号价可以按当日已知
因子映射回未复权成交价；缺任一文件时整个 `159915.SZ` 结果保持 `research_only`。

本节冻结值仅用于 `159915.SZ` 首个样例的可复现审计。增加其他 ETF 或 CTA 品种时，每个
symbol 必须生成自己的输入清单、数据范围、哈希和交易日历审计，不能复用上述哈希。

### 2.2 数据限制

- 当前日期是 `2026-08-07`，但本地最新行情只到 `2026-07-20`，不能把结果描述为截至
  `2026-08-07` 的最新判断。
- `2026-07-20` 是周一，因此该行所在的 `W-FRI=2026-07-24` 周 K 是不完整周，必须排除。
- 当前最后一个可用于通道判断的完整周结束于 `2026-07-17`。
- 后续若要运行真正的最新结果，必须先显式刷新日线，并在输出摘要中同时记录请求结束日、
  实际最后行情日和最后完整周。
- 通道和 EMA 可以使用 point-in-time 复权价格；限价、tick 舍入、涨跌停、止损、成交额、
  费用和 PnL 必须使用同日未复权价格。复权映射缺失时不得生成“可执行历史回测”，更不能把
  复权价发送到交易接口。

### 2.3 跨品种数据与能力契约

跨品种输入不是一张连续序列表，而是三组可对账数据。

信号 K 线最少包含：

```text
signal_symbol, exchange, asset_type, trading_day, interval,
open, high, low, close, volume, open_interest, asof_version
```

逐日真实成交 K 线最少包含：

```text
execution_symbol, contract, trading_day, interval,
open, high, low, close, settlement, pre_settlement,
volume, open_interest, upper_limit, lower_limit,
is_tradable, session_id
```

- ETF 的 `contract` 等于证券代码，`settlement/pre_settlement/open_interest` 可以为空，但
  必须另外提供 `raw_close/adjust_factor` 映射和份额可用日。
- CTA 必须保留同一交易日全部候选真实合约，而不只是当日主力一根 K；旧、新合约的原始
  OHLC、结算价、OI、涨跌停和可交易状态必须足以复算换月两腿。

主力/执行映射最少包含：

```text
decision_asof, effective_trading_day, signal_symbol,
from_contract, to_contract, active_contract,
selection_rule, selection_inputs,
price_transform_scale, price_transform_shift
```

`price_transform_scale/shift` 分别对应第 4.2 节的 `a_T/b_T`；必须能分别复算价格水平与
价格距离，不接受只有一个不透明 `price_transform` 字符串的映射。

`open_interest` 对 ETF 可以为空，对 CTA 主力选择和换月审计必须可用。每个 symbol 还必须
有一份 point-in-time 品种配置：

```text
name, asset_type, exchange, can_long, can_short, short_mode,
same_day_close_allowed, price_tick, volume_step,
contract_multiplier, margin_rate, maintenance_margin_rule,
commission_model, slippage_model, calendar_id, session_id,
signal_series, execution_series, price_transform_method,
roll_rule, deterministic_pre_roll_days, post_roll_cooldown_days
```

- `short_mode` 只能是 `executable`、`research_only` 或 `disabled`。
- `159915.SZ` 的多头可按普通 ETF 现货成交；空头必须同时通过当日交易所标的资格、账户
  融券权限、券商可借券数量、借券期限和费率检查。任一项缺失时只能生成
  `research_only` 空头信号，不能假设历史上始终有券。
- `159915.SZ` 的 `same_day_close_allowed=false`。其他 ETF 是否支持日内回转必须按基金类型
  和当日有效规则配置，不能由代码或名称猜测。
- CTA 期货通常允许双向开仓，但只有真实合约、合约乘数、最小价位、保证金、手续费、
  涨跌停和交易时段在该交易日均有效时，才可把信号标为 `executable`。
- 配置必须带 `effective_from/effective_to` 或数据日期，禁止用当前保证金、费率或融券资格
  回填全部历史。

### 2.4 初步规则体检

使用第 5 节的周线默认阈值进行一次只读探索，并排除末尾不完整周后，当前数据形成：

- `456` 根完整周 K。
- `108` 个满足单周滚动窗口规则的标签，其中上升 `61` 个、下降 `47` 个。
- 严格要求相邻两个完整周窗口连续满足，并按第 5.4 节状态机只容许一个 grace 周后，形成
  `24` 段确认通道，其中上升 `11` 段、下降 `13` 段。

最近可作为人工复核锚点的候选区间包括：

| 方向 | 确认周窗口范围 | 连续合格窗口数 | 备注 |
| --- | --- | ---: | --- |
| 上升 | `2025-07-04` 至 `2025-08-08` | 6 | 明显连续上行阶段 |
| 上升 | `2025-09-05` 至 `2025-10-03` | 5 | 强上升微通道阶段 |
| 上升 | `2026-05-15` 至 `2026-06-05` | 4 | 随后出现较深回撤 |
| 下降 | `2025-04-11` 至 `2025-04-25` | 3 | 下降阶段，禁止直接抄底 |

这些日期只是设计阶段的算法体检，不是正式交易信号或回测结论。正式实现必须通过测试和
完整输出重新生成，不得把本表硬编码到策略中。

## 3. 方案比较与选择

### 3.1 方案 A：纯 Brooks 形态计数

直接使用 higher low、lower high、High 1/High 2、Low 1/Low 2、趋势线突破等形态。

- 优点：最接近 Al Brooks 的人工读图语言。
- 缺点：摆点、包络 K 线和失败突破有多种合理解释，代码结果容易随细节变化。

### 3.2 方案 B：回归窄通道 + Brooks 入场语义

周线用方向一致性、线性拟合、ATR 归一化偏离和 EMA 方向确定窄通道；日线再用回调恢复
或反转跟随触发方向对称的开多、开空信号。

- 优点：规则唯一、可审计、跨价格区间可比较，仍保留“顺强趋势、首个反转通常较弱、
  等待跟随”的核心思想。
- 缺点：与人工画线不会完全一致，阈值必须预注册并做敏感性检查。

### 3.3 方案 C：机器学习分类窄通道

从历史图形或未来收益学习通道标签和买卖点。

- 优点：可以表达复杂非线性形态。
- 缺点：首个审计样例只有一只 ETF，样本太少，标签主观且极易过拟合，不利于解释止损和
  退出；增加 CTA 品种也不能用横截面数量掩盖时间样本不足。

**选择方案 B。** 第一版固定一组参数，不运行网格搜索，不以全样本收益反向调整规则。

## 4. 严格时序与无未来函数

### 4.1 周 K 聚合

逻辑周按品种交易日历分桶；`159915.SZ` 的标签等价于 `W-FRI`：

```text
weekly_open   = 当周第一个交易日 open
weekly_high   = 当周所有交易日 high 的最大值
weekly_low    = 当周所有交易日 low 的最小值
weekly_close  = 当周最后一个交易日 close
weekly_volume = 当周 volume 之和
```

周 K 完整性必须由对应交易日历判断，不提供无日历回退。ETF 使用交易所开市日和停牌标记；
CTA 先把夜盘和日盘归并到交易所定义的 `trading_day`，再按该品种本周最后一个计划交易日
判断完整性。周五休市时，以日历中的当周最后交易日为完成时点。缺日历、缺预期行情且无
可审计状态时失败关闭，不能用本地最后一根 K 线猜测“本周已结束”。

### 4.2 信号与成交时间

- 周线通道状态在完整周收盘后才可见。
- 日线入场信号在交易日 `T` 收盘后形成，最早在下一有效交易日 `T+1` 开盘成交。
- 日线收盘退出信号同样在 `T+1` 开盘执行。
- 盘中硬止损只依赖开仓前已确定、随后只向减小风险方向移动的止损价。
- CTA 的 `T+1 open` 是下一 `trading_day` 在输入日线定义中的首个可交易时段开盘；必须
  输出实际 `execution_session`，不能混淆自然日与交易日。
- 连续/主力序列可以计算通道和方向，但所有委托、止损和 PnL 必须落到当日真实合约原始
  价格；后复权或平滑后的连续价格禁止直接成交。
- 连续序列必须按 point-in-time 方式构造：日期 `T` 的特征只能使用 `T` 当时已经发生的
  换月和调整因子，未来换月不得回写并改变历史 EMA、ATR、通道或信号。
- 每个资产在 `T` 收盘必须冻结可逆、单调递增的仿射 `price_transform=(a_T,b_T)`，其中
  `a_T>0`。ETF 用它把复权信号价映射为当日未复权价，CTA 用它把连续信号价映射为当日
  `execution_contract` 原始价。价格水平和价格距离必须分开映射：

```text
raw_level    = a_T * signal_level + b_T
raw_distance = abs(a_T) * signal_distance
```

  OHLC、结构位等绝对价格使用 `raw_level`；ATR、回归带宽等距离只缩放、不叠加 `b_T`。
  `1.03/0.97`、止损占入场价比例等百分比规则必须先把基准价格映射到真实坐标，再在真实
  坐标计算，禁止在含加法平移的连续坐标中先乘百分比。限价、止损、tick 舍入、风险和 PnL
  均在真实坐标完成；映射缺失、使用未来调整因子、`a_T<=0` 或映射后 OHLC 关系非法时，
  本次交易失败关闭。
- 所有输出必须记录 `feature_asof_date`、`signal_date`、`execution_date`、
  `signal_symbol` 和 `execution_contract`。

## 5. 周线窄通道识别

### 5.1 指标定义

默认参数：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `channel_lookback_weeks` | 6 | 每个滚动通道窗口的完整周数 |
| `weekly_atr_period` | 20 | Wilder ATR 周期 |
| `weekly_ema_period` | 20 | 周线趋势基准 |
| `ema_slope_lookback_weeks` | 3 | EMA20 方向观察周期 |
| `min_r_squared` | 0.65 | 收盘价线性拟合最低决定系数 |
| `min_net_move_atr` | 1.25 | 6 周首尾最小净移动 |
| `max_fit_error_atr` | 0.75 | 单周收盘偏离回归线的最大值 |
| `min_direction_ratio` | 0.60 | 同方向收盘和同方向极值最低比例 |
| `max_counter_move_atr` | 1.00 | 窗口内最大反向回撤或反弹 |
| `confirmation_windows` | 2 | 确认通道所需连续合格窗口数 |

日线固定参数：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `daily_atr_period` | 14 | 日线 Wilder ATR 周期 |
| `daily_ema_periods` | `5/10/20` | 日线趋势和回调判断 |
| `tick_size` | 品种配置 | `159915.SZ` 示例为 `0.001`，CTA 不得硬编码 |
| `reversal_daily_trigger_expiry_days` | 10 | 周线跟随后等待日线反转触发的最长交易日数 |

周线真实波幅和 ATR 沿用项目已有 Wilder 定义：

```text
TR_t = max(high_t - low_t,
           abs(high_t - close_t-1),
           abs(low_t - close_t-1))
ATR20W = EWM(TR, alpha=1/20, adjust=False, min_periods=20)
EMA20W = EWM(close, span=20, adjust=False, min_periods=20)
```

对最近 6 个完整周收盘价，以 `x=0..5` 做一元线性回归，计算 `slope`、`R²`、拟合值和
最大残差。全部距离指标用当前 `ATR20W` 归一化。

日线 `ATR14D` 使用相同 Wilder 公式但周期为 14；`EMA5D/EMA10D/EMA20D` 使用
`EWM(close, span=N, adjust=False, min_periods=N)`。K 线公共字段定义为：

```text
bar_range = high - low
body = abs(close - open)
close_location = (close - low) / max(bar_range, tick_size)
weekly_body = abs(weekly_close - weekly_open)
weekly_close_location = (weekly_close - weekly_low) / max(weekly_high - weekly_low, tick_size)
```

### 5.2 上升窄通道

一个 6 周窗口必须同时满足：

```text
slope > 0
R² >= 0.65
(close_5 - close_0) / ATR20W >= 1.25
max(abs(close_i - fitted_close_i)) / ATR20W <= 0.75
count(close_i > close_i-1) / 5 >= 0.60
count(low_i >= low_i-1) / 5 >= 0.60
max(rolling_peak_close - close_i) / ATR20W <= 1.00
close_5 > EMA20W_5
EMA20W_5 > EMA20W_2
```

连续两个完整周的滚动窗口都满足条件时，状态从 `UP_CANDIDATE` 变为
`UP_CONFIRMED`。通道确认后，只允许在 `UP_CONFIRMED` 或紧随其后的一个
`UP_GRACE` 周寻找顺势多头机会；空头只允许走确认后的反转路径。

### 5.3 下降窄通道

规则完全对称：

```text
slope < 0
R² >= 0.65
(close_5 - close_0) / ATR20W <= -1.25
max(abs(close_i - fitted_close_i)) / ATR20W <= 0.75
count(close_i < close_i-1) / 5 >= 0.60
count(high_i <= high_i-1) / 5 >= 0.60
max(close_i - rolling_trough_close) / ATR20W <= 1.00
close_5 < EMA20W_5
EMA20W_5 < EMA20W_2
```

连续两个完整周窗口满足后进入 `DOWN_CONFIRMED`。该状态下寻找顺势空头机会；多头只允许
走确认后的反转路径。

### 5.4 状态失效

- `NEUTRAL` 遇到一个合格窗口进入对应 `CANDIDATE`；candidate 的下一个完整周仍同向
  合格才进入 `CONFIRMED`。candidate 下一周不合格时回到 `NEUTRAL`；若该周是反向合格
  窗口，则直接作为反向 candidate 的第一周。
- confirmed 后，同方向规则第一个不合格周进入 `UP_GRACE` 或 `DOWN_GRACE`，用于避免
  一次普通回撤让通道状态来回跳变。若该周同时满足反向窗口规则，则并行记录
  `opposite_candidate_direction` 和 `opposite_candidate_streak=1`，但尚不确认反向通道。
- grace 下一周恢复原方向合格时回到原 confirmed，并清空反向 candidate。若下一周再次
  反向合格且 `opposite_candidate_streak=1`，原 episode 当周结束并直接进入反向 confirmed；
  若下一周两个方向都不合格，原 episode 结束并回到 neutral；若此前没有反向 candidate
  而本周首次反向合格，则原 episode 结束，同时建立反向 candidate 第一周。
- 因此相反方向必须是两个相邻完整周窗口连续合格才确认，不会把 grace 前后的非连续窗口
  拼成确认通道。
- 上升通道出现第 9.3 节定义的强空头周，或下降通道出现对称的强多头周：不等待第二个
  失效窗口，立即结束原通道。
- episode 从第一个 candidate 窗口开始，confirmed 后才可交易；中间最多一个 grace 周仍
  属于同一 episode。`ENDED` 是一次审计事件，处理完该周后回到 `NEUTRAL` 或当周已经
  建立的反向 candidate，不作为可持续状态。
- `UP_GRACE` 仍允许类型 A 顺势做多，`DOWN_GRACE` 仍允许类型 B 顺势做空，但都必须通过
  全部日线恢复条件且本周没有强反向周。
- 在 `DOWN_CONFIRMED/DOWN_GRACE` 中先判断类型 C 的强多头反转突破；在
  `UP_CONFIRMED/UP_GRACE` 中先判断类型 D 的强空头反转突破。满足时清空并行 candidate，
  直接进入对应 reversal 状态；只有不满足强突破时才执行普通 candidate/grace 转移。

## 6. 开仓规则

四类 `signal_type` 固定如下：

| 类型 | `signal_type` | 方向 | 含义 |
| --- | --- | --- | --- |
| A | `up_channel_pullback_long` | `LONG` | 上升窄通道回调恢复后顺势做多 |
| B | `down_channel_rally_short` | `SHORT` | 下降窄通道反弹结束后顺势做空 |
| C | `down_channel_reversal_long` | `LONG` | 下降窄通道完成向上突破和跟随后做多 |
| D | `up_channel_reversal_short` | `SHORT` | 上升窄通道完成向下突破和跟随后做空 |

每个 symbol 最多同时持有一个方向。同一 `channel_episode + intended_side` 最多成交两次；
首次被初始硬止损打掉后，至少等待 5 个完整交易日和一个新信号才允许第二次尝试。趋势
失效、强反向周、时间退出或反转失败后，不在原 setup 重入。反转 setup 的优先级高于原
通道延续 setup，并立即撤销尚未成交的原通道延续订单。

### 6.1 类型 A：上升窄通道回调恢复做多

先决条件：最近一个完整周状态为 `UP_CONFIRMED` 或 `UP_GRACE`，且没有强空头周退出。
在日线 `T` 收盘后同时满足：

```text
EMA5D_T > EMA10D_T > EMA20D_T
T 及此前 4 个交易日至少有 2 个 close_j < close_j-1
T 及此前 4 日最低价 <= EMA10D_T + 0.25 * ATR14D_T
对 T 及此前 4 日的每个 j：close_j >= EMA20D_j - 0.50 * ATR14D_j
signal_bar_close > signal_bar_open
close_location >= 0.70
signal_bar_close > previous_day_high
```

该信号代表周线强趋势中的日线回调已经向上恢复，最早在 `T+1` 开多。

### 6.2 类型 B：下降窄通道反弹恢复做空

规则与类型 A 方向对称。先决条件：最近一个完整周状态为 `DOWN_CONFIRMED` 或
`DOWN_GRACE`，且没有强多头周退出。在日线 `T` 收盘后同时满足：

```text
EMA5D_T < EMA10D_T < EMA20D_T
T 及此前 4 个交易日至少有 2 个 close_j > close_j-1
T 及此前 4 日最高价 >= EMA10D_T - 0.25 * ATR14D_T
对 T 及此前 4 日的每个 j：close_j <= EMA20D_j + 0.50 * ATR14D_j
signal_bar_close < signal_bar_open
close_location <= 0.30
signal_bar_close < previous_day_low
```

该信号代表下降趋势中的反弹已经向下恢复，最早在 `T+1` 开空。

### 6.3 类型 C：下降窄通道反转确认做多

下降通道中的第一次向上反弹不直接做多，必须依次完成：

```text
breakout_week_midpoint = (breakout_week_high + breakout_week_low) / 2
```

1. 新完整周开始前状态为 `DOWN_CONFIRMED` 或第一个 `DOWN_GRACE` 周，并冻结最近一个
   合格下降窗口的 6 周回归参数和上轨。
2. 当前完整周满足强多头突破：

```text
previous_2_week_high = max(high_t-1, high_t-2)
weekly_close > previous_2_week_high
weekly_close > weekly_open
weekly_close_location >= 0.70
weekly_close - weekly_open >= 0.60 * ATR20W
weekly_close > regression_upper_line + 0.10 * ATR20W
```

`previous_2_week_high` 不包含突破周。`regression_upper_line` 使用冻结下降窗口计算：
`upper_offset=max(high_i-fitted_close_i)`，再按原 `slope/intercept` 外推到突破周，禁止用
突破周重新拟合。

3. 突破周以 `down_reversal_breakout` 结束下降 episode，进入
   `DOWN_REVERSAL_BREAKOUT`。
4. 下一完整周收盘不低于突破周中点且不低于突破周收盘，进入
   `DOWN_REVERSAL_FOLLOW_THROUGH`；否则 setup 失败。
5. 跟随确认后 10 个交易日内，日线首次同时满足：

```text
EMA5D > EMA10D
close > max(high_T-1, high_T-2, high_T-3)
close > open
close_location >= 0.70
close > breakout_week_midpoint
```

6. `T+1` 执行第 6.5 节多头开盘订单；10 个交易日内未触发则 setup 失效。

### 6.4 类型 D：上升窄通道反转确认做空

类型 D 与类型 C 方向对称，上升通道中的第一次下跌不直接做空：

1. 新完整周开始前状态为 `UP_CONFIRMED` 或第一个 `UP_GRACE` 周，并冻结最近一个合格
   上升窗口的 6 周回归参数和下轨。
2. 当前完整周满足强空头突破：

```text
previous_2_week_low = min(low_t-1, low_t-2)
weekly_close < previous_2_week_low
weekly_close < weekly_open
weekly_close_location <= 0.30
weekly_open - weekly_close >= 0.60 * ATR20W
weekly_close < regression_lower_line - 0.10 * ATR20W
```

`previous_2_week_low` 不包含突破周。`regression_lower_line` 使用冻结上升窗口计算：
`lower_offset=min(low_i-fitted_close_i)`，再按原 `slope/intercept` 外推到突破周，禁止用
突破周重新拟合。

3. 突破周以 `up_reversal_breakout` 结束上升 episode，进入 `UP_REVERSAL_BREAKOUT`。
4. 下一完整周收盘不高于突破周中点且不高于突破周收盘，进入
   `UP_REVERSAL_FOLLOW_THROUGH`；否则 setup 失败。
5. 跟随确认后 10 个交易日内，日线首次同时满足：

```text
EMA5D < EMA10D
close < min(low_T-1, low_T-2, low_T-3)
close < open
close_location <= 0.30
close < breakout_week_midpoint
```

6. `T+1` 执行第 6.5 节空头开盘订单；10 个交易日内未触发则 setup 失效。

类型 C/D 的 10 日窗口统一定义：跟随周完整收盘后的下一交易日是 `trigger_day=1`，第 10
个交易日包含在窗口内；跟随周最后一天本身不能作为日线触发。第 10 日收盘出现信号时，
允许在第 11 日开盘执行已经冻结的订单。等待期间先检查失效条件：类型 C 若任一日收盘低于
突破周低点或任一新完整周收盘低于突破周中点则取消；类型 D 若任一日收盘高于突破周高点
或任一新完整周收盘高于突破周中点则取消。同日同时失效和触发时失效优先，不生成订单。

### 6.5 次日开盘限价订单与追价过滤

在 `T` 收盘、尚不知道 `T+1` 开盘价时，先用第 4.2 节冻结的当日映射转换价格水平和距离，
再在真实成交坐标预先计算：

```text
signal_close_raw = raw_level(signal_close)
signal_high_raw  = raw_level(signal_high)
signal_low_raw   = raw_level(signal_low)
atr14d_raw       = raw_distance(ATR14D_T)

LONG:  max_entry_fill_raw = min(signal_close_raw + 0.75 * atr14d_raw,
                                signal_high_raw * 1.03)
SHORT: min_entry_fill_raw = max(signal_close_raw - 0.75 * atr14d_raw,
                                signal_low_raw * 0.97)
```

这里的 `1.03/0.97` 已作用于真实价格，不受连续合约加法平移影响。第 7 节的止损和数量全部
用该方向最不利允许原始价预先确定。回测统一使用“仅下一开盘有效”的 `LOO` 语义；实盘
adapter 必须转换成交易所实际支持的订单类型：

- 多头先基于真实合约开盘计算带买入滑点的 `slipped_open_buy_raw`；只有其不高于
  `max_entry_fill_raw` 才成交。
- 空头先基于真实合约开盘计算带卖出滑点的 `slipped_open_sell_raw`；只有其不低于
  `min_entry_fill_raw` 才成交。
- 无有效开盘、品种能力检查失败、越过限价或订单未成交时，订单到期，不在盘中追价。
- 数量和初始止损不得根据已经看到的 `T+1 open` 重新优化。
- 若开仓后同一开盘已经越过止损，必须先记录开仓，再按第 8 节和品种回转规则处理：允许
  日内平仓的 CTA 只有在有时序行情时才可按实际顺序紧接着止损；只有日 K 时必须使用
  第 8 节的保守代理并标记 `execution_uncertain`。`159915.SZ` 只能记录止损已触发并等待
  次一交易日执行。两种情况都不能免费取消一笔按既定规则本应成交的订单。

## 7. 初始止损与风险预算

### 7.1 方向对称的结构止损

四类 setup 的结构锚点：

```text
previous_5_day_low  = min(low_T-1, ..., low_T-5)
previous_5_day_high = max(high_T-1, ..., high_T-5)
类型 A：structure_low  = min(signal_day_low, previous_5_day_low)
类型 B：structure_high = max(signal_day_high, previous_5_day_high)
类型 C：structure_low  = min(breakout_week_low, lowest_daily_low_since_breakout)
类型 D：structure_high = max(breakout_week_high, highest_daily_high_since_breakout)
```

`since_breakout` 范围从突破周第一个交易日至信号日。计划值在 `T` 收盘后计算：

```text
structure_low_raw  = raw_level(structure_low)
structure_high_raw = raw_level(structure_high)
atr14d_raw         = raw_distance(ATR14D)

LONG:
  planned_entry_raw = max_entry_fill_raw
  raw_stop_raw = min(structure_low_raw - 0.25 * atr14d_raw,
                     planned_entry_raw - 1.50 * atr14d_raw)
  initial_stop_raw = floor(raw_stop_raw / tick_size) * tick_size
  planned_R_price_raw = planned_entry_raw - initial_stop_raw

SHORT:
  planned_entry_raw = min_entry_fill_raw
  raw_stop_raw = max(structure_high_raw + 0.25 * atr14d_raw,
                     planned_entry_raw + 1.50 * atr14d_raw)
  initial_stop_raw = ceil(raw_stop_raw / tick_size) * tick_size
  planned_R_price_raw = initial_stop_raw - planned_entry_raw
```

`1.50 ATR14D` 是最小止损距离，不是最大值。实际成交后分别以
`long_R=buy_fill_raw-initial_stop_raw` 和
`short_R=initial_stop_raw-sell_fill_raw` 记账；若实际 `R <= 0`，不得启动跟踪逻辑，并按
第 8 节进入实际可执行止损或 `EXIT_PENDING`；不能因品种 T+1 限制而删除这笔已成交交易。

ETF 和 CTA 都必须先完成上述水平/距离映射，再组合止损、执行 tick 舍入和风险计算；禁止
先在信号坐标计算百分比限价或完整止损后再整体映射，也不能用连续价格的 `R` 直接乘真实
合约乘数。

### 7.2 过宽止损过滤

```text
planned_R_price_raw / planned_entry_raw <= max_stop_distance_ratio
```

`159915.SZ` 示例的 `max_stop_distance_ratio=0.08`。其他品种必须在评审前预注册该值；未
配置时可沿用 8% 作为研究默认值，但必须在敏感性报告中单列，禁止为增加成交而把止损移入
结构内部。

### 7.3 数量和资金约束

统一风险计算：

```text
risk_budget = signal_close_equity * 0.01
risk_per_unit = planned_R_price_raw * contract_multiplier
                + estimated_round_trip_cost_per_unit
risk_quantity = floor(risk_budget / risk_per_unit / volume_step) * volume_step
```

从 `risk_quantity` 开始按 `volume_step` 递减。`same_day_close_allowed=true` 时逐笔模拟最不利
允许价开仓并立即止损；为 `false` 时只能用计划止损价、双边成本和至少一个持有日费用计算
`planned_risk`，不能假装开仓日止损已经成交。选择 `planned_risk<=risk_budget` 的最大数量。
`risk_per_unit` 只是搜索起点的保守估算，最终逐笔模拟时成本只计算一次，禁止重复扣除。
这里的预计借券费按“开仓后下一可交易时点即止损”的保守天数计提，只用于初始风险预算；
实际持有期间继续逐日扣费，不得因为入场预算只预留短期借券费而停止计提。

- ETF 多头同时受可用现金和 100%名义敞口限制；`159915.SZ` 的 `volume_step=100`、
  `contract_multiplier=1`。
- ETF 空头同时受 `can_short`、当日可借数量、担保品、融券保证金和 100%空头名义敞口
  限制，借券数量为 0 时不得成交。
- CTA 的 `volume_step=1` 手；数量同时受合约乘数和保证金约束，第一版单品种隔离回测的
  `max_margin_fraction=0.30`。具体上限为
  `floor(equity*0.30/(planned_entry_raw*contract_multiplier*margin_rate))` 手，并与风险数量取
  较小值；不得使用跨品种保证金抵扣美化结果。
- 低于一个交易单位时放弃。第一版不优化 1%风险预算、8%示例止损上限或 30%保证金上限。
- 1% 是信号日计划风险预算，不是收益保证。`159915.SZ` 在开仓日无法平仓，次日跳空可能
  使实际亏损明显超过 1%；必须真实记录并由第 13.4 节发布门槛拒绝过大风险。

## 8. 止损执行

止损条件从开仓成交后立即监控，但能否成交由 `same_day_close_allowed` 决定：

1. LOO 未成交时不建立仓位，也不产生止损事件。
2. `same_day_close_allowed=true` 的 CTA 可在开仓日平仓。多头若 `open<=active_stop` 或随后
   `low<=active_stop`，分别按开盘或止损参考价加卖出滑点；空头按
   `open>=active_stop`、`high>=active_stop` 镜像买入平仓。若 LOO 开仓成交时开盘已经越过
   止损，分钟/tick 数据能证明先开后平的顺序时按实际成交；只有日 K 时不得虚构第二笔也在
   同一开盘精确成交，统一标记 `same_open_stop_unresolved=true` 和 `execution_uncertain`，
   研究账本用当日不利边界作保守退出代理（多头 `low` 加卖出滑点、空头 `high` 加买入滑点），
   并从可执行 headline 指标剔除。
3. `159915.SZ` 普通买入份额当日不能卖出，融券卖出后也只能从次一交易日起买券还券。
   开仓日触及/越过止损时只记录 `stop_breached_while_locked=true`，状态进入 `EXIT_PENDING`，
   禁止生成同日虚构平仓。
4. 上述锁定期止损在次一交易日首个可交易开盘无条件执行：多头按开盘加卖出滑点，空头按
   开盘加买入滑点，即使价格已重新回到止损内也不撤销。原因记为
   `deferred_t1_stop_after_entry_day_breach`。
5. 从首个允许平仓日开始，若没有待执行止损，多头按 `open<=stop`、否则 `low<=stop`；空头
   按 `open>=stop`、否则 `high>=stop`。开盘越过止损使用更差开盘，盘中触价使用止损参考价。
6. 若一字涨跌停、期货单边市、停牌或账户限制导致理论止损仍无法成交，持仓延续到首个
   可执行时点并按更差价格成交；缺少逐日可执行性数据时标记 `execution_uncertain`，不得按
   理论止损价美化结果。
7. 同日盘中止损与收盘退出同时发生时，已实际成交的止损优先且只生成一笔平仓；锁定期内
   只登记待执行状态，不得用另一个收盘理由绕过 T+1。
8. 多头 `active_stop` 只能保持或上移；空头只能保持或下移。换月后的价格平移按第 10.3 节
   单独审计，不视为放宽止损。

## 9. 退出条件

退出按以下优先级执行。除盘中止损外，条件在 `T` 收盘确认并在 `T+1` 开盘平仓：多头
卖出，空头买入回补。

### 9.1 优先级 1：强制平仓与硬止损

- 当 point-in-time 数据明确出现 ETF 券源召回/券商强制回补，或 CTA 保证金不足、临近
  最后交易日且换月失败、交易所强平时，在首个可执行时点按保守价格平仓，原因记为
  `mandatory_forced_exit`。标的资格变化本身不自动推断强平，必须以实际规则或账户事件为准。
- 没有强制事件时执行第 8 节硬止损，不等待周线确认。
- 强制事件和止损同日出现时只生成一笔平仓，以真实强制成交原因为主并保留
  `stop_was_also_triggered=true` 审计字段。

### 9.2 优先级 2：反转 setup 失败

- 类型 C 开多后，任一完整周收盘低于突破周中点，或任一日收盘低于突破周最低价，退出。
- 类型 D 开空后，任一完整周收盘高于突破周中点，或任一日收盘高于突破周最高价，退出。

### 9.3 优先级 3：强反向周

多头的强空头周：

```text
weekly_close < weekly_open
weekly_body >= 0.80 * ATR20W
weekly_close_location <= 0.20
weekly_close < previous_week_low
```

空头的强多头周完全对称：

```text
weekly_close > weekly_open
weekly_body >= 0.80 * ATR20W
weekly_close_location >= 0.80
weekly_close > previous_week_high
```

任一强反向周都在下一开盘全额退出，不等待第二周。单周不够强时，仅在连续两个完整周有
跟随才退出：多头要求两周均为空头且第二周收盘低于第一周低点；空头要求两周均为多头且
第二周收盘高于第一周高点。两种较弱 bar 的 `close_location` 阈值分别为 `<=0.50` 和
`>=0.50`。

### 9.4 优先级 4：确认的反向结构

不因一个反向 candidate 或普通 grace 周离场；只有以下任一事件在收盘确认后，下一开盘
退出已有反向仓位：

- 持有多头时，周状态确认进入 `DOWN_CONFIRMED`，或出现类型 D 的
  `UP_REVERSAL_BREAKOUT`。
- 持有空头时，周状态确认进入 `UP_CONFIRMED`，或出现类型 C 的
  `DOWN_REVERSAL_BREAKOUT`。
- 若四类 setup 的最终日线开仓信号与现有仓位方向相反且仓位仍未退出，也只生成
  `opposite_signal_exit`，不在同一开盘反手；该开仓信号视为已消费，反向开仓必须等待新的
  有效日线信号且至少晚一个交易日。

### 9.5 优先级 5：日线趋势失效

连续两个交易日满足对应条件后退出：

```text
LONG:  close < EMA20D and EMA5D < EMA10D
SHORT: close > EMA20D and EMA5D > EMA10D
```

单日穿越 EMA20 不退出，减少被普通回调或反弹洗出。

### 9.6 优先级 6：时间退出

持仓满 20 个交易日后，仅清理没有展开且当前不利的交易：

```text
LONG:  MFE = max(high_since_entry) - buy_fill
       MFE < R and close < buy_fill
SHORT: MFE = sell_fill - min(low_since_entry)
       MFE < R and close > sell_fill
```

### 9.7 盈利后的慢速跟踪止损

不设置固定百分比或固定 `R` 止盈。未达到 `+2R` 时保持初始止损；达到后在当日收盘激活，
新止损最早下一交易日生效：

```text
LONG activation:  high >= buy_fill + 2 * R
SHORT activation: low  <= sell_fill - 2 * R

LONG weekly_stop = floor(
    (min(last_2_completed_week_lows) - 0.25 * ATR20W) / tick_size
) * tick_size
LONG active_stop = max(previous_stop, long_break_even_price, weekly_stop)

SHORT weekly_stop = ceil(
    (max(last_2_completed_week_highs) + 0.25 * ATR20W) / tick_size
) * tick_size
SHORT active_stop = min(previous_stop, short_break_even_price, weekly_stop)
```

`long_break_even_price` 是使平多净回款不低于开多总支出的最低 tick；
`short_break_even_price` 是使开空净收入扣除回补、借券/资金和双边成本后不亏损的最高回补
tick。两者在每个交易日收盘后按累计成本重算，尤其空头借券费或权益补偿增加时不得继续
使用旧的宽松保本价。周线跟踪只用完整周，从下一交易日生效。达到 `4R` 仍不固定止盈，
继续依靠跟踪止损、强反向周和日线失效退出，以换取持有大牛段和大熊段的能力。

## 10. 成交、成本与品种适配

### 10.1 `159915.SZ` ETF 示例

| 项目 | 示例值或规则 |
| --- | --- |
| 初始资金 | `1,000,000` 元 |
| 交易单位 | `100` 份 |
| 最小价位 | `0.001` 元 |
| 买入/回补滑点 | `reference_price * (1 + 0.0005)` |
| 卖出/开空滑点 | `reference_price * (1 - 0.0005)` |
| 双边佣金 | 成交额 `0.0003`，每笔最低 `5` 元 |
| 印花税 | ETF 示例默认 `0`，仍由生效日期配置确认 |
| 融券费 | 必须使用 point-in-time 券商费率和实际持有天数 |
| 回转规则 | 普通买入后卖出、融券卖出后买券还券均为 `T+1` |
| 价格口径 | 信号用复权价，所有成交与账户记账用未复权价 |

深交所会定期调整融资融券标的名单。`159915` 出现在 2026 年第一季度官方 ETF 名单中，
而 2026 年第二季度名单又于 `2026-07-13` 生效，因此实现不得把某一份名单永久硬编码：
[2026 年第一季度 ETF 名单](https://docs.static.szse.cn/www/disclosure/margin/business/W020260417523165985229.pdf)、
[2026 年第二季度调整通知](https://www.szse.cn/disclosure/margin/business/t20260710_621592.html)。
进入交易所名单只是必要条件，不等于某券商账户当日一定有可借券源。

可执行空头回测必须有逐日 `eligible_to_short`、`borrowable_quantity`、`borrow_rate` 和
`recall_or_forced_cover`。缺少任一字段时，空头结果只能进入单独的 `research_only` 情景，
不得与可执行多头结果合并成 headline 净值。

ETF 融券账户还必须冻结以下 point-in-time 字段：

```text
short_margin_ratio, collateral_haircut_by_asset,
maintenance_ratio_threshold, restricted_proceeds_rule,
borrow_fee_reference_price, borrow_fee_day_count_basis,
borrow_fee_accrual_calendar, dividend_compensation,
corporate_action_adjustment, forced_cover_event
```

第一版按单一空头账户逐日记账：

```text
short_liability = short_quantity * raw_close
equity = unrestricted_cash
         + restricted_short_sale_proceeds
         + collateral_market_value
         + other_collateral_value
         - short_liability
         - accrued_borrow_fee
         - accrued_dividend_compensation
         - other_liabilities

maintenance_ratio = (
    unrestricted_cash
    + restricted_short_sale_proceeds
    + collateral_market_value
    + other_collateral_value
) / (
    short_liability
    + accrued_borrow_fee
    + accrued_dividend_compensation
    + other_liabilities
)
```

- 融券卖出款进入 `restricted_short_sale_proceeds`，不得计入可自由开新仓的现金；开仓可用
  保证金必须按券商当日公式和担保品折算率计算，不能简单使用“卖出款 + 初始资金”。
- 本策略只模拟二级市场 100 份整数手交易，不使用大额 ETF 申赎绕过 `159915.SZ` 的 T+1。
- 借券费优先读取券商逐日费用；否则按明确的参考价、年费率、计息日数和 day-count basis
  逐日累计，周末/节假日是否计息由 `borrow_fee_accrual_calendar` 决定，不能默认只算交易日。
- 分红、拆分等权益事件按实际融券合同调整负债数量并扣除现金/证券补偿；缺历史权益补偿
  数据时空头只能 `research_only`。
- 日终维持担保比例低于券商阈值时记录 margin call；若没有补充担保物，按账户事件在首个
  可执行时点强制回补。只有日线数据而无法确定盘中强平价格时标记 `execution_uncertain`。

### 10.2 CTA 期货适配

CTA 品种不使用上述 ETF 固定费率。每个交易日必须从真实合约 K 线和配置快照读取：

```text
settlement, pre_settlement,
contract_multiplier, price_tick, margin_rate,
commission_open, commission_close, commission_close_today,
slippage_ticks, daily_price_limit, session_id, last_trade_date
```

- 多空 PnL 都乘 `contract_multiplier`；保证金只影响资金占用，不得误当最大可能亏损。
- 滑点用 tick 计，手续费按该品种实际的按手或按成交额模型计算，并区分平今规则。
- 权益曲线按期货逐日盯市口径计算，同时单列可用资金、占用保证金和追加保证金事件。
- 涨跌停、单边市、临近交割、流动性不足或合约解析失败时禁止新开仓；已有仓位按保守可执行
  规则处理，不能静默换到任意合约。

逐日盯市按 lot 的开仓日拆分，`side_sign` 对多头为 `+1`、空头为 `-1`：

```text
昨日持仓、今日仍持有:
  variation_pnl = side_sign * (settlement_t - previous_settlement) * qty * multiplier
昨日持仓、今日平仓:
  variation_pnl = side_sign * (exit_fill - previous_settlement) * qty * multiplier
今日开仓、收盘仍持有:
  variation_pnl = side_sign * (settlement_t - entry_fill) * qty * multiplier
今日开仓且今日平仓:
  variation_pnl = side_sign * (exit_fill - entry_fill) * qty * multiplier

cash_t = cash_t-1 + sum(variation_pnl) - commissions - other_fees
margin_t = settlement_t * open_qty * contract_multiplier * margin_rate_t
available_funds_t = cash_t - margin_t - frozen_order_margin_t
```

`pre_settlement_t` 必须与同合约上一交易日 `settlement` 对账；不一致时以交易所字段为准并
写审计差异。`commission_close_today` 只用于 `open_trading_day==close_trading_day` 的 lot。
开仓前按计划原始价格检查保证金，日终再按结算价和当日有效保证金率重估；
`available_funds_t<0` 或触发券商更高阈值时记录 margin call，日线回测最早在下一可交易开盘
强平。无法还原盘中追加保证金/强平时必须标记 `execution_uncertain`，不得声称精确实盘复刻。

### 10.3 连续合约、真实合约与换月

1. `signal_series` 可以是 `RB0/AU0/IF0` 等连续或主力序列，只用于通道、ATR 和信号；必须
   保存每个交易日当时可知的调整方法、调整因子和源合约，禁止用全历史后复权结果回测旧信号。
2. 信号日必须按 point-in-time 主力日历解析 `execution_contract`；真实委托和成交只使用
   未调整的真实合约 OHLC。第 6、7 节先按 `raw_level=a_T*signal_level+b_T` 映射价格水平，
   按 `raw_distance=abs(a_T)*signal_distance` 映射 ATR 等距离，再在真实坐标计算百分比阈值、
   止损、tick 舍入和 `R*contract_multiplier`。找不到合约或无法映射时失败关闭。
3. 以 OI/成交量决定的主力切换只有在 `T` 收盘计算完成后才可知。若 `T` 首次确认切换，
   撤销旧合约待开订单，最早 `T+1` 迁移已有仓位，并从 `T+1` 起进入 3 个交易日的
   `post_roll_cooldown`，期间不新开仓；禁止利用历史上后来才知道的切换日提前 3 日过滤。
4. 只有基于挂牌时已知的最后交易日/预注册固定换月日，才允许使用
   `deterministic_pre_roll_days` 做事前过滤；决策依据和生效时间必须写入映射表。
5. 已有仓位换月时，同方向平旧开新是仓位迁移，不是新信号，可在同一执行时点完成；两腿
   都计手续费和滑点并写入 `roll_events.csv`。
6. 新合约止损用换月时新旧可执行参考价差平移，使止损到市场价的距离不扩大；目标止损
   映射后，多头向上取 tick、空头向下取 tick。若缺少双腿可执行价格、换月后风险超预算
   或新合约不可交易，则只平旧仓，不自动开新仓。
7. 后复权连续价格永远不能直接成为委托价、止损价、保证金或 PnL 价格。

上述设计借鉴现有 CTA 对连续序列、真实合约和换月动作的分层，但不假设可以原样复用：
现有全历史 back-adjust 输出会基于输入内的后续换月调整更早价格，不能直接作为 point-in-time
历史信号；现有换月窗口也使用自然日和事后已知切换日期。后续 `stock/**` adapter 必须按
本节重新生成 as-of 调整快照，只对已确认切换使用交易日 cooldown。本阶段只读取 `cta/**`
作为参考，不修改其中任何文件。

### 10.4 期末持仓与收益记账

回测结束默认不强平，开放持仓按末日真实合约或 ETF 未复权收盘价形成标准权益；同时披露
扣除假设平仓滑点、手续费、借券费、权益补偿和换月待付成本后的 `liquidation_equity`。
完整交易净利润可按多头 `(sell-buy)*quantity*multiplier-costs`、空头
`(sell_short-buy_to_cover)*quantity*multiplier-costs` 复核；CTA 每日权益必须同时与上述
逐日盯市流水累计值一致，不得把保证金释放或受限融券卖出款记为收益。

## 11. 状态机

周线状态：

```text
NEUTRAL
UP_CANDIDATE -> UP_CONFIRMED -> UP_GRACE -> ENDED
DOWN_CANDIDATE -> DOWN_CONFIRMED -> DOWN_GRACE -> ENDED
```

交易状态使用带方向参数的统一状态，避免复制两套容易漂移的逻辑：

```text
FLAT
CONTINUATION_ARMED(side=LONG|SHORT)
REVERSAL_BREAKOUT(side=LONG|SHORT)
REVERSAL_FOLLOW_THROUGH(side=LONG|SHORT)
ENTRY_PENDING(side=LONG|SHORT)
POSITION_INITIAL_RISK(side=LONG|SHORT)
POSITION_TRAILING(side=LONG|SHORT)
ROLL_PENDING(side=LONG|SHORT)
EXIT_PENDING(side=LONG|SHORT)
COOLDOWN
```

每次变化记录 `symbol`、交易日、原状态、新状态、方向、setup、真实合约和原因。同一 symbol
不得同时多空；只有第 9.4 节确认的反向结构才触发已有仓位退出，candidate 不触发。平仓后
必须等待新的有效信号且至少再过一个交易日，不在同一开盘反手。同方向换月迁移是唯一例外，
但必须单独标记 `is_rollover=true`。

## 12. 后续实现边界

用户评审通过后，第一版仍只修改 `stock/**`，不得为了复用而改动 `cta/**`。策略内核不
硬编码 `159915.SZ`，通过只读 adapter 接收 ETF CSV 或 CTA 连续序列、主力日历和品种配置。
建议最小模块：

- `stock/etf/narrow_channel_strategy.py`：周 K、通道、四类信号、止损、退出和统一记账。
- `stock/etf/narrow_channel_instruments.py`：ETF/CTA 配置校验、执行能力、真实合约和换月适配。
- `stock/etf/run_narrow_channel_strategy.py`：单品种/多品种 CLI、文件加载和原子输出。
- `stock/etf/tests/test_narrow_channel_strategy.py`：纯逻辑和多空镜像测试。
- `stock/etf/tests/test_run_narrow_channel_strategy.py`：ETF 与最小 CTA 样例端到端测试。

计划输出目录保持：

```text
stock/etf/output/20260817_zaitongdao/
```

每个 run 输出：

- `instrument_profiles_snapshot.csv`、`input_manifest.json`、`data_quality.json`。
- `price_transforms.csv`：逐日复权价到 ETF 原始价、连续价到期货真实合约价的冻结
  `scale/shift`，并分别给出水平和距离映射的复算字段。
- `weekly_channels.csv`、`channel_episodes.csv`、`daily_signals.csv`。
- `execution_capability.csv`：逐日多空资格、券源/合约解析和排除原因。
- `trades.csv`、`stop_history.csv`、`positions.csv`、`equity_curve.csv`、`summary.json`。
- `borrow_audit.csv`、`etf_credit_account_ledger.csv`（ETF 有空头时）。
- `futures_settlement_ledger.csv`、`margin_events.csv`、`roll_events.csv`（CTA 运行时）。
- `charts/{rank}_{symbol}_{name}_周线窄通道多空.png`、`charts/index.csv` 和
  `charts/render_summary.json`。

PNG 必须包含：

1. 周 K、周成交量、上升/下降通道背景、回归中线和 episode 起止标记。
2. 日 K、EMA5/EMA10/EMA20、日成交量。
3. 四类信号使用不同标记；开多/平多、开空/回补、初始止损和逐日跟踪止损在同一 symbol
   图中对账，不能把“卖出开空”和“卖出平多”画成同一种含义。
4. `159915.SZ` 开仓日止损触发但受 T+1 锁定时，单独标记触发日和次日实际成交日；CTA
   换月同时标记旧、新合约两腿。
5. 标题显示代码、名称、资产类型、信号序列、真实执行合约、数据截止日和最后完整周。

## 13. 回测与评价

### 13.1 不进行参数寻优

第一版只运行本文固定参数。允许做以下稳健性报告，但不得从中挑选最好结果替换默认值：

```text
channel_lookback_weeks: 5 / 6 / 8
min_r_squared: 0.55 / 0.65 / 0.75
min_net_move_atr: 1.00 / 1.25 / 1.50
```

敏感性只做“每次改变一个参数、其他参数保持默认”的 6 个相邻版本，不做 27 组笛卡尔
积，也不从中选择新参数。每个 symbol 独立报告，不允许先看 CTA 各品种结果再为每个品种
挑不同最优参数。稳健门槛固定为：6 个相邻版本中至少 4 个同时满足总收益大于 0、
Profit Factor 大于 1，且 6 个版本均不得超过 40%最大回撤。

### 13.2 历史报告

`159915.SZ` 报告全期和以下自然时间段：

- `2017-08-14` 至 `2022-12-30`。
- `2023-01-01` 至 `2024-12-31`。
- `2025-01-01` 至本地实际最后行情日。

由于本设计已经查看了全历史图形和最近行情，上述最后一段只能称为“历史分段”，不能
声称为真正未见过的样本外。`2026-07-21` 至最终规则批准日之间即使尚未读取，也只能称为
“未读取历史留出段”；真正前向样本外从用户批准本文件、参数哈希冻结之后的首个交易日
开始，并在 `summary.json` 记录 `strategy_freeze_date`。

其他 ETF 或 CTA 品种使用自身首个有效日到数据截止日，并按相同自然年份切段。CTA 第一版
逐 symbol 独立回测，不先合成组合净值，避免保证金共享、相关性和同时信号掩盖单品种问题。
每个 symbol 必须分别生成：

1. 四类信号的 `signal_research` 统计。
2. `LONG` 可执行结果（类型 A+C）。
3. `SHORT` 可执行结果（类型 B+D）；只有能力审计完整时才生成。
4. 多空组合结果；任一方向是 `research_only` 时，组合也只能标记 `research_only`。

### 13.3 对照组

- `159915.SZ` 复权买入持有。
- `159915.SZ` 现有 `20260719_chuangyeban_optimized` EMA 三档策略。
- `159915.SZ` 现有 `20260727_chuangyeban_regime_overlay` 策略。
- 类型 A/B/C/D 单独结果、顺势 A+B、反转 C+D、多头 A+C、空头 B+D 和完整组合。
- CTA 使用显式换月成本的同向长期持有作为方向参考，并以顺势、反转、多头、空头和完整
  组合互为消融对照；不能用无换月成本的连续指数直接充当可交易基准。

至少报告总收益、年化收益、最大回撤、Sharpe、Calmar、交易次数、胜率、平均盈亏比、
Profit Factor、平均 `R`、最大连续亏损、年化单边换手、费用、借券费、换月成本、保证金
峰值、市场暴露比例和大趋势捕获率。全部指标按 `LONG/SHORT` 和四类 setup 分拆，不能只按
组合总收益选策略。

大趋势捕获率沿用项目现有可执行 episode 思路并固定定义：

1. 趋势 episode 从 `CONFIRMED` 后下一可交易开盘开始，到该 episode 结束后下一可交易
   开盘结束；末尾未结束 episode 标记 `open`，不进入 headline 中位数。
2. `direction_sign` 对上升为 `+1`、下降为 `-1`，
   `directed_return=direction_sign*(end_open/start_open-1)`。
3. `159915.SZ` 只有 `directed_return>20%` 的 episode 进入 headline；CTA 使用
   `abs(end_open-start_open)/ATR14D_at_start>=4`，避免不同价格尺度不可比。
4. episode 起点按相同 1%风险预算、止损下限和该资产资金约束计算假设标准仓位；持有到
   episode 结束且扣除真实成本后的利润为分母。
5. 上升 episode 用类型 A 实际净利润作分子，下降 episode 用类型 B；类型 C/D 的反转捕获
   另表报告，不混入原通道延续捕获率。
6. `capture_ratio=actual_episode_net_pnl/hypothetical_episode_net_profit`，不截断大于 100%
   的结果；报告中位数、分布、合格 episode 数和逐段明细。

### 13.4 发布门槛

第一版是研究策略。输出使用两个独立字段：`execution_mode` 只能是 `research_only` 或
`executable`，`review_status` 只能是 `research_accepted` 或 `research_rejected`。状态按
`symbol + execution_mode` 独立判定，只有同时满足以下条件才可标记为
`review_status=research_accepted`：

- 无未来函数审计通过。
- 所有成交、止损和权益可从输出逐行复算。
- 多空执行能力、ETF 券源/借券费或 CTA 真实合约/换月数据完整；
  `execution_mode=research_only` 即使通过方法评审，也永远不能描述为可执行策略。
- 全期最大回撤不高于 35%。
- 非跳空止损交易的单笔净亏损不得超过交易前权益的 1.25%。
- 包含跳空在内的任一单笔实际净亏损不得超过交易前权益的 3%；超过即拒绝，不以跳空为
  例外，只在审计表中单独归因。
- 任一成交被标记为 `execution_uncertain` 时，可保留研究产物但不得通过可执行评审。
- 扣除默认成本后 Profit Factor 大于 1。
- 参数敏感性必须达到第 13.1 节“至少 4/6 盈利且 Profit Factor 大于 1、全部回撤不超过
  40%”的固定门槛。
- `159915.SZ` 相比买入持有最大回撤绝对值至少降低 5 个百分点，并明确相对现有
  EMA/regime 策略的收益、回撤和换手取舍。CTA 必须明确相对显式换月基准的取舍，不套用
  ETF 买入持有门槛。
- CTA 的逐日 PnL、保证金、平今手续费和每次换月必须完全对账；出现无法解释的连续价格与
  真实合约收益差异时直接拒绝。

未通过时照常保留全部审计产物，但状态必须为 `research_rejected`，不得包装成可实盘策略。

## 14. 测试与验收清单

### 14.1 数据与周线

- 重复交易日、非法 OHLC、非正价格、负成交量、缺少目标 symbol 或品种配置时失败关闭。
- 缺少交易日历、日历哈希不匹配或行情缺少预期交易日时失败关闭；ETF 停牌只有被数据源
  明确标记才可保留缺口。
- `159915.SZ` 周一至周四截止的数据不会生成当前不完整周信号。
- CTA 夜盘正确归属 `trading_day`，节假日前后不会重复或漏算周 K；未完成交易周不出信号。
- ETF 复权信号价与逐日未复权价双向映射可复算，tick、涨跌停、成本和 PnL 只使用原始价。
- 仿射映射分别满足 `raw_level=a*level+b` 和 `raw_distance=abs(a)*distance`；人为给连续序列
  增加常数平移但保持相同真实价格后，百分比限价、止损距离和仓位结果完全不变。
- CTA 输入同时包含旧、新及候选真实合约的 OHLC、结算价、OI、涨跌停和可交易状态；缺任一
  换月腿数据时失败关闭。
- 连续序列与 point-in-time 真实合约映射可复算，调整后连续价格不会进入成交字段。
- 在输入末尾增加未来换月数据，不会改变过去已经冻结的连续特征、信号和真实价格映射。
- Wilder ATR20W、EMA20W、回归斜率、`R²` 和归一化残差与手工样例一致。

### 14.2 通道

- 标准上升、下降、宽通道和横盘样例被正确分类。
- 单窗口只进入 candidate，连续两个窗口才 confirmed。
- grace、连续失效、相反通道、强空头周和强多头周的优先级正确。
- 输入末尾增加未来数据不会改变历史日期已经生成的通道标签。

### 14.3 开仓

- 类型 A/B 的 EMA 排列、回调/反弹、信号 bar 和突破条件方向镜像正确。
- 下降通道第一次向上反弹不能做多，上升通道第一次向下反转不能做空。
- 类型 C/D 都必须完成周突破、下一完整周跟随和日线确认，不能跳过任一步。
- C/D 的 `trigger_day=1..10` 边界、第 10 日信号次日执行、等待期结构失效和同日失效优先
  均与手工样例一致。
- 所有收盘信号只在下一交易日开盘执行。
- 多头最大买入价、空头最低卖出价、数量和初始止损在信号日的真实价格坐标预先确定；
  `1.03/0.97` 不在含加法平移的连续坐标计算，下一开盘只决定成交或不成交，不得按已知
  开盘重新优化。
- ETF 缺券或费率时空头不成交，CTA 缺真实合约、保证金或换月能力时不成交。
- 同一 episode 各方向的成交次数、止损后等待和最多两次尝试正确；相反方向不在同日反手。

### 14.4 止损与退出

- 多空初始止损都同时满足结构和最小 `1.5 ATR` 距离，tick 舍入方向不缩小风险距离。
- 超过 8%的止损距离取消交易。
- `same_day_close_allowed=true` 的期货开仓日止损正确；`159915.SZ` 开仓日触发止损只进入
  `EXIT_PENDING`，次一交易日按开盘实际平仓，绝不生成同日卖出或买券还券。
- 只有日 K 且 CTA 开仓价已越过止损时，不虚构同一开盘二次成交；使用当日不利边界的保守
  代理、写入 `same_open_stop_unresolved/execution_uncertain`，并从可执行 headline 剔除。
- 开多日向下跳空、开空日向上跳空、普通触价和方向滑点计算正确，不能免费取消已成交 LOO。
- 一字涨跌停/单边市无法止损时延迟到首个可执行时点，不按理论价格成交。
- 多头止损只上移、空头只下移；`2R` 后保本与两周低点/高点跟踪镜像正确。
- 强反向周、连续两个较弱反向周、日线失效、C/D 反转失败和时间退出正确。
- 反向 candidate 不退出；确认的相反通道、C/D 反转突破和最终相反日线信号按第 9.4 节
  退出，且同一信号不能在同一开盘反手。
- ETF 借券费与强制回补、CTA 保证金强平/逐日盯市/同方向换月、空头 PnL 和保证金释放
  记账正确；标的资格变化不会在没有账户事件时被误判为强制回补。
- ETF 受限卖出款、担保品折算、维持担保比例、逐日计息和分红补偿与手工账户流水一致。
- OI/成交量主力切换只在收盘确认后生效，不会事前屏蔽交易；确定性到期规则才允许预先
  过滤，切换后 3 个交易日 cooldown 和两腿成本正确。
- 换月止损平移后的 tick 舍入不会扩大风险距离：多头向上取 tick、空头向下取 tick。
- CTA 四类 lot 的逐日盯市公式、`pre_settlement` 对账、平今费率、保证金重估和 margin call
  与手工流水一致。
- 同日多个退出条件只生成一笔成交，并保留最高优先级原因。
- 未平仓头寸不强平且正确计入末日权益。

### 14.5 输出

- CSV/JSON 主键唯一、数值有限、日期有序，摘要可以从明细复算。
- 输入清单记录信号/原始行情、复权因子、source audit、交易日历、品种配置、券源、全部
  真实合约、主力日历和最终策略文档的 SHA-256。
- PNG 可解码，周线/日线/成交量/EMA/通道/四类信号/开平仓/止损完整且与 CSV 对账。
- 真实本地数据运行明确披露 `2026-07-20` 的数据截止限制和 `2026-07-17` 的最后完整周。
- ETF 的 `research_only` 空头与可执行结果物理分表或带强制过滤字段，headline 不会误混；
  CTA 图和交易表同时显示连续 symbol 与真实合约。
- 全部 `stock/etf` 测试、Ruff、编译和 whitespace 检查通过。

## 15. 设计评审结论

### 15.1 已通过的设计检查

- **多空一致性**：上升回调做多、下降反弹做空；两种逆势交易都必须经过突破、周跟随和
  日线确认，不猜第一段反转。
- **可量化性**：主观窄通道被转换为固定、ATR 归一化且可复算的规则。
- **无未来函数**：不完整周排除，收盘信号下一交易日执行；OI/成交量换月只在切换被观察
  后处理，不使用事后主力切换日做事前过滤。
- **订单可执行**：信号日预先确定 LOO 限价、数量和止损，不使用已知次日开盘优化订单。
- **风险闭环**：每次开多/开空前已有止损；期货按可回转规则执行，159915 开仓日触发只
  能次日平仓且如实承担隔夜跳空。数量受 1%计划风险预算以及现金、券源或保证金约束。
- **执行隔离**：ETF 融券和 CTA 真实合约均失败关闭，研究信号不会冒充可执行成交。
- **账户闭环**：ETF 受限卖出款、维持担保和权益补偿与 CTA 逐日盯市、保证金、平今和
  换月均有独立可复算流水。
- **持有大趋势**：多空均无固定止盈，盈利后使用方向对称的慢速周线跟踪止损。
- **避免过拟合**：默认参数预注册，第一版不寻优；历史最近分段不冒充真正样本外。
- **跨品种隔离**：统一信号内核只依赖标准行情；ETF/CTA 的成本、日历、保证金和换月由
  adapter 负责，后续仍只修改 `stock/**`。
- **评审闭环**：独立复审提出的连续确认、反转时序、LOO 成交、日历、跟踪止损和指标
  定义问题均已落实为确定规则；用户批准门仍保持关闭。

### 15.2 主要风险

- 量化规则不会与 Al Brooks 的人工读图逐次完全一致。
- 周线确认和反转跟随会同时牺牲多头最低点及空头最高点附近收益，以降低逆势抢反转风险。
- 周线慢速退出会回吐部分浮盈，快速 V 形反转时尤其明显。
- 日线 OHLC 无法还原盘中路径；本方案只采用不会美化结果的保守成交优先级。
- `159915.SZ` 的 T+1 使开仓日止损无法成交，实际亏损可能显著超过 1%计划风险预算。
- ETF 空头面临无券、借券费变化、召回和逼空跳空；缺少历史券源会显著限制可执行回测。
- CTA 空头虽不依赖券源，但杠杆、单边市、夜盘、平今费率、交割和换月会放大模型风险。
- 当前 `159915.SZ` 数据落后于实际日期，评审通过后仍需刷新数据再判断最新多空信号。

### 15.3 评审门

本文件完成设计自审，但**尚未获得用户策略评审批准**。在用户明确回复批准或提出修改并
完成复审之前，不编写实现计划、不新增策略代码、不运行正式回测。

## 16. 参考依据

- Al Brooks 相关公开材料把紧密通道描述为回调很短、反向交易难以获利的强趋势阶段，
  同时指出第一次反转通常较弱：
  [Distinguishing Strong Legs, Trading Ranges, and Trends](https://www.brookstradingcourse.com/wp-content/uploads/2016/05/Al-Brooks-Webinar-Distinguishing-Strong-Legs-TR-vs-Trends-May-3-2016.pdf)。
- Brooks Trading Course 的周线案例强调，紧密上升通道中交易者通常应做多或观望，合理
  空头 K 线或连续空头力量才值得退出：
  [DAX 40 Possible Bull Leg in Trading Range, Tight Bull Channel](https://www.brookstradingcourse.com/analysis/dax-40-possible-bull-leg/)。
- 近期周线案例同样要求空方先出现强空头 K 线、突破小趋势线并有后续跟随，才构成更可信
  的反转：
  [Weekly E-mini Tight Bull Channel](https://www.brookstradingcourse.com/analysis/weekly-e-mini-tight-bull-channel/)。
- 深交所融资融券规则和季度名单说明标的范围会调整，且会员公布范围不得超出交易所名单：
  [融资融券交易实施细则（2023 年修订）](https://docs.static.szse.cn/www/lawrules/rule/trade/business/margin/W020230217568325595241.pdf)、
  [2026 年第二季度调整通知](https://www.szse.cn/disclosure/margin/business/t20260710_621592.html)。
- 深交所基金规则明确，非债券/黄金/货币/特定跨境或商品期货 ETF 的普通基金份额当日买入、
  次一交易日才能卖出；融资融券规则也规定融券卖出后自次一交易日起才能买券还券：
  [证券投资基金交易和申购赎回实施细则（2022 年修订）](https://docs.static.szse.cn/www/fund/guide/W020220610536157903323.pdf)。
- 中金所产品说明表明合约乘数、最小价位、保证金和交易时间都是逐合约要素，不能沿用 ETF
  口径：[沪深 300 股指期货](https://www.cffex.com.cn/hs300/)。
- 中金所结算规则明确期货以当日结算价计算当日盈亏并逐日清算：
  [中国金融期货交易所结算细则](https://www.cffex.com.cn/ssxz/20250107/43078.html)。
- 本仓库现有 CTA 参考实现明确区分连续序列、真实合约解析和换月动作：
  [`continuous_contract.py`](../../cta/skills/data_backtest/continuous_contract.py)、
  [`contract_resolver.py`](../../cta/portfolio_logic/contract_resolver.py)、
  [`rollover_rules.py`](../../cta/skills/data_backtest/rollover_rules.py)。

以上资料用于形成策略语义，所有数值阈值均是本项目为可回测性预注册的工程定义，不是
Al Brooks 官方参数，也不构成投资建议。
