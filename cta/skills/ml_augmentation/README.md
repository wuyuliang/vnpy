# cta/skills/ml_augmentation/

## 主要做什么

**ML 辅助层**：与 [cta/model/](../../model/) 主流水线互补的轻量 ML 工具集。主流水线是"训练 + walk-forward + 三段 stacking + OOT 全栈"，本目录是"单点 ML 增强"。

## 关键文件

| 文件 | 作用 |
|---|---|
| [trade_filter_model.py](trade_filter_model.py) | trade_filter 的 skill 层接口，封装训练好的 sklearn / LightGBM 模型 |
| [regime_classifier.py](regime_classifier.py) | regime classifier 的 skill 层接口 |
| [mfe_mae_prediction.py](mfe_mae_prediction.py) | MFE / MAE 预测的 skill 层接口 |
| [feature_store.py](feature_store.py) | 特征落盘 / 读取的轻量 store（与 [cta/data/feature/](../../data/feature/) 协作） |
| [walk_forward_validation.py](walk_forward_validation.py) | walk-forward 切分与验证小工具，可在 notebook 里独立使用 |

## 详细过程

```
[研究 notebook 或 skill 调用]
    ├─ feature_store.load(symbol, interval, date_range)
    │       → 拿到对齐好的特征 DataFrame
    │
    ├─ walk_forward_validation.split(...)
    │       → train/valid/test indices
    │
    ├─ trade_filter_model.fit(X_train, y_train)
    │   regime_classifier.fit(X_train, y_train)
    │   mfe_mae_prediction.fit(X_train, y_train)
    │       → 三个模型独立训练
    │
    ▼
[在策略/skill 里推理]
    trade_filter_model.predict_proba(X_now) → 是否值得做
    regime_classifier.predict(X_now)         → 当前 regime
    mfe_mae_prediction.predict(X_now)        → 预期 MFE / MAE
```

## 注意事项

- **不是生产流水线**：生产用 [cta/model/](../../model/) 的 walk-forward + cluster_registry 路由。本目录是"轻量、可拼装、给 notebook / skill 用"。
- **三段模型协议一致**：本目录的 `predict_*` 输出形状/字段必须与 [cta/model/training/trade_filter_model.py](../../model/training/trade_filter_model.py) 等保持一致，方便互换。
- **feature_store 不替代 feature_loader**：[cta/feature/feature_loader.py](../../feature/feature_loader.py) 是生产 loader；本文件的 store 是研究态便捷接口。
- **走 walk-forward**：禁止用 `train_test_split` 这种随机切；必须用 [walk_forward_validation.py](walk_forward_validation.py) 按时间切。
- **测试**：`pytest cta/skills/ml_augmentation/tests/ -v`。
