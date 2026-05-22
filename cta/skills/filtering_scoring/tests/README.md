# cta/skills/filtering_scoring/tests/

## 主要做什么

[cta/skills/filtering_scoring/](..) 各打分维度的单元测试。每个评分函数至少覆盖：典型 positive、典型 negative、边界值、缺失值、空数据。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_setup_quality.py](test_setup_quality.py) | setup 形态质量评分 |
| [test_breakout_quality.py](test_breakout_quality.py) | 突破质量评分 |
| [test_context_score.py](test_context_score.py) | 上下文评分（大周期方向、波动率环境） |
| [test_risk_reward_score.py](test_risk_reward_score.py) | 风险收益比评分 |
| [test_ml_opportunity_model.py](test_ml_opportunity_model.py) | ML opportunity model 包装层 inference 接口 |

## 注意事项

- **score 输出 ∈ [0, 1]**：所有评分函数测试中要 assert range。
- **不调真模型**：`test_ml_opportunity_model.py` 必须 mock 模型权重，避免依赖磁盘 joblib。
- **跑测试**：`pytest cta/skills/filtering_scoring/tests/ -v`。
