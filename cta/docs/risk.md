# CTA 风控系统设计 — `cta/risk/`

> 配套代码 `cta/risk/`；plan 见 [`cta-enumerated-pancake.md`](../../../../.claude/plans/cta-enumerated-pancake.md)；
> 实施日期 2026-05-29。本文档 ~380 行，长度按 `cta/docs/ma_cross_regime_aware_design.md` 体例。

---

## §0 修订记录

| 日期 | 版本 | 内容 |
|------|------|------|
| 2026-05-29 | v1 | 初稿：3 子系统（quantile/bucket/linear+bump）+ orchestrator + state 层落地 |

设计动机来自 2026-05-28 OOT 复盘 + sim/live review（[`review/20260528.md`](review/20260528.md)）：
现有 3 层风控（pre-trade `_BaseRule` / `RiskThrottle` / `symbol_disable_manifest`）覆盖
"硬拒 + 4 档大类回撤 + 全剔除"，但缺：(a) 按 (cluster, symbol) 分位数动态阈值；
(b) 桶级长期亏损追踪 → 持续缩仓；(c) 周回撤起线性降仓 + 模型阈值同步抬高。

---

## §1 现状盘点

| 层 | 文件 | 颗粒度 | 触发 | 行为 |
|----|------|--------|------|------|
| pre-trade 硬拒 | [`cta/live/risk.py`](../../live/risk.py) 6 条 `_BaseRule` | 单笔订单 | order 字段不合规 | 拒单 |
| 累计回撤节流 | [`cta/portfolio_logic/risk_throttle.py`](../../portfolio_logic/risk_throttle.py) | 组合层 | dd 5/10/15% 4 档 | 缩 caps（max_total_positions / cluster cap / notional_pct） |
| 持久剔除 | `cta/feature/symbol_disable_manifest.csv` | 单 symbol | 多 OOT 持续亏 | 完全不交易 |

未覆盖的盲区：
1. **模型分阈值**只按 (cluster, interval) 配置（bond strict default-on 已到 80pp），同一
   cluster 内不同 symbol 共用阈值；
2. **alpha 监控**只有"全剔除"档（要么交易要么不交易）；没有"亏多了缩仓但保留"的中间态；
3. **小回撤区间**（dd < 5%）完全不动作；用户希望"1% 起就开始线性降仓 + 模型阈值同步抬高"。

---

## §2 架构总览

```
                                     SignalContext
                                          │
                                          ▼
                ┌────────────────────────────────────────────────┐
                │  ① threshold layer（cta/risk/threshold/）      │
                │     StaticThresholdAdjuster                    │
                │       → QuantileThresholdAdjuster              │ ← state/ScoreQuantileManifest
                │       → DynamicBumpAdjuster                    │ ← portfolio["effective_dd_pct"]
                │     effective_threshold = chain output         │
                │     if score < threshold: BLOCK                │
                └────────────────────────────────────────────────┘
                                          │
                                          ▼
                ┌────────────────────────────────────────────────┐
                │  ② sizing layer（cta/risk/sizing/）             │
                │     BucketScalingSizer                         │ ← state/BucketPnlTracker
                │       → LinearDdScaler                         │ ← state/DdSignalProvider
                │       → PortfolioThrottleSizer (optional)      │ ← RiskThrottle level
                │     adjusted_lots = Π(scalers)                 │
                │     if lots <= 0: BLOCK                        │
                └────────────────────────────────────────────────┘
                                          │
                                          ▼
                                  AdjustedDecision

                ┌────────────────────────────────────────────────┐
                │  ③ pre-trade guards（cta/live/risk.py）         │  保留原样，不在 orchestrator
                │     6 条 _BaseRule + RiskGuard 短路评估         │  作用在 sim/live 订单出口
                └────────────────────────────────────────────────┘
```

设计要点：
- **三层独立 enable**：`RiskSystemConfig` 提供 4 个 enable 开关（quantile / bucket / linear_dd / dynamic_bump）
- **同一份 cfg 三层消费**：OOT eval / sim / live 都构造同一个 `RiskOrchestrator`，行为一致
- **state 缺失 → fail-open**：任何 manifest / state json 缺失，退化到 base_threshold，不锁单

---

## §3 子系统①：quantile threshold

### §3.1 训练时产 manifest

[`cta/risk/state/score_quantile_manifest.py:build_from_predictions`](../../risk/state/score_quantile_manifest.py)
读 train+valid（不含 test/oot）split 的 `trade_filter_prob`，按 (cluster, symbol, interval)
+ (cluster, "*", interval) 聚合，输出 JSON：

```json
{
    "schema_version": 1,
    "generated_at": "2026-05-29T10:00:00",
    "entries": [
        {"cluster": "metal", "symbol": "CU0", "interval": "day",
         "p50": 0.51, "p60": 0.55, "p70": 0.60, "p80": 0.66,
         "p90": 0.74, "p95": 0.81, "sample_count": 1200}
    ]
}
```

`run_tag + git_sha` 由调用方传入 `meta=` 一起入 JSON，便于复盘归档。

### §3.2 OOT/sim/live 时查 manifest

[`cta/risk/threshold/quantile_threshold.py:QuantileThresholdAdjuster`](../../risk/threshold/quantile_threshold.py)
查询优先级：
1. 精确 (cluster, symbol, interval)
2. cluster-wide fallback (cluster, `"*"`, interval)
3. 返回 `base_threshold`（caller 用 OotEvaluationConfig 的 70pp 默认）

`quantile_field="p70"` 时输出 70.0 pctl，**与现状等价**——只是从"全局 P70 排名"换成
"每个 (cluster, symbol) 自己的 P70"，门槛严苛度不变。

### §3.3 fail-open

- manifest JSON 缺失 / 损坏 → 返回 base（已在 unit test 覆盖）
- entry 命中但 quantile 字段 NaN → 返回 base
- 候选 cluster/symbol/interval 不全 → 返回 base

---

## §4 子系统②：bucket-level PnL 监控 + 缩仓

### §4.1 粒度选择

按 (cluster, interval, score_bucket) —— 共 8 × 6 × 4 = 192 桶。bucket 由
`trade_filter_prob_pctl` 落入：

| edge | bucket name |
|------|-------------|
| [60, 70) | `p60-70` |
| [70, 80) | `p70-80` |
| [80, 90) | `p80-90` |
| ≥ 90 | `p90+` |

< 60 直接不进桶（也不缩仓，认为是边缘候选，不进监控）。

### §4.2 滚动 PnL 追踪

