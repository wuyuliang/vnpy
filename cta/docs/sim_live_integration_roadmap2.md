# 仿真 / 实盘接入路线图 v2（delta）

> **本文档是 [v1 sim_live_integration_roadmap.md](./sim_live_integration_roadmap.md) 的增量**，
> 不替代 v1。v1 仍为完整 spec（27 项任务、风险矩阵、依赖图）；v2 只覆盖：
>
> 1. 自 2026-05-23 v1 定稿后**已落地的代码资产**（不在 v1 任务清单内的新增）；
> 2. 2026-05-24 OOT 侧 3 个 default-on 改动产生的**新 sim/live wire 任务**（P0Δ）；
> 3. v1 27 项任务**截至 2026-05-24 的状态快照** + 高优残余任务的重新排序。
>
> 实施时 v1 与 v2 同时查；v1 给完整 spec，v2 给"现在做什么 / 不做什么"。

> **⚠️ 2026-05-29 功能下线**：`trailing_take_profit` / `profit_aware_horizon` /
> `trend_aware_trade_filter` / `ma_cross_gate` / `regime_short_filter` 五个功能
> 及 `--enable-all-oot-modules` 开关已从 train + OOT + sim 全链路删除（含设计文档
> `ma_cross_regime_aware_design.md`）。本文档及 v1 中涉及 P1-8/9/10/11 的条目作废；
> 保留 `trailing_exit`（ATR 移动止损）、`horizon_extend`、base `trade_filter` gate、
> `compute_trend_score` / `regime_label` 等共享基建。

---

## §0 修订记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-05-23 | claude | v1 [sim_live_integration_roadmap.md](./sim_live_integration_roadmap.md) 定稿 |
| 2026-05-24 | claude | v2 delta 初稿：盘点 v1 残余 + 接入 Action 1/2/3 三段 default-on 的新 sim wire 任务 |

---

## §1 自 v1 之后的进展盘点（2026-05-23 → 2026-05-24）

### §1.1 sim/live 代码资产现状

| 子包 | 文件数 | 行数 | 状态变化（vs v1） |
|---|---|---|---|
| [cta/sim/](../sim/) | 7 Py + adapters/ + tests/ | ~1,200 | +`feature_parity_sim_soak.py` / `timezone_helpers.py` / `trading_calendar.py` 落地；adapters 子包成型 |
| [cta/sim/adapters/](../sim/adapters/) | 4 Py | 517 | **新增子包**：[`entry_gate_chain.py`](../sim/adapters/entry_gate_chain.py)（140 行）/ [`position_evaluator.py`](../sim/adapters/position_evaluator.py)（192 行）/ [`rotation_stepper.py`](../sim/adapters/rotation_stepper.py)（79 行）/ [`state_provider.py`](../sim/adapters/state_provider.py)（106 行） |
| [cta/live/](../live/) | 23 Py + tests/ | ~2,800 | 较 v1 增 [`hot_reloadable_registry.py`](../live/hot_reloadable_registry.py) / [`order_lifecycle.py`](../live/order_lifecycle.py) / [`feature_parity.py`](../live/feature_parity.py) / [`daily_report_archiver.py`](../live/daily_report_archiver.py) 等 |
| sim tests | 7 文件 | — | +`test_adapters.py` / `test_timezone_consistency.py` / `test_trading_calendar.py` |
| live tests | 21 文件 | — | +`test_kill_switch_end_to_end.py` / `test_order_lifecycle.py` / `test_margin_reconciler.py` / `test_online_feature_parity.py` 等 |

**关键里程碑（v1 未覆盖）**：
- [`cta/sim/adapters/entry_gate_chain.py:61 EntryGateChain`](../sim/adapters/entry_gate_chain.py) 已直调
  [`cta/model/oot/oot_gates.py:apply_ma_cross_gate / apply_regime_short_filter`](../model/oot/oot_gates.py)
  与 trade_filter，**OOT/sim 单一实现**已落地（满足 v1 §3.2 不变量 1）；
