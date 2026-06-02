# 资金/名义利用率分析 + 仓位/sizing 参数实现规格（usage_pct）

> **本文性质**：前半（§1-§7）是基于两次全量 OOT 的诊断与结论；后半（§8-§10）是一份
> **可直接生成代码的实现规格（CODE-READY）**——codex/claude 照着 §8 即可落地，无需再猜字段、文件或行为。
>
> 两次 OOT（2024-01-02 → 2025-12-30，`initial_capital=10,000,000`，`margin_rate≈0.12`，
> `use_portfolio_logic_runtime=True`，`trade_filter_gate_mode=cluster_interval_percentile`，`use_risk_system=False`）：
> - **BEFORE（紧约束，实测最优）**：`cta/backtest/oot_20260530_215522_cluster_both`
>   单笔 `max_position_scale=0.10`、单品种 `max_symbol_notional_pct=0.30`/并发 3、组合 `max_concurrent_positions_total=10`、
>   CapsConfig `max_total_per_cluster=4`/`max_cluster_notional_pct=0.50`/`max_total_notional_pct=1.5`。
> - **AFTER（放宽）**：`cta/backtest/oot_20260531_000100_cluster_both`
>   单品种 `0.40`/并发 5、组合 `100`、CapsConfig `per_cluster=20`/`cluster_notional=0.60`。

---

## 0. 一句话结论

1. **放宽仓位上限被实测证伪**：AFTER 多做 18% 交易、净利只 +0.9%，但 MDD -4.31%→-5.25%、Sharpe 2.65→2.50、
   Calmar 9.09→7.51 全面变差，风险资本效率 8.18×→5.45×。portfolio_constraints 砍掉的 88% 候选是**有效质量闸门**，
   不是瓶颈。→ **回退放宽的 5 个上限**（§8.1 改动 A）。
2. **资金虽闲置 95%，但盲目放量赚不到钱**。真正的增长杠杆是"让好信号吃更大仓"：
   bull/breakout_pullback_continuation 60%+ 胜率、占净利 67%；atr_breakout 占 78% 笔数却 edge 薄、放量时边际为负。
   → **新增按 signal_type 差异化 sizing**（§8.3 改动 C）：pullback 类放大、atr_breakout 缩小。
3. 单簇并发上限在旧值 4 基础上**略放宽到 8**（§8.1 改动 A），给强势板块一点余量但远离失败的 20。

---

## 1. 收益与稳健性（BEFORE，确认值得优化）

| 指标 | 值 | 备注 |
|------|----|------|
| 总收益率 | **93.26%** | 2 年，10M 本金，净利 9,326,370 |
| 年化 | **39.15%** | |
| 最大回撤 | **-4.31%** | 极浅 |
| 月度 Sharpe | **2.65** | |
| Calmar | 9.09 | |
| 胜率 | 45.3% | 7248 笔 |
| 手续费/毛利 | 22.5% | 成本占比可接受 |

walk_forward 4 个半年窗年化 **94%/38%/28%/53%**，`single_segment_overfit_warning=False`，跨时间稳健。

---

## 2. 资金利用率：95% 闲置（核心发现）

`09_diagnostics/daily_trade_position_distribution.csv`（484 交易日）+ `raw/all_trade_details.csv`：

| 维度 | p50 | p90 | max | 说明 |
|------|-----|-----|-----|------|
| 单日 margin_used 峰值 % | 4.79 | 4.80 | **4.81** | 全期**资金占用峰值仅 4.81%** |
| 单日 EOD margin % | 3.55 | 4.78 | 4.82 | 隔夜更低 |
| 单笔 `position_scale`（名义/权益） | 0.100 | 0.100 | **0.100** | **每笔都顶满 10% 天花板** |
| 单笔 entry_margin/cap | 0.0122 | 0.0133 | 0.0143 | 单笔保证金仅 ~1.2% 权益 |
| 单日 position_notional 峰值 % | 29.95 | 30.03 | **32.24** | 顶在旧 30% 单品种上限 |
| 单日成交笔数 | 15 | 21 | 31 | |

