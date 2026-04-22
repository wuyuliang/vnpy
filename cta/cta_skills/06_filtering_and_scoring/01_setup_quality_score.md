# Setup 质量打分 / Setup Quality Score

> 归属章节：`06_filtering_and_scoring/` · 前置：`02_price_action/`（各种 setup） · 关联：`cta/feature/price_action.py`、`cta/feature/price_action_context.py`

## 1. Skill 定义

对一个已识别的 setup（tight range / flag / BP / failed breakout 等）**打一个形态质量分**（0-1）：表示该 setup 本身有多"干净 / 有力 / 符合原教义"。是 Brooks 价格行为派的核心度量。

## 2. 解决什么问题

- 痛点 1：所有 tight range 都当作一样的 tight range → 忽略质量差异。
- 痛点 2：有些形态一眼就明显好 / 坏，但没法自动量化。
- 痛点 3：缺少"形态质量 × 上下文" 的多维评估。
- 增量价值：把 setup 从"二值 True/False"变成"连续 0-1"，可作为仓位 / 过滤的输入。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：所有
- **行情状态前提**：已有某种 setup 被识别
- **不适用场景**：没 setup 的 bar，本 skill 无意义

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| signal bar 体量 | (close-open)/ATR | — | > 0.5 良好 | 计算 |
| signal bar 上下影 | 影线长度 / 实体 | — | < 1.0 实体强 | `pa_upper_shadow / pa_lower_shadow`（若有）或 TODO |
| 前置压缩 | 前 5 bar 是否 tight | — | 是 +分 | `pa_tight_range_*` |
| 波动率合理度 | ATR 百分位 | — | 20-60% 为理想 | TODO |
| 近期干净度 | 最近 K bar 是否无未突破假试探 | K=10 | — | TODO |

## 5. 常见策略映射

### 策略 A：setup_quality_score 聚合
- **信号定义**：
  ```text
  s_body   = clamp(abs(body)/ATR, 0, 1)
  s_shadow = 1 - clamp(shadow_sum/body, 0, 1)
  s_prior  = 1 if prior_tight else 0
  s_atr    = triangular(atr_pct, peak=0.4, width=0.4)
  s_clean  = 1 - clamp(recent_fakes/K, 0, 1)
  
  setup_quality = 0.30*s_body + 0.20*s_shadow + 0.20*s_prior + 0.15*s_atr + 0.15*s_clean
  ```
- **应用**：
  - `setup_quality < 0.3` → 跳过
  - `0.3 ≤ sq < 0.5` → 基础仓
  - `sq ≥ 0.5` → 1.5x 仓位
- **失败模式**：某维度长期缺失（如无 shadow 特征）导致分数恒定，退化为 3 维打分。

## 6. 代码模块设计

```text
cta/strategy/common/scoring/
├── setup_quality.py
└── registry.py             # setup_type -> component weights
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

SetupType = Literal['tight_range','bull_flag','bear_flag','bp','failed_break','hl_reversal']

@dataclass
class SetupQuality:
    score: float
    components: dict[str, float]
    setup_type: SetupType

def score_setup(
    df: pd.DataFrame,
    bar_idx: int,
    setup_type: SetupType,
    weights: dict[str, float] | None = None,
) -> SetupQuality:
    """按 setup_type 选择合适的 components 与 weights。"""
    ...

def setup_quality_gate(
    sq: SetupQuality,
    min_score: float = 0.3,
) -> bool:
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：按 score 分桶的胜率 / 盈亏比 / 平均 R / 单调性 / 每桶交易次数 / Sharpe / 与无打分版对比的 PnL 增量
- **分层评估**：按 setup_type / 按品种 / 按周期
- **稳健性检验**：
  1. 权重 ±20% 稳定
  2. 某 component 数据缺失时降级表现
  3. OOS 单调性

## 8. 常见错误

- 分数直接等同胜率 → 没校准
- 不分 setup_type 用同套权重 → 各形态特性不同
- score 阈值每季度调 → 过拟合

## 9. 迭代方向

- v1：规则聚合
- v2：logistic 回归校准
- v3：按 setup_type 训不同模型
- v4：替换为 `09_ml_augmentation/01_trade_filter_model.md` 的 ML 模型

## 10. 与其他 Skills 的关系

- **依赖**：`02_price_action/`（setup 识别）
- **被依赖**：`07_position_and_portfolio/02_vol_targeting.md`（score × vol target → 最终仓位）
- **互补**：`06_filtering_and_scoring/02_breakout_quality_score.md`、`06_filtering_and_scoring/03_context_score.md`
