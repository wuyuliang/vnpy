# 波动率状态识别 / Volatility Regime

> 归属章节：`01_market_regime/` · 前置技能：无 · 关联：`cta/feature/price_action.py`

## 1. Skill 定义

识别波动率的**高 / 低 / 压缩 / 扩张**四态，并给出 vol_score、vol_regime 标签。所有其它 regime（趋势 / 震荡 / 临界）都应**在同等 vol 水平下比较**，否则指标阈值会漂。

## 2. 解决什么问题

- 痛点 1：高波动时的 ATR 和低波动时的 ATR 数值差 10 倍，固定阈值无法跨时段 / 跨品种。
- 痛点 2：趋势 / 震荡判断不看波动率 → 相同 ADX 在高 vol 和低 vol 有不同含义。
- 痛点 3：仓位规模不随波动率调整 → 高 vol 时仓位过大爆仓。
- 增量价值：波动率成为**所有其它 regime 的坐标系**。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部（不同周期的 ATR 含义不同）
- **行情状态前提**：无
- **不适用场景**：新上市合约（历史 < 100 bar，波动率统计不可靠）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| ATR(N) | Wilder ATR | N=14 | — | `cta/feature/price_action.py::pa_atr_*` |
| 年化波动率 | `std(log_ret) * sqrt(252)` | 窗口 60 bar | 黑色 25% / 农产品 20% | TODO |
| ATR 百分位 | ATR(14) 在过去 252 bar 的百分位 | — | >80% 高波 / <20% 低波 | TODO |
| ATR 压缩比 | `ATR(14) / ATR(60)` | — | < 0.7 压缩 / > 1.3 扩张 | `cta/feature/price_action.py::pa_atr_*` |
| 日内范围扩张率 | `(high-low)[t] / ATR(14)[t-1]` | — | > 1.5 视为扩张日 | TODO |

## 5. 常见策略映射

### 策略 A：四态分类
- **信号定义**：
  ```text
  vol_pct = percentile(ATR(14), window=252)
  ratio   = ATR(14) / ATR(60)
  
  if vol_pct < 0.2 and ratio < 0.7: regime = 'compression'
  elif vol_pct > 0.8 and ratio > 1.3: regime = 'expansion'
  elif vol_pct < 0.4: regime = 'low'
  else: regime = 'high'
  ```
- **应用规则**：
  - `compression` → 开启突破准备（见 `05_regime_switch_strategies/01_volatility_compression_expansion.md`）
  - `expansion` → 减仓、收紧止损
  - `low` → 倾向均值回归
  - `high` → 倾向趋势跟随
- **仓位规模**：
  ```text
  size = base_risk / (ATR(14) * contract_multiplier)   # vol targeting
  ```
- **失败模式**：主力切换导致 ATR 跳变。

## 6. 代码模块设计

```text
cta/strategy/common/regime/
├── volatility.py                # 四态分类
└── vol_target.py                # vol targeting 仓位（被 07 章复用）
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

VolRegime = Literal['compression','expansion','low','high']

@dataclass
class VolState:
    regime: VolRegime
    atr: float
    atr_pct: float                # 历史分位 [0,1]
    atr_ratio_fast_slow: float
    annualized_vol: float

def compute_vol_state(
    df: pd.DataFrame,
    atr_n_fast: int = 14,
    atr_n_slow: int = 60,
    pct_window: int = 252,
) -> pd.DataFrame:
    """每根 bar 产出 VolState 列。"""
    ...

def vol_target_size(
    atr: float,
    contract_multiplier: float,
    equity: float,
    risk_pct: float = 0.003,
) -> float:
    """
    vol targeting 单位大小：`risk_pct * equity / (atr * multiplier)`。
    被 07_position_and_portfolio/02_vol_targeting.md 复用。
    """
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：四态分布 / 四态切换次数 / 每态平均收益 / 每态 Sharpe / 每态平均持仓 bar / 误分类率（与前后 5 bar 比较） / vol targeting 下 drawdown 改善
- **分层评估**：按品种、按年份、按交易时段
- **稳健性检验**：
  1. 窗口改 126/252/504，规制分布变化 < 20%
  2. 用 realized vol 和 ATR 分别算，regime 重合度 > 70%
  3. 跨品种一致性（同时间点黑色 vs 能化的状态是否同向？）

## 8. 常见错误

- 用绝对 ATR 数字比较不同品种 → 必须用百分位
- 忽略主力换月后 ATR 跳变
- 低波时用趋势策略目标 → 大概率空仓很久
- vol targeting 只看 ATR 不看保证金 → 实盘保证金不够开仓

## 9. 迭代方向

- v1：四态分类
- v2：融入隐含波动率（若有期权数据）
- v3：GARCH / 动态 beta 估波动率
- v4：波动率聚类的 ML 化（见 `09_ml_augmentation/02_regime_classifier.md`）

## 10. 与其他 Skills 的关系

- **依赖**：无（本 skill 是所有其它 regime 的基石）
- **被依赖**：所有 regime skill（`01/02/03/05`）+ `07_position_and_portfolio/02_vol_targeting.md`
- **互补**：`05_regime_switch_strategies/01_volatility_compression_expansion.md`