- [`cta/sim/adapters/position_evaluator.py:57 PositionEvaluator`](../sim/adapters/position_evaluator.py)
  已复用 [`TrailingTakeProfitEvaluator`](../portfolio_logic/trailing_take_profit.py) +
  [`resolve_max_holding_bars`](../portfolio_logic/profit_aware_horizon.py)（v1 P1-8/9 零重复）；
- [`cta/live/kill_switch.py`](../live/kill_switch.py)（62 行）二路激活（内存 + 信号文件）已完成；
- [`cta/live/risk.py`](../live/risk.py)（243 行）5 条规则链已完成；
- [`cta/config/sim_credentials_template.py`](../config/sim_credentials_template.py) `load_credentials()`
  工厂 + `is_valid()` 校验已落地（v1 P0-4 接近完成）。

### §1.2 v1 27 项任务状态快照表（截至 2026-05-24）

状态标签：✅ completed / 🟡 in_progress / ⚪ not_started / 🚫 superseded by v2

| v1 ID | 任务 | 状态 | 备注 |
|---|---|---|---|
| P0-1 | vnpy CtaTemplate 策略 wrapper | 🟡 | [`cta/strategy/baseline_strategies.py`](../strategy/baseline_strategies.py) + [`cta_adapter.py`](../strategy/cta_adapter.py) 已包装 v1 on_bar 接口；CtaTemplate 子类 3 个 baseline 落地，待 sim_runner 加载验证 |
| P0-2 | online_feature 实时 vs 离线 parity | 🟡 | [`cta/live/tests/test_online_feature_parity.py`](../live/tests/test_online_feature_parity.py) 框架就绪（8.4KB），需扩到 1 symbol × 1 day × 60min × 100+ 列 max\|diff\|<1e-6 |
| P0-3 | 模型热加载 + cluster_registry 校验 | 🟡 | [`cta/live/hot_reloadable_registry.py`](../live/hot_reloadable_registry.py) + [`model_filter.py`](../live/model_filter.py) 骨架完成；缺 SIGHUP signal handler 实装 |
| P0-4 | SimNow / CTP 凭据配置 | 🟡 | template + 工厂完成；缺 sim_runner 启动 fail-fast + CI grep 防回归脚本 |
| P0-5 | 连续合约 → 主力合约映射 + 换月 | 🟡 | [`cta/portfolio_logic/contract_resolver.py`](../portfolio_logic/contract_resolver.py) 骨架完成；缺 vnpy MainEngine.get_all_contracts() fallback hook |
| P0-6 | vnpy_ctp 真实环境装机 | ⚪ | 文档需补；smoke 脚本待写 |
| P1-7 | cross_sectional_momentum_rotation | ✅ | 已补齐 RotationOrderIntent → strategy 下单适配与 `run_sim/run_live` 主循环 wire（见 `rotation_order_wire.py` + `sim_runner.py`） |
| P1-8 | trailing_take_profit | ✅ | PositionEvaluator 已接，单测 [`test_adapters.py`](../sim/tests/test_adapters.py) 覆盖 |
| P1-9 | profit_aware_horizon | ✅ | 同上 |
| P1-10 | ma_cross_gate + regime_short_filter | 🟡 | EntryGateChain 已串联；缺 [`state_provider`](../sim/adapters/state_provider.py) 实时 ma_alignment / regime_label lookup 实现 |
| P1-11 | trend_aware_trade_filter | 🟡 | EntryGateChain 已串联；同 P1-10 缺 realized_vol_rank 实时 lookup |
| P1-12 | trade_filter_bypass_signal_types | ✅ | 零改，OOT/sim 共用 |
| P1-13 | intrabar_stop_loss_pct_by_cluster_interval | ✅ | PositionEvaluator [`_resolve_intrabar_stop_pct`](../sim/adapters/position_evaluator.py) 已接 |
| P2-14 | 5-day sim parity smoke | 🟡 | [`test_sim_parity_5day.py`](../sim/tests/test_sim_parity_5day.py) 框架就绪，待扩到 10 symbol × 10 day |
| P2-15 | 断线重连 chaos test | 🟡 | [`test_supervisor_chaos.py`](../live/tests/test_supervisor_chaos.py) 已有，需覆盖 gateway 30s 断线场景 |
| P2-16 | kill_switch 端到端 | ✅ | [`test_kill_switch_end_to_end.py`](../live/tests/test_kill_switch_end_to_end.py) 覆盖 |
| P2-17 | partial fill / reject / 撤单 6 态 | 🟡 | [`test_order_lifecycle.py`](../live/tests/test_order_lifecycle.py) 已有；需补 chaos test 覆盖 6 态全转移 |
| P2-18 | 夜盘 / 节假日 / 涨跌停 | 🟡 | [`test_trading_calendar.py`](../sim/tests/test_trading_calendar.py) 已有；涨跌停板边界 case 待补 |
| P2-19 | 保证金 / 资金占用 | 🟡 | [`margin_reconciler.py`](../live/margin_reconciler.py) + [`test_margin_reconciler.py`](../live/tests/test_margin_reconciler.py) 完成；缺 CTP query_account 真实对账 CI |
| P2-20 | timezone 一致性 | ✅ | [`cta/sim/timezone_helpers.py`](../sim/timezone_helpers.py) + [`test_timezone_consistency.py`](../sim/tests/test_timezone_consistency.py) 完成 |
| P3-21 | 监控告警 | 🟡 | [`monitoring.py`](../live/monitoring.py) 183 行骨架完成；Prometheus exporter + 钉钉 webhook 实装待做 |
| P3-22 | 结构化日志 + rotation | ⚪ | 未开始；可复用 stdlib logging 即可 |
| P3-23 | 30-day sim soak run | ⚪ | [`cta/live/soak_gate.py`](../live/soak_gate.py) 193 行已有；soak 自动化脚本缺 |
| P3-24 | 实盘灰度计划 | ⚪ | 文档型任务 |
| P3-25 | 小额实盘验证 | ⚪ | 等 P0/P1/P2 完成 |
| P3-26 | 复盘自动化 | 🟡 | [`daily_report.py`](../live/daily_report.py) + [`daily_report_archiver.py`](../live/daily_report_archiver.py) 已落地，邮件摘要待补 |
| P3-27 | 回退策略文档 | ⚪ | 文档型任务 |

