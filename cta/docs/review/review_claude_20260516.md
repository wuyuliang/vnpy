# portfolio_logic_design.md 深度 Review（2026-05-16）

> 对 [portfolio_logic_design.md](portfolio_logic_design.md) v3 的彻底审查。共发现 31 项问题，按严重程度分 4 级：
> - **P0**：阻塞性 bug，按现有文档实现会跑不通或语义错。**必须在 W1 之前修文档**。
> - **P1**：实现盲点，代码会被迫做大幅猜测。**建议在对应 Pillar 实施前补齐**。
> - **P2**：可选优化或边界处理。
> - **P3**：文档表述/一致性问题。

---

## P0 — 阻塞性（5 项，必须在 W1 之前修）

### P0-1. RiskThrottle 不影响 PyramidConfig，风控档失效

**位置**：§10.3.2 + §9.5

**问题**：
- `ThrottleLevel` 含 `allow_pyramid: bool` 与 `max_pyramid_layers: int` 字段
- 但 `RiskThrottle.apply_to_caps(base_caps, level)` 只 mutate `CapsConfig`，**没有 mutate `PyramidConfig`**
- §9.5 主循环 `caps_for(throttle_level)` 拿到的是新 caps，但 `cfg.pyramid` 一直是原始 config
- 结果：throttle 进入 `conservative` 档时，`allow_pyramid=False` 完全没生效，pyramid_manager.decide_add_layer 仍按 `cfg.pyramid.enabled=True` 走

**修复**：
```python
class RiskThrottle:
    def apply_to_caps(self, base_caps, level) -> CapsConfig: ...
    def apply_to_pyramid(self, base_pyramid, level) -> PyramidConfig:
        return replace(base_pyramid,
            enabled=base_pyramid.enabled and level.allow_pyramid,
            max_active_layers=min(base_pyramid.max_active_layers, level.max_pyramid_layers),
        )

# 主循环改：
runtime_caps = throttle.apply_to_caps(cfg.caps, throttle_level)
runtime_pyramid = throttle.apply_to_pyramid(cfg.pyramid, throttle_level)
add_size = pyramid.decide_add_layer(pos, pick, ..., cfg=runtime_pyramid)  # 不是 cfg.pyramid
```

### P0-2. score_threshold 与 score_pctl_threshold 单位不一致

**位置**：§7.3 + §10.3.2

**问题**：
- `OpportunityRanker.allocate(score_threshold: float)` — score ∈ [0, 1]（综合分）
- `ThrottleLevel.score_pctl_threshold: float` — pctl ∈ [0, 100]（仅 prob 单项的百分位）
- 主循环 §9.5 写 `caps_for(throttle_level)`，没说怎么把 throttle 的 pctl 阈值变成 ranker 的 score 阈值
- **两个量量纲完全不同，无法直接转换**

**修复**：方案二选一
- **方案 A（推荐）**：ranker 同时接受两个阈值
  ```python
  def allocate(df, state, caps, score_threshold, min_prob_pctl) -> picks:
      df = df[(df.score >= score_threshold) & (df.trade_filter_prob_pctl >= min_prob_pctl)]
      # ...
  ```
- **方案 B**：throttle 输出"等价 score 阈值"（用历史数据查表 prob_pctl=80 ≈ score=0.65）

### P0-3. PortfolioState 接口完全没在文档定义

**位置**：§5、§7.2、§9.5 隐式调用

**问题**：文档用到了下列 `state.*` 方法但**只有 §4 模块布局一行注释**，没有任何接口定义：
- `state.equity` (属性？方法？)
- `state.positions` / `state.positions_by_sym_dir`
- `state.htf_state`
- `state.apply_exits(exits)`
- `state.apply_entries(news, adds)`
- `state.tentative_apply(sym_key, cluster, notional, direction)`
- `state.tentative_total_positions()` / `tentative_cluster_count()` / `tentative_symbol_count()`
- `state.snapshot_for_allocation()`
- `state.has_open_or_picked(sym_key, direction)`
- `state.record_trades(trades)` / `state.record_add(layer, pos)`
- `state.add_position(pos)` / `state.remove_position(pos_id)`

