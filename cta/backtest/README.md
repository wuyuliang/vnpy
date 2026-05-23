# cta/backtest/

## 主要做什么

**回测报告 + 模型训练产物的默认输出目录**。从 2026-05-23 起，
`DEFAULT_REPORT_ROOT`（[cta/config/baseline_skill_suite_config.py:56](../config/baseline_skill_suite_config.py:56)）
指向本目录，所有 `model_pipeline` / OOT evaluation / `event_driven_backtest` 的
报告默认落到这里。

历史路径 `cta/report/backtest/` 已废弃，新产物不再写入该目录；存量报告保留在
原位，可手动迁移或保留参考。

## 目录约定

```
cta/backtest/
├── {YYYYMMDD}_{SYM}_{INTERVAL}_{SIDE}_model_pipeline/                # 单 symbol pipeline
├── {YYYYMMDD}_GRP_{CLUSTER}_{INTERVAL}_{SIDE}_model_pipeline/        # group-pool pipeline
├── {YYYYMMDD}_POOL_{INTERVAL}_{SIDE}_model_pipeline/                 # 全 pool pipeline
├── {YYYYMMDD}_GROUP_POOL_{GROUP_BY}_{SIDE}_portfolio_logic_runtime/  # 共享 HTF runtime
├── oot_{YYYYMMDD}_{HHMMSS}_{TAG}/                                    # 多 run 聚合 OOT
└── {YYYYMMDD}_cluster_registry_{GROUP_BY}_{SIDE}.json                # 模型注册表
```

## 回测入口（脚本位置未变）

| 入口 | 位置 | 用途 |
|---|---|---|
| 事件驱动回测引擎 | [cta/skills/data_backtest/event_driven_backtest.py](../skills/data_backtest/event_driven_backtest.py) | 单策略历史回放（fill / slippage / margin） |
| 策略级 CLI | [cta/strategy/baseline_backtest_cli.py](../strategy/baseline_backtest_cli.py)、[cta/strategy/skill_tight_range_backtest.py](../strategy/skill_tight_range_backtest.py)、[cta/strategy/backtest_price_action_breakout.py](../strategy/backtest_price_action_breakout.py) | 按策略组织参数与产出 |
| 模型 pipeline + OOT | [cta/model/model_pipeline.py](../model/model_pipeline.py) → [cta/model/oot/pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | walk-forward 训练 + OOT real-execution |

## 注意事项

- **本目录只放产物**，**不要放脚本代码**。新增回测脚本仍应放到上面表里的源码目录。
- 单次 run 产物可能 ~100MB（含特征 / 模型 / 报告 / 图表），定期清理或归档。
- 如要自定义输出位置，用 `--output-root` flag 覆盖默认：
  ```bash
  python3 -m cta.model.model_pipeline --output-root /tmp/oot_smoke ...
  ```
- 真实回测约定（手续费、滑点、成交规则）见 [cta/skills/data_backtest/](../skills/data_backtest/) 与 [cta/config/](../config/)。

## 迁移说明（2026-05-23）

- 旧路径 `cta/report/backtest/` 仍保留存量报告，但**新报告不再写入**
- `DEFAULT_REPORT_ROOT` 切换在 [baseline_skill_suite_config.py:56](../config/baseline_skill_suite_config.py:56)
- 历史文档（`cta/docs/review/*.md`）中的旧路径引用不做批量替换，作为时间快照保留
- 如需要把旧产物移过来：
  ```bash
  mv cta/report/backtest/* cta/backtest/  # 选择性迁移；注意不要覆盖
  ```
