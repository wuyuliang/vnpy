# Always-In 多头日线 EMA 间距过滤设计

## 目标

为大周期定方向、小周期入场策略增加一个配置参数。仅当 `always_in` 多头候选满足以下条件时，才允许进入计划和订单链路：

```text
(EMA5 - EMA20) / EMA20 >= 0.02
```

默认阈值为 `2%`，只在配置文件中维护，不增加 CLI 参数。

## 配置

在 `MultiTimeframeTrendConfig` 中新增：

```python
always_in_long_daily_ema_gap_min_ratio: float = 0.02
```

配置值必须是有限数且位于 `[0, 1)`。`0` 可以关闭实际过滤效果，同时保留同一条计算路径。

## 因果与过滤规则

过滤使用候选决策时刻已经完成并对齐的日线 `daily_ema5` 和 `daily_ema20`：

```text
daily_ema_gap_ratio = (daily_ema5 - daily_ema20) / daily_ema20
```

规则只应用于 `setup_type == "always_in"` 且 `direction > 0` 的候选。恰好等于阈值时放行；低于阈值时保留候选审计记录，但设置：

```text
filtered_reason = DAILY_EMA_GAP_BELOW_MIN
rejection_code = DAILY_EMA_GAP_BELOW_MIN
quantity = 0
```

该候选不会进入计划、订单或成交。`pullback_breakout` 与空头候选不受本规则影响。

新原因的优先级位于元数据有效性之后、五日日线实体突破过滤之前。现有重复信号原因继续保持最高优先级。

## 审计与文档

现有候选字段已经包含 `daily_ema5` 和 `daily_ema20`，不新增重复的计算字段。回测 `summary.json` 记录本次阈值，确保未暴露到 CLI 的配置仍可复现。机会图表沿用按拒绝原因分目录的逻辑，生成 `DAILY_EMA_GAP_BELOW_MIN/` 目录。

中文策略文档同步增加参数、公式、适用范围和拒绝原因说明。

## 测试

1. 默认配置值为 `0.02`，非法值被拒绝。
2. `always_in LONG` 间距低于 `2%` 时被 `DAILY_EMA_GAP_BELOW_MIN` 过滤。
3. 间距恰好等于 `2%` 时通过该过滤。
4. `pullback_breakout LONG` 和空头候选不受该规则影响。
5. 回测摘要记录阈值，现有策略和回测测试保持通过。

