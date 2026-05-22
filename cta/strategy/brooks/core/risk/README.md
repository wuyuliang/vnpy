# cta/strategy/brooks/core/risk/

## 主要做什么

Brooks v3 的**风控三件套**：单笔 sizing、止损更新、组合层面 cap / 回撤降仓。

## 关键文件

| 文件 | 作用 |
|---|---|
| [sizing.py](sizing.py) | 按 ATR / 止损宽度反推单笔 notional：`max_single_loss = equity × per_trade_pct (默认 0.1%)` |
| [stops.py](stops.py) | 初始止损 = ATR × N，move-to-breakeven、trailing stop |
| [portfolio.py](portfolio.py) | 组合 cap：单品种最大同时持仓、总持仓上限、周/月回撤触发后降仓 |

## 详细过程

```
[新入场]
    sizing.compute_notional(
        equity, atr_at_entry, stop_pct, per_trade_pct=0.001
    )
        └─ 反推单笔仓位 → max_loss = equity * 0.1%
    │
    ▼
[每根 bar]
    stops.update(position, bar)
        ├─ 初始 stop（ATR × N）
        ├─ 突破入场价 1× ATR 后 move to breakeven
        └─ 继续盈利后 trailing
    │
    ▼
[组合层]
    portfolio.check_caps(portfolio_state)
        ├─ 单品种持仓数 ≥ max_per_symbol → 拒绝新仓
        ├─ 总持仓 ≥ max_total → 拒绝新仓
        └─ 周/月 dd 超阈值 → 缩仓系数
```

## 注意事项

- **trailing 方向不能反**：long 用 `max(trail, new_stop)`，short 用 `min(...)`。历史上有过 bug（见 [cta/docs/review/202605170735.md](../../../../docs/review/202605170735.md) §C1）；和生产 [cta/portfolio_logic/trailing_exit.py](../../../../portfolio_logic/trailing_exit.py) 口径必须一致。
- **per_trade_pct 默认 0.1%**：跟 v3 任务书一致，**禁止**改默认值（在 [../../config/strategy.yaml](../../config/strategy.yaml) 可覆盖）。
- **回撤触发不停手**：周/月 dd 触达 → 按系数缩仓，**不**完全停手，避免反弹错过。
- **测试**：long / short 各一笔的回归用例**必盖**。
