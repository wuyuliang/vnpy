# 银河证券 CTP 仿真交易接入规划（sim_plan）

> 作者视角：中国商品 CTA 实盘老兵。本文是「把现有 OOT 回测系统平稳接到银河期货 CTP 仿真
> 前置」的**专项执行计划**——不重复 [`sim_live_integration_roadmap.md`](sim_live_integration_roadmap.md)（v1，27 项）
> 和 [`sim_live_integration_roadmap2.md`](sim_live_integration_roadmap2.md)（v2 delta），
> 而是把"接入仿真"这条关键路径上的改动按 P0/P1/P2 拉直，配 [`better_20260530.md`](better_20260530.md)
> 的相关 backlog（B1/C1/C3）。
>
> **最高铁律**：OOT = sim = live 三层**同一份 cfg、同一套 gate/risk 代码**。回测看到的，
> 仿真必须复现；仿真复现了，实盘才敢上。这是用户第 4 点的核心，也是本计划的脊梁。

---

## §0 目标与范围

- **券商**：银河期货 CTP 仿真前置（CTP 协议，与现有 `vnpy_ctp` 链路一致）。
- **品种/周期**：现役 day / 60min / 30min 三个 interval（与 OOT 训练口径一致）。
- **目标**：T 日完整闭环跑通——盘后自动下数据 → 盘前自检 → 盘中自动出信号/下单/拆单/管仓 →
  盘后逐笔落日志 + 与 OOT 对账 + 出报告；先 **1 手灰度**，soak 通过再放量。
- **不做**：不碰真实资金；不改 OOT 回测口径（只复用）；凭据绝不入库。

---

## §1 现状盘点 + 最大缺口

**已具备（直接复用）**：

| 域 | 资产 |
|----|------|
| 运行时 | [`cta/sim/sim_runner.py`](../sim/sim_runner.py)（vnpy_ctp 启动 + `_attach_observers` 把 RiskGuard 挂到 `order_filter`）、[`cta/live/live_runner.py`](../live/live_runner.py) |
| 入场链 | [`cta/sim/adapters/entry_gate_chain.py`](../sim/adapters/entry_gate_chain.py)（trade_filter → HTF → RiskOrchestrator）|
| 持仓/状态 | [`position_evaluator.py`](../sim/adapters/position_evaluator.py)（hard_stop）、[`state_provider.py`](../sim/adapters/state_provider.py)、[`rotation_stepper.py`](../sim/adapters/rotation_stepper.py) |
| 风控 | [`cta/risk/`](../risk/)（orchestrator + W6-W10 guards/sizers）、[`cta/live/risk.py`](../live/risk.py)（6 条 `_BaseRule`）、[`kill_switch.py`](../live/kill_switch.py)、[`soak_gate.py`](../live/soak_gate.py) |
| 数据/特征 | [`cta/data_code/futures_downloader.py`](../data_code/futures_downloader.py)、[`cta/feature/`](../feature/)、[`cta/live/online_feature.py`](../live/online_feature.py) |
| 阈值 | [`cta/risk/state/score_quantile_manifest.py`](../risk/state/score_quantile_manifest.py)（防泄露，train+valid 算分位）|
| 日历 | [`cta/sim/trading_calendar.py`](../sim/trading_calendar.py)、[`timezone_helpers.py`](../sim/timezone_helpers.py) |
| 对账/报告 | [`cta/sim/feature_parity_sim_soak.py`](../sim/feature_parity_sim_soak.py) + parity 测试群、[`cta/model/reporting/oot_report_writer.py`](../model/reporting/oot_report_writer.py)、`meta/cfg_fingerprint.json`（argv + generated_at + cfg）|
| 凭据 | [`cta/config/sim_credentials_template.py`](../config/sim_credentials_template.py) + `load_credentials()` |

**最大缺口（critical path，本计划的重点）**：

1. **🔴 实时信号生成入口缺失**：离线靠 train 产 `predictions.csv`；**没有**"加载已训练模型 →
   对今日最新 bar 算特征 → predict → 查阈值 manifest → 出当日候选"的在线 inference 入口。→ S-C。
