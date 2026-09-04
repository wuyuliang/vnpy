# Stock Signal Implementation Plan

> **For agentic workers:** 当前实现限定在 `stock/` 目录内，按测试优先的方式逐步完成。步骤使用 checkbox 记录执行状态。

**Goal:** 在 `stock/` 内实现沪深 A 股研究链路：下载日线数据，扫描 `bull_pullback_continuation`、`breakout_pullback_continuation`、`volume_spike_up` 与 `ma5_ma10_big_bull` 机会，按 symbol 输出 analysis K 线图，并独立研究大牛股退出规则。

**Architecture:** 参考 `cta/` 的分层方式，但做最小裁剪版。数据下载、信号扫描、图表渲染三层解耦；统一使用 csv 作为日线输入，机会结果输出到 report，图表输出到 analysis。

**Tech Stack:** Python 3.10+、pandas、Pillow、pytest/unittest 风格测试、Tushare 日线接口。

---

## 范围与约束

- 只允许修改 `stock/**`
- 支持样本股票池：沪市 10 只 + 深市 10 只
- 支持全市场股票池：当前上市沪深 A 股
- 只做日线
- 只做四类主线信号，第四类为 `ma5_ma10_big_bull`
- 不做真实仓位管理、组合资金曲线、选机会后的执行、分钟级分析
- 主机会表和普通 analysis 只输出买点；卖点仅出现在独立退出研究表和研究图中
- 每个机会用上一交易日完整日 K 判断，`opportunity_date` 记为下一交易日

## 目标目录

- `stock/config/default_universe.py`
- `stock/data_code/stock_downloader.py`
- `stock/strategy/signal_evaluators.py`
- `stock/analysis/render_symbol_bull_pullback_charts.py`
- `stock/backtest/big_bull_exit_research.py`
- `stock/run/sample_pipeline.py`
- `stock/tests/`

## 输出物

- 下载数据：`stock/data/origin/day/*.csv`
- 机会结果：`stock/report/opportunities/{run_id}_stock_signal_opportunities.csv`
- 每日机会图目录：`stock/report/opportunity_date/{run_id}/{rank}_{YYYY-MM-DD}/*.png`
- 图表结果：`stock/analysis/{run_id}/charts/*.png`，每个 symbol 一张
- 索引文件：`stock/analysis/{run_id}/index.csv`
- 大牛股每日机会图：`stock/report/opportunities_date_ma5_ma10_big/{run_id}/{rank}_{YYYY-MM-DD}/*.png`
- 大牛股退出研究：`stock/report/exit_research/{run_id}_big_bull_exit_trades.csv` 与同名 summary JSON
- 大牛股买卖点图：`stock/analysis/{exit_run_id}/charts/*.png`，每个 symbol 一张

## Task 1: 建立目录与约束文件

- [x] 写入 `stock/AGENTS.md`
- [x] 写入 `stock/agent.md`
- [x] 写入 `stock/stock.md`
- [x] 创建最小包结构与 `__init__.py`

验证：

- `stock/` 下具备独立说明文件与后续代码目录

## Task 2: 先写失败测试

- [x] 为默认股票池写测试
- [x] 为日线标准化与下载器写测试
- [x] 为 `bull_pullback_continuation` 扫描写测试
- [x] 为 analysis 出图写测试
- [x] 为 sample pipeline 串联入口写测试

验证：

- 运行测试时先失败，证明行为尚未实现

## Task 3: 实现下载器

- [x] 提供默认 20 只股票池配置
- [x] 提供 Tushare 日线下载器
- [x] 提供全沪深 A 股股票池下载
- [x] 支持按交易日批量拉取全市场日线并拆分到 symbol csv
- [x] 统一输出为标准字段
- [x] 将样本股日线写入 `stock/data/origin/day`

验证：

- 下载器单测通过
- 脚本支持样本股批量下载

## Task 4: 实现信号扫描

