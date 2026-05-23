# 仿真 / 实盘接入路线图（sim_live_integration_roadmap）

> 范围：`cta/sim/`、`cta/live/`、`cta/portfolio_logic/`、`cta/strategy/`、`cta/feature/`
> 用途：从"OOT 跑通 → sim 验证 → 实盘灰度"的完整路线，作为后续 P0/P1 任务的总 spec。
> 风格：与 [ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)、
> [profit_aware_trend_adaptive_design.md](./profit_aware_trend_adaptive_design.md) 对齐。
> **本文档面向 codex / claude 实施**，每个任务都有验收门槛 + 接口契约。

---

## §0 修订记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-05-23 | claude | 初稿。把当前 sim/live 盘点 + 27 项 P0/P1/P2/P3 任务清单固化。 |

---

## §1 当前状态盘点

### §1.1 现有代码资产（2026-05-23）

| 模块 | 文件数 | 行数 | 状态 |
|---|---|---|---|
| [cta/sim/](../sim/) | 4 Py + tests | 780 | sim_runner 已通 vnpy_ctp 启动链 |
| [cta/live/](../live/) | 10 Py + tests | 1,475 | 10 组件骨架齐全；缺业务接线 |
| sim 测试 | 3 文件 | — | parity / runner / daily_report |
| live 测试 | 11 文件 | — | pnl / risk / runner / supervisor / kill_switch / parity_helper 等 |

唯一 `NotImplementedError`：[cta/live/risk.py:57](../live/risk.py)（抽象基类签名，非真缺失）。

### §1.2 架构已就绪

```
sim_runner.start()
  ├─ EventEngine + MainEngine + CtpGateway + CtaStrategyApp（vnpy_ctp 启动链）
  ├─ load cluster_registry.json + 三段模型 + score_calibration
  ├─ 复用 cta/live/online_feature.py（实时 bar 增量拼接）
  ├─ 复用 cta/portfolio_logic/*（cap / kill_switch / risk_throttle）
  └─ 复用 cta/model/oot/*（gate 链路）

live_runner.start()
  └─ 与 sim 共用绝大部分代码，唯一差异是 gateway 类型
```

### §1.3 已落地但**未 wire 到 sim/live** 的新特性

以下 7 个特性都是"OOT-only"，CLAUDE.local.md 明确要求"离线/仿真/实盘逻辑一致"，
必须同步 wire 才能上 sim：

