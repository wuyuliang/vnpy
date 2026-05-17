# cta/model/

## 主要做什么

**模型流水线核心**：候选生成 → 特征拼接 → walk-forward 训练（trade_filter / regime / mfe_mae / final_decision / score_calibration） → OOT real-execution 评估（portfolio_logic 全运行时）→ 报告。一次 `python -m cta.model.model_pipeline ...` 走完上述全链路。

详见 [model.md](model.md)（三段模型设计）+ [model_baseline.md](model_baseline.md)（baseline 候选逻辑）。

## 关键文件

### 入口
| 文件 | 作用 |
|---|---|
| [model_pipeline.py](model_pipeline.py) | CLI 入口 shim，转发到 `pipeline_orchestrator.main()` |
| [pipeline_orchestrator_source.py.txt](pipeline_orchestrator_source.py.txt) | **真正的 orchestrator 实现**（loader 模式，~4000 行） |
| [pipeline_orchestrator.py](pipeline_orchestrator.py) | loader：exec 上面 `.txt` 文件，对外暴露同名符号 |
| [pipeline_cli.py](pipeline_cli.py) | argparse 解析与默认值 |

### 模型组件（"三段 + 决策"）
| 文件 | 作用 |
|---|---|
| [trade_filter_model.py](trade_filter_model.py) | 段 1：候选 → 是否值得交易（二分类） |
| [regime_classifier_model.py](regime_classifier_model.py) | 段 2：当前 regime（trend_up / trend_down / range） |
| [mfe_mae_model.py](mfe_mae_model.py) | 段 3：预期 MFE / MAE（连续值） |
| [final_decision_model.py](final_decision_model.py) | stacking：把三段 + 特征再过一遍做最终 0-1 决策 |
| [cluster_model_registry.py](cluster_model_registry.py) | 按 symbol → cluster → model_dir 路由，支撑线上推理 |

### Pipeline stages
| 文件 | 作用 |
|---|---|
| [pipeline_dataset_prep.py](pipeline_dataset_prep.py) | 数据集准备：candidate × feature join |
| [pipeline_feature_curation.py](pipeline_feature_curation.py) | 特征筛选（相关性 / IC / 重要度） |
| [pipeline_feature_enrichment.py](pipeline_feature_enrichment.py) | 派生特征（如把三段模型预测作为下一段的输入） |
| [pipeline_feature_meaning.py](pipeline_feature_meaning.py) | 特征含义文字化（写报告用） |
| [pipeline_meta_features.py](pipeline_feature_meaning.py) | meta 特征：跨 cluster 的 universal 信号 |
| [pipeline_splits.py](pipeline_splits.py) | walk-forward 切分 train / valid / test |
| [pipeline_param_grids.py](pipeline_param_grids.py) | 超参网格定义 |
| [pipeline_pooling.py](pipeline_pooling.py) | --pool / --group-pool 模式的样本池化 |
| [pipeline_stages.py](pipeline_stages.py) | 阶段编排：train → validate → test 的顺序与产出协议 |
| [pipeline_provenance.py](pipeline_provenance.py) | 训练时落盘 git sha / config / random seed 用于复现 |
| [pipeline_symbol_ranking.py](pipeline_symbol_ranking.py) | 品种排名（tier / cluster 分组依据） |
| [pipeline_diagnostics.py](pipeline_diagnostics.py) | 训练后诊断：auc / IC / 特征重要度对比 |
| [pipeline_html_report.py](pipeline_html_report.py) | 训练报告 HTML 渲染 |
| [group_pool_aggregate.py](group_pool_aggregate.py) | group-pool 模式的跨 group 月报/年报聚合 |

