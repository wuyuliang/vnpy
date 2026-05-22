# cta/skills/regime_switch/

## 主要做什么

**regime 切换识别 + 切换期策略**：抓住"震荡 → 趋势"或"趋势 → 反转"这种状态机切换的临界点。这是最难的一段（误识别成本高），但收益密度也最高。

## 关键文件

| 文件 | 主题 |
|---|---|
| [switch_machine.py](switch_machine.py) | 状态机：当前 state + 触发条件 → 下一 state；维护转移日志 |
| [breakout_score.py](breakout_score.py) | 即将切到 trend 的"突破得分"（基于波动率压缩 + 关键位接近度） |
| [volatility_transition.py](volatility_transition.py) | 波动率切换识别：低波 → 高波（趋势启动征兆）/ 高波 → 低波（趋势衰竭征兆） |
| [transition_risk.py](transition_risk.py) | 切换期风控：切换期间假信号率高，应当缩小仓位或要求更高 score |

## 详细过程

```
[bar 序列]
    │
    ▼
volatility_transition.classify(bars)
    └─ "low→high" / "high→low" / "stable"
    │
    ▼
breakout_score.evaluate(bars, hl_levels)
    └─ score ∈ [0, 1]：越接近 1 越像 imminent breakout
    │
    ▼
switch_machine.step(current_state, vol_transition, breakout_score)
    └─ next_state ∈ {range, trend_up, trend_down, transition}
    │
    ▼
[策略侧]
    if next_state == "transition":
        candidate.position_scale *= transition_risk.discount(score)
```

## 注意事项

- **切换期误识别成本高**：宁可漏 transition 也不要把 stable range 误判为 transition。`switch_machine.step` 默认要求"连续 N 根 bar 都给出 transition 信号"才切。
- **状态转移图必须有 docstring**：`switch_machine.py` 顶部画 state diagram，注明合法转移与不合法转移。
- **不在 transition 下加仓**：`transition_risk.discount` 必须严格 < 1。加仓由 [cta/portfolio_logic/pyramid_manager.py](../../portfolio_logic/pyramid_manager.py) 决定，但在 transition state 下应当被打折/拒绝。
- **与 [cta/skills/market_regime/](../market_regime/) 的关系**：
  - `market_regime`：判定**当前**是 trend/range/vol
  - 本目录：判定**正在切换**到下一个 regime（一阶导信号）
- **测试**：`pytest cta/skills/regime_switch/tests/ -v`，重点是回放历史 transition 是否被正确识别 + stable 期不误触发。
