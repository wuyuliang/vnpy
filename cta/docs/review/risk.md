# Code Review — `cta/risk/` 风控子系统

> Review 日期：2026-05-29 · 范围：`cta/risk/**`（22 个业务 .py + 23 个 test）
> 配套设计文档 [`cta/docs/risk.md`](../risk.md) · plan [`cta-enumerated-pancake.md`](../../../../.claude/plans/cta-enumerated-pancake.md)

---

## 0. 结论速览

| 维度 | 评价 |
|------|------|
| 架构 | ★★★★★ 插件化三层（ThresholdAdjuster / PositionScaler / Guard），扩展性强 |
| 代码质量 | ★★★★☆ frozen dataclass + post_init 严校验 + 全程 fail-open，一致性高 |
| 测试 | ★★★★☆ 257 passed，覆盖 happy / fail-open / 边界 / cfg 校验 |
| 文件规模 | ★★★★★ 全部 < 500 行（最大 324 `score_quantile_manifest.py`），4871 行非测试 |
| 生产接入 | ★★★☆☆ sim + OOT + train manifest 已接；**live 未接**；**W7-W10 组件未进 orchestrator 自动装配** |
| **关键风险** | ⚠️ 子系统①（quantile）默认配置下退化为 no-op，且**会悄悄覆盖 Action 2 的 bond 严格阈值** |

整体是一套设计良好、测试扎实的风控框架。但存在 **1 个高危语义 bug（H1）** 与 **2 个"实现了但生产路径不生效"的缺口（H2/M1）**，需在 default-on 前修复，否则文档宣称的能力与实际行为不符。

---

## 1. 架构盘点（现状）

### 1.1 三层 + 编排器

```
SignalContext(candidate, portfolio, bar_dt)
   │
   ├─① threshold 层（cta/risk/threshold/）  Protocol: resolve(ctx, base)->float
   │     StaticThresholdAdjuster → QuantileThresholdAdjuster → DynamicBumpAdjuster
   │     score < effective_threshold → BLOCK(stage=threshold)
   │
   ├─② sizing 层（cta/risk/sizing/）         Protocol: scale(ctx, lots)->(lots,reason)
   │     BucketScalingSizer → LinearDdScaler → (+W7-W10 需 extra 注入)
   │     adjusted_lots <= 0 → BLOCK(stage=sizing)
   │
   └─③ guards（cta/risk/guards/，继承 cta.live.risk._BaseRule）
         由 RiskGuard 链在 sim/live 订单出口评估，不在 orchestrator 内
```

[`RiskOrchestrator`](../../risk/orchestrator.py)`.from_config()` 一次按 cfg 装配，`evaluate()` 串行跑两层。

### 1.2 组件清单（22 个业务模块）

| 层 | 组件 | 接入 orchestrator.from_config? |
|----|------|:--:|
| threshold | Static / Quantile / DynamicBump | ✅ 全部 |
| sizing | Bucket / LinearDd | ✅ |
| sizing | PortfolioThrottle / Holiday / VolatilityRegime / NightSessionCarry / DailyVaRBudget / ExecutionQuality / ProfitGiveBack | ❌ **未自动装配** |
| guards | LimitMove / LiquidityFloor / RolloverFreeze / PredictionStale / CrossCluster / ConsecutiveLoss / SignalConcentration / ProfitGiveBack / ScoreDistributionDrift | （走 RiskGuard，不经 orchestrator）|
| state | ScoreQuantileManifest / BucketPnlTracker / DdSignalProvider / ConsecutiveLoss / Liquidity / Rollover / IntradayProfit / DailyVaR / ExecutionQuality / ScoreDistribution | n/a |
| monitors | ScoreDistributionDrift | n/a |

### 1.3 生产接入状态

| 路径 | 文件 | 状态 |
|------|------|------|
| sim 入口 Stage 5 | [`cta/sim/adapters/entry_gate_chain.py:120`](../../sim/adapters/entry_gate_chain.py) | ✅ 调 `RiskOrchestrator.from_config` |
| OOT 批处理 | [`cta/model/oot/pipeline_oot_evaluation_inputs.py:181`](../../model/oot/pipeline_oot_evaluation_inputs.py) | ✅ 输出 `risk_lots_mult` 等列 |
| train manifest 产出 | [`cta/model/orchestration/pipeline_run_predictions.py:14`](../../model/orchestration/pipeline_run_predictions.py) | ✅ `build_from_predictions` |
| **live runner** | `cta/live/**` | ❌ **零引用 cta.risk** |

---

## 2. 优点（值得保持）