这是 codex 最大盲点。

**修复**：补一节 §X PortfolioState 完整接口（dataclass 字段 + 所有方法签名 + tentative 状态机的 commit/rollback 语义），与 §6-10 同等详细程度。

### P0-4. tentative_apply 无 commit / rollback 协议

**位置**：§7.2

**问题**：
```python
def allocate(...):
    state.snapshot_for_allocation()
    for row in candidates:
        ...
        state.tentative_apply(sym_key, cluster, notional, direction)  # ← 何时变正式？
    return picks
```

- `tentative_apply` 之后没有 `commit` / `rollback` 调用
- 是"试探性"还是"立刻生效"？若中途有候选被拒，前面 tentative_apply 的计数怎么处理？
- `snapshot_for_allocation` 是 save-point？没说怎么对应 restore

**修复**：明确协议
```python
state.begin_allocation()                    # save point
try:
    for row in candidates:
        state.tentative_apply(...)         # 内部修改 _tentative_* 副本
    state.commit_allocation()              # _tentative_* → 主状态
except Exception:
    state.rollback_allocation()            # _tentative_* 丢弃
```
或更简单："`tentative_apply` 立刻生效，picks 返回前不需要回滚（被拒的根本没 apply）"——但要明确选哪种。

### P0-5. 主循环 timeline 颗粒度未定义，EquityTracker 频率会失真

**位置**：§5 + §9.5 + §10.3.1

**问题**：
- 主循环 `for ts in timeline`，但同一时刻有 day bar 和 5min bar 同时到达——`timeline` 是 union 还是按某个 base interval？
- `equity_tracker.on_bar(state.equity, ts)`：day bar 一天 1 次、5min bar 一天 48 次。如果都进 history，weekly_return / drawdown 会被 5min 主导（采样不均匀）
- 实际后果：13 年 1min 数据 → equity_history 长度 100 万+，内存和计算双爆

**修复**：明确两点
1. 选定一个 **base_interval**（建议 60min，与商品流动性匹配）作为 equity/throttle 时钟
2. `EquityTracker.on_bar` 仅在 base_interval 的 bar 触发；其它 interval 的事件不更新 equity（但 trailing 在每个 bar 都跑）
3. `EquityTracker` 加 `max_history_bars` 滚动窗口（参考 P2-2）

---

## P1 — 实现盲点（10 项，对应 Pillar 实施前补齐）

### P1-1. running_high 在 layer 退出后是否单调

**位置**：§8.3 + §9.1

**问题**：
- §9.1 注释 "running_high 仅对 active_layers 有意义"
- §8.3 update_layer_stop 用 `pos.running_high`
- 若 5min layer 在 t=200 退出后，剩下的 day layer 看到的 running_high 是否回退到"仅 day layer 入场后的最高点"？

**风险**：若回退，违反 `update_only_in_favor` 隐含的单调性，trailing stop 会跳变。

**修复**：明确语义——**`running_high` 一旦由任何 active layer 推高，就单调不下调**；layer 退出**不重置** running_high。在 §8.1 加一句。

### P1-2. trailing 首次激活时 hard_stop 与 trailing_stop 的覆盖关系

**位置**：§8.3 + §9.6

**问题**：
```python
layer.layer_stop_price = max(layer.layer_stop_price, new_stop)  # update_only_in_favor=True
```
- 初始 `layer.layer_stop_price = entry × (1 - 4%)` = 96
- trailing 首次激活时 `running_high=110`，`new_stop = 110 - 4 × 1.2% × 110 = 104.7`
- `max(96, 104.7) = 104.7` ✓ 正确
- 但若 ATR 很大（如波动品种 atr_pct=3%），`new_stop = 110 - 4×3.3 = 96.8`，与初始 hard 96 几乎一样 → trailing 没意义

**修复**：
- 文档加注："trailing_activated 翻 True 那一刻，**强制覆盖** hard_stop（不取 max）"
- 或：把 hard_stop 与 trailing_stop 拆成两个字段（`hard_stop_price` 和 `trail_stop_price`），exit 检查时取 `max(hard, trail if activated)`