2. **🔴 live 未接 RiskOrchestrator**（`cta/live/*.py` 零引用 `cta.risk`，M2/B1）。→ S-D。
3. **🟡 拆单未实现**（grep 无 iceberg/twap/split）。→ S-G。
4. **🟡 逐笔交易日志↔OOT trade schema 对账闭环未串起来**。→ S-E / S-F。
5. **🟡 日/周/月 + 复盘报告生成器缺失**（OOT 报告器可复用未接实盘）。→ S-J。
6. **🟡 三层一致性未强制**（cfg_fingerprint 已 dump，但无 OOT=sim=live 三处比对闸门）。→ S-F。

---

## §2 目标架构：每日实盘闭环

```
┌─ T-1 盘后（数据/模型）────────────────────────────────────────────┐
│ S-B 自动下载 day/60min/30min 增量 → origin → feature → model_feature │
│      └→ 完整性闸门：品种齐 / 无缺 bar / 无未来数据 / 时间戳单调          │
│ （每周/按需）重训 → 产 models/ + predictions + score_quantile_manifest │
└───────────────────────────────────────────────────────────────────┘
                                  │
┌─ T 盘前（自检）────────────────────────────────────────────────────┐
│ S-I checklist：数据新鲜度(PredictionStaleGuard) / 模型age / 换月日历 /  │
│      保证金充足 / kill_switch=off / 昨日对账平 → 任一不过则不开盘       │
└───────────────────────────────────────────────────────────────────┘
                                  │
┌─ T 盘中（每根 bar 收盘触发：30min/60min/day）──────────────────────┐
│ 行情(md) → BarGenerator → on_bar                                    │
│   └ S-C 信号生成：online_feature(parity) → 模型 predict → 查阈值     │
│           manifest → **同一份** apply_trade_filter_gate + stacking   │
│           → 候选机会 candidates                                      │
│   └ S-D EntryGateChain(trade_filter→HTF→RiskOrchestrator)           │
│           + RiskGuard(6 规则) + kill_switch → pass? + adjusted_lots  │
│   └ S-G sizing → 拆单(iceberg/TWAP/ADV 参与率) → vnpy_ctp send_order │
│           → order_lifecycle 6 态跟踪 → 成交回报                       │
│   └ S-H 持仓管理：hard_stop + trailing_exit + horizon                │
│           + 涨跌停/换月/流动性 guard → 平仓                            │
│   └ S-E 每个 order/trade/decision 落结构化日志（列对齐 OOT trade_cols）│
└───────────────────────────────────────────────────────────────────┘
                                  │
┌─ T 盘后（对账/报告）──────────────────────────────────────────────┐
│ S-F 三层对账：① cfg_fingerprint(OOT=sim=live) 比对                   │
│              ② 同候选逐笔 decision parity（passed/lots/block_reason）│
│              ③ margin_reconciler 资金/持仓对账                        │
│ S-J 日报（成交/盈亏/持仓/风控触发/parity 结果）→ 周报 → 月报 → 复盘   │
│ S-K 隔夜净值/持仓 bootstrap，supervisor 守护                          │
└───────────────────────────────────────────────────────────────────┘
```

---

## §3 改动总表

| ID | 标题 | 类别 | 优先级 | 工作量 |
|----|------|------|--------|--------|
| S-A | 银河 CTP 仿真前置连接与凭据 | 接入 | **P0** | 1d |
| S-B | 数据自动下载 + 完整性闸门 | 数据 | **P0** | 1-2d |
| S-C | 实时信号生成入口（模型+特征+阈值+gate）★ | 信号 | **P0** | 3d |
| S-D | live 接 RiskOrchestrator+RiskGuard+kill_switch | 风控 | **P0** | 1-2d |
| S-E | 逐笔交易日志（对齐 OOT trade_cols） | 日志 | **P0** | 1d |
| S-F | 三层一致性对账闸门 ★ | 一致性 | **P0** | 2d |
| S-G | 下单执行 + 拆单 | 执行 | P1 | 2d |
| S-H | 持仓管理（止损/移动止损/horizon/结构 guard） | 执行 | P1 | 1-2d |
| S-I | 盘前自检 checklist | 运维 | P1 | 0.5d |
| S-J | 日/周/月 + 复盘报告 | 报告 | P1 | 2d |
| S-K | 断线容灾 + 隔夜恢复 | 运维 | P1 | 1-2d |
| S-L | 撮合真实性（涨跌停/夜盘/冲击成本） | 真实成交 | P2 | 1-2d |
| S-M | 监控告警（exporter + parity 漂移） | 运维 | P2 | 1d |
| S-N | 资金风控强化（VaR/周回撤/连损 实盘 wire） | 风控 | P2 | 1-2d |

