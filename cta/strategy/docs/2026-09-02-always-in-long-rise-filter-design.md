# Always-In 多头短线涨幅过滤设计

## 1. 目标

为 `always_in` 多头候选增加可配置的防追高过滤。若信号前最近一组已完成的 5 分钟阳线累计实体涨幅，相对历史日线阳线的平均实体涨幅过大，则保留该候选的审计记录，但禁止生成可执行计划。

该规则只作用于 `setup_type="always_in"` 且 `direction=1` 的候选，不改变空头和 `pullback_breakout` 规则。

## 2. 配置

在 `MultiTimeframeTrendConfig` 中新增：

```python
always_in_long_rise_filter_enabled: bool = True
```

- `True`：启用过滤，作为默认正式研究口径。
- `False`：完全跳过过滤及其历史不足检查。
- 不增加 CLI 参数；后续直接编辑配置文件。

窗口 `6` 复用 `always_in_window`。最近阳线数量固定为 `4`，日线阳线样本固定为 `5`，比较系数固定为 `0.5`，不增加未请求的配置项。

## 3. 指标定义

阳线统一定义为：

```text
close > open
```

单根 K 线实体涨幅定义为：

```text
body_return = (close - open) / open
```

### 3.1 5 分钟累计实体涨幅

在 `always_in` 信号所使用的最近 6 根已完成 5 分钟 K 线中，按时间从近到远选择最近 4 根阳线：

```text
recent_4_up_5m_return_sum = sum(body_return of latest 4 bullish bars)
```

信号 K 线已经完成，因此可以包含在这 6 根 K 线中。若窗口内不足 4 根阳线，拒绝条件不成立，不触发本过滤。

### 3.2 历史日线平均实体涨幅

在信号时刻之前已经完成的日线中，按时间从近到远选择最近 5 根阳线：

```text
prior_5_up_daily_return_mean = mean(body_return of latest 5 bullish daily bars)
```

当前尚未完成的日线不得参与计算。日线值通过 `bar_end` 向后对齐至 5 分钟信号，禁止使用未来日线。

## 4. 过滤规则

配置开启、候选为 `always_in` 多头且最近 6 根 5 分钟 K 线至少包含 4 根阳线时：

```text
rise_limit = 0.5 * prior_5_up_daily_return_mean

if recent_4_up_5m_return_sum > rise_limit:
    reject
```

严格使用大于号；等于阈值时允许候选继续进入后续过滤。

若此时不足 5 根历史日线阳线，无法计算比较基准，按保守原则拒绝候选：

```text
ALWAYS_IN_LONG_RISE_HISTORY_UNAVAILABLE
```

涨幅超过阈值时使用：

```text
ALWAYS_IN_LONG_RECENT_RISE_EXCESS
```

该原因进入既有 `filtered_reason`、`rejection_code`、`rejections.csv` 和机会图表原因目录。候选不会被静默删除。

## 5. 数据流与审计字段

1. `build_daily_context` 因果计算最近 5 根已完成阳线日 K 的平均实体涨幅。
2. `build_intraday_context` 在每根已完成 5 分钟 K 线上计算最近 6 根中最近 4 根阳线的数量和累计实体涨幅。
3. `_align_completed_daily` 将日线基准按完成时间向后对齐到 5 分钟信号。
4. `_candidate_row` 在现有日线突破过滤之后、障碍和风险过滤之前应用本规则。

候选表增加以下审计字段：

```text
recent_4_up_5m_return_sum
recent_6_5m_up_count
prior_5_up_daily_return_mean
always_in_long_rise_limit
```

## 6. 测试与验收

单元测试覆盖：

1. 配置默认开启，并接受显式关闭。
2. 日线平均值只使用信号前已完成的最近 5 根阳线。
3. 5 分钟指标只在最近 6 根中选择时间上最近的 4 根阳线。
4. 大于一半时以 `ALWAYS_IN_LONG_RECENT_RISE_EXCESS` 拒绝。
5. 等于一半时不因本规则拒绝。
6. 5 分钟窗口不足 4 根阳线时不触发。
7. 需要比较但不足 5 根历史日线阳线时以历史不足原因拒绝。
8. 配置关闭、空头或其他 setup 不受影响。
9. 审计字段随候选进入 CSV 和图表索引，不破坏现有因果时间测试。

完成标准为相关策略测试和完整 CTA 测试通过，中文主策略文档及 `cta/report/change_log.md` 同步更新。
