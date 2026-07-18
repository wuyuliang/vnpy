# A 股指数 ETF 日频轮动策略实现规格

## 0A. 2026-07-18 牛股持有与回撤控制研究变体

本变体的完整设计与实现清单分别见：

- `20260718_etf_winner_holding_drawdown_control_design.md`
- `20260718_etf_winner_holding_drawdown_control_implementation.md`

默认 `StrategyConfig` 继续保持旧清洁基线，避免历史研究结果悄然变化。牛股持有变体
必须显式启用 `winner_holding_enabled`、`market_state_enabled` 和
`dynamic_risk_enabled`。预注册组合固定使用 ATR5/ADX10、`3 * ATR5` 初始及移动
止损、盈利 `2 * entry_ATR5` 晋级赢家、持仓排名差于 20 连续三日且跌破 EMA10
退出、跌破 EMA20 立即退出。

组合硬约束为行业 35%、60 日相关系数不低于 0.90 的连通簇 30%、组合计划止损
风险 3%、相关簇计划止损风险 1.5%。市场状态连续确认两日；`CAUTION` 总仓位 50%，
只允许 Top3 以半风险开仓；`RISK_OFF` 次日开盘清仓。减仓优先试仓、较差排名和较弱
EMA5 斜率，恢复仓位优先于新开仓但必须重新通过全部容量检查。

每次回测固定输出 9 个文件：候选、信号、成交、持仓、净值、持仓状态、组合风险、
牛趋势捕获率和摘要。`run_winner_holding_experiments.py` 固定运行清洁基线、仅风险控制、
仅赢家持有、仅市场三态、组合版和组合版双倍成本六组配置，不进行参数寻优。

组合版只有同时满足以下六项才可标记通过：最大回撤不超过 20%、年化单边换手不超过
8 倍、年化收益不低于 4%、Sharpe 不低于 0.50、合格牛趋势中位捕获率不低于 50%、
双倍成本年化收益为正。缺少合格牛趋势区间视为捕获率门槛失败。在真实数据结果全数
通过前，本变体仍是研究策略，不得表述为可直接实盘。

## 0. 2026-07-17 当前实现覆盖层

本节记录当前代码合同，并优先于本文后续早期基线中与之冲突的内容。分阶段设计、实验
顺序和选择门槛详见 `20260717_etf_rotation_optimization_design.md`。

### 0.1 不变交易时序

- 只做境内 A 股指数股票型 ETF，不做港股、海外、QDII、商品、债券或主动 ETF。
- `T` 日收盘生成信号，普通订单在 `T+1` 开盘执行，先卖后买。
- 买入、卖出分别按开盘价加、减 5 bps 滑点，并双边收取万分之三佣金。
- 单 ETF 开仓目标不超过前收盘权益的 20%，每笔风险 1%，100 份整数手，不允许负现金。
- 单券和行业限制约束开仓，不因之后价格漂移被动超限而强制再平衡。

### 0.2 清洁基线

默认 `StrategyConfig` 仍为 Top5、行业开仓 50%、ATR5/ADX5。清洁基线的当前规则为：

```text
Risk On:
EMA5_T > EMA10_T > EMA20_T
trend_adx_T > 20
open_T > EMA5_{T-1}
close_T > EMA10_T

新仓流动性:
median(turnover_{T-19:T}) > 200,000,000

RS Score:
  0.35 * Rank(Return3)
+ 0.35 * Rank(Return5)
+ 0.20 * Rank(Return10)
+ 0.10 * Rank(Return20)
+ 0.20 * Rank(EMA5Slope)
- 0.20 * Rank(NormalizedATR5)
```

RS 基础评分不使用 2 亿元门槛。流动性达标且同一 `benchmark_key` 中 20 日成交额
中位数最高的 ETF 组成入场参照池，并生成 `entry_rank`。所有基础评分有效的 ETF 按
自身得分插入同一参照池生成 `holding_rank`。新仓使用 `entry_rank`，持仓跌出 Top10
使用 `holding_rank > 10`，不得因单日或短期成交额下降直接产生 `rs_unavailable` 退出。

### 0.3 历史 ETF 池和同指数去重

- `fund_basic(market="E")` 同时保留 `status=L` 和 `status=D`。
- 指标计算前严格裁剪到 `list_date <= datetime <= delist_date`；上市前 K 线不得计入
  warmup 或任何指标。