**汇总**：✅ 6 项 / 🟡 16 项 / ⚪ 5 项 / 🚫 0 项。**核心瓶颈在 P0-2 / P0-3 / P0-5 / P1-7 / P1-10/11 这 5 个 🟡 项**。

### §1.3 自 v1 起新增、未在 v1 清单内的 OOT 改动

2026-05-24 OOT 侧合并 3 个 **default-on** 改动（详见 [`cta/report/change_log.md`](../report/change_log.md) 2026-05-24 三段日志）：

| Action | 改动点 | sim/live 影响 |
|---|---|---|
| **Action 1** 真实 cost 表 | [`cta/config/cost_manifest.py`](../config/cost_manifest.py) `build_cluster_interval_cost_dict()`；24 cell × 2（commission + slippage）写入 `OotEvaluationConfig` 默认 | sim 已用 OOT cfg，cost_pct 经由 [`resolve_per_row_cost_pct`](../model/oot/pipeline_oot_evaluation_inputs.py) 自动按 cluster\|interval 分层。**风险**：sim/live 下单成交后的 PnL 估算如果走独立 cost 路径（不读 cfg）会与 OOT 漂移 |
| **Action 2** bond 严格阈值 | [`cta/config/cluster_bond_filter_manifest.py`](../config/cluster_bond_filter_manifest.py) `STRICT_BOND_PERCENTILE_THRESHOLDS`（pctl 80）+ `STRICT_BOND_RAW_THRESHOLDS`（raw 0.70）写入默认 | EntryGateChain 通过 OOT trade_filter gate 函数已经天然消费；**风险**：sim 在 trade_filter_gate_mode 切换或 cluster 派发逻辑里若有独立硬编码阈值需自检 |
| **Action 3** htf_missing 局部放宽 | [`cta/portfolio_logic/config.py`](../portfolio_logic/config.py) `_default_fallback_when_htf_missing_by_cluster_interval()`（12 minute60/30 cell = "both"） | HtfGate.filter 已按 cell 路由，sim 主循环若调用 [`HtfGate`](../portfolio_logic/interval_gate.py) 直接受益。**风险**：sim 当前 EntryGateChain 不含 HTF gate（HTF 只在 OOT portfolio_logic 回放层启用），sim 直连 vnpy 模式下需明确"sim 是否启用 HTF gate" |

