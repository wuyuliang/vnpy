# Cycle V1 回测 CLI 集成分钟下载设计

## 目标

在 `cta.strategy.brooks.cycle_v1.backtest.runner` 中增加可选的分钟数据准备阶段，使一次命令先按推荐并集下载数据，再以同一并集运行 cycle_v1 回测。未开启下载时，现有回测命令、`--symbols all` 和报告语义保持不变。

分钟数据准备只解决行情覆盖，不生成或推断历史交易机制。缺 lifecycle、session、tick、multiplier、费用、保证金、涨跌停或 STOP capability 时，正式回测仍为 `BLOCKED_METADATA`，`official_performance` 和请求区间主曲线仍为 `null`。

## CLI

回测 runner 新增：

```text
--download-minute-data       回测前执行分钟数据准备；默认关闭
--top-n N                    ranking 中按 research_rank 升序取前 N
--include-ema-eligible       加入区间内曾满足前一日 EMA(1)>EMA(3)>EMA(5) 的品种
--ranking-csv PATH           默认 cta/feature/symbols_research_ranking.csv
--day-root PATH              默认 cta/data/origin/day
--download-rate-limit N      Tushare 分钟下载限速，默认 450
--download-audit-output PATH 可选的独立下载审计 JSON
```

推荐命令：

```bash
python3 -m cta.strategy.brooks.cycle_v1.backtest.runner \
  --symbols LC \
  --top-n 20 \
  --include-ema-eligible \
  --download-minute-data \
  --ranking-csv cta/feature/symbols_research_ranking.csv \
  --day-root cta/data/origin/day \
  --start 2026-01-01 \
  --end 2026-07-27 \
  --long-tf 1hour \
  --medium-tf 30min \
  --short-tf 5min \
  --data-root cta/data/origin/minute \
  --initial-equity 200000
```

只有提供 `--download-minute-data` 时，runner 才联网或调用下载器。`--top-n`、`--include-ema-eligible`、`--ranking-csv`、`--day-root`、`--download-rate-limit` 或 `--download-audit-output` 在未开启下载时属于无效组合并明确报错，避免参数被静默忽略。

## 品种语义

下载模式复用现有 `download_universe` 选择逻辑，稳定顺序为：

1. `--symbols` 中的显式根品种；
2. ranking Top-N；
3. ranking 全量池中的因果 EMA 合格品种。

根品种首次出现去重，交易所冲突 fail-closed。该最终并集既是下载池，也是本次回测池；不能下载完整并集后只回测显式品种，也不能静默加入 `data-root` 中未被本次并集选中的其他历史品种。

下载模式下 `--symbols all` 是“没有显式品种”的控制词，不直接展开成本地目录或 ranking 全量；此时必须同时提供正数 `--top-n` 或 `--include-ema-eligible`，最终回测池就是对应选择结果。单独使用 `--download-minute-data` 明确失败，避免 runner 的历史默认值意外触发下载。未开启下载时，`--symbols all` 继续表示回测本地可发现的全部根品种。

## 执行流程

runner 在解析日期、周期和基础参数后执行：

1. 校验下载参数组合及冻结日期区间；
2. 调用 `market_data_update` 的结构化 Python 入口，不启动子进程、不解析另一个 CLI 的文本输出；
3. 获得 `selection`、`selection_rejections`、逐品种下载统计、文件 SHA256 和 `download_errors`；
4. 重新扫描 `data-root`，按下载选择结果逐根解析本地分钟目录；
5. 若任一 selected 根品种仍不可发现，回测前明确失败，不将品种池缩成可用子集；
6. 全部 selected 根品种可发现后，按并集稳定顺序加载、扫描和回放；
7. 将下载审计嵌入最终回测 `summary.json`，可选地另写 `--download-audit-output`。

下载允许单品种或单日错误继续批次，以便收集完整审计；是否进入回测最终由“所有 selected 根品种均可发现”决定。分钟分区缺口、空响应和后续数据质量问题继续由现有 loader、metadata coverage 和 fail-closed 回测规则处理，不伪造行情。

## 代码边界

`market_data_update.py` 提供不依赖 `argparse.Namespace` 的结构化入口，接受显式品种、Top-N、EMA 开关、ranking/day/data 路径、日期、限速和可选 downloader，返回现有审计字典。独立下载 CLI 与集成回测 CLI 都调用该入口，避免两套选择和落盘逻辑漂移。

`runner.py` 只负责参数组合校验、调用数据准备入口、把 selection 转成回测请求，以及把下载审计放入回测 summary。下载、交易所解析、主力映射和 parquet 落盘仍由现有下载模块负责。

## 审计与错误

最终 `summary.json` 增加：

```text
minute_data_update.enabled
minute_data_update.selection
minute_data_update.selection_rejections
minute_data_update.requested_dates
minute_data_update.skipped
minute_data_update.downloaded
minute_data_update.empty
minute_data_update.converted_schema
minute_data_update.files
minute_data_update.download_errors
```

未开启下载时只记录 `minute_data_update.enabled=false`，不创建独立下载审计文件。下载 API、日线、目录身份、映射和单日分钟错误沿用现有 reason/stage；错误不得改写成成功，也不得使用其他交易所或默认合约兜底。

如果下载已完成但回测参数、数据或 metadata 阻断，独立下载审计仍可通过 `--download-audit-output` 保留。回测正式状态继续由现有 `COMPLETE/BLOCKED_METADATA` 契约决定，下载成功本身不能把阻断状态升级为完整绩效。

## 测试与验收

自动测试覆盖：

1. 新参数解析，默认不下载且现有默认值不变；
2. 下载参数在未开启下载时明确拒绝；
3. 下载模式将 `--symbols all` 视为空显式池，且没有 Top-N/EMA 来源时明确拒绝；
4. 显式、Top-N、EMA 并集被原样传入下载并成为回测请求池；
5. 下载后重新发现目录，缺任一 selected 根品种时回测不启动；
6. 已有文件跳过且不覆盖，下载审计进入最终 summary；
7. 独立 `market_data_update` CLI 继续工作并与结构化入口结果一致；
8. API 或单品种失败仍有审计，未静默缩小回测池；
9. 未开启下载的 runner 回归测试和既有 Brooks fail-closed 状态保持不变；
10. 聚焦 pytest、Ruff、compileall 和 CLI `--help` 通过。

不在本次范围内：扩大 `2026-01-01..2026-07-27` 下载窗口、自动补全交易机制元数据、覆盖已有 parquet、修改信号/风控/撮合规则或自动重试整个回测。