- 信号日只允许已上市且尚未退市的 ETF。无行情可成交的退市持仓按零价
  `delisted_writeoff` 保守核销。
- 同一标准化 `benchmark_key` 只允许一个新仓代表；已有同指数持仓时记录
  `buy_skipped/benchmark_duplicate`，但不强制轮换原持仓。

### 0.4 ATR 和 ADX 研究参数

`atr_period` 与 `adx_period` 分别允许 5 或 10：

- `atr5` 和 `normalized_atr5` 始终使用 5 日 ATR，仅供 RS 和跳空过滤。
- `risk_atr` 跟随 `atr_period`，用于仓位和固定初始止损。
- `trend_adx` 跟随 `adx_period`，用于沪深300和 ETF 趋势过滤。

四象限真实长期实验选择 ATR5/ADX10 作为最可信研究周期，但代码默认值保持 5/5，
防止未显式指定研究配置时悄然改变策略。

### 0.5 预注册组合

预注册组合必须通过 CLI 显式启用，不是默认配置：

```text
entry_rank = 3
entry_confirmation_days = 2
max_entry_gap_atr = 1.0
max_industry_weight = 0.35
correlation_lookback = 60
min_correlation_observations = 40
correlation_threshold = 0.90
max_correlation_weight = 0.30
atr_period = 5
adx_period = 10
```

ETF 必须连续两个信号日处于入场 Top3 且趋势确认通过。若执行日开盘严格高于
`signal_close + ATR5_T`，记录 `buy_skipped/gap_filter` 并取消订单。相关性只使用截至
`signal_date` 的共同收益率；少于 40 个共同样本不判定相关。候选与已持仓、同日先成交
新仓形成的相关性连通簇共享 30% 开仓额度。

### 0.6 当前研究结论

2018-01-02 至 2026-07-17 的点时真实回测中，没有变体同时通过全部预注册门槛。
预注册组合未同时超过清洁基线的净收益和 Sharpe，且回撤略差于波动匹配沪深300；
ATR5/ADX10 清洁基线的年化单边换手又高于 10 倍目标。因此当前交付是研究基线和完整
交易图，不是可直接实盘的最终策略，也不得表述为样本外验证成功。

## 1. 目标

在 `stock/etf/` 内实现一个无未来函数的 A 股指数 ETF 日频轮动研究与回测链路。策略在沪深300处于 Risk On 状态时，从满足流动性和上市时间要求的境内 A 股指数 ETF 中选择相对强度最高且趋势确认通过的品种，按 ATR 风险预算建仓；沪深300跌破 EMA10、个券趋势转弱、相对强度跌出 Top10 或触发 ATR 硬止损时退出。

本规格是代码实现合同。除非命令行参数显式覆盖，代码必须使用本文默认值，不得自行改变指标定义、信号时序或成交规则。

## 2. 实现边界

### 2.1 必须实现

- 沪深300市场过滤。
- 沪深交易所 A 股指数 ETF 候选池构建和显式行业分类。
- 日成交额和上市时间初筛。
- 3、5、10、20 日收益率、EMA5 斜率和 ATR5 的截面排名。
- RS Score、Top5 入选和 Top10 退出判断。
- ETF 趋势确认。
- ATR 风险仓位、单品种权重上限、行业权重上限和 100 份整数手约束。
- 日频调仓、ATR 日线止损、交易费用和滑点。
- 每日信号表、交易明细、持仓、净值和回测摘要输出。
- 单元测试和最小端到端回测测试。

### 2.2 第一版不实现

- vn.py 实盘网关、交易所报单和盘中行情订阅。
- 分钟或 Tick 级回测。
- 参数寻优、机器学习、行业中性或风险平价。
- 融资、融券、做空和杠杆。
- 分红再投资的独立现金流建模；价格统一使用前复权数据。

