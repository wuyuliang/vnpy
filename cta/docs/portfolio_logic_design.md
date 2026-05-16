# CTA 组合层与执行层重构设计（portfolio_logic）

> 给 codex 落地用的设计文档（v2，自我 review 后的修正版）。
> 解决 4 个核心问题：**多周期协同 / 同时多机会择优 / 趋势长持有 / 大行情仓位放大**，并新增 **模型分百分位校准 + 回撤自适应风控**。

---

## 0. 修订记录

- **v1**：4 个 Pillar 草案（HTF gate / ranker / trailing / pyramid），目录置于 `cta/model/portfolio_logic/`
- **v2**：
  1. 目录调整为 `cta/portfolio_logic/`（与 model/sim/live 平级，因为它跨这 3 层共享）
  2. 新增 **Pillar 5**：模型分百分位校准 + 回撤自适应风控
  3. 自我 review 补 14 处漏洞（见 §11）
- **v3**：
  1. **Pillar 4 金字塔止损改为"按周期分层独立退"**（替代 v1/v2 的"统一 trailing 一起退"）：每个 layer 用自己的 interval 参数算 trailing stop，长周期 layer 放更大 ATR 倍数、晚被打出；短周期 layer 紧 ATR 倍数、先被打出。PyramidPosition 在最后一个 layer 退出后才消亡。注意回测计算OOT、仿真、实际一样的逻辑。
  2. **新增 cluster 维度的名义上限**：单 cluster 持仓名义 ≤ 50% equity；与 §7.2 已有的"单品种 ≤ 30%"对齐
  3. 自我 review 增 4 处漏洞修正（见 §11 续表）
- **v4（本版）**：消化 [review_claude_20260516.md](review_claude_20260516.md) 全部 31 项（P0+P1+P2+P3+缺失内容）
  1. **P0 (5项)** 全修：RiskThrottle 联动 Pyramid（§10.3.2 `apply_to_pyramid`）、双阈值（score + prob_pctl）、PortfolioState 接口规范（§4.5）、tentative commit/rollback、base_interval 时钟
  2. **P1 (10项)** 全修：running_high 单调声明、Layer 拆 hard_stop+trail_stop、mfe_mae z-score 与 percentile 分流、ScoreCalibrator 自动注入 cluster 列、HTF=both alignment 表、interval_rank 唯一源在 PortfolioLogicConfig、weekly/monthly_return 启动期返回 None、cooldown_bars_per_interval、enforce_notional_caps 统一签名、halt 档 force_close_all_on_enter
  3. **P2 (10项)** 全修：CalibrationStats `is_reliable` 标记 + 小样本 fallback、EquityTracker `deque(maxlen)` 滚窗、throttle_log 仅事件触发（§10.3.3）、`allowed_intervals_for_add` 排除 min、weekly 阈值 -3% → -5%、校准用 holdout fold 而非训练集、文件命名统一 `{model_kind}_calibration.joblib`、`bootstrap_from_broker`、test_integration "6 → 5 Pillar"、picks_rows 用 list[dict] 替代 `_replace`
  4. **P3 (6项)** 全修：§19.3 澄清 cluster_model_registry 仅扩展不替换 routing、§13.2 "8 步流程"→"标准流程"、§15.2 max_concurrent < 10 加原因说明、§14 依赖链 "unified trailing" → "per-layer trailing"、§11.1 漏洞 #7 表述更新
  5. **缺失内容 (7项)** 全补：§20 OOT vs sim/live 一致性保证、§21 冷启动/热重启协议、§22 灰度策略与回滚条件、§23 关键参数敏感度表、`test_oot_sim_parity.py`、`test_backward_compat.py`、PortfolioState 完整接口（§4.5 v4 阶段已补）

---

## 1. Context

20260514 OOT 在 P0/M1/M2/M3 修复后 win_rate 升到 42.5%，但 net_pnl 仍为负。根因不在数据/标签/leakage，而在**组合层与执行层**：

| 问题 | 现象 | 根因 |
|------|------|------|
| 多 interval 不协同 | day 看跌时 5min 仍开多 | 各 interval 模型独立预测，无方向闸 |
| 机会择优是 FCFS | 优质机会被 quota 占满后只能丢掉 | 按 DataFrame 行号顺序处理，非 best-edge |
| 趋势保不住 | always-in-long 行情 20 bars 强制下车 | 固定 stop_loss_pct + horizon_exit，无 trailing |
| 大行情吃不饱 | 超级牛市最多 3 笔 × 10% = 30% 名义 | `max_concurrent_positions_per_symbol=3` 硬上限，非金字塔 |
| 模型分跨集群不可比 | cluster_black 的 0.62 ≠ cluster_metal 的 0.62 | 各模型分布独立校准 |
| 回撤期照常开仓 | 大回撤期间仍开满 10 仓 | 无 equity 自适应 |

**目标**：在 `cta/` 下增加一层 `portfolio_logic` 模块（OOT/sim/live 三处共用），解决以上 6 类问题。**不修改 vnpy 主仓代码**。

---

## 2. As-Is 现状（来自 explore）

| 维度 | 现状 | 文件:行 |
|------|------|---------|
| 退出方式 | `stop_loss` + `horizon_exit(N=20)`，**无 trailing** | [pipeline_oot_evaluation.py:153-306](../model/pipeline_oot_evaluation.py:153) |
| 持仓期最高价跟踪 | 无 `running_high/low` | — |
| Regime 输出 | `pred_regime_label`（多类）+ `predict_proba` | [regime_classifier_model.py:30-110](../model/regime_classifier_model.py:30) |
| ATR 字段 | 候选表只有 `future_mfe_atr / future_mae_atr`，**无运行时 `atr_pct_at_entry`** | [candidate_training_dataset.py:113-127](../model/feature/candidate_training_dataset.py:113) |
| 同时多机会 | 按行号顺序（FCFS） | [pipeline_oot_evaluation.py:957-1109](../model/pipeline_oot_evaluation.py:957) |
| 单 symbol 多笔 | 硬上限 3 笔，**非金字塔** | [model_oot_eval_config.py:60](../config/model_oot_eval_config.py:60) |
| 多 interval 协同 | 完全独立 | — |
| 模型分校准 | 无；直接用 raw prob ≥ 0.62 | — |
| 回撤监控 | 无运行时 equity 反馈 | — |
| 集群路由 | `ClusterModelRegistry` 已可用 | [cluster_model_registry.py:115](../model/cluster_model_registry.py:115) |
| sim_runner | 存在但未接 model 推理 | [sim_runner.py](../sim/sim_runner.py) |

---

## 3. 设计选择（已与用户确认）

1. **多周期协同**：HTF 硬过滤 + LTF score 排序（混合方案）
2. **金字塔止损（v3 修正）**：**按周期分层独立退**——每个 layer 用自己的 interval 参数算 trailing stop，长周期 layer 留得久、短周期 layer 先被打出；不再"一起退"
3. **改造范围**：OOT + sim_runner + live_runner 全覆盖（共用同一份 `portfolio_logic`）
4. **目录位置**：`cta/portfolio_logic/`（v2 调整：与 model/sim/live 平级而非 `cta/model/` 子目录）
5. **Pillar 5**：模型分百分位校准 + 回撤自适应风控
6. **仓位上限（v3 新增）**：单品种 ≤ 30% equity，单 cluster ≤ 50% equity
7. **base_interval（v4 新增）**：portfolio_logic 选 `60min` 作为"组合时钟"——EquityTracker 仅在 base_interval 的 bar 触发 `on_bar`、RiskThrottle 仅在 base_interval 重算档位、throttle_log 也仅按 base_interval 落盘。Trailing 与 layer 退出仍在所有 interval 的 bar 上跑（因为 5min layer 必须按 5min 颗粒度做 stop check）

---

## 4. 模块布局

```
cta/portfolio_logic/
├── __init__.py                  # 导出主入口
├── config.py                    # 所有 dataclass(frozen) 配置 + __post_init__ 校验
├── portfolio_state.py           # 运行时共享状态：equity、positions、HTF state
├── interval_gate.py             # Pillar 1：HTF 硬过滤
├── opportunity_ranker.py        # Pillar 2：机会评分与容量分配
├── trailing_exit.py             # Pillar 3：ATR trailing + horizon extend
├── pyramid_manager.py           # Pillar 4：金字塔加仓状态机
├── score_calibrator.py          # Pillar 5：模型分百分位校准（离线 + 运行时映射）
├── risk_throttle.py             # Pillar 5：回撤跟踪与多档位风控
└── tests/
    ├── __init__.py
    ├── test_config.py
    ├── test_portfolio_state.py
    ├── test_interval_gate.py
    ├── test_opportunity_ranker.py
    ├── test_trailing_exit.py
    ├── test_pyramid_manager.py
    ├── test_score_calibrator.py
    ├── test_risk_throttle.py
    └── test_integration.py
```

**目录位置的理由**：portfolio_logic 跨越 model（载入模型）/ sim（运行）/ live（运行）三层调用方，放在 `cta/model/` 内会造成"model 调用 sim_runner"的奇怪依赖。提到 `cta/` 顶层、与 `model/` `sim/` `live/` 平级最干净。

---

## 4.5 PortfolioState 接口规范（v4 新增，修 P0-3 + P0-4）

`PortfolioState` 是 portfolio_logic 的**唯一可变运行时状态**，OOT/sim/live 三处共用同一个类、同一份字段。

### 4.5.1 字段

```python
@dataclass
class PortfolioState:
    # ---- 资金与时钟 ----
    equity: float                                              # 当前权益（动态盯市）
    cash: float                                                # 现金部分（信息字段，不参与决策）
    base_interval: str = "60min"                               # 组合时钟（决定 EquityTracker.on_bar 触发节奏）

    # ---- 持仓主表 ----
    positions: dict[str, PyramidPosition] = field(default_factory=dict)
    # pos_id -> PyramidPosition，pos_id = f"{symbol}_{direction}_{first_entry_iso}"

    positions_by_sym_dir: dict[tuple[tuple[str, str], str], str] = field(default_factory=dict)
    # ((symbol, exchange), direction) -> pos_id；O(1) 反向查询是否已有同向 PyramidPosition

    # ---- HTF 缓存 ----
    htf_state: dict[tuple[str, str], dict] = field(default_factory=dict)
    # (symbol, exchange) -> {"state": "long_only", "computed_at": ts, "by_interval": {"day": "trend_up", "60min": "range"}}

    # ---- 计数与 notional 累加器（用于 caps 快速校验）----
    per_symbol_count: dict[tuple[str, str], int] = field(default_factory=dict)
    per_cluster_count: dict[str, int] = field(default_factory=dict)
    per_symbol_notional: dict[tuple[str, str], float] = field(default_factory=dict)
    per_cluster_notional: dict[str, float] = field(default_factory=dict)
    total_open_notional: float = 0.0

    # ---- tentative shadow（仅在 ranker.allocate 期间有效）----
    _tentative_active: bool = False
    _tentative_per_symbol_count: dict[tuple[str, str], int] | None = None
    _tentative_per_cluster_count: dict[str, int] | None = None
    _tentative_per_symbol_notional: dict[tuple[str, str], float] | None = None
    _tentative_per_cluster_notional: dict[str, float] | None = None
    _tentative_total_open_notional: float | None = None
    _tentative_picked_sym_dir: set[tuple[tuple[str, str], str]] | None = None

    # ---- 交易记录 ----
    trade_log: list[TradeRecord] = field(default_factory=list)

    # ---- 运行时状态机标记 ----
    halt_close_done: bool = False                              # halt 档已强制平仓的幂等标记（每次离开 halt 后重置）
```

### 4.5.2 入场决策期（tentative 协议）

ranker.allocate 期间需要在不污染主状态的前提下试探多个候选，所以必须用 **shadow + commit/rollback** 协议：

```python
def begin_allocation(self) -> None:
    """ranker.allocate 入口调用。复制 per_* 计数到 _tentative_*，激活 shadow。"""
    assert not self._tentative_active, "嵌套 begin_allocation 不允许"
    self._tentative_active = True
    self._tentative_per_symbol_count = dict(self.per_symbol_count)
    self._tentative_per_cluster_count = dict(self.per_cluster_count)
    self._tentative_per_symbol_notional = dict(self.per_symbol_notional)
    self._tentative_per_cluster_notional = dict(self.per_cluster_notional)
    self._tentative_total_open_notional = self.total_open_notional
    self._tentative_picked_sym_dir = set()

def tentative_apply(self, sym_key, cluster, notional, direction) -> None:
    """在 shadow 上累加。begin_allocation 后必须用这个，不能直接改主状态。"""
    assert self._tentative_active
    self._tentative_per_symbol_count[sym_key] = self._tentative_per_symbol_count.get(sym_key, 0) + 1
    self._tentative_per_cluster_count[cluster] = self._tentative_per_cluster_count.get(cluster, 0) + 1
    self._tentative_per_symbol_notional[sym_key] = self._tentative_per_symbol_notional.get(sym_key, 0.0) + notional
    self._tentative_per_cluster_notional[cluster] = self._tentative_per_cluster_notional.get(cluster, 0.0) + notional
    self._tentative_total_open_notional += notional
    self._tentative_picked_sym_dir.add((sym_key, direction))

def commit_allocation(self) -> None:
    """ranker.allocate 完成、确定要下单时调用：shadow → 主状态。
       注意：仅在真正调用 add_position / record_add 之后调用 commit，否则会出现
       '计数已 +1 但 PyramidPosition 还没创建' 的不一致。"""
    assert self._tentative_active
    self.per_symbol_count = self._tentative_per_symbol_count
    self.per_cluster_count = self._tentative_per_cluster_count
    self.per_symbol_notional = self._tentative_per_symbol_notional
    self.per_cluster_notional = self._tentative_per_cluster_notional
    self.total_open_notional = self._tentative_total_open_notional
    self._reset_tentative()

def rollback_allocation(self) -> None:
    """ranker.allocate 中途异常或 picks 不被采纳时调用：丢弃 shadow。"""
    assert self._tentative_active
    self._reset_tentative()

def _reset_tentative(self) -> None:
    self._tentative_active = False
    self._tentative_per_symbol_count = None
    self._tentative_per_cluster_count = None
    self._tentative_per_symbol_notional = None
    self._tentative_per_cluster_notional = None
    self._tentative_total_open_notional = None
    self._tentative_picked_sym_dir = None
```

### 4.5.3 tentative 只读查询（ranker 内部用）

```python
def tentative_total_positions(self) -> int:
    return sum(self._tentative_per_symbol_count.values()) if self._tentative_active \
           else sum(self.per_symbol_count.values())

def tentative_cluster_count(self, cluster: str) -> int:
    src = self._tentative_per_cluster_count if self._tentative_active else self.per_cluster_count
    return src.get(cluster, 0)

def tentative_symbol_count(self, sym_key) -> int:
    src = self._tentative_per_symbol_count if self._tentative_active else self.per_symbol_count
    return src.get(sym_key, 0)

def tentative_symbol_notional(self, sym_key) -> float:
    src = self._tentative_per_symbol_notional if self._tentative_active else self.per_symbol_notional
    return src.get(sym_key, 0.0)

def tentative_cluster_notional(self, cluster: str) -> float:
    src = self._tentative_per_cluster_notional if self._tentative_active else self.per_cluster_notional
    return src.get(cluster, 0.0)

def tentative_total_notional(self) -> float:
    return self._tentative_total_open_notional if self._tentative_active else self.total_open_notional

def has_open_or_picked(self, sym_key, direction) -> bool:
    """已有该方向 PyramidPosition，或本轮 allocation 已经 picked 过。"""
    if self._tentative_active and (sym_key, direction) in self._tentative_picked_sym_dir:
        return True
    return (sym_key, direction) in self.positions_by_sym_dir
```

