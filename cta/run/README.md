# cta/run/

## 主要做什么

**批量跑批入口**：一次提交跑多个 (策略 × 品种 × 周期 × 时间窗) 组合，集中产出到 [cta/report/](../report/)。日常研究主要靠这里和 `cta/model/model_pipeline.py`，单独一笔回测用 `cta/strategy/*backtest*.py` 即可。

## 关键文件

| 文件 | 作用 |
|---|---|
| [runner.py](runner.py) | 单一回测的核心 runner：装配 config → 调 backtest engine → 落盘 |
| [multi_runner.py](multi_runner.py) | 多任务批量 runner：按 (symbol, interval, strategy_id) 笛卡尔扫描，支持串/并行、断点续跑 |
| [cta_backtester.py](cta_backtester.py) | 高层入口：把上面两个串起来，提供给 shell 脚本或 CLI |
| [tests/](tests/) | 单元 + smoke test |

## 详细过程

```
[shell 脚本 / CLI]
    │
    ▼
cta_backtester.run(task_set)
    │
    ├─ multi_runner.MultiRunner
    │     ├─ 读 task list（symbol × interval × strategy × time range）
    │     ├─ 去重 + 检查 cta/report/backtest/ 是否已有产物（断点续跑）
    │     └─ 派发到 runner（串行 / 多进程）
    │
    └─ runner.Runner.run_one(task)
          ├─ 加载 config（cta/config/*）
          ├─ 加载数据（cta/data/origin/* + cta/data/feature/*）
          ├─ 实例化策略（cta/strategy/*）
          ├─ 调 event_driven_backtest（cta/skills/data_backtest/*）
          ├─ 写产物到 cta/report/backtest/<run_tag>/
          └─ append 到 cta/report/change_log.md
```

## 注意事项

- **断点续跑是默认**：跑批中途死了/被 kill，重新提交同样 task set 应当跳过已完成的子任务（按 `cta/report/backtest/<tag>` 存在即跳）。
- **并行边界**：若开启多进程，每个子进程独立读 parquet、独立写 csv；**不要**共享内存中的 DataFrame，避免数据竞态。
- **任务参数 vs 配置文件**：本目录代码做 task → 调用映射；具体的策略/手续费/止损参数从 `cta/config/` 来。不要在 `multi_runner.py` 里硬编码业务参数。
- **不重复造轮子**：单笔回测请用 `cta/strategy/skill_tight_range_backtest.py` 这类策略级入口；模型 walk-forward 请用 `cta/model/model_pipeline.py`。本目录只承担"批量"角色。
- **测试**：
  - `test_no_500plus_files.py` 卡 500 行硬上限，所有改动后必跑；
  - `test_docs_sync.py` 卡 README / 关键文档存在；
  - `test_multi_runner.py` / `test_run_script_actions.py` 是端到端 smoke。
