# cta/strategy/brooks/backtest/

## 主要做什么

Brooks v3 的**离线回测包装**：把 [core](../core/) 装进自研轻量回测引擎，按批量任务跑 + 出 summary / report。**不依赖 vnpy**。

## 关键文件

| 文件 | 作用 |
|---|---|
| [engine.py](engine.py) | 自研轻量回测引擎：按 bar 推进、调 `BrooksV3Core.on_bar`、fill 模拟、PnL 统计 |
| [runner.py](runner.py) | 批量 CLI：扫多个 (symbol, time range) 跑回测，产出落到 [../report/<timestamp>/](../report/) |
| [reporter.py](reporter.py) | 渲染 summary.csv + report.md（与 [cta/report/render/](../../../report/render/) 互补，本目录格式 Brooks 专用） |

## 详细过程

```
[CLI]
    python -m cta.strategy.brooks.backtest.runner \
        --config cta/strategy/brooks/config/strategy.yaml \
        --top-n 10 --start 2024-01-01 --end 2025-12-31

[runner.py]
    │
    ├─ params = BrooksV3Params.from_yaml(...)
    ├─ symbols = load_top_n(ranking_csv, n=10)
    └─ for each symbol:
            engine.run(BrooksV3Core(params), bars, cost_model)
                └─ trade records → ../report/<ts>/per_run/<symbol_exchange>/
    │
    ▼
[reporter.py]
    summary = aggregate(all_per_run)
    write_markdown(summary, ../report/<ts>/report.md)
```

## 注意事项

- **轻量引擎 vs cta/skills/data_backtest**：本目录的 `engine.py` 是 Brooks 专用的简化引擎；通用回测请用 [cta/skills/data_backtest/event_driven_backtest.py](../../../skills/data_backtest/event_driven_backtest.py)。两者口径需要保持成本 / 滑点一致。
- **per_trade_pct 风控严**：默认 0.1% 单笔风险；如果回测里看到单笔仓位异常大，检查 `BrooksV3Params.per_trade_pct` 是否被覆盖。
- **产物目录**：本目录所有产出落到 `../report/<YYYYMMDD_HHMMSS>/per_run/<symbol_exchange>/`；这是 Brooks 专用，与 [cta/report/backtest/](../../../report/backtest/) 区分开。
- **不连 vnpy**：本目录禁止 import vnpy。
