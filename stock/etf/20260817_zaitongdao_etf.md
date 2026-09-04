# 159915.SZ 周线窄通道多空快速验证方案

## 0. 文档状态

- 本文件是 `20260817_zaitongdao.md` 的独立最小版，只用于快速判断策略是否值得继续。
- 只分析 `159915.SZ` 易方达创业板 ETF，不包含其他 ETF 或 CTA 品种。
- 当前状态：**已实现并完成首次快速回测（2026-08-08）**。
- 快速版结果全部标记为 `research_only`，不能直接用于实盘下单。

## 1. 验证目标

只回答三个问题：

1. 完整周线形成上升窄通道后，日线回调恢复做多是否有正期望。
2. 完整周线形成下降窄通道后，日线反弹结束做空是否有正期望。
3. 不设置固定止盈，使用趋势失效和慢速跟踪止损，能否保留大趋势的主要利润。

快速版不包含：反转抄底/摸顶、仓位管理、组合净值、参数寻优、融券账户建模、实时交易和
数据下载。若结果有价值，再回到完整版补齐执行细节。

## 2. 数据

直接读取现有文件，不重新下载：

```text
stock/etf/output/20260727_chuangyeban_regime_overlay/signals.csv
stock/etf/output/20260727_chuangyeban_regime_overlay/source_audit.json
```

只使用以下列，忽略文件中已有信号和状态列：

```text
symbol, datetime, open, high, low, close, volume
```

当前可验证区间为 `2017-08-14` 至 `2026-07-20`，价格口径为
`point_in_time_adjusted`。由于快速版没有逐日未复权价格、真实券源和费率：

- 多头和空头都只输出研究结果。
- 空头用于验证方向逻辑，不代表历史上实际能够融券卖出。
- 所有收益以每笔交易的净 `R` 表示，不输出人民币盈亏和组合资金曲线。

周 K 按 `W-FRI` 聚合。只使用周期结束日不晚于数据截止日的周，末尾不完整周直接丢弃。
根据冻结数据中的交易日历，周五休市时以该周实际最后交易日作为周状态可用日，同时保留名义
周五作为审计字段；日线信号只能读取信号日已经完成且可用的周 K。

## 3. 最小策略规则

### 3.1 固定参数

| 参数 | 默认值 |
| --- | ---: |
| 周线通道窗口 | 6 个完整周 |
| 周线确认 | 连续 2 个合格窗口 |
| 周线指标 | EMA10、Wilder ATR14 |
| 日线指标 | EMA5、EMA10、EMA20、Wilder ATR14 |
| 次日最大追价 | 0.75 ATR14D |
| 初始最小止损距离 | 1.50 ATR14D |
| 跟踪止损启动 | 2R |
| 单边成交成本 | 0.08% |
| 空头假设年化借券费 | 8% |

第一轮只跑这一组参数，不寻优。

任一必需指标尚未形成时不生成状态或信号。

Wilder ATR 的第 14 个有效值以最初 14 个 TR 的算术平均数为种子，后续按
`ATR_t = (13 * ATR_t-1 + TR_t) / 14` 递推，不使用 pandas EWM 的默认初始化。

### 3.2 周线上升窄通道

最近 6 个完整周同时满足：

```text
5 次周收盘变化中至少 4 次上涨
最后收盘 - 第一周收盘 >= 1.0 * 当前 ATR14W
max(running_max_close_i - close_i) <= 1.0 * 当前 ATR14W
最后收盘 > EMA10W
当前 EMA10W > 两周前 EMA10W
```

连续两个滚动窗口都合格，状态为 `UP_CHANNEL`；任一窗口不合格立即回到 `NEUTRAL`，快速版
不设置 grace 周。

### 3.3 周线下降窄通道

规则方向对称：

```text
5 次周收盘变化中至少 4 次下跌
第一周收盘 - 最后收盘 >= 1.0 * 当前 ATR14W
max(close_i - running_min_close_i) <= 1.0 * 当前 ATR14W
最后收盘 < EMA10W
当前 EMA10W < 两周前 EMA10W
```

连续两个窗口合格，状态为 `DOWN_CHANNEL`。

### 3.4 日线做多信号

`signal_type=up_channel_pullback_long`。交易日 `T` 收盘同时满足：

