# stock agent 说明

本目录的实际约束说明见：

- `stock/AGENTS.md`

当前阶段目标：

1. 下载沪深股票日线数据
2. 生成 `bull_pullback_continuation`、`breakout_pullback_continuation` 与 `volume_spike_up` 交易机会
3. 按 symbol 输出 analysis K 线图
4. 按 `opportunity_date` 输出每日机会目录

硬性边界：

- 只能修改 `stock/**`
- 不能修改 `stock/` 外的任何文件

执行入口与实现计划见：

- `stock/stock.md`

当前信号口径：

- 只输出买点，不输出卖点
- 默认排除名称以 `ST`、`*ST`、`S*ST` 开头的股票
- 用上一交易日数据生成下一交易日 `opportunity_date`
- `bull_pullback_continuation` 买点要求 `ema5 >= ema10 >= ema20`
- `bull_pullback_continuation` 买点要求最近一天成交量翻倍，或近 3 日均量 >= 1.5 倍近 10 日均量
- `breakout_pullback_continuation` 买点要求突破前高、回踩突破位附近、再重新站回突破结构
- `breakout_pullback_continuation` 额外要求信号日收盘价低于过去约一年半到当天最高价的一半
- `volume_spike_up` 买点要求当天上涨，且当天成交量 >= 前 5 个交易日平均成交量的 2 倍
- 每日机会目录为 `stock/report/opportunity_date/{run_id}/0001_{YYYY-MM-DD}/`
- `breakout_pullback_continuation` 专用每日目录为 `stock/report/opportunities_date_break_out/{run_id}/0001_{YYYY-MM-DD}/`
- 每日机会目录内输出带 `signal_type` 文件名的 PNG 图，不输出 CSV

全市场运行入口：

```bash
python3 -m stock.run.sample_pipeline --universe all --start 2023-01-01 --end 2026-07-05
```

每日增量运行入口：

```bash
python3 -m stock.run.sample_pipeline --universe all --latest-days 5
```
