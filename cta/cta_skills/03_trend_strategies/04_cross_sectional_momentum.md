# 横截面动量 / Cross-Sectional Momentum

> 归属章节：`03_trend_strategies/` · 前置：`00_overview_methodology/02_research_boundary.md` · 关联：`cta/feature/symbols_research_ranking.csv`

## 1. Skill 定义

每个调仓周期，把研究池中所有品种按"过去 N 期收益率"排序，**做多排名前 k 的品种，做空后 k**。与时序动量互补，利用横截面的相对强弱。

## 2. 解决什么问题

- 痛点 1：时序动量在不同品种同步震荡时无法分辨。
- 痛点 2：单品种策略无法受益于"同板块强弱分化"。
- 痛点 3：缺少天然多品种 / 组合级趋势。
- 增量价值：降低与时序动量策略的相关性，提升组合 Sharpe。

## 3. 适用市场 / 适用场景

- **品种类别**：研究池 ≥ 10 个品种（A+B 档）
- **周期**：day / week（低频最稳；分钟级噪声太大）
- **行情状态前提**：品种间存在强弱分化
- **不适用场景**：研究池品种高度相关（共同涨跌，选不出差异）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| ret(N) | `close[t]/close[t-N] - 1` | N=20/60/120 | — | 计算 |
| 标准化动量 | `ret(N) / std(ret, N)` | — | 避免高波压制 | TODO |
| 排名 | 全市场横截面 rank | — | top 20% / bot 20% | TODO |
| 相关性 | 品种间 ret 相关 | 窗口 60 | 过高则剔除 | TODO |
| 换手频率 | 调仓频率 | 每周/每月 | — | — |

## 5. 常见策略映射

### 策略 A：Long-Short 动量组合
- **信号定义**：
  ```text
  每周末:
    for each symbol in pool:
      mom = ret(60)_{std adjusted}
    排序后:
      long top 20%
      short bottom 20%
  ```
- **开仓触发**：下周一开盘市价，等权配 long-short。
- **止损规则**：组合级别（最大日回撤 ≥ 3% → 减半仓）。
- **平仓规则**：下一次调仓时重新分配。
- **仓位规模**：总 gross exposure = 100% 权益，net exposure 约 0。
- **失败模式**：系统性风险事件（2020-03 全市场同跌）时 long-short 两边同亏。

### 策略 B：Momentum + 时序过滤
- 只保留横截面动量同向且时序动量同向的品种。

## 6. 代码模块设计

```text
cta/strategy/xs_momentum/
├── ranking.py          # 计算每品种的标准化动量
├── portfolio.py        # 构造多空组合
├── risk.py
├── run_backtest.py     # 组合级回测
└── config.yaml
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class XSPortfolioTarget:
    rebalance_date: pd.Timestamp
    long_symbols: list[str]
    short_symbols: list[str]
    weights: dict[str, float]        # symbol -> weight (signed)

def compute_xs_momentum(
    price_panel: pd.DataFrame,       # columns = symbols, index = date
    lookback: int = 60,
    vol_window: int = 60,
) -> pd.DataFrame:
    """返回 symbol -> standardized momentum per date"""
    ...

def build_xs_portfolio(
    momentum: pd.DataFrame,
    top_pct: float = 0.2,
    bot_pct: float = 0.2,
    gross_exposure: float = 1.0,
) -> list[XSPortfolioTarget]:
    """按 rebalance 日期构造 target 组合。"""
    ...
```

## 7. 回测评估重点

- **必看指标（8）**：总收益 / 年化 / MDD / Sharpe / Calmar / 多空对冲下的 beta / 换手率 / 单次调仓成本
- **分层评估**：按板块（黑色/有色/能化分组动量）、按年份、按 gross_exposure
- **稳健性检验**：
  1. lookback = 20/60/120 单调性
  2. top_pct = 10%/20%/30% 的 Sharpe 变化
  3. 对研究池的依赖（减少 2 个品种后稳定性）

## 8. 常见错误

- 未考虑各品种 contract multiplier → 权重计算错
- 未做波动率归一化 → 高波品种主导仓位
- 调仓换手过高 → 被成本吃掉
- 研究池太小（< 8 品种）→ 排名稳定性差

## 9. 迭代方向

- v1：简单 ret 排名
- v2：vol-adjusted + 板块中性
- v3：引入基本面因子（产量 / 库存）
- v4：与时序动量 combine

## 10. 与其他 Skills 的关系

- **依赖**：`00_overview_methodology/02_research_boundary.md`（研究池）、`07_position_and_portfolio/05_portfolio_allocation.md`
- **被依赖**：`07_position_and_portfolio/03_sector_exposure_control.md`
- **互补**：`03_trend_strategies/01_donchian_breakout.md`（时序动量 + 横截面动量 = ensemble）