[`cta/risk/state/bucket_pnl_tracker.py:BucketPnlTracker`](../../risk/state/bucket_pnl_tracker.py)：
- **窗口**：默认 30 天（cfg.bucket_window_days）
- **bp 单位**：`pnl_bp = net_pnl / portfolio_equity * 10_000` —— 跨账户大小通用
- **持久化**：JSON 文件 `cta/run/state/bucket_pnl_state.json`；每日 save() 一次
- **on_trade 时机**：close trade（FIFO 配对完成）→ append；查询时 evict 超窗
- **fail-open**：JSON 损坏 / 缺失 → 空 tracker；equity ≤ 0 → 跳过

### §4.3 缩仓规则

[`cta/risk/sizing/bucket_scaling.py:BucketScalingSizer`](../../risk/sizing/bucket_scaling.py)：

| pnl_bp 区间 | mult |
|-------------|------|
| < -10bp | 0.5 |
| [-10, -5) | 0.7 |
| [-5, 0) | 0.9 |
| ≥ 0 | 1.0 |
| trade_count < 20 | 1.0（样本不足）|
| 任意区间下限 | floor = 0.3 |

**round-down 保护**：`1 lot × 0.5 = 0` 时返回 1，避免单笔被缩到 0。

### §4.4 默认 off

`enable_bucket_scaling=False`（默认）—— 等 sim soak 跑出 30 日 ground truth 把 tracker
预热好再 default-on。设计早开会因为大量"trade_count < min"长期 mult=1.0 不产生效用。

---

## §5 子系统③：linear DD 缩仓 + dynamic bump 阈值抬高

### §5.1 dd 信号

[`cta/risk/state/dd_signal_provider.py:DdSignalProvider`](../../risk/state/dd_signal_provider.py)：

| 信号 | 算法 |
|------|------|
| `cumulative_dd_pct()` | 复用 `EquityTracker.current_drawdown_pct()`（running_high 起算）|
| `weekly_dd_pct()` | 近 N 天（默认 7）的 running_high → 当下 equity 的回撤 |
| `effective_dd_pct()` | `max(cumulative, weekly)` ← **用户决策** |

**用户决策的好处**：
- 单纯看 cumulative：很久前的高点导致很高 dd，但当前已恢复，仍持续过度防守
- 单纯看 weekly：周内回撤已很大但当前周才开始，cumulative 视而不见
- 取 max：抓"近 1 周 + 历史最大"两种最坏情况

### §5.2 LinearDdScaler

[`cta/risk/sizing/linear_dd_scaler.py:LinearDdScaler`](../../risk/sizing/linear_dd_scaler.py)：

```
dd in [0, 1%)     → mult = 1.00
dd in [1%, 2%)    → mult = 0.90
dd in [2%, 3%)    → mult = 0.80
...
dd >= 10%         → mult = 0.10 (floor)
```

参数 (`trigger_pct`, `step_pct`, `step_mult`, `floor_mult`) 全部可配。

### §5.3 DynamicBumpAdjuster

[`cta/risk/threshold/dynamic_bump.py:DynamicBumpAdjuster`](../../risk/threshold/dynamic_bump.py)：

```
dd in [0, 1%)     → bump = 0pp
dd in [1%, 2%)    → bump = +5pp
dd in [2%, 3%)    → bump = +10pp
...
封顶 cap_pp = 25pp（累计抬高上限）
封顶 threshold_cap_pp = 95pp（最终阈值天花板）
```

例：base=70 + dd=3% → bump=10 → 80；base=85 + dd=3.5% → bump=12 → 97 → cap 95。

### §5.4 与现有 RiskThrottle 共存

| 维度 | 现有 RiskThrottle | 新 LinearDdScaler |
|------|------------------|-------------------|
| 颗粒 | 组合 caps（max_positions / cluster / notional） | 单笔 lots |
| 触发 | dd 5/10/15% 阶梯 | dd 1% 起每 1pp 一档 |
| 滞后 | recovery_hysteresis | hysteresis 留参，建议 0.5% |
| 阈值动作 | 字段已有但未接入 | 由 DynamicBumpAdjuster 接管 |

两者作用维度不同 → **同时启用不冲突**。`PortfolioThrottleSizer` 作为 thin adapter
允许把 `ThrottleLevel.max_total_positions_mult` 也接入 sizing 链。

---

## §6 编排器 RiskOrchestrator

[`cta/risk/orchestrator.py:RiskOrchestrator`](../../risk/orchestrator.py) 关键方法
`evaluate(ctx, original_lots) -> AdjustedDecision`：

```
debug = {"original_lots": ...}
# ── threshold ───
base = base_threshold or self._resolve_base_threshold(ctx)
eff_threshold = base
for adj in self._adjusters:                        # static → quantile → dynamic_bump
    eff_threshold = adj.resolve(ctx, eff_threshold)
score = extract_score(ctx, prefer_pctl=True)
if score < eff_threshold: BLOCK(stage=threshold)
# ── sizing ───
lots = original_lots
for sc in self._scalers:                           # bucket → linear_dd → (portfolio_throttle)
    new_lots, reason = sc.scale(ctx, lots)
    if new_lots <= 0: BLOCK(stage=sizing)
    lots = new_lots
return AdjustedDecision(passed=True, adjusted_lots=lots, effective_threshold=eff_threshold, debug=debug)
```

工厂 `RiskOrchestrator.from_config(cfg, quantile_manifest=..., bucket_tracker=...)`
按 `RiskSystemConfig` 自动装配组件链路，调用方无需手工拼。

异常处理：每个 adjuster / scaler 内部异常被捕获后 keep previous，不影响链路。

---

## §7 RiskSystemConfig

完整字段见 [`cta/risk/config.py:RiskSystemConfig`](../../risk/config.py)。关键默认：

| 字段 | 默认 | 说明 |
|------|------|------|
| `enable_quantile_threshold` | True | 有 manifest 时生效；无 manifest 自动降级到 static |
| `enable_bucket_scaling` | **False** | 需 30 日预热；sim soak 后再 default-on |
| `enable_linear_dd_scaler` | True | dd < 1% 时完全无影响 |
| `enable_dynamic_bump` | True | dd < 1% 时 bump=0 |
| `quantile_field` | `"p70"` | 与 OotEvaluationConfig 默认 70pp 等价 |
| `bucket_window_days` | 30 | |
| `bucket_min_trades` | 20 | 不足时桶不缩仓 |
| `linear_dd_trigger_pct` | 0.01 | 用户决策的 1% 起 |
| `linear_dd_step_mult` | 0.10 | 每档 -10% lots |
| `linear_dd_floor_mult` | 0.10 | 90% 缩仓后留 10% |
| `dynamic_bump_pp_per_step` | 5.0 | 每 1pp dd 抬 5pp 阈值 |
| `dynamic_bump_threshold_cap_pp` | 95.0 | 阈值不会超过 95pp |
| `pass_through_when_score_missing` | True | candidate 缺 score 不拦 |

post_init 严格校验所有参数边界。

---

## §8 接入路径

