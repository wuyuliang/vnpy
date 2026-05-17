# cta/sim/

## 主要做什么

**仿真平台接入**：把研究阶段的策略 + 三段模型搬到仿真盘上跑（不发真实订单），逐日产出仿真成交、对比 OOT 期望、生成 parity 报告，作为实盘前的最后一道关。

## 关键文件

| 文件 | 作用 |
|---|---|
| [sim_runner.py](sim_runner.py) | 仿真主循环，类似 [cta/live/live_runner.py](../live/live_runner.py) 但 gateway 是仿真 |
| [parity_check.py](parity_check.py) | 仿真单笔 vs OOT 单笔逐笔对账：fill_price / fill_time / cost / pnl 偏差 |
| [daily_parity_report.py](daily_parity_report.py) | 每日仿真收盘后生成 parity_report.md，含 diff 明细与告警 |
| [tests/](tests/) | parity 与 runner 测试 |

## 详细过程

```
[启动]
    sim_runner.start()
        ├─ 加载 cluster_registry.json + 三段模型 + score_calibration
        ├─ 订阅仿真行情
        └─ 复用 cta/live/online_feature.py / cta/portfolio_logic/* / cta/model/oot_*
            （和实盘共用同一份决策代码）

[每笔成交]
    sim_runner.on_trade(trade)
        ├─ 落盘到 sim_trades.csv
        └─ parity_check.compare_with_oot_expected()
              ├─ 找到对应 OOT 候选（按 symbol/exchange/entry_time）
              ├─ diff fill_price / fill_time / cost / pnl
              └─ 累计到 parity_log

[收盘]
    daily_parity_report.generate()
        ├─ 当日仿真 PnL vs OOT 期望 PnL
        ├─ 累计 diff 趋势
        └─ 告警：连续 N 日 diff > 阈值
```

## 注意事项

- **sim 与 live 共用决策代码**：任何 fill / cost / 决策都不能"sim 一套、live 一套"。本目录只负责调度与对账，不重写决策。
- **OOT 期望是 ground truth**：仿真和 live 都必须以 OOT real-execution 的输出（`*_oot_trade_details.csv`）为对账基准。出现 diff 优先怀疑数据/时区/合约展期，最后再怀疑 OOT 本身。
- **状态恢复**：仿真进程重启后能从 `sim_trades.csv` 重建 open positions、parity log；不能丢历史 diff。
- **不发真单**：sim 用的 gateway 必须是 vnpy 的仿真接口（如 simnow），任何路径上"误打"到真实 broker 会被 [cta/live/kill_switch.py](../live/kill_switch.py) 兜底——但 sim 不应当依赖这个兜底，禁止 import live gateway。
- **测试**：跑 `pytest cta/sim/tests/ -v`；`test_parity_check.py` 必须绿。
- **切实盘前置条件**：连续 5 个交易日 parity_report 全部通过（diff < 0.5%），且无 critical 漂移。