1. **fail-open 贯穿全栈**：manifest/state 文件缺失或损坏 → 返回空对象；adjuster/scaler 抛异常 → orchestrator try/except 退回上一阶段输出（[`orchestrator.py:177-182,210-213`](../../risk/orchestrator.py)）。单个组件崩不影响整链，符合"风控不能反过来锁死交易"的第一原则。
2. **frozen dataclass + post_init 严校验**：每个 cfg 都校验区间/单调/长度匹配（如 `bucket_pnl_mults` 长度必须 = `bp_steps + 1`、`vol_pctl_edges` 严格升序），把错误配置挡在构造期。
3. **round-down 保护一致**：所有 sizer 在 `floor(lots×mult)==0 且 lots≥1 且 mult>0` 时保留 1 手，避免"缩仓"意外变成"清零下单"。
4. **防数据泄露**：[`build_from_predictions`](../../risk/state/score_quantile_manifest.py) 默认 `exclude_splits=("test","oot")`，manifest 只用 train+valid 分布。
5. **score 双向归一化**：[`extract_score`](../../risk/base.py:76) 把 `≤1` 的值当 fraction 自动 ×100，pctl/raw 两种口径都能稳健解析。
6. **guards 复用现有 `_BaseRule`**：不重写硬拒链路，append 进 `RiskGuard.rules` 即生效，零侵入。

---

## 3. 问题清单

### H1（高危）— QuantileThresholdAdjuster `emit_pctl=True` 退化为 no-op 且覆盖 bond 严格阈值

[`cta/risk/threshold/quantile_threshold.py:60-65`](../../risk/threshold/quantile_threshold.py)

```python
if self.emit_pctl:                              # orchestrator 默认走这支
    label_value = self._pctl_from_field(self.quantile_field)  # p70 → 恒 70.0
    new_thr = float(label_value)
else:
    new_thr = float(raw_q)                      # 只有这支才用 manifest 的 per-symbol 数据
```

- `manifest.lookup()` 算出的 `raw_q`（per-(cluster,symbol) 实际分位）在 `emit_pctl=True` 下**被完全丢弃**，输出恒为 `quantile_field` 字面值（p70 → 70.0），与具体 symbol 无关。
- 设计文档 [`risk.md §3.2`](../risk.md) 声称"从全局 P70 排名换成每个 (cluster,symbol) 自己的 P70"，但 wired 默认（`emit_pctl=True`）**根本没用到 per-symbol 数据** → 子系统①在默认 base=70 + p70 时是纯 no-op。
- **更严重**：`StaticThresholdAdjuster` 先把 bond|day 设成 Action 2 的严格 80pp，`QuantileThresholdAdjuster` 紧随其后，对任何**在 manifest 里存在的 bond cell** 把 80 重新拉回 70 → **悄悄回退 Action 2 的 bond 严格阈值**。触发条件：`enable_quantile_threshold=True`（默认）+ 加载了含 bond 条目的 manifest。

**建议**：
- 短期：`emit_pctl` 模式下命中 manifest 时应 `new_thr = max(base_threshold, label_value)`（只抬不降），避免覆盖更严的 static 层；
- 根治：明确子系统①语义——若目标是"per-symbol 动态阈值"，emit_pctl 模式必须真正消费 `raw_q`（换算成该 symbol 自己的 pctl），否则删掉 emit_pctl=True 这条无效路径，文档同步改写。

### H2（高/中）— W7-W10 共 10 个组件未进 orchestrator 自动装配

[`orchestrator.py:91-151`](../../risk/orchestrator.py) `from_config` 只装配 5 个：static / quantile / dynamic_bump（threshold）+ bucket / linear_dd（sizing）。

- Holiday / VolatilityRegime / NightSessionCarry / DailyVaR / ExecutionQuality / ProfitGiveBack（6 个 sizer）+ 6 个新 guard **不会被 from_config 自动挂上**。
- `entry_gate_chain.py:120` 调 from_config 时也没传 `extra_position_scalers`，所以 sim 路径上这 10 个组件**实际不生效**。
- 文档 [`risk.md §20`](../risk.md) 把 W7/W8 标"✅ 已落"，但"已落"= 代码+单测存在，**≠ 生产路径生效**。容易让人误判风控覆盖面。

**建议**：在 `RiskSystemConfig` 增对应 enable 开关 + `from_config` 装配分支（或显式文档说明这些组件需 caller 经 `extra_position_scalers` / 独立 `RiskGuard` 注入），并在 risk.md §20 区分"已实现"与"已接入生产"。

### M1（中）— `linear_dd_hysteresis_pct` 配置定义但无人消费

