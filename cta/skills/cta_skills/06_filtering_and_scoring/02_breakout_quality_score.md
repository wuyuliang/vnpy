# 突破质量打分 / Breakout Quality Score

> 归属章节：`06_filtering_and_scoring/` · 前置：`05_regime_switch_strategies/02_breakout_mode_scoring.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

对已触发的突破（价格已越过 range / Donchian / ATR 通道）打**突破本身的质量分**：close 距离边界、成交量放大、bar 形态、后续跟进。本 skill 比 `05/02` 更靠后，关注"突破是否真的站住"。

## 2. 解决什么问题

- 痛点 1：突破触发不等于突破成立，需要质量确认。
- 痛点 2：用 close 价 vs 新高的数值偏差来量化"真突破"。
- 痛点 3：跟进 1-2 bar 的形态对后续成功率影响巨大。
- 增量价值：在 Donchian / tight range / ATR 突破之后加一层"是否真突破"过滤。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：所有
- **行情状态前提**：刚发生突破
- **不适用场景**：无突破 bar

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| close 越界幅度 | (close - breakout_level)/ATR | — | > 0.2 视为越界充分 | 计算 |
| 成交量放大 | volume / MA(vol,20) | — | > 1.3 | 计算 |
| bar 实体比 | body/range | — | > 0.7 | 计算 |
| 后续跟进 | 下 1-2 bar 是否延续 | — | close 仍在越界 | 计算 |
| signal bar 失败影线 | 越界 bar 反向影线长度 | — | < 0.3 * range | 计算 |

## 5. 常见策略映射

### 策略 A：breakout_quality_score
- **信号定义**：
  ```text
  s_cross  = clamp(close_crossing/ATR / 0.5, 0, 1)
  s_vol    = clamp((vol_ratio - 1)/0.5, 0, 1)
  s_body   = clamp(body_ratio / 0.8, 0, 1)
  s_follow = clamp(consecutive_break_bars / 2, 0, 1)
  s_shadow = 1 - clamp(reverse_shadow_ratio / 0.5, 0, 1)
  
  breakout_quality = 0.3*s_cross + 0.2*s_vol + 0.2*s_body + 0.2*s_follow + 0.1*s_shadow
  ```
- **应用**：
  - `bq < 0.3` → 平掉任何基于此突破的新仓
  - `0.3 ≤ bq < 0.5` → 基础仓位
  - `bq ≥ 0.5` → 1.3x 仓位
- **失败模式**：在流动性极差时段（午夜）质量分可能误判。

## 6. 代码模块设计

```text
cta/strategy/common/scoring/
├── breakout_quality.py
└── breakout_quality_components.py
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class BreakoutQuality:
    score: float
    components: dict[str, float]

def score_breakout(
    df: pd.DataFrame,
    breakout_bar_idx: int,
    breakout_level: float,
    atr: float,
    weights: dict[str,float] | None = None,
) -> BreakoutQuality:
    """
    在 breakout_bar_idx 之后立刻调用，或在 +1 bar 后重新调用（含 follow）。
    """
    ...

def breakout_quality_gate(
    bq: BreakoutQuality,
    min_score: float = 0.3,
) -> bool:
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：各分数桶胜率 / 单调性 / 盈亏比 / 过滤掉的假突破率 / 错杀率 / 总 PnL 增量 / Sharpe
- **分层评估**：按品种 / 按 breakout 类型（Donchian / tight range / ATR）
- **稳健性检验**：
  1. 权重 ±20%
  2. 关闭单个 component
  3. OOS 单调性

## 8. 常见错误

- 只看一根 bar 越界 → 忽略跟进
- 成交量数据有空值时 s_vol = 0 造成误过滤
- 只评估真突破率没看错杀率 → 可能过滤过度
- 在不同 interval 用同一权重

## 9. 迭代方向

- v1：规则聚合
- v2：logistic 校准
- v3：ML 打分（XGBoost）
- v4：与 setup_quality 合并为 combined_score

## 10. 与其他 Skills 的关系

- **依赖**：`03_trend_strategies/01-02`、`02_price_action/01`
- **被依赖**：`07_position_and_portfolio/02_vol_targeting.md`
- **互补**：`05_regime_switch_strategies/02_breakout_mode_scoring.md`（偏"事前"，本 skill 偏"事后"）
