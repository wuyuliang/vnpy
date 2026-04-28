# 紧缩区间突破 / Tight Range Breakout

> 归属章节：`02_price_action/` · 前置技能：`01_market_regime/03_breakout_threshold_detection.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

识别连续若干 bar 的高低点压缩成"小方块"（tight range），在方块被击穿时做突破。是 Al Brooks 最高频的 setup 之一，也是中国商品日内最稳定的 setup。

## 2. 解决什么问题

- 痛点 1：震荡中盲目做突破 → 假突破很多。
- 痛点 2：tight range 人眼识别，自动化弱。
- 痛点 3：突破触发价该放哪？止损放哪？仓位多少？缺少标准答案。
- 增量价值：用 `pa_tight_range_*` 特征量化 tight range，然后按本 skill 标准流程开单。

## 3. 适用市场 / 适用场景

- **品种类别**：全部（黑色 / 能化尤其有效）
- **周期**：minute60 / minute30 / minute15 / minute5
- **行情状态前提**：`01_market_regime/03_breakout_threshold_detection.md` 的 `threshold_score > 0.6`
- **不适用场景**：趋势已走出的中段、涨跌停附近

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| tight_range_count | 连续 N bar 的 (max-min)/ATR < α | N=5, α=1.5 | ≥ 5 根 | `cta/feature/price_action.py::pa_tight_range_*` |
| range_height_atr | (max-min)/ATR(14) | — | < 1.5 | `pa_rolling_high/low` + `pa_atr_*` |
| signal bar | tight range 方向打破的 K 线 | — | 收盘在 range 外 | `cta/feature/price_action.py::pa_is_signal_bar_*` |
| 成交量确认 | volume / MA(volume,20) | — | > 1.3 | 计算 |
| ATR 压缩比 | ATR(14)/ATR(50) | — | < 0.7 | `pa_atr_*` |

## 5. 常见策略映射

### 策略 A：tight range breakout（完整版）
- **信号定义**：
  ```text
  窗口回看 N=5..10 bar
  range_h = max(high[-N:]) - min(low[-N:])
  if range_h / atr14 < 1.5 and tight_range_count >= 5:
      upper_trigger = max(high[-N:]) + tick_size
      lower_trigger = min(low[-N:]) - tick_size
  signal_bar = bar that closes outside [lower_trigger, upper_trigger]
  ```
- **开仓触发**：在 signal_bar 的 **下一根 bar** 开盘挂 stop 单于 `signal_bar.high` / `signal_bar.low`（Brooks 标准入场）。
- **止损规则**：signal_bar 反向极值 − 1 tick。通常 = 1R ≈ 0.6-0.9 ATR。
- **平仓规则**：
  - 目标 1R 平一半，2R 启动 trailing
  - signal_bar reversal（出现 failed_breakout）立即退
- **仓位规模**：0.3% 权益 ÷ (止损距离 × multiplier)，见 `07_position_and_portfolio/01_single_trade_risk.md`。
- **失败模式**：事件日（OPEC / 数据）假突破高发、交易末尾流动性差。

### 策略 B：仅在 HTF 趋势方向上做突破
- 叠加 `01_market_regime/05_multi_timeframe_alignment.md` 的 HTF bias，提升胜率。

## 6. 代码模块设计

```text
cta/strategy/tight_range_breakout/
├── __init__.py
├── signals.py
├── filters.py
├── risk.py
├── run_backtest.py
└── config.yaml
```

```python
from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class TightRangeSetup:
    valid: bool
    upper: float
    lower: float
    range_atr: float
    count: int
    direction_bias: int        # -1/0/1

def detect_tight_range(
    df: pd.DataFrame,
    lookback: int = 10,
    alpha: float = 1.5,
    min_count: int = 5,
) -> pd.DataFrame:
    """
    返回每根 bar 的 TightRangeSetup 列。
    依赖 df 中已计算的 pa_tight_range_* / pa_atr_* 列。
    """
    ...

def resolve_breakout_trigger(
    setup: TightRangeSetup,
    bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """
    在 signal bar 上判断方向并返回 stop 单价位:
    {"side": "long"|"short", "trigger": float, "stop": float}
    """
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：胜率 / 盈亏比 / 平均持仓 bar / 真突破率 / 假突破率 / 平均 R 倍数 / Sharpe / 每日最大连续打止损次数
- **分层评估**：按品种 / 按 HTF bias 是否一致 / 按开盘/收盘时段 / 按 range_atr 桶（越小越紧）
- **稳健性检验**：
  1. N（lookback）= 5/7/10，Sharpe 变化 < 20%
  2. alpha = 1.2/1.5/2.0 的胜率单调性检查
  3. 叠加 HTF bias 后，胜率应提升 ≥ 10pp

## 8. 常见错误

- tight range 定义太宽（alpha=3）→ 信号泛滥
- 没滤 HTF → 趋势中做 tight range 假突破方向
- stop 放在 range 中点 → 太近，被随机噪声打
- 突破后追涨 → 不等 signal bar 回测结构

## 9. 迭代方向

- v1：规则版（本文）
- v2：加 context_score（`06_filtering_and_scoring/03_context_score.md`）
- v3：加 ML 过滤（`09_ml_augmentation/01_trade_filter_model.md`）
- v4：tight range 宽度自适应（按品种波动率分位）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/03_breakout_threshold_detection.md`、`01_market_regime/05_multi_timeframe_alignment.md`
- **被依赖**：`03_trend_strategies/01_donchian_breakout.md`（tight range + Donchian 是两种突破变体）、`05_regime_switch_strategies/02_breakout_mode_scoring.md`
- **互补**：`02_price_action/05_failed_breakout.md`（tight range 假突破就是它的反面）
