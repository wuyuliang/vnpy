# cta/strategy/brooks/report/

## 主要做什么

Brooks v3 **每次回测/dry-run 跑批的产物归档**。每次跑产生一个 `<YYYYMMDD_HHMMSS>/` 子目录，内部按 `per_run/<symbol_exchange>/` 组织。

```
report/
├── 20260419_103632/        ← 一次跑批
│   ├── summary.csv
│   ├── report.md
│   └── per_run/
│       ├── RB0_SHFE/
│       │   ├── trades.parquet
│       │   ├── equity.csv
│       │   └── report.md
│       ├── CU0_SHFE/
│       └── ...
├── 20260419_104417/
├── ...
└── 20260419_160049/
```

历史 v1 报告（`20260419_103632/`）保留作为 v3 的对比基线，**禁止删除**。

## 注意事项

- **目录命名**：`YYYYMMDD_HHMMSS`，由 [../backtest/runner.py](../backtest/runner.py) 自动生成；禁止手工创建。
- **只写不改**：跑完一次落盘，**禁止**手工修改 csv / parquet；要修就重跑。
- **per_run/<symbol_exchange>/** 子目录的所有内容都属于"一次回测的产物"，没必要为每个 SYM 目录写 README（自描述）。
- **删除前确认 change_log**：[cta/report/change_log.md](../../../report/change_log.md) 是否已经记录该次实验的结论。v1 baseline 报告（20260419_103632/）**禁止删除**。
- **和 cta/report/backtest/ 的区别**：本目录是 **Brooks 专用**格式（`per_run/` 子层），仅服务 [../backtest/](../backtest/) 与 [../online/](../online/)。统一回测产物在 [cta/report/backtest/](../../../report/backtest/)。
