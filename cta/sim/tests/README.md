# cta/sim/tests/

## 主要做什么

[cta/sim/](..) 仿真平台接入的单元测试，重点验证仿真与 OOT 的逐笔对账逻辑。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_sim_runner.py](test_sim_runner.py) | 仿真主循环：行情订阅 → 特征 → 过滤 → 仿真下单的端到端 mock |
| [test_parity_check.py](test_parity_check.py) | **仿真单笔 vs OOT 期望单笔的 diff 计算**：fill_price / fill_time / cost / pnl，是切实盘前的关键关卡 |
| [test_daily_parity_report.py](test_daily_parity_report.py) | 收盘 parity 日报生成与告警阈值 |

## 注意事项

- **不调真 gateway**：所有测试必须 mock 仿真接口，禁止依赖网络。
- **`test_parity_check.py` 必须绿**：挂了说明仿真与 OOT 对账逻辑断了，不能切实盘。
- **跑测试**：`pytest cta/sim/tests/ -v`。
- **切实盘前置 checklist**：见 [../README.md](../README.md) 末段，连续 5 个交易日 parity_report 通过 + 本目录全绿。
