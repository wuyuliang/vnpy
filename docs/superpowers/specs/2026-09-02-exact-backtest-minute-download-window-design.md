# 回测分钟数据精确下载区间设计

## 目标

多周期趋势和 cycle_v1 回测启用 `--download-minute-data` 时，分钟下载器只下载 CLI `--start` 至 `--end` 的自然日数据。删除固定的 `2025-08-30..2026-07-27` 下载限制，使较早或较晚的回测区间可以按请求日期尝试下载。

回测预热区间与下载区间保持分离：预热只读取本地已有分钟数据，不得把预热起点隐式传给下载器。本地可用历史的起点继续由现有 runner 计算，特征和元数据仍使用既有可用性规则，不伪造行情，也不静默改变正式回测区间。

## CLI 契约

现有 CLI 参数不变。例如 Top-20 回测命令为：

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --download-minute-data \
  --top-n 20 \
  --start 2025-01-01 \
  --end 2026-06-30 \
  --initial-equity 1000000
```

该命令的下载审计必须记录：

```text
start = 2025-01-01
end   = 2026-06-30
```

只使用 `--top-n 20` 时不隐式增加 AG；最终品种池仍由排名表前 20 个根品种决定。

## 共享下载器

`market_data_update.update_minute_data` 删除静态允许起止日期，只校验 `end >= start`。供应商对某些日期返回空数据或错误时，继续写入现有 `empty`、`download_errors` 和文件审计，不把外部失败伪装成成功。

`prepare_minute_data` 不再接受 `download_start`。它把自己的 `start`、`end` 原样传给 `update_minute_data`，从接口上保证集成调用无法扩展下载范围。

独立分钟下载 CLI 与集成回测 CLI 继续复用同一个结构化入口，因此两者具有相同的精确日期语义。

## Runner 数据流

多周期趋势与 cycle_v1 runner 都按以下顺序执行：

1. 解析并校验回测 `start`、`end`；
2. 以原始 `start`、`end` 选择品种并准备分钟数据；
3. 重新发现本地分钟目录并确定本次回测品种池；
4. 单独计算策略预热起点；
5. 从本地数据中尽可能加载预热至回测结束的行情；
6. 特征、交易机制元数据或正式区间覆盖不足时，沿用现有可用性与 fail-closed 规则。

`prepare_backtest_symbols` 同步删除 `download_start` 参数。这样 runner 不能再把 `start - warmup_days` 传入下载阶段，但仍可将预热起点用于 `_effective_warmup_start` 和元数据准备。

## 兼容性与边界

- 未开启 `--download-minute-data` 时行为不变。
- `--top-n`、显式品种和 EMA 品种池选择规则不变。
- 已存在 parquet 继续跳过且不覆盖。
- 请求区间早于原固定起点或晚于原固定终点时不再因本地常量失败。
- `end < start` 仍明确报错。
- 请求日期是否能从供应商获得，由下载审计如实反映。
- 不自动下载 `start` 之前的预热数据。

## 测试与验收

1. `update_minute_data` 接受跨越原固定边界的有效日期，并继续拒绝倒序日期。
2. `prepare_minute_data` 将 `start`、`end` 原样传给底层更新函数。
3. 多周期趋势 runner 的 `2025-01-01..2026-06-30` 调用不会传入 120 天预热起点。
4. cycle_v1 runner 同样只下载请求区间，两个入口不发生语义漂移。
5. Top-20 复现命令包含 `--top-n 20`、不包含隐式 `--symbols AG`。
6. 下载器、两个 runner 的聚焦测试、Ruff 和 compileall 全部通过。

## 非目标

本次不修改策略信号、仓位、撮合、手续费、元数据 fail-closed 规则、供应商重试策略或分钟文件覆盖规则，也不自动补齐回测起点之前的本地预热数据。