| # | 特性 | OOT 落地位置 | wire 状态 |
|---|---|---|---|
| F1 | `cross_sectional_momentum_rotation` | [strategy/cross_sectional_momentum_rotation.py](../strategy/cross_sectional_momentum_rotation.py) | OOT only |
| F2 | `trailing_take_profit` | [config/trailing_take_profit_config.py](../config/trailing_take_profit_config.py) | OOT only |
| F3 | `profit_aware_horizon` | [config/profit_aware_horizon_config.py](../config/profit_aware_horizon_config.py) | OOT only |
| F4 | `ma_cross_gate` / `regime_short_filter` | [model/oot/oot_gates.py](../model/oot/oot_gates.py) | OOT only |
| F5 | `trend_aware_trade_filter` | [model/oot/oot_trade_filter_gate.py](../model/oot/oot_trade_filter_gate.py) | OOT only |
| F6 | `trade_filter_bypass_signal_types` | 同上 | OOT only |
| F7 | `intrabar_stop_loss_pct_by_cluster_interval` | [model/oot/pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | OOT only |

---

## §2 工作清单（按优先级 P0→P3）

### §2.1 P0 — 不做就跑不起来（6 项）

#### P0-1：vnpy CtaTemplate 策略 wrapper

**目标**：把 [strategy/baseline_setup_detection.py](../strategy/baseline_setup_detection.py) 输出的
candidate 包装成 vnpy `CtaTemplate` 子类，让 sim/live 主循环可以接管下单。

| 维度 | 内容 |
|---|---|
| 新增文件 | `cta/strategy/vnpy_adapters/baseline_strategy.py` |
| 关键类 | `BaselineSetupVnpyStrategy(CtaTemplate)` |
| 接口契约 | `on_bar(bar)` → 调用 baseline_setup_detection → emit order; `on_trade(trade)` → 更新 PnLTracker |
| 测试 | `cta/strategy/tests/test_baseline_strategy_vnpy.py`（mock CtaTemplate）|
| 验收 | sim_runner 启动后能加载 1 个 strategy 实例 + 收到 mock bar + 发出 LimitOrder |
| 工作量 | 1 天 |

#### P0-2：online_feature 实时 vs 离线 parity

**目标**：[live/online_feature.py](../live/online_feature.py) 计算的实时 feature 与
[feature/](../feature/) 离线 batch feature **在同一 bar 上数值一致**（允许浮点误差 < 1e-6）。

| 维度 | 内容 |
|---|---|
| 关键文件 | [live/online_feature.py](../live/online_feature.py) + [feature/online.py](../feature/online.py)（如存在） |
| 新增测试 | `cta/live/tests/test_online_feature_parity.py` |
| 接口契约 | `compare_online_vs_offline(symbol, date_range, interval) -> ParityReport` |
| 验收指标 | 1 个 symbol × 1 天 × 60min 上 100+ feature 列 max\|diff\| < 1e-6；rolling window 边界点（前 N bar）允许 NaN |
| 风险 | timezone 偏差、rolling 初始化点不一致、NaN 处理不同 |
| 工作量 | 4-6 小时 |

#### P0-3：模型热加载 + cluster_registry 校验

**目标**：sim/live 启动时正确加载 [cluster_registry.json](#) 指向的所有模型，
hot-reload 失败时 gracefully degrade 到上一版本。

| 维度 | 内容 |
|---|---|
| 关键文件 | [live/model_filter.py](../live/model_filter.py) + [model/training/cluster_model_registry.py](../model/training/cluster_model_registry.py) |
| 新增功能 | (1) 启动前 schema 校验; (2) 热加载 hook（SIGHUP 触发）; (3) version 回滚 |
| 测试 | `cta/live/tests/test_model_registry_hot_reload.py` |
| 验收 | 启动时缺失模型文件 → 显式 raise（不能静默 fallback）；启动后 model 文件替换 + SIGHUP → 切换成功；切换失败 → 保留旧模型 |
| 工作量 | 1 天 |

#### P0-4：SimNow / CTP 凭据配置

**目标**：把账号/密码/broker_id/服务器地址从代码硬编码迁移到 **.gitignore 配置文件**。

| 维度 | 内容 |
|---|---|
| 新增文件 | `cta/config/sim_credentials.py.template`（入库）+ `cta/config/sim_credentials.py`（**.gitignore**） |
| schema | `@dataclass(frozen=True) class SimnowCredentials: userid, password, broker_id, td_address, md_address, app_id, auth_code` |
| 测试 | `cta/config/tests/test_sim_credentials.py`（仅校验 template 结构）|
| 验收 | `git status` 不能看到 `sim_credentials.py`；sim_runner 启动时 fail-fast 提示该文件缺失 |
| 安全 | **绝不入库**；只入 `.template`；CI 应 grep 防回归 |
| 工作量 | 2 小时 |

#### P0-5：连续合约 → 主力合约映射 + 换月

**目标**：sim/live 下单时把研究阶段的 `RB0` 映射到当前真实主力合约（如 `RB2501`），
并在换月日处理切换。

| 维度 | 内容 |
|---|---|
| 新增文件 | `cta/portfolio_logic/contract_resolver.py` |
| 关键函数 | `resolve_active_contract(continuous_symbol, as_of_date) -> str`、`is_rollover_day(symbol, date) -> bool` |
| 依赖 | [data_code/futures_downloader.py](../data_code/futures_downloader.py) 的主力切换日历，或 vnpy `MainEngine.get_all_contracts()` |
| 测试 | `cta/portfolio_logic/tests/test_contract_resolver.py` |
| 验收 | (1) 给定 2024-12-15 RB0 返回 RB2501（最近主力）; (2) 换月日不发新单 + 平掉旧主力 + 开新主力 |
| 工作量 | 1 天 |

#### P0-6：vnpy_ctp 真实环境装机 + 网络验证

**目标**：在目标机器上 `pip install vnpy_ctp` 通过，能 `connect` 到 SimNow 测试服务器
并订阅 1 个合约行情。

| 维度 | 内容 |
|---|---|
| 文档 | `cta/sim/README.md` 加 "环境装机" 章节（OS / Python 版本 / vnpy_ctp 版本 / SimNow 申请） |
| 可选 | Docker 镜像（基础环境 + vnpy_ctp + python deps） |
| 验收 | `python -m cta.sim.sim_runner --smoke` 启动 → 连接成功 → 收到至少 10 个 tick |
| 工作量 | 半天（不含 SimNow 账号申请等待时间） |

### §2.2 P1 — 已落地新特性的 sim/live 接入（7 项）

**核心要求**：每个特性必须读**同一份** cfg（`OotEvaluationConfig` / `PortfolioLogicConfig`），
**绝不允许** sim/live 单独维护副本。每个特性单独 wire + 单独 parity 验证。

#### P1-7：cross_sectional_momentum_rotation

| 维度 | 内容 |
|---|---|
| OOT 已有 | [CrossSectionalRotationExecutor](../portfolio_logic/cross_sectional_rotation_executor.py)（独立类，输出 `RotationOrderIntent`）|
| sim wire | sim_runner 主循环增加 daily/weekly tick → 调 executor.step() → 把 RotationOrderIntent 转 vnpy 下单 |
| 难点 | RotationOrderIntent 是抽象 intent，需要 adapter 转 vnpy `OrderRequest` |
| 测试 | `cta/sim/tests/test_cross_sectional_rotation_sim.py` |
| 验收 | sim 模式跑 1 周，rotation 候选 → executed 通过率 ≥ OOT 的 80% |
| 工作量 | 1.5 天 |

#### P1-8：trailing_take_profit

| 维度 | 内容 |
|---|---|
| OOT 已有 | [trailing_take_profit.py](../portfolio_logic/trailing_take_profit.py)（codex 实现） |
| sim wire | sim 持仓评估器在每 bar 上构造 `PositionTrendState` → 调 `evaluate_trailing_take_profit` → 触发即发市价平仓 |
| 难点 | highwater_price 需 sim 自己维护（OOT 是事后回放）；订单失败处理 |
| 测试 | `cta/sim/tests/test_trailing_tp_sim.py` |
| 验收 | sim 5 天，trailing TP 触发率与 OOT diff < 20%；触发后必平仓（不允许 reject 后无重试） |
| 工作量 | 1 天 |

#### P1-9：profit_aware_horizon

| 维度 | 内容 |
|---|---|
| OOT 已有 | [profit_aware_horizon.py](../portfolio_logic/profit_aware_horizon.py)（codex 实现） |
| sim wire | sim 持仓评估器的 horizon 计算从 base 改为调用 `resolve_max_holding_bars(state, interval, cfg)` |
| 测试 | `cta/sim/tests/test_profit_aware_horizon_sim.py` |
| 验收 | sim 持仓平均时长 vs OOT 一致（±5%）；浮盈 ≥ 5% 时不被 base horizon 强平 |
| 工作量 | 半天 |

#### P1-10：ma_cross_gate + regime_short_filter

| 维度 | 内容 |
|---|---|
| OOT 已有 | [oot_gates.py](../model/oot/oot_gates.py) 的 `apply_ma_cross_gate` / `apply_regime_short_filter` |
| sim wire | sim 入场前 gate 链增加这两个 gate，按 cfg 配置启用 |
| 难点 | sim 需要在每个 bar 计算 `ma_alignment` / `regime_label` 实时值（与 online_feature 拼接） |
| 测试 | `cta/sim/tests/test_oot_gates_sim.py` |
| 验收 | sim 拦截率 vs OOT diff < 5pp |
| 工作量 | 1 天 |

#### P1-11：trend_aware_trade_filter

| 维度 | 内容 |
|---|---|
| OOT 已有 | [oot_trade_filter_gate.py](../model/oot/oot_trade_filter_gate.py) 的 trend-aware delta |
| sim wire | sim trade_filter gate 共享同一个函数（已 OOT/sim 通用，应该零改） |
| 难点 | 验证 `ma_alignment` / `regime_label` / `realized_vol_rank` 在 sim 实时计算与 OOT 一致 |
| 测试 | `cta/sim/tests/test_trend_aware_trade_filter_sim.py` |
| 验收 | sim 阈值松绑触发频率 vs OOT 一致（±10%） |
| 工作量 | 半天 |

#### P1-12：trade_filter_bypass_signal_types

| 维度 | 内容 |
|---|---|
| OOT 已有 | [oot_trade_filter_gate.py](../model/oot/oot_trade_filter_gate.py) bypass 逻辑 |
| sim wire | sim trade_filter gate 复用同一函数（零改） |
| 测试 | `cta/sim/tests/test_trade_filter_bypass_sim.py` |
| 验收 | sim 中标记 bypass 的 signal_type 100% 通过 trade_filter |
| 工作量 | 2 小时 |

#### P1-13：intrabar_stop_loss_pct_by_cluster_interval

| 维度 | 内容 |
|---|---|
| OOT 已有 | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) intrabar 路径 |
| sim wire | sim 止损路径按 cfg 字段读 cluster-specific stop_loss_pct |
| 难点 | sim 是事件驱动，没有"intrabar 回放"概念；需用 tick 精度逼近 |
| 测试 | `cta/sim/tests/test_intrabar_stop_sim.py` |
| 验收 | sim hard_stop 触发价 vs OOT diff < 1 tick |
| 工作量 | 1 天 |

### §2.3 P2 — 跑通前必须的测试覆盖（7 项）

| # | 任务 | 关键文件 | 验收 |
|---|---|---|---|
| P2-14 | **5-day sim parity smoke** | [sim/parity_check.py](../sim/parity_check.py) | sim trades vs OOT 期望，每笔 diff ≤ 1bp，累计 PnL diff ≤ 10bp |
| P2-15 | 断线重连 chaos test | [supervisor.py](../live/supervisor.py) | 模拟 gateway 30s 断线 + reconnect → 不丢单 / 不重发 |
| P2-16 | kill_switch 端到端 | [kill_switch.py](../live/kill_switch.py) | 日亏损 > 3%、持仓异常、数据延迟 > 5s 三场景全覆盖 + 触发后人工恢复 |
| P2-17 | partial fill / reject / 撤单 | 新增 `cta/live/tests/test_order_lifecycle.py` | CTP 单据 6 种状态（submit / queued / part_traded / all_traded / rejected / canceled）全覆盖 |
| P2-18 | 夜盘 / 节假日 / 涨跌停 | 新增 `cta/sim/tests/test_trading_calendar.py` | 商品 night session / 春节假期 / RB 涨跌停板 边界 |
| P2-19 | 保证金 / 资金占用 | [pnl_tracker.py](../live/pnl_tracker.py) | CTP `query_account` 返回 vs 本地估算 diff ≤ 1% |
| P2-20 | timezone 一致性 | docstring + 测试 | UTC vs Asia/Shanghai vs vnpy bar timestamp 全链路对账 |

### §2.4 P3 — 运维 / 上线流程（7 项）

| # | 任务 | 内容 |
|---|---|---|
| P3-21 | 监控告警 | Prometheus exporter + 钉钉/微信 webhook（kill_switch / daily_pnl / parity diff）|
| P3-22 | 结构化日志 + rotation | `cta/utils/logging_config.py`，json 格式 / 按日 rotate |
| P3-23 | **30-day sim soak run** | 连续跑 30 个交易日，weekly parity report 无异常 → 上实盘门槛 |
| P3-24 | 实盘灰度计划 | 1 个 symbol → 1 个 cluster → 全 universe，每阶段 2-4 周 |
| P3-25 | 小额实盘验证 | 5-10 万元 × 3-5 个交易日，对账误差 ≤ 0.5% |
| P3-26 | 复盘自动化 | [daily_report.py](../live/daily_report.py) 扩展归档 + 异常摘要邮件 |
| P3-27 | 回退策略文档 | sim 出现 PnL/parity 异常时如何回滚到上一版本（cluster_registry.json + 数据库 snapshot） |

---

## §3 关键风险与不变量

### §3.1 风险矩阵

| # | 风险 | 等级 | 应对 |
|---|---|---|---|
| R1 | **OOT / sim / live cfg 漂移** | 🔴 高 | 同一份 cfg 对象贯穿三层；CI 加 grep 防独立副本 |
| R2 | **online_feature vs 离线计算不一致** | 🔴 高 | P0-2 parity 测试是硬门槛；max\|diff\| < 1e-6 不通过不上 sim |
| R3 | **连续合约换月跳价** | 🟡 中 | P0-5 contract_resolver + rollover_check；换月日不发新单 |
| R4 | **OOT 看到的 trade 在 sim 被 reject** | 🟡 中 | P2-17 partial fill + reject 测试；保证金/涨跌停/流动性约束 |
| R5 | **5+ P1 新特性堆叠 effect 不可控** | 🟡 中 | P1 每项**独立 wire + 独立 parity**，不一次性全开 |
| R6 | **regime 切换时模型表现差异** | 🟡 中 | 参考 [oot_20260523_034107](../report/backtest/oot_20260523_034107_cluster_both/) 金银 12.7% 通过率案例；sim 期间需逐 regime 监控 |
| R7 | **CTP gateway 异常 / 断线** | 🟢 低 | P2-15 chaos test + P3-21 监控告警 |
| R8 | **凭据泄漏** | 🔴 高 | P0-4 sim_credentials.py 不入库 + CI grep |

### §3.2 不变量（执行时不可破坏）

1. **三层 cfg 一致**：OOT / sim / live 必须读同一个 cfg 实例（或同一来源），禁止重复定义。
2. **默认 off**：所有新特性默认 `use_*=False`，sim/live 不会自动启用任何 OOT 中默认 off 的功能。
3. **parity 是硬门槛**：P0-2（feature parity）+ P2-14（trade parity）任一不通过不上 sim soak。
4. **凭据不入库**：sim_credentials.py 永远在 .gitignore；CI 必须 grep `password|apikey|broker_id` 防回归。
5. **kill_switch 优先级最高**：任何模块的 exception 不能绕过 kill_switch。
6. **TDD**：每个 P0/P1 任务都先写测试再写实现。
7. **文档先行**：本文是 spec；实施过程中接口变更需先更新 §0 修订记录。

---

## §4 推荐落地顺序

```
Week 1-2:  P0 #1-3（策略 wrapper + online_feature parity + 模型加载）
Week 3:    P0 #4-6（凭据 + 合约 resolver + vnpy_ctp 安装）
Week 4:    P1 #7-13（**逐个** wire，每个独立跑 parity smoke）
Week 5:    P2 #14-20（端到端测试覆盖）
Week 6-9:  P3 #23 30-day sim soak
Week 10:   P3 #24-25 实盘灰度（1 symbol → 1 cluster）
Week 11+:  全 universe 实盘
```

### §4.1 关键依赖图

```
P0-2 (feature parity) ──┐
                        ├──> P0-1 (strategy wrapper) ──> P1-* (新特性 wire)
P0-3 (model registry) ──┘                                   │
                                                            ▼
P0-4 (credentials) ──┐                              P2-14 (sim parity)
                     ├──> P0-6 (vnpy_ctp env) ──>     │
P0-5 (contract) ─────┘                                ▼
                                            P3-23 (30-day soak)
                                                      │
                                                      ▼
                                        P3-24/25 (实盘灰度)
```

P0-2/3/5 是底层依赖，必须先做完。P0-1/4/6 可并行。P1 全部依赖 P0 完成。

---

## §5 验收门槛（按阶段）

### §5.1 sim soak 启动门槛（P0 + P1 完成后）

| 检查项 | 标准 |
|---|---|
| P0-2 feature parity | 1 symbol × 1 day × 60min，max\|diff\| < 1e-6 |
| P0-3 model registry | 启动时 schema check pass + 热加载 unit test 全过 |
| P0-5 contract resolver | 给定 2024-12-15 RB0 → RB2501；换月日测试通过 |
| P1-7~13 单测 | 7 个特性单测全过；每个特性单独跑 1 天 sim parity |
| 全量单测 | `pytest cta/` 全过（含 sim/live/portfolio_logic） |

### §5.2 实盘灰度启动门槛（30-day sim soak 后）

| 检查项 | 标准 |
|---|---|
| 30-day sim soak | 每周 parity report 通过；累计 PnL diff vs OOT ≤ 5% |
| daily report 自动归档 | 30 天每日报告完整无缺失 |
| 异常事件 | kill_switch 触发 0 次（非演练）；reject 率 < 0.5% |
| 监控告警 | Prometheus + 钉钉 webhook 全链路通；3 次模拟告警 ack |
| 回退预案 | 文档完整 + 1 次回滚演练成功 |

### §5.3 全 universe 实盘门槛（小额验证后）

| 检查项 | 标准 |
|---|---|
| 小额实盘 | 5-10 万元 × 5 个交易日，累计对账误差 ≤ 0.5% |
| 业务指标 | 5 日 sharpe ≥ 0.8、max_drawdown ≤ 2%、win_rate ≥ 35% |
| 异常事件 | 无 reject、无 partial fill 异常、无凭据泄漏 |

---

## §6 立即可执行的 next step

| 优先级 | 动作 | 工作量 | 触发条件 |
|---|---|---|---|
| 🔴 立即 | 本文档定稿 + review | 0.5 天 | 已完成 |
| 🟡 本周 | **P0-2 online_feature parity 测试**（最关键风险）| 4-6 小时 | 立即 |
| 🟡 本周 | **P0-1 选 donchian_breakout 写 vnpy strategy wrapper**（最小可跑通示例）| 1 天 | 同上 |
| 🟢 下周 | P0-3 模型热加载 + P0-4 凭据配置（并行） | 1.5 天 | P0-1/2 完成后 |
| 🟢 下周 | P0-5 contract_resolver | 1 天 | 并行 |
| 🟢 下周 | P0-6 vnpy_ctp 装机 | 0.5 天 | 并行 |
| ⚪ 第 3-4 周 | P1 逐个 wire（按 F1→F7 顺序）| 5-6 天 | P0 全部完成 |

---

## §7 实施 TODO（按任务 ID 索引）

按 §2 的 27 项任务编号，每个任务的 spec / 测试 / 验收已在 §2 内联给出。
codex / claude 实施时按 ID 查表，**先 P0-2/3/5（底层依赖），再 P0-1/4/6（并行），最后 P1（按 F1→F7）**。

---

## §8 不变量复述（最重要）

以下 7 条**任何时候不能破坏**：

1. **三层 cfg 一致**（OOT / sim / live 同源）
2. **默认 off**（新特性 use_*=False，需显式 opt-in）
3. **feature parity 硬门槛**（max\|diff\| < 1e-6 才能上 sim）
4. **凭据永不入库**（.gitignore + CI 检查）
5. **kill_switch 最高优先级**（任何 exception 不能绕过）
6. **TDD**（先测试后实现）
7. **文档先行**（接口变更先更新本文档 §0）

---

## §9 参考链路

- 现有 sim runner：[cta/sim/sim_runner.py](../sim/sim_runner.py)（已通 vnpy_ctp 启动链）
- 现有 live runner：[cta/live/live_runner.py](../live/live_runner.py)（骨架完整）
- 现有 parity：[cta/sim/parity_check.py](../sim/parity_check.py) + [cta/live/parity_helper.py](../live/parity_helper.py)
- 现有 cluster registry：[cta/model/training/cluster_model_registry.py](../model/training/cluster_model_registry.py)
- 现有连续合约数据：[cta/data_code/futures_downloader.py](../data_code/futures_downloader.py)
- OOT 入口（cfg 同源点）：[cta/config/model_oot_eval_config.py](../config/model_oot_eval_config.py)
- portfolio_logic 入口：[cta/portfolio_logic/config.py](../portfolio_logic/config.py)
- 已落地 OOT 新特性：见 §1.3
- 项目规则：[CLAUDE.local.md](../../CLAUDE.local.md)（特别是"离线/仿真/实盘逻辑一致性"硬要求）
- 设计文档同款风格：[ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)、
  [profit_aware_trend_adaptive_design.md](./profit_aware_trend_adaptive_design.md)

---

## §10 v2 后续扩展（路线图之外）

1. **多账户并行**：同时跑 2-3 个 CTP 账户，做 A/B 模型对比
2. **券商接入**：除 CTP 外，加港股 / 外盘 gateway（vnpy_ib / vnpy_oes）
3. **机器学习驱动的运维**：日志异常分类、自动告警分级
4. **可视化 dashboard**：替代 daily_report.md，做 Grafana 实时看板
5. **演练回放**：根据 sim_trades.csv 重放历史日，验证新版本不退化
