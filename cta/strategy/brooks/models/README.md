# cta/strategy/brooks/models/

## 主要做什么

Brooks v3 **XGBoost 训练产物**存放地。每次 `cta.strategy.brooks.core.model.train_xgb` 跑完都会在这里落一对：

```
<model_id>.ubj         ← XGBoost 模型权重
<model_id>.meta.json   ← 训练元信息（git sha / 训练时间 / 特征列表 / 超参 / 训练数据 range）
```

当前目录可能为空（首次训练前）。

## 注意事项

- **每个 .ubj 必须配 .meta.json**：缺一即视为非法产物；推理时 [score_gate.py](../core/model/score_gate.py) 会拒绝加载没有 meta 的模型。
- **不可手工编辑**：模型文件由 train_xgb 落盘，禁止人工改 .json 字段。
- **不可读到生产路径**：模型读取走 [core/model/score_gate.py](../core/model/score_gate.py)，不允许其它代码直接 `xgb.Booster().load_model("...ubj")`。
- **磁盘膨胀**：每个模型 1-10MB，老模型可删除——但删之前确认 [cta/report/change_log.md](../../../report/change_log.md) 记录了对应实验。
- **跨 symbol 模型**：v3 默认按 cluster / pool 训练池化模型；如果按品种单训，目录应当有命名规约（如 `<cluster>_<interval>_<seed>.ubj`）。