### §8.1 train 阶段（manifest 产出）

在 [`cta/model/orchestration/pipeline_run_predictions.py`](../../model/orchestration/pipeline_run_predictions.py)
写完 predictions.csv 后追加：

```python
from cta.risk.state.score_quantile_manifest import build_from_predictions
build_from_predictions(
    predictions_paths=[predictions_csv],
    out_path=cta_root / "model" / "manifests" / f"score_quantile_manifest_{ts}_{run_tag}.json",
    exclude_splits=("test", "oot"),  # 零数据泄露
    meta={"run_tag": run_tag, "git_sha": git_sha, "cfg_fingerprint": cfg_fingerprint},
)
```

输出附带 `score_quantile_manifest_latest.json` symlink 给 OOT/sim/live 默认读取。

### §8.2 OOT eval 阶段（消费）

在 [`cta/model/oot/pipeline_oot_evaluation_inputs.py`](../../model/oot/pipeline_oot_evaluation_inputs.py)
对 candidates DataFrame 批处理：

```python
from cta.risk import RiskOrchestrator, SignalContext
orc = RiskOrchestrator.from_config(cfg.risk_system, ...)
for idx, row in df.iterrows():
    ctx = SignalContext(candidate=row.to_dict(), portfolio={...}, bar_dt=row["signal_datetime"])
    decision = orc.evaluate(ctx, original_lots=int(row["lots"]))
    df.at[idx, "risk_effective_threshold"] = decision.effective_threshold
    df.at[idx, "risk_lots_mult"] = decision.adjusted_lots / max(int(row["lots"]), 1)
    df.at[idx, "risk_block_reason"] = decision.block_reason
```

新增 3 列写入 trade_details.csv，便于复盘归因。

### §8.3 sim/live 阶段

在 [`cta/sim/adapters/entry_gate_chain.py:EntryGateChain`](../../sim/adapters/entry_gate_chain.py)
新增 Stage 5：

```python
# ── Stage 5: RiskOrchestrator (threshold + sizing) ──────────────
if self._risk_orchestrator is not None:
    ctx = SignalContext(candidate=row, portfolio=portfolio_snapshot, bar_dt=current_time)
    decision = self._risk_orchestrator.evaluate(ctx, original_lots=lots)
    if not decision.passed:
        return GateDecision(passed=False, block_reason=decision.block_reason,
                             block_stage=f"risk_{decision.block_stage}")
    lots = decision.adjusted_lots
```

`sim_runner._attach_observers` 也增加 `risk_orchestrator` 注入。

### §8.4 cfg_fingerprint 同步

[`cta/model/eval_only_run.py:_dump_cfg_fingerprint`](../../model/eval_only_run.py) 扩展
覆盖 `risk_system` 子配置，呼应 [`review/20260528.md`](review/20260528.md) §3.4 P0-4 invariant 8。

---

## §9 状态持久化

| state | 路径 | 写入时机 | 读取时机 | fail-open |
|-------|------|----------|----------|-----------|
| score_quantile_manifest | `cta/model/manifests/score_quantile_manifest_<ts>_<run_tag>.json` | train 完 | OOT/sim 启动 | 文件缺失 → adjuster 不加入链 |
| bucket_pnl_state | `cta/run/state/bucket_pnl_state.json` | 每日 close 后 | sim 启动 | 文件损坏 → 空 tracker |
| equity_history | `cta/run/state/equity_history.csv`（由 EquityTracker 维护）| 每 bar | sim 启动 | 文件缺失 → 空 history |

不变量：所有 state 文件都不在 git 里管理（运行时产出），路径在 `cta/.gitignore` 中已排除。

---

## §10 不变量

1. **绝不动 cta/live/risk.py 6 个 `_BaseRule`**（pre-trade kill 路径稳定）。
2. **fail-open**：所有 state 文件缺失/损坏 → 退回 static config，永不锁单。
3. **OOT/sim/live 同一 `RiskOrchestrator` 实例**（cfg 决定行为，不分叉实现）。
4. **每子系统独立 enable / disable**，互不依赖。
5. **零数据泄露**：quantile manifest 只用 train+valid split，test/OOT 不参与统计。

---

## §11 测试策略

每个子模块独立 unit test + orchestrator 集成测试：

| 测试文件 | 覆盖 | 当前数量 |
|----------|------|----------|
| `test_quantile_threshold.py` | manifest lookup + fallback + JSON roundtrip + 排除 split | 11 |
| `test_dynamic_bump.py` | dd 阶梯 + cap + 后向兼容 + invalid cfg | 11 |
| `test_bucket_pnl_tracker.py` | classify + on_trade + window evict + save/load + fail-open | 15 |
| `test_bucket_scaling.py` | 各 mult 区间 + sample_insufficient + round-down 保护 + cfg 校验 | 11 |
| `test_linear_dd_scaler.py` | dd 阶梯 + floor + 后向兼容 + invalid cfg | 11 |
| `test_dd_signal_provider.py` | static + tracker 衔接 + weekly window | 8 |
| `test_orchestrator.py` | 三层组合 + BLOCK 各 stage + fail-open + debug dict | 11 |
| `test_score_distribution_drift.py` | drift 监控 + severity 分级 + critical/emergency 拦截 | 5 |
| `test_profit_give_back_guard.py` | 高水位回吐触发 + 次日 reset + sizer 归零 | 5 |
| `test_daily_var_budget.py` | warning 缩仓 + cluster/account 硬停 + tracker 日切 reset | 5 |
| `test_execution_quality_feedback.py` | 滑点/拒单/偏离触发 + 最保守 mult 组合 | 5 |

**当前状态**：`python3 -m pytest cta/risk/tests/ -q` → **257 passed**。

验收：
- 单测全过
- 跑 1 次 OOT 回放（用 `oot_20260528_221616` 数据）：trade_count 下降 5-15% + MDD 改善 0.3-1pp
- sim 集成不破坏现有 entry_gate_chain Stage 1-4 测试

---

## §12 阶段实施

| 周 | 范围 | 状态（2026-05-29）|
|---|------|------|
| W1 | base / config / orchestrator + tests | ✅ 已落 |
| W2 | quantile_threshold + manifest 生成 + train hook | ✅ 已接入（`pipeline_run` 训练侧写 manifest） |
| W3 | bucket PnL tracker + scaling + state | ✅ 组件全落 |
| W4 | dynamic_bump + linear_dd_scaler + entry_gate_chain Stage 5 | ✅ 已接入（sim `EntryGateChain` Stage 5） |
| W5 | OOT pipeline 接入 + cfg_fingerprint 扩展 + 端到端 | ✅ 已接入（OOT 风控列 + cfg_fingerprint `risk_system`） |

