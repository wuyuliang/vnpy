# 监控与告警 / Monitoring & Alerting

> 归属章节：`10_live_ops/` · 前置：`02_order_execution.md` · 关联：`cta/report/`

## 1. Skill 定义

定义**实盘关键指标**（equity / 持仓 / 订单状态 / 风险等级 / 数据新鲜度）并设置**分级告警**（info / warn / critical）。让人能"离开屏幕"。

## 2. 解决什么问题

- 痛点 1：盯盘不可持续。
- 痛点 2：出了事才知道 → 损失已扩大。
- 痛点 3：告警太多 → 疲劳无视。
- 增量价值：把"有人盯盘"变成"规则盯盘 + 人响应告警"。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：实盘
- **行情状态前提**：任何
- **不适用场景**：研究 / 回测

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| equity_drawdown | (peak - now) / peak | 实时 | warn ≥ 3% / critical ≥ 5% | TODO |
| position_delta | 本地 - 券商 | 每 5 分钟 | critical ≥ 1 lot | TODO |
| data_freshness | 最新 bar 延迟 | — | warn > 2min / critical > 5min | TODO |
| order_reject_rate_5m | 滚动 5 分钟 | — | warn ≥ 2% | TODO |
| strategy_heartbeat | 进程健康 | 30s | miss = critical | TODO |

## 5. 常见策略映射

### 分级
- **info**：策略开仓 / 平仓 / 日终 PnL
- **warn**：回撤进入 3% / 挂单被拒一次 / 数据 2 分钟未更新
- **critical**：仓位对账失败 / heartbeat 丢失 / 回撤 > 5% / kill_switch 触发

### 通道
- **info**：日志文件
- **warn**：企业微信 / Telegram
- **critical**：企业微信 + 电话（机器人）

### 抑制规则
- 相同 warn 5 分钟内只告一次
- critical 必须独立一条，不做抑制

## 6. 代码模块设计

```text
cta/strategy/common/live/monitor/
├── metrics.py            # 指标收集
├── rules.py              # 阈值 / 分级
├── notifier.py           # 通道适配
└── heartbeat.py          # 进程活性
```

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd

Level = Literal['info','warn','critical']

@dataclass
class Alert:
    ts: pd.Timestamp
    level: Level
    code: str
    msg: str
    payload: dict

def check_rules(state: dict) -> list[Alert]:
    ...

def notify(alert: Alert, channels: list[str]) -> None:
    ...

def heartbeat_loop(interval_sec: int, on_miss) -> None:
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：告警触达率 / 重复告警比例 / 从发生到送达延迟 / 假阳性率 / critical 响应时长 / 未覆盖事件事后补（后续补规则数）
- **分层评估**：按 level / 按 code
- **稳健性检验**：
  1. 人为断网 → data_freshness 告警时长
  2. 强制 reject 几单 → warn 触发
  3. kill_switch → critical 必达

## 8. 常见错误

- warn 也用电话通道 → 疲劳 + 被屏蔽
- 时间戳用本地时区 → 跨市场时乱
- 没有心跳 → 进程悄悄死了
- critical 阈值过紧 → 天天触发

## 9. 迭代方向

- v1：核心指标 + 企微 / Telegram
- v2：prometheus / grafana 仪表盘
- v3：异常检测（基线 + 偏离）
- v4：智能聚合（同根因告警合并）

## 10. 与其他 Skills 的关系

- **依赖**：`10_live_ops/02_order_execution.md`
- **被依赖**：`10_live_ops/04_daily_review.md`
- **互补**：`07_position_and_portfolio/04_drawdown_control.md`（DD 升级触发告警）
