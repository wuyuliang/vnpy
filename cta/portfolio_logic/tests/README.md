# cta/portfolio_logic/tests/

## 主要做什么

[cta/portfolio_logic/](..) 各子模块的单元测试 + OOT vs sim 对账。这层代码 OOT 与实盘共用，挂任何一个测试都意味着两边都不能上。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_config.py](test_config.py) | `PortfolioLogicConfig` 及子 config `__post_init__` 校验（如 `enable_pyramid` 必须 + `enable_trailing`） |
| [test_interval_gate.py](test_interval_gate.py) | `HtfGate.compute_htf_state` / `.filter` 各 state 分支 + `htf_missing/conflict/opposite/unknown` 触发条件 |
| [test_opportunity_ranker.py](test_opportunity_ranker.py) | ranker 评分、阈值、排序稳定性 |
| [test_trailing_exit.py](test_trailing_exit.py) | `simulate_trailing_exit` long/short 方向、ATR 调整、horizon 延展 |
| [test_pyramid_manager.py](test_pyramid_manager.py) | 加仓三条件（layer 数 / cooldown / min_profit_atr） |
| [test_risk_throttle.py](test_risk_throttle.py) | EquityTracker + RiskThrottle 的 normal/soft/hard/halt 状态机 |
| [test_score_calibrator.py](test_score_calibrator.py) | sigmoid / isotonic 校准 |
| [test_portfolio_state.py](test_portfolio_state.py) | open positions / margin / open_notional 滚动状态 |
| [test_oot_sim_parity.py](test_oot_sim_parity.py) | **OOT vs sim 决策一致性**：同一候选两边路径必须给同样的执行/拒单结果 |

## 注意事项

- **`test_oot_sim_parity.py` 是硬关卡**：挂了说明 OOT 与实盘共用代码出现了分支，**不允许合并**。
- **trailing 方向回归**：[review/202605170735.md](../../docs/review/202605170735.md) §C1 曾发现 long/short 方向反；`test_trailing_exit.py` 对两个方向都有正反测试，**禁止删除**。
- **HTF 边界 case**：新增 regime label / state 时必须扩 `test_interval_gate.py`，覆盖到 `htf_unknown` 兜底路径不应被正常触发。
- **frozen dataclass**：测试里修改 config 必须用 `dataclasses.replace(...)`。
- **跑测试**：`pytest cta/portfolio_logic/tests/ -v`。