### 4.5.4 持仓变更（commit_allocation 后调用）

```python
def add_position(self, pos: PyramidPosition) -> None:
    """新开 PyramidPosition：初始 layer 已在 pyramid.open_first_layer 内构造好。"""
    self.positions[pos.pos_id] = pos
    self.positions_by_sym_dir[((pos.symbol, pos.exchange), pos.direction)] = pos.pos_id
    # 主计数已经在 commit_allocation 时同步好了，这里只放对象引用

def record_add(self, layer: Layer, pos: PyramidPosition) -> None:
    """金字塔加层：layer 已 append 到 pos.layers；主计数也已经在 commit_allocation 同步。"""
    pass  # 仅作为日志钩子，可留空或写 trade_log

def apply_exits(self, exits: list[LayerExitEvent]) -> None:
    """trailing/horizon 退出。逐 layer 扣减 notional 计数，layer 标 exited=True；
       pos 在 is_dead 时调用 remove_position。"""
    for ev in exits:
        pos = self.positions[ev.pos_id]
        layer = next(l for l in pos.layers if l.layer_id == ev.layer_id)
        sym_key = (pos.symbol, pos.exchange)
        cluster = infer_symbol_cluster(pos.symbol)
        self.per_symbol_notional[sym_key] -= layer.notional
        self.per_cluster_notional[cluster] -= layer.notional
        self.total_open_notional -= layer.notional
        # 注意：per_symbol_count 计的是 PyramidPosition 不是 layer，只在 pos 死亡时 -1
        layer.exited = True
        self.trade_log.append(_build_trade_record(pos, layer, ev))
        if pos.is_dead:
            self.remove_position(pos.pos_id)

def remove_position(self, pos_id: str) -> None:
    pos = self.positions.pop(pos_id)
    sym_key = (pos.symbol, pos.exchange)
    cluster = infer_symbol_cluster(pos.symbol)
    self.per_symbol_count[sym_key] = max(0, self.per_symbol_count.get(sym_key, 0) - 1)
    self.per_cluster_count[cluster] = max(0, self.per_cluster_count.get(cluster, 0) - 1)
    self.positions_by_sym_dir.pop((sym_key, pos.direction), None)

def record_trades(self, trades: list[TradeRecord]) -> None:
    self.trade_log.extend(trades)
```

### 4.5.5 调用约定

| 调用方 | 何时调用 | 调用什么 |
|--------|---------|---------|
| 主循环 A) | 每个 bar | （无 state 改动，trailing.update_running_extremes 改 pos 字段） |
| 主循环 B) | 每个 bar | `state.apply_exits(exits)`（内部含 layer.exited + remove_position） |
| ranker.allocate | base_interval bar | `begin_allocation` → 循环 `tentative_apply` → 返回 picks（**不 commit**，交主循环 D） |
| 主循环 D) 入场后 | 每根 picks 处理完 | `state.add_position(pos)` 或 `state.record_add(layer, pos)` |
| 主循环 D) 入场最后 | 一次性 | `state.commit_allocation()`（picks 全部处理完后） |
| 主循环 D) 异常路径 | 异常时 | `state.rollback_allocation()` |
| force_close 路径 | halt 档 enter 时 | `pyramid.force_close_all` → `state.apply_exits` |

### 4.5.6 stateless 模块的契约

portfolio_logic 中除 `PortfolioState` 外，**所有模块（HtfGate / OpportunityRanker / TrailingExitSimulator / PyramidManager / ScoreCalibrator / RiskThrottle / EquityTracker）必须 stateless 调用**：
- 不持有可变 instance 字段（cfg/常量除外）
- 所有变更都通过 `PortfolioState` 表达
- 这是 OOT / sim / live **行为完全一致**的保障——只要把同样的 bar 序列喂进去，PortfolioState 演化路径必相同

---

## 5. 统一接口（OOT / sim / live 共用）

```python
from cta.portfolio_logic import (
    HtfGate, OpportunityRanker, TrailingExitSimulator, PyramidManager,
    ScoreCalibrator, RiskThrottle, EquityTracker, PortfolioState,
)

# v4 修正：每个 bar 都跑 A/B 与 D 的 layer 退出/加仓判定；
#         C 的 throttle 重算 + ranker.allocate 仅在 base_interval 触发
#         以及 EquityTracker.on_bar 仅在 base_interval 触发

is_base_bar = (current_bar.interval == state.base_interval)

# A) 更新极值（所有 bar）
trailing_exit.update_running_extremes(state.positions, current_bar)

# B) 持仓维护（所有 bar）：trailing/horizon 退出
trailing_exit.update_stops_and_horizons(state.positions, current_bar, regime_map)
exits = trailing_exit.check_exits(state.positions, current_bar)
state.apply_exits(exits)

# C) 仅在 base_interval：重算 equity / throttle，开新仓
if is_base_bar:
    plc = cfg.portfolio_logic
    equity_tracker.on_bar(state.equity, current_bar.time)
    throttle_level = risk_throttle.compute(equity_tracker.snapshot(), current_level=throttle_level)

    # P0-1：throttle 同时影响 caps 与 pyramid
    runtime_caps = risk_throttle.apply_to_caps(plc.caps, throttle_level)
    runtime_pyramid = risk_throttle.apply_to_pyramid(plc.pyramid, throttle_level)

    # P1-10：halt 档进入时一次性强制平仓
    if throttle_level.force_close_all_on_enter and not state.halt_close_done:
        for pos in list(state.positions.values()):
            trades = pyramid.force_close_all(pos, current_bar.close, current_bar.time, "throttle_halt")
            state.record_trades(trades); state.remove_position(pos.pos_id)
        state.halt_close_done = True
    elif throttle_level.name != "halt":
        state.halt_close_done = False  # 离开 halt 后允许下次再触发

    # 入场候选 → gate → 校准 → 评分 → 分配
    df = collect_candidates(current_bar.time)
    df = htf_gate.filter(df, state.htf_state, current_bar.time)
    df = score_calibrator.transform(df)               # 加 *_pctl 列
    df = ranker.score(df, state.htf_state)
    state.begin_allocation()
    try:
        picks = ranker.allocate(
            df, state, runtime_caps,
            score_threshold=throttle_level.score_threshold,        # P0-2 综合分阈值
            min_prob_pctl=throttle_level.min_prob_pctl,            # P0-2 prob 百分位下限
        )
        # D) 入场：split + apply
        for pick in picks.itertuples():
            sym_dir = ((pick.symbol, pick.exchange), pick.direction)
            if sym_dir in state.positions_by_sym_dir:
                pos = state.positions[state.positions_by_sym_dir[sym_dir]]
                add_size = pyramid.decide_add_layer(
                    pos, pick, current_bar, state, runtime_caps, state.htf_state, runtime_pyramid)
                if add_size:
                    layer = pyramid.add_layer(pos, pick, add_size, current_bar.time, plc.trailing)
                    state.record_add(layer, pos)
            else:
                pos = pyramid.open_first_layer(pick, current_bar.time, plc.trailing)
                state.add_position(pos)
        state.commit_allocation()
    except Exception:
        state.rollback_allocation()
        raise
```

---

## 6. Pillar 1 — 多周期 HTF 硬过滤 + LTF Score 排序

### 6.1 HTF 方向闸规则

- Interval rank：`day=L0 > 60min=L1 > 30min=L2 > 15min=L3 > 5min=L4 > min=L5`（**v4：唯一源在 `PortfolioLogicConfig.interval_rank`，§12，所有模块从这里读，避免 §6.4 与 §7.1 双写**）
- HTF = `day` 与 `60min`（可配置）。每个 (symbol, exchange) 计算 `htf_state ∈ {long_only, short_only, both, none}`：

| day.regime | 60min.regime | htf_state |
|------------|--------------|-----------|
| trend_up | trend_up / range | long_only |
| trend_down | trend_down / range | short_only |
| range | range | both |
| trend_up | trend_down | **none** |
| trend_down | trend_up | **none** |
| 任一缺失 | — | 按 `fallback_when_htf_missing` 处理（默认 `skip`） |

### 6.1.1 htf_alignment 列取值（v4 修 P1-5）

`HtfGate.filter` 给每行候选打 `htf_alignment ∈ {aligned, neutral, opposite}`，由 ranker §7.1 读取：

| htf_state | direction=long | direction=short |
|-----------|---------------|----------------|
| long_only | aligned | (filter 已 drop，不进入 ranker) |
| short_only | (filter 已 drop) | aligned |
| both | **neutral** | **neutral** |
| (缺失 + fallback="both") | **neutral** | **neutral** |
| (缺失 + fallback="skip") | (filter 已 drop) | (filter 已 drop) |

> "opposite"实际上只在某些灰度配置下出现（例如把 fallback 改成"permissive"允许逆向，并惩罚 score）；v4 默认配置下 opposite 行不会进入 ranker，但保留枚举值以便后续扩展。

### 6.2 HTF 状态新鲜度（v2 新增）

- **离线 OOT**：HTF 状态从 day/60min 的预测 DataFrame 按 `(symbol, time)` join 取得，自然对齐
- **Sim/Live**：`state.htf_state[(symbol, exchange)] = {state, computed_at}`，TTL：
  - `day` 状态 TTL = 1 个交易日
  - `60min` 状态 TTL = 1 小时
  - 任一过期 → 视为缺失，按 `fallback_when_htf_missing` 处理
- 实现：`HtfGate.is_state_fresh(htf_state_entry, current_time, interval) -> bool`

### 6.3 HTF flip 时正在开的仓如何处理（v2 新增）

**策略**：HTF flip 反向 → **阻止金字塔加仓，但不强制平仓**；交给 trailing/horizon 自然退出。

理由：HTF 信号本身有滞后，强制平仓会把 trailing 设定的浮盈让出去；金字塔禁止加仓已经避免了"在错误方向继续放大"。

### 6.4 接口

```python
@dataclass(frozen=True)
class IntervalGateConfig:
    htf_intervals: tuple[str, ...] = ("day", "60min")
    require_consensus: bool = True
    fallback_when_htf_missing: str = "skip"  # 'skip' | 'both'
    state_ttl_seconds: dict[str, int] = field(default_factory=lambda: {
        "day": 86400, "60min": 3600,
    })
    # v4 修 P1-6：interval_rank 移到 PortfolioLogicConfig 顶层（§12），此处不再保存

    def __post_init__(self) -> None:
        if self.fallback_when_htf_missing not in {"skip", "both"}:
            raise ValueError(...)
        for itv, ttl in self.state_ttl_seconds.items():
            if ttl <= 0:
                raise ValueError(f"state_ttl_seconds[{itv}] must be > 0")


class HtfGate:
    def compute_htf_state(
        self,
        htf_predictions: dict[str, pd.DataFrame],  # {"day": df, "60min": df}
        as_of: pd.Timestamp,
    ) -> dict[tuple[str, str], dict]:
        """返回 {(symbol, exchange): {"state": "long_only", "computed_at": ts}}"""

    def is_state_fresh(self, entry: dict, current_time: pd.Timestamp, interval: str) -> bool: ...

    def filter(
        self,
        opportunities: pd.DataFrame,  # 含 symbol, exchange, direction, interval
        htf_state: dict,
        current_time: pd.Timestamp,
    ) -> pd.DataFrame:
        """加列 htf_allowed(bool), htf_block_reason(str), htf_alignment ∈ {'aligned','neutral','opposite'}"""
```

---

## 7. Pillar 2 — 机会评分与容量分配

### 7.1 评分公式（v2 修正：明确 edge 归一化）

```
raw_edge = pred_mfe_atr - 0.7 * pred_mae_atr
edge_z = clip((raw_edge - μ_per_(cluster,interval)) / σ_per_(cluster,interval), -2, 4)
edge_norm = (edge_z + 2) / 6                       # 映射到 [0, 1]

# v4 修 P1-6：interval_rank 唯一源 = PortfolioLogicConfig.interval_rank
score = w_prob  * (trade_filter_prob_pctl / 100.0)   # 百分位 [0,100] 先归一到 [0,1]
      + w_edge  * edge_norm
      + w_rank  * cfg.portfolio_logic.interval_rank[interval]
      + w_align * htf_alignment_bonus               # aligned=1.0, neutral=0.5, opposite=0.0
# 全部子项均 ∈ [0,1]，权重和 = 1，故 score ∈ [0,1]
```

默认权重：`w_prob=0.40, w_edge=0.30, w_rank=0.20, w_align=0.10`，加起来 = 1.0。

`μ_per_(cluster,interval)` 与 `σ_per_(cluster,interval)` 从 score_calibrator 的离线统计读取（mfe_mae 模型校准产物，见 §10.2）。

### 7.2 容量分配（v2 修正：补 per_cluster 上限 + tentative_apply 定义）

```python
@dataclass(frozen=True)
class CapsConfig:
    max_total_positions: int = 10
    max_per_symbol: int = 1                  # v2: 默认 1 个 PyramidPosition / symbol/direction，金字塔在内部加层
    max_total_per_cluster: int = 4           # v2: 每个 cluster 最多 4 个 PyramidPosition
    max_symbol_notional_pct: float = 0.30    # 单品种持仓名义 / equity 上限
    max_cluster_notional_pct: float = 0.50   # v3 新增：单 cluster 持仓名义 / equity 上限
    max_total_notional_pct: float = 1.5      # 组合总名义 / equity，可超 1（杠杆）
    dedup_same_symbol_same_direction: bool = True

    def __post_init__(self) -> None:
        if not 0.0 < self.max_symbol_notional_pct <= self.max_cluster_notional_pct:
            raise ValueError("max_symbol_notional_pct 必须 ≤ max_cluster_notional_pct")
        if not self.max_cluster_notional_pct <= self.max_total_notional_pct:
            raise ValueError("max_cluster_notional_pct 必须 ≤ max_total_notional_pct")


def allocate(
    candidates: pd.DataFrame,
    state: PortfolioState,
    caps: CapsConfig,
    score_threshold: float,        # 综合 score ∈ [0,1]
    min_prob_pctl: float,          # trade_filter_prob_pctl ∈ [0,100]（v4 修 P0-2）
) -> pd.DataFrame:
    """v4：调用方负责 begin_allocation / commit_allocation / rollback_allocation。
       allocate 内部只做 tentative_apply。"""
    assert state._tentative_active, "调用方必须先 state.begin_allocation()"
    # candidates 已按 score desc 排序
    # P0-2 双阈值过滤：score 看综合分，min_prob_pctl 看 prob 单项百分位
    candidates = candidates[
        (candidates["score"] >= score_threshold)
        & (candidates["trade_filter_prob_pctl"] >= min_prob_pctl)
    ]
    picks_rows = []  # v4 修 P2-10：用 list[dict] 而非 _replace
    for row in candidates.itertuples():
        sym_key = (row.symbol, row.exchange)
        cluster = infer_symbol_cluster(row.symbol)
        if caps.dedup_same_symbol_same_direction and state.has_open_or_picked(sym_key, row.direction):
            continue
        if state.tentative_total_positions() >= caps.max_total_positions:
            break
        if state.tentative_cluster_count(cluster) >= caps.max_total_per_cluster:
            continue
        if state.tentative_symbol_count(sym_key) >= caps.max_per_symbol:
            continue
        notional = compute_base_notional(row, state, caps)
        if notional <= 0:
            continue
        # v4 修 P1-9：统一签名（kwargs）
        notional = enforce_notional_caps(
            notional=notional, state=state, caps=caps,
            sym_key=sym_key, cluster=cluster, direction=row.direction,
            existing_for_this_pos=0.0,        # 新开 pos，没有 existing
        )
        if notional <= 0:
            continue
        d = row._asdict()
        d["allocated_notional"] = notional
        picks_rows.append(d)
        state.tentative_apply(sym_key, cluster, notional, row.direction)
    return pd.DataFrame.from_records(picks_rows)
```

