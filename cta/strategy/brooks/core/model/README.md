# cta/strategy/brooks/core/model/

## 主要做什么

Brooks v3 的 **XGBoost 评分门控**：把 candidate（HTF + MTF + LTF 共振之后的）+ 历史特征丢进 XGBoost 打分；低于阈值的 candidate 拒绝入场。

## 关键文件

| 文件 | 作用 |
|---|---|
| [labeler.py](labeler.py) | 给 candidate 打 label：`label = (future_pnl_atr > 0) & atr_warmed`（与 cta/strategy/baseline_skill_suite 口径一致） |
| [dataset.py](dataset.py) | candidate × 特征 join → X / y / weight 训练样本表 |
| [train_xgb.py](train_xgb.py) | XGBoost 训练入口；输出落到 [../../models/](../../models/) 为 `.ubj` + `.meta.json` |
| [score_gate.py](score_gate.py) | 推理时使用：`predict(features)` 输出 [0, 1] 评分，与阈值比较决定是否放行 |

## 详细过程

```
[训练阶段] python -m cta.strategy.brooks.core.model.train_xgb --config config/strategy.yaml
    │
    ├─ dataset.build_training_set(candidates, features)
    ├─ labeler.label(candidates) → y
    ├─ XGBoost.fit(X_train, y_train)
    └─ save → ../../models/<model_id>.ubj + .meta.json
    │
[推理阶段] backtest/online 调用
    score_gate.load(model_path)
    score_gate.predict(features_now) → ∈ [0, 1]
        └─ score < threshold → reject candidate
```

## 注意事项

- **label 口径与 baseline 对齐**：`atr_warmed=1` & `is_executed=1` & `future_pnl_atr>0`。和 [cta/strategy/baseline_skill_suite.py](../../../baseline_skill_suite.py) 保持一致，否则训练/线上口径漂移。
- **walk-forward 切分**：禁止随机切；用按时间的 train / valid / test，参考 [cta/skills/ml_augmentation/walk_forward_validation.py](../../../../skills/ml_augmentation/walk_forward_validation.py)。
- **模型版本**：每个 `.ubj` 旁必须有 `.meta.json` 记录 git sha / 训练时间 / 特征列表 / 超参，方便复现。
- **不直接发单**：score_gate 只输出标量，下一步的决策由 [Brooks core strategy.py](../strategy.py) 综合 risk / portfolio 后决定。
- **不允许 import vnpy**：保持 core 的离线/在线复用约束。
