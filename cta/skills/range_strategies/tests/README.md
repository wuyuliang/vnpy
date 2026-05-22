# cta/skills/range_strategies/tests/

## 主要做什么

[cta/skills/range_strategies/](..) 区间/震荡市策略组件的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_range_boundary_reversal.py](test_range_boundary_reversal.py) | 区间上下沿反转入场 |
| [test_mean_reversion.py](test_mean_reversion.py) | 均值回归入场 |
| [test_false_breakout_reversal.py](test_false_breakout_reversal.py) | 假突破反转 |
| [test_noise_filtering.py](test_noise_filtering.py) | 噪声过滤：识别低质量 candidate |

## 注意事项

- **关键边界：区间末期突破**：区间策略最大亏损来源是"以为还在震荡，结果走出区间"。每个测试都要覆盖这条边界——突破后是否能立即止损。
- **不在 trend regime 触发**：测试应当 assert 在明显趋势市场不识别为 setup。
- **跑测试**：`pytest cta/skills/range_strategies/tests/ -v`。