`compute_base_notional`：基于 `OotEvaluationConfig.max_position_scale` 与候选预期最大亏损 ATR 倍数推出（沿用原有 `_calc_position_scale` 思路，封装到 `portfolio_state.py`）。

`enforce_notional_caps`（v4 修 P1-9：统一签名，新开仓与加层共用）：

```python
def enforce_notional_caps(
    *,
    notional: float,
    state: PortfolioState,
    caps: CapsConfig,
    sym_key: tuple[str, str],
    cluster: str,
    direction: str,
    existing_for_this_pos: float = 0.0,   # 加层时填 pos.total_active_notional，新开 0
) -> float:
    """依次按 symbol / cluster / total 三档收缩，返回最终可用 notional（可能 ≤ 0 → 调用方丢弃）。"""
    eq = state.equity
    cap_sym = caps.max_symbol_notional_pct * eq      # 30%
    cap_clu = caps.max_cluster_notional_pct * eq     # 50%
    cap_tot = caps.max_total_notional_pct * eq       # 150%

    existing_sym = state.tentative_symbol_notional(sym_key)
    existing_clu = state.tentative_cluster_notional(cluster)
    existing_tot = state.tentative_total_notional()
    # 加层时该 pos 自己的 notional 已在 existing 里，不能重复扣
    headroom_sym = max(0.0, cap_sym - existing_sym)
    headroom_clu = max(0.0, cap_clu - existing_clu)
    headroom_tot = max(0.0, cap_tot - existing_tot)
    return min(notional, headroom_sym, headroom_clu, headroom_tot)
```

新开仓与金字塔加层都调用同一函数，差异仅在 `existing_for_this_pos` 参数。

### 7.3 接口

```python
@dataclass(frozen=True)
class OpportunityRankerConfig:
    w_prob: float = 0.40
    w_edge: float = 0.30
    w_rank: float = 0.20
    w_align: float = 0.10
    score_threshold_baseline: float = 0.45  # 在 normal 风控档下使用
    dedup_same_symbol: bool = True
    edge_clip: tuple[float, float] = (-2.0, 4.0)

    def __post_init__(self) -> None:
        s = self.w_prob + self.w_edge + self.w_rank + self.w_align
        if abs(s - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {s}")


class OpportunityRanker:
    def score(self, df: pd.DataFrame, htf_state: dict) -> pd.DataFrame:
        """加列：score(float ∈ [0,1]), score_components(JSON 字符串，避免 namedtuple 不接 dict)"""

    def allocate(
        self,
        df_scored: pd.DataFrame,
        state: PortfolioState,
        caps: CapsConfig,
        score_threshold: float,    # 综合分 ∈ [0,1]，由 ThrottleLevel.score_threshold 给出
        min_prob_pctl: float,      # prob 百分位 ∈ [0,100]，由 ThrottleLevel.min_prob_pctl 给出（v4 修 P0-2）
    ) -> pd.DataFrame:
        """返回 picks_df，含 allocated_notional 列。
           调用方必须先 state.begin_allocation()，并在 picks 处理完后 commit/rollback。"""
```

---

## 8. Pillar 3 — 趋势保持：ATR Trailing + Regime-extend Horizon

### 8.1 持仓期 running_extremes 跟踪（前置）

`PortfolioState.positions[pos_id]` 共享字段：
- `running_high: float` / `running_low: float`（整个 PyramidPosition 共享）
- `extensions_used: int = 0`
- `planned_exit_bar_idx: int`（仅 horizon 用，trailing 在每个 layer 上）

**v3 注意**：原 v2 的 `atr_pct_at_first_entry` 和 `unified_stop_price` **去掉**，移到每个 `Layer` 上（每 layer 独立 ATR 和独立 trailing stop）。详见 §9。

**v4 修 P1-1：running_high/low 单调性**：一旦由**任何 active layer** 推高（多头）或推低（空头），就**永不下调**：
- 当某个 layer 退出后，剩余 layer 看到的 `pos.running_high` 仍然是历史峰值，**不重置到"剩余 layer 入场后的最高点"**
- 否则违反 trailing 的 `update_only_in_favor` 隐含语义（stop 会跳变后退）
- 仅在 PyramidPosition 整体 `is_dead` 后，pos 从 `state.positions` 删除，running_high 随对象消失

每个 bar 强制更新（OOT/sim/live 都要）：
```python
for pos in state.positions.values():
    if pos.direction == "long":
        pos.running_high = max(pos.running_high, current_bar.high)
    else:
        pos.running_low = min(pos.running_low, current_bar.low)
```

### 8.2 ATR 字段补齐

- [cta/feature/volatility.py](../feature/volatility.py) 增加 `atr_pct = atr / close` 输出列
- [candidate_training_dataset.py](../model/feature/candidate_training_dataset.py) 候选行携带 `atr_pct_at_entry`（= 入场前一根 bar 的 atr_pct）

### 8.3 Trailing 算法（v3：per-interval per-layer）

设计原则：**每个 layer 用自己的 interval 参数独立维护 trailing stop**。长周期 layer（如 day）放大 ATR 倍数 + 高激活门槛，短周期 layer（如 5min）紧 ATR 倍数 + 低激活门槛，使得日内回撤时 5min layer 先被打出、day layer 继续持有。

```python
@dataclass(frozen=True)
class IntervalTrailingParams:
    atr_multiplier: float          # trailing 距离 = atr_multiplier × atr_abs
    activation_profit_atr: float   # 浮盈 ≥ 此值才把 stop 从 hard fallback 切到 trailing
    fallback_hard_stop_pct: float  # 激活前的固定止损（按 entry 价比例）

# v3 默认表：周期越长，stop 越宽（让趋势 layer 活久点）
DEFAULT_INTERVAL_TRAILING: dict[str, IntervalTrailingParams] = {
    "day":   IntervalTrailingParams(atr_multiplier=4.0, activation_profit_atr=1.5, fallback_hard_stop_pct=0.04),
    "60min": IntervalTrailingParams(atr_multiplier=3.5, activation_profit_atr=1.2, fallback_hard_stop_pct=0.03),
    "30min": IntervalTrailingParams(atr_multiplier=3.0, activation_profit_atr=1.0, fallback_hard_stop_pct=0.025),
    "15min": IntervalTrailingParams(atr_multiplier=2.5, activation_profit_atr=0.8, fallback_hard_stop_pct=0.020),
    "5min":  IntervalTrailingParams(atr_multiplier=2.0, activation_profit_atr=0.6, fallback_hard_stop_pct=0.015),
    "min":   IntervalTrailingParams(atr_multiplier=1.5, activation_profit_atr=0.5, fallback_hard_stop_pct=0.010),
}

@dataclass(frozen=True)
class TrailingExitConfig:
    enabled: bool = True
    activate_only_when_regime: tuple[str, ...] = ("trend_up", "trend_down")
    interval_params: dict[str, IntervalTrailingParams] = field(default_factory=lambda: DEFAULT_INTERVAL_TRAILING)
    update_only_in_favor: bool = True
    default_interval_params_key: str = "30min"  # 未知 interval 时 fallback


def init_layer_stops(pos: PyramidPosition, layer: Layer, cfg: TrailingExitConfig) -> None:
    """v4 修 P1-2：开 layer 时分别初始化 hard_stop_price 和 trail_stop_price。
       - hard_stop_price：固定百分比止损，整个生命周期不变，trailing 激活后仍生效作为"地板"
       - trail_stop_price：trailing 未激活时 = NaN（或 ±inf），激活后才有有效值
       退出判定取 effective_stop = max(hard, trail if activated)（long）/ min(...)（short）
    """
    params = cfg.interval_params.get(layer.interval, cfg.interval_params[cfg.default_interval_params_key])
    if pos.direction == "long":
        layer.hard_stop_price = layer.entry_price * (1 - params.fallback_hard_stop_pct)
        layer.trail_stop_price = float("-inf")     # 还未激活
    else:
        layer.hard_stop_price = layer.entry_price * (1 + params.fallback_hard_stop_pct)
        layer.trail_stop_price = float("+inf")
    layer.trailing_activated = False


def update_layer_stop(pos: PyramidPosition, layer: Layer, current_regime: str, cfg: TrailingExitConfig) -> None:
    """对单个 layer 计算新 trailing stop。trailing 在 layer.atr_pct_at_entry 上展开。
       v4 修 P1-2：trailing 写入 trail_stop_price，不动 hard_stop_price；
                   首次激活强制覆盖（不 max），之后 update_only_in_favor 才生效。
    """
    if not cfg.enabled or current_regime not in cfg.activate_only_when_regime:
        return
    params = cfg.interval_params.get(layer.interval, cfg.interval_params[cfg.default_interval_params_key])
    atr_abs = layer.atr_pct_at_entry * layer.entry_price
    if pos.direction == "long":
        profit_atr = (pos.running_high - layer.entry_price) / atr_abs
        if profit_atr < params.activation_profit_atr:
            return
        new_trail = pos.running_high - params.atr_multiplier * atr_abs
        if not layer.trailing_activated:
            layer.trail_stop_price = new_trail        # 首次激活：直接覆盖（即使比 hard 还低也存进去）
            layer.trailing_activated = True
        elif cfg.update_only_in_favor:
            layer.trail_stop_price = max(layer.trail_stop_price, new_trail)
        else:
            layer.trail_stop_price = new_trail
    else:  # short
        profit_atr = (layer.entry_price - pos.running_low) / atr_abs
        if profit_atr < params.activation_profit_atr:
            return
        new_trail = pos.running_low + params.atr_multiplier * atr_abs
        if not layer.trailing_activated:
            layer.trail_stop_price = new_trail
            layer.trailing_activated = True
        elif cfg.update_only_in_favor:
            layer.trail_stop_price = min(layer.trail_stop_price, new_trail)
        else:
            layer.trail_stop_price = new_trail


def effective_stop(pos: PyramidPosition, layer: Layer) -> float:
    """退出判定用：长头取 max(hard, trail)、空头取 min(hard, trail)。"""
    if pos.direction == "long":
        return max(layer.hard_stop_price, layer.trail_stop_price)  # trail 未激活时 -inf，max 自然得 hard
    else:
        return min(layer.hard_stop_price, layer.trail_stop_price)
```

**关键点**：
- 每个 layer 用**自己的** `entry_price` 算 profit_atr，但用 PyramidPosition **共享的** `running_high/low`（因为同方向，position 的极值就是 layer 能看到的最优）
- 短周期 layer 的 `atr_pct_at_entry`（在小周期上算的 ATR）天然更小，配合更小 `atr_multiplier`，stop 更靠近 running_high → 先被打出
- 长周期 layer 的 ATR 大、倍数大，stop 远离 running_high → 后被打出
- **v4：hard_stop 永远当作"地板"——即使 trailing 想往下走，effective_stop 取 max 保住底线**

### 8.4 Regime-extend Horizon

```python
@dataclass(frozen=True)
class HorizonExtendConfig:
    enabled: bool = True
    extend_when_regime: tuple[str, ...] = ("trend_up", "trend_down")
    max_extensions: int = 3
    extension_bars: int = 20
```

到期前 1 根 bar 时检查 regime：仍同向 → 延长 N bars，最多 3 次（即 20 → 80 bars 上限）。

### 8.5 接口（v3）

```python
class TrailingExitSimulator:
    def update_running_extremes(self, positions, current_bar) -> None:
        """更新 pos.running_high / pos.running_low"""

    def update_stops_and_horizons(self, positions, current_bar, regime_map) -> None:
        """对每个 pos 的每个 layer 调用 update_layer_stop；并对 pos 调用 horizon extend"""

    def check_exits(self, positions, current_bar) -> list[LayerExitEvent]:
        """触发条件（按 layer 粒度）：
           - layer.layer_stop_price 被穿透 → reason='trailing_stop' 或 'hard_stop'
           - pos.planned_exit_bar_idx 到达 → 对该 pos 所有未退出 layer 发 'horizon_exit'
           注意：返回 LayerExitEvent 而非 PositionExitEvent。
                 一个 pos 一次可能只退部分 layer，pos 在 layers=[] 时才彻底消亡。
        """
```

---

## 9. Pillar 4 — 金字塔加仓（按周期分层独立退）

### 9.1 状态模型（v3）