**本轮实际进度（2026-05-29 更新）**：W1~W5 已全部打通。当前默认仍保持保守（`risk_system=None`
时行为与旧版一致）；显式配置 `risk_system` 后启用 orchestrator 逻辑。

### W2/W4/W5 运行命令（可直接复现）

1) 训练并自动生成 quantile manifest（W2）：

```bash
python3 -m cta.model.model_pipeline \
  --symbol RB0 --exchange SHFE \
  --interval day \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2020-12-31 --valid-end 2023-12-31 \
  --enable-risk-system \
  --risk-quantile-field p70
```

2) 评估阶段启用 risk orchestrator（W5）：

```bash
python3 -m cta.model.eval \
  --from-root cta/backtest \
  --pattern "*_model_pipeline" \
  --output-root cta/backtest/$(date +%Y%m%d)_eval_risk \
  --run-tag cluster_both_risk \
  --enable-risk-system \
  --risk-manifest-path cta/model/manifests/score_quantile_manifest_latest.json
```

3) 核心回归测试（W4/W5）：

```bash
python3 -m pytest -q cta/risk/tests/
python3 -m pytest -q cta/model/tests/test_risk_wiring.py cta/sim/tests/test_adapters.py
```

---

## §13 与现有风控的关系

| 现有组件 | 关系 |
|----------|------|
| `cta/live/risk.py:_BaseRule` × 6 | **保留原样**；orchestrator 不重写硬拒规则 |
| `cta/portfolio_logic/risk_throttle.py:RiskThrottle` | **共存**；新 LinearDdScaler 在 sizing 维度补充 |
| `cta/portfolio_logic/config.py:CapsConfig` / `ThrottleLevel` | **不动**；`ThrottleLevel.score_pctl_threshold` 由 DynamicBumpAdjuster 在概念上"接管" |
| `cta/feature/symbol_disable_manifest.csv` | **保留**；持久剔除是最严档；bucket_scaling 是中间态 |
| `cta/config/cluster_bond_filter_manifest.py` | 仍是 base layer (`OotEvaluationConfig` 默认值)；quantile manifest 在其之上 |

---

## §14 与 v1/v2 roadmap 的关系

本子系统作为 [`sim_live_integration_roadmap2.md`](sim_live_integration_roadmap2.md) v2 的
**新分支 P0Δ-5**（与现有 P0Δ-1/2/3/4 并列）。v2 §7 不变量从 13 条扩到 14 条：

> **不变量 14（新增）**：sim/live 的 `cfg_fingerprint.json` 必须包含 `risk_system` 子配置
> 的完整字段；CI grep 不到视为风控漂移。

---

## §15 中国 CTA 特化风控策略总览

本节起列出 12 条 **针对中国期货市场结构** 的扩展风控策略，按 4 大类组织（每条都给出
触发条件 / 参数 / 实现路径 / 关键边界）。这些策略不在 W1-W5 范围，但架构层已经为
它们留好接口（`ThresholdAdjuster` / `PositionScaler` / 新增 `GuardRule` / 新增
`PortfolioReshaper`）。

| § | 策略 | 类型 | 优先级 | 接口 |
|---|------|------|--------|------|
| §16.1 | LimitMoveGuard（涨跌停硬约束） | Guard | **P0** | RiskGuard 子类 |
| §16.2 | LiquidityFloorGuard（流动性下限） | Guard | **P0** | RiskGuard 子类 |
| §16.3 | RolloverFreezeRule（换月禁交） | Guard | **P0** | RiskGuard 子类 |
| §16.4 | HolidayPositionReducer（节假日降仓） | Sizer | **P0** | PositionScaler |
| §16.5 | NightSessionCarryRule（夜盘 / 周末隔仓） | Sizer | P1 | PositionScaler |
| §16.6 | VolatilityRegimeScaler（波动率自适应） | Sizer | P1 | PositionScaler |
| §17.1 | CrossClusterCorrelationGuard（cluster 联动） | Guard | **P0** | RiskGuard 子类 |
| §17.2 | ConsecutiveLossGuard（连续亏暂停） | Guard | P1 | RiskGuard 子类 |
| §17.3 | SignalConcentrationGuard（同类信号并发上限） | Guard | P1 | RiskGuard 子类 |
| §18.1 | ScoreDistributionDriftMonitor（模型分漂移） | State+Guard | P1 | 监控 + Guard |
| §18.2 | PredictionStaleGuard（数据新鲜度） | Guard | **P0** | RiskGuard 子类 |
| §19.1 | ProfitGiveBackGuard（盈利回吐保护） | Guard+Sizer | P1 | 双接口 |
| §19.2 | DailyVaRBudget（VaR 预算） | Sizer | P2 | PositionScaler |
| §19.3 | ExecutionQualityFeedback（执行质量反馈） | State+Sizer | P2 | State + PositionScaler |

**P0**：sim soak 启动前必须落地（影响交易安全）；**P1**：sim soak 期间落地（影响业绩）；
**P2**：实盘灰度后再做（影响精细化）。

---

## §16 市场结构风控（涨跌停 / 流动性 / 换月 / 节假日 / 夜盘）

### §16.1 LimitMoveGuard — 涨跌停硬约束

**背景**：中国商品 5-10% / 股指 10% / 国债 1.5-2% 涨跌停板触及后**单边盘单不成交**，
策略若持反向仓位会陷入"想平平不掉、想反向开开不进"的死局；若持同向仓位会被进一步
锁死利润 → 必须在板前预判并主动阻断。

**触发条件**（任一）：
- 当前价距 `prev_close × (1 + limit_pct)` < tolerance（默认 0.3%）→ "**近板**"
- 已经触板（涨停 vs 跌停）→ "**触板**"

**行为**：
| 持仓状态 | 信号方向 | 行为 |
|---------|---------|------|
| 无持仓 | open long, 已涨停 | 拒（无法成交） |
| 无持仓 | open short, 已跌停 | 拒 |
| 无持仓 | 任何方向，近板 | 拒（避免触板后被锁）|
| 有持仓 | close 同向，已触板 | 切到对手价 limit 单 + 限时撤单（不在 Guard 内实现，由订单层做）|
| 有持仓 | open 反向，已触板 | 拒（趋势可能延续）|

**关键参数**：
```python
@dataclass(frozen=True)
class LimitMoveGuardConfig:
    near_limit_tolerance_pct: float = 0.003     # 近板阈值（板前 0.3%）
    block_open_when_near: bool = True
    block_open_when_at: bool = True
    block_close_at_unfavorable_limit: bool = False  # 平仓基本不拦，由订单层处理
    limit_pct_by_cluster: dict[str, float] = ...    # 复用 infer_symbol_limit_pct
```

