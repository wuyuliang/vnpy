# cta/skills/trend_strategies/

## 主要做什么

**趋势市策略组件**：在 trend regime 下使用的突破 / 顺势 / 跨截面动量 / 趋势持有 + 移动止损。是 CTA 主要盈利来源——本目录是核心。

## 关键文件

| 文件 | 主题 |
|---|---|
| [donchian_breakout.py](donchian_breakout.py) | Donchian 通道突破（经典经典），可调 lookback / 退出规则 |
| [atr_breakout.py](atr_breakout.py) | ATR 突破：价格突破入场参考点 + N×ATR |
| [ma_trend_following.py](ma_trend_following.py) | 均线顺势：MA 系统判方向 + 回踩入场 |
| [cross_sectional_momentum.py](cross_sectional_momentum.py) | 跨截面动量：选 universe 内动量最强 / 最弱品种 |
| [trend_hold_trailing.py](trend_hold_trailing.py) | 趋势持有 + ATR 移动止损（持仓侧逻辑，配合上面 entry 用） |
| [_common.py](_common.py) | 共用 helper |

## 详细过程

```
[entry 信号] 选一种 entry strategy
    ├─ donchian_breakout.detect(bars, n=20)
    ├─ atr_breakout.detect(bars, atr_mult=2.0)
    └─ ma_trend_following.detect(bars, ma_fast=20, ma_slow=60)
    │
    ▼
[组合层 / 截面选股]
    cross_sectional_momentum.rank(universe_bars, lookback=20)
        → 选前 N 做多 / 后 N 做空
    │
    ▼
[持仓侧] trend_hold_trailing.update(position, bar)
    └─ ATR 移动止损，触发 stop 即退出
```

## 注意事项

- **必须先过 regime gate**：在 range regime 下用突破策略容易被假突破吃光收益。生产中由 [cta/portfolio_logic/interval_gate.py](../../portfolio_logic/interval_gate.py) 把关；研究时也应当先用 [cta/skills/market_regime/](../market_regime/) 过滤。
- **趋势持有是收益主体**：CTA 收益主要来自抓住少数大趋势 → 必须用 [trend_hold_trailing.py](trend_hold_trailing.py) 持有；过早止盈是最常见的策略错误。
- **trailing 方向不能搞反**：long 用 `max(trail, new_stop)`，short 用 `min(...)`，详见 [review/202605170735.md](../../docs/review/202605170735.md) §C1。生产 trailing 在 [cta/portfolio_logic/trailing_exit.py](../../portfolio_logic/trailing_exit.py)，本目录是研究/skill 版需要保持口径一致。
- **跨截面策略需要 universe**：[cross_sectional_momentum.py](cross_sectional_momentum.py) 输入应当是 N 个品种同时刻的 bar 字典，**不要**单独跑单品种。
- **测试**：`pytest cta/skills/trend_strategies/tests/ -v`，至少覆盖单边上涨 / 单边下跌 / 假突破不入场三类场景。
