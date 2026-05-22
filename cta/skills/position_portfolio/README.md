# cta/skills/position_portfolio/

## 主要做什么

**仓位与组合规则的离线/skill 层实现**：单笔风控、波动率目标、板块/行业暴露、组合分配、回撤控制。和 [cta/portfolio_logic/](../../portfolio_logic/) 互补——`portfolio_logic` 是 OOT / 实盘共用的"运行时状态机"，本目录是"研究/离线工具 + 可解释的规则"。

## 关键文件

| 文件 | 作用 |
|---|---|
| [single_trade_risk.py](single_trade_risk.py) | 单笔风险：按 ATR / 止损宽度反推仓位，max_single_loss_pct 约束 |
| [vol_targeting.py](vol_targeting.py) | 波动率目标：组合年化波动率 → 仓位倍数 |
| [sector_exposure.py](sector_exposure.py) | 板块暴露：按 cluster 集中度 cap，避免同 sector 多品种同向重仓 |
| [portfolio_allocation.py](portfolio_allocation.py) | 资金分配：等权 / vol-parity / 凯利系数变体 |
| [drawdown_control.py](drawdown_control.py) | 回撤控制：周/月回撤触达后的缩仓比例 |

## 详细过程

```
[研究层 / OOT 决策点]
    │
    ├─ single_trade_risk.size(equity, atr, stop_pct)
    │       → 单笔 notional
    │
    ├─ vol_targeting.scale(portfolio_vol_target, realized_vol)
    │       → 总仓位倍数
    │
    ├─ sector_exposure.check(open_positions, cluster_map)
    │       → 单 cluster 是否触顶
    │
    ├─ drawdown_control.scale_after_breach(dd_pct)
    │       → 触达回撤后的缩仓系数
    │
    └─ portfolio_allocation.allocate(candidates, budget)
            → 单 bar 内多候选的资金分配
```

## 注意事项

- **不直接发单 / 写 OOT 状态**：本目录函数都应当**输入参数 → 输出标量/dict**，不可变状态。状态由调用方（[cta/portfolio_logic/portfolio_state.py](../../portfolio_logic/portfolio_state.py)）维护。
- **公式必须可解释**：每个函数顶部 docstring 必须给出公式与默认参数来源；魔数 `0.5`/`0.3` 必须解释来自哪个 review / config。
- **与 [cta/portfolio_logic/](../../portfolio_logic/) 的边界**：
  - 本目录：纯函数 + 可在 notebook 单独跑；研究态用
  - portfolio_logic：状态机 + OOT/sim/live 共享代码；生产用
  - **不允许互相 import**（portfolio_logic 不依赖 skill；skill 也不依赖 portfolio_logic 的状态机）
- **测试**：`pytest cta/skills/position_portfolio/tests/ -v`；每个函数至少覆盖正常 + 边界（零波动率 / 满仓 / 单笔过大）。
