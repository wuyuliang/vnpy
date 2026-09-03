# 分品种绩效与入场前市场流动性设计

## 目标

在多周期趋势策略的回测输出目录增加一份每品种一行的绩效文件，并在最终 `trades.csv` 中补充每笔交易入场前的主力合约市场流动性。新增内容只属于报告层，不改变候选、撮合、仓位、退出、手续费、权益和已有风控逻辑。

## 输出文件

新增：

```text
performance_by_symbol.csv
daily_equity_curve.png
```

该文件对每个已加载品种输出一行，按 `symbol` 升序排列。即使某品种没有成交，也保留一行，交易数和金额类指标写 `0`，没有定义的胜率与比率写空值。

字段依次为：

```text
symbol
trade_count
winning_trade_count
losing_trade_count
breakeven_trade_count
win_rate_pct
total_gross_pnl
total_net_pnl
total_return_pct
max_drawdown_amount
max_drawdown_pct
profit_factor
average_win_loss_ratio
average_win
average_loss
expectancy_R
fees
slippage
turnover
```

## 分品种绩效口径

每个品种只使用最终逻辑交易 `trades.csv` 中属于该品种的记录，按 `exit_time`、`candidate_id` 稳定排序。

- `trade_count`：最终平仓的逻辑交易笔数。
- `winning_trade_count`、`losing_trade_count`、`breakeven_trade_count`：分别统计 `net_pnl > 0`、`net_pnl < 0`、`net_pnl == 0`。
- `win_rate_pct = 100 * winning_trade_count / trade_count`。
- `total_gross_pnl`、`total_net_pnl`、`fees`、`slippage`、`turnover`：对应逐笔字段之和。
- `total_return_pct = 100 * total_net_pnl / initial_equity`。这里使用回测组合总初始资金，因此该字段表示品种对组合收益率的贡献，不假设不存在的品种独立资金账户。
- `profit_factor = 正收益交易的 net_pnl 之和 / 负收益交易的 net_pnl 绝对值之和`。
- `average_win_loss_ratio = 正收益交易的平均 net_pnl / 负收益交易的平均 net_pnl 绝对值`。
- `average_win`、`average_loss`：正收益和负收益交易的平均 `net_pnl`；`average_loss` 保留负号。
- `expectancy_R`：该品种逐笔 `net_r` 的算术平均值。

最大回撤采用已确认的平仓权益口径。令按最终平仓顺序排列的净收益为 `pnl_i`：

```text
cumulative_pnl_0 = 0
cumulative_pnl_i = sum(pnl_1 ... pnl_i)
high_water_i = max(0, cumulative_pnl_1 ... cumulative_pnl_i)
drawdown_amount_i = high_water_i - cumulative_pnl_i
max_drawdown_amount = max(drawdown_amount_i)
max_drawdown_pct = 100 * max_drawdown_amount / initial_equity
```

该口径不包含持仓期间尚未实现的浮动盈亏。没有交易时，最大回撤金额和回撤率均为 `0`。

## `trades.csv` 流动性字段

在现有 `return_30min` 后依次增加：

```text
prior_5d_avg_market_volume
prior_5d_avg_market_turnover
prior_10d_avg_market_volume
prior_10d_avg_market_turnover
prior_20d_avg_market_volume
prior_20d_avg_market_turnover
```

这些字段来自该品种逐日主力合约实际行情链，不是策略自身成交量。`volume` 与 `turnover` 从已经完成全部交易时段的分钟行情按交易所交易日求和；夜盘归属其对应的交易所交易日。`turnover` 保留源行情单位，不额外缩放。

对每笔交易，先通过实际入场分钟匹配其 `exchange_trade_date`，再按品种匹配入场交易日前的已完成日线：

```text
prior_Nd_avg_market_volume
    = 入场交易日前最近 N 个完整交易日 volume 的算术平均值

prior_Nd_avg_market_turnover
    = 入场交易日前最近 N 个完整交易日 turnover 的算术平均值
```

入场当天不进入窗口，避免使用入场之后才产生的行情。窗口必须完整；不足 `N` 个交易日，或窗口内对应字段存在无效值时，该字段写空值。换月时按品种主力连续链滚动，不要求 N 天都属于同一实际合约。

## 数据流与边界

1. `runner.py` 继续使用现有预热分钟数据生成完整的实际合约日线，并保留 `symbol`、`exchange_trade_date`、`volume`、`turnover`。
2. `runner.py` 仅为已成交交易抽取实际入场分钟对应的交易所交易日，避免把整份分钟数据复制到报告层。
3. `report.py` 在写盘前计算滚动市场流动性，并将带六个新增字段的交易表写入 `trades.csv`。
4. `report.py` 基于同一交易表生成 `performance_by_symbol.csv`。
5. 现有 `summary.json`、`report.md`、`performance_by_group.csv` 和机会图表逻辑不改变；新增逐笔字段不能影响组合正式绩效。
6. 空回测仍生成只含表头的 `performance_by_symbol.csv`；若已有加载品种但没有交易，则按上述零交易规则保留品种行。

## 每日权益图

`daily_equity_curve.png` 只使用最终写出的 `daily_equity.csv` 中的 `date`、`equity` 和报告参数 `initial_equity` 生成，不重新计算权益：

```text
equity_pct = 100 * equity / initial_equity
```

- 横轴为交易所交易日日期，按自然日期每隔 `5` 天显示一个刻度，标签采用 `YYYY-MM-DD`。
- 纵轴显示初始资金百分比并带 `%`，初始资金对应 `100%`；纵轴刻度根据实际数据范围自动等距分布。
- 绘制每日权益折线、点状水平网格和 `100%` 初始资金基准线，不增加其他绩效曲线或交易标记。
- 图片使用静态 PNG 和无界面绘图后端，回测命令无需新增参数。
- 当 `daily_equity.csv` 为空时仍生成同名图片，但只显示无可用每日权益数据的提示，不绘制虚假曲线。

## 测试

1. 验证六个流动性字段严格位于 `return_30min` 后。
2. 验证夜盘入场使用交易所交易日，而不是自然日。
3. 验证入场日行情被严格排除，5/10/20 日窗口只使用此前完整交易日。
4. 验证窗口不足时写空值，窗口完整时成交量和成交额均值正确。
5. 验证换月前后的主力合约日线按品种连续计算。
6. 验证 `performance_by_symbol.csv` 每个已加载品种一行，包含零交易品种。
7. 验证交易笔数、净收益、收益率、胜率、利润因子、平均盈亏比和期望 R 公式。
8. 验证累计净收益先盈利后亏损、开局连续亏损等情况下的最大回撤金额和百分比。
9. 验证最终写出的 CSV 列序与内存结果一致。
10. 验证每日权益图使用 `equity / initial_equity`、每 5 个自然日日期刻度和 `100%` 基准线，并能处理空权益表。
11. 运行全部多周期趋势策略测试，确认回测交易行为和正式组合绩效不变。
