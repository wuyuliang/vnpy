# cta/model/

## 主要做什么

**模型流水线核心**：候选生成 → 特征拼接 → walk-forward 训练（trade_filter / regime / mfe_mae / final_decision / score_calibration） → OOT real-execution 评估（portfolio_logic 全运行时）→ 报告。一次 `python -m cta.model.model_pipeline ...` 走完上述全链路。

详见 [model.md](model.md)（三段模型设计）+ [model_baseline.md](model_baseline.md)（baseline 候选逻辑）。

## 关键文件

### 入口
| 文件 | 作用 |
|---|---|
| [model_pipeline.py](model_pipeline.py) | CLI 入口 shim，转发到 `pipeline_orchestrator.main()` |
| [orchestration/pipeline_orchestrator.py](orchestration/pipeline_orchestrator.py) | pipeline facade：统一 re-export 主流程 API |
| [orchestration/pipeline_run.py](orchestration/pipeline_run.py) | 单次 pipeline 主流程：candidate → feature → train → OOT → report |
| [orchestration/pipeline_cli.py](orchestration/pipeline_cli.py) | argparse 解析与 pool/group-pool dispatch |

### 阶段目录
| 目录 | 阶段 | 作用 |
|---|---|---|
| [dataset/](dataset/) | Step 1-2 | 候选样本、通用特征拼接、特征筛选、walk-forward split、pool/group-pool 样本组织 |
| [training/](training/) | Step 3 | 三段模型、final decision、模型 registry、参数搜索与 AUC gap 约束 |
| [orchestration/](orchestration/) | Step 0/全局 | CLI、主流程编排、多 interval / group-pool 调度 |
| [oot/](oot/) | Step 4 | OOT real-execution、gate、intrabar、仓位 sizing、组合约束、block_reason |
| [reporting/](reporting/) | Step 5 | OOT 报告、HTML、aggregate、diagnostics、provenance |

### 模型组件（"三段 + 决策"）
| 文件 | 作用 |
|---|---|
| [training/trade_filter_model.py](training/trade_filter_model.py) | 段 1：候选 → 是否值得交易（二分类） |
| [training/regime_classifier_model.py](training/regime_classifier_model.py) | 段 2：当前 regime（trend_up / trend_down / range） |
| [training/mfe_mae_model.py](training/mfe_mae_model.py) | 段 3：预期 MFE / MAE（连续值） |
| [training/final_decision_model.py](training/final_decision_model.py) | stacking：把三段 + 特征再过一遍做最终 0-1 决策 |
| [training/cluster_model_registry.py](training/cluster_model_registry.py) | 按 symbol → cluster → model_dir 路由，支撑线上推理 |

### Pipeline stages
| 文件 | 作用 |
|---|---|
| [dataset/pipeline_dataset_prep.py](dataset/pipeline_dataset_prep.py) | 数据集准备：candidate × feature join、synthetic fallback、walk-forward split |
| [dataset/pipeline_feature_curation.py](dataset/pipeline_feature_curation.py) | causality manifest、leakage name filter、unaudited feature list |
| [dataset/pipeline_feature_enrichment.py](dataset/pipeline_feature_enrichment.py) | 派生特征（如把三段模型预测作为下一段的输入） |
| [dataset/pipeline_feature_meaning.py](dataset/pipeline_feature_meaning.py) | 特征含义文字化（写报告用） |
| [dataset/pipeline_meta_features.py](dataset/pipeline_meta_features.py) | OOF base prediction 与 final decision meta 特征 |
| [training/pipeline_param_grids.py](training/pipeline_param_grids.py) | 三类模型超参网格与 train/valid AUC gap 选参 |
| [dataset/pipeline_pooling.py](dataset/pipeline_pooling.py) | --pool / --group-pool 模式的样本池化 |
| [reporting/pipeline_outputs.py](reporting/pipeline_outputs.py) | provenance、decile、group runtime bundle、shared HTF OOT 重算 |
| [dataset/pipeline_symbol_ranking.py](dataset/pipeline_symbol_ranking.py) | 品种排名（tier / cluster 分组依据） |
| [reporting/pipeline_diagnostics.py](reporting/pipeline_diagnostics.py) | 训练后诊断：auc / IC / 特征重要度对比 |
| [reporting/pipeline_html_report.py](reporting/pipeline_html_report.py) | 训练报告 HTML 渲染 |
| [reporting/group_pool_aggregate.py](reporting/group_pool_aggregate.py) | group-pool 模式的跨 group 月报/年报聚合 |