每条 5 字段：目标 / 现状 / 改动 / 验收 / 工作量。

---

## §4 P0 — 接入阻断（先打通"仿真下 1 手且与 OOT 一致"）

### S-A 银河 CTP 仿真前置连接与凭据
- **目标**：用银河仿真账号通过 CTP 连上行情(md)+交易(td)前置，能订阅、能下单、能收回报。
- **现状**：[`sim_credentials_template.py`](../config/sim_credentials_template.py) 现用 SimNow 地址
  （td/md `tcp://180.168.146.187:10130/10131`、broker_id `9999`）；`load_credentials()` 工厂已把
  `broker_id→brokerid` 映射好；`sim_runner.py` 已有 vnpy_ctp 启动链。
- **改动**：
  1. 新建 `cta/config/sim_credentials.py`（**绝不入库**，必须在 `.gitignore`），填银河仿真前置：
     `td_address / md_address / broker_id / userid / password / appid / auth_code`（向银河期货
     仿真平台申请）。模板 `sim_credentials_template.py` 补银河字段注释。
  2. `sim_runner.py` 连接处加：断线重连参数、`appid/auth_code` 鉴权、合约查询(qry_instrument)
     成功才算就绪。
- **验收**：`python3 -m cta.sim.sim_runner --dry-run` 能连上银河仿真、拉到合约列表、订阅 1 个主力；
  CI grep 确认仓库内无真实 `password/auth_code`（防回归）。
- **工作量**：1d。

### S-B 数据自动下载 + 完整性闸门
- **目标**：每个交易日盘后自动增量下载 day/60min/30min → 落 `cta/data/origin/{interval}/{symbol}`
  → 生成 feature / model_feature，且**保证无缺口、无未来数据**。
- **现状**：[`futures_downloader.py`](../data_code/futures_downloader.py)（648 行）已能下载；feature 链
  在 `cta/feature/`；调度多为手动。
- **改动**：
  1. 新建 `cta/data_code/daily_update.py`：封装"增量下 3 个 interval → 算 feature → 算 model_feature"
     一条龙，按 symbol×interval 幂等（已存在跳过）。
  2. 新增**完整性闸门** `cta/data_code/data_integrity_check.py`：校验①品种清单齐全；②每个
     (symbol, interval) 最新 bar 日期 = 上一交易日（用 `trading_calendar`）；③bar 时间戳单调、
     无重复、无跳空缺失；④**无未来数据**（最大 datetime ≤ now）。任一不过 → 阻断当日开盘 + 告警。
  3. 调度：cron（盘后 16:30 日盘 + 次日 03:00 夜盘补）或 `cta/run/` 脚本；不硬编码路径。
- **验收**：跑 `daily_update.py` 后 `data_integrity_check.py` 全绿；故意删一根 bar 能被闸门拦。
- **工作量**：1-2d。

### S-C 实时信号生成入口 ★最大缺口
- **目标**：每根 bar 收盘后，用**已训练模型**对最新数据生成候选机会——跑模型出 prob、查阈值、
  过 gate、决定是否交易——**且与 OOT eval 在同一日的输出逐行一致**。
- **现状**：🔴 **完全缺失**。离线是 train → `predictions.csv` → OOT 复用；没有"加载 model + 今日
  predict"的在线入口。模型存在 `*_model_pipeline/models/`；`online_feature.OnlineFeatureLoader`
  已能加载离线 parquet 特征；阈值在 `score_quantile_manifest`；gate 在 `cta/model/oot/oot_trade_filter_gate.py`。