3 个改动都**默认 on**且**显式传 `={}` 可回退**，意味着任何使用 `OotEvaluationConfig()` 默认构造的 sim/live 都会自动用上新行为；这正是为什么 v2 §2 必须立项确认"sim 真的消费了"。

---

## §2 自 v1 起新增的 sim/live wire 任务（Δ）

### §2.1 P0Δ-1：sim 消费真实 cost manifest（Action 1）

| 维度 | 内容 |
|---|---|
| OOT 已有 | [`resolve_per_row_cost_pct(df, cfg)`](../model/oot/pipeline_oot_evaluation_inputs.py) 按行返 cost_pct；OOT trade DataFrame 的 `cost_pct` 列已是 per-row |
| sim wire | sim_runner 撮合后 PnL 计算需调同一函数；不允许走 `cfg.commission_pct_per_trade + cfg.slippage_pct_per_trade` 标量回退（除非 cfg 显式空 dict）|
| 测试 | 新增 `cta/sim/tests/test_cost_manifest_parity.py`：对同一笔成交，sim 算出的 cost_pct 与 OOT `resolve_per_row_cost_pct` 输出 abs diff < 1e-9 |
| 验收 | 1 symbol × 1 day × 5 笔，sim 与 OOT cost_pct 逐笔一致；bond 笔 cost < 1 bp，black 笔 cost ≈ 5 bp |
| 工作量 | 0.5 天 |

### §2.2 P0Δ-2：sim 消费 cluster_bond 严格阈值（Action 2）

| 维度 | 内容 |
|---|---|
| OOT 已有 | trade_filter gate 默认含 `STRICT_BOND_*_THRESHOLDS`；bond\|day pctl=80 / raw=0.70 |
| sim wire | EntryGateChain 复用 OOT trade_filter，理论零改。**需自检**：sim 内 trade_filter_gate_mode 切换路径、cluster 派发 key 归一化是否一致 |
| 测试 | 新增 `cta/sim/tests/test_bond_filter_parity.py`：bond 候选 100 条灌入 sim EntryGateChain 与 OOT `apply_oot_model_gates`，pass/block 决策位逐行一致 |
| 验收 | 100% 决策一致；任一 cell 漂移即 fail |
| 工作量 | 0.5 天 |

### §2.3 P0Δ-3：sim 消费 htf_missing per-cell fallback（Action 3）

| 维度 | 内容 |
|---|---|
| OOT 已有 | [`HtfGate.filter`](../portfolio_logic/interval_gate.py) 按 (cluster, interval) 查 dict 决定 skip / both |
| sim wire | 当前 [`EntryGateChain`](../sim/adapters/entry_gate_chain.py) **未含 HTF gate**——OOT 是在 portfolio_logic 回放层独立调 HtfGate，sim 主循环若启用 HTF gate 需新增第 4 段 gate。**决策点**：sim 是否启用 HTF？建议默认 on（与 OOT 同口径），用 cfg.portfolio_logic.enable_htf_gate 控制 |
| 测试 | 新增 `cta/sim/tests/test_htf_gate_fallback_sim.py`：metal\|60min 候选无 HTF state → sim 与 OOT 都放行；bond\|60min 候选无 HTF → 都拦截 |
| 验收 | 默认 cfg 下 sim 与 OOT htf 决策位 100% 一致 |
| 工作量 | 1 天（含决策 + wire + 测试） |

### §2.4 P0Δ-4：cfg 漂移自检 CI

| 维度 | 内容 |
|---|---|
| 目标 | 防止 OOT/sim/live 三层之一独立维护 cost / threshold / fallback manifest |
| 新增脚本 | `cta/run/tests/test_cfg_drift_guard.py`：grep `cta/sim/ cta/live/` 不允许出现硬编码 `0.0003` / `0.00015` / `STRICT_BOND_` 等数值字面量（除测试） |
| 测试 | 同上脚本即测试 |
| 验收 | CI 通过；任何 hardcode 重新引入会 fail |
| 工作量 | 0.5 天 |