## 3. 默认参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `benchmark_symbol` | `000300.SH` | 沪深300指数 |
| `ema_periods` | `5, 10, 20` | EMA 周期 |
| `adx_period` | `5` | 市场和 ETF 趋势强度周期；固定为 5 |
| `adx_threshold` | `20` | 必须严格大于 20 |
| `atr_period` | `5` | Wilder ATR 周期；固定为 5 |
| `atr_stop_multiple` | `2.0` | 初始止损距离 |
| `risk_per_trade` | `0.01` | 每笔风险为组合净值 1% |
| `max_position_weight` | `0.20` | 单只 ETF 最大权重 20% |
| `max_industry_weight` | `0.50` | 下单时同一已识别行业最大权重 50% |
| `entry_rank` | `5` | 入选 Top5 |
| `exit_rank` | `10` | 跌出 Top10 时退出 |
| `min_turnover` | `200_000_000` | 当日成交额严格大于 2 亿元 |
| `min_listing_months` | `6` | 上市至少满 6 个自然月 |
| `lot_size` | `100` | ETF 每手 100 份 |
| `commission_rate` | `0.0003` | 买卖双边佣金万分之三 |
| `min_commission` | `5.0` | 每笔最低佣金 5 元 |
| `slippage_rate` | `0.0005` | 单边滑点 5 bps |
| `initial_capital` | `1_000_000` | 默认初始资金 100 万元 |
| `warmup_bars` | `80` | 信号输出前至少准备 80 根有效日线 |

ETF 交易默认不收印花税。费用模型必须封装，便于测试时设为零，但第一版不需要通用插件系统。

## 4. 数据约定

### 4.1 数据源

默认使用 Tushare：

- 沪深300日线：指数日线接口，代码 `000300.SH`。
- ETF 清单和上市日期：基金基础信息接口。
- ETF 日线和成交额：基金日线接口。
- 复权因子：基金复权因子接口；回测 OHLC 使用前复权价格。

若当前 Tushare 权限不能返回某字段，允许从本地 CSV 读取同一标准字段，但不得改变计算口径。

### 4.2 ETF 范围

候选池只包含上海证券交易所和深圳证券交易所挂牌、状态为上市、可在二级市场交易、且能从名称和跟踪基准严格确认的境内 A 股指数股票型 ETF。排除：

- 货币 ETF。
- 债券、商品和其他非股票型 ETF。
- 港股、海外、QDII、纳斯达克、日经及其他非 A 股指数 ETF。
- 主动基金的复合业绩基准和无法可靠判定资产范围的 ETF。
- LOF、封闭式基金和非 ETF 基金。
- 已退市或信号日已暂停上市的基金。
- 信号日没有有效 OHLC 或成交额的基金。

基金类型优先使用数据源的基金类型字段判定；若字段缺失，必须使用项目内显式白名单/黑名单映射，不得只按名称模糊猜测。无法可靠判定类型的基金排除，并记录 `unsupported_fund_type`。

### 4.3 标准日线字段

指数和 ETF 日线统一为：

- `symbol`
- `exchange`
- `datetime`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `turnover`

另为 ETF 保存：

- `name`
- `list_date`
- `fund_type`
- `benchmark`
- `asset_scope`
- `industry`
- `is_trading`

要求：

- `datetime` 为无时区的交易日日期，按升序排列。
- 同一 `symbol + datetime` 只能保留一条记录。
- OHLC 必须为正数，且 `high >= max(open, close)`、`low <= min(open, close)`。
- `turnover` 统一为人民币元。若数据源单位为千元，标准化时乘以 1000。
- 指标按各品种自己的有效交易日序列计算，不用前向填充补造停牌 K 线。
- 截面排名只比较信号日有真实有效 K 线的 ETF。

### 4.4 上市满 6 个月

信号日为 `T`，仅当 `T >= list_date + DateOffset(months=6)` 时通过。这里使用 6 个自然月，不用 120 或 126 个交易日近似。

### 4.5 数据不足和异常

- 任一指标所需历史不足时，该品种当日不参与排名。
- ETF 至少要有 21 根有效收盘数据，并满足 `warmup_bars=80` 后才允许产生信号。
- 任何输入指标为 `NaN` 或无穷大时，该品种当日排除。
- 当日成交额等于 2 亿元不通过，必须严格大于。
- 排除原因写入每日候选审计表，不允许静默丢弃。

## 5. 指标定义

所有指标在品种自身按 `datetime` 升序的日线上计算。`T` 日信号只能使用截至 `T` 日收盘已知的数据。

### 5.1 EMA

使用 pandas 等价定义：

```python
ema_n = close.ewm(span=n, adjust=False, min_periods=n).mean()
```

市场开盘过滤是唯一使用前一日指标的条件：

```text
open_T > EMA5_{T-1}
```

### 5.2 收益率

