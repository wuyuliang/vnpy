# 风险收益比打分 / Risk-Reward Score

> 归属章节：`06_filtering_and_scoring/` · 前置：setup + context 识别 · 关联：`cta/strategy/brooks/core/risk/`

## 1. Skill 定义

对一个**待开仓的交易方案**估计其预期 RR（risk-reward），并打分。只接受 RR ≥ 某阈值（常 2.0）的交易。是 Brooks 派的"必须有 10 个 tick 的利润潜力"规则的量化。

## 2. 解决什么问题

- 痛点 1：开单不看目标 → 赢小亏大。
- 痛点 2：止损是已知的（signal bar），但目标不清。
- 痛点 3：没有统一 RR 口径，策略间对比难。
- 增量价值：把"RR ≥ 2"变成客观可计算的 gate。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：所有
- **行情状态前提**：任意
- **不适用场景**：RR 无法估计的情况（如刚上市合约）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| initial_risk | entry - stop（多），stop - entry（空） | — | > 0 | 计算 |
| target_1 | 最近 resistance / support | N=20/50 | — | `pa_rolling_*` |
| target_2 | measured move：原 leg 延长 | — | — | 计算 |
| atr_based_target | entry + k × ATR | k=2..3 | — | 计算 |
| RR = |target - entry| / initial_risk | — | ≥ 2.0 | 计算 |

## 5. 常见策略映射

### 策略 A：RR gate
- **信号定义**：
  ```text
  risk = |entry - stop|
  target_cand = [nearest_swing, measured_move, entry + 2*ATR]
  target = min/max(target_cand) depending direction
  rr = |target - entry| / risk
  ```
- **应用**：
  - `rr < 1.5` → 跳过
  - `1.5 ≤ rr < 2.5` → 基础仓
  - `rr ≥ 2.5` → 1.3x 仓位
- **开仓触发**：与策略本身相同。
- **仓位规模**：rr × base_size（或直接 gate）。
- **失败模式**：target 被近阻力压住 → RR 虚低；或 target 过远（measured move）→ RR 虚高。

## 6. 代码模块设计

```text
cta/strategy/common/scoring/
├── rr.py
└── targets.py        # 多种 target 构造
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

@dataclass
class RRAssessment:
    entry: float
    stop: float
    target: float
    rr: float
    target_source: Literal['swing','measured_move','atr','blended']

def compute_rr(
    df: pd.DataFrame,
    bar_idx: int,
    entry: float,
    stop: float,
    direction: Literal['long','short'],
    lookback: int = 50,
    atr_k: float = 2.0,
) -> RRAssessment:
    ...

def rr_gate(
    rr: float,
    min_rr: float = 1.5,
) -> bool:
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：RR 桶（<1.5 / 1.5-2.5 / >2.5）的胜率 / 盈亏比 / 命中 target 率 / 实际 R 与预估 RR 的偏差 / Sharpe / 过滤掉低 RR 的 PnL 增量 / 交易次数下降
- **分层评估**：按 target_source / 按品种
- **稳健性检验**：
  1. target_source 改变时 RR 分布变化
  2. 阈值 1.0 / 1.5 / 2.0 / 2.5 的权衡
  3. 预估 RR 与 OOS 实际 R 的一致性

## 8. 常见错误

- 用"我希望"的 target → 主观拔高 RR
- 忽略滑点 → 实际 R 比预估小 20%+
- 目标被近阻力压缩但仍强行开
- 对每种 setup_type 用同一 target 逻辑

## 9. 迭代方向

- v1：规则组合
- v2：按 setup_type 选 target 模式
- v3：ML 预测 MFE / target 到达概率（`09_ml_augmentation/03`）
- v4：动态 RR 阈值（vol 高时门槛更高）

## 10. 与其他 Skills 的关系

- **依赖**：各 setup skill 提供 entry / stop
- **被依赖**：`07_position_and_portfolio/01_single_trade_risk.md`（仓位 = risk / stop 距离）
- **互补**：`09_ml_augmentation/03_mfe_mae_prediction.md`
