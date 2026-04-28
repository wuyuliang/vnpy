# Context 打分 / Context Score

> 归属章节：`06_filtering_and_scoring/` · 前置：`01_market_regime/05_multi_timeframe_alignment.md` · 关联：`cta/feature/price_action_context.py`

## 1. Skill 定义

把**"当前行情上下文"** 打成一个综合分：多周期对齐、regime、结构位置、时间段。与 setup_quality / breakout_quality 正交，用于决定"这个形态是否在合适的舞台上演出"。

## 2. 解决什么问题

- 痛点 1：好 setup 出现在不合时宜的 context 里（如震荡中做突破 setup）。
- 痛点 2：setup 质量打分只看形态，忽略环境。
- 痛点 3：context 打分有助于 regime 过渡期识别（context 分低即过渡信号）。
- 增量价值：形态 × 环境 = 最终胜率。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：所有（本 skill 天然多周期）
- **行情状态前提**：任何
- **不适用场景**：数据不足 100 bar 的新合约

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| HTF 对齐 | HTF 方向与 setup 方向一致 | — | 1/0 | `cta/feature/price_action_context.py::pa_htf_*` |
| MTF 对齐 | MTF 同向 | — | 1/0 | 同上 |
| regime 适配 | 当前 regime 是否允许该 setup | — | 允许=1 | `05_regime_switch_strategies/03` 输出 |
| 结构位置 | 价格在 HTF leg 的位置（起 / 中 / 末） | — | 起/中 +分 | `cta/feature/price_action_context.py` |
| 时间段 | 开盘 / 午盘 / 尾盘 | — | 开 / 尾更强 | 计算 |

## 5. 常见策略映射

### 策略 A：context_score
- **信号定义**：
  ```text
  s_htf    = 1 if htf_dir == setup_dir else 0
  s_mtf    = 1 if mtf_dir == setup_dir else 0.3
  s_regime = regime_allow[setup_type]
  s_pos    = pos_weight[leg_position]     # start: 1.0 / mid: 0.7 / end: 0.3
  s_time   = time_weight[bar.session]
  
  context_score = 0.30*s_htf + 0.20*s_mtf + 0.20*s_regime + 0.20*s_pos + 0.10*s_time
  ```
- **应用**：
  - context_score < 0.3 → 跳过
  - final_score = setup_quality × context_score × breakout_quality（几何平均）
  - 最终 score 高 → 加仓倍数

## 6. 代码模块设计

```text
cta/strategy/common/scoring/
├── context.py
└── final_score.py        # 组合三类 score
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

@dataclass
class ContextScore:
    score: float
    components: dict[str, float]

def compute_context_score(
    df_ltf: pd.DataFrame,
    df_mtf: pd.DataFrame,
    df_htf: pd.DataFrame,
    bar_idx_ltf: int,
    setup_type: str,
    setup_dir: Literal['long','short'],
) -> ContextScore:
    ...

def combine_final_score(
    setup_q: float,
    breakout_q: float,
    context: float,
) -> float:
    """几何平均 or 加权，避免单维突出。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：context 分桶的胜率 / 单调性 / 过滤掉的坏 context 占比 / 误杀率 / 与 setup_quality 的联合单调性 / Sharpe / 总 PnL 增量
- **分层评估**：按 HTF 对齐情况 / 按 regime / 按时段
- **稳健性检验**：
  1. 各 component 权重 ±20%
  2. 关闭 context gate 的 PnL 变化
  3. 多周期误差（HTF 数据晚到）情形下降级

## 8. 常见错误

- HTF 取"当前 bar"（含未完结数据）→ 未来函数
- 时间段权重按样本内最优挑 → 过拟合
- regime 与 setup_type 的允许矩阵不维护
- context_score 全部用于过滤而非 sizing → 浪费信息

## 9. 迭代方向

- v1：加权聚合
- v2：XGBoost 打分（context 高维特征）
- v3：把 `05_regime_switch_strategies/03` 的 regime age / conf 直接进入 context
- v4：跨品种 context（宏观 / 大类走势）

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/05_multi_timeframe_alignment.md`、`05_regime_switch_strategies/03_regime_switch_signal.md`
- **被依赖**：`07_position_and_portfolio/02_vol_targeting.md`、`09_ml_augmentation/01_trade_filter_model.md`
- **互补**：`06_filtering_and_scoring/01_setup_quality_score.md`、`06_filtering_and_scoring/02_breakout_quality_score.md`
