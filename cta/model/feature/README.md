# cta/model/feature/

## 主要做什么

**候选 → 训练样本**的特征拼接层。把 [cta/strategy/baseline_skill_suite.py](../../strategy/baseline_skill_suite.py) 产出的 candidate 事件流，与 [cta/data/feature/](../../data/feature/) 的通用特征、[cta/data/model_feature/](../../data/model_feature/) 的模型独有特征 join 起来，落成统一 schema 的训练样本表。

详见 [candidate_vs_executed_samples.md](candidate_vs_executed_samples.md)（候选样本 vs 已执行样本的口径区分）。

## 关键文件

| 文件 | 作用 |
|---|---|
| [candidate_schema.py](candidate_schema.py) | **统一的 candidate DataFrame schema**：核心字段、`sample_status` → `block_reason` 兜底映射（5 种 fallback reason） |
| [candidate_baseline_bridge.py](candidate_baseline_bridge.py) | 把 baseline_skill_suite 产出的 candidate 桥接到本目录的统一 schema |
| [candidate_training_dataset.py](candidate_training_dataset.py) | candidate × 通用特征 × 模型特征 join；处理 atr_warmed / label_class 等口径 |
| [training_feature_builder.py](training_feature_builder.py) | 训练样本特征 frame 的最终装配（含 P/N 平衡 / shift 检查） |
| [candidate_vs_executed_samples.md](candidate_vs_executed_samples.md) | 文档：训练候选 vs 已执行样本的 label 口径 |

## 详细过程

```
[1] cta/strategy/baseline_skill_suite.generate_candidate_opportunities()
        └── 输出原始候选（含 future_mfe_atr / future_mae_atr label）
                │
                ▼
[2] candidate_baseline_bridge.py
        └── 统一字段名 / dtype / 缺省值，落到 candidate_schema 定义的 schema
                │
                ▼
[3] candidate_training_dataset.py
        ├── join 通用特征（cta/data/feature/*/symbol/<date>.parquet）
        ├── join 模型独有特征（cta/data/model_feature/*/symbol/<date>.parquet）
        ├── drop atr_warmed=0 的行（前 ~14 根 ATR 未热身）
        └── label_class = 1 仅当 is_executed=1 & atr_warmed=1 & 真实执行收益 > 0
                │
                ▼
[4] training_feature_builder.py
        └── 最终 X / y / weight，喂给 cta/model/pipeline_stages.py
```

## 注意事项

- **schema 是契约**：所有上游策略产出的 candidate DataFrame 必须满足 [candidate_schema.py](candidate_schema.py) 的字段；缺字段会让 `candidate_training_dataset` 静默丢行。
- **5 种 fallback block_reason**：当上游没塞 `block_reason` 时按 `sample_status` 兜底——`filtered_by_rule` / `risk_rule_blocked` / `capacity_blocked` / `execution_rule_blocked` / `next_bar_not_triggered`。新增 sample_status 必须同步加 reason 并更新 [cta/docs/block_reason.md](../../docs/block_reason.md) §20。
- **特征穿越**：通用特征 parquet 已经在 `cta/feature/` 做过 shift 错位，本目录在 join 时不再 shift；模型独有特征同理。**新加特征前先确认它没穿越**，否则 `cta/model/tools/leakage_audit.py` 会报。
- **atr_warmed 必须 drop**：前 ~14 根 bar 的 ATR 是 warmup 残值，label 用它会严重偏差。`run_model_pipeline` 已经自动 drop，本目录代码不要绕过。
- **label_class 口径**：只能在 `is_executed=1 & atr_warmed=1` 时按"入场后逐 bar 跟踪止损的真实执行收益 > 0"判正；其余行（not_triggered / filtered / warmup）label 必须 0。改这块前必读 [candidate_vs_executed_samples.md](candidate_vs_executed_samples.md)。
- **测试**：跑 `pytest cta/model/feature/tests/ -v`；`test_candidate_split_modules_contract.py` 校验上下游接口，**不能挂**。