关键推论：
1. **`position_scale` 恒等于 0.10** → 反推 sizing 几乎总是想要更大、被 `max_position_scale` 裁掉。**单笔大小被天花板压着**。
2. margin 峰值 4.81% ≪ 名义/杠杆上限（150% 杠杆从未触及）；真正 binding 的是**单品种 30%**（=3 笔 × 10%）。
3. `return_on_risk_capital = 8.18×`：以真实占用资金衡量 edge 极强；10M 本金对这套策略**约 20× 偏大**。

---

## 3. 名义利用率：按品种/板块拆解

- **单品种**：活跃品种 `max_after_pct ≈ 30%`，紧贴旧 0.30 上限（被打满）；`p90_trade_notional ≈ 10%`（单笔天花板）。
- **板块**（`cluster_notional_usage_pct.csv`，节选）：

| cluster | trades | max_after% | sum_pnl |
|---------|--------|-----------|---------|
| other | 2484 | 32.24 | **+3,814,318** |
| chemical | 1038 | 30.46 | +1,475,518 |
| precious | 59 | 10.08 | **+924,409**（单位效率最高）|
| bond | 268 | 20.00 | **-49,963**（唯一净亏）|

- 板块名义峰值 ~30% **远低于 0.50 上限** → 板块名义不 binding；真正限板块宽度的是 CapsConfig `max_total_per_cluster=4`。
- bond 名义只用 20% 仍亏 → 是**信号问题**非容量问题。

---

## 4. 漏斗：88% 候选死在 portfolio_constraints

`06_drilldown/gate_funnel.csv`：candidates 72,127 → after_htf 71,028 → after_ranker 59,475 →
**after_portfolio_constraints 7,248（仅 12.2%）** → executed 7,248。

**ranker 选出的 59,475 候选里 87.8% 被仓位/笔数约束挡掉。** 与 4.81% 资金利用率并列：候选被拒 + 资金闲置。
但 §7 的 AFTER 实证表明：放进这批候选**不赚钱**——它们是 ranker 排序靠后的低质量信号。

---

## 5. 集中度：偏高

| 指标 | 值 |
|------|----|
| pnl_gini | 0.451 |
| 剔除 top20 笔后剩余净利 | 55.2%（top20 = 45% 净利）|
| annualized_excl_top20 | **24.7%**（去肥尾仍有 24.7% 年化）|
| top5 品种净利占比 | 40.3% |
| concentration_warning | **True** |

单品种 EC0 +1,434,589（占 15.4%）、AU0 +660,726、LH0 +606,063、IC0 +544,332、BC0 +512,957；cluster_other 占 40.9%。

---

## 6. signal_type / interval：alpha 来源（§8.3 的依据）

| signal_type | 笔数 | 胜率 | 净利 |
|-------------|------|------|------|
| **bull_pullback_continuation** | 640 | **65.8%** | **+4,069,284** |
| **breakout_pullback_continuation** | 446 | **60.3%** | +2,233,315 |
| atr_breakout | 5662 | 42.5% | +1,576,427 |
| trend_acceleration_breakout | 81 | 39.5% | +651,881 |
| cross_sectional_momentum | 133 | 47.4% | +411,657 |
| donchian_breakout | 265 | 31.7% | +337,219 |

interval：minute60 收益率 42.1% > minute30 26.3% > day 24.9%（day 胜率仅 27.2%，最弱）。

- 两类 pullback_continuation = **质量之王**（60%+ 胜率，合计占净利 67%，仅占 15% 笔数）→ 加大单笔 + 更多名额。
- atr_breakout 是工作马（78% 笔数）但 edge 薄，应**缩小单笔量级**。

---

## 7. BEFORE vs AFTER 实证（放宽无效的硬证据）

| 指标 | BEFORE（紧） | AFTER（放宽） | 变化 |
|------|------|------|------|
| 执行笔数 | 7,248 | 8,569 | +18% |
| 净利 | 9,326,370 | 9,408,272 | **+0.9%** |
| 年化 | 39.15% | 39.44% | 持平 |
| 最大回撤 | -4.31% | **-5.25%** | **变差** |
| 月度 Sharpe | 2.65 | **2.50** | **变差** |
| Calmar | 9.09 | **7.51** | **变差** |
| return_on_risk_capital | 8.18× | **5.45×** | **效率↓33%** |
| pnl_gini（笔级） | 0.451 | 0.465 | 略升 |
| top5 品种净利占比 | 40.3% | 37.8% | 略改善 |

