# {Skill 中文名} / {english_name}

> 归属章节：`NN_xxx/` · 前置技能：`YY_yyy/ZZ_zzz.md` · 关联策略：`cta/strategy/...`

## 1. Skill 定义

一句话定义。

2-3 句背景：为什么这个 skill 独立存在、它在技能树中的位置、属于「识别 / 策略 / 风控 / 基础设施 / 运维」哪一类。

## 2. 解决什么问题

- 痛点 1：...
- 痛点 2：...
- 痛点 3：...
- 与不做这件事相比的增量价值：...

## 3. 适用市场 / 适用场景

- **品种类别**：黑色 / 有色 / 能化 / 农产品 / 贵金属（写明是哪一类或哪几类）
- **周期**：day / minute60 / minute30 / minute15 / minute5 / minute
- **行情状态前提**：趋势 / 震荡 / 压缩 / 扩张 / 过渡 / 任意
- **不适用场景**：明确写出在什么条件下应当关闭这个 skill

## 4. 核心指标

> 必须 ≥ 3 行，每行含参数建议 + 典型阈值 + 现有实现引用（若未实现写 `TODO`）。

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| ATR(N) | `mean(TR, N)` | N=14/20 | — | `cta/feature/price_action.py::pa_atr_*` |
| XXX    | ...      | ...      | ...      | ...      |
| YYY    | ...      | ...      | ...      | TODO: 待实现 |

## 5. 常见策略映射

> 至少 1 条完整策略（含信号/开仓/止损/平仓/仓位），越多越好。

### 策略 A：{name}
- **信号定义**：公式或条件，用核心指标表中的名字。
- **开仓触发**：bar 何时成立？下单方式（市价/限价/stop）？
- **止损规则**：ATR 倍数、结构位、时间止损。
- **平仓规则**：目标价、跟踪止损、失败快退。
- **仓位规模**：单笔风险 % × 账户净值 / 止损距离（参考 `07_position_and_portfolio/01_single_trade_risk.md`）。
- **失败模式**：什么行情会杀这个策略。

### 策略 B：{name}（可选）
...

## 6. 代码模块设计

> 文件树 + ≥ 2 个带类型注解的函数签名。

```text
cta/strategy/{name}/
├── __init__.py
├── signals.py          # 负责 ...
├── filters.py          # 负责 ...
├── risk.py             # 负责 ...
├── run_backtest.py     # 入口
└── config.yaml         # 参数
```

```python
# cta/strategy/{name}/signals.py
from typing import Optional
import pandas as pd

def detect_setup(
    df: pd.DataFrame,
    atr_n: int = 14,
    squeeze_ratio: float = 0.6,
) -> pd.Series:
    """
    返回 bool Series：True = 本 bar 成立 setup。
    要求 df 已含 pa_* 特征（由 cta/feature/feature_loader.py 读取）。
    """
    ...

def decide_entry(
    setup_bar: pd.Series,
    next_bar: pd.Series,
) -> Optional[dict]:
    """
    根据 setup 决定是否开仓、方向、价位。
    返回 {"side": "long"|"short", "price": float, "stop": float} 或 None。
    """
    ...
```

## 7. 回测评估重点

- **必看指标（≥6 个）**：总收益 / 年化 / 最大回撤 / Sharpe / Calmar / 胜率 / 盈亏比 / 平均持仓 bar 数。
- **分层评估**：按品种 / 按年份 / 按 regime / 按入场 setup 类型。
- **稳健性检验（≥3 种）**：参数敏感性（±20%）、样本外（walk-forward）、交易成本敏感性（滑点 ×2）。
- **诊断图**：资金曲线、回撤曲线、MFE/MAE 散点、每年收益柱状图。

## 8. 常见错误

- 数据对齐错误（未来函数、含当前 bar 收盘价）。
- 过度拟合（参数过多 / 无样本外 / 在一个品种调完就上多品种）。
- 信号与执行不一致（回测 close 成交、实盘滑点）。
- 忽略交易时段 / 涨跌停 / 主力切换。

## 9. 迭代方向

- **v1 → v2 → v3**：从规则骨架 → 加过滤 → 加 ML 门控。
- **ML 增强点**：哪些特征值得进模型、模型输出如何参与决策（score 过滤 / size 调节）。
- **多品种扩展**：如何从单品种调参迁到多品种组合。

## 10. 与其他 Skills 的关系

> ≥ 3 条双向链接。

- **依赖**：`01_market_regime/XX.md`（需要先识别状态）
- **被依赖**：`06_filtering_and_scoring/XX.md`（打分会引用本 skill 的指标）
- **互补**：`07_position_and_portfolio/XX.md`（仓位规模由它决定）
- **替代**：`04_range_strategies/XX.md`（不同行情下本 skill 的反例）
