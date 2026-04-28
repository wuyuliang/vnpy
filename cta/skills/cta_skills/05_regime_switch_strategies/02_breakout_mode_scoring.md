# 突破模式打分 / Breakout Mode Scoring

> 归属章节：`05_regime_switch_strategies/` · 前置：`01_market_regime/03_breakout_threshold_detection.md`、`02_price_action/01_tight_range_breakout.md` · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

对"即将 / 正在发生的突破"打**综合质量分**（0-1），用于：
- 决定是否开仓（score > 阈值）
- 决定仓位规模（score 高 → 重仓）
- 决定预期盈亏比（score 高 → 更大目标）

是 Brooks v3 里 XGBoost gate 的规则前身。

## 2. 解决什么问题

- 痛点 1：突破质量参差不齐，一刀切参数不合理。
- 痛点 2：把"多指标打分"工程化，避免规则叠罗汉。
- 痛点 3：score 可以迁移到 ML 模型作为 label smoothing。
- 增量价值：用统一分数 gate 所有突破类策略，显著降低假突破率。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：minute60 / minute30 / minute15 / minute5
- **行情状态前提**：前序在压缩 / 震荡
- **不适用场景**：趋势末期、事件驱动

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| tight_range_count | 见 `02_price_action/01_tight_range_breakout.md` | — | ≥ 5 | `pa_tight_range_*` |
| ATR 压缩比 | 见 `04_volatility_regime.md` | — | < 0.7 | `pa_atr_*` |
| 成交量萎缩 | vol/MA(vol,20) | — | < 0.8 | 计算 |
| HTF 同向 | 见 `01_trend_detection.md` | — | 同向 +1 | 传入 |
| 结构支撑 | 突破点上方 / 下方是否有前高 / 前低支撑 | — | 有则更强 | `pa_rolling_*` |
| 时间窗 | 突破发生时段 | 开盘 30min / 午后启动 | — | 计算 |

## 5. 常见策略映射

### 策略 A：加权打分 + 阈值 gate
- **信号定义**：
  ```text
  s_tight   = clamp(tight_count / 10, 0, 1)
  s_atr     = clamp((0.8 - atr_ratio)/0.3, 0, 1)        # atr_ratio < 0.8 拿分
  s_vol     = clamp((0.9 - vol_ratio)/0.3, 0, 1)
  s_htf     = 1 if HTF_same else 0
  s_struct  = 1 if breakout breaks prior_high/low within 20 bar else 0
  s_time    = 1 if in_session_window else 0
  
  score = 0.25*s_tight + 0.20*s_atr + 0.15*s_vol + 0.20*s_htf + 0.15*s_struct + 0.05*s_time
  ```
- **应用**：
  - score < 0.3 → 跳过
  - 0.3 ≤ score < 0.5 → 基准仓位
  - score ≥ 0.5 → 1.5x 仓位
- **开仓触发 / 止损 / 平仓**：与具体突破策略（Donchian / tight range）相同。
- **失败模式**：score 高但 HTF 反转初期，应与 transition_risk_control 联动。

## 6. 代码模块设计

```text
cta/strategy/common/scoring/
├── breakout_score.py
└── presets.py              # 不同品种的权重配置
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class BreakoutScoreResult:
    score: float                  # [0, 1]
    parts: dict[str, float]       # 各组成分
    gate_pass: bool
    size_multiplier: float

def compute_breakout_score(
    df: pd.DataFrame,
    bar_idx: int,
    htf_same_direction: bool,
    weights: dict[str, float] | None = None,
) -> BreakoutScoreResult:
    """
    返回 BreakoutScoreResult。
    weights 不传则用默认 preset。
    """
    ...

def calibrate_weights_from_history(
    df: pd.DataFrame,
    labels: pd.Series,
) -> dict[str, float]:
    """
    用 logistic 回归从历史 profitable vs not 标签学权重。
    过渡到 ML 版本的桥梁。
    """
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：各 score 桶（<0.3 / 0.3-0.5 / >0.5）的胜率 / 盈亏比 / Sharpe / 单调性（score 越高越好）/ 过滤掉的假突破率 / 错杀真突破率 / 总 PnL 增量
- **分层评估**：按品种 / 按组分维度（去掉某维度的影响）
- **稳健性检验**：
  1. 权重 ±20% 稳定性
  2. 单调性 check（每 0.1 桶胜率应单调）
  3. OOS 单调性保持

## 8. 常见错误

- 分数带主观常数（"我觉得这个因子更重要"）→ 调了 10 次仍在 IS
- 用历史 hit / miss 直接训线性回归 → 过拟合，应加 regularization
- score 阈值每个品种都调 → 过拟合
- 不 track 分数分布变化，实盘时分布漂移未察觉

## 9. 迭代方向

- v1：等权 / 手工权重
- v2：logistic 校准
- v3：替换为 XGBoost（见 `09_ml_augmentation/01_trade_filter_model.md`）
- v4：online 学习，按季度更新权重

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/03_breakout_threshold_detection.md`、`01_market_regime/01_trend_detection.md`
- **被依赖**：`03_trend_strategies/01_donchian_breakout.md`、`02_price_action/01_tight_range_breakout.md`、`07_position_and_portfolio/02_vol_targeting.md`
- **互补**：`06_filtering_and_scoring/02_breakout_quality_score.md`（本 skill 是其雏形）