**实现路径**：
- 新建 `cta/risk/guards/limit_move_guard.py`，继承 `cta.live.risk._BaseRule`
- 复用 [`cta/config/symbol_cluster_config.py:infer_symbol_limit_pct`](../../config/symbol_cluster_config.py)
- 复用 [`cta/sim/trading_calendar.py:is_at_price_limit`](../../sim/trading_calendar.py) 已有判定
- ctx 需要 prev_close + current_price：从 `EquityTracker` 加 bar tracker 派生

**边界**：
- 跨日 prev_close 跳变（结算价 vs 收盘价）→ 按交易所结算价（更稳）
- 国债 limit_pct 与商品不同（CFFEX TF/T 2%）→ cluster 表 override
- 夜盘开盘 prev_close 引用日盘收盘价，限价计算无歧义

### §16.2 LiquidityFloorGuard — 流动性下限

**背景**：远月 / 边缘品种（如 PG、SS）单日成交量可能只有主力品种的 1-5%，模型回测时未
扣折算，sim 跑出来似乎可下单，**实盘几手就把盘口吃穿**。冷品种 + 大行情时 spread 会
飙到 5-10 个 tick，瞬间报废 alpha。

**触发条件**：
- 当前 bar `volume` < `N` 日中位数 × `volume_floor_ratio`（默认 0.3）→ "稀薄"
- `bid_ask_spread_ticks` > `max_spread_ticks`（默认 3）→ "宽 spread"
- `turnover_ratio = volume × close / open_interest` < 5% → "OI 占比过低"

任一触发 → 拒开仓；平仓允许（流动性差也要走）。

**关键参数**：
```python
@dataclass(frozen=True)
class LiquidityFloorGuardConfig:
    lookback_days: int = 20
    volume_floor_ratio: float = 0.3
    max_spread_ticks: int = 3
    min_turnover_ratio: float = 0.05
    allow_close_orders: bool = True   # 平仓不拦
    bypass_on_main_contract: bool = True  # 主力合约不拦（默认 .888 后缀视为主力）
```

**实现**：
- 新建 `cta/risk/guards/liquidity_floor_guard.py`
- 流动性指标按 (symbol, interval) 滚动统计；可复用
  [`cta/risk/state/bucket_pnl_tracker.py`](../../risk/state/bucket_pnl_tracker.py) 同款 deque 模式 →
  新 state 类 `LiquiditySnapshotTracker`
- vnpy `TickData.bid_price_1 / ask_price_1` 计算 spread；sim 用 bar close 估算

**边界**：
- 新品种上市初期 lookback 不足 → 用全样本中位数 + 警告
- 节假日前后成交量自然萎缩 → 与 §16.4 联动（HolidayPositionReducer 优先）

### §16.3 RolloverFreezeRule — 换月禁交

**背景**：主力合约换月时（典型节奏：到期月前 1 个月），老合约：
- 持仓被强行减仓 / 手续费 ×3 / 限制开仓数
- 价差跳变（次主力 vs 主力的 basis 突变）
- 流动性瞬间转移到次主力

如果策略持老合约或在换月窗口里开仓，**实盘可能直接被强平**。

**触发条件**：
- `days_to_expiry < freeze_window_days`（默认 7）→ 老合约任何方向不开新仓 + 限时清仓
- `days_since_main_switch < post_switch_freeze_days`（默认 3）→ 新主力也不开（让 basis 稳）

**行为**：
- 老合约：拒 open，强制 close（在订单层做对应优化）
- 新主力：在 post_switch_freeze 内允许 close、拒 open

**关键参数**：
```python
@dataclass(frozen=True)
class RolloverFreezeConfig:
    freeze_window_days: int = 7
    post_switch_freeze_days: int = 3
    force_close_days: int = 3      # 到期前 N 天强制平仓
    contract_calendar_path: str = "cta/config/contract_rollover_calendar.csv"
```

**实现**：
- 新建 `cta/risk/guards/rollover_freeze_guard.py`
- 复用 [`cta/portfolio_logic/contract_resolver.py`](../../portfolio_logic/contract_resolver.py) +
  `cta/config/contract_rollover_calendar.csv`（仍待创建）
- 与 `cta/sim/adapters/rotation_stepper.py:RolloverCheck` 协议对接

**边界**：
- 部分品种主力切换不稳定（持仓量震荡换主力）→ 用 3 日加权 OI 判断主力位
- 跨年合约（如玉米）窗口拉到 14 天

### §16.4 HolidayPositionReducer — 节假日降仓

**背景**：长假（春节 7 天 / 国庆 7 天 / 五一 5 天）期间海外消息累积，节后第一天经常
跳空 3-5%（极端情况如 2020 年新冠跳空 8%）。日 / 60min 策略在节前满仓持有 ≈ 隔夜赌单。

**触发条件**：
- 距下一个法定节假日 ≤ `pre_holiday_taper_days` → 按线性降仓
- 距上一个法定节假日结束 ≤ `post_holiday_warmup_days` → 仍降仓（节后波动大）

**行为**（线性缩仓乘数）：
```
距节假日（交易日）：5  4  3  2  1
mult：              1.0  0.8  0.6  0.4  0.2
节中持仓上限：       0.2 × normal
```

**关键参数**：
```python
@dataclass(frozen=True)
class HolidayPositionReducerConfig:
    holidays_csv: str = "cta/config/cn_futures_holidays_2024_2026.csv"
    pre_holiday_taper_days: int = 5
    post_holiday_warmup_days: int = 2
    floor_mult_in_holiday: float = 0.2
    apply_only_to_overnight_carry: bool = False  # True = 只对持仓过节的减
```

**实现**：
- 新建 `cta/risk/sizing/holiday_position_reducer.py`（`PositionScaler` 子类）
- 复用 [`cta/sim/trading_calendar.py:load_holidays_from_csv`](../../sim/trading_calendar.py)

**边界**：
- 与 §19.1 ProfitGiveBackGuard 联动：节前对盈利仓位优先平
- 国债节前反而要满仓（避险资金）→ cluster_bond 加 `inverse_for_clusters=("bond",)`

### §16.5 NightSessionCarryRule — 夜盘/周末隔仓

**背景**：
- 夜盘 21:00-02:30 仅部分品种（金属、能化、农产品有夜盘；股指 / 国债无），日盘策略
  若把夜盘成交计入会重复计算 PnL
- 周末持仓 = 隔 60 小时 + 海外两天行情；周一开盘 50% 概率跳空

**触发条件**：
- 周五日盘最后 30 分钟 → 按 cluster 减仓比例（带夜盘的 50%，无夜盘的 30%）
- 长假前规则同 §16.4

**行为**：
- 周五 14:30 后：所有未持仓的开仓信号 mult=0.5
- 已持仓但未到平仓信号：按 cluster 默认平仓比例（带夜盘品种留 0.5，无夜盘留 0.3）

