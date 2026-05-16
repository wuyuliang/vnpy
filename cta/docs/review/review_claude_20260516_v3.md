# Code Review v3（2026-05-16）— 上线代码深度审查

> **重要前提**：这是直接对接 CTP / 实盘下单的代码。每一项 Critical 都对应**真金白银的风险**。本次按"假设要明天上 1000 万人民币"的视角挑刺，不放过任何"运气好不出事"的代码。
>
> 范围：cta/portfolio_logic/ 9 个新文件、cta/sim/ + cta/live/ runner、cta/model/cluster_model_registry.py 与 pipeline_oot_evaluation.py 的 portfolio_logic 集成层。
>
> 严重程度：
> - **🔴 Critical**：上线就出事——超额下单、止损失效、状态紊乱、重复下单
> - **🟠 High**：实施盲点 + 容易引发线上事故
> - **🟡 Medium**：边界/容错；非紧急但需修
> - **🟢 Low**：表述/命名
>
> 合计 **34 项**（Critical:9 / High:11 / Medium:9 / Low:5）

---

# Part A: 设计 vs 代码的根本性脱节（Critical 区）

## 🔴 C-1. `PortfolioState` 缺 5 个关键方法，状态会永久泄漏

**位置**：[portfolio_state.py:1-79](../portfolio_logic/portfolio_state.py)

**事实**：当前 PortfolioState 只有 `snapshot_for_allocation` + `tentative_apply` + 一堆 `tentative_*` 查询。**没有**：
- `commit_allocation()`：tentative → 主状态
- `rollback_allocation()`：丢弃 tentative
- `apply_exits(exits)`：layer 退出时扣减计数
- `add_position(pos)` / `remove_position(pos_id)`：增删 PyramidPosition
- `record_trades(trades)`：交易日志

