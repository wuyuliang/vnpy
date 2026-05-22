# cta/skills/position_portfolio/tests/

## 主要做什么

[cta/skills/position_portfolio/](..) 仓位与组合规则函数的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_single_trade_risk.py](test_single_trade_risk.py) | 单笔风险 sizing：按 ATR / 止损反推 notional |
| [test_vol_targeting.py](test_vol_targeting.py) | 波动率目标缩放 |
| [test_sector_exposure.py](test_sector_exposure.py) | 板块暴露 cap：单 cluster 集中度 |
| [test_portfolio_allocation.py](test_portfolio_allocation.py) | 资金分配：等权 / vol-parity / 凯利变体 |
| [test_drawdown_control.py](test_drawdown_control.py) | 回撤触发后的缩仓系数 |

## 注意事项

- **公式与 docstring 一致**：测试要 assert 在已知输入下输出严格等于 docstring 给出的公式结果，避免后续重构改了公式没改 docstring。
- **边界 case 必盖**：单笔仓位 ≥ 全仓、波动率 = 0、回撤 = 0、回撤超阈值。
- **跑测试**：`pytest cta/skills/position_portfolio/tests/ -v`。