- **改动**：新建 `cta/live/signal_generator.py`，核心接口：
  ```python
  def generate_today_candidates(
      bars_by_symbol_interval: dict, cfg: OotEvaluationConfig, as_of: pd.Timestamp,
  ) -> pd.DataFrame:  # 返回候选机会（含 trade_filter_prob / *_pctl / 决策列）
  ```
  ① 新建轻量 `cta/live/model_registry.py`：按 (cluster, interval) 加载
     `*_model_pipeline/models/` 的 joblib（含 mtime/age 记录，供 PredictionStaleGuard）；
  ② 用 `OnlineFeatureLoader` 算特征，**必须与离线 train 特征 parity**（复用
     `cta/live/feature_parity.py` 校验，精度 < 1e-6）；
  ③ 模型 predict → `trade_filter_prob` / `final_decision_score` / `pred_regime_label` 等列；
  ④ 查 `score_quantile_manifest` 得 `trade_filter_prob_pctl`——**严禁在实时 batch 现算分位
     （= 特征穿越/泄露红线）**；分位只能来自 train+valid 预算的 manifest；
  ⑤ 直接调**与 OOT 同一份** `apply_trade_filter_gate` + stacking/regime gate（`cta/model/oot/`，
     不重写），产候选机会。
- **验收**：选一个历史交易日，`signal_generator` 的候选/决策与 OOT eval 在该日的输出**逐行一致**
  （symbol/side/prob/pctl/passed 全等）；新增 `cta/live/tests/test_signal_generator_oot_parity.py`。
- **工作量**：3d（本计划最重，是整条链能否一致的根）。

### S-D live 接 RiskOrchestrator + RiskGuard + kill_switch ★B1/M2
- **目标**：live 下单前走与 OOT/sim **同一份** cfg 的风控编排 + 6 条硬拒规则 + 总开关。
- **现状**：🔴 `cta/live/*.py` 零引用 `cta.risk`；`live_runner.py` 仅复用 SimRunner。sim 侧
  `entry_gate_chain` 已接 `RiskOrchestrator.from_config`，`sim_runner._attach_observers` 已挂 RiskGuard。
- **改动**：`live_runner.py` 按同一 `OotEvaluationConfig` 构造 `EntryGateChain` +
  `RiskOrchestrator.from_config` + `RiskGuard`(6 规则) + `KillSwitch`；下单 hook：
  `chain.evaluate(candidate, state_provider, dt, original_lots)` → `passed=False` 不下单，
  `adjusted_lots` 为最终手数；再过 RiskGuard.evaluate + kill_switch。
- **验收**：同一 cfg 下 live 与 sim/entry_gate_chain 对同一 candidate 给出**相同 GateDecision**；
  新增 `cta/live/tests/test_live_risk_wire.py`；CI grep `from cta.risk` 在 live_runner 必须命中。
- **工作量**：1-2d。

### S-E 逐笔交易日志（对齐 OOT trade_cols）
- **目标**：每个 order / trade / 决策都落**结构化日志**，列尽量对齐 OOT，使实盘逐笔可直接与
  OOT `trade_details.csv` 比对。
- **现状**：OOT trade schema 在 [`pipeline_oot_evaluation.py:33`](../model/oot/pipeline_oot_evaluation.py)
  `trade_cols`（~110 列：datetime/symbol/side/execution_status/block_reason/entry_fill_price/
  net_pnl/cost_pct/trade_filter_prob/trade_filter_prob_pctl/risk_block_reason/... ）；实盘侧无统一 log。
- **改动**：新建 `cta/live/trade_logger.py`：定义与 `trade_cols` 同名子集的实盘日志 schema，
  逐笔写 `cta/run/state/live_trades_<date>.csv`（+ 决策快照 jsonl：candidate/gate/risk/最终 lots）。
  共享 schema 常量抽到 `cta/model/oot/` 或 `cta/config/` 供两侧引用（单一定义源）。
- **验收**：跑一日 sim，`live_trades_*.csv` 列名 ⊆ OOT trade_cols，可被对账脚本直接 join。
- **工作量**：1d。

