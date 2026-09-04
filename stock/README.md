# Stock Signal Pipeline

本目录提供一个最小 A 股研究链路，只覆盖三步：

1. 下载沪深股票日线数据
2. 生成 `bull_pullback_continuation`、`breakout_pullback_continuation` 与 `volume_spike_up` 交易机会
3. 按 symbol 输出 analysis K 线图

## 目录

- `stock/config/default_universe.py`
  - 默认 20 只样本股
- `stock/data_code/stock_downloader.py`
  - Tushare 日线下载器
- `stock/strategy/signal_evaluators.py`
  - `bull_pullback_continuation`、`breakout_pullback_continuation` 与 `volume_spike_up` 评估与扫描
- `stock/analysis/render_symbol_bull_pullback_charts.py`
  - 机会图表渲染
- `stock/run/sample_pipeline.py`
  - 串联下载、扫描、出图

## 运行

需要环境变量：

```bash
export TUSHARE_TOKEN=your_token_here
```

运行全部沪深 A 股全流程：

```bash
python3 -m stock.run.sample_pipeline --universe all --start 2023-01-01 --end 2026-07-05
```

运行 20 只样本股全流程：

```bash
python3 -m stock.run.sample_pipeline --universe sample --start 2023-01-01 --end 2026-07-05
```

复用本地已有日线数据，跳过下载：

```bash
python3 -m stock.run.sample_pipeline --universe all --start 2023-01-01 --end 2026-07-05 --skip-download
```

每天只拉最近几个交易日并增量合并到本地历史数据：

```bash
python3 -m stock.run.sample_pipeline --universe all --latest-days 5
```

只回测 `volume_spike_up`，例如最近两年：

```bash
python3 -m stock.run.sample_pipeline --universe all --signal-type volume_spike_up --start 2024-07-31 --end 2026-07-31 --merge-existing
```

只回测低位 `breakout_pullback_continuation`，例如最近两年：

```bash
python3 -m stock.run.sample_pipeline --universe all --signal-type breakout_pullback_continuation --start 2024-07-31 --end 2026-07-31 --merge-existing
```

## 输出

- 日线 csv：`stock/data/origin/day/*.csv`
- 机会表：`stock/report/opportunities/*_stock_signal_opportunities.csv`
- 每日机会图目录：`stock/report/opportunity_date/<run_id>/0001_<YYYY-MM-DD>/*.png`
- `volume_spike_up` 专用每日机会图目录：`stock/report/opportunities_date_spike_up/<run_id>/0001_<YYYY-MM-DD>/*.png`
- `breakout_pullback_continuation` 专用每日机会图目录：`stock/report/opportunities_date_break_out/<run_id>/0001_<YYYY-MM-DD>/*.png`
- 图表目录：`stock/analysis/<run_id>/charts/*.png`，每个 symbol 一张图
- 图表索引：`stock/analysis/<run_id>/index.csv`
- 图表摘要：`stock/analysis/<run_id>/render_summary.json`

## 当前说明

- 第一版只做日线，不做分钟级
- 每张图包含日 K、成交量、EMA5、EMA10、EMA20
- 每张图把同一 symbol 的买入位置画在同一张图里，不再画卖出点
- 机会表包含 `name`、`total_mv`、`circ_mv`、`limit_up_count_2y`、`limit_down_count_2y`、`close_price`
- 机会表包含 `volume_5_avg`，用于复核 `volume_spike_up` 的五日均量条件
- 机会表通过 `signal_type` 区分 `bull_pullback_continuation`、`breakout_pullback_continuation` 和 `volume_spike_up`
- `opportunity_date` 使用上一交易日数据生成，用于每日机会目录
- 每日机会目录用 `0001_YYYY-MM-DD` 编号，按文件名排序时最近日期在最上面
- 每日机会目录内不再输出 CSV，而是输出与 `analysis/charts` 同风格的 PNG 图，文件名包含 `signal_type`
- 图表包含中文名称、EMA5、EMA10、EMA20、成交量和按 `signal_type` 着色的买点
- 股票池、机会表、analysis 图和每日 report 默认排除名称以 `ST`、`*ST`、`S*ST` 开头的股票
- `--latest-days N` 会按最近 N 个开市交易日下载数据，并合并进已有 `stock/data/origin/day/*.csv`，不会把历史 K 线覆盖成只有最近几天
- `--latest-days N` 扫描时仍读取完整本地历史 K 线，但机会表、analysis 和每日报告只输出最近 N 个交易日区间内的 `opportunity_date`
- `bull_pullback_continuation` 买点要求 `ema5 >= ema10 >= ema20`
- `bull_pullback_continuation` 买点要求最近一天成交量翻倍，或近 3 日均量 >= 1.5 倍近 10 日均量
- `breakout_pullback_continuation` 买点要求先突破前高，再回踩突破位附近，随后重新站回突破结构
- `breakout_pullback_continuation` 额外要求信号日收盘价低于过去约一年半到当天最高价的一半
- `volume_spike_up` 买点要求当天上涨，且当天成交量 >= 前 5 个交易日平均成交量的 2 倍
- `volume_spike_up` 额外要求信号日收盘价低于过去约一年半到当天最高价的一半
