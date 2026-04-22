# 多周期状态对齐 / Multi-Timeframe Alignment

> 归属章节：`01_market_regime/` · 前置技能：`01-04` · 关联：`cta/strategy/brooks/core/signal/`

## 1. Skill 定义

把 HTF（高周期，如 day/60m）的 bias + MTF（中周期，如 30m/15m）的 setup + LTF（低周期，如 5m/1m）的 entry **对齐**，只有三者方向一致才进场。这是 Brooks v3 的核心设计，也是大多数成熟 CTA 的通用套路。

## 2. 解决什么问题

- 痛点 1：只看单周期 → 大小趋势冲突导致高回撤。
- 痛点 2：只看 HTF → 滞后，入场点偏差。
- 痛点 3：只看 LTF → 噪声多，胜率低。
- 增量价值：HTF 定方向、MTF 找 setup、LTF 精确入场 → 胜率 + 盈亏比同时提升。

## 3. 适用市场 / 适用场景

- **品种类别**：全部，尤其对流动性好的日内品种
- **周期**：HTF / MTF / LTF 三元组推荐：
  - day / minute60 / minute30
  - minute60 / minute30 / minute15
  - minute30 / minute15 / minute5
  - minute15 / minute5 / minute
- **行情状态前提**：HTF 必须有清晰 bias
- **不适用场景**：HTF 为震荡 / 过渡时本 skill 失效，应改用震荡策略

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| HTF bias | HTF 的 trend_score（见 `01_trend_detection.md`） | — | abs > 0.5 | `cta/strategy/brooks/core/signal/htf_bias.py` |
| MTF setup | MTF 的 setup 形态（tight range / pullback / etc.） | — | pa_* 特征命中 | `cta/strategy/brooks/core/signal/mtf_setup.py` |
| LTF entry | LTF 的 signal bar + next bar 触发 | — | 触发价 stop | `cta/strategy/brooks/core/signal/ltf_entry.py` |
| 对齐分数 | HTF × MTF × LTF 方向一致计数 | — | = 3 才入场 | TODO: 聚合 |
| 周期比 | HTF:MTF:LTF 的时长比 | ~ 4:2:1 或 6:2:1 | — | — |

## 5. 常见策略映射

### 策略 A：三周期共振（Brooks v3 套路）
- **信号定义**：
  ```text
  htf_dir = sign(htf.trend_score) if |htf.trend_score| > 0.5 else 0
  mtf_ok  = mtf.setup_type in {'pullback','flag','tight_range'} and mtf.dir == htf_dir
  ltf_trigger = ltf.signal_bar_valid and ltf.next_bar_breaks(signal_bar.high/low)
  
  enter = (htf_dir != 0) and mtf_ok and ltf_trigger
  side  = 'long' if htf_dir > 0 else 'short'
  ```
- **开仓触发**：LTF stop 单（突破 signal bar 的高/低）。
- **止损规则**：signal bar 的反向极值 + 1 tick。
- **平仓规则**：
  - MTF 反转（setup 失效）
  - HTF bias 衰竭（`|trend_score| < 0.3`）
  - 或达到 2R 启动 trailing
- **仓位规模**：vol targeting × (对齐分数 / 3) 加权。
- **失败模式**：HTF 转换期。参考 `05_regime_switch_strategies/04_transition_risk_control.md`。

### 策略 B：两周期快速版
- 只用 HTF + LTF，忽略 MTF（适合日线 + 60m 的中频策略）。

## 6. 代码模块设计

```text
cta/strategy/{name}/signal/
├── htf_bias.py       # 仅消费 HTF pa_*
├── mtf_setup.py      # 仅消费 MTF pa_*
├── ltf_entry.py      # 仅消费 LTF pa_*
└── alignment.py      # 三者汇合
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

@dataclass
class AlignmentResult:
    enter: bool
    side: Literal['long','short','flat']
    htf_dir: int
    mtf_setup: str | None
    ltf_trigger_price: float | None
    alignment_score: int       # 0..3

def align(
    htf_state: pd.Series,
    mtf_state: pd.Series,
    ltf_bar: pd.Series,
) -> AlignmentResult:
    """
    在 LTF 每根 bar 上调用，读取 HTF/MTF 同时间点（或上一 HTF/MTF bar 的值）。
    参考 cta/strategy/brooks/core/features/adapter.py 的 multi-interval 读取。
    """
    ...

def resolve_enter_price(
    alignment: AlignmentResult,
    signal_bar: pd.Series,
    tick_size: float,
) -> float | None:
    """把 alignment + signal bar 映射为具体 stop 触发价。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：对齐分数 ∈ {0,1,2,3} 各自的胜率 / 盈亏比 / 平均持仓 / 交易次数分布 / 未对齐时"被放过的好机会" / 对齐误判率 / Sharpe
- **分层评估**：按品种、按三元组组合（day/60m/30m vs 60m/30m/15m 等）
- **稳健性检验**：
  1. 对齐分数阈值改 2/3 → 胜率和次数的平衡
  2. 改三元组周期比，Sharpe 下降 < 20%
  3. LTF 的 signal bar 定义调整，系统稳定

## 8. 常见错误

- HTF / MTF / LTF 数据不对齐（时间戳错位）→ 回测错误
- HTF 用"当前 bar"的 trend_score → 未来函数（HTF 这根 bar 还没 close）
- 三周期一致但 HTF bias 方向才形成 5 bar → 假象
- 忽略对齐分数与胜率的非线性关系（2 分和 3 分差距可能不大）

## 9. 迭代方向

- v1：硬对齐（必须 3 分）
- v2：软对齐（2 分也开，仓位折半）
- v3：HTF bias 连续性判定（不只是当前值，还看过去 N bar 稳定性）
- v4：用 ML 学"哪种对齐模式盈亏比最高"

## 10. 与其他 Skills 的关系

- **依赖**：`01_market_regime/01_trend_detection.md`、`01_market_regime/02_range_detection.md`、`01_market_regime/04_volatility_regime.md`
- **被依赖**：所有策略 skill（几乎都要用）、`02_price_action/`（价格行为一般在 MTF 上判断）
- **互补**：`06_filtering_and_scoring/03_context_score.md`（对齐是 context 的子成分）