**P0Δ 小计：2.5 天**。所有 4 项都依赖 2026-05-24 Action 1/2/3 已合并；优先级高于 v1 §3 残余任务，因 default-on 让"sim 偷偷漂移"的风险窗口已经打开。

---

## §3 v1 中仍未完成的高优任务（重新排序）

不重写 v1 spec，只列名 + 锚点 + 当前优先级。

| v2 优先级 | v1 ID | 标题 | v1 锚点 | 现状 |
|---|---|---|---|---|
| 🔴 P1 | P0-2 | online_feature parity 精细测试 | [v1 §2.1 P0-2](./sim_live_integration_roadmap.md) | [`test_online_feature_parity.py`](../live/tests/test_online_feature_parity.py) 框架就绪，待扩展到精度 1e-6 |
| 🔴 P1 | P0-3 | model registry hot-reload SIGHUP | [v1 §2.1 P0-3](./sim_live_integration_roadmap.md) | hot_reloadable_registry 完成；缺 signal handler |
| 🔴 P1 | P0-5 | contract_resolver vnpy fallback | [v1 §2.1 P0-5](./sim_live_integration_roadmap.md) | 骨架完成；缺 MainEngine.get_all_contracts() hook |
| ✅ done | P1-7 | cross_sectional_rotation 适配器 | [v1 §2.2 P1-7](./sim_live_integration_roadmap.md) | 已完成：`RotationOrderIntent` 下单映射 + 主循环 `on_bar` wire |
| 🟡 P2 | P1-10/11 | state_provider 实时 ma/regime/vol lookup | [v1 §2.2 P1-10/11](./sim_live_integration_roadmap.md) | [`FeatureBasedStateProvider`](../sim/adapters/state_provider.py) 接口已声明；lookup 实现待写 |
| 🟡 P2 | P2-17 | order lifecycle 6 态全覆盖 | [v1 §2.3 P2-17](./sim_live_integration_roadmap.md) | 基本测试已有；chaos test 待写 |
| 🟡 P2 | P2-18 | trading_calendar 边界 case | [v1 §2.3 P2-18](./sim_live_integration_roadmap.md) | 日历测试已有；涨跌停板 + 春节 case 待补 |
| 🟢 P3 | P3-21 | 监控 exporter（Prometheus + 钉钉） | [v1 §2.4 P3-21](./sim_live_integration_roadmap.md) | monitoring.py 骨架；exporter 实装待做 |
| 🟢 P3 | P3-23 | 30-day soak 自动化 | [v1 §2.4 P3-23](./sim_live_integration_roadmap.md) | soak_gate.py 已有；自动化脚本待写 |

---

## §4 完整 punch list（合并 §2 + §3）