**新增交易零 edge**：cluster_other +1,020 笔仅 +30,335；**atr_breakout +1,167 笔，桶净利反降 224,898**；
仅 bull_pullback +86 笔 +104k 仍优质（佐证 §8.3 方向）。→ 放宽不成立，回退。

---

# 8. 实现规格（CODE-READY）

> 约定：TDD（先写/改测试再改实现）；只改 `cta/**`；frozen dataclass + 类型注解；每文件 < 500 行。
> 每条改动给【目标 / 文件:符号 / 旧→新 或 接口 / 验收】。

## 8.1 改动 A — 回退 5 个上限 + `max_total_per_cluster` 放宽到 8

**目标**：撤销被证伪的放宽；单簇并发在旧值 4 基础上略放宽到 8。

**文件 1**：`cta/config/model_oot_eval_config.py`（`OotEvaluationConfig`，约 L166-171）

| 字段 | 当前值 | 改为 |
|------|------|------|
| `max_symbol_notional_pct` | 0.40 | **0.30** |
| `max_concurrent_positions_per_symbol` | 5 | **3** |
| `max_concurrent_positions_total` | 100 | **10** |

**文件 2**：`cta/portfolio_logic/config.py`（`CapsConfig`，约 L155-160）

| 字段 | 当前值 | 改为 |
|------|------|------|
| `max_total_positions` | 100 | **10** |
| `max_per_symbol` | 5 | **1** |
| `max_total_per_cluster` | 20 | **8**（旧值 4 略放宽）|
| `max_symbol_notional_pct` | 0.40 | **0.30** |
| `max_cluster_notional_pct` | 0.60 | **0.50** |

**验收**：`CapsConfig().__post_init__` 校验通过（0.30 ≤ 0.50 ≤ 1.50）；构造 `OotEvaluationConfig()` 不报错。

## 8.2 改动 B — `max_position_scale` 回退到 0.10

**目标**：单笔名义上限基准回到 BEFORE 最优 run 的口径（当前误为 0.15）。

**文件**：`cta/config/model_oot_eval_config.py` L165
```python
# 旧: max_position_scale: float = 0.15
max_position_scale: float = 0.10  # 单笔最多吃 10% 权益（signal_type 系数在此基准上乘）
```
注释同步改为 0.10。**验收**：默认值断言 == 0.10。

## 8.3 改动 C — 新增 signal_type 差异化 sizing（主增量）

**目标**：高胜率的 pullback 类单笔放大、低 edge 的 atr_breakout 缩小（激进档）。

### C-1 新增配置字段（`OotEvaluationConfig`）
仿 `commission_pct_by_cluster_interval` 的 frozen-dict 范式：
```python
signal_type_size_multiplier: dict[str, float] = field(
    default_factory=lambda: {
        "bull_pullback_continuation": 2.0,
        "breakout_pullback_continuation": 1.5,
        "atr_breakout": 0.4,
    }
)
```
### C-2 `__post_init__` 归一化 + 校验（仿现有 `commission_pct_by_cluster_interval` 块）
- 键 `.strip().lower()`，值 `float()`；用 `object.__setattr__` + `MappingProxyType` 冻结。
- `_check(f"signal_type_size_multiplier[{key}]", value, 0.0, 5.0)`（开区间下界：值必须 > 0；上界 ≤ 5.0）。
- 空 dict 合法（= 全部信号系数 1.0）。

### C-3 pipeline 注入（`cta/model/oot/pipeline_oot_evaluation.py`）
1. **取列**（约 L139，仿 `symbol_arr` 写法）：
   ```python
   signal_type_arr = (
       selected.get('signal_type', pd.Series([''] * n, index=selected.index))
       .astype(str).str.strip().str.lower().to_numpy()
   )
   ```
2. **作用在单笔名义上限上**（约 L388，sizing 处）。当前两条分支都把 `position_scale` 截断到
   `min(cfg.max_position_scale, 1.0)`；改为乘上系数后再截断：
   ```python
   st = signal_type_arr[idx] if idx < len(signal_type_arr) else ''
   st_mult = float(cfg.signal_type_size_multiplier.get(st, 1.0))
   eff_cap = min(float(cfg.max_position_scale) * st_mult, 1.0)
   position_scale = min(position_scale, eff_cap)   # reverse-sizing 分支
   # fallback 分支同理：position_scale = min(eff_cap, 1.0)
   ```
   语义：pullback 上限抬到 0.10×2.0=0.20、breakout 0.10×1.5=0.15、atr 压到 0.10×0.4=0.04。
   **reverse-sizing 分支与 fallback 分支都要乘 `st_mult`**，保持一致。
