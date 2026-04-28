# MFE / MAE 回归预测 / MFE-MAE Regression

> 归属章节：`09_ml_augmentation/` · 前置：`01_trade_filter_model.md` · 关联：`07_position_and_portfolio/02_vol_targeting.md`

## 1. Skill 定义

对每笔潜在信号预测**最大浮盈 MFE / 最大浮亏 MAE**，用于动态仓位、动态止损、风险预算。

## 2. 解决什么问题

- 痛点 1：所有信号等同对待 → 错失高 MFE 的机会、给低 MFE 仓位太重。
- 痛点 2：止损固定 ATR，但不同信号 MAE 差异巨大。
- 痛点 3：入场后不知道该不该加仓。
- 增量价值：把"仓位该多大 / 止损该多远"从规则改成估计量。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：任何
- **不适用场景**：trade_log < 300 笔

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| mfe_target | `max(high[t:t+N]) - entry` / ATR | N=20 / 60 | — | TODO |
| mae_target | `entry - min(low[t:t+N])` / ATR | N=20 / 60 | — | TODO |
| model | LightGBM 回归（分位数） | quantile 0.2/0.5/0.8 | — | TODO |
| calib_check | 预测分位 vs 实际分位 | — | KS < 0.1 | TODO |
| sample_age | 训练样本覆盖时长 | 3-5 年 | — | — |

## 5. 常见策略映射

### 策略 A：分位数回归
- **模型**：3 个 LightGBM，分别拟合 mfe 的 20/50/80 分位。
- **应用**：
  - 止损 = `max(1 × ATR, predicted_mae_80)`
  - 止盈第一目标 = `predicted_mfe_50`
  - 仓位 = `base × clip(mfe_50 / mae_80, 0.5, 2.0)`
- **失败模式**：极端行情 outlier → 预测偏差大，需要 quantile cap。

### 策略 B：二元分类辅助
- 另训一个"本笔是否达到 2R"的二分类，与 filter 模型融合。

## 6. 代码模块设计

```text
cta/strategy/common/ml/mfe_mae/
├── labeler.py            # 构造 MFE/MAE 标签
├── train.py              # 分位数回归
├── predict.py
└── use_in_sizing.py
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class MFEMAEConfig:
    horizon: int = 40
    atr_col: str = 'atr_14'
    quantiles: tuple[float, ...] = (0.2, 0.5, 0.8)

def build_mfe_mae_labels(
    bars: pd.DataFrame,
    signal_index: pd.Index,
    cfg: MFEMAEConfig,
) -> pd.DataFrame:
    ...

def train_quantile_models(
    X: pd.DataFrame,
    y_mfe: pd.Series,
    y_mae: pd.Series,
    cfg: MFEMAEConfig,
) -> dict:
    ...

def predict_mfe_mae(
    models: dict,
    feat: pd.Series,
) -> dict[str, float]:
    """返回 mfe_q20/50/80 与 mae_q20/50/80（单位 ATR）。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：MFE/MAE 实际 vs 预测分位校准 / Sharpe 提升 / 平均止损距离变化 / 大赢单捕获率 / 总 PnL / 仓位动态相关性 / CAGR
- **分层评估**：按品种 / 信号类型
- **稳健性检验**：
  1. horizon 20 / 40 / 60 的差异
  2. 分位 0.2/0.5/0.8 vs 0.1/0.5/0.9
  3. 单独用 mfe 模型 vs mfe+mae 组合

## 8. 常见错误

- 用未来数据算 ATR → 穿越
- 没做 quantile cap → 极端预测值破坏仓位
- 预测单位混乱（ATR vs 元）→ 仓位错乱
- 覆盖所有信号共用一个模型 → 不同信号分布差异大

## 9. 迭代方向

- v1：LightGBM 分位回归
- v2：预测分布 → 随机仓位（采样）
- v3：MFE path 模型（预测达到 2R 前最大 MAE）
- v4：survival analysis / 时间到达模型

## 10. 与其他 Skills 的关系

- **依赖**：`09_ml_augmentation/04_feature_store.md`、`08_data_and_backtest_infra/05_trade_log_and_evaluation.md`
- **被依赖**：`07_position_and_portfolio/01_single_trade_risk.md`、`07_position_and_portfolio/02_vol_targeting.md`
- **互补**：`09_ml_augmentation/01_trade_filter_model.md`