**关键参数**：
```python
@dataclass(frozen=True)
class NightSessionCarryConfig:
    enable_friday_taper: bool = True
    friday_taper_after_hhmm: str = "14:30"
    weekend_carry_mult_by_cluster: dict[str, float] = (
        {"metal": 0.5, "chemical": 0.5, "agri": 0.5, "black": 0.5,
         "index": 0.3, "bond": 0.3, "precious": 0.6, "other": 0.4}
    )
```

**实现**：
- 新建 `cta/risk/sizing/night_session_carry.py`
- 复用 [`cta/sim/trading_calendar.py:is_trading_session`](../../sim/trading_calendar.py)

**边界**：跟客户实际持仓偏好相关，留 cfg override。

### §16.6 VolatilityRegimeScaler — 波动率自适应

**背景**：CTA 中"高波动期减仓"是经典经验。中国期货波动率有明显 regime：低 vol 期
（沪深 300 ATR < 历史 P30）信号准确度高 → 可加仓；高 vol 期（黑色 ATR > P90）止损
被频繁打穿 → 应减仓。

**触发条件**：
- `realized_vol_20d` 落入历史分位区间，按区间映射 mult

**行为**：
```
realized_vol_pctl   →  mult
< 30                →  1.2 (低 vol 期加仓 20%)
30 - 70             →  1.0
70 - 85             →  0.8
85 - 95             →  0.6
> 95                →  0.4 (极端高 vol 期砍 60%)
```

**关键参数**：
```python
@dataclass(frozen=True)
class VolatilityRegimeScalerConfig:
    vol_pctl_edges: tuple[float, ...] = (30, 70, 85, 95)
    mults: tuple[float, ...] = (1.2, 1.0, 0.8, 0.6, 0.4)
    cap_mult: float = 1.3       # 加仓最高上限
    floor_mult: float = 0.3
```

**实现**：
- 新建 `cta/risk/sizing/volatility_regime_scaler.py`
- 复用 `cta/feature/` 输出的 `realized_vol_20d_pctl` 列（若无则按 candidate.atr_pct 估）

**边界**：
- 加仓不能超过 cap（避免低 vol 期叠加多个 mult > 1）
- 与 LinearDdScaler 串联时：低 vol mult=1.2 × dd mult=0.9 = 1.08（仍属加仓）；
  若用户希望"先做减不做加"，把 cap_mult 设为 1.0

---

## §17 集中度 / 相关性 / 连损风控

### §17.1 CrossClusterCorrelationGuard — cluster 联动

**背景**：黑色系（RB/HC/I/J/JM）历史相关性 0.85+，多笔同方向开仓相当于一笔大单。
2020 年 11 月黑色集体冲高回落，单 cluster 因相关性聚集亏损 -8%。仅看单 symbol cap
保不住整组。

**触发条件**：
- 当前 cluster 已持仓 N 个 symbol，再开同向 → 拒
- 高相关 cluster 组合（黑色 + 有色、农产品 + 化工）已开同向总数 ≥ M → 拒

**关键参数**：
```python
@dataclass(frozen=True)
class CrossClusterCorrelationConfig:
    max_same_cluster_same_direction: int = 3   # 同 cluster 同向最多 3 笔
    correlated_cluster_groups: tuple[tuple[str, ...], ...] = (
        ("black", "metal"),       # 工业金属 + 黑色相关
        ("agri", "chemical"),     # 农产品 + 化工
    )
    max_per_correlated_group_same_direction: int = 5
```

**实现**：
- 新建 `cta/risk/guards/cross_cluster_correlation_guard.py`
- 复用 `cta/portfolio_logic/portfolio_state.py:PortfolioState.positions` 统计当前持仓

**边界**：
- correlated_cluster_groups 应可按日历重算（如 7 月相关性矩阵 + EWMA 校正）—— 但
  初版用静态分组够用
- 高频策略（5min/min）开仓节奏快，cap 会频繁触发 → enable_by_interval

### §17.2 ConsecutiveLossGuard — 连续亏暂停

**背景**：同 (symbol, signal_type) 连续亏 N 笔通常意味着 regime 切换或信号失效，
继续交易是把"已经失效的 alpha"放大。经典 CTA 经验：连续 3-5 笔亏后冷却 24 小时。

**触发条件**：
- 同 (cluster, symbol, signal_type) 在 lookback_days 内连续亏 ≥ N 笔 → 冷却 cooldown_hours

**行为**：
- 冷却期：(cluster, symbol, signal_type) 维度拒任何开仓；允许平仓
- 期间若有新成功信号（非该 signal_type）→ 不受影响

**关键参数**：
```python
@dataclass(frozen=True)
class ConsecutiveLossGuardConfig:
    n_consecutive_losses: int = 3
    cooldown_hours: int = 24
    lookback_days: int = 7    # 跨这么久就重置
    apply_to_groups: tuple[str, ...] = ("symbol", "signal_type")   # 可改为 cluster 级
```

**实现**：
- 新建 `cta/risk/guards/consecutive_loss_guard.py`
- 新 state：`cta/risk/state/consecutive_loss_tracker.py` —— 按 (cluster, symbol, signal_type)
  存最近 N 笔结果 deque + cooldown 截止时间 + JSON 持久化

### §17.3 SignalConcentrationGuard — 同类信号并发上限

**背景**：cross_sectional rotation 一次产 30+ 同质 momentum 信号，若全部下单 = 把组合
打成单一 factor exposure；breakout 突破信号也类似。需要"同 signal_type 在同一 bar
内 max 笔数"约束。

**触发条件**：
- 当前 bar 已下单的同 signal_type 数 ≥ `max_per_signal_type_per_bar` → 拒

**关键参数**：
```python
@dataclass(frozen=True)
class SignalConcentrationGuardConfig:
    max_per_signal_type_per_bar: int = 5
    max_per_cluster_per_bar: int = 3
    reset_at_session_open: bool = True   # 日内累计
```

**实现**：复用 `PortfolioState` 当前持仓状态 + 当 bar 累计计数器。

---

## §18 模型治理与监控

### §18.1 ScoreDistributionDriftMonitor — 模型分漂移

**背景**：模型训练时 `trade_filter_prob` 分布 mean=0.55 / std=0.15；上线两个月后突然
变成 mean=0.45 / std=0.08 → 模型已严重漂移（市场 regime 切换 / 数据上游变质 / 模型
过拟合到训练样本）。此时模型分阈值原样套用会**误开很多本来不该过的单**或**误拒
本应过的单**。

**触发条件**：
- 当日累计 ≥ N 笔候选，分布对比训练 manifest（KL 散度 / KS 检验 / 1-Wasserstein 距离）：
  - KL > 0.5 → warning（继续运行 + 告警）
  - KL > 1.0 → critical（停所有新开仓 1 小时；只允许平仓）
  - KL > 2.0 → emergency（kill_switch 激活）