### S-F 三层一致性对账闸门 ★用户第 4 点核心
- **目标**：强制 **OOT = sim = live 同 cfg 同代码**，并逐笔验证仿真成交与 OOT 预期一致。
- **现状**：cfg_fingerprint 已在 OOT bundle dump（argv+generated_at+cfg）；parity 基建有
  （`feature_parity_sim_soak.py` + parity 测试群）；但**三处比对闸门 + 逐笔 decision parity 未串起来**。
- **改动**：
  1. sim run / live run 也 dump `cfg_fingerprint.json`（复用 `eval_only_run._dump_cfg_fingerprint`）；
     新建 `cta/sim/cfg_consistency_check.py`：比对 OOT / sim / live 三处 fingerprint 关键字段
     （use_portfolio_logic_runtime / commission / bond_filter / htf_fallback / risk_system / initial_capital），
     差异即告警（呼应 better C3）。
  2. 逐笔 decision parity：对"同一候选"比对 sim/live 实际 decision（passed / adjusted_lots /
     block_reason）与 OOT 预期 decision，容差内才算一致；复用 `feature_parity_sim_soak` 比对模式。
  3. 把它做成 **soak 闸门**：连续 N 日 parity 全过才允许放量（见 §8）。
- **验收**：构造 OOT vs sim 同输入，三处 fingerprint 一致 + 逐笔 decision 一致；故意改一个 cfg
  字段能被 fingerprint check 抓到；新增 `cta/sim/tests/test_three_layer_consistency.py`。
- **工作量**：2d。

---

## §5 P1 — 完整闭环

### S-G 下单执行 + 拆单
- **目标**：大单按规则拆成子单，降低冲击、贴近真实成交。
- **现状**：🟡 无拆单实现（grep 无 iceberg/twap/split）；`order_lifecycle.py` 已有订单态机骨架。
- **改动**：新建 `cta/live/order_slicer.py`：输入最终 lots（来自 `cta/risk/sizing`），按
  `max_order_volume`（单笔上限）/ ADV 参与率（child_lots / adv_lots ≤ 参与率上限，复用 better C1
  的 impact/ADV 口径）拆成 iceberg（隐藏量）或 TWAP（时间均摊）子单；每个子单经 `order_lifecycle`
  跟踪 6 态（提交/部分成交/全成/撤单/拒单/超时）；未成回报超时自动改价/撤单。
- **验收**：模拟一个 > max_order_volume 的大单被拆成 N 子单且参与率不超限；
  新增 `cta/live/tests/test_order_slicer.py`。
- **工作量**：2d。

### S-H 持仓管理（止损 / 移动止损 / horizon / 结构 guard）
- **目标**：实盘持仓与 OOT 同款退出逻辑 + 中国市场结构约束。
- **现状**：`position_evaluator.py`（hard_stop P1-13）+ `trailing_exit.py`（ATR 移动止损 + horizon）
  已存在；W6 结构 guard（涨跌停 LimitMove / 换月 RolloverFreeze / 流动性 LiquidityFloor）已实现，
  但实盘 wire 未串。
- **改动**：live on_bar 持仓阶段调 `PositionEvaluator` + `simulate_trailing_exit` 同款逻辑（流式版）；
  开仓前过 LimitMoveGuard / RolloverFreezeGuard / LiquidityFloorGuard（已实现，按 §7 同 cfg 接入）。
- **验收**：触发涨跌停/换月窗口的开仓被拦；移动止损与 OOT 同参数；新增持仓管理集成测试。
- **工作量**：1-2d。

### S-I 盘前自检 checklist
- **目标**：开盘前一票否决式自检，任一不过不开盘。
- **现状**：单点能力散落（PredictionStaleGuard / kill_switch / trading_calendar / margin_reconciler）。
- **改动**：新建 `cta/live/preopen_checklist.py`：①数据新鲜度（predictions/特征 mtime）；②模型 age；
  ③换月日历（今日是否在 freeze 窗）；④保证金充足（可用资金 ≥ 预估占用）；⑤kill_switch=off；
  ⑥昨日对账平。输出 go/no-go + 明细日志。