- [x] 实现 `bull_pullback_continuation` 单点 evaluator
- [x] 实现 `breakout_pullback_continuation` 单点 evaluator
- [x] 实现 `volume_spike_up` 单点 evaluator
- [x] 实现基于历史日线的整段扫描函数
- [x] 组合扫描复用同一份清洗后的日线和指标，避免重复计算
- [x] 输出 buy-only 机会字段
- [x] 输出 `name`、`total_mv`、`circ_mv`、`limit_up_count_2y`、`limit_down_count_2y`、`close_price`
- [x] 按 `opportunity_date` 生成每日机会图目录
- [x] 输出机会 csv

验证：

- 扫描单测通过
- 样本运行能生成非空或空但格式正确的机会表

## Task 5: 实现 analysis 出图

- [x] 每个 symbol 输出一张日 K 汇总图
- [x] 在同一张图中绘制所有买入位置
- [x] 图表下方包含成交量
- [x] 图表包含 EMA5、EMA10、EMA20
- [x] 生成 `index.csv` 与 `render_summary.json`

验证：

- 出图单测通过
- 样本运行后能看到 PNG 文件

## Task 6: 串联入口与样本产物

- [x] 实现 `stock/run/sample_pipeline.py`
- [x] 串联下载、扫描、出图
- [x] 生成一轮样本股产物

验证：

- 一条命令可跑完整链路

## 运行命令草案

全沪深 A 股全流程：

```bash
python3 -m stock.run.sample_pipeline --universe all --start 2023-01-01 --end 2026-07-05
```

样本股全流程：

```bash
python3 -m stock.run.sample_pipeline --universe sample --start 2023-01-01 --end 2026-07-05
```

仅扫描本地已下载数据：

```bash
python3 -m stock.run.sample_pipeline --universe all --start 2023-01-01 --end 2026-07-05 --skip-download
```

每天增量拉取最近 N 个交易日并重新生成机会：

```bash
python3 -m stock.run.sample_pipeline --universe all --latest-days 5
```

只回测 `volume_spike_up` 最近两年：

```bash
python3 -m stock.run.sample_pipeline --universe all --signal-type volume_spike_up --start 2024-07-31 --end 2026-07-31 --merge-existing
```

只回测低位 `breakout_pullback_continuation` 最近两年：

```bash
python3 -m stock.run.sample_pipeline --universe all --signal-type breakout_pullback_continuation --start 2024-07-31 --end 2026-07-31 --merge-existing
```

## 已知风险

- 依赖 `TUSHARE_TOKEN`
- 如果当前环境无法访问外网，则真实下载步骤可能无法完成
- `bull_pullback_continuation`、`breakout_pullback_continuation` 与 `volume_spike_up` 属于研究候选信号，不代表可直接下单
- 全市场下载和出图文件量较大，运行时间取决于 Tushare 响应和本机磁盘速度

## 2026-07-05 sample20 实际产物

- 实际运行 ID：`20260705_sample20_live`
- 样本股票数：`20`
- 机会数：`1918`
- 图表数：`1918`

## 2026-07-05 优化项

- [x] 支持下载全部沪深 A 股 2023-01-01 到 2026-07-05 日 K
- [x] analysis 从每个机会一张图改为每个 symbol 一张汇总图
- [x] 同一张图包含买入点、成交量、EMA5、EMA10、EMA20
- [x] `--universe all` 串联全市场下载、扫描、出图

## 2026-07-05 全沪深 A 股实际产物

- 实际运行 ID：`20260705_all_a_share_bull_pullback`
- 股票日线文件数：`5210`
- 机会数：`442783`
- 有机会股票数：`5193`
- 图表数：`5193`
- 缺失 K 线数：`0`
- 机会表：`stock/report/opportunities/20260705_all_a_share_bull_pullback_bull_pullback_continuation.csv`
- 图表目录：`stock/analysis/20260705_all_a_share_bull_pullback/charts/`

