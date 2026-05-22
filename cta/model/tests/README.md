# cta/model/tests/

## 主要做什么

[cta/model/](..) 流水线全部测试：pipeline orchestrator、OOT real-execution、三段模型 + stacking、group-pool 模式、cluster 路由、特征 leakage 审计。

数量较多（~25 文件），按主题分组。

## 关键测试（按主题）

### Pipeline 主流程
| 文件 | 覆盖 |
|---|---|
| [test_model_pipeline_part01.py](test_model_pipeline_part01.py) ~ [part07.py](test_model_pipeline_part07.py) | pipeline_orchestrator 拆出来的 7 个集成测试块（part01: 入口与 CLI、part02: HTF/portfolio_logic 运行时、...） |
| [test_pipeline_cli.py](test_pipeline_cli.py) | argparse 解析与默认值 |
| [test_pipeline_dataset_prep.py](test_pipeline_dataset_prep.py) | candidate × feature join |
| [test_pipeline_feature_curation.py](test_pipeline_feature_curation.py) | 特征筛选（相关性 / IC / 重要度） |
| [test_pipeline_meta_features.py](test_pipeline_meta_features.py) | meta 特征 |
| [test_pipeline_feature_meaning.py](test_pipeline_feature_meaning.py) | 特征含义文字化（用于报告） |
| [test_pipeline_diagnostics.py](test_pipeline_diagnostics.py) | 训练诊断（auc / IC 等） |

### OOT
| 文件 | 覆盖 |
|---|---|
| [test_pipeline_oot_evaluation.py](test_pipeline_oot_evaluation.py) | OOT real-execution 5 个核心测试：empty、HTF intervals 自适配、HTF gate disable、block_reason 分布日志、canonical block_reason AST 巡检 |
| [test_oot_modules.py](test_oot_modules.py) | oot_intrabar / oot_trade_simulation / oot_position_sizing / oot_metrics 等模块 |
| [test_oot_report_writer.py](test_oot_report_writer.py) | OOT 产出文件格式 |

### 模型 / 路由
| 文件 | 覆盖 |
|---|---|
| [test_models_core.py](test_models_core.py) | trade_filter / regime / mfe_mae 三段模型 + final_decision stacking |
| [test_cluster_model_registry.py](test_cluster_model_registry.py) | cluster_registry.json 读写 + symbol→model_dir 路由 |
| [test_group_pool_mode.py](test_group_pool_mode.py) | --group-pool 端到端集成 |
| [test_group_pool_aggregate.py](test_group_pool_aggregate.py) | group-pool 跨 group 聚合月报/年报 |

### 审计 / 防穿越
| 文件 | 覆盖 |
|---|---|
| [test_leakage_audit.py](test_leakage_audit.py) | **特征穿越审计工具**：训练 / valid / test 不能用未来 bar；挂了禁止上线 |
| [test_blind_spot_coverage.py](test_blind_spot_coverage.py) | 训练集覆盖盲区检测（某些 regime / cluster 样本太少） |
| [test_auto_flag_persistent_loss_symbols.py](test_auto_flag_persistent_loss_symbols.py) | 自动 flag 持续亏损品种工具 |
| [test_backward_compat.py](test_backward_compat.py) | 旧 cluster_registry.json / 旧产物格式向后兼容 |

## 注意事项

- **`test_leakage_audit.py` 不可挂**：挂了说明流水线引入了未来信息，**严禁上线**。
- **`test_model_pipeline_part0X.py` 跑得慢**：每个 part 至少几秒，加起来 30-60 秒。本地开发推荐 `-k <subset>` 缩小范围。
- **block_reason canonical 集合**：[test_pipeline_oot_evaluation.py](test_pipeline_oot_evaluation.py) 里的 `_CANONICAL_BLOCK_REASONS` 是 25 个字面量的白名单；新增 block_reason 必须同步更新这里 + [cta/docs/block_reason.md](../../docs/block_reason.md) §1。
- **跑测试**：
  - 全量：`pytest cta/model/tests/ -v`
  - 快速冒烟：`pytest cta/model/tests/test_pipeline_oot_evaluation.py cta/model/tests/test_leakage_audit.py -v`
  - 长测试（part01-07）：`pytest cta/model/tests/ -k "model_pipeline_part" -v`