```text
ReturnN_T = close_T / close_{T-N} - 1
```

其中 `N` 分别为 3、5、10、20。这里是跨 N 个有效交易日的收盘收益率。

### 5.3 EMA5 斜率

使用 5 个交易日的归一化变化率：

```text
EMA5Slope_T = EMA5_T / EMA5_{T-5} - 1
```

不使用线性回归角度，避免价格量纲和角度定义歧义。

### 5.4 True Range 和 ATR5

```text
TR_T = max(
    high_T - low_T,
    abs(high_T - close_{T-1}),
    abs(low_T - close_{T-1})
)
```

ATR5 使用 Wilder 平滑：

```python
atr5 = tr.ewm(alpha=1 / 5, adjust=False, min_periods=5).mean()
```

RS 中使用归一化 ATR，避免高价格 ETF 被机械惩罚：

```text
NormalizedATR5_T = ATR5_T / close_T
```

后文 RS 中的 ATR rank 均指 `Rank(NormalizedATR5)`；仓位和止损使用价格单位的 `ATR5`。策略配置和指标入口必须拒绝非 5 日 ATR 周期，避免字段名与实际计算周期不一致。

### 5.5 ADX5

ADX 使用 Wilder 方法计算 `+DM`、`-DM`、TR、`+DI`、`-DI`、DX 和 ADX，周期均为 5。实现不得改用简单移动平均。若分母为 0，则当日 ADX 记为缺失，不通过过滤。策略配置和指标入口必须拒绝非 5 日 ADX 周期。

## 6. 信号和交易时序

采用已确认的时序 A：

1. `T` 日收盘后，用截至 `T` 的完整数据生成市场状态、RS 排名、退出信号和 `T+1` 目标持仓。
2. 市场开盘条件使用 `open_T > EMA5_{T-1}`；其余条件使用 `T` 日值。
3. 普通退出和调仓在 `T+1` 开盘执行。
4. `T+1` 当日先执行卖出，再按剩余现金执行买入。
5. 新开仓的 ATR 和风险预算使用 `T` 日数值，预计买入价使用 `T+1` 实际开盘成交价。
6. 当天盘中是否触发止损只能在拥有当天 `open/high/low/close` 后判断；日线回测按第 11 节规则成交。

严禁使用 `T+1` 的收盘价、最高价、最低价或成交额决定 `T+1` 开盘订单。

## 7. 市场过滤：Risk On

沪深300在 `T` 日同时满足以下条件时，`risk_on_T = True`：

```text
EMA5_T > EMA10_T > EMA20_T
ADX5_T > 20
open_T > EMA5_{T-1}
```

全部使用严格大于。任一指标缺失则为 Risk Off。

当 `risk_on_T = False`：

- 不产生任何 `T+1` 新开仓订单。
- 已有持仓不因 Risk On 的均线排列、ADX 或开盘过滤单独退出；只按第 12 节的明确退出规则处理。

## 8. ETF 初筛

仅在 Risk On 时对每只 ETF 在 `T` 日执行：

```text
turnover_T > 200,000,000 元
T >= list_date + 6 个自然月
is_trading_T = True
数据和指标完整
```

初筛不使用未来日期的基金状态。回测某历史日时，只能使用该历史日已上市且仍可交易的 ETF，避免幸存者偏差。

## 9. Relative Strength 排名

### 9.1 排名总体

排名截面是当日通过第 8 节初筛的全部 ETF，趋势确认在 RS Top5 之后执行。

每个指标使用降序百分位排名，数值越大，排名值越接近 1：

```python
rank = series.rank(method="average", ascending=True, pct=True)
```

因此：

- 收益率和 EMA5 斜率越高，加分越多。
- NormalizedATR5 越高，ATR rank 越高，经负权重后扣分越多。
- 并列值使用平均百分位。

若当日初筛后少于 5 只 ETF，仍对现有 ETF 排名，Top5 实际数量为可用数量。

### 9.2 RS Score

```text
RS Score =
    0.35 × Rank(Return3)
  + 0.35 × Rank(Return5)
  + 0.20 × Rank(Return10)
  + 0.10 × Rank(Return20)
  + 0.20 × Rank(EMA5Slope)
  - 0.20 × Rank(NormalizedATR5)
```

权重按原策略原样保留，正负权重净和为 1.00，不再归一化。

### 9.3 最终名次和稳定排序

按以下键依次排序：