## 2026-07-05 第二轮优化项

- [x] 去掉卖点字段和图上的卖出标记
- [x] 机会表增加公司名称、总市值、流动市值、近两年涨停次数、近两年跌停次数、股价
- [x] 增加每日机会图目录：`stock/report/opportunity_date/{run_id}/0001_{YYYY-MM-DD}/*.png`
- [x] 每日目录按 `opportunity_date` 拆分，`opportunity_date` 由上一交易日数据生成
- [x] 买点加强为 `ema5 >= ema10 >= ema20`
- [x] 买点加强为最近一天成交量翻倍，或近 3 日均量 >= 1.5 倍近 10 日均量

## 2026-07-05 全沪深 A 股 v2 实际产物

- 实际运行 ID：`20260705_all_a_share_bull_pullback_v2`
- 股票数：`5210`
- 机会数：`15664`
- 有机会股票数：`4525`
- 图表数：`4525`
- 每日机会目录数：`815`
- 缺失 K 线数：`0`
- 机会表：`stock/report/opportunities/20260705_all_a_share_bull_pullback_v2_bull_pullback_continuation.csv`
- 每日机会目录：`stock/report/opportunity_date/20260705_all_a_share_bull_pullback_v2/`
- 图表目录：`stock/analysis/20260705_all_a_share_bull_pullback_v2/charts/`

## 2026-07-05 第三轮优化项

- [x] 每日机会目录改为：`stock/report/opportunity_date/{run_id}/`
- [x] 日期子目录改为 `0001_YYYY-MM-DD`，按名称排序时最近日期在最上面
- [x] 日期子目录内不再输出 `opportunities.csv`
- [x] 日期子目录内输出与 `analysis/charts` 同风格的 PNG 图
- [x] 每张 PNG 包含中文名称、成交量、EMA5、EMA10、EMA20 和买点

## 2026-07-05 每日机会 PNG 实际产物

- 实际运行 ID：`20260705_all_a_share_bull_pullback_v2`
- 日期目录数：`815`
- 日报 PNG 数：`15664`
- CSV 文件数：`0`
- 最近日期目录：`stock/report/opportunity_date/20260705_all_a_share_bull_pullback_v2/0001_2026-07-03/`
- 索引文件：`stock/report/opportunity_date/20260705_all_a_share_bull_pullback_v2/index.md`

## 2026-07-05 第四轮优化项

- [x] 增加 `breakout_pullback_continuation`
- [x] `stock/report/opportunities` 输出组合机会表：`{run_id}_stock_signal_opportunities.csv`
- [x] `stock/analysis` 图表索引增加 `signal_types` 字段
- [x] 图表标题和图例展示不同 `signal_type`
- [x] 每日机会 PNG 文件名增加 `signal_type`
- [x] 组合扫描复用一次 OHLCV 清洗和 EMA/量能指标，减少重复计算

## 2026-07-05 第五轮优化项

- [x] 增加 `volume_spike_up`
- [x] `volume_spike_up` 使用前 5 个交易日平均成交量作为基准，不包含当天
- [x] `volume_spike_up` 要求当天上涨，优先用 `pct_chg > 0`，缺失时回退到 `close > previous_close`
- [x] 机会表增加 `volume_5_avg` 字段，便于复核量能触发条件
- [x] `stock/analysis` 图表标题、图例、买点颜色支持 `volume_spike_up`
- [x] `stock/report/opportunity_date` 每日 PNG 文件名支持 `volume_spike_up`
- [x] 组合扫描继续复用一次 OHLCV 清洗、EMA 和量能指标，避免三类信号重复准备数据

## 2026-07-10 第六轮优化项

- [x] 增加 `--latest-days N`，用于每天只下载最近 N 个开市交易日
- [x] `--latest-days` 自动通过交易日历解析 `start/end`
- [x] 最近几天下载结果增量合并到已有 symbol CSV，避免覆盖历史 K 线
- [x] 下载后继续扫描完整本地历史数据，保证 EMA、突破、量能窗口计算不被最近几天截断
- [x] `--latest-days` 输出只保留最近交易日区间内的 `opportunity_date`，避免每日报告混入全历史机会

