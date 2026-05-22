# cta/skills/overview/tests/

## 主要做什么

[cta/skills/overview/](..) 项目方法论代码化的测试 + 真实数据冒烟。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_acceptance.py](test_acceptance.py) | 实验验收阈值：win_rate / sharpe / max_drawdown 的 pass / fail 判定 |
| [test_backtest_principles.py](test_backtest_principles.py) | 回测原则 assertion（手续费 / 滑点 / 跳空配置必填等） |
| [test_live_principles.py](test_live_principles.py) | 实盘前置条件：sim 天数、parity diff、kill_switch 配置完整 |
| [test_iteration.py](test_iteration.py) | 策略迭代节奏建议 |
| [test_research_pool.py](test_research_pool.py) | 研究池子选品种规则 |
| [test_smoke_rb0.py](test_smoke_rb0.py) | RB0 端到端冒烟：从研究 → 验收的最小完整链 |
| [test_real_feature_data_intervals.py](test_real_feature_data_intervals.py) | 真实特征数据在多 interval 下可用性 |
| [test_real_feature_data_market_price_action.py](test_real_feature_data_market_price_action.py) | 真实数据 × market_regime / price_action skill 可用性 |
| [test_real_feature_data_strategies_scoring.py](test_real_feature_data_strategies_scoring.py) | 真实数据 × strategies / filtering_scoring skill 端到端 |

## 注意事项

- **`test_real_feature_data_*.py` 依赖真实数据**：CI 前要先 `python -m cta.data_code.download_all` 或保证 fixture 数据存在；本地若磁盘小可 skip 这几个。
- **阈值改动 lock**：[acceptance.yaml](../../configs/acceptance.yaml) 与 [research_pool.yaml](../../configs/research_pool.yaml) 改字段必须同步改 test 的 expected。
- **跑测试**：
  - 全量：`pytest cta/skills/overview/tests/ -v`
  - 不依赖真数据：`pytest cta/skills/overview/tests/ -k "not real_feature" -v`
