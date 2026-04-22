# 过渡期风控 / Transition Risk Control

> 归属章节：`05_regime_switch_strategies/` · 前置：`03_regime_switch_signal.md` · 关联：`cta/strategy/brooks/core/risk/portfolio.py`

## 1. Skill 定义

当 `regime == 'transition'` 或切换刚发生时，**额外的一层风险管理**：减仓、收紧止损、关闭加仓、禁止新开。避免在方向不明期间被双向抽打。

## 2. 解决什么问题

- 痛点 1：regime 切换初期，旧策略还在开仓、新策略也开始开仓，反向冲突。
- 痛点 2：切换附近回撤大，是策略杀手段。
- 痛点 3：仓位调整节奏缺乏规则。
- 增量价值：降低过渡期回撤，平滑资金曲线，提升长期 Calmar。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：所有
- **行情状态前提**：regime == transition 或 regime 刚切换 < 5 bar
- **不适用场景**：regime 稳定的时段

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| transition_flag | regime 是否是 transition 或 age<5 | — | True/False | `03_regime_switch_signal` 输出 |
| position_scale | transition 下仓位缩放 | 0.5 | — | TODO |
| stop_tighten | 止损缩放 | 0.7 (更紧) | — | TODO |
| add_on_disabled | 禁止加仓 | True | — | — |
| new_entry_disabled | 禁止新开 | 可选 True | — | — |

## 5. 常见策略映射

### 策略 A：Transition Risk Gate
- **信号定义**：当 `regime_state.label == 'transition'` 或 `regime_state.age_bars < 5`。
- **规则**：
  - 已有仓位：减至 50%，止损上移 30%
  - 新开单：禁止（或仅允许对齐 3 周期的高置信度信号）
  - 加仓：禁止
  - 持仓时间：限制 transition 下最长 10 bar，不到则平
- **仓位规模**：`effective_size = base_size * 0.5`。
- **失败模式**：transition 持续过久（> 30 bar）→ 可能实际是 range 误判为 transition。

### 策略 B：组合级别 transition 风控
- 当 > 30% 品种同时进入 transition → 整体组合仓位 × 0.7。

## 6. 代码模块设计

```text
cta/strategy/common/risk/
├── transition_gate.py
└── combined_risk.py        # 与 drawdown_control 合流
```

```python
from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class RiskAdjustment:
    size_multiplier: float
    stop_multiplier: float
    allow_new_entry: bool
    allow_add_on: bool
    max_hold_bars: int | None

def transition_risk_adjustment(
    regime_label: str,
    regime_age: int,
    portfolio_transition_ratio: float = 0.0,
) -> RiskAdjustment:
    """
    根据当前 regime 状态返回风控调整。
    portfolio_transition_ratio: 组合中 transition 品种占比，> 0.3 时整体降仓。
    """
    ...

def enforce_transition_max_hold(
    position_bars: int,
    adj: RiskAdjustment,
) -> bool:
    """是否该平（超过 max_hold_bars）。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：transition 段 vs non-transition 段的回撤对比 / PnL 贡献 / 触发次数 / Sharpe 改善 / 平均持仓 / 与 baseline（无 transition gate）对比 / 额外成本
- **分层评估**：按品种 / 按 transition 产生原因（vol / trend / range 切换）
- **稳健性检验**：
  1. size_multiplier 0.3/0.5/0.7 单调性
  2. 关闭 portfolio transition 级别后效果
  3. OOS 的 transition 频率与 IS 相近

## 8. 常见错误

- 一切 transition 就全部清仓 → 牺牲真正延续的单
- 规则过严 → 长期欠仓
- 未记录 transition 轨迹 → 事后无法归因
- 与 `drawdown_control` 重复降仓 → 双重缩放

## 9. 迭代方向

- v1：固定系数
- v2：系数依赖 transition 原因（vol vs trend）
- v3：ML 预测"transition 会持续多久 / 转向哪"
- v4：组合级 transition 打分（多品种聚合）

## 10. 与其他 Skills 的关系

- **依赖**：`05_regime_switch_strategies/03_regime_switch_signal.md`
- **被依赖**：所有策略 skill（通过 risk gate）
- **互补**：`07_position_and_portfolio/04_drawdown_control.md`（单笔 vs 组合的风控两层）