| 优先级 | ID | 标题 | 关键文件 | 验收门槛 | 工作量 | 状态 |
|---|---|---|---|---|---|---|
| 🔴 立即 | P0Δ-1 | sim 消费 cost manifest | `cta/sim/sim_runner.py` PnL 路径 + `test_cost_manifest_parity.py` | sim vs OOT cost_pct abs diff < 1e-9 | 0.5d | ⚪ |
| 🔴 立即 | P0Δ-2 | sim 消费 bond 严格阈值 | `EntryGateChain` 自检 + `test_bond_filter_parity.py` | 100 bond 候选决策 100% 一致 | 0.5d | ⚪ |
| 🔴 立即 | P0Δ-3 | sim 消费 htf fallback | EntryGateChain 增 HTF 段 + `test_htf_gate_fallback_sim.py` | 默认 cfg sim/OOT htf 决策 100% 一致 | 1d | ⚪ |
| 🔴 立即 | P0Δ-4 | cfg 漂移自检 CI | `cta/run/tests/test_cfg_drift_guard.py` | grep 通过 | 0.5d | ⚪ |
| 🔴 高 | P0-2 | online_feature parity 精细 | `cta/live/tests/test_online_feature_parity.py` | 1 sym × 1 day × 100+ 列 max\|diff\| < 1e-6 | 4-6h | 🟡 |
| 🔴 高 | P0-3 | model SIGHUP handler | `cta/live/model_filter.py` | signal kill -HUP → 切换 + 失败回滚 | 2h | 🟡 |
| 🔴 高 | P0-5 | contract_resolver vnpy hook | `cta/portfolio_logic/contract_resolver.py` + sim_runner | RB0 + 2024-12-15 → RB2501 ；换月日不发新单 | 1d | 🟡 |
| ✅ 完成 | P1-7 | rotation adapter | `cta/sim/adapters/rotation_stepper.py` + `rotation_order_wire.py` | 主循环可按 bar 驱动 stepper 并发单；含 sim 单测覆盖 | 1.5d | ✅ |
| 🟡 中 | P1-10/11 | state_provider lookup | `cta/sim/adapters/state_provider.py` | sim 实时 ma/regime/vol 与 OOT batch 同 bar 一致 | 1d | 🟡 |
| 🟡 中 | P2-14 | 5-day parity smoke 扩展 | `cta/sim/tests/test_sim_parity_5day.py` | 10 sym × 10 day, 每笔 diff ≤ 1bp | 1d | 🟡 |
| 🟡 中 | P2-17 | order lifecycle chaos | 新增 `test_order_lifecycle_chaos.py` | 6 态全转移覆盖 | 1d | 🟡 |
| 🟡 中 | P2-18 | trading_calendar 边界 | `cta/sim/tests/test_trading_calendar.py` 扩 | 涨跌停 / 春节 / 夜盘三场景 | 1d | 🟡 |
| 🟢 低 | P3-21 | 监控 exporter | `cta/live/monitoring.py` | Prometheus metrics + 钉钉 webhook 实装 | 1.5d | 🟡 |
| 🟢 低 | P3-23 | 30-day soak 自动化 | 新增 `cta/sim/soak_automation.py` | weekly parity report 自动生成 | 2d | ⚪ |

**总计 14 项 ≈ 13.5 工人日**（不含 SimNow 账号申请等待 / 等待 30-day soak 自然时间）。

---

## §5 推荐落地顺序

```
W1 (本周):
  P0Δ-1/2/3/4 全部完成 (2.5d) — 让 default-on 真正 sim 一致
  P0-2 online_feature parity 起步 (4-6h)

W2:
  P0-2 收尾 + P0-3 SIGHPHU + P0-5 vnpy hook (≈ 2d)
  P0-6 vnpy_ctp 装机文档 + smoke (0.5d)

W3:
  P1-7 rotation adapter (1.5d)
  P1-10/11 state_provider 实时 lookup (1d)

W4:
  P2-14/17/18 测试补强 (3d)

W5+:
  P3-21 监控 exporter (1.5d)
  P3-23 启动 30-day soak (自动化脚本 2d + 30 个自然交易日)

W10:
  P3-24/25 灰度 1 symbol → 1 cluster → 全 universe
```

**关键依赖**：
- P0Δ-1/2/3 ← OotEvaluationConfig 默认（已就绪，无外部依赖）
- P0Δ-3 决策点 ← sim 是否启用 HTF gate（建议默认 on）
- P0-2 ← P1-10/11 state_provider lookup（feature parity 含动态 ma/regime/vol）
- P1-7/10/11 全部依赖 P0-2 通过 + P0-3/5 完成

---

## §6 验收门槛更新

### §6.1 sim soak 启动门槛（v1 §5.1 + Action 一致性）

新增 3 行：

| 检查项 | 标准 |
|---|---|
| Action 1 一致性 | `test_cost_manifest_parity.py` 通过；sim/OOT cost_pct 逐笔 abs diff < 1e-9 |
| Action 2 一致性 | `test_bond_filter_parity.py` 通过；100 bond 候选决策 100% 一致 |
| Action 3 一致性 | `test_htf_gate_fallback_sim.py` 通过；默认 cfg htf 决策 100% 一致 |

### §6.2 实盘灰度门槛

保持 [v1 §5.2](./sim_live_integration_roadmap.md) 不变。

---

## §7 不变量复述

继承 v1 §8 的 7 条，新增 3 条：

