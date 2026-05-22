# cta/skills/ml_augmentation/tests/

## 主要做什么

[cta/skills/ml_augmentation/](..) ML 辅助层的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_feature_store.py](test_feature_store.py) | 特征落盘 / 读取的轻量 store |
| [test_regime_classifier.py](test_regime_classifier.py) | regime classifier 训练 / 推理接口 |
| [test_mfe_mae_prediction.py](test_mfe_mae_prediction.py) | MFE / MAE 预测接口 |

> 缺 `test_trade_filter_model.py` / `test_walk_forward_validation.py` 是计划中的扩展；新增前先确认 [cta/model/tests/](../../../model/tests/) 没有同名覆盖。

## 注意事项

- **不调真训练 / 真落盘**：测试用极小数据 + tmp_path，禁止依赖 `cta/data/feature/` 真实数据。
- **接口一致性**：本目录模型的 `predict_*` 输出形状必须与 [cta/model/](../../../model/) 同名模型一致，方便互换。测试要 assert 形状。
- **跑测试**：`pytest cta/skills/ml_augmentation/tests/ -v`。
