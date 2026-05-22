# cta/model/feature/tests/

## 主要做什么

[cta/model/feature/](..) 候选 → 训练样本拼接层的单元测试。重点防（1）candidate schema 漂移、（2）label_class 误判正例、（3）特征穿越在拼接环节渗透。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_candidate_split_modules_contract.py](test_candidate_split_modules_contract.py) | candidate_baseline_bridge → candidate_training_dataset → training_feature_builder 三段的接口契约 |
| [test_candidate_training_dataset.py](test_candidate_training_dataset.py) | 全量端到端 |
| [test_candidate_training_dataset_part01.py](test_candidate_training_dataset_part01.py) ~ [part03.py](test_candidate_training_dataset_part03.py) | 上面的拆分（part01: join 逻辑、part02: label_class 判正、part03: atr_warmed 过滤） |
| [test_model_feature_builder.py](test_model_feature_builder.py) | training_feature_builder：X / y / weight 最终装配 |

## 注意事项

- **schema 是契约**：[candidate_schema.py](../candidate_schema.py) 字段集合改动必须先在本目录加一个测试 fail，再去改 schema，避免下游 silent failure。
- **label_class 判正口径**：只允许 `is_executed=1 & atr_warmed=1 & 真实执行收益 > 0`。其它行 label 必须 0。`test_candidate_training_dataset_part02.py` 覆盖这条。
- **atr_warmed=0 必 drop**：前 ~14 根 bar 的 ATR 未热身，label / 特征都不可信。
- **特征 join 不穿越**：通用特征 parquet 已在 `cta/feature/` 做过 shift，本目录不再 shift。`test_candidate_training_dataset.py` 校验 join 时的时间一致性。
- **跑测试**：`pytest cta/model/feature/tests/ -v`。