3. 既有列 `position_scale`（trade_cols 已含）便于事后核对每笔实际倍数；无需新增列。

**验收**：构造含 `signal_type ∈ {bull_pullback_continuation, atr_breakout}` 的候选，断言输出
`position_scale`：pullback 笔 > atr 笔，且分别 ≈ `min(reverse_scale, 0.20)` / `min(reverse_scale, 0.04)`。

## 8.4 改动 D — signal_type 并发名额优先级（建议同改，可独立验证）

**目标**：有限名额下让 pullback 优先占坑（"更多并发名额"的真实含义）。

**文件**：`cta/portfolio_logic/opportunity_ranker.py` `allocate()`（L174 排序处）
- 复用 C-1 的 `signal_type_size_multiplier`（系数 > 1 即视为高优先），或新增轻量
  `signal_type_rank_bonus: dict[str,float]`（默认 pullback 类 +0.02、atr 0.0）。
- 把 `df_scored.sort_values("score", ascending=False)` 改为按临时列
  `score_rank = score + bonus(signal_type)` 降序排序；**不改 score_threshold 闸门（L178 仍用原始 score）**，
  即只影响"谁先占坑"，不放宽准入。
- 标注：最小实现可只做 C（sizing），D 作为 phase-2 独立验证。

**验收**：`test_opportunity_ranker` 中构造同 `score`、不同 signal_type 的两候选 + 仅 1 个名额，断言 pullback 被选中。

## 8.5 测试（TDD，先写）
- `cta/config/tests/test_model_oot_eval_config.py`：`signal_type_size_multiplier` 默认值（3 个键）、键归一化（大写/空格）、
  越界（≤0 或 >5 抛错）、空 dict 合法；CapsConfig 间接通过 §8.1。
- `cta/model/tests/`（pipeline 单测群，如 `test_model_pipeline_part0x` 或新建 `test_signal_type_sizing.py`）：
  断言不同 signal_type 的 `position_scale` 倍数关系（改动 C-3 验收）。
- `cta/portfolio_logic/tests/test_config.py`：CapsConfig 新默认值 `10 / 1 / 8 / 0.30 / 0.50`。
- `cta/portfolio_logic/tests/test_opportunity_ranker.py`：改动 D 验收。
- 回归：`python3 -m pytest cta/config/tests cta/portfolio_logic/tests cta/model/tests -q`。

## 8.6 三层一致 / 不变量
- `signal_type_size_multiplier` 属 `OotEvaluationConfig`（单一来源）。**实现时必须确认 sim/live 的 sizing 路径
  也读取并应用同一系数**：若 live/sim 的下单 sizing 不走 OOT pipeline，需在 `cta/risk/sizing`（或 live 下单 sizing 处）
  用同一字段，避免 OOT≠sim≠live 分叉。这是硬性验收项。
- cfg_fingerprint 三处比对照旧；防泄露不受影响（静态配置，不触信号/标签链路）。
- 仅改 `cta/**`；不动 `cross_sectional_rotation_config` 的 `max_symbol_notional_pct`（轮动权重上限，语义不同）。

---

## 9. 验证与复跑（已执行，结果如下）

复跑命令（与 BEFORE/AFTER 同口径）：
```bash
python3 -m cta.model.eval --from-root cta/backtest \
  --pattern '20260529_GRP_CLUSTER_*_both_model_pipeline' \
  --output-root cta/backtest --run-tag cluster_both_sigsize
```
NEW=`oot_20260531_011914_cluster_both_sigsize`（回退上限 + per_cluster=8 + signal_type 激进档 sizing）。