## 2026-07-11 第七轮优化项

- [x] 默认排除名称以 `ST`、`*ST`、`S*ST` 开头的股票
- [x] 全市场股票池从 `stock_basic` 阶段过滤 ST 股票
- [x] pipeline 在显式传入 symbols 和最终机会表阶段再次过滤 ST 股票
- [x] analysis 和每日 report 复用过滤后的机会表，不再输出 ST 股票图表

## 2026-07-31 第八轮优化项

- [x] `volume_spike_up` 增加低位过滤：信号日收盘价低于过去约一年半到当天最高价的一半
- [x] 机会表增加 `lookback_high_18m` 与 `close_to_lookback_high` 字段，便于复核低位过滤
- [x] pipeline 增加 `--signal-type volume_spike_up`，支持只回测放量上涨机会
- [x] pipeline 增加 `--merge-existing`，避免全量下载指定窗口时覆盖更早本地历史 K 线
- [x] 增加 `stock/report/opportunities_date_spike_up/{run_id}/` 专用每日机会 PNG 目录

## 2026-07-31 第九轮优化项

- [x] `breakout_pullback_continuation` 增加低位过滤：信号日收盘价低于过去约一年半到当天最高价的一半
- [x] `breakout_pullback_continuation` 机会表同样写入 `lookback_high_18m` 与 `close_to_lookback_high`
- [x] 增加 `stock/report/opportunities_date_break_out/{run_id}/` 专用每日机会 PNG 目录

## 2026-08-01 第十轮优化项：MA5/MA10 大牛股模式

- [x] 增加第四类 `signal_type=ma5_ma10_big_bull`
- [x] 仅在 `MA5` 从下向上穿越 `MA10` 的状态切换时生成原始候选
- [x] 增加 MA20/MA60 趋势、20/60/120 日收益、20 日量比、120 日涨停次数、60 日放量上涨次数、平台压缩度等特征
- [x] 增加同一机会日全部非 ST 股票的 `return_60d` 全市场横截面分位 `rs_60d_pct`，不在候选子集内自排名
- [x] 大牛股评分公共 API 强制传入 `market_returns_by_date`，防止调用方退回候选子集排名
- [x] 120 日涨停次数按板块阈值计算：主板 9.8%，创业板/科创板 19.8%
- [x] 增加满分 100 的 `big_bull_score`，默认保留 `>=70` 分
- [x] 每个机会日先剔除冷却 symbol 再保留 Top 30，同一 symbol 按机会日 20 个自然日内最多出现一次
- [x] 主机会表和普通图继续保持 buy-only，EMA5/EMA10/EMA20 与成交量展示不变
- [x] 增加专用逐日目录 `stock/report/opportunities_date_ma5_ma10_big/{run_id}/`
- [x] 每日目录只用当天机会决定 symbol 集合；PNG 按 symbol 汇总本次运行全部买点并统一画到运行 `end`
- [x] 单信号专用日报通过硬链接复用普通日报 PNG，文件系统不支持时才复制，避免重复渲染
- [x] 增加 `stock/backtest/big_bull_exit_research.py`，使用初始止损、MA20 趋势破坏和最高价减 `3 x ATR20` 的 Chandelier Exit
- [x] 退出研究必须精确匹配 `opportunity_date` 日 K，并从入场日开始检查止损；缺少当日 K 线不顺延入场
- [x] 缺少精确入场 K 线的异常行保留状态但不绘制虚假买点，且不会中断其他 symbol 出图
- [x] 退出研究按 symbol 把全部买点和卖点画在同一张独立研究图中，并继续展示 EMA5/EMA10/EMA20 与成交量
- [x] 全流程继续过滤 ST 股票，名称为空时按不可分析处理
- [x] 近两年涨跌停次数只统计两年 cutoff 至回测 `end`，不读取 `end` 后本地数据