8. **三段 default-on 必须传导**：Action 1/2/3 在 sim/live 必须由同一份 OOT cfg 驱动；任何 sim 独立的 cost / threshold / fallback 字面量都是漂移。
9. **sim/live 不允许独立 manifest**：cost_manifest / cluster_bond_filter_manifest / fallback dict 只允许从 `cta/config/*` 引入，sim/live 子包不得复制定义。
10. **v2 任何新增 cfg 字段必须有"显式空 dict 退路"**：让旧脚本与单测可以通过 `={}` 显式回退旧行为，防止 default-on 突变破坏现存 fixture。

---

## §8 立即可执行的 next step

| 优先级 | 动作 | 工作量 | 触发条件 |
|---|---|---|---|
| 🔴 本周 | P0Δ-1 / P0Δ-2 / P0Δ-3 / P0Δ-4 四项全做完 | 2.5d | 立即（无外部依赖）|
| 🔴 本周 | P0-2 online_feature parity 精细化（最关键风险）| 4-6h | 并行 |
| 🟡 下周 | P0-3 SIGHUP handler + P0-5 vnpy hook | 1.5d | P0Δ 完成后 |
| 🟡 下周 | P0-6 vnpy_ctp 装机 + smoke | 0.5d | 并行 |
| 🟢 第 3-4 周 | P1-7 / P1-10/11 / P2-* | 5d | P0 全部完成 |

---

## §9 参考链路

- v1 总 spec：[`cta/docs/sim_live_integration_roadmap.md`](./sim_live_integration_roadmap.md)
- 2026-05-24 三段 default-on：[`cta/report/change_log.md`](../report/change_log.md)（2026-05-24 日条目）
- cost manifest：[`cta/config/cost_manifest.py`](../config/cost_manifest.py) `build_cluster_interval_cost_dict()`
- bond filter manifest：[`cta/config/cluster_bond_filter_manifest.py`](../config/cluster_bond_filter_manifest.py)
- htf fallback default：[`cta/portfolio_logic/config.py`](../portfolio_logic/config.py) `_default_fallback_when_htf_missing_by_cluster_interval()`
- OOT cfg 同源点：[`cta/config/model_oot_eval_config.py`](../config/model_oot_eval_config.py)
- sim adapter 入口：[`cta/sim/adapters/entry_gate_chain.py`](../sim/adapters/entry_gate_chain.py) +
  [`position_evaluator.py`](../sim/adapters/position_evaluator.py) +
  [`state_provider.py`](../sim/adapters/state_provider.py)
- 项目规则：[`CLAUDE.local.md`](../../CLAUDE.local.md) "离线/仿真/实盘逻辑一致性" 硬要求

---

## §10 v1 ↔ v2 对照表

| v1 ID | v2 处理 | 备注 |
|---|---|---|
| P0-1 | 状态化（§1.2）| baseline_strategies.py 落地，待 sim_runner 验证 |
| P0-2 | 留 v1 spec + §3 重排优先级 | 仍是核心瓶颈 |
| P0-3 | 留 v1 spec + §3 | hot_reloadable_registry 已落地，缺 signal handler |
| P0-4 | 状态化（§1.2 完成 90%）| template + 工厂完成 |
| P0-5 | 留 v1 spec + §3 | 骨架完成，缺 vnpy hook |
| P0-6 | 留 v1 spec | 未开始 |
| P1-7 | 留 v1 spec + §3 | rotation_stepper 骨架 |
| P1-8 / P1-9 / P1-12 / P1-13 | ✅ 完成 | PositionEvaluator / EntryGateChain 已接 |
| P1-10 / P1-11 | 留 v1 spec + §3 | EntryGateChain wire 完成；state_provider lookup 待写 |
| P2-14~20 | 留 v1 spec + §3 | 大部分 🟡 in_progress |
| P3-21~27 | 留 v1 spec + §3 | 监控与 soak 待启动 |
| —— | **新增 P0Δ-1/2/3/4** | 2026-05-24 三段 default-on + cfg 漂移自检 |

---

## §11 v3 后续扩展（路线图之外）

参照 [v1 §10](./sim_live_integration_roadmap.md)，本轮不展开。预留：

1. 多账户并行 / A-B 模型对比
2. 港股 / 外盘 gateway 接入
3. 日志异常 ML 分类
4. Grafana 实时 dashboard
5. 演练回放（sim_trades.csv 重放）
