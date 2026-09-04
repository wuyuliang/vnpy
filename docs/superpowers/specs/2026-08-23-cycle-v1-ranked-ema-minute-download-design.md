# Cycle V1 Ranking 与 EMA 并集分钟下载设计

## 目标

扩展 `cta.strategy.brooks.cycle_v1.backtest.market_data_update`，使一次下载运行可以按以下三个来源选择品种，并对根品种去重后下载真实主力合约 1 分钟数据：

1. CLI 显式指定品种；
2. `cta/feature/symbols_research_ranking.csv` 中 `research_rank` 最小的 Top-N 品种；
3. ranking 全量池中，在请求区间至少一个交易日满足因果日线条件 `EMA(1) > EMA(3) > EMA(5)` 的全部品种。

选择只决定需要准备哪些分钟数据，不改变 cycle_v1 回测内逐日 EMA 准入逻辑。下载结果不能被称为策略机会或绩效。

## CLI

新增参数：

```text
--symbols [SYMBOL ...]       显式附加品种，可用 LC、LC0、LC.GFEX 或 LC0.GFEX
--top-n N                    ranking 中按 research_rank 升序取前 N；0 表示关闭
--include-ema-eligible       加入请求区间曾满足前一日日线 EMA 条件的 ranking 品种
--ranking-csv PATH           默认 cta/feature/symbols_research_ranking.csv
--day-root PATH              默认 cta/data/origin/day
```

保留现有 `--start`、`--end`、`--data-root`、`--audit-output` 和 `--rate-limit`。为避免旧命令突然触发全市场下载，当 `--symbols`、`--top-n`、`--include-ema-eligible` 均未提供时，仍使用原有 `RB CU`。推荐命令：

```bash
python3 -m cta.strategy.brooks.cycle_v1.backtest.market_data_update \
  --symbols LC \
  --top-n 20 \
  --include-ema-eligible \
  --ranking-csv cta/feature/symbols_research_ranking.csv \
  --day-root cta/data/origin/day \
  --start 2026-01-01 \
  --end 2026-07-27 \
  --data-root cta/data/origin/minute \
  --audit-output cta/strategy/brooks/report/cycle_v1/20260823_minute_download.json
```

## 品种解析与并集

ranking 必须包含 `symbol`、`exchange` 和 `research_rank`。代码将连续代码规范化为大写根品种，例如 `RB0 -> RB`，并规范交易所别名 `SHF -> SHFE`、`CZC/ZCE -> CZCE`、`GFE -> GFEX`、`CFX -> CFFEX`。

Top-N 始终加入，不要求通过 EMA。EMA 扩展在 ranking 全量池内计算。显式品种始终加入；其交易所按以下顺序解析：

1. ranking 中唯一匹配记录；
2. `day-root/{ROOT}0.csv` 中唯一 exchange；
3. Tushare 各支持交易所 `fut_basic` 中覆盖请求区间的唯一根品种。

解析不到或跨交易所歧义时不猜测，品种进入 `selection_rejections`。最终下载顺序稳定为显式品种顺序、Top-N 排名顺序、EMA 扩展排名顺序，并按根品种首次出现去重。支持 SHFE、DCE、CZCE、INE、GFEX 和 CFFEX。

## EMA 准入

每个 ranking 品种读取 `day-root/{symbol}.csv`。要求 `datetime`、`close` 字段，日期升序且 close 为有限正数。EMA 使用 pandas `ewm(span=N, adjust=False)`。

对请求区间内交易日 `D`，只使用该品种 `D` 之前最近一根完整日 K 计算的 EMA 值：

```text
eligible(D) = EMA1(D-1) > EMA3(D-1) > EMA5(D-1)
```

实现上先按日线计算 EMA，再将三列整体 `shift(1)` 后检查请求区间。任一 `D` 为真即将该根品种加入下载池。缺文件、缺字段、无请求区间行或没有合格日期不会异常终止全批次，而是记录明确的选择审计原因。禁止用请求日当日 close 选择当天分钟数据。

## 下载与落盘

每个已选择品种用其真实交易所调用 `fetch_fut_mapping(f"{root}0", exchange)`，再按请求日期下载映射出的实际合约分钟数据。仍保持：

- 接受任意 `end >= start` 的日期区间，并只下载请求的 `start..end`；
- 已存在日分区不覆盖；
- 空响应记为 `empty`；
- 写临时文件后原子替换；
- 同一文件内只能包含映射出的一个实际合约；
- 交易所、根品种和合约代码必须来自可审计来源，不使用默认 multiplier、tick、费用或保证金。

若本地已有该根品种的可识别分钟目录，则继续写入该目录，避免产生 alias 重复；否则写入 `data-root/{ROOT}`。新文件统一包含 `datetime/open/high/low/close/volume/open_interest/turnover/ts_code/contract_code/symbol/exchange`。现有 CU 特殊 schema 保持兼容并继续修复，不批量重写其他历史文件。

本功能只准备行情。若某品种缺失 lifecycle、session、费用、保证金、涨跌停或 STOP capability，后续正式回测仍必须为 `BLOCKED_METADATA`，不能因为分钟行情已下载而放宽。

## 审计输出

JSON 除现有日期和文件哈希外增加：

```text
selection.explicit
selection.top_n
selection.ema_eligible
selection.selected
selection_rejections
ranking_csv
day_root
```

每个 selected 项记录 `root_symbol`、`exchange` 和来源集合。每个拒绝项记录 `root_symbol`、阶段和 reason code；无 EMA 命中使用 `NO_EMA_ELIGIBLE_DATE`，与缺失或损坏的日线分离。下载统计继续按根品种输出 `requested_dates/skipped/downloaded/empty/converted_schema`。`files` 对新下载、已有跳过文件和 CU schema 受控迁移分别记录 `status=downloaded/skipped/converted_schema`、实际合约、路径与 SHA256。目录身份发现异常时必须 fail-closed，不得退回猜测目录导致重复 alias；已有 CU schema 修复异常只阻断该文件，不中断其他品种。

## 测试与验收

自动测试覆盖：

1. CLI 新参数与无参数 RB/CU 兼容默认；
2. ranking Top-N 排序、根品种规范化和重复行拒绝；
3. 显式、Top-N、EMA 三来源并集与稳定去重；
4. EMA 使用前一日数据，未来日线修改不改变此前选择；
5. 缺日线和无合格日期进入审计；
6. LC 从 GFEX 基础信息解析并以 GFEX 调用主力映射；
7. SHFE/DCE/CZCE/INE/GFEX/CFFEX 交易所传递正确；
8. 已存在文件不覆盖、空响应、原子写入和现有 CU schema 兼容；
9. 审计 JSON 可序列化且包含选择来源与文件哈希；
10. `cycle_v1` 下载器聚焦测试、Ruff 和编译通过。

不在本次范围内：自动下载 `start` 之前的策略预热行情、自动生成历史交易机制元数据、修改 cycle_v1 信号或风险规则、实际回测或实盘下单。
