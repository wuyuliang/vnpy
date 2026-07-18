# A 股 ETF 轮动优化设计

## 1. 目标

在现有日频 ETF 轮动策略上完成四项调整，并使用真实 Tushare 数据重新运行
`2024-05-17` 至 `2026-07-17`：

1. 候选池只保留跟踪境内 A 股指数的股票型 ETF。
2. 开仓和调仓时，同一已识别行业的合计持仓目标权重不超过 50%。
3. RS 使用 3、5、10、20 日收益、EMA5 斜率和 ATR5。
4. ATR 和 ADX 周期统一固定为 5；ATR5 用于仓位与止损，ADX5 用于市场和 ETF 趋势过滤。

保留原策略的 Risk On 结构和阈值、Top5 入场、Top10 退出、趋势确认、单 ETF 20% 上限、
每笔风险 1%、100 份整数手、费用、滑点和交易时序 A。

## 2. A 股指数 ETF 候选池

### 2.1 严格纳入条件

基金必须同时满足：

- 沪深交易所挂牌且状态为上市。
- 标准化 `fund_type` 为 `股票型ETF`。
- `benchmark` 非空且能确认跟踪指数，不是主动基金的复合业绩基准。
- `name + benchmark` 能确认标的是境内 A 股指数。

指数判定采用失败关闭：缺失、冲突或无法可靠识别时排除，不因名称中仅出现
`ETF` 就默认纳入。主动基金常见的多资产复合基准，例如指数收益率与存款、债券
收益率相加，也不视为指数 ETF。

### 2.2 非 A 股排除

排除债券、商品、货币及其他非股票型 ETF，并排除包含下列境外含义的基金或基准：

- `QDII`、港股、香港、沪港深、中概、海外、全球。
- 纳斯达克、日经、恒生非 A 股指数、道琼斯、标普海外指数。
- 美国、日本、德国、法国、印度、越南、新加坡、韩国、沙特及其他明确海外市场。
- 对应英文名称，例如 `NASDAQ`、`NIKKEI`、`S&P`、`DAX`。

明确写明境内 A 股的指数提供商名称不误排，例如：

- 恒生 A 股指数。
- 标普中国 A 股指数。
- MSCI 中国 A 股和 MSCI 中国 A50 互联互通指数。
- 富时中国 A50 指数。

过滤后的元数据增加 `asset_scope=a_share_index`。下载阶段先过滤元数据，再下载 ETF
行情；回测入口再次应用同一过滤器，保证本地缓存和直接函数调用的行为一致。

## 3. 行业分类

行业由 ETF `name + benchmark` 使用有顺序的显式关键词规则确定。更具体的规则先于
宽泛规则，例如半导体先于电子、新能源车先于新能源。至少覆盖：

- `semiconductor`：半导体、芯片、集成电路。
- `electronics`：消费电子、电子、元器件、光学光电子。
- `computer_ai`：人工智能、计算机、软件、云计算、大数据、信创、数字经济。
- `communication`：通信、5G。
- `machinery`：机床、工业母机、机械、机器人、工程机械、高端装备。
- `automobile`：汽车、智能车、新能源车。
- `new_energy`：新能源、光伏、电池、锂电、储能、风电。
- `finance`：银行、证券、保险、金融。
- `healthcare`：医药、医疗、生物科技、创新药、中药。
- `consumer`：消费、食品饮料、酒、家电、旅游。
- `materials`：有色、稀有金属、钢铁、化工、建材。
- `defense`：军工、国防、航空航天。
- `energy`：石油、油气、煤炭、传统能源。
- `utilities`：电力、公用事业、绿色电力。
- `agriculture`：农业、畜牧、养殖、粮食。
- `real_estate`：地产、房地产。
- `construction`：建筑、基建、工程建设。
- `transportation`：交通运输、物流、航运。
- `media`：传媒、游戏、影视。
- `environmental`：环保。

宽基、规模、风格、红利以及无法可靠识别行业的 ETF 标记为 `broad_or_other`。
`broad_or_other` 不共享一个行业额度，避免把互不相关的宽基 ETF 错误合并为单一行业。
元数据、每日候选审计和持仓输出均保留 `industry`。

## 4. 指标和 RS

新增以下指标：

```text
ReturnN_T = close_T / close_{T-N} - 1, N in {3, 5, 10, 20}
EMA5Slope_T = EMA5_T / EMA5_{T-5} - 1
ATR5_T = WilderATR(high, low, close, period=5)
NormalizedATR5_T = ATR5_T / close_T
ADX5_T = WilderADX(high, low, close, period=5)
```