### P1-3. mfe_mae 校准用 percentile 还是 z-score 语义不清

**位置**：§10.2.2 + §10.2.4

**问题**：
- CalibrationStats 同时存 `percentile_values` 和 `edge_mean/edge_std`
- ScoreCalibrator.to_percentile 只用 percentile_values
- 但 §7.1 评分公式对 edge 用 z-score 标准化（`raw_edge - μ / σ`）→ 用 edge_mean/edge_std

**修复**：明确分工
- `trade_filter` / `final_decision_stack` / `regime_classifier`：用 `percentile_values`（输出 `*_pctl`）
- `mfe_mae`：用 `edge_mean/std`（输出 `edge_z` 或 `edge_norm`，归一到 [0,1]）
- 把 `percentile_values` 和 `edge_*` 改成可选字段，按 `model_kind` 决定填哪个

### P1-4. ScoreCalibrator.transform 需要的 cluster 列从哪来

**位置**：§10.2.4

**问题**：
```python
def to_percentile(self, cluster, interval, model_kind, raw_score):
```
- 需要外部传 cluster
- 但 candidate DataFrame 一般只有 symbol/interval，没有 cluster
- 调用方需先 join `infer_symbol_cluster(symbol)`，文档没说

**修复**：
- `transform(df)` 内部自动 `df["cluster"] = df["symbol"].apply(infer_symbol_cluster)`
- 或要求调用方先 join，并在文档明确

### P1-5. fallback_when_htf_missing="both" 的 htf_alignment 列怎么填

**位置**：§6.1 + §6.4 + §7.1

**问题**：
- HTF=both 时 long 候选和 short 候选都允许
- 但 ranker 评分用 `htf_alignment ∈ {aligned, neutral, opposite}`：HTF=both 时这个值应该是什么？
- 文档没说

**修复**：补表
| htf_state | direction=long | direction=short |
|-----------|---------------|----------------|
| long_only | aligned | (filtered) |
| short_only | (filtered) | aligned |
| both | **neutral** | **neutral** |
| (缺失 fallback=both) | **neutral** | **neutral** |
| (缺失 fallback=skip) | (filtered) | (filtered) |

### P1-6. interval_rank 在 IntervalGateConfig 和评分公式中重复定义

**位置**：§6.4 + §7.1

**问题**：
- §6.4 `IntervalGateConfig.interval_rank: dict[str, float]`
- §7.1 评分公式 `w_rank * interval_rank_weight[interval]`
- 两个是同一份数据吗？

**修复**：明确"`OpportunityRanker.score` 从 `IntervalGateConfig.interval_rank` 取值"，或把 `interval_rank` 提到 `PortfolioLogicConfig` 顶层共享。

### P1-7. weekly_return / monthly_return 在 history 不足时失真

**位置**：§10.3.1

**问题**：
```python
def _period_return_pct(self, days: int) -> float:
    cutoff = self.equity_history[-1][0] - pd.Timedelta(days=days)
    base = next((e for (t, e) in self.equity_history if t >= cutoff), self.equity_history[0][1])
```
- OOT 启动只有 3 天数据时，`weekly_return = equity_now / equity_3天前 - 1`
- 若这 3 天恰巧大跌 5%，会被错误地报告为 weekly_return=-5%，触发 `force_conservative_if_weekly_lt`
- 启动期不应有 throttle 误激活

**修复**：
```python
def _period_return_pct(self, days: int) -> float | None:
    if len(self.equity_history) < self.min_bars_for_period_return:
        return None  # 不足以下结论
    elapsed = self.equity_history[-1][0] - self.equity_history[0][0]
    if elapsed < pd.Timedelta(days=days * 0.5):  # 至少半个周期数据
        return None
    # ... 计算

# RiskThrottle.compute 里：
if snap.weekly_return is None or snap.monthly_return is None:
    skip force_conservative 判断
```

### P1-8. cooldown_minutes 全局统一会让短周期信号过严

**位置**：§9.2