1. `rs_score` 降序。
2. `return_5` 降序。
3. `turnover` 降序。
4. `symbol` 升序。

排序后从 1 开始生成唯一整数 `rs_rank`。Top5 指 `rs_rank <= 5`；跌出 Top10 指 `rs_rank > 10` 或当日不在有效排名截面中。

## 10. 趋势确认和入场

RS Top5 中的 ETF 还必须在 `T` 日满足：

```text
close_T > EMA5_T > EMA10_T > EMA20_T
ADX5_T > 20
```

原描述中的 `EAM5` 按笔误处理为 `EMA5`。全部使用严格大于。

通过趋势确认且当前未持有的 ETF 生成 `T+1` 开盘买入候选。已经持有且仍未触发任何退出条件的 ETF 继续持有，不因为掉出 Top5 就立即卖出；只有跌出 Top10 才因 RS 退出。

当持仓数量不足 5 时，按 `rs_rank` 从高到低补充买入。由于 ATR 仓位可能低于 20%，持仓总权重可能低于 100%，剩余资金保留现金，不使用第 6 名递补来强行满仓。

## 11. 仓位、成交和止损

### 11.1 开仓份额

在 `T+1` 开盘处理新开仓时：

```text
equity = T 日收盘后的组合净值
risk_budget = equity × 1%
stop_distance = 2 × ATR5_T
risk_units = floor(risk_budget / stop_distance)
weight_units = floor((equity × 20%) / estimated_fill_price)
cash_units = floor(available_cash / estimated_fill_price)
industry_units = floor(industry_capacity / estimated_fill_price)
raw_units = min(risk_units, weight_units, cash_units, industry_units)
order_units = floor(raw_units / 100) × 100
```

对已识别行业，`industry_capacity = equity × 50% - 当前同行业持仓市值`；执行日先卖后买，同日先成交订单立即占用额度。`broad_or_other` 不共享行业额度。该限制只约束开仓和调仓，不因价格变化造成的盘后被动超限而强制卖出。

`estimated_fill_price` 使用加入买入滑点后的 `T+1` 开盘价。计算 `cash_units` 时还要预留佣金。若 `order_units < 100`，跳过该笔买入并记录 `insufficient_size`。

多个买入候选按 `rs_rank` 升序逐笔计算，后一个候选只能使用前序订单扣减后的可用现金。不得按目标权重重新归一化，以免突破 1% 风险预算。

### 11.2 普通开盘成交

- 买入成交价：`open × (1 + slippage_rate)`。
- 卖出成交价：`open × (1 - slippage_rate)`。
- 佣金：`max成交额 × commission_rate, min_commission)`，买卖双边收取。
- 现金不足时只允许向下减少到 100 份整数手，不得产生负现金。

若 `T+1` ETF 停牌或无有效开盘价：

- 买入订单取消，不在盘中追价。
- 卖出订单保留原因，下一有效交易日开盘继续尝试。

### 11.3 初始硬止损

买入成交后固定：

```text
stop_price = actual_buy_fill_price - 2 × ATR5_T
```

止损价在持仓生命周期内不向下调整。第一版不实现 ATR 移动止损；其他退出规则可能先于硬止损发生。

### 11.4 日线止损成交

对每个持仓交易日 `D`：

1. 若 `open_D <= stop_price`，按 `open_D × (1 - slippage_rate)` 全部卖出，原因 `atr_stop_gap`。
2. 否则若 `low_D <= stop_price`，按 `stop_price × (1 - slippage_rate)` 全部卖出，原因 `atr_stop_intraday`。
3. 否则不触发硬止损。

同一交易日若已有开盘普通退出订单，开盘退出优先，不再重复执行止损。新仓在买入当日也适用盘中止损；日线回测按“先开盘买入，后检查当日最低价”的保守顺序处理。

止损退出后，该 ETF 最早可在下一次完整的 `T` 日收盘信号生成后，于再下一交易日开盘重新买入，不允许同日止损后重新开仓。

## 12. 退出规则

持仓 ETF 在 `T` 日收盘后满足任一条件，生成 `T+1` 开盘全额卖出订单：

```text
benchmark_close_T < benchmark_EMA10_T
ETF_close_T < ETF_EMA10_T
ETF_rs_rank_T > 10
ETF 当日不在有效 RS 排名截面
```