- **验收**：6 项任一造假能 no-go；新增测试。
- **工作量**：0.5d。

### S-J 日 / 周 / 月 + 复盘报告 ★用户第 5 点
- **目标**：每日收盘出日报；周末出周报；月末出月报；定期出复盘报告（实盘 vs OOT 归因）。
- **现状**：OOT 报告器 `oot_report_writer.py`（headline / by_cluster / by_symbol / summary）成熟，
  未接实盘；实盘无报告。
- **改动**：新建 `cta/live/reporting/`：
  - `daily_report.py`：当日成交数/盈亏/持仓/风控触发次数/parity 结果/资金曲线点；
  - `periodic_report.py`：周/月聚合（复用 `oot_report_views` 的 cluster/symbol 聚合 + 收益质量
    诊断列，呼应 better B2：剔 top-N 后年化 + 集中度）；
  - `review_report.py`：**复盘** = 实盘 net vs OOT 预期 net 三段归因——①信号差异（候选不一致）
    ②成交差异（滑点/拆单/拒单）③风控差异（实盘 guard 拦截）。
- **验收**：跑一日 sim 出 daily_report；复盘报告能把"实盘-OOT"差额拆成三段且求和闭合。
- **工作量**：2d。

### S-K 断线容灾 + 隔夜恢复
- **目标**：行情/交易断线自动重连；进程重启后恢复隔夜持仓/净值/未完成订单。
- **现状**：`EquityTracker.bootstrap_from_broker` 已支持净值恢复；`margin_reconciler` 已有对账骨架；
  `supervisor.py` 进程守护。
- **改动**：md/td 断线指数退避重连 + 重连后重新订阅/查持仓；启动时 `bootstrap_from_broker` +
  `margin_reconciler` 对账（broker 持仓 vs 本地账本不一致 → 告警 + 冻结开仓）；未完成订单查询并接管。
- **验收**：模拟断线能重连恢复；重启后持仓/净值与 broker 一致。
- **工作量**：1-2d。

---

## §6 P2 — 精细化

### S-L 撮合真实性
- 涨跌停板不成交、夜盘时段（`trading_calendar` 已有）、冲击成本回填（better C1）、对手价/排队
  撮合近似；让仿真成交价更贴近真实。**工作量** 1-2d。

### S-M 监控告警
- `monitoring.py` exporter（Prometheus / 钉钉 webhook）：心跳、持仓、当日 PnL、风控触发、
  **parity 漂移**（sim/live 偏离 OOT 超阈值即告警）。**工作量** 1d。

### S-N 资金风控强化（实盘 wire）
- 把 W7-W10 的 DailyVaRBudget / 周回撤线性降仓 / ProfitGiveBack / 连损冷却 在 live 实盘 wire
  （组件已实现，按 §7 同 cfg 接入）。**工作量** 1-2d。

---

## §7 三层一致性铁律（OOT = sim = live）

1. **单一 cfg 来源**：三层都由**同一个 `OotEvaluationConfig` 实例**构造
   `EntryGateChain` / `RiskOrchestrator` / `RiskGuard`；live 不得另维护一份阈值/成本/fallback。
2. **同一套代码**：信号 gate 直接调 `cta/model/oot/` 函数（`apply_trade_filter_gate` 等），
   退出逻辑调 `trailing_exit` / `position_evaluator`，风控调 `cta/risk/`——**禁止在 live 重写**。
3. **cfg_fingerprint 三处比对**：OOT bundle / sim run / live run 各 dump fingerprint，
   `cfg_consistency_check.py` 比对关键字段，差异即告警（S-F）。
4. **逐笔 decision parity**：同候选下 sim/live 实际决策 == OOT 预期决策（容差内）。
5. **CI 防分叉**：grep 确保 live 引用 `cta.risk` + `cta.model.oot`，不出现重复实现的 gate/risk。

---

## §8 上线灰度门槛（soak）

复用 [`cta/live/soak_gate.py:RollbackChecklist`](../live/soak_gate.py)：