**问题**：
- cooldown_minutes=30
- 对 5min layer：30 分钟 = 6 根 bar（合理）
- 对 day layer：30 分钟 = 0 根 bar（cooldown 形同虚设）
- 对 min layer：30 分钟 = 30 根 bar（也合理）
- 实际问题是 cooldown 在长周期上失效，可能允许 day 信号 1 小时内连续加层

**修复**：
```python
cooldown_bars_per_interval: dict[str, int] = field(default_factory=lambda: {
    "day": 3,    # 至少隔 3 天
    "60min": 4,  # 至少 4 小时
    "30min": 6,
    "15min": 8,
    "5min": 12,
    "min": 30,
})
```
elapsed 校验改为：`bars_since_last >= cooldown_bars_per_interval[new_signal.interval]`

### P1-9. enforce_notional_caps vs enforce_notional_caps_for_add 不一致

**位置**：§7.2 + §9.2

**问题**：
- §7.2 ranker 用 `enforce_notional_caps(row, notional, state, caps)`
- §9.2 pyramid 用 `enforce_notional_caps_for_add(pos, proposed, state, caps, cluster)`
- 两个函数签名不同，是同一个还是两个？

**修复**：明确为同一函数的两种调用形式
```python
def enforce_notional_caps(*, notional, state, caps, sym_key, cluster, direction,
                          existing_for_this_pos=0.0) -> float:
    """统一函数。add layer 时 existing_for_this_pos = pos.total_active_notional。"""
```

### P1-10. halt 档下是否强制平仓

**位置**：§9.6 + §10.3.2

**问题**：
- ThrottleLevel("halt", ..., 0.0, 0.0, 100.0, False, 0)
- `max_total_positions_mult=0.0` → caps.max_total_positions=0 → 不再开新仓
- 但已开的 PyramidPosition 怎么处理？继续按 trailing 自然退出，还是强制 force_close_all？
- §9.6 PyramidManager.force_close_all 存在但主循环 §9.5 没调用

**修复**：明确 halt 档策略（推荐两段式）
```python
@dataclass(frozen=True)
class ThrottleLevel:
    # ... 现有字段
    force_close_all_on_enter: bool = False  # halt 档默认 True

# 主循环 B 之前：
if throttle_level.force_close_all_on_enter and not throttle_state.halt_close_done:
    for pos in state.positions.values():
        pyramid.force_close_all(pos, ...)
    throttle_state.halt_close_done = True
```

---

## P2 — 边界与优化（10 项）

### P2-1. CalibrationStats 在小样本上失真

**位置**：§10.2.2

**问题**：训练集 < 500 样本时，101 个 percentile 点会有大量重复值，`np.searchsorted` 在重复值上的 idx 含糊。

**修复**：
- CalibrationStats 加 `is_reliable: bool`（sample_count ≥ 500）
- ScoreCalibrator.transform 若 stats.is_reliable=False，直接 fallback 到 raw × 100，并 log warning

### P2-2. EquityTracker 内存无限增长

**位置**：§10.3.1

**问题**：`equity_history: list` 无上限。

**修复**：
```python
from collections import deque
@dataclass
class EquityTracker:
    max_history_bars: int = 10000  # 配置项
    equity_history: deque = field(default_factory=lambda: deque(maxlen=10000))
```
但要注意 weekly/monthly_return 的 base 取法可能受影响 → 单独维护 `weekly_base_ts / monthly_base_ts` 字段而非每次扫 history。

### P2-3. throttle_log 落盘频率过高

**位置**：§10.3.3

**问题**：每个 bar 写一行，1min 13 年 ≈ 几百 MB。

**修复**：仅在 level 变化时写一行（带 transition timestamp），每日收盘写 daily summary。

### P2-4. min interval 不应参与加层

**位置**：§9.2

**问题**：min 周期信号 1 分钟一根，即便 cooldown=30，一天 8 次机会，容易把 size_decay 用满。

**修复**：
```python
@dataclass(frozen=True)
class PyramidConfig:
    allowed_intervals_for_add: tuple[str, ...] = ("day", "60min", "30min", "15min")
    # 'min' 和 '5min' 仅作 first_layer
```

### P2-5. force_conservative 触发太敏感

**位置**：§10.3.2