另有第 11.4 节盘中 ATR 硬止损。等于 EMA10 不退出，必须严格跌破。

若同日满足多个原因，记录全部原因，主退出原因按以下优先级：

1. `atr_stop_gap`
2. `atr_stop_intraday`
3. `market_below_ema10`
4. `etf_below_ema10`
5. `rs_out_top10`
6. `rs_unavailable`

Risk On 的完整条件只控制新开仓和 Risk Off 清仓；退出中的市场条件按原需求单独使用 `benchmark_close < benchmark_EMA10`。因此即使市场 ADX 或均线排列暂时不满足 Risk On，只要沪深300尚未跌破 EMA10，已有持仓不会仅因该项退出，但不会新增仓位。

## 13. 每日组合处理顺序

回测每个交易日必须按以下顺序：

1. 读取当日开盘前由上一交易日收盘生成的订单。
2. 按退出订单优先级执行开盘卖出。
3. 按 RS 名次执行开盘买入并设置初始止损。
4. 对开盘后仍持有的仓位检查当日盘中 ATR 止损。
5. 用当日收盘价盯市，计算组合净值。
6. 收盘后计算截至当日的指标、市场状态、RS 排名和下一交易日订单。
7. 输出当日审计记录。

最后一个回测日只计算收盘净值和信号，不虚构下一交易日成交。是否在回测结束日强制平仓必须作为报告选项，默认不强平。

## 14. 建议代码结构

实现应保持小函数和低耦合，建议最小结构如下：

```text
stock/etf/
├── __init__.py
├── config.py
├── data.py
├── indicators.py
├── ranking.py
├── portfolio.py
├── backtest.py
├── run_etf_rotation.py
└── tests/
    ├── test_indicators.py
    ├── test_ranking.py
    ├── test_portfolio.py
    └── test_backtest.py
```

职责：

- `config.py`：不可变策略参数数据类及校验。
- `data.py`：Tushare 下载、本地缓存、字段标准化和候选池构建。
- `indicators.py`：EMA、收益率、EMA 斜率、TR、ATR 和 ADX。
- `ranking.py`：市场过滤、ETF 初筛、截面排名、RS Score、趋势确认。
- `portfolio.py`：仓位计算、订单、费用、持仓和止损状态。
- `backtest.py`：严格按第 13 节驱动事件顺序并生成结果表。
- `run_etf_rotation.py`：命令行入口，不承载指标或交易规则。

这是建议边界，不要求为单次使用再增加抽象基类、依赖注入框架或通用事件总线。

## 15. 命令行接口

至少支持：

```bash
python3 -m stock.etf.run_etf_rotation \
  --start 2020-01-01 \
  --end 2026-07-17 \
  --initial-capital 1000000 \
  --run-id 20260717_etf_rotation
```

以及复用本地数据：

```bash
python3 -m stock.etf.run_etf_rotation \
  --start 2020-01-01 \
  --end 2026-07-17 \
  --skip-download \
  --benchmark-csv stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/benchmark.csv \
  --etf-csv stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/etfs.csv \
  --metadata-csv stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/metadata.csv \
  --run-id 20260717_etf_rotation_local
```

命令行参数至少包括：`--start`、`--end`、`--initial-capital`、`--run-id`、`--skip-download` 和 `--output-dir`。日期为闭区间。参数非法时给出明确错误并以非零状态退出。

## 16. 输出约定

默认输出到：

```text
stock/etf/output/{run_id}/
```

必须生成：

- `daily_candidates.csv`：每日 ETF 初筛、各指标、排名、趋势确认和排除原因。
- `daily_signals.csv`：Risk On、Top5、继续持有、计划买入和计划退出。
- `trades.csv`：每笔成交、份额、成交价、费用、滑点、退出原因和已实现盈亏。
- `positions.csv`：每日每只 ETF 的份额、成本、止损价、市值、权重、行业和行业权重。
- `equity_curve.csv`：每日现金、持仓市值、总净值、收益率和回撤。
- `summary.json`：参数、起止日期、总收益、年化收益、最大回撤、年化波动、夏普、交易次数、胜率和费用合计。

关键审计字段：

