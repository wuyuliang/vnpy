# ETF 行业日频汇总与技术指标设计

## 1. 目标

在不使用未来数据的前提下，将 A 股指数 ETF 按现有 `industry` 分类逐日汇总，生成
`stock/etf/data/industry.csv`。文件同时提供行业真实总市值、流动性统计和可用于趋势
研究的行业价格指数及技术指标。

## 2. 输入与范围

- ETF 日线：`stock/etf/data/20260717_2018_20260717_point_in_time_live/etfs_lifecycle_clean.csv`。
- ETF 元数据：同目录 `metadata.csv`，使用现有确定性 `industry` 分类。
- ETF 历史份额：Tushare `etf_share_size`，原始结果缓存到
  `stock/etf/data/etf_share_size.csv`。接口单次最多返回 5000 行，下载器按 ETF 和日期
  范围查询，并对结果去重后增量写入缓存。
- 输出日期范围与 ETF 日线有效范围一致。
- 保留全部现有行业分类；`broad_or_other` 作为独立桶保留，便于全量对账。

只使用符合 ETF 上市和退市生命周期的日线。份额记录按 ETF、公告交易日排序，只允许
向未来交易日填充；首次份额记录以前不得反向填充。

## 3. 真实行业总市值

Tushare `etf_share_size.total_share` 按万份记录，转换为基金份额；市值价格必须使用同一
接口返回的未复权 `close`，不得使用策略缓存中的前复权收盘价：

```text
fund_units = total_share * 10,000
etf_market_value = etf_share_size.close * fund_units
industry_total_market_value = sum(etf_market_value)
```

接口的 `total_size`（万元）保留在原始缓存中用于质量核对，但不替代上述市场价格市值
公式。份额可按第 2 节只向未来填充，接口未复权收盘价仅按精确交易日匹配、不得填充；
某日缺失时通过覆盖率披露，不借用前复权价格估算。

每日同时记录：

- `etf_count`：当日有有效日线的行业 ETF 数。
- `market_value_etf_count`：当日同时有有效份额和收盘价的 ETF 数。
- `share_coverage_ratio`：`market_value_etf_count / etf_count`。
- `total_market_value`：有有效份额 ETF 的市值合计，单位元。

若覆盖率不足 100%，总市值仍保留可计算部分，但覆盖率必须明确揭示缺口，不能用发行
份额或未来份额补齐。

## 4. 行业价格指数

技术指标不直接基于总市值，以免申购赎回被误判为行情涨跌。每个行业建立起点为 100
的前一日市值加权价格指数。

在交易日 `T`，只使用 `T-1` 已知的 ETF 市值作为权重：

```text
weight_i,T = market_value_i,T-1 / sum(market_value_T-1)
field_return_i,T = field_i,T / pre_close_i,T - 1
industry_field_T = industry_close_T-1 * (1 + sum(weight_i,T * field_return_i,T))
```

`field` 分别为 `open/high/low/close`。权重只在同日有有效 OHLC、`pre_close` 和前日市值
的成分间重新归一化。新上市 ETF 至少有一个可用前日市值后才参与指数。若某日无法
形成有效权重，该行业指数 OHLC 留空，不伪造收益。

“前日”严格指该行业的上一交易日；ETF 若在上一行业交易日没有日线或真实市值，复牌
后不得把停牌前的陈旧市值作为权重。

该构造保证行业 `high >= max(open, close)`、`low <= min(open, close)`，并且当份额变化
但 ETF 价格不变时，行业指数不产生虚假涨跌。

## 5. 成交统计

ETF 日线 `turnover` 单位为元，行业当日成交额为成分之和。Tushare ETF 原始 `volume`
单位为手，策略生命周期缓存为了配合前复权价格已按复权因子反向调整；因此这里乘以
100 后得到的是“前复权口径基金份额”，再按行业求和。它适合与现有策略价格序列配套
研究，但不冒充交易所原始整数成交份额；若后续需要原始成交量，必须单独缓存未复权
`fund_daily.vol`，不能从前复权缓存近似还原。

对周期 `1/3/5/10/20/60/180` 分别输出：

- `turnover_sum_N`：最近 N 个行业交易日成交额合计，单位元。
- `volume_sum_N`：最近 N 个行业交易日成交量合计，单位为前复权口径份。

滚动窗口必须包含完整 N 行；不足 N 行时输出空值。

## 6. 技术指标

技术指标统一基于第 4 节行业 OHLC 指数：

- `ema_N`：收盘指数的标准 EMA，周期 `1/3/5/10/20/60/180`，至少 N 行后有效。
- `atr_N`：行业 OHLC 的 Wilder ATR，周期 `1/3/5/10/20/60/180`。
- `adx_N`：行业 OHLC 的 Wilder ADX，周期 `3/5/10/20/60/180`。

不输出无有效平滑意义的 `adx_1`。ATR 为行业指数点数，不做价格归一化。
若行业 OHLC 缺失，该日所有 EMA/ATR/ADX 均为空；下一有效日从新的连续价格片段重新
预热，避免跨缺口延续看似有效的指标。

## 7. 输出模式

基础字段按以下稳定顺序输出：

```text
datetime, industry, industry_name,
etf_count, market_value_etf_count, share_coverage_ratio, total_market_value,
open, high, low, close,
turnover_sum_1 ... turnover_sum_180,
volume_sum_1 ... volume_sum_180,
ema_1 ... ema_180,
atr_1 ... atr_180,
adx_3 ... adx_180
```

文件按 `datetime, industry` 升序，键必须唯一。金额与数量保留浮点精度，不格式化为
带逗号字符串。

## 8. 代码与命令

- `stock/etf/industry.py`：份额标准化、点时对齐、行业指数和指标纯函数。
- `stock/etf/build_industry_data.py`：下载/复用份额缓存并生成 CSV 的 CLI。
- `stock/etf/tests/test_industry.py`：单元及小型端到端测试。

CLI 支持显式输入日线、元数据、份额缓存和输出路径；`--skip-share-download` 用于完全
离线复现。默认输出为 `stock/etf/data/industry.csv`。

增量下载按每只 ETF 的生命周期交易日检查首尾和内部缓存缺口，连续缺口合并请求；
批次之间遵守统一限速。缓存和输出均通过同目录唯一临时文件原子替换，并发运行不会
共享固定临时路径。

## 9. 验收条件

- 份额只向后填充，无首次记录前市值。
- 份额变化、价格不变时行业指数保持不变。
- 行业总市值、成交额和成交量可由成分精确加总复核。
- 所有滚动统计严格要求完整窗口。
- 行业 OHLC 合法，键无重复，日期和分类排序稳定。
- 单元测试、Ruff、格式和编译检查全部通过。
- 真实 Tushare 份额下载完成，`industry.csv` 非空且覆盖缓存日期。