```python
@dataclass
class Layer:
    layer_id: int
    entry_time: pd.Timestamp
    entry_price: float
    notional: float
    interval: str                          # 决定 trailing 参数
    atr_pct_at_entry: float                # v3: 每个 layer 独立 ATR
    signal_score: float
    trade_filter_prob: float
    trade_filter_prob_pctl: float
    # v4 修 P1-2：双 stop 分开存
    hard_stop_price: float                 # 固定百分比止损，整生命周期不变（hard floor）
    trail_stop_price: float                # trailing 激活前 = ±inf；激活后存动态值
    trailing_activated: bool = False       # 浮盈达到 activation_profit_atr 后翻 True
    exited: bool = False                   # 退出后置 True；不立即从 list 删除，便于审计

    def to_dict(self) -> dict:
        return {
            "layer_id": self.layer_id,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "notional": self.notional,
            "interval": self.interval,
            "atr_pct_at_entry": self.atr_pct_at_entry,
            "signal_score": self.signal_score,
            "trade_filter_prob": self.trade_filter_prob,
            "trade_filter_prob_pctl": self.trade_filter_prob_pctl,
            "hard_stop_price": self.hard_stop_price,
            "trail_stop_price": self.trail_stop_price,
            "trailing_activated": self.trailing_activated,
            "exited": self.exited,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Layer":
        return cls(
            layer_id=int(d["layer_id"]),
            entry_time=pd.Timestamp(d["entry_time"]),
            entry_price=float(d["entry_price"]),
            notional=float(d["notional"]),
            interval=str(d["interval"]),
            atr_pct_at_entry=float(d["atr_pct_at_entry"]),
            signal_score=float(d["signal_score"]),
            trade_filter_prob=float(d["trade_filter_prob"]),
            trade_filter_prob_pctl=float(d["trade_filter_prob_pctl"]),
            hard_stop_price=float(d["hard_stop_price"]),
            trail_stop_price=float(d["trail_stop_price"]),
            trailing_activated=bool(d.get("trailing_activated", False)),
            exited=bool(d.get("exited", False)),
        )

@dataclass
class PyramidPosition:
    pos_id: str                            # f"{symbol}_{direction}_{first_entry_iso}"
    symbol: str
    exchange: str
    direction: str                          # 'long' | 'short'
    layers: list[Layer]                     # 所有曾经存在的 layer（含 exited=True 的）
    base_interval_minutes: int              # 以第一层 interval 为冷却基准
    running_high: float                     # 仅活跃 layers 的最高点；从最早活跃 layer 入场起算
    running_low: float
    planned_exit_bar_idx: int               # 共享 horizon
    extensions_used: int

    @property
    def active_layers(self) -> list[Layer]:
        return [l for l in self.layers if not l.exited]

    @property
    def is_dead(self) -> bool:
        return len(self.active_layers) == 0

    @property
    def avg_entry_price(self) -> float:
        active = self.active_layers
        if not active: return 0.0
        w = sum(l.notional for l in active)
        return sum(l.entry_price * l.notional for l in active) / w if w > 0 else 0.0

    @property
    def total_active_notional(self) -> float:
        return sum(l.notional for l in self.active_layers)

    def to_dict(self) -> dict:
        return {
            "pos_id": self.pos_id,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "direction": self.direction,
            "layers": [l.to_dict() for l in self.layers],
            "base_interval_minutes": self.base_interval_minutes,
            "running_high": self.running_high,
            "running_low": self.running_low,
            "planned_exit_bar_idx": self.planned_exit_bar_idx,
            "extensions_used": self.extensions_used,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PyramidPosition":
        return cls(
            pos_id=str(d["pos_id"]),
            symbol=str(d["symbol"]),
            exchange=str(d["exchange"]),
            direction=str(d["direction"]),
            layers=[Layer.from_dict(x) for x in d.get("layers", [])],
            base_interval_minutes=int(d["base_interval_minutes"]),
            running_high=float(d["running_high"]),
            running_low=float(d["running_low"]),
            planned_exit_bar_idx=int(d["planned_exit_bar_idx"]),
            extensions_used=int(d.get("extensions_used", 0)),
        )
```

**v3 关键点**：
- Layer 不立即从 `layers` 列表里删，而是标 `exited=True`，便于 trade_details 完整记录。`active_layers` property 给运行时用。
- `running_high/low` 仅对 active_layers 有意义；一个 layer 退出后，position 继续追踪剩余 layers 的 running_high。

### 9.2 加仓判定（v3）

```python
@dataclass(frozen=True)
class PyramidConfig:
    enabled: bool = True
    max_active_layers: int = 4
    max_lifetime_layers: int = 6
    size_decay: tuple[float, ...] = (1.0, 0.5, 0.25, 0.15, 0.10, 0.10)
    min_profit_atr_to_add: float = 1.0
    # v4 修 P1-8：cooldown 改为 per-interval 的 bar 数，避免 day 周期 cooldown 形同虚设
    cooldown_bars_per_interval: dict[str, int] = field(default_factory=lambda: {
        "day": 3,     # 至少隔 3 天才能再加 day layer
        "60min": 4,   # 至少 4 小时
        "30min": 6,
        "15min": 8,
        "5min": 12,
        "min": 30,
    })
    require_new_signal_same_direction: bool = True
    require_htf_still_aligned: bool = True
    one_layer_per_interval: bool = True
    # v4 修 P2-4：min 周期只作 first_layer，不参与加层
    allowed_intervals_for_add: tuple[str, ...] = ("day", "60min", "30min", "15min", "5min")

    def __post_init__(self) -> None:
        if self.max_active_layers > self.max_lifetime_layers:
            raise ValueError("max_active_layers 不能超过 max_lifetime_layers")
        if len(self.size_decay) < self.max_lifetime_layers:
            raise ValueError("size_decay 长度至少要等于 max_lifetime_layers")
        for itv, n in self.cooldown_bars_per_interval.items():
            if n <= 0:
                raise ValueError(f"cooldown_bars_per_interval[{itv}] must be > 0")


INTERVAL_MINUTES = {
    "day": 240,
    "60min": 60,
    "30min": 30,
    "15min": 15,
    "5min": 5,
    "min": 1,
}


def interval_to_minutes(interval: str) -> int:
    return int(INTERVAL_MINUTES.get(str(interval).strip().lower(), 30))


def compute_profit_atr(pos: PyramidPosition, current_price: float) -> float:
    if not pos.active_layers:
        return 0.0
    avg_entry = pos.avg_entry_price
    atr_pct = float(pos.active_layers[0].atr_pct_at_entry)
    atr_abs = atr_pct * avg_entry
    if atr_abs <= 0:
        return 0.0
    if pos.direction == "long":
        return (float(current_price) - avg_entry) / atr_abs
    return (avg_entry - float(current_price)) / atr_abs


def htf_aligned_with(direction: str, gate_entry: dict | None) -> bool:
    if gate_entry is None:
        return False
    state = str(gate_entry.get("state", "")).lower()
    if state == "both":
        return True
    if str(direction).lower() == "long" and state == "long_only":
        return True
    if str(direction).lower() == "short" and state == "short_only":
        return True
    return False


def decide_add_layer(pos, new_signal, current_bar, state, caps, htf_state, cfg) -> Optional[float]:
    if not cfg.enabled:
        return None
    if new_signal.interval not in cfg.allowed_intervals_for_add:
        return None
    if len(pos.active_layers) >= cfg.max_active_layers:
        return None
    if len(pos.layers) >= cfg.max_lifetime_layers:
        return None
    if new_signal.direction != pos.direction:
        return None
    # v4 修 P1-8：cooldown 用"new_signal interval 的 bar 数"
    last_active = pos.active_layers[-1] if pos.active_layers else pos.layers[-1]
    cd_bars = cfg.cooldown_bars_per_interval.get(new_signal.interval, 6)
    cd_minutes = cd_bars * interval_to_minutes(new_signal.interval)
    elapsed_min = (current_bar.time - last_active.entry_time).total_seconds() / 60.0
    if elapsed_min < cd_minutes:
        return None
    if cfg.one_layer_per_interval:
        active_intervals = {l.interval for l in pos.active_layers}
        if new_signal.interval in active_intervals:
            return None
    profit_atr = compute_profit_atr(pos, current_bar.close)
    if profit_atr < cfg.min_profit_atr_to_add:
        return None
    if cfg.require_htf_still_aligned:
        gate_entry = htf_state.get((pos.symbol, pos.exchange))
        if not htf_aligned_with(pos.direction, gate_entry):
            return None
    base_size = compute_base_notional(new_signal, state)
    proposed = base_size * cfg.size_decay[len(pos.layers)]
    # v4 修 P1-9：统一签名（与 ranker 同一函数），existing_for_this_pos = 当前 pos 已有名义
    cluster = infer_symbol_cluster(pos.symbol)
    sym_key = (pos.symbol, pos.exchange)
    proposed = enforce_notional_caps(
        notional=proposed, state=state, caps=caps,
        sym_key=sym_key, cluster=cluster, direction=pos.direction,
        existing_for_this_pos=pos.total_active_notional,
    )
    if proposed <= 0:
        return None
    return proposed
```

**v4 修 P1-8 + P2-4 + P1-9 三处**：
1. `cooldown_bars_per_interval`：长周期 cooldown 用更多 bar，避免 day 信号 30 分钟内连续加层
2. `allowed_intervals_for_add`：`min` 周期不参与加层（频率太高会把 size_decay 耗光）
3. `enforce_notional_caps` 与新开仓共用同一函数，仅 `existing_for_this_pos` 参数不同

### 9.3 与原硬上限的兼容

| 原参数 | v3 处理 | 理由 |
|--------|---------|------|
| `max_concurrent_positions_per_symbol=3` | **deprecated**，改为 `caps.max_per_symbol=1 + pyramid.max_active_layers=4` | 同 symbol 同方向只能 1 个 PyramidPosition；金字塔在其内加层 |
| `max_concurrent_positions_total=10` | 统计 PyramidPosition 数（不是 Layer 数） | 一个 4 层金字塔仍是 1 个方向押注 |
| `max_symbol_notional_pct=0.30` | 保持，统计所有 active layer 名义总和 | v3 强制：30% 是单品种硬顶 |
| —（v3 新增） | `max_cluster_notional_pct=0.50` | 单 cluster 持仓名义 / equity ≤ 50% |

### 9.4 分层独立退出（v3 核心改写）

```python
def check_layer_exits(pos: PyramidPosition, current_bar) -> list[Layer]:
    """返回本根 bar 内被触发的 active layer 列表。"""
    triggered = []
    for layer in pos.active_layers:
        if pos.direction == "long":
            if current_bar.low <= layer.layer_stop_price:
                triggered.append(layer)
        else:
            if current_bar.high >= layer.layer_stop_price:
                triggered.append(layer)
    return triggered


def exit_layers(
    pos: PyramidPosition,
    triggered: list[Layer],
    exit_price: float,           # 通常取 layer.layer_stop_price（最坏价）
    exit_time: pd.Timestamp,
    reason: str,                 # 'trailing_stop' | 'hard_stop' | 'horizon_exit'
) -> list[TradeRecord]:
    trades = []
    for layer in triggered:
        # 触发价：长头按 max(open, layer_stop) 模拟跳空；空头按 min(open, layer_stop)
        # 由 _simulate_intrabar_exit_with_layered_stops 决定具体价
        trades.append(TradeRecord(
            symbol=pos.symbol, exchange=pos.exchange, direction=pos.direction,
            entry_time=layer.entry_time, entry_price=layer.entry_price,
            exit_time=exit_time, exit_price=exit_price,
            notional=layer.notional, interval=layer.interval,
            exit_reason=reason, layer_id=layer.layer_id,
            trade_filter_prob=layer.trade_filter_prob,
            trade_filter_prob_pctl=layer.trade_filter_prob_pctl,
        ))
        layer.exited = True
    return trades


def on_position_dead(pos: PyramidPosition) -> None:
    """当 pos.is_dead = True 时调用：从 state.positions 移除 + 释放 cluster/symbol notional 计数。
       注意：layer 退出时 notional 也要逐步从 state 计数中扣减，不是 dead 时一次性扣。"""
    ...
```

**关键行为**：
1. **同根 bar 多 layer 同时触发**：按 layer_id 升序依次记录（早入场的先记），价格按 `layer.layer_stop_price` 各自取（不强求统一价）
2. **横盘震荡 5min layer 先死，趋势恢复后 5min 信号可再加层**：受 `cooldown_minutes` 与 `one_layer_per_interval` 约束（先要等之前那个 5min layer 真的退出）
3. **PyramidPosition 不消亡的条件**：至少 1 个 active layer。day layer 由于 stop 更宽，常常是最后退出的那个

### 9.5 主循环顺序（v4 完整版）