```text
最近完整周状态为 UP_CHANNEL
EMA5D > EMA10D > EMA20D
最近 5 日至少有 2 日收盘下跌
最近 5 日最低价 <= EMA10D_T + 0.25 * ATR14D_T
最近 5 日所有收盘 >= 对应 EMA20D - 0.50 * ATR14D_T
close_T > open_T
close_T > high_T-1
```

### 3.5 日线做空信号

`signal_type=down_channel_rally_short`，与做多规则对称：

```text
最近完整周状态为 DOWN_CHANNEL
EMA5D < EMA10D < EMA20D
最近 5 日至少有 2 日收盘上涨
最近 5 日最高价 >= EMA10D_T - 0.25 * ATR14D_T
最近 5 日所有收盘 <= 对应 EMA20D + 0.50 * ATR14D_T
close_T < open_T
close_T < low_T-1
```

### 3.6 次日开盘入场

信号在 `T` 收盘形成，只允许在 `T+1` 开盘入场：

```text
LONG:
  max_entry = close_T + 0.75 * ATR14D_T
  只有 long_structure_stop < open_T+1 <= max_entry 时成交

SHORT:
  min_entry = close_T - 0.75 * ATR14D_T
  只有 min_entry <= open_T+1 < short_structure_stop 时成交
```

不满足条件则记录 `skipped_gap_or_invalidated`，不在盘中追价。买入/回补价格乘
`1.0008`，卖出/开空价格乘 `0.9992`。空头另外按持有自然日计提 8%年化假设借券费。
同一时间最多持有一笔交易；持仓期间忽略新入场信号，不在同一开盘反手。

## 4. 止损与退出

### 4.1 初始止损

信号日 `T` 收盘先冻结结构位和 ATR：

```text
LONG:
  structure_low = min(low_T, ..., low_T-5)
  long_structure_stop = structure_low - 0.25 * ATR14D_T

SHORT:
  structure_high = max(high_T, ..., high_T-5)
  short_structure_stop = structure_high + 0.25 * ATR14D_T
```

`T+1` 开盘满足第 3.6 节条件并成交后，只使用已冻结的结构位、ATR 和实际成交价立即确定
最终止损：

```text
LONG_initial_stop = min(long_structure_stop,
                        entry_fill - 1.50 * ATR14D_T)
SHORT_initial_stop = max(short_structure_stop,
                         entry_fill + 1.50 * ATR14D_T)

LONG_R  = entry_fill - LONG_initial_stop
SHORT_R = SHORT_initial_stop - entry_fill
```

成交后的初始止损不得再放宽。

### 4.2 ETF T+1 处理

`159915.SZ` 开仓当日不能平仓。若开仓日已经触及止损：

1. 保留已成交的入场，不虚构同日退出。
2. 记录 `entry_day_stop_breached=true`。
3. 下一交易日开盘无条件退出，即使价格已经回到止损内。

从下一交易日起，开盘跳过止损按更差开盘退出；否则日内触价按止损价退出。

### 4.3 趋势退出

除硬止损外，只保留两个收盘退出条件，均在下一交易日开盘执行：

```text
LONG:
  连续 2 日 close < EMA20D 且 EMA5D < EMA10D
  或完整周出现：close < open、实体 >= 0.80 ATR14W、close < 前周 low

SHORT:
  连续 2 日 close > EMA20D 且 EMA5D > EMA10D
  或完整周出现：close > open、实体 >= 0.80 ATR14W、close > 前周 high
```

### 4.4 盈利跟踪

不设置固定止盈。盘中最大有利波动达到 `2R` 后，从下一交易日启用周线跟踪止损：

```text
LONG_stop = max(旧止损, 含成本保本价,
                最近 2 个完整周最低价 - 0.25 * ATR14W)

SHORT_stop = min(旧止损, 含成本保本价,
                 最近 2 个完整周最高价 + 0.25 * ATR14W)
```

多头止损只能上移，空头止损只能下移。数据结束时仍持仓的交易标记为 `OPEN`，不强制平仓，
未实现 `R` 单独披露。

退出优先级固定为待执行 T+1 止损、有效硬止损、趋势退出。同一日既触及止损又达到 `2R`
时按止损优先，避免用日线 OHLC 猜测有利的盘中先后顺序；同一交易只生成一笔退出。

## 5. 最小输出

固定输出目录：

```text
stock/etf/output/20260817_zaitongdao_etf/
```