**问题**：weekly_return < -3% 单独触发，对高波动品种太敏感（黑色/有色周波动常 ±5%）。

**修复**：改 OR 为 AND，或抬高阈值
```python
force_conservative_if_weekly_lt: float = -0.05
force_conservative_if_monthly_lt: float = -0.08
require_both_periods: bool = False  # True 时需要 weekly AND monthly 同时下穿
```

### P2-6. 训练时 ScoreCalibrator.fit 用哪段数据未明确

**位置**：§10.2.1

**问题**："训练集（或 holdout fold）"——两个语义不同：
- 训练集：分布偏乐观（模型在它上面 overfit）
- holdout：更符合 OOT 实际分布

**修复**：明确"用 trade_filter 训练时 split 出的 holdout fold（与模型验证用同一段）"。

### P2-7. calibration 文件命名不一致

**位置**：§10.2.3

**问题**：
- §10.2.1 用 `score_calibration.joblib`
- §10.2.3 表格用 `trade_filter_score_calibration.joblib` / `mfe_mae_edge_calibration.joblib`

**修复**：统一为 `{model_kind}_calibration.joblib`（如 `trade_filter_calibration.joblib`、`mfe_mae_calibration.joblib`、`final_decision_stack_calibration.joblib`、`regime_classifier_calibration.joblib`）。

### P2-8. EquityTracker.bootstrap 在 sim/live 热启动未定义

**位置**：§10.3.1（隐式）

**问题**：sim_runner 启动时账户已有持仓与历史净值，EquityTracker.equity_history 从哪开始？running_high 是否取真实历史高点？

**修复**：
```python
class EquityTracker:
    @classmethod
    def bootstrap_from_broker(cls, initial_equity, history_path: Path | None = None):
        """从 broker 接口或历史净值文件恢复"""
        tracker = cls()
        if history_path:
            df = pd.read_csv(history_path)
            for _, row in df.iterrows():
                tracker.on_bar(row.equity, pd.Timestamp(row.ts))
        else:
            tracker.on_bar(initial_equity, pd.Timestamp.now())
        return tracker
```

### P2-9. test_integration 标"6 个支柱"应为"5 个 Pillar"

**位置**：§15.1

**问题**：文档其它地方都是 Pillar 1-5，整合测试写"6 个支柱"。

**修复**：改为 "5 个 Pillar 联合的端到端假数据 OOT"。

### P2-10. ranker.allocate 中 score_components 列在 picks 中丢失

**位置**：§7.2

**问题**：
```python
picks.append(row._replace(allocated_notional=notional, score_components=row.score_components))
```
- `_replace` 是 namedtuple 的方法，但 `score_components` 是 dict，无法做 namedtuple 字段
- DataFrame.itertuples() 返回的 Pandas namedtuple 字段名要合法（无空格、非保留字）

**修复**：picks 用 dict-of-dict 累积，最后 `pd.DataFrame.from_records`，不用 _replace；或 score_components 序列化为 JSON 字符串再放入 row。

---

## P3 — 文档表述与一致性（6 项）

### P3-1. §19.3 与 §19.2 矛盾

**位置**：§19.2 + §19.3

**问题**：
- §19.2 "修改"列含 `cluster_model_registry.py`（load 加 calibration）
- §19.3 "不修改"列又写 `cluster_model_registry.py 的 routing 逻辑（仅扩展 load + predict_proba）`

**修复**：§19.3 删除该项，或改为更精确表述 "不动 routing 接口，仅扩展两个方法"。

### P3-2. v3 修订记录里"OOT/仿真/实际一样的逻辑"没在正文落地

**位置**：§0

**问题**：用户明确要求"注意回测计算OOT、仿真、实际一样的逻辑"，但文档没有专章说明一致性保证机制。

**修复**：新增 §X "OOT vs sim/live 一致性保证"
- 所有 portfolio_logic 模块 stateless（state 由外部传入）
- 三个 runner 共用同一份 PortfolioState 类
- 加 `test_oot_sim_parity.py`：相同 bar 序列 + 相同 cfg → 断言 trade_details 完全一致

### P3-3. §5 提到"8 步流程"，实际只有 4 个分组