详见 [§5 统一接口](#5-统一接口oot--sim--live-共用) 中的代码块；本节仅对 layer 退出细节做补充：

```python
# B 阶段内部展开：trailing.check_exits 已对每个 layer 算 effective_stop（§8.3）
for pos in list(state.positions.values()):
    triggered = check_layer_exits(pos, current_bar)   # 用 effective_stop 判断穿透
    if triggered:
        trades = exit_layers(pos, triggered, current_bar.time, reason="trailing_stop")
        state.apply_exits([TradeRecord→LayerExitEvent 转换])
    # horizon 到期：剩余所有 active layer 一起退
    if pos.planned_exit_bar_idx <= current_bar_idx and pos.active_layers:
        trades = exit_layers(pos, pos.active_layers, current_bar.time, reason="horizon_exit")
        state.apply_exits(...)
```

**v4 与 v3 的主循环差异**：
- C 阶段（入场）的 throttle 重算与 ranker.allocate 仅在 `current_bar.interval == state.base_interval` 时执行（**P0-5**）
- C 阶段开头先 `runtime_caps = throttle.apply_to_caps(...)` 同时 `runtime_pyramid = throttle.apply_to_pyramid(...)`（**P0-1**）
- C 阶段开头若 `throttle_level.force_close_all_on_enter and not state.halt_close_done` → 调 `pyramid.force_close_all` 平掉所有 pos（**P1-10**）
- ranker.allocate 调用前后必须 `state.begin_allocation() / commit_allocation()`，异常路径 `rollback_allocation()`（**P0-4**）

### 9.6 接口

```python
class PyramidManager:
    def __init__(self, cfg: PyramidConfig): ...

    def open_first_layer(self, pick, entry_time, trailing_cfg) -> PyramidPosition:
        """构造 PyramidPosition + 第 0 个 Layer，调用 init_layer_stop() 给第 0 层初始化 stop"""

    def add_layer(self, pos, pick, notional, entry_time, trailing_cfg) -> Layer:
        """append 一个 Layer，调用 init_layer_stop() 用该 layer interval 的参数初始化它的 hard fallback stop"""

    def decide_add_layer(self, pos, new_signal, current_bar, state, caps, htf_state) -> Optional[float]:
        """返回 notional 或 None（被拒绝）；详见 §9.2"""

    def force_close_all(self, pos, exit_price, exit_time, reason) -> list[TradeRecord]:
        """horizon 到期或风控档=halt 时把剩余 active layers 一次性退出"""
```

---

## 10. Pillar 5 — 模型分百分位校准 + 回撤自适应风控（v2 新增）

### 10.1 为什么要做（动机）

- **跨集群可比性**：训练时 cluster_black 的 `trade_filter_prob` 分布可能 `mean=0.55, std=0.12`，cluster_metal 可能 `mean=0.48, std=0.08`。raw 0.62 在前者是 P57，在后者是 P95。直接用 0.62 当全局阈值 → 不公平、信号质量参差。
- **风险预算自适应**：当组合处于回撤期，应该降低整体仓位、提高入场门槛、减少并发数，把资金留给"更高百分位"的极优机会，而非照常发放 quota。

### 10.2 模型分百分位校准（离线）

#### 10.2.1 何时计算

- **训练侧（v4 修 P2-6）**：每个 gate 模型 (`trade_filter / final_decision_stack / regime_classifier / mfe_mae`) 训练完毕后，**用与 trade_filter 训练时切出的同一段 holdout fold** 做 `predict_proba`，得到 raw score 分布；**不使用训练集本身**，避免 over-fit 让分布过乐观（OOT 实际分布会偏低，校准 P80 会比真实 P80 严格→ throttle 过于保守）
- **写盘**：在每个模型 `model_dir` 内保存 `{model_kind}_calibration.joblib`（v4 修 P2-7 命名统一），与 `cluster_registry.json` 自然对齐（每个 cluster × interval × model_kind 一份）

#### 10.2.2 校准数据结构

```python
@dataclass(frozen=True)
class CalibrationStats:
    cluster: str
    interval: str
    model_kind: str             # 'trade_filter' | 'final_decision_stack' | 'regime_classifier' | 'mfe_mae'
    sample_count: int
    train_window: tuple[str, str]

    # v4 修 P1-3：按 model_kind 分流，二选一
    # 用法 A: percentile_values（trade_filter / final_decision_stack / regime_classifier 用）
    #         101 个分位点（P0=min, P50=median, P100=max）
    percentile_values: np.ndarray | None = None    # shape (101,), float
    # 用法 B: z-score（mfe_mae 的 edge 用，因为 edge 是连续实数，不是概率）
    edge_mean: float | None = None
    edge_std: float | None = None

    # v4 修 P2-1：可靠性标记
    is_reliable: bool = True   # sample_count < min_reliable_samples 时 False，runtime fallback 到 raw

    def __post_init__(self) -> None:
        if self.model_kind == "mfe_mae":
            if self.edge_mean is None or self.edge_std is None:
                raise ValueError("mfe_mae 校准必须填 edge_mean / edge_std")
        else:
            if self.percentile_values is None or len(self.percentile_values) != 101:
                raise ValueError(f"{self.model_kind} 校准必须填 percentile_values (shape (101,))")
```

#### 10.2.3 落盘格式

```
cta/report/backtest/{run_tag}_GRP_BLACK_60min_both/
├── trade_filter.joblib                          # 已有
├── trade_filter_features.csv                    # 已有
├── trade_filter_calibration.joblib              # v4 新增（命名统一）
├── final_decision_stack.joblib                  # 已有
├── final_decision_stack_calibration.joblib      # v4
├── regime_classifier.joblib                     # 已有
├── regime_classifier_calibration.joblib         # v4
├── mfe_mae.joblib                               # 已有
├── mfe_mae_calibration.joblib                   # v4（内部存 edge_mean/edge_std）
└── ...
```

**v4 修 P2-7 文件命名规范**：每个模型的校准文件统一为 `{model_kind}_calibration.joblib`，与模型 joblib 同名前缀；旧的 `_score_calibration` / `_edge_calibration` 后缀**废弃**。

`ClusterModelRegistry` 在 `load()` 时一并加载，给 `predict_proba` 增加 `_pctl` 列输出（trade_filter / final_decision_stack / regime_classifier）或 `edge_z / edge_norm` 列（mfe_mae）。

#### 10.2.4 runtime 映射

```python
class ScoreCalibrator:
    def __init__(self, calibrations: dict[tuple[str, str, str], CalibrationStats]): ...

    def to_percentile(self, cluster: str, interval: str, model_kind: str, raw_score: float) -> float:
        """trade_filter / final_decision_stack / regime_classifier 用。返回 ∈ [0, 100]。"""
        key = (cluster, interval, model_kind)
        stats = self.calibrations.get(key)
        if stats is None or not stats.is_reliable:                # v4 修 P2-1：fallback
            logger.warning(f"score_calibration missing/unreliable for {key}, fallback raw*100")
            return float(np.clip(raw_score * 100.0, 0, 100))
        idx = np.searchsorted(stats.percentile_values, raw_score, side="right")
        return float(np.clip(idx, 0, 100))

    def to_edge_z(self, cluster: str, interval: str, raw_edge: float) -> float:
        """mfe_mae 专用：z-score 归一化。返回 ∈ [-2, 4] clipped。"""
        key = (cluster, interval, "mfe_mae")
        stats = self.calibrations.get(key)
        if stats is None or not stats.is_reliable:
            logger.warning(f"edge_calibration missing/unreliable for {key}, fallback z=0")
            return 0.0
        z = (raw_edge - stats.edge_mean) / max(stats.edge_std, 1e-8)
        return float(np.clip(z, -2.0, 4.0))

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """v4 修 P1-4：自动注入 cluster 列（如果没有），调用方无须先 join。
           v4 修 P1-3：trade_filter / final_decision_stack 用 percentile；mfe_mae edge 用 z-score。

           输出新列：
             - trade_filter_prob_pctl    ∈ [0, 100]
             - final_decision_score_pctl ∈ [0, 100]（若 df 含 final_decision_score 列）
             - regime_pred_pctl          ∈ [0, 100]（若 df 含 pred_regime_prob_trend_up 列）
             - edge_z                    ∈ [-2, 4]（若 df 含 pred_mfe_atr / pred_mae_atr）
             - edge_norm                 ∈ [0, 1]   = (edge_z + 2) / 6（喂给 ranker.score 用）
        """
        if "cluster" not in df.columns:
            df = df.copy()
            df["cluster"] = df["symbol"].apply(infer_symbol_cluster)
        out = df.copy()
        if "trade_filter_prob" in out.columns:
            out["trade_filter_prob_pctl"] = out.apply(
                lambda r: self.to_percentile(r["cluster"], r["interval"], "trade_filter", r["trade_filter_prob"]),
                axis=1,
            )
        if "final_decision_score" in out.columns:
            out["final_decision_score_pctl"] = out.apply(
                lambda r: self.to_percentile(
                    r["cluster"], r["interval"], "final_decision_stack", r["final_decision_score"]
                ),
                axis=1,
            )
        if "pred_regime_prob_trend_up" in out.columns:
            out["regime_pred_pctl"] = out.apply(
                lambda r: self.to_percentile(
                    r["cluster"], r["interval"], "regime_classifier", r["pred_regime_prob_trend_up"]
                ),
                axis=1,
            )
        if {"pred_mfe_atr", "pred_mae_atr"}.issubset(out.columns):
            out["raw_edge"] = out["pred_mfe_atr"] - 0.7 * out["pred_mae_atr"]
            out["edge_z"] = out.apply(
                lambda r: self.to_edge_z(r["cluster"], r["interval"], r["raw_edge"]),
                axis=1,
            )
            out["edge_norm"] = (out["edge_z"] + 2.0) / 6.0
        return out
```

#### 10.2.5 cold-start / 缺失处理

- 若某 (cluster, interval, model_kind) 没有校准数据 → fallback：直接用 raw 值 ×100 作 percentile，并 log warning
- 校准过期检查：runtime 时若 `(current_time - train_window.end) > 180 days` → log warning，建议重训

### 10.3 回撤自适应风控（runtime）

#### 10.3.1 EquityTracker（运行时跟踪）

```python
from collections import deque

@dataclass
class EquityTracker:
    """v4：on_bar 仅在 base_interval 的 bar 上被主循环调用（§5）。
            其他 interval 的事件不进入此 tracker，避免采样畸形（P0-5）。
    """
    base_interval: str = "60min"
    # v4 修 P2-2：滚动窗口防内存爆
    max_history_bars: int = 10000
    equity_history: deque = field(default_factory=lambda: deque(maxlen=10000))
    running_high: float = 0.0
    running_high_at: pd.Timestamp | None = None

    # v4 修 P1-7：启动期 history 不足时 weekly/monthly 应返回 None，不参与档位判定
    min_bars_for_weekly: int = 7      # 7 个 base_interval bar 约 7 小时（60min）；更激进可调
    min_bars_for_monthly: int = 30

    def on_bar(self, equity: float, ts: pd.Timestamp) -> None:
        self.equity_history.append((ts, equity))
        if equity > self.running_high:
            self.running_high = equity
            self.running_high_at = ts

    def current_drawdown_pct(self) -> float:
        if self.running_high <= 0 or not self.equity_history:
            return 0.0
        return 1 - self.equity_history[-1][1] / self.running_high

    def weekly_return_pct(self) -> float | None:
        return self._period_return_pct(days=7, min_bars=self.min_bars_for_weekly)

    def monthly_return_pct(self) -> float | None:
        return self._period_return_pct(days=30, min_bars=self.min_bars_for_monthly)

    def _period_return_pct(self, days: int, min_bars: int) -> float | None:
        if len(self.equity_history) < min_bars:
            return None                               # v4 修 P1-7：数据不足返 None
        elapsed = self.equity_history[-1][0] - self.equity_history[0][0]
        if elapsed < pd.Timedelta(days=days * 0.5):  # 至少半个周期数据
            return None
        cutoff = self.equity_history[-1][0] - pd.Timedelta(days=days)
        base = next((e for (t, e) in self.equity_history if t >= cutoff),
                    self.equity_history[0][1])
        if base <= 0:
            return None
        return self.equity_history[-1][1] / base - 1.0

    def snapshot(self) -> "EquitySnapshot":
        return EquitySnapshot(
            drawdown_pct=self.current_drawdown_pct(),
            weekly_return_pct=self.weekly_return_pct(),    # 可能 None
            monthly_return_pct=self.monthly_return_pct(),  # 可能 None
            equity=self.equity_history[-1][1] if self.equity_history else 0.0,
            running_high=self.running_high,
            running_high_at=self.running_high_at,
        )

    @classmethod
    def bootstrap_from_broker(cls, initial_equity: float,
                              history_path: Path | None = None,
                              base_interval: str = "60min") -> "EquityTracker":
        """v4 修 P2-8：sim/live 热启动用。"""
        tracker = cls(base_interval=base_interval)
        if history_path and history_path.exists():
            df = pd.read_csv(history_path)
            for _, row in df.iterrows():
                tracker.on_bar(float(row["equity"]), pd.Timestamp(row["ts"]))
        else:
            tracker.on_bar(initial_equity, pd.Timestamp.now())
        return tracker
```

#### 10.3.2 RiskThrottle（多档位）

```python
@dataclass(frozen=True)
class ThrottleLevel:
    name: str                              # 'normal' | 'reduced' | 'conservative' | 'halt'
    drawdown_lo: float
    drawdown_hi: float
    max_total_positions_mult: float
    max_per_cluster_mult: float
    # v4 修 P0-2：把单一 pctl 阈值拆成两个，与 ranker.allocate 签名对齐
    score_threshold: float                 # 综合 score ∈ [0,1]，ranker 入场最低分
    min_prob_pctl: float                   # trade_filter_prob_pctl ∈ [0,100]
    allow_pyramid: bool
    max_pyramid_layers: int                # halt=0
    # v4 修 P1-10：halt 档进入时一次性强制平仓
    force_close_all_on_enter: bool = False


@dataclass(frozen=True)
class RiskThrottleConfig:
    levels: tuple[ThrottleLevel, ...] = (
        # name              dd_lo dd_hi mt× mc× score_thr min_pctl pyr pyr_layers force_close
        ThrottleLevel("normal",       0.00, 0.05, 1.0, 1.0, 0.45, 60.0, True,  4, False),
        ThrottleLevel("reduced",      0.05, 0.10, 0.7, 0.7, 0.55, 80.0, True,  2, False),
        ThrottleLevel("conservative", 0.10, 0.15, 0.4, 0.5, 0.70, 90.0, False, 0, False),
        ThrottleLevel("halt",         0.15, 1.00, 0.0, 0.0, 1.00, 100.0, False, 0, True),
    )
    recovery_hysteresis: float = 0.02
    # 周/月加严规则（v4 修 P2-5：放宽周阈值，避免高波动品种误触发）
    force_conservative_if_weekly_lt: float = -0.05
    force_conservative_if_monthly_lt: float = -0.08


class RiskThrottle:
    def compute(self, snap: EquitySnapshot, current_level: ThrottleLevel | None) -> ThrottleLevel:
        # 1) 按 drawdown 选档
        dd = snap.drawdown_pct
        candidate = next(L for L in self.cfg.levels if L.drawdown_lo <= dd < L.drawdown_hi)

        # 2) hysteresis：若试图比 current 放宽一档，要求 dd 比目标档 hi 再低 hysteresis
        if current_level is not None and self._is_relaxation(candidate, current_level):
            if dd >= candidate.drawdown_hi - self.cfg.recovery_hysteresis:
                candidate = current_level

        # 3) 周/月强制加严（v4 修 P1-7：snap 字段可能 None，None 时跳过该规则）
        wk = snap.weekly_return_pct
        mo = snap.monthly_return_pct
        weekly_breach  = (wk is not None and wk < self.cfg.force_conservative_if_weekly_lt)
        monthly_breach = (mo is not None and mo < self.cfg.force_conservative_if_monthly_lt)
        if weekly_breach or monthly_breach:
            candidate = max(candidate, self._level_by_name("conservative"), key=self._severity)
        return candidate

    def apply_to_caps(self, base_caps: CapsConfig, level: ThrottleLevel) -> CapsConfig:
        return replace(base_caps,
            max_total_positions=int(base_caps.max_total_positions * level.max_total_positions_mult),
            max_total_per_cluster=int(base_caps.max_total_per_cluster * level.max_per_cluster_mult),
        )

    # v4 新增（P0-1）：throttle 必须同时收紧 pyramid 配置，否则 allow_pyramid=False 完全失效
    def apply_to_pyramid(self, base_pyramid: PyramidConfig, level: ThrottleLevel) -> PyramidConfig:
        return replace(base_pyramid,
            enabled=base_pyramid.enabled and level.allow_pyramid,
            max_active_layers=min(base_pyramid.max_active_layers, level.max_pyramid_layers)
                              if level.max_pyramid_layers > 0 else 0,
        )
```

档位含义与默认表（v4）：

| Level | DD 区间 | max_total × | per_cluster × | score_threshold | min_prob_pctl | 金字塔 | force_close | 备注 |
|-------|---------|-------------|---------------|----------------|--------------|--------|-------------|------|
| normal | [0%, 5%) | 1.0 | 1.0 | 0.45 | P60 | 启用 max_layers=4 | False | 默认 |
| reduced | [5%, 10%) | 0.7 | 0.7 | 0.55 | P80 | 启用 max_layers=2 | False | 收紧入场 + 限制加仓 |
| conservative | [10%, 15%) | 0.4 | 0.5 | 0.70 | P90 | 禁用 | False | 极优机会才开 |
| halt | [15%+ | 0.0 | 0.0 | 1.00 | P100 | 禁用 | **True** | 一次性强制平仓 + 仅退出 |

恢复 hysteresis：若当前在 `reduced`(dd∈[5,10%))，要降到 `normal` 需 dd<5%-2%=3%；避免在边界附近反复跳档。

**两个 score 阈值的语义（v4 修 P0-2）**：
- `score_threshold`：综合分阈值，单位 [0,1]，用于 §7.1 评分公式输出的 `score` 列
- `min_prob_pctl`：trade_filter 概率的百分位下限，单位 [0,100]，用于 `trade_filter_prob_pctl` 列
- 两者 AND（同时满足）才进入 ranker.allocate。一个低门槛 + 一个高门槛，等于"中等综合分但必须有顶级 prob"或"中等 prob 但综合分必须高"——风控档收紧时两者同步收紧

#### 10.3.3 落盘日报（v4 修 P2-3：从每 bar 写改为仅事件触发）

落盘策略：
1. **transition row**：仅在 `throttle_level.name` 与上一根 base_interval bar 不同时写一行
2. **daily summary row**：每个交易日收盘（base_interval 最后一根 bar）固定写一行汇总

文件：`cta/report/portfolio_logic/{run_tag}_throttle_log.csv`
```
timestamp, event_type, equity, drawdown_pct, weekly_ret, monthly_ret,
level, caps_total, caps_cluster, score_threshold, min_prob_pctl,
force_close_triggered
```
- `event_type ∈ {"transition", "daily_summary"}`
- 13 年 60min base 跑下来约几千行（vs 原方案百万行），可读且能复现 throttle 路径

OOT 报告新增字段（聚合）：
- `oot_throttle_level_distribution`：每档位的累计 bar 数比例
- `oot_score_pctl_at_entry`：每笔实际入场时的 score 百分位（应统计分布）
- `oot_throttle_transitions_count`：档位切换次数（健康范围 0-20 / 年）

---

## 11. 自我 Review 修正清单

### 11.1 v2 修正（14 项）

| # | 漏洞 | 修正位置 |
|---|------|---------|
| 1 | 模型分跨集群/周期不可比 | §10 Pillar 5：score percentile 校准 |
| 2 | 缺乏 equity 自适应 | §10 Pillar 5：RiskThrottle 多档位 |
| 3 | 缺乏 cluster 维度的分散约束 | §7.2 CapsConfig.max_total_per_cluster=4 |
| 4 | HTF 状态可能过期 | §6.2 state_ttl_seconds + is_state_fresh |
| 5 | HTF 反向时是否强平未定义 | §6.3 "阻止加仓，不强平" |
| 6 | pyramid cooldown 用 "bars" 在跨 interval 时含混 | §9.2 改为 wall-clock minutes |
| 7 | layer 初始 stop 未定义（v2 称 unified_stop，v3 拆为 per-layer） | §8.3 `init_layer_stops()`：hard_stop = entry × (1 ∓ fallback_pct)，trail_stop = ±inf（v4 修 P3-6 表述） |
| 8 | 多层 entry 的 trailing 基准（v2: avg_entry → v3: per-layer entry） | §8.3 v3 改为：每 layer 用自己 entry_price 算 profit_atr |
| 9 | edge 归一化数学未明确 | §7.1 z-score per (cluster,interval) + clip + 线性映射 [0,1] |
| 10 | state.tentative_apply 未定义 | §7.2 snapshot_for_allocation + tentative_* 函数族 |
| 11 | max_concurrent_positions_total 在金字塔下的语义 | §9.3 表格：按 PyramidPosition 数（非 layer 数）计数 |
| 12 | htf_alignment_bonus 取列未定义 | §6.4 HtfGate.filter 加列 `htf_alignment ∈ {aligned, neutral, opposite}` |
| 13 | 风险参数硬编码无校验 | §7.3 OpportunityRankerConfig.__post_init__ 校验 weights 和=1.0 |
| 14 | 校准与训练数据耦合，walk-forward 时会引入 lookahead | §10.2.1 强制在训练时（不在 OOT 上）计算校准；OOT 仅 transform，不 refit |

### 11.2 v3 新增修正（4 项）

| # | 漏洞 | 修正位置 |
|---|------|---------|
| 15 | 缺少 cluster 维度的名义上限（v2 只有 cluster 计数上限，没有金额上限） | §7.2 `max_cluster_notional_pct=0.50` |
| 16 | 单 interval 信号狂加 layer（如 5min 连续触发把整 pos 短期化） | §9.2 `one_layer_per_interval=True` |
| 17 | active_layers 与 lifetime_layers 概念混淆（layer 退出后能否再加） | §9.1 `Layer.exited` 标记 + §9.2 拆分 `max_active_layers` / `max_lifetime_layers` |
| 18 | 同根 bar 多 layer 同时被打出的成交价/顺序未定义 | §9.4 按 layer_id 升序、各取自己的 `layer_stop_price` 作 fill price |

### 11.3 v4 新增修正（消化 review_claude_20260516 的 P0+P1，15 项）

| # | Tag | 漏洞 | 修正位置 |
|---|-----|------|---------|
| 19 | P0-1 | RiskThrottle 不影响 PyramidConfig，`allow_pyramid=False` 失效 | §10.3.2 `apply_to_pyramid()` + §5 / §9.5 主循环 `runtime_pyramid` |
| 20 | P0-2 | score_threshold (∈[0,1]) 与 score_pctl_threshold (∈[0,100]) 单位冲突 | §10.3.2 ThrottleLevel 拆 `score_threshold` + `min_prob_pctl` 双字段；§7.3 ranker.allocate 双阈值过滤 |
| 21 | P0-3 | PortfolioState 接口完全没定义 | 新增 §4.5 完整接口规范（字段、方法、调用约定） |
| 22 | P0-4 | tentative_apply 无 commit/rollback 协议 | §4.5.2 `begin_allocation / commit_allocation / rollback_allocation`；§7.2 调用约定明确 |
| 23 | P0-5 | timeline 颗粒度未定义，equity 采样会失真 | §3 `base_interval` + §12 PortfolioLogicConfig.base_interval + §5 主循环按 base_interval 触发 throttle / equity_tracker / allocate |
| 24 | P1-1 | running_high 在 layer 退出后是否单调 | §8.1 显式声明"任何 active layer 推高后单调不下调" |
| 25 | P1-2 | trailing 首次激活与 hard_stop 覆盖关系 | §8.3 拆 `hard_stop_price` + `trail_stop_price` 双字段；§9.1 Layer 字段更新；effective_stop = max(hard, trail) |
| 26 | P1-3 | mfe_mae 校准用 percentile 还是 z-score | §10.2.2 CalibrationStats 按 model_kind 分流；§10.2.4 拆 `to_percentile` 与 `to_edge_z` |
| 27 | P1-4 | ScoreCalibrator.transform 的 cluster 列来源 | §10.2.4 transform 内部自动 `df["cluster"] = symbol.apply(infer_symbol_cluster)` |
| 28 | P1-5 | fallback="both" 时的 htf_alignment 取值不明 | §6.1.1 新增 alignment 取值表（both → neutral） |
| 29 | P1-6 | interval_rank 在 IntervalGateConfig / 评分公式中重复 | §12 PortfolioLogicConfig.interval_rank 唯一源；§6.4 移除；§7.1 评分公式从 portfolio_logic.interval_rank 取 |
| 30 | P1-7 | weekly/monthly_return 启动期失真，误触发 force_conservative | §10.3.1 history 不足时返回 `None`；§10.3.2 None 时跳过 force_conservative 规则 |
| 31 | P1-8 | cooldown_minutes 全局统一，对 day 周期形同虚设 | §9.2 改为 `cooldown_bars_per_interval` |
| 32 | P1-9 | enforce_notional_caps vs enforce_notional_caps_for_add 签名分裂 | §7.2 统一为单一函数 + `existing_for_this_pos` 参数；§9.2 加层用同一函数 |
| 33 | P1-10 | halt 档是否强制平仓未定义 | §10.3.2 ThrottleLevel.force_close_all_on_enter；§5 主循环 C 阶段开头处理 + `state.halt_close_done` 幂等标记 |

### 11.4 v4 续补 P2 / P3 / 缺失内容（16 项）

| # | Tag | 漏洞 | 修正位置 |
|---|-----|------|---------|
| 34 | P2-1 | CalibrationStats 小样本失真 | §10.2.2 `is_reliable: bool`；§10.2.4 不可靠 → fallback raw×100 + warning |
| 35 | P2-2 | EquityTracker 内存无限增长 | §10.3.1 `deque(maxlen=max_history_bars)` |
| 36 | P2-3 | throttle_log 落盘频率过高 | §10.3.3 仅 `transition` + `daily_summary` 两类事件触发写盘 |
| 37 | P2-4 | min 周期信号占满 pyramid | §9.2 `allowed_intervals_for_add` 排除 'min' |
| 38 | P2-5 | force_conservative weekly 阈值太敏感 | §10.3.2 `force_conservative_if_weekly_lt = -0.05`（原 -0.03） |
| 39 | P2-6 | 校准 fit 用训练集还是 holdout 未明 | §10.2.1 明确"用 trade_filter 训练时切出的同一段 holdout fold，不使用训练集本身" |
| 40 | P2-7 | 校准文件命名不一致 | §10.2.3 统一为 `{model_kind}_calibration.joblib` |
| 41 | P2-8 | sim/live 热启动 EquityTracker bootstrap 未定义 | §10.3.1 `bootstrap_from_broker` 类方法；§21 完整 bootstrap 流程 |
| 42 | P2-9 | test_integration 描述错"6 个支柱" | §15.1 改为"5 个 Pillar" |
| 43 | P2-10 | `row._replace` 接 dict 字段会失败 | §7.2 picks 用 `list[dict] → pd.DataFrame.from_records` |
| 44 | P3-1 | §19.3 与 §19.2 矛盾 | §19.3 澄清"扩展不替换 routing" |
| 45 | P3-2 | "OOT/仿真/实际一致逻辑"在 v3 §0 提到但正文没落地 | 新增 §20 OOT vs sim/live 一致性保证（4 个子节） |
| 46 | P3-3 | "8 步流程"实际只有 4 个分组 | §13.2 改为"标准流程"，§5 仍是 4 阶段 ABCD |
| 47 | P3-4 | max_concurrent <10 未解释原因 | §15.2 表格补"因 cluster cap=4 + cluster_notional=50% 限制集中" |
| 48 | P3-5 | §14 依赖链 "unified trailing" 过时 | §14 改为 "per-layer trailing" |
| 49 | P3-6 | §11.1 漏洞 #7 描述过时 | §11.1 #7 改为引用 v3 的 `init_layer_stops` |

### 11.5 v4 续补缺失大节（4 个新章节 + 2 个 test）

| # | 缺失项 | 修正位置 |
|---|-------|---------|
| 50 | OOT vs sim/live 一致性保证机制 | 新增 §20（4 子节：原则、契约、风险防御、parity test） |
| 51 | 冷启动 / 热重启协议 | 新增 §21（PortfolioState 序列化 + bootstrap 顺序 + broker reconciliation + 持久化频率） |
| 52 | 灰度策略与回滚条件 | 新增 §22（7 阶段开启顺序 + 滚动验证 + 自动回滚条件 + enable_* 依赖矩阵） |
| 53 | 关键参数敏感度 | 新增 §23（高/中/低敏感参数表 + 调参流程） |
| 54 | `test_oot_sim_parity.py` | §15.1 + §20.4 详细设计 |
| 55 | `test_backward_compat.py` | §15.1：`enable_*=False` 全关时与 baseline 字节级一致 |

---

## 12. 配置整合

```python
# cta/config/model_oot_eval_config.py 追加（不动现有字段）

@dataclass(frozen=True)
class PortfolioLogicConfig:
    # v4 修 P0-5：base_interval 作为组合时钟，控制 EquityTracker / throttle 的更新频率
    base_interval: str = "60min"

    enable_htf_gate: bool = True
    enable_ranker: bool = True
    enable_trailing: bool = True
    enable_pyramid: bool = True
    enable_horizon_extend: bool = True
    enable_score_calibration: bool = True
    enable_risk_throttle: bool = True

    # v4 修 P1-6：interval_rank 唯一源在这里，IntervalGateConfig 和 OpportunityRanker 都从这里读
    interval_rank: dict[str, float] = field(default_factory=lambda: {
        "day": 1.0, "60min": 0.85, "30min": 0.70,
        "15min": 0.55, "5min": 0.40, "min": 0.25,
    })

    interval_gate: IntervalGateConfig = field(default_factory=IntervalGateConfig)
    ranker: OpportunityRankerConfig = field(default_factory=OpportunityRankerConfig)
    trailing: TrailingExitConfig = field(default_factory=TrailingExitConfig)
    horizon_extend: HorizonExtendConfig = field(default_factory=HorizonExtendConfig)
    pyramid: PyramidConfig = field(default_factory=PyramidConfig)
    risk_throttle: RiskThrottleConfig = field(default_factory=RiskThrottleConfig)
    caps: CapsConfig = field(default_factory=CapsConfig)

    def __post_init__(self) -> None:
        if self.enable_pyramid and not self.enable_trailing:
            raise ValueError("enable_pyramid 必须搭配 enable_trailing")
        if self.enable_risk_throttle and not self.enable_score_calibration:
            raise ValueError("enable_risk_throttle 依赖 enable_score_calibration（百分位阈值需要校准）")
        if self.base_interval not in self.interval_rank:
            raise ValueError(f"base_interval={self.base_interval} 必须出现在 interval_rank 里")
        for itv, w in self.interval_rank.items():
            if not 0.0 < w <= 1.0:
                raise ValueError(f"interval_rank[{itv}]={w} 必须 ∈ (0, 1]")


@dataclass(frozen=True)
class OotEvaluationConfig:
    # ... 原字段（intrabar_stop_loss_pct=0.01, trade_filter_threshold=0.62, ...）
    portfolio_logic: PortfolioLogicConfig = field(default_factory=PortfolioLogicConfig)
```

每个 `enable_*` 开关默认 True，但允许灰度回退到 False 复用旧路径。

---

## 13. 模型推理：HTF 状态 + 校准 如何对接

### 13.1 ClusterModelRegistry 扩展

```python
# cta/model/cluster_model_registry.py 改动（最小侵入）

class ClusterModelRegistry:
    def __init__(self, ...):
        # 新增：每个 model_dir 同时加载 *_calibration.joblib
        self._calibrators: dict[tuple[str, str, str], CalibrationStats] = {}

    def get_calibrator(self) -> ScoreCalibrator:
        """返回与已加载模型对齐的 ScoreCalibrator 单例"""

    def predict_proba(self, df, model_kind="trade_filter") -> pd.DataFrame:
        # 已有：返回 raw prob
        out = self._predict_proba_raw(df, model_kind)
        # 新增：若 calibration 可用，同时附加 _pctl 列
        if self._calibrators:
            calibrator = self.get_calibrator()
            out = calibrator.transform(out)
        return out
```

### 13.2 离线 OOT 流程

1. `model_pipeline.py` 训练每个 gate model 后立刻调用 `score_calibrator.fit(holdout_predictions, model_dir)`，写出 `{model_kind}_calibration.joblib`
2. OOT 启动 → `ClusterModelRegistry.from_run_tag(run_tag)` 自动加载所有 calibration 文件
3. OOT 主循环按 §5 的标准流程跑（A 极值更新 → B 退出 → C base_interval 触发的入场 → D commit/rollback）

### 13.3 Sim/Live 流程

1. runner 启动时 `registry = ClusterModelRegistry.from_run_tag(run_tag)`，自带校准
2. 订阅 day/60min bar → 增量刷新 `state.htf_state`
3. 订阅 LTF bar → 按 §5 流程跑

---

## 14. 实施顺序（ ROI 排序）

| Wave | 内容 | 文件 | 验证标准 |
|------|------|------|---------|
| W1.1 | `config.py` + `portfolio_state.py` 骨架 | 2 个新文件 | dataclass 单测全通过 |
| W1.2 | Pillar 5 `score_calibrator.py`（独立） | 1 个新文件 + 训练侧 hook | 校准 joblib 生成；transform 输出 _pctl 列 |
| W1.3 | Pillar 2 `opportunity_ranker.py` | 1 个新文件 + 改 OOT | OOT 报告 reject_reason 出现 `ranker_dropped` |
| W2.1 | Pillar 3 ATR 字段补齐 | `volatility.py` + `candidate_training_dataset.py` | 候选表多出 `atr_pct_at_entry` 列 |
| W2.2 | Pillar 3 `trailing_exit.py` + running_extremes | 1 个新文件 + 改 OOT | average_holding_bars 上升；exit_reason 出现 trailing_stop |
| W3.1 | Pillar 1 `interval_gate.py` | 1 个新文件 + 改 OOT | reject_reason 出现 htf_blocked |
| W3.2 | Pillar 5 `risk_throttle.py` + EquityTracker | 1 个新文件 + 改 OOT | throttle_log.csv 生成；OOT 不同 dd 期入场数差异显著 |
| W4 | Pillar 4 `pyramid_manager.py` | 1 个新文件 + 重写 OOT 持仓部分 | 单 symbol 多 layer 出现在 trade_details，layer_id ∈ {0,1,2,3} |
| W5.1 | sim_runner 集成 | [sim_runner.py](../sim/sim_runner.py) | sim 启动日志 |
| W5.2 | live_runner 集成 | [live_runner.py](../live/live_runner.py) | live 启动日志 |

**关键依赖链**：
- Pillar 5 校准必须先于 Pillar 2 ranker（ranker 用 `*_pctl` 作 w_prob 输入、用 edge_norm 作 w_edge 输入）
- Pillar 5 risk_throttle 必须先于 Pillar 4 pyramid（throttle 通过 `apply_to_pyramid` 控制 `pyramid.enabled` / `max_active_layers`）
- Pillar 3 trailing 必须先于 Pillar 4 pyramid（v4 修 P3-5：pyramid 退出依赖 **per-layer trailing** stop，每 layer 用自己的 `hard_stop_price + trail_stop_price`）
- Pillar 1 gate 与 Pillar 2 ranker 解耦（gate 在 ranker 前调用即可）
- PortfolioState（§4.5）是所有 Pillar 的隐式依赖：W1.1 必须先把 dataclass 骨架建出来

---

## 15. 测试策略

### 15.1 单元测试（每个新模块必配）

`cta/portfolio_logic/tests/`：

- `test_config.py`：所有 `__post_init__` 校验路径（weights 和=1.0、enable 依赖一致性、ttl > 0）
- `test_portfolio_state.py`：tentative_apply/rollback、cluster 计数、notional 累加
- `test_interval_gate.py`：
  - day=trend_up + 60min=trend_down → htf_state="none"
  - 单 interval 缺失 + fallback="skip" → none
  - state TTL 过期 → 视为缺失
- `test_opportunity_ranker.py`：
  - 3 候选竞争 2 quota → 取 top-2 by score
  - 同 symbol 同方向 2 interval → 仅保留 top-1
  - cluster count 上限触发 → 后面同 cluster 候选 reject 理由正确
  - **v3 新增**：cluster notional 上限触发（已有 cluster 持仓 = 40% equity，新候选会推到 60%）→ notional 被收缩到 ≤50% equity 上限
  - **v3 新增**：单品种 notional 上限触发（已有该 symbol 25%，新加层 10%）→ 收缩到 ≤30% equity
- `test_trailing_exit.py`：
  - 1 ATR 浮盈未激活、3 ATR 浮盈后回撤 0.5 ATR 不触发、回撤 3.1 ATR 触发（按 30min 默认参数）
  - regime=range → trailing 不激活
  - regime trend_up 在 horizon 末尾 → 自动 extend 1 次
  - **v3 新增**：同一 pos 含 day + 5min 两层，相同 running_high；构造回撤 → 5min layer 先打出，day layer 仍 active
  - **v3 新增**：未知 interval 走 `default_interval_params_key` fallback
  - **v3 新增**：`update_only_in_favor=True` 时回撤再涨回 high，stop 不退（保持上次更新过的水平）
- `test_pyramid_manager.py`：
  - 1→2→3→4 层正常加仓；第 5 层（超 max_active_layers）拒绝
  - 冷却期内（minutes 未到）拒绝
  - 浮盈不足拒绝
  - HTF flip 反向 → 拒绝加仓但不强平
  - **v3 新增**：同 interval 重复加层被 `one_layer_per_interval` 拒绝
  - **v3 新增**：layer.exited 后同 interval 信号可重新加层（受 cooldown 约束）
  - **v3 新增**：5min layer 被 trailing 打出但 day layer 仍 active → pos 不消亡
  - **v3 新增**：同根 bar 5min + 15min 两个 layer stop 同时触发 → 各按自己 stop 价记录，pos 还活着（day/60min 在）
  - **v3 新增**：加层时撞 max_cluster_notional_pct → notional 被收缩或 reject
- `test_score_calibrator.py`：
  - 训练集 raw scores=[0.1...0.9] → P50 raw=0.5
  - transform 添加 _pctl 列且 ∈ [0, 100]
  - 缺失 cluster 时 fallback + warning
- `test_risk_throttle.py`：
  - dd=3% → normal；dd=7% → reduced；dd=12% → conservative；dd=20% → halt
  - hysteresis：从 reduced 回 normal 需 dd<3%（非 5%）
  - weekly_return=-5% 即使 dd=2% 也降到 conservative
- `test_integration.py`：5 个 Pillar 联合的端到端假数据 OOT（10 根 base_interval bar、2 个 symbol、3 个 interval、模拟 dd 0% → 12% 上升场景，断言 throttle 从 normal 走到 conservative、score_threshold 同步升高）
- `test_oot_sim_parity.py`（v4 新增，对应 §21 一致性保证）：构造同一段 bar 序列 + 同一份 cfg，分别走 OOT 批量路径与 sim 流式路径，断言 `state.trade_log` 完全一致（精度到价、时间、layer_id）
- `test_backward_compat.py`（v4 新增）：`PortfolioLogicConfig.enable_*=False`（除 caps 默认外）跑 OOT，断言与现有 [pipeline_oot_evaluation.py](../model/pipeline_oot_evaluation.py) baseline 行为字节级一致（同一份 candidate 输入 → 同一份 trade_details 输出）

### 15.2 集成测试 / 离线对照

用 `cta/report/backtest/20260514_POOL_minute60_both` 数据集重跑 OOT，落盘对比 `cta/report/portfolio_logic_comparison/{date}_baseline_vs_new.csv`：

| 指标 | baseline | new | 期望 |
|------|----------|-----|------|
| net_pnl | 当前值 | ≥ baseline | ↑ |
| win_rate | 42.5% | ±2% | ≈ |
| average_holding_bars | 当前 | 显著 ↑ | ↑ |
| mfe_capture_ratio（新指标） | — | ≥ 0.5 | — |
| max_concurrent PyramidPositions 峰值 | 10（FCFS 满载常见） | < 10（v4 修 P3-4：因 cluster_count cap=4 + cluster_notional=50% 限制集中，组合自然更分散） | ↓ |
| 入场 score_pctl 分布 mean | — | normal 档 ~70，conservative 档 ~92 | — |
| 各档位累计 bar 占比 | — | normal>60%（健康） | — |
| reject_count by reason | 仅 stop/horizon | 多出 htf_blocked / ranker_dropped / pyramid_cooldown / throttle_halted | — |

---

## 16. 风险与回退

| 风险 | 回退手段 |
|------|---------|
| trailing 反而降 win_rate | 调 `interval_params` 表里对应周期的 `atr_multiplier` 或 `activation_profit_atr`；可只调短周期保守化（先收紧 5min/min） |
| 分层 trailing 让长周期 layer 也被打飞 | 把 `interval_params["day"].atr_multiplier=4.0→5.0`；或临时 `interval_params["day"].activation_profit_atr=1.5→2.5` |
| 单品种 30%/cluster 50% 仍嫌大 | 调 `caps.max_symbol_notional_pct=0.30→0.20`、`max_cluster_notional_pct=0.50→0.40` |
| HTF 硬过滤过严 | `fallback_when_htf_missing="skip"→"both"`；或 `enable_htf_gate=False` |
| 金字塔加剧回撤 | `min_profit_atr_to_add=1.0→1.5` 或 `enable_pyramid=False` |
| 风控档过敏感 | `recovery_hysteresis=0.02→0.005`；或调宽 `levels` 的 dd 区间 |
| 校准在新品种上失真 | 校准缺失自动 fallback 到 raw score；或 `enable_score_calibration=False` |
| ranker 阈值过滤过多 | `score_threshold_baseline=0.45→0.30`（在 normal 档下生效） |
| 全模块一键关闭 | `PortfolioLogicConfig.enable_*=False`（除 caps 外）→ 走原 FCFS + 固定 stop + 3 笔上限路径 |

---

## 17. 给 codex 的执行指引

1. **不修改主 vnpy 仓代码**：所有改动在 `cta/` 下
2. **保持向后兼容**：现有 OOT 报告字段名不变；新字段以 `portfolio_logic_` / `pyramid_` / `throttle_` / `score_pctl_` 前缀
3. **代码风格参考**：[model_oot_eval_config.py](../config/model_oot_eval_config.py)（`dataclass(frozen=True)` + `__post_init__` 校验）、[cluster_model_registry.py](../model/cluster_model_registry.py)（loguru 日志、清晰 type hint）
4. **测试 framework**：unittest 风格（与现有 `test_cluster_model_registry.py` 一致），放在 `cta/portfolio_logic/tests/`
5. **不引入新依赖**：复用 pandas / numpy / sklearn / loguru / joblib
6. **不自动 commit**（cta 项目工作流：feature 分支开发，禁止自动 commit，等人 review）
7. **每完成一个 Pillar**：跑 `python -m pytest cta/portfolio_logic/tests/ -v` 加 `python -m pytest cta/ -v`，全绿才能进下一 Pillar
8. **配置默认值**：所有 `enable_*` 默认 True；OOT 第一次跑建议先 `enable_pyramid=False`、`enable_risk_throttle=False` 跑对照，再逐项开
9. **校准 joblib 落盘约定**：与同目录 model joblib 同名前缀，后缀 `_calibration.joblib`
10. **导入约定**：`from cta.portfolio_logic import HtfGate, ...`（顶层 `__init__.py` 显式 re-export 主类）

---

## 18. 验证清单（人工核查）

- [ ] `cta/portfolio_logic/` 9 个核心 .py + 9 个 test .py 都存在
- [ ] [model_oot_eval_config.py](../config/model_oot_eval_config.py) 含 `portfolio_logic: PortfolioLogicConfig` 字段
- [ ] `python -m pytest cta/portfolio_logic/tests/ -v` 全绿
- [ ] `python -m pytest cta/ -v` 整体 ≥ 现有测试数（持平或更多）
- [ ] `bash cta/run.sh group_pool` 跑通，`cluster_registry.json` + 各 `*_calibration.joblib` 都生成
- [ ] OOT 在 20260514 数据集上 `net_pnl` 不下降
- [ ] OOT 报告 `_oot_trade_details.csv` 多出列：`layer_id`、`exit_reason ∈ {hard_stop, trailing_stop, horizon_exit}`、`layer_interval`、`score_components`、`score_pctl_at_entry`、`throttle_level_at_entry`
- [ ] **v3**：同一 `pos_id` 在 trade_details 内可能出现 1~max_lifetime_layers 行，时间不同（按周期分批退）
- [ ] **v3**：OOT 报告新增 `_oot_position_lifetime.csv`，每行一个 PyramidPosition，列：`pos_id, symbol, direction, first_entry, last_exit, layer_count, max_active_layers, peak_notional`，便于验证"长周期 layer 活更久"
- [ ] `cta/report/portfolio_logic/{run_tag}_throttle_log.csv` 生成且每行有 level
- [ ] sim_runner 启动 log 含 `portfolio_logic enabled: htf=T ranker=T trail=T pyramid=T calib=T throttle=T`
- [ ] `grep -rn "max_concurrent_positions_per_symbol" cta/` 除配置文件外均标注 `# deprecated, replaced by PyramidConfig.max_layers`

---

## 19. 关键文件总览

### 19.1 新建

```
cta/portfolio_logic/__init__.py
cta/portfolio_logic/config.py
cta/portfolio_logic/portfolio_state.py
cta/portfolio_logic/interval_gate.py
cta/portfolio_logic/opportunity_ranker.py
cta/portfolio_logic/trailing_exit.py
cta/portfolio_logic/pyramid_manager.py
cta/portfolio_logic/score_calibrator.py
cta/portfolio_logic/risk_throttle.py
cta/portfolio_logic/tests/__init__.py
cta/portfolio_logic/tests/test_config.py
cta/portfolio_logic/tests/test_portfolio_state.py
cta/portfolio_logic/tests/test_interval_gate.py
cta/portfolio_logic/tests/test_opportunity_ranker.py
cta/portfolio_logic/tests/test_trailing_exit.py
cta/portfolio_logic/tests/test_pyramid_manager.py
cta/portfolio_logic/tests/test_score_calibrator.py
cta/portfolio_logic/tests/test_risk_throttle.py
cta/portfolio_logic/tests/test_integration.py
```

### 19.2 修改

| 文件 | 改动 |
|------|------|
| [cta/config/model_oot_eval_config.py](../config/model_oot_eval_config.py) | 追加 `portfolio_logic: PortfolioLogicConfig` |
| [cta/model/pipeline_oot_evaluation.py](../model/pipeline_oot_evaluation.py) | 替换主循环（FCFS → ranker + gate + pyramid + trailing + throttle）；替换持仓状态；新增 trailing 路径 |
| [cta/model/cluster_model_registry.py](../model/cluster_model_registry.py) | `load()` 同时加载 `*_calibration.joblib`；`predict_proba()` 加 `_pctl` 列 |
| [cta/model/model_pipeline.py](../model/model_pipeline.py) | 每个 gate 训练后立刻拟合并 dump `*_calibration.joblib` |
| [cta/model/feature/candidate_training_dataset.py](../model/feature/candidate_training_dataset.py) | 候选行携带 `atr_pct_at_entry` |
| [cta/feature/volatility.py](../feature/volatility.py) | 确保输出 `atr_pct` 列 |
| [cta/sim/sim_runner.py](../sim/sim_runner.py) | 集成 portfolio_logic（按 §5 的 8 步流程） |
| [cta/live/live_runner.py](../live/live_runner.py) | 同上 |

### 19.3 不修改

- vnpy 主仓任何文件
- [cta/model/trade_filter_model.py](../model/trade_filter_model.py) 等 4 个 gate 模型本身（不改训练算法、不改 save/load 字段）
- [cta/model/cluster_model_registry.py](../model/cluster_model_registry.py) 的 **routing 逻辑**（_cluster_group_key、resolve_group、resolve_model_dir 完全不动）；§19.2 列出的扩展仅是新加 `_calibrators` 字段和 `predict_proba` 的 `_pctl` 列输出，对现有调用方零破坏

> **v4 修 P3-1**：v3 中 §19.2 和 §19.3 同时列了 cluster_model_registry.py，看似矛盾。本节澄清：**扩展，不替换 routing**。

---

## 20. OOT vs sim/live 一致性保证（v4 新增，修 P3-2 + 缺失 #2）

v3 §0 提到"OOT、仿真、实际一样的逻辑"但没有落地机制。本节集中说明。

### 20.1 一致性原则

所有 `cta/portfolio_logic/` 模块（除 `PortfolioState` 外）必须 **stateless**，即：
- 不持有可变 instance 字段（仅 `__init__` 时存只读 cfg / 校准常量）
- 所有状态变更通过传入的 `PortfolioState` 表达
- 同一 cfg + 同一 bar 序列 → `state.trade_log` 演化完全相同

OOT 与 sim/live 唯一的差异**只能**出现在：
| 维度 | OOT | sim / live |
|------|-----|-----------|
| 数据驱动 | 批量 DataFrame，时间排序 | 事件流，单 bar 触发 |
| 时钟来源 | bar.datetime | wall-clock，对齐到 bar close |
| 候选 collect | `entries_map[ts]` 切片 | strategy 累积 last-bar 出来的 candidate |
| 价格 fill | 用 bar OHLC 模拟 intrabar | 真实 broker fill |
| 模型 predict | 一次性 vectorized batch | 每 bar 调一次 `predict_proba` |

但所有这些差异都被封装在调用 `portfolio_logic` 之前的层。**进入 portfolio_logic 时，state + bar + candidates 三个输入完全相同 → 输出必然相同**。

### 20.2 强制契约（代码必须实现）

1. **同一份 PortfolioState 类**：OOT、sim_runner、live_runner 都 import `cta.portfolio_logic.PortfolioState`，**绝不能在 OOT 里另写一个简化版**
2. **同一份 ScoreCalibrator**：从 `cluster_registry.json` 寻址同一批 `*_calibration.joblib`
3. **同一份 cfg 序列化**：OOT 跑完把 `PortfolioLogicConfig` 序列化为 JSON 落盘到 `cta/report/.../portfolio_logic_config.json`；sim_runner 启动时**强制读这个文件**，不允许在 sim 里手动写 cfg
4. **stateless 检查**：`test_modules_are_stateless.py` 扫描所有模块，断言 `dataclass` 之外的类没有 mutable instance attribute

### 20.3 不一致风险点 + 防御

| 风险 | 防御 |
|------|------|
| 时钟漂移：OOT 用 bar close 时刻，sim 用 wall-clock，**毫秒级误差**导致 trailing/horizon 判定不同 | 强制对齐：sim_runner 收到 bar 后，把 `current_bar.time = bar.close_time`（不用 wall-clock）传入 portfolio_logic |
| 模型版本：OOT 用 X 模型，sim 部署成 X+1 漂移 | sim_runner 启动 log 必须打印每个 model 的 joblib `md5`；live 加 PR check：joblib hash 必须与 OOT 报告中一致才允许部署 |
| HTF state 计算：OOT 批量 join `(symbol, time)`，sim 流式增量更新 | 用同一个 `HtfGate.compute_htf_state` 函数；OOT 把每个时刻都调一次（不预先 join），与 sim 路径完全一致 |
| 候选行的 cluster 列：OOT 可能预算好，sim 临时算 | `ScoreCalibrator.transform` 内部自动 `df["cluster"] = symbol.apply(infer_symbol_cluster)`（v4 P1-4 已修），三处一致 |
| numpy / pandas 随机性 | portfolio_logic 不引入随机；若引入必须 `seed = run_tag`，sim/live 也传同一 seed |

### 20.4 parity test 设计

`cta/portfolio_logic/tests/test_oot_sim_parity.py`：

```python
def test_oot_sim_parity_basic():
    # 1) 准备：10 个 base_interval bar、2 个 symbol、3 个 interval 的合成数据
    bars = make_synthetic_bars(...)
    candidates = make_synthetic_candidates(...)
    cfg = PortfolioLogicConfig()  # 全默认

    # 2) OOT 路径：批量
    state_oot = PortfolioState(equity=1_000_000, base_interval="60min")
    run_oot_pipeline(state_oot, bars, candidates, cfg)

    # 3) sim 路径：逐 bar
    state_sim = PortfolioState(equity=1_000_000, base_interval="60min")
    for bar in bars:
        on_bar_event(state_sim, bar, candidates_by_ts[bar.time], cfg)

    # 4) 断言：trade_log 完全一致（按 entry_time + layer_id 排序对比）
    assert state_oot.trade_log == state_sim.trade_log
    assert state_oot.equity == pytest.approx(state_sim.equity, rel=1e-9)
```

任何 portfolio_logic 模块改动都必须保持 parity test 绿色。

---

## 21. 冷启动 / 热重启协议（v4 新增，修缺失 #3）

OOT 是一次性批跑，无冷启动问题。sim/live 必须支持重启。

### 21.1 PortfolioState 序列化

```python
@dataclass
class PortfolioState:
    ...
    def to_dict(self) -> dict:
        """JSON-serializable snapshot。trade_log 默认不入快照（另存 csv）。"""
        return {
            "equity": self.equity, "cash": self.cash,
            "base_interval": self.base_interval,
            "positions": {pid: pos.to_dict() for pid, pos in self.positions.items()},
            "htf_state": {f"{s}|{e}": v for (s, e), v in self.htf_state.items()},
            "per_symbol_count": {f"{s}|{e}": n for (s, e), n in self.per_symbol_count.items()},
            "per_cluster_count": dict(self.per_cluster_count),
            "per_symbol_notional": {f"{s}|{e}": n for (s, e), n in self.per_symbol_notional.items()},
            "per_cluster_notional": dict(self.per_cluster_notional),
            "total_open_notional": self.total_open_notional,
            "halt_close_done": self.halt_close_done,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PortfolioState":
        # 反向构造，注意 PyramidPosition / Layer 也要 round-trip
        ...

# 落盘：每个 base_interval bar 收盘后写一次
state_snapshot_path = CTA_LIVE_ROOT / "state_snapshot.json"
state_snapshot_path.write_text(json.dumps(state.to_dict(), default=str, indent=2))
```

### 21.2 sim/live 热启动顺序

```python
def bootstrap_runner(run_tag: str):
    # 1) 校准 + 模型
    registry = ClusterModelRegistry.from_run_tag(run_tag)
    calibrator = registry.get_calibrator()

    # 2) cfg 从 OOT 落盘文件恢复（不允许手写）
    cfg = PortfolioLogicConfig.from_json(CTA_REPORT / f"{run_tag}_portfolio_logic_config.json")

    # 3) PortfolioState：优先从 snapshot 恢复，没有则 bootstrap_from_broker
    if state_snapshot_path.exists():
        state = PortfolioState.from_dict(json.loads(state_snapshot_path.read_text()))
        # 校验 broker 真实持仓 vs snapshot.positions：不一致就 fatal
        reconcile_with_broker(state, broker_client)
    else:
        state = PortfolioState(
            equity=broker_client.get_account_equity(),
            base_interval=cfg.base_interval,
        )

    # 4) EquityTracker：从历史净值文件恢复
    tracker = EquityTracker.bootstrap_from_broker(
        initial_equity=state.equity,
        history_path=CTA_LIVE_ROOT / "equity_history.csv",
        base_interval=cfg.base_interval,
    )

    # 5) 回放最近 N 根 base_interval bar，让 HTF state / trailing running_high 等"预热"
    for bar in load_recent_bars(symbol_universe, count=cfg.warmup_bars):
        run_one_bar(state, tracker, bar, cfg, registry, allow_orders=False)  # 仅 update，不下单

    return state, tracker, cfg, registry
```

### 21.3 broker reconciliation

热启动时，**broker 真实持仓** 必须与 `state.positions` 校对：
- 仓位手数完全一致 → 正常
- broker 多出仓位（如人工手动开了仓） → log warning + 把该仓位作为"外部仓位"加入 `state.positions` 但标 `external=True`，trailing 不接管
- snapshot 多出仓位 → fatal，停止启动并人工介入

### 21.4 持久化频率

- `state_snapshot.json`：每个 base_interval bar 收盘后写一次（覆盖）
- `equity_history.csv`：每个 base_interval bar append 一行
- 每日收盘额外打一份带日期后缀的备份 `state_snapshot.YYYY-MM-DD.json`

---

## 22. 灰度策略（v4 新增，修缺失 #4）

### 22.1 enable_* 开启顺序（推荐）

每开一项观察 5-10 个交易日（OOT 验证后才上 sim → live）：

| 阶段 | 配置 | 验证目标 |
|------|------|---------|
| Day 0（baseline） | 全部 enable_*=False | 与现有 [pipeline_oot_evaluation.py](../model/pipeline_oot_evaluation.py) 行为字节级一致（`test_backward_compat.py` 把关） |
| Day 1-5 | enable_score_calibration=True，其它仍 False | candidate 表多出 `*_pctl` 列；不影响交易决策（开关均 off）；验证校准 joblib 加载、percentile 范围正确 |
| Day 6-15 | + enable_ranker=True | 决策从 FCFS 改为 score-排序；net_pnl 应 ≥ baseline；observe reject_reason 出现 `ranker_dropped` |
| Day 16-25 | + enable_trailing=True + enable_horizon_extend=True | average_holding_bars 应显著 ↑；exit_reason 出现 trailing_stop |
| Day 26-35 | + enable_htf_gate=True | reject_reason 出现 htf_blocked；若 reject_rate > 50% 视为过严，回退到 fallback="both" |
| Day 36-50 | + enable_pyramid=True | trade_details 出现 layer_id>0；单 symbol notional 上限不被穿透 |
| Day 51+ | + enable_risk_throttle=True（全开） | throttle_log.csv 生成；DD 期间档位切换符合预期 |

### 22.2 每阶段的滚动验证（critical）

每个阶段都跑 4 个对照实验：
1. **本阶段 cfg + 历史 OOT 数据**：net_pnl / win_rate / max_dd vs baseline
2. **本阶段 cfg + 上阶段数据**：保证向后兼容
3. **本阶段 cfg + sim 实盘 5 天**：parity check 通过率
4. **关掉本阶段最新开关 + 同样数据**：回退性证明

### 22.3 回滚条件（自动触发）

OOT 上线检查脚本（建议自动化）：

| 触发条件 | 回滚动作 |
|---------|---------|
| net_pnl < baseline × 0.9 | 退回上一阶段配置 |
| win_rate 跌 > 5 pp | 退回上一阶段 |
| max_dd 增 > 3 pp | 退回上一阶段 |
| sim parity test 失败率 > 1% | 立即停 sim、查 timestamp / model hash |
| 单笔交易额超 30% equity | 立即停、查 enforce_notional_caps 实现 |
| 单 cluster notional 超 50% equity | 立即停、查 ranker.allocate 路径 |

### 22.4 enable_* 间的依赖矩阵

| 依赖 | 强制 | 推荐 |
|------|------|------|
| `enable_pyramid` → `enable_trailing` | ✅（`__post_init__` 拒绝） | — |
| `enable_risk_throttle` → `enable_score_calibration` | ✅（`__post_init__` 拒绝） | — |
| `enable_ranker` → `enable_score_calibration` | ❌（ranker 可用 raw prob fallback） | ✅（pctl 让评分更公平） |
| `enable_htf_gate` → 训了 day + 60min regime model | ❌（运行时自动 fallback） | ✅（没训就直接 skip） |
| `enable_horizon_extend` → `enable_trailing` | ❌（独立） | ✅（趋势保持是组合策略） |

---

## 23. 关键参数敏感度（v4 新增，修缺失 #5）

### 23.1 高敏感参数（调一次必须做 sanity check）

| 参数 | 默认 | 影响 | 调整方向 | sanity check |
|------|------|------|---------|--------------|
| `TrailingExitConfig.interval_params["day"].atr_multiplier` | 4.0 | day layer 持仓时长、capture mfe 比例 | 升高 → 持有更久；降低 → 更早锁利 | 跑 OOT 对比 `average_day_layer_holding_bars` 与 `mfe_capture_ratio` |
| `IntervalTrailingParams.activation_profit_atr` | 1.5（day） | trailing 何时接管 hard stop | 升高 → trailing 激活更晚，hard_stop 起效久；降低 → 快速锁利 | 检查 `trailing_activated_ratio` ∈ [0.3, 0.7] 健康 |
| `ThrottleLevel.score_threshold`（normal=0.45） | 0.45 | normal 档 entry 严苛度 | 升高 → entry 数 ↓、单笔质量 ↑；降低 → entry 多但鱼龙混杂 | 升降 0.05 后跑 OOT 看 entry 数和 win_rate |
| `ThrottleLevel.min_prob_pctl`（normal=60） | 60 | normal 档 prob 单项门槛 | 升高 → 拒绝中等 prob 候选 | 看 reject_reason `prob_pctl_too_low` 比例 |
| `RiskThrottleConfig.recovery_hysteresis` | 0.02 | 档位回升的迟滞 | 升高 → 档位粘性强；降低 → 更敏感 | `oot_throttle_transitions_count` 健康范围 0-20/年 |
| `PyramidConfig.min_profit_atr_to_add` | 1.0 | 加层最小浮盈 | 升高 → 加层稀少；降低 → 容易加错 | 看 `avg_layers_per_position` 健康 1.5-2.5 |
| `CapsConfig.max_cluster_notional_pct` | 0.50 | 单 cluster 集中度上限 | 升高 → 允许 cluster 集中；降低 → 强制分散 | 看 `max_cluster_notional_observed` 是否撞 cap |
| `IntervalGateConfig.fallback_when_htf_missing` | "skip" | HTF 缺失时是否放行 | "both" → 缺失视为允许 | 看 `htf_block_reason="missing"` 比例 |

### 23.2 中敏感参数（首次调可不必专项验证）

| 参数 | 默认 | 备注 |
|------|------|------|
| `OpportunityRankerConfig.w_*` | 0.4/0.3/0.2/0.1 | 修必须 sum=1.0 |
| `PyramidConfig.size_decay` | (1, 0.5, 0.25, 0.15, ...) | 太陡 → 后层无意义；太平 → 加仓激进 |
| `PyramidConfig.cooldown_bars_per_interval` | day=3, 60min=4, ... | 调小风险大 |
| `EquityTracker.max_history_bars` | 10000 | 仅影响内存 |
| `HorizonExtendConfig.max_extensions` | 3 | trend 行情中持仓时长 = horizon × (1+extensions) |

### 23.3 低敏感（基本不动）

- `IntervalGateConfig.state_ttl_seconds`（合理范围 0.5-2×interval）
- `CalibrationStats.min_reliable_samples`（500 是经验值）
- `EquityTracker.min_bars_for_weekly / monthly`（7 / 30 已合理）

### 23.4 调参流程

1. **永远先在 OOT 上调**：调一项 → 跑 OOT → 看对比表（§15.2）
2. **一次只调一个**：多个参数同改难以归因
3. **保留实验日志**：每个 cfg 落盘到 `cta/report/portfolio_logic_experiments/{ts}_{tag}/portfolio_logic_config.json`
4. **OOT 验证通过后才进灰度（§22）**

---

## 24. 后续（不在本次范围）

- 自适应 `atr_multiplier`（按 regime 强度动态调节）
- 跨 cluster 资金平衡器（cluster_black 集中时压制 cluster_metal 信号）
- 实时 PnL 仪表盘（sim/live）
- Walk-forward 滚动验证 portfolio_logic 稳健性
- 校准分布漂移检测（runtime KS 检验）
- 引入 IC（信息系数）作为 mfe_mae edge 的二次校准
- 跨品种相关性校验（cluster 内品种过强相关时单独压制）
- 在线 calibration 再训练（每月用近 N 天数据增量更新分位）
