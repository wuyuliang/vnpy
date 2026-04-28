# 突破临界识别 / Breakout Threshold Detection

> 归属章节：`01_market_regime/` · 前置技能：`02_range_detection.md`、`04_volatility_regime.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

识别**即将发生突破**的行情状态：压缩够久、成交量萎缩、ATR 收敛、形态收敛（tight range / flag / triangle）等，并给出临界分数和预期突破方向（若有）。是突破策略的"火药桶检测"。

## 2. 解决什么问题

- 痛点 1：突破策略只看"价格超过 N 期新高" → 进场时机已过半，PnL 差。
- 痛点 2：无法区分"随机突破"与"蓄势突破"，后者胜率 / 盈亏比显著更好。
- 痛点 3：盲目追涨 → 假突破频发。
- 增量价值：在突破发生**之前**给出分数 + 预期方向 + 触发位，策略可以"提前埋单"或"只在临界分数高时进场"。

## 3. 适用市场 / 适用场景

- **品种类别**：黑色 / 能化 / 有色（周期性强、事件敏感）
- **周期**：minute60 / minute30 / minute15 更常用（日线临界也有效但次数少）
- **行情状态前提**：前提是先处于震荡 / 压缩态
- **不适用场景**：已经开始趋势的中段

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| ATR 压缩比 | `ATR(14) / ATR(50)` | — | < 0.6 视为压缩 | `cta/feature/price_action.py::pa_atr_*` |
| 布林带宽度分位 | BB 宽度在过去 252 bar 的分位 | — | < 20% 分位 | TODO |
| Tight range | 最近 N bar 的 (max-min)/ATR | N=5..10 | < 1.5 视为 tight | `cta/feature/price_action.py::pa_tight_range_*` |
| 成交量萎缩 | `volume / MA(volume, 20)` | — | < 0.7 | 计算自 OHLCV |
| 上下沿接近度 | \|close - upper\| / width 或 \|close - lower\| / width | — | < 0.2 接近上沿 / 下沿 | TODO |

## 5. 常见策略映射

### 策略 A：临界分数 + 预期方向
- **信号定义**：
  ```text
  threshold_score = 0.3 * (ATR(14)/ATR(50) < 0.6)
                  + 0.25 * (bb_width_rank < 0.2)
                  + 0.2 * (tight_range_count >= 5)
                  + 0.15 * (volume_ratio < 0.7)
                  + 0.1 * (|close-mid|/width < 0.3)
  direction_bias = sign(close - rolling_mid(N=20))   # 靠上沿偏多，靠下沿偏空
  ```
- **开仓触发**：与具体突破策略配合，见 `03_trend_strategies/01_donchian_breakout.md`；本 skill 只产生"可以开"信号。
- **止损规则**：突破失败（回到 range 内）立刻退出。
- **平仓规则**：突破方向走出 1.5 ATR 时启动 trailing。
- **仓位规模**：临界分数 × 基准仓位（高分加仓），见 `07_position_and_portfolio/02_vol_targeting.md`。
- **失败模式**：事件驱动的"假突破"：短时突破后快速回撤。

### 策略 B：时间窗过滤
- 仅在开盘前 30 分钟 / 收盘前 30 分钟关注临界（流动性 + 情绪集中）。

## 6. 代码模块设计

```text
cta/strategy/common/regime/
├── threshold.py                 # 临界打分
├── volume_profile.py            # 成交量压缩
└── range_boundary.py            # 上下沿距离
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class ThresholdState:
    score: float                  # [0, 1]
    direction_bias: int           # -1/0/1
    upper_trigger: float          # 突破上方触发价
    lower_trigger: float          # 突破下方触发价
    time_in_range_bars: int

def compute_threshold_state(
    df: pd.DataFrame,
    atr_fast: int = 14,
    atr_slow: int = 50,
    tight_range_n: int = 5,
    volume_ma: int = 20,
) -> pd.DataFrame:
    """
    每根 bar 产出 ThresholdState 列。
    要求 df 含 pa_atr_* 与 rolling_high/low 特征。
    """
    ...

def estimate_breakout_probability(
    state: ThresholdState,
    historical_base_rate: float = 0.4,
) -> float:
    """
    基于分数映射到历史突破概率（需离线标注 base_rate）。
    """
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：临界命中后 N bar 内的突破发生率 / 突破正确方向率 / 平均持仓 bar / Sharpe / 真假突破比 / 平均盈亏比 / 临界持续 bar 分布
- **分层评估**：按品种、按时段（开盘 / 午盘 / 收盘）、按方向偏好
- **稳健性检验**：
  1. ATR 参数改 (10,40) / (14,50) / (20,60) 分数稳定
  2. 去掉成交量维度后，突破率下降不超过 15%
  3. OOS 的突破率误差应 < 20%

## 8. 常见错误

- 用"今天 bb 窄了一点"就认为临界 → 没和历史分位比
- 忽略 direction_bias → 在下沿附近强行做多突破
- 临界分数高但在震荡年龄很短 → 胜率低
- 漏看事件日历（OPEC / 美联储），临界在事件前毫无意义

## 9. 迭代方向

- v1：加权分数
- v2：把分数映射到突破概率（贝叶斯校准）
- v3：与 `05_regime_switch_strategies/02_breakout_mode_scoring.md` 合并成统一评分
- v4：加入期权隐含波动率（若能拿到）作为临界特征

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/02_range_detection.md`（先有震荡再有临界）、`01_market_regime/04_volatility_regime.md`
- **被依赖**：`02_price_action/01_tight_range_breakout.md`、`03_trend_strategies/01_donchian_breakout.md`、`05_regime_switch_strategies/02_breakout_mode_scoring.md`
- **互补**：`06_filtering_and_scoring/02_breakout_quality_score.md`（突破质量的打分补强）