**关键参数**：
```python
@dataclass(frozen=True)
class ScoreDistributionDriftConfig:
    train_distribution_path: str = "cta/model/manifests/score_distribution_train.json"
    drift_metric: str = "kl"           # "kl" / "ks" / "wasserstein"
    warning_threshold: float = 0.5
    critical_threshold: float = 1.0
    emergency_threshold: float = 2.0
    min_samples_for_assessment: int = 100
    rolling_window_hours: int = 4
```

**实现**：
- 新 state：`cta/risk/state/score_distribution_tracker.py` —— 滚动 4 小时 prob 直方图
- 新 monitor：`cta/risk/monitors/score_distribution_drift.py`（与 `cta/live/monitoring.py:MetricsRegistry` 配合）
- Guard：触发 critical 时调 KillSwitch

**边界**：与 ScoreQuantileManifest 共用 train+valid 分布；可直接读 manifest 反推。

### §18.2 PredictionStaleGuard — 数据新鲜度

**背景**：predictions.csv 由 batch pipeline 每日生成。如果上游任务挂了 / 服务器宕机，
sim 仍会读取昨天甚至更早的 predictions —— "拿过期的模型分做今天的决策"是非常隐蔽的
bug。

**触发条件**：
- `predictions.csv` 最新 row 的 `datetime` 距 now > `max_stale_hours`（默认 4）→ 拒所有
  开仓
- model joblib 文件 mtime > `max_model_age_days`（默认 14）→ warning

**关键参数**：
```python
@dataclass(frozen=True)
class PredictionStaleGuardConfig:
    max_stale_hours: int = 4
    max_model_age_days: int = 14
    predictions_path: str = ""    # 由调用方注入
    block_open_on_stale: bool = True
    block_close_on_stale: bool = False   # 平仓允许
```

**实现**：新建 `cta/risk/guards/prediction_stale_guard.py`，每订单 cache 上次检查
结果（mtime 没变就用 cache，避免 stat 抖动 —— 与 [`review/20260528.md`](review/20260528.md) §4.4
P1-4 一致）。

---

## §19 收益保护 + 风险预算 + 执行反馈

### §19.1 ProfitGiveBackGuard — 盈利回吐保护

**背景**：日内盈利 5% 后回吐到 2% 才平 → 已经送回 60% 利润。心理上盈利"已经到手"，
应当锁定。经典做法：日内盈利达 X% 触发"盈利保护模式"，回吐 Y% 强制平仓。

**触发条件**（按账户级日内 PnL%）：
- 日内盈利 >= `activation_pct`（默认 +3%）→ 启动保护
- 启动后盈利从最高点回吐 >= `give_back_pct`（默认 50% × 触发时的盈利幅度）→ 全部平仓

**行为**：
- 不仅平仓，还在剩余日内 **拒所有新开仓**（避免又把本金亏掉）
- 跨日自动 reset

**关键参数**：
```python
@dataclass(frozen=True)
class ProfitGiveBackConfig:
    activation_pnl_pct: float = 0.03
    give_back_ratio: float = 0.50     # 回吐高点的 50% 触发
    block_new_opens_after_trigger: bool = True
    reset_at_session_open: bool = True
```

**实现**：
- 新 state：`cta/risk/state/intraday_profit_tracker.py` —— 当日 high water + current PnL%
- Guard + Sizer 双接口（Guard 拒开仓，Sizer 把所有持仓 lots scale 到 0 → 触发平仓信号
  由订单层处理）

**边界**：与 weekly/monthly RiskThrottle 不同（那个看回撤，本规则看"盈利回吐"）→ 互补。

### §19.2 DailyVaRBudget — VaR 预算

**背景**：用 Value-at-Risk 给每个 cluster 分配日内最大损失预算。例：cluster_metal
分配 80bp、cluster_bond 分配 20bp。当 cluster 当日亏损接近 budget 时停开新仓；
触达后只允许平仓。这是机构 CTA 的标准做法（基金 risk team 的硬约束）。

**触发条件**：
- 单 cluster 当日累计 PnL 达 -`budget_bp` × 95% → warning 减仓
- 达 100% → cluster 维度禁开仓
- 全账户累计 PnL 达 -`account_budget_bp` × 100% → 全停

**关键参数**：
```python
@dataclass(frozen=True)
class DailyVaRBudgetConfig:
    budget_bp_by_cluster: dict[str, float] = (
        {"metal": 80, "chemical": 80, "agri": 60, "black": 100,
         "index": 50, "bond": 20, "precious": 50, "other": 60}
    )
    account_budget_bp: float = 250   # 累计 2.5% 全停（与 weekly_max_drawdown_pct 2.5% 一致）
    warning_ratio: float = 0.95
    reset_at_session_open: bool = True
```

**实现**：
- 新 state：`cta/risk/state/daily_var_tracker.py`
- Sizer：超 warning 后 mult=0.5；超 100% 拒所有开仓

**边界**：跨日 reset 时需要考虑夜盘归属（21:00 起算新交易日 vs 自然日）—— 用
[`cta/sim/timezone_helpers.py`](../../sim/timezone_helpers.py) 已统一的 Asia/Shanghai。

### §19.3 ExecutionQualityFeedback — 执行质量反馈

**背景**：实盘滑点 / reject_rate / 撤单比 持续偏离回测假设时，应自动缩仓。例：bond
预期滑点 0.5bp，实盘 3bp → cost_pct 假设全错；冷品种 reject_rate 10% → 信号实际命中
率 90%。

**触发条件**（按 symbol 滚动统计）：
- 7 日实际滑点 / 预期 > 3x → 该 symbol mult=0.5
- 7 日 reject_rate > 5% → mult=0.7
- 7 日实际成交均价 vs 信号价偏离 > 0.5% → mult=0.3

**关键参数**：
```python
@dataclass(frozen=True)
class ExecutionQualityConfig:
    slippage_ratio_threshold: float = 3.0
    reject_rate_threshold: float = 0.05
    avg_price_deviation_threshold: float = 0.005
    rolling_window_days: int = 7
    mults: tuple[float, ...] = (0.5, 0.7, 0.3)   # 对应 3 个触发条件
    min_trades_for_assessment: int = 10
```

**实现**：
- 新 state：`cta/risk/state/execution_quality_tracker.py` —— 接 vnpy `TradeData` /
  `OrderData` 回调更新统计
- Sizer：`cta/risk/sizing/execution_quality_scaler.py`
- Hook：`cta/sim/sim_runner._attach_observers` 增加 `execution_quality_tracker`
  注入

**边界**：
- 跟 §16.2 LiquidityFloorGuard 不重复：那个看"事前流动性指标"，这个看"事后实际成交质量"
- 与 [`review/20260528.md`](review/20260528.md) §4.6 P1-6 监控 wire 配合：把执行质量指标
  导出到 Prometheus