### OOT 评估
| 文件 | 作用 |
|---|---|
| [oot/pipeline_oot_evaluation.py](oot/pipeline_oot_evaluation.py) | **OOT 主实现**：portfolio_logic runtime 全开的 real-execution |
| [oot/pipeline_oot_evaluation_base.py](oot/pipeline_oot_evaluation_base.py) | OOT 共享 imports、block reason 日志与合约规格 helper |
| [oot/oot_gates.py](oot/oot_gates.py) | OOT 期的 trade_filter / regime / mfe_mae 三段 gate 实现 |
| [oot/oot_intrabar.py](oot/oot_intrabar.py) | intrabar 止损跟踪：拉 60/30/15/5/1min bar 模拟逐笔 fill |
| [oot/oot_trade_simulation.py](oot/oot_trade_simulation.py) | 单笔成交模拟（entry/exit/cost/PnL） |
| [oot/oot_position_sizing.py](oot/oot_position_sizing.py) | sizing：单笔最大可亏损反推仓位 |
| [oot/oot_position_lifetime.py](oot/oot_position_lifetime.py) | 持仓 lifetime 表（每笔仓位从开到平的全状态） |
| [oot/oot_portfolio_constraints.py](oot/oot_portfolio_constraints.py) | caps：单品种/cluster/总杠杆/保证金等约束 |
| [oot/oot_metrics.py](oot/oot_metrics.py) | sharpe / calmar / max drawdown / roll cost 计算 |
| [reporting/oot_report_writer.py](reporting/oot_report_writer.py) / [reporting/oot_report_views.py](reporting/oot_report_views.py) | OOT 产出（`*_oot_*.csv` / `*_oot_report.md`） |
| [oot/block_reasons.py](oot/block_reasons.py) | 25 种 block_reason 字面量集中定义 |

### 工具
| 子目录 | 作用 |
|---|---|
| [feature/](feature/) | 候选 → 训练样本的特征拼接（详见其 README） |
| [tools/](tools/) | 离线小工具：leakage_audit / 自动 flag 持续亏损品种 / causality manifest seed |
| [tests/](tests/) | 单元 + 集成测试（详见其 README） |

## 详细过程

```
[1] candidate generation
    cta/strategy/baseline_skill_suite.generate_candidate_opportunities()
        ├─ 拼日历 + 价格行为特征
        └─ 标 label: future_mfe_atr / future_mae_atr / label_executed
            │
            ▼
[2] dataset prep
    dataset/pipeline_dataset_prep.py
        ├─ candidate × cta/data/feature/* 通用特征 join
        └─ candidate × cta/data/model_feature/* 模型独有特征 join
            │
            ▼
[3] walk-forward training (per window)
    orchestration/pipeline_stages.py
        ├─ training/trade_filter_model.fit()
        ├─ training/regime_classifier_model.fit()
        ├─ training/mfe_mae_model.fit()
        ├─ score_calibrator.fit()          # → cta/portfolio_logic/score_calibrator.py
        └─ final_decision_model.fit(stacking)
            │
            ▼
[4] OOT real-execution
    oot/pipeline_oot_evaluation.py
        ├─ HTF gate（interval_gate.py）
        ├─ ranker（opportunity_ranker.py）
        ├─ throttle（risk_throttle.py）
        ├─ intrabar trailing（oot/oot_intrabar.py / trailing_exit.py）
        ├─ pyramid（pyramid_manager.py）
        └─ caps（oot_portfolio_constraints.py）
            │
            ▼
[5] report
    reporting/oot_report_writer.py / reporting/pipeline_html_report.py
        └─ cta/backtest/<run_tag>/{predictions, details, monthly, summary, *.md}
```

## 注意事项

- **pipeline 已按阶段拆分**：`cta/model` root 只保留 `model_pipeline.py` 入口，业务实现放在 `dataset/`、`training/`、`orchestration/`、`oot/`、`reporting/`。改业务逻辑时优先进入对应阶段目录。
- **训练/OOT 口径一致性是命**：任何 OOT 阈值（如 `intrabar_stop_loss_pct`）改动必须同时检查训练 label 是否同步（`label_stop_loss_pct`）。[OotEvaluationConfig.__post_init__](../config/model_oot_eval_config.py) 已经自动校验。
- **三段模型独立训练 + stacking 决策**：trade_filter / regime / mfe_mae 各自独立训练（避免一段过拟合带累全局），最后 final_decision_model 做 stacking 集成（含 `stacking_score_threshold` 默认 0.55）。
- **特征穿越防御**：训练数据用 `cta/data/feature/` 已经做过 `shift(1)` 错位的特征；候选 label 用 future_mfe/mae 但**只在训练时可见**，OOT 路径下 `pred_mfe_atr/pred_mae_atr` 才是模型推理值。改这块前先读 [cta/docs/block_reason.md](../docs/block_reason.md) §1。
- **OOT block_reason 25 种**：见 [cta/docs/block_reason.md](../docs/block_reason.md)；新增 block_reason 必须同步 §1 总表 + [tests/test_pipeline_oot_evaluation.py](tests/test_pipeline_oot_evaluation.py) 的 `_CANONICAL_BLOCK_REASONS` 集合。
- **--group-pool × --use-portfolio-logic-runtime 慢**：默认 N(clusters) × M(intervals) × walk-forward windows × 全 portfolio_logic 运行时。先用 `--only-clusters index --interval day` 小规模验证再放开。详见最近 review。
- **HTF gate 自适配**：`htf_intervals` 默认 `("day","60min")`，单 interval 跑批时会自动窄化到实际可用 interval。详见 [block_reason.md](../docs/block_reason.md) §4-§7。
- **测试**：跑 `pytest cta/model/tests/ -v`；`test_leakage_audit.py` 必须绿（特征穿越审计）。
