# cta/skills/range_strategies/

## 主要做什么

**区间/震荡市策略组件**：在 range regime 下使用的反转 / 均值回归 / 假突破识别 / 噪声过滤。和 [trend_strategies/](../trend_strategies/) 互补：趋势市靠后者，震荡市靠本目录。

## 关键文件

| 文件 | 主题 |
|---|---|
| [range_boundary_reversal.py](range_boundary_reversal.py) | 区间上下沿反转：触上沿做空 / 触下沿做多 |
| [mean_reversion.py](mean_reversion.py) | 均值回归：偏离中轴 N 倍标准差时反向 |
| [false_breakout_reversal.py](false_breakout_reversal.py) | 假突破反转：突破区间后立即被吞没 → 反向开仓 |
| [noise_filtering.py](noise_filtering.py) | 噪声过滤：识别"看起来像突破但只是噪声"的 candidate，下调 score |

## 详细过程

```
[regime gate]
    market_regime.range.is_in_range(bars) == True
    │
    ▼
[本目录策略选其一]
    ├─ range_boundary_reversal.detect(bars, boundary=...)
    ├─ mean_reversion.detect(bars, mid=..., std=...)
    └─ false_breakout_reversal.detect(bars, breakout=...)
    │
    ▼
[噪声过滤]
    noise_filtering.evaluate(candidate, bars) → 上调或下调 score
    │
    ▼
[生成 candidate → portfolio_logic]
```

## 注意事项

- **必须先过 regime gate**：本目录策略**只在 range regime 下** evaluate。trend regime 下用本目录策略大概率亏（被趋势打穿）。
- **止损/止盈口径**：区间策略的止损通常贴在区间外沿稍远处；如果突破直接走出区间，止损必须**马上**触发，避免被趋势打穿。
- **手续费敏感性高**：区间策略的单笔利润空间小，对手续费 / 滑点敏感，建议先在 60min 以上验证。
- **测试**：`pytest cta/skills/range_strategies/tests/ -v`，特别覆盖"区间末期突破"边界——这是这类策略最大亏损来源。