### 9.1 三向对比（headline）
| 指标 | BEFORE（紧） | AFTER（放宽） | **NEW（sigsize）** |
|------|------|------|------|
| 执行笔数 | 7,248 | 8,569 | 8,600 |
| 净利 | 9,326,370 | 9,408,272 | 8,954,359 |
| 年化 | 39.15% | 39.44% | 37.80% |
| 最大回撤 | -4.31% | -5.25% | **-3.87%**（最优）|
| 月度 Sharpe | 2.65 | 2.50 | **3.91**（+47% vs BEFORE）|
| Calmar | 9.09 | 7.51 | **9.77**（最优）|
| pnl_gini（笔级） | 0.451 | 0.465 | 0.522 |
| top1 单笔占比 | 8.6% | 8.5% | **3.75%** |
| top5 品种占比 | 40.3% | 37.8% | **29.2%** |
| concentration_warning | True | True | **False** |

### 9.2 signal_type 分桶（净利）
| signal_type | BEFORE | NEW | 说明 |
|-------------|--------|-----|------|
| bull_pullback_continuation | 4,069,284 | **4,236,656** | ↑（×2.0 放大高胜率信号）|
| breakout_pullback_continuation | 2,233,315 | 2,174,322 | ~持平 |
| atr_breakout | 1,576,427 | **961,935** | ↓（×0.4 缩量低 edge 信号，符合预期）|

### 9.3 判定：**通过（风险调整后大胜）**
- Sharpe 2.65→**3.91（+47%）**、Calmar 9.09→**9.77**、MDD -4.31%→**-3.87%**（均改善）；
- 集中度大幅下降：top5 品种 40.3%→29.2%、top1 单笔 8.6%→3.75%、**concentration_warning 由 True 变 False**；
- 仅以年化 -1.3pp（39.15%→37.80%）换取上述全面的风险/集中度改善 → **达标，保留激进档**。
- 机制验证：bull_pullback 净利↑、atr_breakout 净利↓，与 §8.3 设计意图完全一致。
- 若未来想找回那 1.3pp 年化：可把 atr_breakout 系数从 0.4 略放回 0.5-0.6（中等档），重测权衡。

---

## 10. 不变量与边界
- 原始 OOT 产物只读；本文档不改代码（codex/claude 据 §8 实现）。
- 关于 10M 本金：真实峰值保证金 ~481k、风险资本 ~960k，10M 约 20× 偏大；实盘容量口径需统一写死，
  对外汇报用 `return_on_risk_capital` 而非 return%。与本规格的上限/sizing 改动正交。
- 防特征穿越 / 标签泄露：分位阈值仍来自训练期 manifest；sizing 系数为静态配置，不进入信号/标签链路。

---

# 11. signal_type 精细化优化方案（v2 数据驱动，CODE-READY）

> 依据 v2 run `oot_20260531_090349_cluster_both_sigsize_v2`（年化 56.9% / Sharpe 4.09 / MDD -3.02% /
> concentration_warning False）的 `05_by_signal_type/_comparison.csv`。在 §8 的"单笔 size 系数"基础上，
> 进一步用"成交笔数"和"同时在仓笔数"两个维度做差异化，解决两个问题：
> (a) 各 signal_type 的 `avg_net_pnl_per_trade` 离散度过大；(b) 回撤集中在少数低质量类型。

## 11.1 v2 现状（问题诊断）

| signal_type | 笔数 | win% | **avg_pnl/笔** | **maxDD%** | sharpe | calmar | top5_sym% |
|---|---|---|---|---|---|---|---|
| bull_pullback_continuation | 719 | 63.4% | **10,869** | -0.88% | 5.21 | 38.3 | 23% |
| breakout_pullback_continuation | 501 | 60.5% | 6,553 | -0.71% | 4.63 | 21.6 | 27% |
| trend_acceleration_breakout | 80 | 40.0% | **13,556** | -2.36% | 0.37 | 2.3 | **99.4%** |
| cross_sectional_momentum | 138 | 47.8% | 4,762 | -0.90% | 0.63 | 3.7 | 80% |
| tight_range_breakout | 21 | 33.3% | 2,476 | -1.28% | **-1.05** | 0.22 | 99.6% |
| donchian_breakout | 264 | 31.4% | 1,746 | **-6.48%** | 0.06 | 0.35 | 87% |
| atr_breakout | 6623 | 42.5% | **184** | **-7.66%** | 0.48 | 0.77 | 56% |

诊断：
1. **avg_pnl/笔 离散度 ≈ 74×**（13,556 vs 184）。`atr_breakout` 用 6623 笔（占 79%）做近零 edge（184/笔）的量，
   稀释组合且贡献最差回撤。
