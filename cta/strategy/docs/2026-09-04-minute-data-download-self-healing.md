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

## 8. 第二轮：重建反而把好表覆盖掉了

服务器上第一版自动重建打出：

```
{"event":"turnover_table_rebuilt", "rows":20, "roots":1,
 "roots_from_minute":["UR"],
 "roots_missing_multiplier":["A","AD","AG",... 几乎全部]}
{"status":"INVALID_ARGUMENT","reason":"turnover table '...' has no rows before 2026-01-01"}
```

两个独立的缺陷叠在一起：

**(a) 重建用的是默认元数据缓存路径。** `build_turnover_table` 靠
`load_contract_sizes(meta_root)` 拿合约乘数、靠 `_load_next_open_dates(meta_root)`
拿交易日历。服务器上那个默认路径是空的 → 乘数几乎全缺 → 日线行全被丢掉；
交易日历也缺 → 分钟行只剩 UR 一个。builder **不报错**，就是返回得很少。

修复：新增 `--turnover-meta-cache-root`（默认
`cta/strategy/brooks/cycle_v1/meta_cache`），显式传给重建，并在事件里回显这个
路径，缺乘数/缺日历的品种也一并打出来。

**(b) 重建结果无条件采纳并落盘。** 于是一份 68 品种 / 11670 行的好表被 20 行
1 品种的残次品换掉，然后整跑挂在"没有 start 之前的行"。

修复：`_rebuild_rejection()` 在采纳之前做三道体检——

| 条件 | 判定 |
| --- | --- |
| 空表 / `trade_date` 全解析不出来 | 拒绝 |
| 没有任何行早于所需日期 | 拒绝（用它排名必然要看未来数据） |
| 品种数 < 10 | 拒绝（本机元数据不全的典型信号） |
| 品种数 < 已有表的一半 | 拒绝 |

拒绝时保留旧表、**不落盘**，打 `turnover_table_rebuild_rejected` 事件并带上
`roots_missing_multiplier` / `roots_missing_trade_calendar` / `meta_cache_root`，
直接指向病因。采纳时改为写临时文件 + `os.replace` 原子替换，中途失败不会留下
半张表。

**(c) 补池失败不再阻塞整跑。** 表不可用或覆盖不到窗口时，以前两条路都是
`raise`。现在退回纯 `--top-n` 选池继续跑，并且：

- stderr 打一行 `{"event":"turnover_topup_skipped","reason_code":...}`
- `metadata_gaps.csv` 记一行 `field=turnover_universe`，
  `reason_code` 为 `TURNOVER_TABLE_UNUSABLE` 或 `TURNOVER_TABLE_WINDOW_UNCOVERED`

补池本来就是"锦上添花"——它的作用是把 SN/LC/JD 这类成交额高但研究排名靠后的
品种捞进来。缺了它跑出来的是一个更小的池子，不是错误结果；为它牺牲整跑不划算。

## 9. 服务器上的一次性修复

服务器那份表已经被覆盖成 20 行了，需要重建一次（本地这份是好的：
68 品种 / 11670 行 / 2024-01-02 ~ 2026-07-28）：

```bash
python3 -m cta.data_code.build_symbol_turnover \
  --start 2023-01-01 --end 2026-07-01 \
  --meta-cache-root cta/strategy/brooks/cycle_v1/meta_cache
```

跑完确认 `roots` 是几十而不是个位数。若 `roots_missing_multiplier` 仍然很长，
说明服务器上的 `meta_cache` 目录本身没同步过去——那才是根因，重建多少次都没用。
即便如此，回测本身现在也不会再被它卡住。

## 10. 服务器重跑

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

## 11. 第三轮：`BLOCKED_METADATA` —— 根因是 `.gitignore` 的 `*.csv`

```
{"status":"INVALID_ARGUMENT","reason":"BLOCKED_METADATA: required metadata file
 is missing: /opt/vnpy/cta/strategy/brooks/scalp/meta/exchange_calendar.csv"}
```

`git check-ignore -v` 直接给出答案：

```
.gitignore:9:*.csv     cta/strategy/brooks/scalp/meta/exchange_calendar.csv
.gitignore:2:cta/data  cta/data/origin/day/OI0.csv
```

仓库根的 `*.csv` 把执行元数据包的四个规范 CSV 全排除了
（`git ls-files` 里确实一个都没有），所以服务器 clone 出来这个目录是空的。
同一条规则也解释了**上一轮**的现象：`cycle_v1/meta_cache` 里的 CSV 同样没进 git，
所以重建拿不到合约乘数和交易日历，只剩 1 个品种。

### 这份元数据不能自动下载

`prepare_execution_metadata` 强制 `MetadataBundle.load(base_root)`，而且把
`manifest.json` 的 sha256 算进缓存键；包本身由 `shfe_metadata_builder` 从交易所
源文件导入（本地目录里那份 `shfe_source_audit.jsonl.gz` 就是证据），不是 tushare
能生成的。所以"缺了就从供应商拉一份"在这里做不到，也不该做——fail-closed 的
执行元数据一旦允许运行时凭空捏造，回测的可复现性就没了。

### 已修 (a)：预检提前到下载之前

`_preflight_required_data()` 在 `run_from_args` 里、任何下载动作之前检查
`manifest.json` + 四个规范 CSV，缺了就报出**文件名 + 根因 + 两条可执行的修复**，
两秒失败而不是下载一小时之后失败。

日线目录和成交额元数据缓存缺失只降级、不阻塞，各打一行
`preflight_day_data_missing` / `preflight_turnover_meta_cache_missing` 说明影响。

### 已修 (b)：降级分支里的二次抛错

`prepare_backtest_metadata` 的 `except` 分支本意是"记一笔缺口然后继续"，但它
`return MetadataBundle.load(args.meta_root)` —— 基准包缺失时这一行**又抛一次**，
于是降级被伪装成了一句看不懂的 `BLOCKED_METADATA: required metadata file is
missing`，真正的首因（`prepare_execution_metadata` 那次失败）被吞掉。现在包住它，
抛出的消息同时带上首因和基准包的加载错误。

### 服务器修复

```bash
# 1. 执行元数据包（约 5MB，必需）
git add -f cta/strategy/brooks/scalp/meta/
git commit -m "track execution metadata bundle"
# 服务器 git pull

# 2. 日线数据（约 16MB，--include-ema-eligible 和成交额表的日线部分要用）
git add -f cta/data/origin/day/
# 或 rsync -av cta/data/origin/day/ <server>:<repo>/cta/data/origin/day/

# 3. 成交额表重建改用 scalp/meta（load_contract_sizes / _load_next_open_dates
#    都已同时支持扁平布局，不必再传 234MB 的 cycle_v1/meta_cache）
--turnover-meta-cache-root cta/strategy/brooks/scalp/meta
```

## 12. 测试

`cta/strategy/tests/test_missing_symbol_diagnosis.py`（23 例）：诊断文本的
逐品种计数/原因归并/跨品种隔离、成交额表新鲜与过期两条路径、重建失败回退、
映射逐行降级的四种情形、响应级交易所不符仍然硬失败、重建体检的四条判据、被拒的重建不覆盖磁盘、预检的缺文件/可执行修复/两条降级警告/扁平元数据布局。
`test_universe_and_metadata_window.py` 里两条"表坏了要抛错"改成"退回 --top-n 并留痕"。

全量：545 passed。
