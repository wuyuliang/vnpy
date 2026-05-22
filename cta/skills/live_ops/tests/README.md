# cta/skills/live_ops/tests/

## 主要做什么

[cta/skills/live_ops/](..) 实盘运维组件的单元测试。所有 gateway / 告警通道必须 mock，禁止依赖网络。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_signal_to_order.py](test_signal_to_order.py) | signal → order 转换：滑点保护、限价/市价选择、拆单 |
| [test_order_execution.py](test_order_execution.py) | 下单执行：超时撤单、拒单重试、部分成交 |
| [test_monitoring_alerting.py](test_monitoring_alerting.py) | 监控告警：连接断线、数据延迟、风控触发、日亏损 |
| [test_daily_review.py](test_daily_review.py) | 收盘日复盘流程：成交回顾、PnL 拆解、与 OOT 对比 |
| [test_strategy_iteration_loop.py](test_strategy_iteration_loop.py) | 迭代闭环：日复盘反馈 → 参数调整建议 → 影子运行 |

## 注意事项

- **gateway / 告警 mock**：所有外部依赖必须 mock，包括 vnpy gateway、IM webhook、邮件 / 短信。
- **告警阈值改动**：测试要 lock 当前阈值，改阈值的 PR 必须同步更新这里的 expected。
- **跑测试**：`pytest cta/skills/live_ops/tests/ -v`。