### OOT 评估
| 文件 | 作用 |
|---|---|
| [pipeline_oot_evaluation_source.py.txt](pipeline_oot_evaluation_source.py.txt) | **OOT 主实现**：portfolio_logic runtime 全开的 real-execution（~1600 行） |
| [pipeline_oot_evaluation.py](pipeline_oot_evaluation.py) | loader |
| [oot_gates.py](oot_gates.py) | OOT 期的 trade_filter / regime / mfe_mae 三段 gate 实现 |
| [oot_intrabar.py](oot_intrabar.py) | intrabar 止损跟踪：拉 60/30/15/5/1min bar 模拟逐笔 fill |
| [oot_trade_simulation.py](oot_trade_simulation.py) | 单笔成交模拟（entry/exit/cost/PnL） |
| [oot_position_sizing.py](oot_position_sizing.py) | sizing：单笔最大可亏损反推仓位 |
| [oot_position_lifetime.py](oot_position_lifetime.py) | 持仓 lifetime 表（每笔仓位从开到平的全状态） |
| [oot_portfolio_constraints.py](oot_portfolio_constraints.py) | caps：单品种/cluster/总杠杆/保证金等约束 |
| [oot_metrics.py](oot_metrics.py) | sharpe / calmar / max drawdown / roll cost 计算 |
| [oot_report_writer.py](oot_report_writer.py) / [oot_report_views.py](oot_report_views.py) | OOT 产出（`*_oot_*.csv` / `*_oot_report.md`） |
| [block_reasons.py](block_reasons.py) | 25 种 block_reason 字面量集中定义 |

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
    pipeline_dataset_prep.py
        ├─ candidate × cta/data/feature/* 通用特征 join
        └─ candidate × cta/data/model_feature/* 模型独有特征 join
            │
            ▼
[3] walk-forward training (per window)
    pipeline_stages.py
        ├─ trade_filter_model.fit()
        ├─ regime_classifier_model.fit()
        ├─ mfe_mae_model.fit()
        ├─ score_calibrator.fit()          # → cta/portfolio_logic/score_calibrator.py
        └─ final_decision_model.fit(stacking)
            │
            ▼
[4] OOT real-execution
    pipeline_oot_evaluation_source.py.txt
        ├─ HTF gate（interval_gate.py）
        ├─ ranker（opportunity_ranker.py）
        ├─ throttle（risk_throttle.py）
        ├─ intrabar trailing（oot_intrabar.py / trailing_exit.py）
        ├─ pyramid（pyramid_manager.py）
        └─ caps（oot_portfolio_constraints.py）
            │
            ▼
[5] report
    oot_report_writer.py / pipeline_html_report.py
        └─ cta/report/backtest/<run_tag>/{predictions, details, monthly, summary, *.md}
```

## 注意事项

- **`*.py.txt` loader 模式**：`pipeline_oot_evaluation.py` / `pipeline_orchestrator.py` 都是 33 行 loader，真实代码在同名 `_source.py.txt`。改业务逻辑改 `.txt` 文件，import 路径不变。详见 [refactor_long_files_v2.md](../docs/refactor_long_files_v2.md)。
- **训练/OOT 口径一致性是命**：任何 OOT 阈值（如 `intrabar_stop_loss_pct`）改动必须同时检查训练 label 是否同步（`label_stop_loss_pct`）。[OotEvaluationConfig.__post_init__](../config/model_oot_eval_config.py) 已经自动校验。
- **三段模型独立训练 + stacking 决策**：trade_filter / regime / mfe_mae 各自独立训练（避免一段过拟合带累全局），最后 final_decision_model 做 stacking 集成（含 `stacking_score_threshold` 默认 0.55）。
- **特征穿越防御**：训练数据用 `cta/data/feature/` 已经做过 `shift(1)` 错位的特征；候选 label 用 future_mfe/mae 但**只在训练时可见**，OOT 路径下 `pred_mfe_atr/pred_mae_atr` 才是模型推理值。改这块前先读 [cta/docs/block_reason.md](../docs/block_reason.md) §1。
- **OOT block_reason 25 种**：见 [cta/docs/block_reason.md](../docs/block_reason.md)；新增 block_reason 必须同步 §1 总表 + [tests/test_pipeline_oot_evaluation.py](tests/test_pipeline_oot_evaluation.py) 的 `_CANONICAL_BLOCK_REASONS` 集合。
- **--group-pool × --use-portfolio-logic-runtime 慢**：默认 N(clusters) × M(intervals) × walk-forward windows × 全 portfolio_logic 运行时。先用 `--only-clusters index --interval day` 小规模验证再放开。详见最近 review。
- **HTF gate 自适配**：`htf_intervals` 默认 `("day","60min")`，单 interval 跑批时会自动窄化到实际可用 interval。详见 [block_reason.md](../docs/block_reason.md) §4-§7。
- **测试**：跑 `pytest cta/model/tests/ -v`；`test_leakage_audit.py` 必须绿（特征穿越审计）。
