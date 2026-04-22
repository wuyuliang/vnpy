# 唐奇安突破 / Donchian Breakout

> 归属章节：`03_trend_strategies/` · 前置：`01_market_regime/04_volatility_regime.md`、`02_price_action/01_tight_range_breakout.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

价格突破过去 N 根 bar 的最高 / 最低点则开仓。Turtle Trading 原教旨版本 & 趋势跟随老祖宗。结构最简、鲁棒性好，是 CTA 策略库必备一员。

## 2. 解决什么问题

- 痛点 1：趋势定义争议太多 → Donchian 给出不带参数偏见的定义。
- 痛点 2：入场点无标准 → 突破 N 期新高就是最明确的标准。
- 痛点 3：老策略但参数过时 → 需要适配当代中国商品。
- 增量价值：提供**可作为 benchmark** 的最朴素趋势策略，所有更复杂策略都要打败它。

## 3. 适用市场 / 适用场景

- **品种类别**：黑色、有色、能化（趋势品种）
- **周期**：day / minute60 / minute30
- **行情状态前提**：`trend_score` 与本 skill 无关（本 skill 自带定义），但波动率合理（`compression` 前或 `high` 中）
- **不适用场景**：农产品小品种、震荡主导时段

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| Donchian 上轨 | `max(high, N)` | N=20/55 | — | `cta/feature/price_action.py::pa_rolling_high_*` |
| Donchian 下轨 | `min(low, N)` | N=20/55 | — | `cta/feature/price_action.py::pa_rolling_low_*` |
| ATR(N) | — | N=14/20 | — | `cta/feature/price_action.py::pa_atr_*` |
| 突破间隔 | 距离上次突破的 bar 数 | — | > 20 为 "首次" | 计算 |
| 趋势过滤 | `trend_score` | — | > 0.3 视为允许做多 | `01_market_regime/01_trend_detection.md` |

## 5. 常见策略映射

### 策略 A：Turtle Trading（改中国版）
- **信号定义**：
  ```text
  long_entry:  high[t] > donchian_upper_55
  short_entry: low[t]  < donchian_lower_55
  
  long_exit:   low[t]  < donchian_lower_20
  short_exit:  high[t] > donchian_upper_20
  ```
- **开仓触发**：`donchian_upper_55 + tick` 的 stop buy；对称 stop sell。
- **止损规则**：`entry - 2*ATR(20)` for long；对称。
- **平仓规则**：反向 Donchian 20 日。
- **仓位规模**：
  ```text
  unit = 0.3% * equity / (ATR(20) * multiplier)
  pyramid: 每 +0.5 ATR 加一单位，最多 4 单位
  ```
- **失败模式**：震荡市反复被抽打。应配合 `01_market_regime/02_range_detection.md` 在 range_score > 0.6 时关闭。

### 策略 B：Donchian + tight range 过滤
- 只在前序 ≥ 5 根 bar `pa_tight_range_*` 标记为 True 时触发突破。
- 胜率从 ~30% 升到 ~40%，但信号次数下降 60%。

### 策略 C：Donchian + HTF 同向过滤
- 仅在 HTF 的 `trend_score` 同方向时开仓。

## 6. 代码模块设计

```text
cta/strategy/donchian_breakout/
├── __init__.py
├── signals.py          # Donchian 上下轨
├── filters.py          # HTF / regime / tight range 过滤
├── risk.py             # ATR 止损、pyramid
├── run_backtest.py     # 入口
└── config.yaml
```

```python
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class DonchianSignal:
    side: Literal['long','short','flat']
    entry_price: float
    stop_price: float
    exit_price: float
    unit_count: int           # 当前已建单位数（1..4）

def compute_donchian(
    df: pd.DataFrame,
    n_entry: int = 55,
    n_exit: int = 20,
) -> pd.DataFrame:
    """添加 don_upper_entry / don_lower_entry / don_upper_exit / don_lower_exit 列。"""
    ...

def decide_donchian_trade(
    df_with_donchian: pd.DataFrame,
    bar_idx: int,
    current_position: DonchianSignal | None,
    filters: dict,
    tick_size: float,
) -> Optional[DonchianSignal]:
    """每根 bar 产出 0..1 个新单或调整单。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：总收益 / 年化 / 最大回撤 / Sharpe / Calmar / 胜率（通常 25-35%） / 盈亏比（2.0+） / pyramid 贡献
- **分层评估**：按品种 / 按年份 / 按是否有过滤 / 按 n_entry / n_exit 组合
- **稳健性检验**：
  1. n_entry / n_exit ±20%，Sharpe 不变超过 30%
  2. 去掉 pyramid 后 PnL 下降可控
  3. 成本 ×2 仍盈利

## 8. 常见错误

- n_entry = n_exit → 高换手 / 噪声大
- ATR 止损太紧（1 × ATR）→ 经常被震出
- 没有 regime 过滤 → 震荡市大回撤
- pyramid 不限制 → 加到 10 单位，单笔爆仓
- 回测用 `close` 成交 → 违反 `00_overview_methodology/03_backtest_principles.md`

## 9. 迭代方向

- v1：Turtle 原版
- v2：+ regime 过滤 + HTF 过滤
- v3：+ ATR 动态参数（vol 高时 N 更大）
- v4：+ ML 过滤（每个信号打 score）
- v5：合并 `02_price_action/01_tight_range_breakout.md` 成统一突破框架

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/04_volatility_regime.md`（vol targeting）、`01_market_regime/01_trend_detection.md`（过滤）
- **被依赖**：`06_filtering_and_scoring/02_breakout_quality_score.md`、`09_ml_augmentation/01_trade_filter_model.md`（本 skill 的过滤模型）
- **互补**：`03_trend_strategies/02_atr_breakout.md`（同类突破的 ATR 版）
