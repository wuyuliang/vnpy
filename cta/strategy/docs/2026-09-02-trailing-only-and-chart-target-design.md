# 回调突破纯跟踪止损与全机会 Target 线设计

## 1. 目标

1. `pullback_breakout` 取消可执行的 2R 固定止盈。
2. `pullback_breakout` 与 `always_in` 都使用已确认摆动点更新跟踪止损。
3. 每张日线、1 小时和 5 分钟机会图都显示 Target 参考线。
4. Target 参考线只用于复盘，不参与订单、保护性退出或绩效归因。
5. 回测引擎与独立持仓管理模块保持一致。

## 2. 退出规则

开仓后，`pullback_breakout` 的初始止损仍为突破前区间下沿或上沿。两类持仓随后都调用相同的 `advance_trailing_stop`：

1. 只使用在当前决策时点已经确认的摆动点。
2. 多头止损只能抬高，空头止损只能降低。
3. 新结构的 `known_at` 必须晚于上次已处理结构。
4. 不设置有限的可执行目标价，因此价格达到 2R 时不能产生 `TARGET` 退出。
5. 其他退出原因保持不变，包括止损、日线方向失效、合约切换、区间结束和隔夜保证金减仓。

同一分钟不再存在止损与固定目标同时触发的歧义；保护性退出只检查跟踪止损。

## 3. Target 图表参考线

为所有候选计算 2R 虚拟参考目标：

```text
virtual_target = entry_reference + direction * 2 * abs(entry_reference - stop)
```

其中：

1. 未成交机会使用信号触发价作为 `entry_reference`。
2. 已成交机会使用实际成交价重新计算，使图表反映跳空和滑点后的真实风险距离。
3. 沿用配置中的 `pullback_target_r` 数值作为图表 R 倍数，但该参数不再控制止盈。
4. `target_price` 保持为空，`target_price_virtual` 保存信号时参考线。
5. `final_target` 保存已成交机会按实际入场价计算的虚拟参考线，并增加字段明确其不可执行。

图表优先使用已成交机会的 `final_target`，否则使用 `target_price_virtual`。日线、1 小时和 5 分钟三个面板必须绘制同一条 Target 水平线，标题区域同时显示该数值。

## 4. 数据与报告

`plans.csv` 中的 `target` 改为虚拟参考目标，不代表实际退出委托。`trades.csv` 中的 `final_target` 同样为图表参考值，并通过 `target_exit_enabled = 0` 明确没有目标止盈。

`exit_reason` 不得再因价格达到虚拟 Target 而出现 `TARGET`。历史报告不回写，新报告按新语义生成。

## 5. 测试与验收

1. 回调突破价格达到 2R 后不退出，继续持仓直到跟踪止损或其他有效退出。
2. 回调突破出现新的已确认摆动点后，止损按方向单调移动。
3. 独立持仓管理模块和回测引擎得到相同的跟踪止损结果。
4. 所有候选均有有限的 `target_price_virtual`。
5. 已成交机会按实际成交价得到 `final_target`，但 `target_exit_enabled` 为 0。
6. 日线、1 小时和 5 分钟面板都收到有限 Target 价格并绘制 Target 线。
7. 相关策略、回放、报告和图表测试全部通过。