只生成四类产物：

- `signals.csv`：周线状态、两类日线信号和次日是否成交。
- `trades.csv`：入场、初始止损、退出、成本、MFE、MAE 和净 `R`。
- `summary.json`：多头、空头和合并统计。
- `charts/159915.SZ_周线窄通道快速验证.png`：同一张图包含周线通道、日线 K 线、
  EMA5/EMA10/EMA20、成交量、做多/做空、止损和退出；图形只展示最近两年，CSV 保留全期。

`summary.json` 至少报告：信号数、成交数、跳过数、完成交易数、胜率、总净 `R`、平均净
`R`、Profit Factor、最大累计 `R`回撤、中位持有日、借券费，以及 `MFE>=4R` 大趋势交易的
利润捕获率。单笔 `profit_capture_ratio=realized_net_R/MFE_R`；多头和空头必须分开显示。

## 6. 快速验收

必须通过：

1. 修改任一信号日之后的数据，不改变该日及以前的周线状态和信号。
2. 末尾不完整周不会生成通道状态。
3. 多空规则严格镜像，信号只在下一交易日开盘尝试成交。
4. 开仓日触发止损时只记录待退出，下一交易日才成交。
5. 每笔交易的入场、止损、退出、成本和净 `R` 可以从 CSV 复算。
6. PNG 中的信号和交易日期与 CSV 一致。

结果判定预先固定：

- 每个方向独立判定：完成交易少于 10 笔记为 `INCONCLUSIVE`。
- 样本足够时，总净 `R>0`、Profit Factor `>1.10`、最大累计回撤不超过 `12R`，该方向记为
  `PROMISING`；否则记为 `REJECTED`，不再给合并结果单独贴通过标签。
- 对 `MFE>=4R` 的完成交易，若至少有 5 笔，则中位利润捕获率需达到 35%；不足 5 笔只
  披露样本不足，不据此调参数。

## 7. 后续边界

评审通过后，第一轮实现仍只允许修改 `stock/**`，并只实现本文件所需的单 symbol
研究脚本、测试和上述四类输出。不得顺手接入 CTA、其他 ETF、真实融券账户或参数优化。

本快速版验证通过，只说明信号与退出值得继续研究；要形成可执行策略，仍需回到
`20260817_zaitongdao.md` 补齐未复权成交价、逐日券源、真实费率和账户约束。

## 8. 首次回测结果（2026-08-08）

执行命令：

```bash
python3 -m stock.etf.run_narrow_channel_etf --overwrite
```

输入共 2,166 根日 K，范围为 `2017-08-14` 至 `2026-07-20`，最后完整周为
`2026-07-17`。输出位于 `stock/etf/output/20260817_zaitongdao_etf/`。

| 方向 | 信号 | 成交 | 完成交易 | 总净 R | Profit Factor | 最大回撤 | 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 多头 | 45 | 12 | 12 | 1.610 | 1.245 | 5.049R | `PROMISING` |
| 空头 | 19 | 9 | 9 | -6.501 | 0.009 | 6.501R | `INCONCLUSIVE` |
| 合并 | 64 | 21 | 21 | -4.892 | 0.628 | 10.925R | `NOT_APPLICABLE` |

多头胜率为 25.0%，空头胜率为 11.1%，合并胜率为 19.0%。全样本仅有 1 笔完成交易达到
`MFE>=4R`，其中位
利润捕获率为 66.57%，按预设规则仍属于样本不足，不能据此调整参数。多头结果达到第一轮
继续研究门槛；空头虽然当前结果较差，但只有 9 笔完成交易，必须保持 `INCONCLUSIVE`，
不能提前判为通过或淘汰。合并统计只用于观察，不设置通过标签。

实现文件：

- `narrow_channel_etf_strategy.py`：数据校验、指标、信号、T+1 事件循环和统计。
- `narrow_channel_etf_chart.py`：周线、日线、全部信号、成交、退出、止损和成交量图。
- `run_narrow_channel_etf.py`：输入审计、原子目录写入和命令行入口。

参考规则：

- [深交所基金交易规则](https://docs.static.szse.cn/www/fund/guide/W020220610536157903323.pdf)
- [深交所融资融券交易实施细则](https://docs.static.szse.cn/www/lawrules/rule/trade/business/margin/W020230217568325595241.pdf)
