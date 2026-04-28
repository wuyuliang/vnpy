# 波动率目标 / Volatility Targeting

> 归属章节：`07_position_and_portfolio/` · 前置：`01_market_regime/04_volatility_regime.md`、`07_position_and_portfolio/01_single_trade_risk.md` · 关联：`cta/strategy/brooks/core/risk/sizing.py`

## 1. Skill 定义

让每个品种 / 每笔仓位的**名义波动率保持恒定**：高波动品种少开、低波动品种多开；以 ATR 或 realized vol 归一化。单笔风险的"广义版本"。

## 2. 解决什么问题

- 痛点 1：单笔 risk_pct 已固定，但品种间 ATR 差 5 倍 → 仓位名义波动不等。
- 痛点 2：组合层面希望每个 slot 贡献相近 PnL 方差。
- 痛点 3：高波期 equity 波动放大，不降仓即爆仓。
- 增量价值：PnL 更平滑，Calmar 大幅提升。

## 3. 适用市场 / 适用场景

- **品种类别**：全部，尤其跨品种组合
- **周期**：所有
- **行情状态前提**：任何
- **不适用场景**：新合约（ATR 不稳定）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| ATR(N) | Wilder | N=14/20 | — | `pa_atr_*` |
| realized_vol | std(log_ret) × √252 | 60-day | — | TODO |
| target_vol | 目标年化波动率 | 8%-15% 单品种 | — | — |
| contract_notional_vol | ATR × multiplier × lot | — | — | 计算 |
| rebalance_threshold | 偏离目标多少时才调整 | 20% | — | — |

## 5. 常见策略映射

### 策略 A：ATR 归一化仓位
- **公式**：
  ```text
  target_daily_pnl_std = equity * target_vol / sqrt(252)
  per_lot_daily_std = ATR(14) * contract_multiplier
  target_lots = target_daily_pnl_std / per_lot_daily_std
  final_lots = min(target_lots, single_trade_risk_lots, max_lots)
  ```
- **应用**：
  - 开仓时取 `min(单笔 risk_pct 给出的 lots, vol_target 给出的 lots)` 作为最终手数。
  - 持仓期间 ATR 显著变化 → rebalance（> 20%）。
- **失败模式**：ATR 跳变（换月）→ 突然减仓造成成本。

## 6. 代码模块设计

```text
cta/strategy/common/risk/
├── vol_target.py
└── rebalance.py
```

```python
from dataclasses import dataclass

@dataclass
class VolTargetResult:
    target_lots: int
    target_daily_std: float
    per_lot_daily_std: float

def vol_target_size(
    equity: float,
    atr: float,
    contract_multiplier: float,
    target_vol: float = 0.1,
    trading_days: int = 252,
) -> VolTargetResult:
    ...

def combine_sizing(
    risk_pct_lots: int,
    vol_target_lots: int,
    max_lots: int,
) -> int:
    """双目标中的保守者（min）。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：各品种名义 vol 分布 / 目标 vol 偏差 / rebalance 次数 / 成本 / Sharpe 改善 / 回撤改善 / 与单 risk_pct 对比 PnL
- **分层评估**：按品种 / 按时间段 / 按 target_vol
- **稳健性检验**：
  1. target_vol = 8% / 10% / 12%
  2. rebalance_threshold = 10% / 20% / 30%
  3. ATR 窗口 14 / 20 / 30

## 8. 常见错误

- 不加 rebalance_threshold → 每 bar 调仓，成本飞涨
- 用当 bar close 计算 ATR 做本 bar 决策 → 未来函数
- 未约束 max_lots → 低波时仓位极大
- 组合级未做，单品种各自 vol target → 总权益超风险预算

## 9. 迭代方向

- v1：ATR 归一
- v2：realized vol 混合 ATR
- v3：组合级 vol target（协方差约束）
- v4：动态 target_vol（回撤后调低）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/04_volatility_regime.md`、`07_position_and_portfolio/01_single_trade_risk.md`
- **被依赖**：`07_position_and_portfolio/05_portfolio_allocation.md`
- **互补**：`07_position_and_portfolio/04_drawdown_control.md`（回撤后自动降 target_vol）
