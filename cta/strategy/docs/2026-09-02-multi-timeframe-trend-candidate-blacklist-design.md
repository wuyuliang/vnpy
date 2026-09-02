# 多周期趋势策略候选黑名单设计

## 1. 目标

在多周期趋势策略的候选生成阶段增加可编辑黑名单，默认彻底删除：

- `setup_type == "pullback_breakout"` 的候选；
- `direction == -1` 的全部空头候选。

默认最终只保留多头 `always_in` 候选。分钟数据下载流程保持不变，继续下载 1 分钟数据并在回测内部聚合 5 分钟信号 K 线。

## 2. 配置

在 `cta/config/multi_timeframe_trend_config.py` 的 `MultiTimeframeTrendConfig` 中增加：

```python
candidate_setup_blacklist: tuple[str, ...] = ("pullback_breakout",)
candidate_direction_blacklist: tuple[int, ...] = (-1,)
```

配置只接受已知候选类型 `always_in`、`pullback_breakout` 和方向 `-1`、`1`，重复值视为无效配置。后续需要恢复某类机会时，直接编辑配置默认值或构造配置实例，不增加 CLI 参数。

## 3. 候选生成顺序

`generate_multi_timeframe_candidates` 按以下顺序处理每根已完成的 5 分钟 K 线：

1. 读取已完成日线许可方向；
2. 如果方向在 `candidate_direction_blacklist` 中，跳过该方向的全部形态检测和候选输出；
3. 更新未被方向黑名单屏蔽方向的 Always-In 与回调状态；
4. 如果已识别候选的 `setup_type` 在 `candidate_setup_blacklist` 中，不加入候选列表；
5. 只在同一时刻存在两个未被黑名单删除的候选时应用原有重复信号规则。

因此，被删除的 `pullback_breakout` 不会令同一时刻的多头 `always_in` 被标记为 `DUPLICATE_SIGNAL`。

## 4. 输出语义

黑名单发生在候选 DataFrame 创建之前。被删除机会不会获得 `candidate_id`，也不会进入：

- `candidates.csv`；
- `rejections.csv`；
- `plans.csv`、`orders.csv`、`fills.csv`、`trades.csv`；
- 机会图表及其索引。

报告上下文记录当前候选类型黑名单和方向黑名单，保证每次回测结果可以复现。

## 5. 测试

回归测试至少覆盖：

- 默认配置只输出多头 `always_in`；
- 默认配置不输出 `pullback_breakout`；
- 默认配置不输出任何空头候选；
- 被删除的同刻回调突破不会压制可保留的多头 `always_in`；
- 清空黑名单后恢复原有多空和两类形态行为；
- 非法候选类型、非法方向和重复黑名单项会被配置校验拒绝；
- 回测报告上下文包含实际生效的黑名单。

## 6. 非目标

- 不修改 Tushare 下载频率；
- 不增加 5 分钟下载参数；
- 不改变入场、跟踪止损、仓位、手续费、保证金或图表逻辑；
- 不为已删除机会新增拒绝原因码。
