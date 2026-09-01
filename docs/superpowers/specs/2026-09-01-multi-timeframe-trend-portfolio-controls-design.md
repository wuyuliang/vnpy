# 多周期趋势策略组合风控与回测审计设计

## 目标

将 `cta.strategy.multi_timeframe_trend_backtest.runner` 从单品种回放升级为多品种共享账户回放，并完成三项审计增强：组合保证金限额、按结果分类的 `1d/1h/5min` 机会图、按实际开平类型和生效日期计算的手续费。

本回测仍属于指定区间研究结果，不声明为样本外结果。任何品种缺少历史合约、费率、保证金、交易时段或涨跌停元数据时，整个请求区间保持 `BLOCKED_METADATA`，不得用当前参数回填。

## 命令与配置

- `--symbols` 支持多个显式品种。
- `--top-n N` 复用现有排名表和下载选择逻辑，将排名前 N 个品种与显式品种合并去重后放入同一个共享账户回放；该参数继续要求 `--download-minute-data`。
- `--max-concurrent-positions` 默认 `5`，限制同时持仓的品种数。
- `--intraday-margin-utilization` 默认 `0.60`。
- `--overnight-margin-utilization` 默认 `0.20`。
- `--max-symbol-margin-utilization` 默认 `0.40`。
- `--overnight-reduction-minutes` 默认 `30`。
- 所有非默认参数都进入 `reproduction_command.json` 和运行摘要。

## 共享账户回放

每个品种继续独立完成数据标准化、日线方向、5 分钟候选和实际合约映射。组合层把各品种一分钟事件按 `bar_end` 合并，共享现金、盯市净值、待单和持仓字典。

同一时间戳的处理顺序固定为：

1. 保护性退出和日线方向失效退出；
2. 收盘前隔夜限额减仓；
3. 已有停止单触发；
4. 新候选提交。

并列事件按品种代码、候选编号稳定排序。每个品种最多一笔待单或持仓，不加仓；组合持仓品种数默认不超过 5。

## 保证金限额

保证金占用按当前可见价格计算：

```text
margin = quantity * marked_price * contract_multiplier * side_margin_rate
utilization = margin / marked_equity
```

开仓成交前将数量同时向下裁剪到以下上限：

- 日内组合总保证金不超过净值的 60%；
- 单品种保证金不超过净值的 40%；
- 同时持仓品种数不超过 5。

裁剪后不足一手时分别记录 `PORTFOLIO_MARGIN_LIMIT`、`SYMBOL_MARGIN_LIMIT` 或 `MAX_CONCURRENT_POSITIONS`，不得缩小结构止损。

每个品种根据其有效交易时段确定日盘收盘时刻。收盘前 30 分钟取消该品种新开待单，并启动隔夜检查。若预计留仓总保证金超过净值的 20%，按最新开仓优先、品种代码稳定排序整笔退出，直到不超过 20%。首版不拆分部分手数，允许实际占用保守地低于 20%。退出原因记录为 `OVERNIGHT_MARGIN_REDUCTION`。

## 手续费

执行元数据保留每个生效日的开仓、平仓、平今费率和每手固定费用。手续费按实际成交价和成交时点计算：

```text
open_fee = entry_price * multiplier * open_fee_rate + fee_per_lot_open
close_fee = exit_price * multiplier * selected_close_rate + selected_fixed_close_fee
```

入场与退出的 `exchange_trade_date` 相同使用平今参数，否则使用普通平仓参数。开仓费取入场日费率，平仓费取退出日费率，不能继续使用入场快照的一次往返费用。风险测算仍使用开仓日可见的开仓费加 `max(平仓费, 平今费)`，避免低估计划风险。

报告新增逐笔费用字段和 `fee_audit.csv`，至少包含开仓费、平仓费、平仓类型、两端费率、固定费用、合约乘数和元数据生效标识。历史费率看起来偏低时只披露来源和公式，不人为提高费率。

## 图表与结果目录

每个候选机会生成一张包含 `1d`、`1h`、`5min` 三个面板的图。`1h` 使用实际分钟数据和有效交易时段聚合，不使用自然小时跨休市拼接。

候选按 `signal_time` 全局稳定排序，文件名为：

```text
<sequence>_<symbol>_<YYYYMMDD_HHMMSS>_<setup>_<direction>.png
```

图表目录按最终结果分类：

- 成交并形成退出记录：`opportunity_charts/TRADED/`；
- 未成交：`opportunity_charts/<reason_code>/`，每个原因一个目录。

结果判定依次查看成交、拒绝、最终订单状态、候选过滤原因，仍无法解释时使用 `NOT_FILLED_UNKNOWN`。`opportunity_charts/index.csv` 增加 `outcome_code` 和 `chart_path`，所有候选必须且只能出现一次。

## 报告与验证

组合报告保留收益、最大回撤、胜率、盈亏比、交易次数，并增加每日总保证金利用率、隔夜利用率、单品种峰值、最大同时持仓数、风险拒绝数量及分品种业绩。

测试覆盖：配置校验、跨品种资金竞争、数量向下裁剪、最大持仓数、收盘前 30 分钟整笔减仓、平仓/平今及跨费率生效日手续费、三周期图、结果目录、文件排序和复现命令。最终运行聚焦测试、完整相关测试、Ruff、编译检查以及 AG 和一个小型多品种代表性回测。
