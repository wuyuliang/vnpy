# 分钟数据下载：自诊断与自动补齐

**触发**：远程服务器报 `{"status": "INVALID_ARGUMENT", "reason": "missing selected minute data after download: OI.CZCE,MA.CZCE"}`。

## 1. 这条报错为什么是死胡同

`prepare_backtest_symbols` 的流程是：选池 → `update_minute_data` 下载 →
`discover_symbols` 回读本地分区 → 对不上就抛错。

`update_minute_data` 其实把每个品种的 `requested_dates / downloaded /
skipped / empty` 计数和**每一条**下载错误都写进了 summary，但抛错那一行
只拼了品种名，summary 整个被丢掉。于是"供应商在这段区间对这个品种没有
主力映射"和"网络断了"在报错里长得一模一样。

OI/MA 本地是有数据的（`cta/data/origin/minute/OI` 直到 2026-07-27，合约
`OI2609.ZCE`，交易所字段 `CZCE`），别名解析、目录命名都没问题——所以这是
**服务器侧那次下载**的问题，只能靠把 summary 打出来定位。

## 2. 已修：报错自带诊断

`_missing_symbol_diagnosis()`（`cycle_v1/backtest/runner.py`）现在把
每个缺失品种的计数 + 归并后的下载错误原因一起打出来：

```
missing selected minute data after download: OI.CZCE,MA.CZCE
  OI.CZCE: requested_dates=0 downloaded=0 skipped=0 empty=0
    ×1 mapping: no main-contract mapping in requested interval
  MA.CZCE: requested_dates=3 downloaded=0 skipped=0 empty=3
    ×3 minute: HTTP 503
重跑时加 --allow-missing-symbols 可以跳过这些品种继续，缺口会写进 metadata_gaps.csv。
```

`requested_dates=0` 一律指向映射层（供应商没给主力合约），非 0 而
`empty=N` 则指向分钟接口本身。一条错误都没有时会明说"供应商在该区间对这个
品种就没有数据，或者主力合约映射为空"，而不是留白。

## 3. 已修：主力映射逐行降级，不再一行毁一个品种

`market_data_update.py` 里两处原来是**整品种**硬失败：

| 原行为 | 现在 |
| --- | --- |
| `_select_mapping`：任一 trade_date 映射到两个合约 → `raise` → 该品种整段映射作废 | 只丢掉这些歧义日期，其余日期照常下载；被丢的日期记进 `download_errors[stage=mapping_partial]` |
| `_validate_mapping_identity`：任一行合约码非法/root 或交易所不符 → `raise` → 同上 | 逐行过滤，非法行丢弃并记录原因 |

换月附近供应商同一天返回新旧两个合约是常态；以前这一条就能让整个品种在
整段回测区间里没有任何分钟数据，而报错只说"missing"。

**仍然硬失败的一条**（有意保留）：整份响应的 `mapping_exchange` 与请求的
交易所不符——那说明问了 A 答了 B，继续下载会把别人的数据写进本地分区。

丢弃只造成**缺日**，下游 replay 本来就容忍缺日；而整品种缺失会直接让跑挂掉。

## 4. 已修：成交额表过期自动重建

`--include-top-turnover` 读的 `symbol_turnover_daily.parquet` 是分钟分区的
派生物。刚下载完新品种/新交易日之后，它仍然是旧的，于是选池按过期数据排名，
而且没有任何提示。

`_turnover_table_for()` 现在：表里最新日期 < 回测所需日期 → 用**本次运行的**
`--data-root` / `--day-root` 重建到 `--end`，落盘，并打一行
`{"event":"turnover_table_rebuilt", ...}` 到 stderr。重建失败或产出为空时
退回旧表并打 `turnover_table_rebuild_failed`，不吞掉整跑。

（`day_root` 必须显式传：只覆盖 `minute_root` 时，一个假的分钟根仍然会从仓库
里真实的日线目录读出 5 万行，测试里就是这么暴露的。）

## 5. 已修：补池品种被拒不再是致命错误

`--include-top-turnover` 补进来的品种走的是 explicit 通道，而 explicit 被拒
以前一律 `raise`。用户自己 `--symbols` 点名的品种被拒该报错，程序自己补的
不该让整跑失败。现在补池品种被拒记为
`metadata_gaps.csv[reason_code=TURNOVER_TOPUP_UNRESOLVED]`，跑继续。

## 6. 已有的自动补齐（本次核对确认仍然有效）

- **元数据预热**：`--metadata-warmup-days`（默认 30）自动把 start 往前推 30 个
  交易日下载元数据，`--no-auto-metadata` 关闭。
- **供应商元数据磁盘缓存**：`CachingMetadataSourceClient` 只缓存不可变历史
  （`date < today` 的表、`end < today` 的日历），命中记 `source="cache"`。
- **缺口台账**：`metadata_gaps.csv` 现在覆盖 `MISSING_MINUTE_DATA_AFTER_DOWNLOAD`
  和 `TURNOVER_TOPUP_UNRESOLVED`。

## 7. 查过但**不能**自动补的

从本地日线反推主力映射——`cta/data/origin/day` 存的是连续合约（`OI0.csv`，
`symbol=OI0`），没有分合约的持仓量，无法算出"当日持仓量最大的合约"。所以
供应商映射为空时没有本地兜底，只能靠 §2 的诊断说清楚是映射层的问题。

## 8. 服务器重跑

```bash
python3 -m cta.strategy.multi_timeframe_trend_backtest.runner \
  --start 2026-01-01 --end 2026-07-01 --initial-equity 1e+06 \
  --download-minute-data --top-n 40 --include-top-turnover 0.8 \
  --allow-missing-symbols \
  --run-id oi_ma_diag
```

- 如果 OI/MA 这次能下下来 → 上一次是瞬时的（限流/网络），映射逐行降级也可能
  已经把换月那天的歧义处理掉了。
- 如果仍然缺 → 报错/`metadata_gaps.csv` 里会写明是 `mapping` 阶段还是 `minute`
  阶段。`mapping` 阶段且 `requested_dates=0`，说明该账号的 `fut_mapping` 对
  `OI.ZCE`/`MA.ZCE` 返回空——需要核对 tushare 权限或积分，不是代码问题。
- `--allow-missing-symbols` 让这两个品种被跳过、其余 38 个继续跑，长区间的跑
  不会再因为两个品种整个作废。

## 9. 测试

`cta/strategy/tests/test_missing_symbol_diagnosis.py`（13 例）：诊断文本的
逐品种计数/原因归并/跨品种隔离、成交额表新鲜与过期两条路径、重建失败回退、
映射逐行降级的四种情形、响应级交易所不符仍然硬失败。

全量：530 passed。
