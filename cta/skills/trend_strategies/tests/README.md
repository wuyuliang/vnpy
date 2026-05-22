# cta/skills/trend_strategies/tests/

## 主要做什么

[cta/skills/trend_strategies/](..) 趋势市策略组件的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_donchian_breakout.py](test_donchian_breakout.py) | Donchian 通道突破入场 |
| [test_atr_breakout.py](test_atr_breakout.py) | ATR 突破入场 |
| [test_ma_trend_following.py](test_ma_trend_following.py) | 均线顺势入场 + 回踩 |
| [test_cross_sectional_momentum.py](test_cross_sectional_momentum.py) | 跨截面动量选股 |
| [test_trend_hold_trailing.py](test_trend_hold_trailing.py) | **趋势持有 + ATR 移动止损**：long / short 方向都要覆盖，防方向反 |

## 注意事项

- **`test_trend_hold_trailing.py` long/short 双向必盖**：[review/202605170735.md](../../../docs/review/202605170735.md) §C1 曾发现 short 方向 trailing 用错（min vs max）。本目录是研究版，**必须**与生产版 [cta/portfolio_logic/trailing_exit.py](../../../portfolio_logic/trailing_exit.py) 行为一致。
- **跨截面 universe**：[test_cross_sectional_momentum.py](test_cross_sectional_momentum.py) 输入要包含多品种同时刻 bar，禁止只跑单品种。
- **跑测试**：`pytest cta/skills/trend_strategies/tests/ -v`。
