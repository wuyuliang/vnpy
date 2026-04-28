# 滚动样本外验证 / Walk-Forward Validation

> 归属章节：`09_ml_augmentation/` · 前置：`04_feature_store.md` · 关联：`cta/strategy/brooks/`

## 1. Skill 定义

用**滚动窗口**的方式把数据切成 train / test 多折，每折只用该折"之前"的数据训练，在"之后"的数据上评估，汇总所有折得到样本外表现。是 ML / 参数优化的唯一可信度量。

## 2. 解决什么问题

- 痛点 1：IS 最佳参数往往 OOS 表现差。
- 痛点 2：随机划分在时序数据上严重高估性能。
- 痛点 3：不做滚动重训 → 模型过期。
- 增量价值：得到**更接近实盘**的性能估计。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：任何
- **不适用场景**：样本 < 1 年（折太少，方差高）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| train_window | 训练窗口长度 | 24M / 36M | — | TODO |
| test_window | 测试窗口长度 | 3M / 6M | — | TODO |
| step | 滑动步长 | = test_window | — | TODO |
| min_train_samples | 每折最少样本 | ≥ 500 | — | TODO |
| fold_count | 折数 | ≥ 6 | — | 计算 |

## 5. 常见策略映射

### 方案 A：expanding window
- 训练集从起点到当前，测试集为之后 3 个月。
- 适合：数据量逐步增多、希望用尽所有历史。

### 方案 B：rolling window
- 训练集固定 24 月，往后滑。
- 适合：模型假设分布变化。

### 方案 C：purged K-fold（时序）
- 训练 / 测试间设 "purge gap"（如 5 bar），防止标签跨区间泄漏。
- 适合：标签本身含未来 N bar 的信息。

## 6. 代码模块设计

```text
cta/strategy/common/ml/validation/
├── split.py              # 生成 folds
├── fit_predict.py        # 逐折训练预测
└── aggregate.py          # 汇总 OOS 指标
```

```python
from dataclasses import dataclass
from typing import Iterator
import pandas as pd

@dataclass
class WFConfig:
    mode: str = 'expanding'       # 'expanding' / 'rolling'
    train_months: int = 24
    test_months: int = 3
    purge_bars: int = 5

def walk_forward_splits(
    timestamps: pd.DatetimeIndex,
    cfg: WFConfig,
) -> Iterator[tuple[pd.Index, pd.Index]]:
    ...

def run_walk_forward(
    X: pd.DataFrame,
    y: pd.Series,
    model_factory,
    cfg: WFConfig,
) -> pd.DataFrame:
    """返回按折聚合的 OOS 预测 + 指标表。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：每折 OOS Sharpe / 折间方差 / 总 OOS PnL / 稳定折比例（Sharpe > 0）/ IS-OOS 衰减 / AUC / 最差折表现
- **分层评估**：按品种 / 折
- **稳健性检验**：
  1. expanding vs rolling 一致性
  2. train_window ±50% 敏感性
  3. purge_bars 0 vs 5 vs 10

## 8. 常见错误

- 整个数据集先做标准化 → 测试集的均值泄漏
- hyperparam tuning 跨所有数据 → 过拟合
- folds 间标签重叠（未 purge）→ 泄漏
- 只看平均 Sharpe，不看最差折

## 9. 迭代方向

- v1：expanding + 固定 test
- v2：purged K-fold
- v3：combinatorial purged CV（Marcos López 方法）
- v4：embargo + cross-validation with block bootstrap

## 10. 与其他 Skills 的关系

- **依赖**：`09_ml_augmentation/04_feature_store.md`
- **被依赖**：`09_ml_augmentation/01/02/03`、`06_filtering_and_scoring/05_ml_opportunity_model.md`
- **互补**：`00_overview_methodology/03_backtest_principles.md`
