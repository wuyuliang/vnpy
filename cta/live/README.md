# cta/live/

## 主要做什么

**实盘运行时**：行情接入 → 在线特征 → 模型推理过滤 → 下单 → 风控 → PnL 跟踪 → 日报。和 [cta/sim/](../sim/) 仿真共用绝大部分代码，关键差异只在"下单走真实 gateway"。

## 关键文件

| 文件 | 作用 |
|---|---|
| [live_runner.py](live_runner.py) | 实盘主循环：订阅行情、定时拉特征、调模型、发单 |
| [supervisor.py](supervisor.py) | 守护进程：监控 live_runner 健康、自动重启、连接断线告警 |
| [online_feature.py](online_feature.py) | 在线增量特征：复用 [cta/feature/online.py](../feature/online.py) 的实时 bar 拼接，落到内存窗口供模型用 |
| [model_registry.py](model_registry.py) | 在线模型注册表（cluster_registry 热加载包装 + 模型时效信息） |
| [signal_generator.py](signal_generator.py) | 实时信号入口：候选扩展 + 特征拼接 + 模型打分 + OOT 同款 gate 决策 |
| [model_filter.py](model_filter.py) | 加载 cluster_registry.json + 三段模型 + score_calibration，对实时候选打分过滤 |
| [order_slicer.py](order_slicer.py) | 下单拆单（iceberg/twap + ADV 参与率上限） |
| [preopen_checklist.py](preopen_checklist.py) | 盘前 go/no-go 检查（预测新鲜度、模型时效、kill switch、保证金） |
| [risk.py](risk.py) | 实盘风控：单笔/日/周/月限额、最大并发持仓、品种黑名单 |
| [kill_switch.py](kill_switch.py) | 全局熔断：超过日亏损/连接异常/数据延迟阈值时强制停手 |
| [pnl_tracker.py](pnl_tracker.py) | 每笔成交回填，逐笔 PnL / 累计权益 / 回撤 |
| [trade_recorder.py](trade_recorder.py) | 成交流水落盘，是 `parity_helper` 的 diff 来源 |
| [trade_logger.py](trade_logger.py) | OOT 风格逐笔结构化日志（blocked / filled / 概率与分位） |
| [daily_report.py](daily_report.py) | 收盘后日报：当日成交、PnL、与 OOT 预期偏差 |
| [reporting/periodic_report.py](reporting/periodic_report.py) | live 周/月/summary 聚合（口径复用 OOT aggregate） |
| [reporting/review_report.py](reporting/review_report.py) | live vs OOT 复盘归因（signal/execution/risk 三段） |
| [reporting/build_reports.py](reporting/build_reports.py) | 从 CSV 一键生成 periodic + review 报告 |
| [parity_helper.py](parity_helper.py) | 实盘 vs 仿真 vs OOT 三方对账：定位 fill / cost / decision 漂移 |

## 详细过程

```
[启动]   supervisor.start_live_runner()
            │
            ▼
[初始化]  live_runner
            ├─ 加载 cluster_registry.json + 模型
            ├─ 订阅行情（vnpy gateway）
            ├─ kill_switch.preflight_check()  # 资金、连接、数据延迟
            └─ 启动 online_feature 实时窗口
            │
            ▼
[每根新 bar]
   1. online_feature.on_new_bar() → 拼出当前可见的特征向量
   2. baseline_strategy 在该 bar 上扫描候选事件
   3. LegacyCtaAdapter `_dispatch_order`：
      - signal_generator_context 补模型分数
      - entry_gate_chain（trade_filter/HTF/risk_orchestrator）
      - order_filter（RiskGuard/kill_switch）
      - order_slicer（可选）
      - send_order
   4. 成交回报 → pnl_tracker.on_trade() + trade_recorder.append() + trade_logger.record_fill()
   5. 持仓阶段 position_evaluator（hard stop）触发强平（forced_exit）
            │
            ▼
[收盘]   daily_report.generate()
         parity_helper.compare_with_sim_and_oot()
         reporting.build_reports（周/月/复盘归因）
```

## 注意事项

- **online vs offline 一致性硬约束**：特征/过滤/sizing 任何代码不能"实盘和回测分两套"。一切都先在 [cta/sim/](../sim/) 仿真验证一致性后再切实盘。`parity_helper` 每日检 diff，连续两天 diff > 阈值要求停手排查。
- **kill_switch 优先级最高**：任何路径上"发单前"必须先过 [kill_switch.py](kill_switch.py)，不允许绕过。改它要 review。
- **risk vs portfolio_logic 的区别**：
  - [risk.py](risk.py) 是**实盘前置硬限额**（送单前），写法直接、原子。
  - [cta/portfolio_logic/](../portfolio_logic/) 是**回测期决策与口径**（htf gate、ranker、trailing），不直接发单。
- **状态恢复**：进程重启后必须能从 [trade_recorder.py](trade_recorder.py) 落盘记录里恢复 open positions / PnL anchor / kill_switch 状态。
- **日志与告警**：所有 ERROR 级日志都要触发告警通道（短信/IM）；INFO 级保留 30 天用于事后对账。
- **不允许硬编码账户/Token**：连接信息走 vnpy gateway 配置 + 环境变量。
- **测试**：跑 `pytest cta/live/tests/ -v`。重点：`test_kill_switch.py` / `test_risk.py` / `test_parity_helper.py` 三个不能挂。
- **真正切实盘前的 checklist**：
  1. `cta/sim/` 上跑过等价回测，PnL diff < 0.5%
  2. `parity_helper` 在 paper trading 上连续 5 天对账通过
  3. `kill_switch` 阈值 review 一遍（日亏损 / 数据延迟 / 连接断线）
  4. 单笔最大仓位规模在 [risk.py](risk.py) 里**额外**设硬上限（兜底 portfolio_logic）
