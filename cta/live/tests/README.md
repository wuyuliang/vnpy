# cta/live/tests/

## 主要做什么

[cta/live/](..) 实盘运行时各组件的单元测试。本目录所有测试**禁止**接真实 gateway 或网络，所有 IO 必须 mock。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_live_runner.py](test_live_runner.py) | 实盘主循环：行情订阅 → 特征 → 过滤 → 发单的端到端 mock |
| [test_supervisor.py](test_supervisor.py) | 守护进程的重启 / 心跳 / 告警 |
| [test_kill_switch.py](test_kill_switch.py) | **全局熔断器**：日亏损 / 数据延迟 / 连接断线触发；优先级最高的测试 |
| [test_risk.py](test_risk.py) | 实盘前置硬限额：单笔/日/周/月、并发持仓、品种黑名单 |
| [test_model_filter.py](test_model_filter.py) | 加载 cluster_registry + 三段模型 + score_calibrator 后对实时候选打分过滤 |
| [test_group_model_filter.py](test_group_model_filter.py) | group-pool / cluster 路由下的 model_filter |
| [test_online_feature.py](test_online_feature.py) | 在线特征与 [cta/feature/online.py](../../feature/online.py) 一致性 |
| [test_pnl_tracker.py](test_pnl_tracker.py) | 单笔成交回填 / 累计 PnL / 回撤 |
| [test_trade_recorder.py](test_trade_recorder.py) | 成交流水落盘与回放 |
| [test_daily_report.py](test_daily_report.py) | 收盘日报渲染 |
| [test_parity_helper.py](test_parity_helper.py) | live vs sim vs OOT 三方对账 |

## 注意事项

- **kill_switch / risk 不能挂**：这两个是实盘前最后兜底，挂了**禁止上线**。
- **禁止真网络**：vnpy gateway / Tushare / 任何 HTTP/TCP 都必须 mock。检查方法：在 test setUp 里临时 `socket.socket = MagicMock()` 验证没真连。
- **状态恢复测试**：进程重启后必须能从 trade_recorder 落盘记录里恢复 open positions + PnL anchor + kill_switch 状态，这条覆盖在 `test_live_runner.py::test_recovery_*`。
- **跑测试**：`pytest cta/live/tests/ -v`；正式上线前必跑全集且全绿。