最近两年全市场扫描命令：

```bash
python3 -m stock.run.sample_pipeline --universe all --signal-type ma5_ma10_big_bull --start 2024-08-01 --end 2026-08-01 --merge-existing --run-id 20260801_ma5_ma10_big_bull
```

退出研究命令：

```bash
python3 -m stock.backtest.big_bull_exit_research --opportunity-csv stock/report/opportunities/20260801_ma5_ma10_big_bull_stock_signal_opportunities.csv --data-root stock/data/origin --run-id 20260801_ma5_ma10_big_bull_exit
```

核心产物：

- `stock/report/opportunities/20260801_ma5_ma10_big_bull_stock_signal_opportunities.csv`
- `stock/report/opportunities_date_ma5_ma10_big/20260801_ma5_ma10_big_bull/`
- `stock/analysis/20260801_ma5_ma10_big_bull/charts/`
- `stock/report/exit_research/20260801_ma5_ma10_big_bull_exit_big_bull_exit_trades.csv`
- `stock/report/exit_research/20260801_ma5_ma10_big_bull_exit_big_bull_exit_summary.json`
- `stock/analysis/20260801_ma5_ma10_big_bull_exit/charts/`

## 2026-08-01 最近两年全市场实际产物

- 扫描区间：`2024-08-01` 至 `2026-08-01`，本地最后交易日为 `2026-07-31`
- 股票数：`4998`
- 机会数：`4917`
- 有机会股票数：`2863`
- 机会日期数：`464`
- 每日最多机会数：`30`
- 分数范围：`70.02` 至 `94.48`
- 全市场 RS 范围：`0.7002` 至 `1.0000`
- ST 或空名称机会数：`0`
- 次日入场日期违规数：`0`
- symbol 最短机会日冷却：`20` 个自然日
- 普通 analysis 图：`2863` 张，缺失 K 线 `0`
- 逐日机会图：`4917` 张，最近目录为 `0001_2026-07-31`
- 退出研究：`4917` 笔、`2863` 张买卖点图、缺失 K 线 `0`
- 初始止损：`3355` 笔，占 `68.23%`，平均收益 `-10.15%`
- Chandelier 退出：`759` 笔，占 `15.44%`，平均收益 `29.11%`
- MA20 趋势破坏退出：`749` 笔，占 `15.23%`，平均收益 `19.95%`
- 全部样本平均收益：`0.638%`；该结果未计手续费、滑点、涨跌停成交约束和仓位管理，仅用于规则研究
- 最终验证：聚焦测试 `43 passed`，完整 `stock` 测试 `66 passed`，Ruff 与 `compileall` 通过
- 产物审计：20 项全部通过；独立代码复审结论为 Ready，无 Critical/Important findings

## 2026-08-02 PNG 时间轴与买点汇总优化

- [x] 所有股票图每两个月绘制时间辅助竖线，并在价格图下方标注 `YYYY-MM`
- [x] 同一 symbol 的全部买入机会合并到一张 PNG，不再按 signal_type 拆图
- [x] 每个买点使用 signal_type 对应颜色的竖向虚线和向上箭头
- [x] 每日目录继续表示当天候选 symbol，但 PNG 改为截至运行 `end` 的回看汇总图
- [x] `end=2026-08-01` 时标题显示 `chart_end=2026-08-01`，实际最后交易 K 线显示到 `2026-07-31`
- [x] 同一 symbol 跨日期目录使用硬链接复用同一张汇总图
- [x] 样本运行 `20260802_sample_chart_markers`：13 个机会、6 个 symbol、6 张 analysis 图、缺失 K 线 0

注意：回看汇总 PNG 会展示候选日之后的行情和买点，只用于图形复盘；机会 CSV 的生成仍使用当时可获得的数据。
