# ETF 行业日频离线汇总优化设计

## 1. 目标

在当前 Tushare token 没有 `etf_share_size` 权限时，仍可完全离线生成
`stock/etf/data/industry.csv`。默认构建不请求 ETF 份额，不输出无法诚信计算的真实市值
字段；行业价格指数改用上一行业交易日成交额加权，并继续输出成交统计和技术指标。

## 2. 输入与范围

- ETF 日线：`stock/etf/data/20260717_2018_20260717_point_in_time_live/etfs_lifecycle_clean.csv`。
- ETF 元数据：同目录 `metadata.csv`，使用现有 `industry` 分类。
- 输出范围与显式 `--start/--end` 或日线有效范围一致。
- 保留全部 21 个现有行业分类，包括 `broad_or_other`。
- 默认路径不读取 `etf_share_size.csv`，也不创建 Tushare 客户端。

现有份额标准化和下载代码保留为独立、已测试的未来能力，但不参与默认行业文件构建。

## 3. 行业基础汇总

每个 `datetime, industry` 输出：

- `industry_name`：行业中文名称。
- `etf_count`：当日有有效日线的行业 ETF 数。
- `daily_turnover`：当日行业 ETF 成交额之和，单位元，仅作为滚动指标中间列。
- `daily_volume`：当日行业 ETF `volume × 100` 之和，单位为前复权口径份，仅作为滚动
  指标中间列。

删除默认输出中的 `market_value_etf_count`、`share_coverage_ratio` 和
`total_market_value`。不使用发行份额、前复权收盘价或其他近似值填充这些字段。

## 4. 上一交易日成交额加权行业指数

每个行业建立起点为 100 的价格指数。在行业交易日 `T`，ETF 权重只来自该行业严格上
一交易日 `T-1` 的成交额：

```text
weight_i,T = turnover_i,T-1 / sum(turnover_T-1)
field_relative_i,T = field_i,T / pre_close_i,T
industry_field_T = industry_close_T-1
                   × sum(weight_i,T × field_relative_i,T)
```

`field` 分别为 `open/high/low/close`。ETF 必须满足：

- 在行业上一交易日和当日都有日线，禁止使用停牌前陈旧成交额。
- 上一交易日成交额为正且有限。
- 当日 `pre_close` 和 OHLC 均为正且有效。

符合条件的 ETF 在行业内重新归一化。新上市 ETF 至少有一个严格上一行业交易日成交额
后才参与。若某日无有效成分，行业 OHLC 留空；下一有效日继续以上一有效行业收盘指数
为基准，但技术指标按新连续片段重新预热。

该构造强制 `high >= max(open, close)`、`low <= min(open, close)`。

## 5. 成交统计与技术指标

周期统一为 `1/3/5/10/20/60/180`：

- `turnover_sum_N`：最近 N 个行业交易日成交额合计，完整窗口才有效。
- `volume_sum_N`：最近 N 个行业交易日前复权口径成交量合计，完整窗口才有效。
- `ema_N`：成交额加权行业收盘指数 EMA，连续 N 个有效 OHLC 后有效。
- `atr_N`：成交额加权行业 OHLC Wilder ATR。

ADX 输出周期为 `3/5/10/20/60/180`，不输出 `adx_1`。行业 OHLC 缺失时，该日所有
EMA/ATR/ADX 为空；后续从新的连续有效价格片段重新预热。流动性滚动不因价格缺口
重置。

## 6. 输出模式

`industry.csv` 按以下稳定顺序输出：

```text
datetime, industry, industry_name, etf_count,
open, high, low, close,
turnover_sum_1 ... turnover_sum_180,
volume_sum_1 ... volume_sum_180,
ema_1 ... ema_180,
atr_1 ... atr_180,
adx_3 ... adx_180
```

文件按 `datetime, industry` 升序，键唯一。金额和数量保留数值类型。

## 7. 代码优化

- 将不依赖份额的行业分类、流动性汇总和成交额加权指数合并为一次 ETF 日线准备流程，
  避免对百万行日线重复执行份额 as-of 合并。
- `build_industry_csv` 默认只加载日线和元数据，删除默认路径中的份额缓存扫描、下载请求
  和限速等待。
- 保留原子 CSV 替换和并发唯一临时文件。
- 份额下载器继续留在 `data.py`，但默认构建不得实例化或调用它。

## 8. CLI

默认命令：

```bash
python3 -m stock.etf.build_industry_data
```

保留 `--daily`、`--metadata`、`--output`、`--start`、`--end`。移除默认构建所需的
`--share-cache` 和 `--skip-share-download` 参数，避免给人仍需份额权限的误导。

## 9. 验收条件

- 默认命令在无网络、无 `etf_share_size.csv` 时成功生成非空 `industry.csv`。
- 输出不包含三个市值相关字段。
- 所有指数权重严格来自上一行业交易日成交额，无停牌陈旧权重和当日权重。
- 行业 OHLC 合法；价格缺口日技术指标为空并重新预热。
- 滚动窗口、EMA、ATR、ADX 与参考实现逐周期一致。
- 对真实 1,047,360 行、1,302 只 ETF、21 个行业缓存完成构建并输出质量摘要。
- 全部 ETF 测试、Ruff lint/format 和编译检查通过。