- 信号日期 `signal_date` 和计划成交日期 `execution_date` 必须分开。
- `daily_candidates.csv` 必须包含 `return_3/5/10/20`、五项正向 rank、ATR5 rank、`industry`、`rs_score`、`rs_rank`、`trend_confirmed` 和 `exclusion_reason`。
- `daily_signals.csv` 的买入成交必须包含 `industry` 和 `industry_weight_after_entry`。
- `trades.csv` 必须包含 `side`、`quantity`、`raw_price`、`fill_price`、`commission`、`slippage_cost`、`primary_reason`、`all_reasons`。
- 输出按日期、名次、代码稳定排序，确保重复运行可比较。

## 17. 必须测试的行为

### 17.1 指标测试

- EMA 与 pandas 指定公式一致。
- ReturnN 恰好使用 `T-N` 收盘价。
- EMA5Slope 使用 EMA5 的 5 日变化率。
- ATR5 和 ADX5 与手工构造的 Wilder 计算样例一致。
- ATR 排名使用归一化的 `ATR5 / close`，仓位和止损使用价格单位 ATR5。
- 配置和指标入口拒绝任何非 5 日的 ATR 或 ADX 周期。

### 17.2 排名与信号测试

- 截面百分位排名方向正确，高收益高分、高波动扣分。
- RS Score 权重和公式逐项一致。
- 同分时按 Return5、成交额、代码稳定破同分。
- Top5 之后才执行趋势确认，趋势失败者不由第 6 名递补。
- ETF 掉出 Top5 但仍在 Top10 时继续持有。
- ETF 排名为 11 或当日无法参与排名时退出。
- 成交额等于 2 亿元和上市未满 6 个月均不通过。

### 17.3 无未来函数测试

- 修改 `T+1` 收盘、高低价或成交额，不得改变 `T+1` 开盘订单。
- 修改 `T+1` 开盘价只可改变实际成交价和由成交价决定的份额，不得改变 `T` 日候选名单。
- 市场开盘过滤验证使用 `open_T > EMA5_{T-1}`，不得使用 `EMA5_T` 或 `open_{T+1}`。
- 历史 ETF 池不得包含尚未上市或当时已退市的基金。

### 17.4 仓位和执行测试

- 风险仓位、20% 单券权重上限、50% 行业上限和现金上限取最小值。
- 份额向下取整到 100 的整数倍。
- 五只 ETF 每只不超过 20%，组合不使用杠杆，现金不为负。
- 已识别行业的每笔买入后目标权重不超过 50%，宽基不共享行业额度。
- 多候选按 RS 名次依次占用现金。
- 普通退出先于买入执行。
- 停牌买入取消，停牌卖出延后。

### 17.5 止损测试

- 开盘跳空低于止损价时按开盘卖出。
- 日内最低价触及止损时按止损价卖出。
- 未触及止损时不成交。
- 新仓买入当日可触发盘中止损。
- 止损后不能同日重新买入。

### 17.6 端到端验收

使用至少 3 只人工 ETF 和 1 条人工沪深300序列完成最小回测，必须覆盖：

1. Risk Off 时无新仓；若沪深300跌破 EMA10，则已有持仓清仓。
2. Risk On 时按 RS 和趋势确认选入。
3. ATR 仓位与交易费用正确进入现金和净值。
4. RS 跌出 Top10、ETF 跌破 EMA10和 ATR 止损均能退出。
5. 相同输入重复运行产生字节级一致的 CSV 排序和一致的摘要指标。

## 18. 完成标准

只有同时满足以下条件才视为实现完成：

- 所有第 17 节测试通过。
- CLI 能在人工离线数据上完整运行，不依赖网络。
- 有 Tushare 凭证时可下载并缓存真实指数与 ETF 数据。
- 输出文件齐全，信号日期和成交日期可审计。
- 回测中无负现金、无超过 20% 的新开仓目标权重、无超过 50% 的已识别行业下单目标权重、无非 100 整数倍份额。
- 代码只修改 `stock/**`，使用 Python 3.10+、类型注解、关键函数 docstring 和必要日志。

## 19. 风险说明

- 本策略和回测仅用于研究，不构成投资建议。
- 日线止损无法还原盘中真实成交顺序；本文采用保守但仍是近似的成交模型。
- 基于名称和跟踪基准的行业规则无法等同于官方行业分类，规则变更必须同步测试和审计。
- ETF 历史清单和退市信息不完整会产生幸存者偏差，真实回测前必须核验候选池的历史有效性。
- 前复权价格适合收益连续性研究，但实际成交和份额变化仍是简化模型。
