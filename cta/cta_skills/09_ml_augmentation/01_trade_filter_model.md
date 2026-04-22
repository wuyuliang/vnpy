# 交易过滤模型 / Trade Filter Model

> 归属章节：`09_ml_augmentation/` · 前置：`04_feature_store.md`、`05_walk_forward_validation.md` · 关联：`cta/strategy/brooks/core/gate/`

## 1. Skill 定义

训练一个**二分类模型**：输入规则触发时刻的特征快照，输出"该信号是否值得执行"的概率。模型作为 gate，规则不过 gate 就不下单。

## 2. 解决什么问题

- 痛点 1：规则策略信号多但胜率低 → ML 筛出高质量。
- 痛点 2：人工加条件无终点 → ML 一次学 50+ 特征的联合规律。
- 痛点 3：策略迁移到新品种缺数据 → filter 可借用同品种类。
- 增量价值：在不动规则主干的前提下把 Sharpe 从 0.8 提到 1.2+。

## 3. 适用市场 / 适用场景

- **品种类别**：全部（样本够即可）
- **周期**：与规则策略一致
- **行情状态前提**：与规则策略一致
- **不适用场景**：规则本身信号 < 100 笔（样本不够）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| label | trade net_pnl > 0 或 mfe/risk > 1 | — | 二者都试 | TODO |
| features | feature_snapshot @ signal bar | 50-100 | — | `cta/feature/price_action.py` |
| model | XGBoost / LightGBM | max_depth 4-6 | — | `cta/strategy/brooks/core/gate/` |
| threshold | 概率阈值 | 0.5 / 0.55 / 0.6 | 按 OOS Sharpe 选 | TODO |
| min_samples | 训练最小样本 | ≥ 500 | — | — |

## 5. 常见策略映射

### 模型 A：门控 XGBoost
- **标签**：`y = 1 if net_pnl > 0 else 0`（或 `1 if mfe > 2 × risk`）
- **特征**：规则触发 bar 上的价格行为 + 趋势 + vol + regime + 日内时段
- **训练**：walk-forward，每 3 个月重训
- **推理**：`p = model.predict_proba(feat); if p >= thr: execute`
- **调优**：`thr` 以 OOS Sharpe 最大化，但加 `min_trades_per_year` 硬约束

### 应用例子
- 接在 Donchian breakout 后（`03_trend_strategies/01_donchian_breakout.md`）
- 用 `pa_tight_range_*`、`pa_h2_confirm_*`、`trend_score_htf_*`、`atr_pct_*` 作输入

## 6. 代码模块设计

```text
cta/strategy/common/ml/filter/
├── labeler.py            # 从 trade_log 构造标签
├── featurizer.py         # 对齐 feature_snapshot
├── train.py              # walk-forward 训练
└── gate.py               # 推理接口
```

```python
from dataclasses import dataclass
import pandas as pd
import numpy as np

@dataclass
class FilterConfig:
    label_rule: str = 'rr'        # 'net_pnl' / 'rr'
    rr_threshold: float = 1.0
    model_type: str = 'xgboost'
    walk_window: str = '24M'
    walk_step: str = '3M'

def build_dataset(
    trade_log: pd.DataFrame,
    feature_panel: pd.DataFrame,
    cfg: FilterConfig,
) -> tuple[pd.DataFrame, pd.Series]:
    ...

def train_gate(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    cfg: FilterConfig,
):
    ...

def apply_gate(
    model,
    feat_at_signal: dict,
    threshold: float,
) -> bool:
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：OOS Sharpe 提升 / OOS 胜率提升 / 交易数减少比例 / 最大回撤变化 / AUC / PR curve / feature importance / 不同 threshold 下 IR
- **分层评估**：按品种 / 年份 / 触发类型
- **稳健性检验**：
  1. walk-forward 所有折都要提升，不是只有平均
  2. 留出样本（最后 1 年）不进任何训练
  3. 重训频率 3M / 6M / 12M 对比

## 8. 常见错误

- 标签用 close_price 包含 exit rule → 泄漏
- 特征包含未来信息（交易结束后的指标）→ 穿越
- 模型选在 IS 最佳而非 OOS 最佳 → 过拟合
- 过滤太狠 → 年交易 < 30 笔，统计显著性差

## 9. 迭代方向

- v1：单周期 XGBoost，label=net_pnl>0
- v2：多 label 多模型（先过"是否盈利"再过"是否大赢"）
- v3：加入相似历史样本检索（KNN ensemble）
- v4：在线学习 / concept drift 检测

## 10. 与其他 Skills 的关系

- **依赖**：`09_ml_augmentation/04_feature_store.md`、`05_walk_forward_validation.md`、`08_data_and_backtest_infra/05_trade_log_and_evaluation.md`
- **被依赖**：任何规则策略（作为 gate 可选接入）
- **互补**：`06_filtering_and_scoring/05_ml_opportunity_model.md`
