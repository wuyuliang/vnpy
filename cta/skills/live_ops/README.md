# cta/skills/live_ops/

## 主要做什么

**实盘运维方法论的代码化**：把"信号 → 下单"、"日复盘"、"监控告警"、"策略迭代回环"这些 ops 流程做成可调用模块，供 [cta/live/](../../live/) 调度。

注意区别：[cta/live/](../../live/) 是**实际跑实盘**的运行时；本目录是**运维流程组件**，研究/仿真也可以引用。

## 关键文件

| 文件 | 作用 |
|---|---|
| [signal_to_order.py](signal_to_order.py) | 信号 → 订单的转换规则：滑点保护、限价/市价选择、拆单 |
| [order_execution.py](order_execution.py) | 下单执行：超时撤单、拒单重试、部分成交处理 |
| [monitoring_alerting.py](monitoring_alerting.py) | 监控告警：连接断线、数据延迟、风控触发、日亏损 |
| [daily_review.py](daily_review.py) | 收盘日复盘流程：成交回顾、PnL 拆解、与 OOT 期望对比 |
| [strategy_iteration_loop.py](strategy_iteration_loop.py) | 策略迭代闭环：从日复盘反馈 → 参数调整 → 影子运行 → 上线 |

## 详细过程

```
[实盘主循环] cta/live/live_runner.py
    │
    │ signal 产生
    ▼
signal_to_order.transform(signal)         → 候选订单
    │
    ▼
order_execution.send(order)               → 真实下单（vnpy gateway）
    │
    ▼
monitoring_alerting.observe(trade, log)   → 异常检测 + 告警
    │
    ▼
[收盘后]
daily_review.run()                        → 当日 review.md
    │
    ▼
strategy_iteration_loop.collect_feedback() → 回到研究侧
```

## 注意事项

- **不直接连 gateway**：本目录代码应当通过依赖注入接收 gateway 实例，**禁止** import vnpy 具体 gateway 类，方便单测。
- **告警通道抽象**：[monitoring_alerting.py](monitoring_alerting.py) 应当通过 abstract sender 发送，避免硬编码 IM / 短信 webhook。
- **日复盘产物可机读**：[daily_review.py](daily_review.py) 输出 markdown + 同名 json，供下游聚合/对账消费。
- **策略迭代闭环不自动改 config**：[strategy_iteration_loop.py](strategy_iteration_loop.py) 只**建议**参数调整，最终落地必须人工 review。
- **测试**：`pytest cta/skills/live_ops/tests/ -v`；所有 gateway / 告警通道必须 mock。