---

## §20 扩展阶段实施（W6-W10）

接续 §12 W1-W5：

| 周 | 范围 | 依赖 | 优先级 | 状态（2026-05-29）|
|---|------|------|--------|------|
| W6 | LimitMoveGuard + LiquidityFloorGuard + RolloverFreezeRule + PredictionStaleGuard | 现有 trading_calendar + contract_resolver | P0 | ✅ **已落** |
| W7 | HolidayPositionReducer + CrossClusterCorrelationGuard + ConsecutiveLossGuard | W3 BucketPnlTracker 模板复用 | P0/P1 | ✅ **已落** |
| W8 | VolatilityRegimeScaler + NightSessionCarryRule + SignalConcentrationGuard | 现有 realized_vol 列 + trading_calendar | P1 | ✅ **已落** |
| W9 | ScoreDistributionDriftMonitor + ProfitGiveBackGuard | 现有 ScoreQuantileManifest + DailyPnlTracker | P1 | ✅ **已落** |
| W10 | DailyVaRBudget + ExecutionQualityFeedback | sim soak 数据 + 监控 wire（P1-6） | P2 | ✅ **已落** |

**W6 落地清单**（2026-05-29 完成）：
- `cta/risk/guards/{limit_move_guard, liquidity_floor_guard, rollover_freeze_guard, prediction_stale_guard}.py`
- `cta/risk/state/{rollover_calendar, liquidity_snapshot_tracker}.py`
- `cta/risk/guards/config.py`（4 个 frozen dataclass + post_init 校验）
- 6 个 test 文件 / **79 个 test cases，全过**
- 复用资产：[`infer_symbol_limit_pct`](../../config/symbol_cluster_config.py) /
  [`is_at_price_limit`](../../sim/trading_calendar.py) / `cta.live.risk._BaseRule` /
  `RiskContext`，零重复实现

**W7+W8 落地清单**（2026-05-29 完成）：
- `cta/risk/guards/{cross_cluster_correlation_guard, consecutive_loss_guard, signal_concentration_guard}.py`
  （W7 §17.1 + W7 §17.2 + W8 §17.3）
- `cta/risk/sizing/{holiday_position_reducer, volatility_regime_scaler, night_session_carry}.py`
  （W7 §16.4 + W8 §16.6 + W8 §16.5）
- `cta/risk/state/consecutive_loss_tracker.py`（含 LossKey + cooldown JSON 持久化）
- `cta/risk/sizing/config.py`（3 个新 Sizer cfg）
- `cta/risk/guards/config.py` 追加 3 个新 Guard cfg
- 7 个 test 文件 / **78 个 test cases，全过**
- 复用资产：[`infer_symbol_cluster`](../../config/symbol_cluster_config.py) /
  [`load_holidays_from_csv`](../../sim/trading_calendar.py) /
  `RiskContext.open_positions_by_cluster` + 新字段 `open_positions_by_cluster_direction`

**W9+W10 落地清单**（2026-05-29 完成）：
- `cta/risk/monitors/score_distribution_drift.py` + `cta/risk/state/score_distribution_tracker.py`
  （W9 §18.1）
- `cta/risk/guards/{score_distribution_drift_guard, profit_give_back_guard}.py`
  + `cta/risk/state/intraday_profit_tracker.py`
  + `cta/risk/sizing/profit_give_back.py`
  （W9 §19.1）
- `cta/risk/sizing/{daily_var_budget, execution_quality_scaler}.py`
  + `cta/risk/state/{daily_var_tracker, execution_quality_tracker}.py`
  （W10 §19.2 + §19.3）
- `cta/risk/{guards,sizing,state}/config.py` 追加 4 个配置 dataclass：
  `ScoreDistributionDriftConfig` / `ProfitGiveBackConfig` /
  `DailyVaRBudgetConfig` / `ExecutionQualityConfig`
- 新增 4 个测试文件 / **20 个 test cases，全过**：
  `test_score_distribution_drift.py` /
  `test_profit_give_back_guard.py` /
  `test_daily_var_budget.py` /
  `test_execution_quality_feedback.py`

**优先级**：P0 必须在 sim soak 启动门槛前；P1 在 sim soak 跑满 30 日；P2 实盘灰度后。

每周新增 2-4 个 .py + 单测 + 文档章节追加，每个组件独立 PR 便于 review。

---

## §21 不变量补充

承接 §10 5 条 + plan §11 新增 14（cfg_fingerprint 含 risk_system），本节再追加 4 条：

6. **市场结构 Guard 优先**：LimitMoveGuard / LiquidityFloorGuard / RolloverFreezeRule 在
   `RiskGuard.rules` 链路里排在最前；任何 alpha-level 风控（quantile/bucket/dd）只
   能在它们之后才被评估 —— 即"硬约束优先于优化"。
7. **平仓默认不拦**：所有 Guard 默认 `block_close_orders=False`（让持仓有出口）；
   特殊场景才显式开启（如盈利保护强制平仓）。
8. **状态 reset 边界统一**：所有"日内累计"状态（daily_var / profit_giveback / signal_concentration）
   按 Asia/Shanghai 日切线（21:00 夜盘起算下一交易日）统一 reset；不允许用 wall clock
   本地时间。
9. **A/B 可观测**：每个新 Guard / Scaler 必须在 `AdjustedDecision.debug` 写入触发理由
   与触发前后的 lots/threshold；OOT 回放后能精确归因到"哪条规则砍了多少 trade_count
   / 多少 PnL"。

---

## §22 通用扩展占位（保留原 §15）

新组件通过实现以下接口即可即插即用：
- `cta.risk.base.ThresholdAdjuster` — 阈值调整
- `cta.risk.base.PositionScaler` — 仓位缩放
- `cta.live.risk._BaseRule` — 硬拒规则
- 新接口 `cta.risk.base.PortfolioReshaper`（待加）— 跨 symbol 的组合层重构（对冲、再平衡）

**v3 路线**：
1. **ML-based dynamic threshold**：用 RL 训出动态阈值策略，替换静态 manifest
2. **cross-asset hedge**：cluster 持仓过大时自动开反相关 cluster 对冲（如多金属对空黑色）
3. **disaster recovery 自动化**：满足回退条件时自动触发
   [`cta/live/soak_gate.py:RollbackChecklist`](../../live/soak_gate.py)
4. **regime-conditional cfg switching**：高 vol 期切换到 conservative cfg，低 vol 期
   切换到 aggressive cfg（cfg hot-swap by regime detector）
5. **基于成交量分布的预测时机**：避开"开盘前 5 分钟 + 收盘前 15 分钟"的盘口异常时段
   （TimeOfDayThrottle 的高阶版）