```
阶段 0：dry-run（连仿真、订阅、出信号、不下单）——验证 S-A/S-B/S-C 链路通
阶段 1：1 手 soak ≥ 5-10 交易日，必须满足：
        ① 逐笔 decision parity 全过（S-F）
        ② cfg_fingerprint 三处一致
        ③ margin_reconciler 每日对账平
        ④ 无 kill_switch 触发、无未捕获异常
        ⑤ 实盘 net vs OOT 预期 net 偏离在容差内（滑点可解释）
阶段 2：按 cta/risk/sizing 正常手数放量；周回撤/VaR/连损 guard 全程在线
任一门槛不过 → 回滚到上一阶段 + 复盘报告定位差异
```

---

## §9 落地顺序 + 依赖图

```
W1（打通"能连、能出信号、与 OOT 一致"）：
  S-A 连接 → S-B 数据+闸门 → S-C 信号生成★ → dry-run 跑通；S-E 日志 并行
W2（风控 + 对账）：
  S-D live 接 risk → S-F 三层对账★ → 跑通 1 手 sim 全过 parity
W3（执行 + 报告 + 容灾）：
  S-G 拆单 → S-H 持仓管理 → S-I 盘前自检 → S-J 报告 → S-K 容灾
W4（灰度）：
  阶段 0/1 soak ≥ 5-10 日 → 阶段 2 放量；S-L/S-M/S-N 精细化穿插
```
**依赖图**：S-C ← models/ + online_feature + score_quantile_manifest（先确保特征 parity）；
S-D ← entry_gate_chain + RiskOrchestrator（已有，接线）；S-F ← S-C + S-E + cfg_fingerprint；
S-G ← cta/risk/sizing 输出 lots + order_lifecycle；S-J ← oot_report_writer + S-E 日志。

---

## §10 风险与回退

| # | 风险 | 应对 |
|---|------|------|
| R1 | 银河仿真前置不稳/限频 | 断线指数退避重连（S-K）；下单限频 OrderRateLimit（已有 _BaseRule）|
| R2 | 数据缺口/未来数据穿越 | S-B 完整性闸门一票否决；防泄露铁律 |
| R3 | **信号与 OOT 不一致** | S-C 逐行 parity 测试为硬验收；特征 parity < 1e-6；同一 gate 代码 |
| R4 | 拆单滑点/部分成交 | S-G 参与率上限 + 超时改价撤单；S-L 撮合真实性 |
| R5 | 隔夜跳空/换月强平 | S-H 节假日/换月 guard + 隔夜减仓；S-K bootstrap 恢复 |
| R6 | cfg 漂移（命名失真历史教训）| S-F fingerprint 三处比对 + CI grep |
| R7 | 模型/数据过期 | 盘前 PredictionStaleGuard（S-I）|

---

## §11 不变量（接入全程不可破坏）

1. **防特征穿越 / 标签泄露**：实时分位只能查 train+valid 预算的 `score_quantile_manifest`，
   严禁在实时 batch 现算 rank/quantile；任何用到未来 bar 的特征一律禁止。
2. **OOT = sim = live 同 cfg 同代码**：单一 `OotEvaluationConfig` 来源 + 复用 OOT/risk 函数，
   不在 live 重写 gate/risk/exit。
3. **fail-open 但不盲开**：风控/数据缺失时**宁可不开仓**（pre-open no-go），不可带病下单。
4. **凭据绝不入库**：`sim_credentials.py` 必须在 `.gitignore`；CI grep 防真实 password/auth_code 回归。
5. **cta/ 边界**：只改 `cta/**`；不动交易网关底层、不动历史回测产物（只读）。
6. **变更管理**：每个 S-* 实现后更新 `cta/report/change_log.md`（改了什么/怎么跑/结果在哪/风险）。

---

## §12 一句话收尾

接银河仿真的**脊梁是 S-C（实时信号）+ S-F（三层一致对账）**：把"加载模型→今日出信号"这条
在线链补上，并用 cfg_fingerprint + 逐笔 parity 钉死"仿真必须复现 OOT"。这两条通了，剩下的
连接/拆单/报告/容灾都是工程拼装。**先 dry-run，再 1 手 soak，parity 全过再放量**——
平稳接入的唯一正确姿势。
