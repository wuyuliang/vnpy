# 每日复盘 / Daily Review

> 归属章节：`10_live_ops/` · 前置：`03_monitoring_and_alerting.md` · 关联：`cta/report/`、`cta/strategy/brooks/backtest/reporter.py`

## 1. Skill 定义

每日收盘后产出**标准化复盘报告**：今日订单 / 成交 / PnL / 风险等级 / 实盘 vs 回测 diff / 待确认项。强制每日 15 分钟复盘，让策略不失控。

## 2. 解决什么问题

- 痛点 1：没有复盘 → 小问题累积成大问题。
- 痛点 2：复盘内容因人而异 → 无法对比。
- 痛点 3：问题找不到根因 → 重复发生。
- 增量价值：每日都能回答"今天策略做得对不对"。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：实盘
- **行情状态前提**：任何
- **不适用场景**：纯研究阶段

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| daily_pnl | 今日 equity - 昨日 equity | — | — | TODO |
| live_vs_bt_diff | 实盘 PnL - 同日回测 PnL | — | warn ≥ 20% | TODO |
| slippage_realized | 成交价 - 信号价 | 每笔 | 平均 ≤ 1.5 tick | TODO |
| reject_count | 今日被拒单 | — | 0 为优 | TODO |
| open_issues | 未关闭问题数 | — | — | TODO |

## 5. 常见策略映射

### 报告结构（markdown）
```text
## 今日摘要
- equity / 日 PnL / 本月累计 / 回撤
- 告警总数 / 未处理告警

## 成交明细
- trade_log（今日）
- 每笔 slippage / MFE / MAE

## 实盘 vs 回测
- 今日回测 PnL 对照
- 差异 > 阈值的交易逐笔解释

## 风险
- DD level / sector exposure / vol target

## 问题与 TODO
- [ ] 开盘价跳空止损被跳 → 是否加入 gap gate
- [ ] RB 挂单重挂 3 次才成 → 调挂单价格逻辑
```

### 强制项
- 必须有"与回测的差异"板块
- 必须有"待办 + 负责人 + 期限"

## 6. 代码模块设计

```text
cta/strategy/common/live/review/
├── collect.py            # 收集当日数据
├── compare_bt.py         # 实盘 vs 回测对比
├── render.py             # markdown 渲染
└── issue_tracker.py      # TODO 列表
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class ReviewConfig:
    out_dir: str = 'cta/report/daily'
    bt_pnl_loader = None
    include_charts: bool = True

def build_daily_review(
    date: pd.Timestamp,
    trade_log_today: pd.DataFrame,
    equity_today: pd.Series,
    alerts_today: pd.DataFrame,
    cfg: ReviewConfig,
) -> str:
    """返回报告路径。"""
    ...

def compare_live_vs_backtest(
    live_trades: pd.DataFrame,
    bt_trades: pd.DataFrame,
) -> pd.DataFrame:
    """返回逐笔对比表。"""
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：每周平均 live_vs_bt diff / open_issues 趋势 / slippage 均值趋势 / DD level 分布 / 人工决策次数 / 策略改动频率
- **分层评估**：按策略 / 按品种 / 按周
- **稳健性检验**：不适用（流程）

## 8. 常见错误

- 只看赚钱 / 亏钱 → 忽略过程异常
- 没对比回测 → 假定实盘正常
- TODO 不跟踪 → 事后忘
- 数据定义漂移（今日 PnL 是净 or 毛）

## 9. 迭代方向

- v1：每日 md 报告
- v2：周报 / 月报（汇总 + 调参建议）
- v3：模型辅助（LLM 总结交易关键点）
- v4：策略健康分（自动评级）

## 10. 与其他 Skills 的关系

- **依赖**：`10_live_ops/03_monitoring_and_alerting.md`、`08_data_and_backtest_infra/05_trade_log_and_evaluation.md`
- **被依赖**：`10_live_ops/05_strategy_iteration_loop.md`
- **互补**：`00_overview_methodology/05_iteration_workflow.md`
