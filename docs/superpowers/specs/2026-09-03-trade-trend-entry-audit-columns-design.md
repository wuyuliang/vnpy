# 交易趋势首笔与突破比例审计列设计

## 目标

在多周期趋势策略最终生成的 `trades.csv` 中增加两列，记录每笔实际成交是否为当前多头趋势段的第一笔交易，以及候选入场触发价相对前五根已完成日线实体最高价的真实突破百分比。

新增字段只用于成交审计和后续分析，不改变候选过滤、订单撮合、仓位、退出、手续费、权益或已有趋势首笔突破缓冲规则。

## 输出字段

在现有 `cycle` 列之后依次插入：

```text
is_first_trade_in_trend_segment
trigger_to_prior_5d_high_ratio
```

`is_first_trade_in_trend_segment` 使用整数 `1/0`：

- `1` 表示该笔交易是当前品种当前 `daily_bull_trend_id` 内第一笔真实开仓成交。
- `0` 表示同一趋势段已有更早的真实开仓成交。
- 被过滤、未触发、撤单或因风控未能开仓的候选不算成交，不能把后续第一笔真实成交标记为非首笔。
- 趋势中断后产生新的 `daily_bull_trend_id`，新趋势段的第一笔真实成交重新标记为 `1`。
- 当前文档只定义多头趋势段；若未来恢复空头交易，在没有空头趋势段规格前，空头交易记录为 `0`。

`trigger_to_prior_5d_high_ratio` 保存百分数值，公式为：

```text
trigger_to_prior_5d_high_ratio = 100 * (trigger / prior_5d_high - 1)
```

例如 `trigger=100.10`、`prior_5d_high=100.00` 时写入 `0.1`，表示 `0.1%`。该列不再额外乘 `100` 后展示。

公式中的 `trigger` 和 `prior_5d_high` 使用 `_execution_candidates` 已转换到实际成交合约口径的候选值，不使用连续合约原始信号价，也不使用包含滑点的实际 `entry_price`。若任一输入不是有限数或 `prior_5d_high <= 0`，该列留空。

## 状态冻结与数据流

采用成交时冻结方案：

1. 候选通过过滤并真实匹配开仓时，事件回放检查仅供成交审计使用的 `traded_bull_trend_ids`；该集合与原突破缓冲过滤使用的 `filled_bull_trend_ids` 相互独立。
2. 候选方向为多头且其 `daily_bull_trend_id` 尚未出现真实开仓 fill 时，首笔标记为 `1`；否则为 `0`。
3. 只有 `_open_position` 成功后，才把当前趋势段加入 `traded_bull_trend_ids`；原 `filled_bull_trend_ids` 的更新条件保持不变。
4. `_open_position` 根据候选的实际合约 `trigger` 和 `prior_5d_high` 计算突破百分比，并把两个审计值冻结到 `_Position`。
5. `_close_position` 在最终逻辑交易汇总时把冻结值写入 trade 行。

单品种和组合回放使用相同字段和判断公式。部分隔夜减仓不会生成新的首笔判断；最终 `trades.csv` 仍是一笔逻辑交易一行，并保留开仓时冻结的值。

首笔标记独立于 `first_trend_entry_daily_breakout_buffer_ratio` 是否启用。即使配置为 `0`，仍按真实成交状态记录当前趋势段第一笔交易。

## 实现边界

- 在 `_Position` 增加两个不可变开仓审计属性。
- 在单品种和组合回放的两个 `_open_position` 调用点传入开仓前计算的首笔标记。
- 在 `TRADE_COLUMNS` 和最终 trade 字典中增加两个字段。
- `report.py` 沿用现有 `ReplayArtifacts.trades` 写盘逻辑，不重新计算字段。
- 不在 `exit_legs.csv` 增加这两个字段，因为需求只针对最终逻辑交易 `trades.csv`。
- 不根据 `trades.csv` 的排序后处理首笔状态，避免把撤单、同时间事件或趋势切换解释错误。

## 测试

1. 两个新增列严格位于 `cycle` 后，且最终写出的 `trades.csv` 保持相同列序。
2. 单品种回放中，同一多头趋势段第一笔真实成交记录 `1`，后续交易记录 `0`。
3. 多头趋势中断并进入新 `daily_bull_trend_id` 后，第一笔真实成交重新记录 `1`。
4. 组合回放按品种独立维护首笔状态，AG 的成交不影响 AU。
5. 被过滤、撤单或未成交候选不消耗趋势首笔状态。
6. 缓冲参数为 `0` 时仍正确记录首笔状态。
7. 验证 `trigger=100.10`、`prior_5d_high=100.00` 时比例约为 `0.1`。
8. 验证比例使用候选 `trigger` 而非含滑点的实际 `entry_price`。
9. 验证无效价格产生空值，部分减仓后的最终交易仍保留开仓时冻结值。
10. 运行全部多周期趋势策略测试，确认交易、收益和权益行为不变。