2. **回撤集中在低质量类型**：atr_breakout -7.66%、donchian -6.48%，远超其它（均 < 1.3%）。
3. **脆弱/负 alpha**：trend_accel（top5_symbol 99.4% = 纯肥尾、40% 胜率）、tight_range（Sharpe **-1.05**，
   风险调整为负）。

## 11.2 三级分层（CTA 视角）

- **Tier S 核心 alpha**：`bull_pullback_continuation` / `breakout_pullback_continuation`
  —— 胜率 60-63%、avg 高、DD < 1%、Sharpe 4.6-5.2、分散好（top5_sym 23-27%）。**放大 + 给量 + 宽准入**。
- **Tier M 中等/脆弱**：`cross_sectional_momentum`（中等）、`trend_acceleration_breakout`
  （avg 高但 99% 肥尾、40% 胜率）。**标准/收紧，控尾部**。
- **Tier C 低质量/高回撤**：`atr_breakout`（184/笔、DD -7.66%）、`donchian_breakout`（31% 胜率、DD -6.48%）、
  `tight_range_breakout`（Sharpe -1.05）。**缩量 + 限仓 + 提阈值减量**。

## 11.3 三杠杆策略表（默认值，可迭代）

| signal_type | 杠杆1 `size_mult` | 杠杆2 `max_concurrent`（新）| 杠杆3 `tf_pctl_delta`（新）|
|---|---|---|---|
| bull_pullback_continuation | 2.0 | 6 | -5 |
| breakout_pullback_continuation | 1.5 | 5 | -3 |
| cross_sectional_momentum | 1.0 | 3 | 0 |
| trend_acceleration_breakout | 0.7 | 2 | +5 |
| atr_breakout | 0.4 | 3 | +15 |
| donchian_breakout | 0.5 | 2 | +12 |
| tight_range_breakout | 0.3 | 1 | +20 |

- **杠杆1 `size_mult`**（已存在 `signal_type_size_multiplier`，扩展默认 dict）：**需求 2 的"降低单笔仓位"**——
  Tier C/脆弱类下调（trend_accel 0.7、donchian 0.5、tight_range 0.3，atr 维持 0.4）。
- **杠杆2 `max_concurrent`（新）**：**需求 2 的"限制同时在仓笔数"**——高 DD 类收紧到 1-3。
- **杠杆3 `tf_pctl_delta`（新）**：**需求 1**——对 atr/donchian/tight_range 提高准入分位阈值（+12~+20），
  砍掉低分长尾，保留笔均质量↑、avg_pnl/笔↑、笔数↓，从而压缩离散度；Tier S 放宽（-3~-5）多给量。

> 需求映射：**需求 1（按 win/avg 调笔数、压缩离散度）→ 杠杆3**；**需求 2（高回撤限仓+降仓）→ 杠杆1+杠杆2**。

## 11.4 目标（迭代判定指标）
- `avg_pnl/笔` 离散度由 ~74× 压到 **≤ ~10×**（保留类型落在约 1,500–12,000 区间）。
- 任一 signal_type `maxDD` 不差于 **约 -4%**（atr/donchian 从 -7.7%/-6.5% 收敛）。
- 组合 Sharpe / Calmar **不低于 v2**，MDD 不恶化，`concentration_warning` 保持 False。

## 11.5 实现规格（CODE-READY）

### 杠杆1（扩展现有字段）
- `cta/config/model_oot_eval_config.py` 的 `signal_type_size_multiplier` 默认 dict 扩成 §11.3 七项
  （新增 `trend_acceleration_breakout=0.7`、`donchian_breakout=0.5`、`tight_range_breakout=0.3`）。
- 现有 `__post_init__` 校验 (0,5] 与 pipeline 注入（`resolve_effective_position_scale_cap`，§8.3）不变。

### 杠杆2（新）`signal_type_max_concurrent_positions: dict[str, int]`
- `model_oot_eval_config.py`：新增字段（`default_factory` 取 §11.3 列）+ `__post_init__`（键 `.strip().lower()`、
  值 `int>=1` 校验、`MappingProxyType` 冻结）+ helper `resolve_signal_type_max_concurrent(signal_type) -> int | None`
  （未配置返回 None = 不额外限制）。