- [`config.py:63`](../../risk/config.py) 定义 `linear_dd_hysteresis_pct=0.005` 并在 post_init 校验；
- [`linear_dd_scaler.py:12`](../../risk/sizing/linear_dd_scaler.py) 注释称"滞后需在 DdSignalProvider 层做"；
- 但 [`dd_signal_provider.py`](../../risk/state/dd_signal_provider.py) 没有任何 hysteresis 逻辑，`from_config` 也未传递该参数 → **死配置 / 未实现特性**。

**建议**：要么在 DdSignalProvider 实现滞后（dd 回到 trigger-hysteresis 之下才解除当档），要么删除该配置字段，避免"配了不生效"。

### M2（中）— live 未接入，违反"OOT/sim/live 同 cfg 同行为"不变量

`cta/live/**` 零引用 `cta.risk`。sim 与 OOT 已接，但 live runner 缺。设计不变量（risk.md §10.3）要求三层同一 orchestrator 实例。当前 live 走单独路径，存在 cfg 漂移风险。

**建议**：参照 `entry_gate_chain.py` 把 RiskOrchestrator 接入 live 下单链路（W5 收尾项）。

### L1（低）— 文档"阶梯"与实现"连续线性"不一致

- [`dynamic_bump.py:61-72`](../../risk/threshold/dynamic_bump.py) / [`linear_dd_scaler.py:57-69`](../../risk/sizing/linear_dd_scaler.py) 实际是**连续**线性（`steps = excess/step_pct` 不取整）：dd=1.5% → bump=+2.5pp。
- 但 docstring 与 risk.md §5.2/§5.3 用**阶梯表**呈现（dd∈[1%,2%) → +5pp）。行为本身（连续更平滑）没问题，但文档误导读者。

**建议**：把文档表格改成"连续线性，示例值"，或在实现里 `floor(steps)` 改成真阶梯，二选一对齐。

### L2（低）— 文件头 docstring 默认值过期

[`bucket_scaling.py:3-8`](../../risk/sizing/bucket_scaling.py) 头注释写 `bp_steps=(-5,-10)` / `mults=(0.9,0.7,0.5)`，实际签名默认 `(-10,-5,0)` / `(0.5,0.7,0.9,1.0)`。仅注释陈旧，逻辑正确。

### L3（低）— manifest `meta` 可能含绝对路径

[`score_quantile_manifest.py:140-146`](../../risk/state/score_quantile_manifest.py) `to_json` 把 `source_predictions_paths` 等 meta 原样写入 JSON。若 manifest 落 git，会带本机绝对路径。建议 manifest 统一写 `cta/run/` 或 `cta/model/manifests/` 并确认在 `.gitignore` 内（非安全问题，属可复现性卫生）。

---

## 4. 测试评估

- `python3 -m pytest cta/risk/tests/ -q` → **257 passed in 0.51s**。
- 覆盖面好：每组件含 happy path / fail-open（state 缺失）/ 边界（lots=0、dd=0/100%、空 manifest）/ cfg post_init 拒绝非法值。
- **缺口**：
  1. 没有"H1 场景"测试——即 quantile adjuster 命中 bond 严格 cell 后是否覆盖 80pp。建议补一条断言 quantile 不下调 static 严格阈值。
  2. 没有 orchestrator + W7-W10 sizer 经 `extra_position_scalers` 串联的集成测试（验证多 sizer 连乘 + BLOCK 归因）。
  3. entry_gate_chain Stage 5 与 orchestrator 的集成测试在 sim 侧（需确认 `cta/sim/tests` 覆盖）。

---

## 5. 修复优先级建议

| 优先级 | 项 | 动作 |
|--------|----|------|
| **P0** | H1 | quantile emit_pctl 改 `max(base, label)`（不下调严格阈值）+ 补测试；或重定义子系统①语义 |
| **P0** | H2 | from_config 装配 W7-W10 或文档明确"需 extra 注入"；risk.md §20 区分"实现/接入" |
| P1 | M1 | 实现或删除 `linear_dd_hysteresis_pct` |
| P1 | M2 | live runner 接入 RiskOrchestrator |
| P2 | L1/L2/L3 | 文档与实现对齐（阶梯↔连续、过期注释、manifest 路径卫生）|

---

## 6. 一句话总结

`cta/risk/` 是一套架构优雅、fail-open 严谨、测试扎实的可扩展风控框架；**唯一阻断性问题是子系统①在默认配置下既无效又会暗中回退 bond 严格阈值（H1）**，加上 W7-W10 组件"实现了但生产不生效"（H2）。修掉这两点 + live 接入后，即可作为 sim soak 启动门槛的合格风控底座。
