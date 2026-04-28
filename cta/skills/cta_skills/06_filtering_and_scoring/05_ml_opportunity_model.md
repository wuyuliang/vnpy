# ML 机会模型 / ML Opportunity Model

> 归属章节：`06_filtering_and_scoring/` · 前置：`01-04 本章` + `02_price_action/` · 关联：`cta/strategy/brooks/core/model/`

## 1. Skill 定义

把 setup_quality / breakout_quality / context_score / rr / pa_* 特征等全部输入 ML（XGBoost / LightGBM / NN），学习"该 setup 达到目标 R 倍数"的概率。用模型分数替换规则 gate。

## 2. 解决什么问题

- 痛点 1：规则 gate 维度有限且权重人工挑。
- 痛点 2：不同 setup / 品种应有不同权重，手工维护困难。
- 痛点 3：规则过滤掉低分时未考虑相关性。
- 增量价值：用 ML 把所有规则 gate 统一成一个概率 p，仓位 / 过滤都用 p。

## 3. 适用市场 / 适用场景

- **品种类别**：有足够历史交易数据（≥ 5000 笔）的品种
- **周期**：所有
- **行情状态前提**：任意
- **不适用场景**：新策略前期样本不足；数据漂移严重的时段

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| label | 是否达到 target_rr 且未先触 stop | target_rr=2.0, bars=20 | 0/1 | `cta/strategy/brooks/core/model/labeler.py` |
| features | setup_q / breakout_q / context / rr / pa_* | — | — | `cta/strategy/brooks/core/model/dataset.py` |
| model | XGBoost / LightGBM | binary:logistic | — | `cta/strategy/brooks/core/model/train_xgb.py` |
| AUC / precision@top-k | 模型评估 | — | AUC ≥ 0.65 上线 | 同上 |
| 校准曲线 | 概率与实际命中率 | — | 偏差 < 0.05 | TODO |

## 5. 常见策略映射

### 策略 A：ML Gate + Sizing
- **label**：`1 if MFE ≥ target_rr × risk before MAE ≥ risk else 0`
- **features**：（≥ 30 维）
  - setup_quality / breakout_quality / context_score / rr
  - pa_tight_range_count / pa_h1_l1_signal / pa_micro_channel_count
  - regime_label one-hot / trend_score / range_score
  - atr_pct / vol_regime one-hot
  - HTF_dir / MTF_dir / LTF_dir
- **模型**：XGBoost binary:logistic，early stopping on validation logloss
- **应用**：
  - `p < 0.4` → skip
  - `p ∈ [0.4, 0.6]` → base_size
  - `p ≥ 0.6` → 1.5x size
- **失败模式**：
  - 数据漂移 → 按季度重训
  - 特征缺失 → 需 fallback 规则

## 6. 代码模块设计

```text
cta/strategy/{name}/model/
├── labeler.py            # 构造 label
├── dataset.py            # 拼接 features
├── train_xgb.py          # 训练 + 评估
├── score_gate.py         # 推理 + gate
└── artifacts/
    ├── xgb_<ts>.ubj
    └── xgb_<ts>.meta.json
```

```python
from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class MLGateResult:
    probability: float
    gate_pass: bool
    size_multiplier: float

def predict_ml_gate(
    features: pd.Series,
    model_path: str,
    thresholds: dict[str, float] | None = None,
) -> MLGateResult:
    """
    thresholds: {'skip':0.4, 'boost':0.6}
    """
    ...

def build_training_dataset(
    trade_log_parquet: str,
    feature_loader_fn,
    target_rr: float = 2.0,
    max_bars: int = 20,
) -> pd.DataFrame:
    """
    从 trade log 和 feature 仓库构造监督学习集。
    已有参考: cta/strategy/brooks/core/model/dataset.py
    """
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：AUC / precision@top-10% / 校准误差 / OOS 各概率桶胜率 / 与规则 gate 的 PnL 对比 / Sharpe / turnover / 概率分布稳定性
- **分层评估**：按品种 / 按 setup_type / 按时间段
- **稳健性检验**：
  1. walk-forward 重训（参考 `09_ml_augmentation/05_walk_forward_validation.md`）
  2. 特征打乱测试（shuffle important feature，AUC 应显著下降）
  3. 不同 target_rr（1.5 / 2.0 / 2.5）的模型
  4. 成本 ×2 后 PnL 仍正

## 8. 常见错误

- label 用了未来信息（含 entry bar 后的 bar）→ 过拟合
- feature 不去重 → 有的 feature 实际是 label 的泄漏
- 把回测的 ML 分当成未来概率使用，但训练未滚动更新
- 不做校准，阈值挑在样本内

## 9. 迭代方向

- v1：XGBoost gate（已在 Brooks v3 实现）
- v2：多任务学习（同时预测命中率 / MFE / MAE）
- v3：图模型 / Transformer（把多周期序列直接作为输入）
- v4：在线学习 + 概念漂移检测

## 10. 与其他 Skills 的关系

- **依赖**：`06_filtering_and_scoring/01-04 全部`、`09_ml_augmentation/`
- **被依赖**：`07_position_and_portfolio/02_vol_targeting.md`（p × base_size）
- **互补**：`09_ml_augmentation/02_regime_classifier.md`（regime 模型可为本模型的一个特征）