设计文档（[portfolio_logic_design.md §4.5](portfolio_logic_design.md#L106)）明确要求这些方法，**代码里全没有**。

**线上后果**：
1. ranker.allocate 写 tentative_*，但**永远不会同步到主 symbol_counts/cluster_notional** → 风控计数永远停在 0
2. layer 退出时没有任何代码扣减 cluster_notional → cluster 50% 上限永久虚高
3. 重启时 trade_log 丢失 → 无审计追溯

**修复**：补齐 5 个方法（review v2 §4.5 已给完整模板）。**这是 W0 必须的**。

## 🔴 C-2. `snapshot_for_allocation` 只在 tentative 为空时触发，跨 bar 数据污染

**位置**：[opportunity_ranker.py:149-150](../portfolio_logic/opportunity_ranker.py:149)

```python
if not state._tentative_symbol_counts:
    state.snapshot_for_allocation()
```

**事实**：
- 第一根 bar：tentative 是空字典 → `snapshot_for_allocation()` 触发，复制空主状态 → tentative 空
- 第一根 bar ranker.allocate 跑完：tentative_apply 加了几个 picks → tentative 非空
- 第二根 bar：`if not _tentative_symbol_counts` = False → **不再调 snapshot** → tentative 继承上根 bar 的虚假 picks
- 主状态从未更新 → tentative 永远基于"虚构历史" cap 检查

**线上后果**：
- 一个 bar 配额 10 仓，跑 100 个 bar 后会"用完"配额，但实际持仓可能为 0（因为主状态从未更新）
- **风控完全失效**

**修复**：每次 allocate 入口**强制** `state.begin_allocation()`，并在 ranker 返回后**显式** `commit_allocation()`。当前的 `if not _tentative_*` 这种 hack 应直接删除。

## 🔴 C-3. `sim_runner` / `live_runner` **完全没接** portfolio_logic 决策

**位置**：[sim_runner.py:86-165](../sim/sim_runner.py:86) + [live_runner.py:107-133](../live/live_runner.py:107)

**事实**：
- `sim_runner` 仅把 `portfolio_logic_flags` 作为 dict 透传到 `strategy_setting`（line 156-165）
- `live_runner` 完全复用 sim_runner 配置，没接 PortfolioState、HtfGate、PyramidManager、RiskThrottle
- 下单路径直接走 vnpy MainEngine → CTP，**绕过所有 portfolio_logic 安全模块**
- 没有从磁盘恢复 PortfolioState 的代码

**线上后果**：**这是最严重的 bug**——
- 在用户启用 portfolio_logic 的预期下上线，实际上交易完全没有：HTF gate、ranker 排序、trailing stop、金字塔加仓控制、cluster notional 上限、回撤档位降仓
- 等于把"全力跑"的策略接到生产，且认为有风控
- 一次大行情反向就足以重创账户

**修复**：必须在 live_runner 启动时：
```python
# 1) 模型加载
registry = ClusterModelRegistry.from_run_tag(run_tag)
calibrator = registry.get_calibrator()
# 2) 状态恢复
state = PortfolioState.load_snapshot(...) or PortfolioState.bootstrap_from_broker(broker)
tracker = EquityTracker.bootstrap_from_broker(...)
# 3) 决策管线注入到 strategy 的 on_bar 中
strategy.set_portfolio_logic(
    gate=HtfGate(cfg.interval_gate),
    ranker=OpportunityRanker(cfg.ranker),
    trailing=TrailingExitSimulator(cfg.trailing),
    pyramid=PyramidManager(cfg.pyramid),
    throttle=RiskThrottle(cfg.risk_throttle),
    state=state, tracker=tracker, registry=registry,
)
# 4) 每个 bar 走 5 步流程（§5）后才下单
```

**这条不修，portfolio_logic 整个项目对实盘无意义。**

## 🔴 C-4. `ClusterModelRegistry` 没有加载 calibration 文件

**位置**：[cluster_model_registry.py](../model/cluster_model_registry.py) + [score_calibrator.py](../portfolio_logic/score_calibrator.py)

**事实**：
- `score_calibrator.py` 类已实现 `to_percentile / to_edge_z / transform`
- 但 `cluster_model_registry.py` 没有加载 `*_calibration.joblib` 的代码
- 训练侧 model_pipeline.py 也没有 fit calibration 的钩子（grep `score_calibrator` / `fit_calibration` 无结果）
- 设计 [§13.1 portfolio_logic_design.md](portfolio_logic_design.md#L1432) 要求 registry.load() 一并加载校准

**线上后果**：
- calibration joblib 永远不会被生成、加载
- ranker.score 公式中 `trade_filter_prob_pctl` 列会是 NaN 或缺失 → score 公式整个崩
- 等于 ranker 完全失效

**修复**：
1. `model_pipeline.py` 每个 gate model 训练完调 `ScoreCalibrator.fit_for_holdout()` 落盘
2. `ClusterModelRegistry.load()` 自动加载同目录 `*_calibration.joblib`
3. `predict_proba()` 输出 raw + `_pctl` 列

## 🔴 C-5. Layer 的 `layer_stop_price` 是单字段，trailing 会覆盖 hard_stop

**位置**：[pyramid_manager.py:24](../portfolio_logic/pyramid_manager.py:24)

```python
@dataclass
class Layer:
    ...
    layer_stop_price: float          # 单一字段！
    trailing_activated: bool = False
```

**事实**：
- Layer 只有一个 `layer_stop_price` 字段
- design 文档 v4 §9.1 要求拆 `hard_stop_price + trail_stop_price`，`effective_stop = max(hard, trail)` 保住地板
- 当前 simulate_trailing_exit 用本地 `cur_stop` + `max(cur_stop, new_stop)`——但 cur_stop 是本地变量，**不持久化到 Layer**

**线上后果**：
- pyramid_manager 创建 Layer 时设了 hard_stop 到 layer_stop_price
- 后续如果有代码（pipeline_oot_evaluation）调 update_layer_stop 写回 layer_stop_price，且 trailing 计算出来的 new_stop **大幅低于 hard_stop**（ATR 很大、波动品种），update 会让 stop "变松"（虽然 update_only_in_favor 应该防住，但风险窗口存在）
- 更严重：simulate_trailing_exit **完全没读 Layer.layer_stop_price**，是独立的本地 cur_stop 计算 → Layer 的 trail 状态机就是死代码

**修复**：
- Layer 拆 `hard_stop_price: float` + `trail_stop_price: float = -inf (long) / +inf (short)`
- 加 `effective_stop` property
- simulate_trailing_exit 接收 Layer 对象并 mutate 它的 trail_stop_price

## 🔴 C-6. `pyramid_manager` 用 `cooldown_minutes` 而非 `cooldown_bars_per_interval`

**位置**：[pyramid_manager.py:132](../portfolio_logic/pyramid_manager.py:132)

```python
if elapsed_min < float(self.cfg.cooldown_minutes):
    return False
```

**事实**：
- design 文档 v4 §9.2 明确：cooldown 改为 `cooldown_bars_per_interval: dict[str, int]`（day=3 bars、5min=12 bars 等），避免对 day 周期形同虚设
- 代码用了固定 `cooldown_minutes`（单一值）

**线上后果**：
- 默认 30 分钟意味着 day 周期信号可以 30 分钟内连加 2 层（不到一根 day bar）
- 同 day 信号狂加 layer 把整个 pos 短期化
- min 周期 30 分钟才能加一层，但 min 不应加层（design 排除了）

**修复**：按 design 文档实现 `cooldown_bars_per_interval` + `interval_to_minutes` 转换。

## 🔴 C-7. `simulate_trailing_exit` 是 per-trade 独立模拟，与多 layer 状态机脱节

**位置**：[trailing_exit.py:53-203](../portfolio_logic/trailing_exit.py:53)

**事实**：
- simulate_trailing_exit 接收 entry_ts / planned_exit_ts / bars，模拟一笔交易从入到出
- 内部维护本地 `cur_stop / running_high / trailing_activated`
- **不接收 PyramidPosition 或 Layer 对象**
- 不更新任何 state
- design 要求"per-layer trailing in shared running_high"，需要持久化 layer state + 共享 pos.running_high

**线上后果**：
- 当前实现适合 OOT 批量回测一笔笔走（已有 candidate 表 entry+horizon）
- 但 sim/live 是流式：bar tick 来了要查"我所有持仓的每个 layer 的 stop 是否被穿透"
- 当前函数无法支撑流式调用——每次都要重新模拟整段 bar
- 多 layer 共享 running_high 也没法实现

**修复**：另起一个 `class TrailingExitSimulator`，方法 `update_one_bar(positions, current_bar, regime_map)`，mutate 每个 Layer 的 trail_stop_price 并返回 LayerExitEvent 列表。原 `simulate_trailing_exit` 留给 OOT batch 模式。

## 🔴 C-8. 无订单幂等性 / 无重启状态恢复

**位置**：[live_runner.py](../live/live_runner.py) 整体

**事实**：
- 没有 `client_order_id` / 订单去重逻辑
- 没有 PortfolioState 序列化到磁盘
- 没有 "broker 持仓 vs local state 校对"
- `supervisor_max_reconnects=100` 是参数，但没看到对应实现

**线上后果（真金白银场景）**：
- 网络闪断重试时同一订单可能 send 两次 → 双倍仓位
- 进程崩溃重启后 portfolio_logic 认为账户是 empty，开始新一轮交易；实际账户已有持仓 → 超额下单
- broker 异步 reject 一笔订单后本地 state 仍认为已成交 → 风控基于错误状态做决策

**修复**：
- 订单提交前生成 `client_order_id = f"{strategy_id}_{bar_ts}_{symbol}_{direction}_{layer_id}"`，broker side 去重
- 每个 base_interval bar 收盘后 dump PortfolioState 到 `state_snapshot.json`
- 启动时强制做 broker reconciliation（design §21 已写但代码没做）

## 🔴 C-9. 没有 broker 真实持仓校对

**位置**：[live_runner.py](../live/live_runner.py) 整体

**事实**：上节相关。设计 §21.3 要求 broker 真实持仓必须与 state.positions 校对，代码无任何实现。

**线上后果**：
- 程序 crash → 重启 → state 是空（C-8） → 开始新仓
- 这时账户里其实还有上次的仓（broker 没记 logout）
- 双倍暴露

**修复**：启动时调 broker REST API 拿 positions，与 state 对比；不一致则 fatal 退出 + 人工介入。

---

# Part B: 实施盲点（High 区）

## 🟠 H-1. EquityTracker 类是否真存在、是否实现 bootstrap

**位置**：[risk_throttle.py](../portfolio_logic/risk_throttle.py)（待核验）

**事实**：v2 review P2-8 要求 `bootstrap_from_broker(initial_equity, history_path=None)`。设计 §10.3.1 也定义。**未发现实现**。

**修复**：实现该类方法，否则 live 重启后 weekly/monthly_return 计算全错。

## 🟠 H-2. `regime_extend / horizon_extend` 在 simulate_trailing_exit 没实现

**位置**：[trailing_exit.py:53-203](../portfolio_logic/trailing_exit.py)

**事实**：design §8.4 HorizonExtendConfig 在文档存在，但 simulate_trailing_exit 没用 `extensions_used` / `max_extensions` 概念。看不到 horizon 延长代码。

**线上后果**：always-in-long 行情下持仓仍按 horizon 强制下车 → P1 问题（design 解决的第 3 大痛点）未真正实现。

**修复**：simulate_trailing_exit 接收 `horizon_cfg`，到期前 1 bar 检查 regime 仍同向则延 N bars。

## 🟠 H-3. `force_close_all` 在 halt throttle 进入时未挂接

**位置**：[risk_throttle.py](../portfolio_logic/risk_throttle.py) + 主循环

**事实**：design v4 §10.3.2 + §5 要求 halt 档进入时 force_close_all。pyramid_manager 没有该方法（grep `force_close_all` 无结果）。

**线上后果**：DD ≥ 15% 进入 halt 档时，**已开仓位不会被强制平掉**——不符合 design"halt = 仅退出且一次性清仓"语义。

**修复**：PyramidManager 加 `force_close_all(pos, exit_price, exit_time, reason)`，主循环 C 阶段开头判断 throttle_level.name == "halt" 时调用。

## 🟠 H-4. `HtfGate` state TTL / `fallback_when_htf_missing` 是否真生效

**位置**：[interval_gate.py](../portfolio_logic/interval_gate.py)（待核验）

**事实**：design v4 §6.2 要求 state TTL（day=86400s、60min=3600s）和过期 fallback。需要核实代码实现。

**修复**：grep `state_ttl_seconds` / `is_state_fresh` 在 interval_gate.py，确认实现。

## 🟠 H-5. `OpportunityRanker` 没接收 `min_prob_pctl` 双阈值

**位置**：[opportunity_ranker.py:140-145](../portfolio_logic/opportunity_ranker.py:140)

**事实**：`allocate` 签名只有 `score_threshold`，**没有 `min_prob_pctl`**。
design v4 P0-2 明确要求双阈值（综合分 + prob 百分位 AND）。

**线上后果**：
- ThrottleLevel.min_prob_pctl 字段在 risk_throttle.py 存在，但 ranker 接不到
- 风控档 conservative=90 pctl 设定形同虚设

**修复**：allocate 加 `min_prob_pctl: float` 参数，filter `df[df.trade_filter_prob_pctl >= min_prob_pctl]`。

## 🟠 H-6. `_base_notional` 写死 `state.equity * 0.10`

**位置**：[opportunity_ranker.py:118](../portfolio_logic/opportunity_ranker.py:118)

```python
return float(state.equity) * 0.10
```

**事实**：硬编码 10%。design 期望读 `OotEvaluationConfig.max_position_scale`（也是 0.10 但应该可配置）。

**线上后果**：调 `max_position_scale=0.08` 没用。

**修复**：通过 `OpportunityRankerConfig` 暴露 `base_notional_pct: float = 0.10`。

## 🟠 H-7. 无 Order Idempotency / 网络重试时双倍下单

（与 C-8 重复维度，列在 High 强调具体实现路径）

**修复**：vnpy `OrderRequest` 自带 `reference: str` 字段，可作 client_order_id；要求所有 send_order 都带上唯一 reference 并 broker 校验。

## 🟠 H-8. `test_oot_sim_parity.py` / `test_backward_compat.py` 未创建

**位置**：[cta/portfolio_logic/tests/](../portfolio_logic/tests/)（27 test 用例 in 7 文件）

**事实**：v4 design + v2 review 要求的 2 个关键测试缺失。

**线上后果**：
- 无法证明 OOT/sim/live 行为一致
- 无法证明 `enable_*=all False` 时与 baseline 一致

**修复**：实现这 2 个测试，**parity test 必须每次 CI 跑**。

## 🟠 H-9. 校验 `intrabar_stop_loss_pct` 一致性的 hook 是否真生效

**位置**：[model_pipeline.py:1075](../model/model_pipeline.py:1075)（agent 报告位置）

**事实**：agent 说已实现，需核实。如果 hook 只在 model_pipeline.main 入口跑，sim/live runner 启动**不会跑** → live 配置漂移仍可能发生。

**修复**：把 `_validate_stop_loss_pct_consistency()` 移到 OotEvaluationConfig.__post_init__，**任何 config 实例化都强制校验**。

## 🟠 H-10. Float 精度风险（金额比较）

**位置**：[opportunity_ranker.py:130-135](../portfolio_logic/opportunity_ranker.py:130)

```python
symbol_cap = float(caps.max_symbol_notional_pct) * float(state.equity)
room_symbol = symbol_cap - state.tentative_symbol_notional(sym_key)
```

**事实**：用 float 做金额累加 + 比较。equity = 10,000,000 + 多次 +/- 后浮点误差可能累积到 0.0001 元。

**线上后果**：
- 边界附近（如 cluster_notional = 5,000,000.001 vs cap=5,000,000.000）小数点误差导致拒绝合法下单
- 不会"溢出"成大问题，但会"卡单"

**修复**：所有金额比较加 epsilon（`abs(a - cap) < 0.01` 视为相等）；或在新建 portfolio 时改 Decimal。**建议保留 float + epsilon**，Decimal 改造成本太大。

## 🟠 H-11. 无 Holiday / Trading Calendar 校验

**位置**：[interval_gate.py / trailing_exit.py](../portfolio_logic/)

**事实**：bar.datetime 直接做时间算术（`current_time - last_layer.entry_time`），节假日跨度变长会让 cooldown 计算虚增。

**线上后果**：
- 周五尾盘开 layer，周一开盘想加 layer，elapsed_min 看起来 4000 分钟（包含周末），通过 cooldown
- 但策略原意是"间隔 N bars"，周末不算
- 这与 C-6 cooldown_minutes → cooldown_bars 修复后会缓解，但仍需 trading_calendar

**修复**：用 `vnpy` 自带交易日历计算"两个时间点之间的有效交易分钟数"。

---

# Part C: 边界与容错（Medium 区）

## 🟡 M-1. `simulate_trailing_exit` 不处理 limit-up / limit-down

bar_low / bar_high 触发 stop 时直接成交，没考虑涨跌停（实际市场打停板时无法成交）。

**修复**：传入 `limit_up_price / limit_down_price`，若 stop 在板内则等到下一根 bar。

## 🟡 M-2. `simulate_trailing_exit` 没有 slippage / volume constraint

策略以 `cur_stop` 价成交 100 手，但实际市场该价位可能只有 10 手挂单。OOT 高估了执行能力。

**修复**：可选参数 `max_size_per_bar`，超出则部分成交+剩余继续在下一根 bar 平。

## 🟡 M-3. `score_calibrator.transform` 用 `.apply(axis=1)` 在长 DF 上慢

[score_calibrator.py:80-100](../portfolio_logic/score_calibrator.py:80)（agent 报告）

OOT 13 年 60min 数据约 30000 行，逐行 apply 跑 4 个模型校准 → 慢。

**修复**：vectorize：先按 (cluster, interval) 分组，每组一次 numpy.searchsorted。

## 🟡 M-4. `htf_alignment` 列在 ranker.score 没用上

design 评分公式有 `w_align * htf_alignment_bonus`，需核实代码是否真消费该列。

## 🟡 M-5. `dedup_same_symbol_same_direction` 用 `str(direction).lower()` 比较

[portfolio_state.py:39](../portfolio_logic/portfolio_state.py:39) 用 `str(direction)`。
[opportunity_ranker.py:162](../portfolio_logic/opportunity_ranker.py:162) 用 `str(...).lower()`。

不一致。若 ranker 写入"Long" 而 state 查询"long"，去重失效。

**修复**：portfolio_state 也 `.lower()`。

## 🟡 M-6. opportunity_ranker.allocate `df_scored.empty` 早退分支返回 `df_scored.copy()`，没含 `allocated_notional` 列

下游消费 picks 时若做 `picks["allocated_notional"]` 会 KeyError。

**修复**：早退路径返回 `pd.DataFrame(columns=[...原列..., "allocated_notional"])`。

## 🟡 M-7. trailing_exit 入场前一根 bar 的 high/low 没有正确处理

[trailing_exit.py:142-145](../portfolio_logic/trailing_exit.py:142) 在 entry bar 上把 bar_high 纳入 running_high，但 bar_high 可能是 entry 时点之前的（intrabar 看不见）。可能高估 trailing 激活。

**修复**：第一根 bar 用 close 作为 high/low；从第二根开始用真正的 high/low。

## 🟡 M-8. score_threshold = 0.0 时所有候选都通过

OpportunityRanker 没有 `score_threshold > 0` 的 assert。如果 ThrottleLevel.score_threshold=0 配置错传，所有低分候选都开仓。

**修复**：`assert score_threshold > 0.0, "score_threshold must be positive"`。

## 🟡 M-9. cluster_model_registry 加载失败时一个 cluster 不影响其他

agent 报告已实现（try/except 在 354-415 行）。**需要测试覆盖**——只 try-except 没用，要有 unit test。

---

# Part D: 文档/工程质量（Low 区）

## 🟢 L-1. `Layer` / `PyramidPosition` 没有 `to_dict / from_dict`

序列化到磁盘做 snapshot 必需。design §21.1 已要求，代码没实现。

## 🟢 L-2. logging level 配置不一致

部分模块 logger.warning，部分 print。生产建议统一 loguru + structured logging（JSON），方便 ELK 检索。

## 🟢 L-3. 单测覆盖的边界 case 不全

agent 报告：empty registry / 损坏 JSON / schema 缺列 / weekly_return None / regime range 不激活 trailing 等 case 缺测试。

## 🟢 L-4. 类型注解不一致

部分 `dict | None`（PEP 604），部分 `Optional[dict]`。统一为 PEP 604。

## 🟢 L-5. docstring 多数模块只有一行

`OpportunityRanker.allocate` 这种生产关键路径应有详细 docstring 说明参数边界、副作用（修改 state）、返回 schema。

---

# 总结：上线前必修清单（Critical 8 项 + High 11 项）

## P0 — W0 之前必修（不修则不能上 sim）

| # | 影响 |
|---|------|
| C-1 PortfolioState 补 5 个方法 | 状态完全无法更新 |
| C-2 删除 `if not _tentative_*` 跨 bar 数据污染 | 风控失效 |
| C-3 sim/live runner 接入 portfolio_logic | 实盘绕过所有安全模块 |
| C-4 calibration 加载与生成全链路 | ranker 评分整个失效 |
| C-5 Layer 拆 hard_stop + trail_stop | trailing 可能覆盖 hard stop |
| C-6 cooldown 按 interval bars 计算 | day 加仓失控 |
| C-7 TrailingExitSimulator 接 Layer 状态机 | sim/live 流式无法用 |
| C-8 order idempotency + state snapshot | 网络闪断双倍下单 |
| C-9 broker reconciliation | 重启后双倍暴露 |

## P1 — W1 之前必修（不修则不能上 live）

| # | 影响 |
|---|------|
| H-1 EquityTracker.bootstrap_from_broker | live 重启后 dd 计算错 |
| H-2 horizon_extend 实现 | 趋势保不住 |
| H-3 halt 档 force_close_all | DD 15% 时仓位不被强平 |
| H-4 HtfGate TTL 实现核实 | 用过期 HTF 状态 |
| H-5 ranker 双阈值 min_prob_pctl | 风控档收紧失效 |
| H-6 base_notional 可配置 | max_position_scale 无效 |
| H-8 parity + backward_compat 测试 | 行为漂移无法检测 |
| H-9 stop_loss consistency 强制校验 | 配置漂移导致 stop_loss 偏差 10x（曾经的 P0 bug 复发） |
| H-10 金额比较加 epsilon | 边界卡单 |
| H-11 trading calendar | 周末跨度让 cooldown 失效 |

---

# 给 codex 的最终检查清单（按依赖顺序）

```
[ ] W0.1 PortfolioState 补齐 commit/rollback/apply_exits/add_position/remove_position/record_trades
[ ] W0.2 OpportunityRanker.allocate 改用 begin_allocation + commit_allocation
[ ] W0.3 OpportunityRanker.allocate 增加 min_prob_pctl 参数
[ ] W0.4 Layer 拆 hard_stop_price + trail_stop_price + effective_stop
[ ] W0.5 PyramidConfig.cooldown_bars_per_interval + interval_to_minutes 转换
[ ] W0.6 PyramidManager.force_close_all 实现
[ ] W0.7 TrailingExitSimulator (类) 接 Layer 状态机 + 流式 update_one_bar
[ ] W0.8 EquityTracker.bootstrap_from_broker
[ ] W0.9 ScoreCalibrator 集成进 ClusterModelRegistry (load + predict_proba 自动注入 _pctl 列)
[ ] W0.10 model_pipeline 训练 hook fit_calibration_for_holdout
[ ] W1.1 sim_runner 接入 portfolio_logic 决策管线（5 阶段流程）
[ ] W1.2 live_runner 接入 portfolio_logic + 启动加载 state_snapshot + broker reconciliation
[ ] W1.3 OrderRequest.reference 唯一 client_order_id + idempotency
[ ] W1.4 PortfolioState.to_dict + from_dict + 每 base_interval bar dump 到磁盘
[ ] W1.5 HorizonExtend 实现 + halt 档 force_close_all 挂接
[ ] W1.6 金额比较加 epsilon (epsilon=0.01 元)
[ ] W1.7 trading_calendar 集成（vnpy 自带）
[ ] W1.8 test_oot_sim_parity.py + test_backward_compat.py 实现
[ ] W1.9 OotEvaluationConfig.__post_init__ 强制 stop_loss consistency 校验
[ ] W1.10 score_threshold > 0 + score_threshold < 1.0 assert
```

**绝对禁止跳过 W0 任何一项直接上 sim/live。**

完文。