**位置**：§5 + §13.2

**问题**：
- §5 标注 4 个分组（1) equity, 2) exits, 3) entries, 4) split picks）
- §13.2 写 "OOT 主循环按 §5 的 8 步流程跑"

**修复**：把"8 步"改为"标准流程"或重新拆 8 步并在 §5 编号。

### P3-4. §15.2 指标表中"max_concurrent PyramidPositions 峰值 < 10"未解释为什么

**位置**：§15.2

**问题**：baseline 是 10（FCFS 满载），new < 10 是因为 cluster cap 限制了同 cluster 集中。文档没说原因，看 reviewer 会困惑"难道不应该和 baseline 一样吗"。

**修复**：加备注"new < 10 因为 max_total_per_cluster=4 + max_cluster_notional_pct=0.50 让组合更分散"。

### P3-5. §14 实施依赖链中 "Pillar 3 trailing 必须先于 Pillar 4 pyramid（pyramid 退出依赖 unified trailing）"

**位置**：§14

**问题**：v3 已经把"unified trailing"改成"per-layer trailing"，但依赖链文字没更新。

**修复**：改成"pyramid 退出依赖 per-layer trailing"。

### P3-6. §11.1 漏洞 #7 已过时

**位置**：§11.1

**问题**：v2 漏洞 #7 "unified_stop 初始化未定义 → §8.3 init_layer_stop()"，但 v3 已经把 unified_stop 改成 per-layer stop。表述应该更新。

**修复**：改成"#7（v2 修补、v3 适配）：每个 layer 的 hard_stop 初始化定义清楚（§8.3 init_layer_stop）"。

---

## 总结与建议执行顺序

### 立即修文档（W1.1 之前）
- 全部 P0 (5 项)
- P1-1, P1-2, P1-3, P1-4 (running_high 单调、trailing 覆盖、mfe_mae 校准、cluster 列来源)

### W2 之前修
- P1-5, P1-6, P1-7, P1-8 (htf_alignment 表、interval_rank 唯一源、return 启动期、cooldown 跨 interval)

### W3 之前修
- P1-9, P1-10 (notional caps 统一、halt 档策略)

### 各 Pillar 实施时关注
- P2-1, P2-2 (校准小样本、equity 内存) — Pillar 5
- P2-3, P2-4, P2-5 (throttle log、min interval、force_conservative) — Pillar 5 + Pillar 4
- P2-6, P2-7, P2-8 (校准 fit 数据、命名、bootstrap) — Pillar 5
- P2-9, P2-10 (文档错字、namedtuple bug) — Pillar 2

### 文档质量
- P3-1 ~ P3-6 全部一次性修

---

## 缺失但应该有的内容

1. **§X PortfolioState 完整接口规范**（对应 P0-3）
2. **§X OOT vs sim/live 一致性保证机制**（对应 P3-2）
3. **§X cold-start / warm-restart 协议**（sim/live 重启时 EquityTracker / PortfolioState / pyramid_positions 如何恢复）
4. **§X 灰度策略**：每个 enable_* 的灰度开启顺序与回滚条件
5. **§X 关键参数敏感度**：哪些参数最敏感（atr_multiplier、score_pctl_threshold、recovery_hysteresis），调一次要做的 sanity check 列表
6. **`test_oot_sim_parity.py`**：reproducibility / cross-runner 一致性测试
7. **`test_backward_compat.py`**：`enable_*=all False` 时与现有 OOT 行为字节级一致

---

## 与 v3 修订记录的对照

v3 §0 写"自我 review 增 4 处漏洞修正"，本次 review 又找出 31 处，说明：
- 14 (v2) + 4 (v3) 处已修，但**主要补的是技术细节**
- 系统性盲点（PortfolioState 接口、RiskThrottle 与 Pyramid 联动、单位不一致、OOT/sim 一致性）**还没被自我 review 捕获**
- 建议每次大改后用本 review 模板做一遍跨 Pillar 联动检查

> 后续请继续阅读并执行 [review_claude_20260516_v2.md](review_claude_20260516_v2.md) 的增量检查项。

完文。
