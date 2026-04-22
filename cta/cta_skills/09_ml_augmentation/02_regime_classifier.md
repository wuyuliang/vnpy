# 状态分类器 / Regime Classifier

> 归属章节：`09_ml_augmentation/` · 前置：`01_market_regime/ 全章`、`04_feature_store.md` · 关联：`05_regime_switch_strategies/`

## 1. Skill 定义

用 ML 把 bar 级特征映射到 **regime 类别**（trend_up / trend_down / range / compression / transition 等），替代或补充手工阈值。

## 2. 解决什么问题

- 痛点 1：手工阈值（ADX > 25 算趋势）脆弱。
- 痛点 2：regime 在边界处跳变。
- 痛点 3：不同品种用不同阈值维护成本大。
- 增量价值：模型输出的概率可直接用于策略加权。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：日 + MTF
- **行情状态前提**：任何（全域分类）
- **不适用场景**：数据 < 3 年（标签噪声大）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| label_scheme | 弱监督：基于未来 N 日 return/vol | N=10/20 | — | TODO |
| features | 趋势结构 + vol + 成交量 + 价差 | 30-60 | — | `cta/feature/price_action.py` |
| model | LightGBM 多分类 / HMM | — | — | TODO |
| smoothing | 输出概率的 EMA 平滑 | 3-5 bar | — | TODO |
| stability_score | 1 - 切换频率 | — | ≥ 0.7 | TODO |

## 5. 常见策略映射

### 规则 A：LightGBM 多分类
- **标签（弱监督）**：
  ```text
  future_return = close[t+20] / close[t] - 1
  future_vol = std(ret[t:t+20])
  if abs(future_return) > 1.5 × future_vol: trend (+ / -)
  elif future_vol < percentile(vol, 40): range
  else transition
  ```
- **特征**：ADX / trend_score / atr_pct / range_ratio / bb_width / 成交量 zscore / MTF 协同
- **输出**：`p_trend_up, p_trend_down, p_range, p_transition`
- **应用**：在策略层 `if p_trend_up > 0.6: 启用趋势策略`，作为门控。

### 规则 B：HMM
- 隐状态 = regime，观测 = return, |return|, vol, vol_change
- 优点：序列平滑自带

## 6. 代码模块设计

```text
cta/strategy/common/ml/regime/
├── labeler.py          # 弱监督标签
├── train.py
├── predict.py
└── postproc.py         # 平滑 / 概率融合
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class RegimeConfig:
    horizon: int = 20
    vol_pct_range: float = 40.0
    label_scheme: str = 'weak_future'
    ema_smooth: int = 5

def build_regime_labels(
    bars: pd.DataFrame,
    cfg: RegimeConfig,
) -> pd.Series:
    ...

def predict_regime(
    model,
    feature_row: pd.Series,
) -> dict[str, float]:
    """返回各 regime 的概率。"""
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：混淆矩阵 / 每 regime 内策略 Sharpe / 切换频率 / 稳定度 / 与手工分类一致率 / OOS acc
- **分层评估**：按品种 / 年份
- **稳健性检验**：
  1. 标签 horizon 变化（10/20/40）
  2. 不同模型（LightGBM / HMM / 两阶段）
  3. 与手工分类的 kappa

## 8. 常见错误

- 用未来 return 标签但 feature 也含未来 → 穿越
- 平滑过度 → 切换滞后
- 全局一个模型 vs 分品种 → 农产品与黑色不同
- 概率不校准 → 阈值难选

## 9. 迭代方向

- v1：LightGBM 多分类
- v2：HMM + LightGBM ensemble
- v3：按板块训练（黑色 / 有色 / 能化分开）
- v4：在线 regime drift 检测

## 10. 与其他 Skills 的关系

- **依赖**：`09_ml_augmentation/04_feature_store.md`
- **被依赖**：`05_regime_switch_strategies/03_regime_switch_signal.md`、`03_trend_strategies/*`、`04_range_strategies/*`
- **互补**：`01_market_regime/01_trend_detection.md`
