# cta/skills/market_regime/tests/

## 主要做什么

[cta/skills/market_regime/](..) regime 判别函数的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_trend.py](test_trend.py) | trend_up / trend_down / unknown 三类判定，正反样本 |
| [test_range.py](test_range.py) | range 判定：ATR 收窄 + ADX 低 + 振幅压缩 |
| [test_volatility.py](test_volatility.py) | low_vol / mid_vol / high_vol 三档分位识别 |

## 注意事项

- **label 必须在合法集合内**：trend_up / trend_down / range / unknown；任何其它字符串会让 [HtfGate](../../../portfolio_logic/interval_gate.py) emit `htf_unknown`。测试要 assert 输出 ∈ 合法集合。
- **shift 防穿越**：测试要构造"截至当前 bar 已收盘"的输入，禁止泄露未来 bar。
- **跑测试**：`pytest cta/skills/market_regime/tests/ -v`。
