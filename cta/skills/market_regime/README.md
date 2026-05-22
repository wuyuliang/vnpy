# cta/skills/market_regime/

## 主要做什么

**市场状态判别**：识别当前 bar 所处的 regime（trend / range / volatility regime），供策略路由和 portfolio_logic 的 HTF gate 用。

## 关键文件

| 文件 | 维度 |
|---|---|
| [trend.py](trend.py) | 趋势识别：MA 方向 / HH-HL 结构 / 收盘位置等组合判 trend_up / trend_down |
| [range.py](range.py) | 区间识别：ATR 收窄、ADX 低、振幅压缩 → range |
| [volatility.py](volatility.py) | 波动率 regime：vol_quantile / GARCH-like 短期波动率分级 |

## 详细过程

```
bar 序列
    │
    ├─ trend.classify(bars, window=20)       → "trend_up" / "trend_down" / "unknown"
    ├─ range.is_in_range(bars, atr_window=14) → bool + reason
    └─ volatility.regime(bars, lookback=60)   → "low_vol" / "mid_vol" / "high_vol"
    │
    ▼
组合到 pred_regime_label / pred_volatility_label，供：
    - cta/strategy/* 路由
    - cta/portfolio_logic/interval_gate.py 做 HTF 共识
    - cta/model/training/regime_classifier_model.py 学到的 label 与本目录的硬规则交叉验证
```

## 注意事项

- **regime label 必须在合法集合内**：trend_up / trend_down / range（及别名 bull/bear/up/down/sideways/neutral，见 [cta/portfolio_logic/interval_gate.py:13-15](../../portfolio_logic/interval_gate.py)）。**禁止**输出 `_state_from_regimes` 不识别的字符串，否则触发 `htf_unknown` block（详见 [cta/docs/block_reason.md](../../docs/block_reason.md) §8.3）。
- **shift 防穿越**：所有 regime 计算必须基于"截至当前 bar 已收盘"的信息；分类函数默认 `lookback` 不含当前 bar。
- **与 ML regime_classifier 的关系**：本目录是**规则版** regime；ML 版在 [cta/model/training/regime_classifier_model.py](../../model/training/regime_classifier_model.py)。两者输出 label 集合必须一致。
- **测试**：`pytest cta/skills/market_regime/tests/ -v`，覆盖单边趋势、震荡、混合三类历史样本。
