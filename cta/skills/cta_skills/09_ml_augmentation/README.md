# 09 机器学习增强 / ML Augmentation

> 本章目标：在**规则策略已稳定**的前提下，用 ML 做"门控 / 分类 / 预测"，而不是替代规则。参考实现：`cta/strategy/brooks/core/gate/` XGBoost 门控。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_trade_filter_model.md](01_trade_filter_model.md) | 交易过滤模型（trade vs skip） |
| 02 | [02_regime_classifier.md](02_regime_classifier.md) | 状态分类器（trend / range / ...） |
| 03 | [03_mfe_mae_prediction.md](03_mfe_mae_prediction.md) | MFE / MAE 回归预测 |
| 04 | [04_feature_store.md](04_feature_store.md) | 特征存储与版本化 |
| 05 | [05_walk_forward_validation.md](05_walk_forward_validation.md) | 滚动样本外验证 |

## 推荐阅读顺序

`04 → 05 → 01 → 02 → 03`。先搭好特征 + 验证，再做模型。

## 已有资产

| 资产 | 作用 |
|------|------|
| `cta/feature/run_all_features.py` | 多频率批量特征生成 |
| `cta/feature/feature_loader.py` | 加载 API |
| `cta/strategy/brooks/core/gate/` | XGBoost 门控参考实现 |
| `cta/feature/price_action*.py` | 180+ 基础特征 |

## 指导思想

- ML 是**规则策略的过滤器**，不是策略本身
- 样本外 Sharpe 提升 ≥ 0.2 才保留
- 特征穿越检查是硬红线
