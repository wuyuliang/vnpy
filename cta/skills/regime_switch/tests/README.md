# cta/skills/regime_switch/tests/

## 主要做什么

[cta/skills/regime_switch/](..) regime 切换识别 + 切换期策略的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_switch_machine.py](test_switch_machine.py) | 状态机：合法转移 / 不合法转移 / 连续 N 根 bar 确认 |
| [test_breakout_score.py](test_breakout_score.py) | 突破临近度评分 |
| [test_volatility_transition.py](test_volatility_transition.py) | 波动率切换识别（低→高 / 高→低） |
| [test_transition_risk.py](test_transition_risk.py) | 切换期仓位折扣系数 |

## 注意事项

- **误识别成本高**：切换期识别错会让策略在"还没真正切换"时就重仓。测试要重点覆盖 stable range / stable trend 期不被误判为 transition。
- **状态转移图必须 freeze**：[test_switch_machine.py](test_switch_machine.py) 应当用参数化覆盖**所有**合法 (state, event) → next_state 组合，新增 state 时必须扩 test 才允许合并。
- **跑测试**：`pytest cta/skills/regime_switch/tests/ -v`。