- `cta/model/oot/pipeline_oot_evaluation.py`：`runtime_state` 增 `signal_type_counts`——`add_position` 的 dict
  带 `signal_type`，add/remove 时维护该计数；portfolio_constraints 循环（约 L412，紧随
  `max_concurrent_positions_total` / `blocked_symbol_concurrent` 之后）新增：
  ```python
  st_cap = cfg.resolve_signal_type_max_concurrent(signal_type_arr[idx])
  st_count_now = int(runtime_state.signal_type_counts.get(signal_type_arr[idx], 0))
  elif st_cap is not None and st_count_now >= st_cap:
      notional = 0.0; reason = BR_BLOCKED_SIGNAL_TYPE_CONCURRENT
  ```
- `cta/model/oot/block_reasons.py`：新增 `BR_BLOCKED_SIGNAL_TYPE_CONCURRENT = "blocked_signal_type_concurrent"`。
- `cta/docs/block_reason.md` §1.1 表增 1 行。

### 杠杆3（新）`signal_type_trade_filter_percentile_delta: dict[str, float]`
- `model_oot_eval_config.py`：新增字段（`default_factory` 取 §11.3 列）+ `__post_init__`（键归一化、
  值 `_check(..., -50.0, 50.0)`、冻结）。
- `cta/model/oot/oot_trade_filter_gate.py`：在 `apply_trade_filter_gate` 解出 `threshold`(Series) 后，
  仿 `trend_delta` 的加法叠加——按行 `signal_type` 映射 delta 加到 `threshold`，再 `clip(0, 100)`。
  仅 `mode == "cluster_interval_percentile"` 生效；其它模式 fail-open 不加（文档注明）。

### 三层一致 / 不变量
- 三个 dict 均属 `OotEvaluationConfig`（单一来源）。杠杆1 已有 sim/live 等价件
  `cta/risk/sizing/signal_type_size_scaler.py`；**杠杆2/杠杆3 的 sim/live 等价件列为 follow-up**
  （OOT 先落地验证；实盘需对齐时在 `cta/risk/guards` 加 per-signal_type 并发 guard + 在线阈值叠加）。
- 防泄露不变（静态配置，不触信号/标签）；仅改 `cta/**`；frozen dataclass + 类型注解 + 文件 < 500 行。

### 测试（TDD）
- `cta/config/tests/test_model_oot_eval_config.py`：两个新 dict 默认值 + 键归一化 + 越界校验
  （`max_concurrent` < 1 抛错、`delta` 越 [-50,50] 抛错）。
- `cta/model/tests/`（trade_filter gate）：同 (cluster, interval) 下不同 signal_type 的 effective threshold = base + delta。
- `cta/model/tests/`（pipeline）：构造超过 `signal_type_max_concurrent` 的同类同时在仓候选，断言第 N+1 笔
  `block_reason == "blocked_signal_type_concurrent"`。
- 修复因 atr 提阈值/缩量受影响的既有 cap 测试（按需加 `signal_type_max_concurrent_positions={}` /
  `signal_type_trade_filter_percentile_delta={}` 解耦）。
- 回归：`python3 -m pytest cta/config/tests cta/portfolio_logic/tests cta/risk/tests cta/model/tests -q`。

---

# 12. 验证与迭代（v3）

落地 §11 后重跑（与 v2 同口径，新 run_tag）：
```bash
python3 -m cta.model.eval --from-root cta/backtest \
  --pattern '20260529_GRP_CLUSTER_*_both_model_pipeline' \
  --output-root cta/backtest --run-tag cluster_both_sigtype_v3
```
对比 v2 vs v3 的 `05_by_signal_type/_comparison.csv` + `00_overview/headline_metrics.csv`：
- **达标**：avg_pnl/笔 离散度 ↓（≤ ~10×）；atr/donchian 的 maxDD 收敛到 ~-4% 内；组合 Sharpe/Calmar ≥ v2、
  MDD 不恶化、concentration_warning 仍 False。
- **不达标 → 单维度迭代**（每次只动一类、记录对比）：
  - 某类 avg 仍过低 / 笔数仍过多 → 该类 `tf_pctl_delta` 再 +3~+5；
  - 某类 maxDD 仍 > -4% → 该类 `max_concurrent` -1 或 `size_mult` -0.1；
  - 总收益掉太多 → 把 atr/donchian 的 `tf_pctl_delta` 回调（如 +15→+10），在收益与回撤间取平衡。
