# cta/report/

## 主要做什么

**回测/训练/审计**的全部落盘产物存放地。**只写不读业务**——所有读这里的代码都应该是离线分析、报告渲染、对账校验，禁止把它作为生产数据上游。

## 目录结构

```
cta/report/
├── change_log.md       ← 必读：每次跑批 / 重大变更的一行式记录（手写 + 自动 append 并用）
├── backtest/           ← 单次回测 / 训练 / OOT 产物，按 <YYYYMMDD>_<POOL/GRP_xxx>_<interval>_<side>_model_pipeline 命名
│   └── <run_tag>/
│       ├── *_model_report.md
│       ├── *_predictions.csv
│       ├── *_metrics.csv
│       ├── *_top10_feature_importance.csv
│       ├── *_oot_monthly.csv / _oot_summary.csv / _oot_trade_details.csv
│       ├── *_throttle_log.csv / *_position_lifetime.csv
│       └── ...
├── data/               ← 离线数据 dump 落盘（与 cta/data/ 区分；这里是一次性导出/分析快照）
├── data_audit/         ← 数据审计产物：cross_source_audit、缺数据报表等
├── render/             ← report 渲染相关：HTML 模板、figure 中间件
└── run_log/            ← 长跑批的日志归档（可清理）
```

## 详细过程

### 谁写到这里
- `cta/model/model_pipeline.py` → `backtest/<run_tag>/*`（训练 + OOT 全产物）
- `cta/strategy/*backtest*.py` → `backtest/<run_tag>/*`（单策略回测）
- `cta/data_code/cross_source_audit.py` → `data_audit/*`
- `cta/skills/data_backtest/event_driven_backtest.py` → 写入调用者指定的 `backtest/<...>` 子目录
- 手动 append → `change_log.md`（每次大改后由开发者补充）

### 谁读这里
- `cta/model/reporting/group_pool_aggregate.py` → 读 `backtest/*_oot_*.csv` 做聚合
- 各种报告渲染脚本 → 读对应 run_tag 产生 markdown / HTML
- 人 / review agent → 读 `change_log.md` + `*_model_report.md`

## 关键文件

| 文件 | 作用 |
|---|---|
| [change_log.md](change_log.md) | 项目唯一的"日志总线"：每次跑批 / bug 修复 / 实验后追加一行（日期 / 分支 / 任务 / 文件 / 结论） |

## 注意事项

- **目录只写不读**：禁止任何在 `cta/run/`、`cta/strategy/`、`cta/model/` 等业务模块里 `pd.read_csv("cta/report/...")` 当生产输入。报告分析脚本（一次性 jupyter / `cta/model/reporting/group_pool_aggregate.py`）可读。
- **新跑批的目录名**：必须含日期（`YYYYMMDD`）+ 实验主体（POOL/GRP_CLUSTER_xxx）+ interval + side + suffix `_model_pipeline`。不带日期的目录会被 cleanup 脚本清掉。
- **不要往 `cta/report/backtest/<run>` 里手工添东西**：所有内容都应该可由原始 command 复现；手工产物（如人写的复盘 md）请放到 `change_log.md` 或 `cta/docs/review/`。
- **`change_log.md` 是必填**：[CLAUDE.local.md](../../CLAUDE.local.md) 明确要求每次任务后必须写一条；不要省略。
- **磁盘清理**：`run_log/` 与历史 `backtest/<old_tag>/` 可以删除，但删之前请确认 `change_log.md` 里的结论还在；删 backtest 前后 `data_audit/` 数据要保留。
- **size 警告**：本目录会快速膨胀（每次 OOT 产物 50~500MB）。定期跑 cleanup 脚本前先用 `du -sh cta/report/backtest/*/` 看 top 10。