RS 截面排名方向与现有实现一致，原始值越大，百分位 rank 越接近 1：

```text
RS Score =
    0.35 * Rank(Return3)
  + 0.35 * Rank(Return5)
  + 0.20 * Rank(Return10)
  + 0.10 * Rank(Return20)
  + 0.20 * Rank(EMA5Slope)
  - 0.20 * Rank(NormalizedATR5)
```

最终稳定排序键依次为：`rs_score` 降序、`return_5` 降序、`turnover` 降序、
`symbol` 升序。Top5 和 Top10 规则不变。价格单位 `ATR5` 用于 1% 风险预算和
`2 * ATR5` 固定硬止损；归一化 `ATR5 / close` 仅用于 RS 波动惩罚。沪深300
Risk On 和 ETF 趋势确认均要求 `ADX5 > 20`。配置层与指标入口均拒绝非 5 日周期。

## 5. 行业 50% 下单约束

行业上限参数为 `max_industry_weight=0.50`。每个执行日继续先卖后买。处理买入候选
前，用执行日开盘价估算已有同行业持仓市值；停牌或无开盘价时使用最近有效收盘价，
仍无价格时使用持仓成本。

```text
industry_capacity =
    previous_close_equity * max_industry_weight
    - current_industry_market_value
```

新订单份额取以下上限的最小值，并向下取整到 100 份：

- ATR5 与 1% 风险预算上限。
- 单 ETF 20% 权重上限。
- 可用现金上限。
- 已识别行业的剩余额度上限。

同日先成交的买单立即按实际含滑点成交金额占用行业额度。若行业额度使原本可成交的
订单降到 100 份以下，记录 `buy_skipped` 和原因 `industry_cap`。行业为
`broad_or_other` 时不应用行业上限。

该约束只应用于开仓和调仓，不因后续价格相对变化造成的被动超限而强制卖出。
`positions.csv` 增加 `industry` 和按当日收盘市值计算的 `industry_weight`；因此该字段
可能因盘中或后续价格变化略高于 50%，不代表下单约束失效。`daily_signals.csv` 的
买入成交记录增加 `industry` 和 `industry_weight_after_entry`，用于精确验证下单时上限。

## 6. 输出和运行

保留旧运行结果，新运行使用：

```text
stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/
stock/etf/output/20260717_last26months_ashare_sector50_rs351020_atr5_adx5_live/
```

复用已真实下载并验证至 `2026-07-17` 的 Tushare warmup 缓存，报告区间为
`2024-05-17` 至 `2026-07-17`，保证仅比较策略变化。回测完成后，为每只实际成交 ETF 生成一张 1680 x 1000 PNG，包含
周 K、日 K、成交量、symbol、中文名和真实买卖点，不生成小时 K。

候选审计列更新为 `return_3/5/10/20`、对应 rank、`ema5_slope`、
`normalized_atr5`、`industry`、`rs_score`、`rs_rank` 和排除原因。旧的
`return_60`、`ema10_slope` 和旧的归一化长周期 ATR 不再作为 RS 审计字段。
`atr5` 用于仓位和止损，`adx5` 用于市场与 ETF 趋势确认。

## 7. 测试和验收

自动测试至少覆盖：

- A 股指数过滤保留普通境内指数及明确的恒生/标普/MSCI A 股例外。
- A 股指数过滤排除 QDII、日经、纳斯达克、港股、债券和商品 ETF。
- 半导体、芯片和集成电路 ETF 归为同一行业，宽基归为 `broad_or_other`。
- Return3/5/10/20、EMA5Slope、ATR5 和 NormalizedATR5 公式正确。
- RS 权重、正负号、排名方向和 `return_5` 破同分正确。
- 同行业已有两只 20% 持仓时，第三只最多获得约 10% 行业额度。
- 同行业剩余额度不足一手时跳过，宽基 ETF 不被错误合并限额。
- 原有交易时序保持不变，ATR5 风险仓位、ATR5 固定止损和 ADX5 双重趋势过滤通过测试。

真实运行必须满足：

- 元数据和成交中不存在港股、海外/QDII、债券或商品 ETF。
- 所有 `buy_filled` 的 `industry_weight_after_entry <= 0.50 + tolerance`。
- 单 ETF 下单目标权重不超过 20%，成交份额均为 100 的整数倍，现金不为负。
- 交易图数量等于有成交且有日线的唯一 ETF 数；索引成交数与 `trades.csv` 对账。
- `pytest`、ETF 目录 Ruff、Python 编译、PNG 解码和尺寸检查全部通过。
