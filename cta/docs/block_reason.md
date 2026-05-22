# block_reason 全量 review 与修复方案

> **本文档面向 codex 实施。** 起因：`20260517_GRP_CLUSTER_METAL_day/details.csv` 中绝大多数样本的 `block_reason == "htf_missing"`，几乎吞掉全部交易。本文档把 CTA 代码库里**所有 32 种 `block_reason` 字面量**（外加 1 个易混淆的 `execution_status` sentinel）重新 review 了一遍，每种都给出触发条件、根因、修复方案、验收命令。
>
> `block_reason` 只解释入场为何被挡住。持仓后的震荡边界降仓不是 block：
> 开启 `oscillation_upper_band_taper` 后请看交易明细的
> `position_taper_count / position_taper_target_ratio / position_taper_realized_ratio`
> 与 `exit_reason="oscillation_upper_band_taper"`。
>
> **2026-05-20 更新**：§1.1 表新增两条 OOT 执行期 block_reason —— `blocked_ma_cross_trend` 和 `blocked_regime_short_filter`，默认 off，按 (cluster, interval) 灰度启用，详见 [ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)。
>
> **codex 实施纪律**：
> 1. 不许只挑容易的做 — 32 种 reason 每种都必须 review，禁止省略
> 2. 不许把"修复"写成 TODO — 每条都要给出 file:line + 代码 diff 草稿
> 3. 不许跳过测试 — §21 验收清单逐项打勾
> 4. 不许改业务 — 本次只**修 bug + 加配置开关**，不动交易逻辑/风控阈值默认值
> 5. 不许只看 grep — 每个 emit-site 必须读上下 30 行确认触发条件
> 6. 完工后必须复现 METAL day 案例，确认 `htf_missing` 比例从 ~100% 下降到 <5%

---

## §1 概览与 32 reason 总表

### 1.1 OOT 执行期（[pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py)，27 种）

| # | block_reason | emit 位置 | 触发条件 | 类别 | 优先级 |
|---|---|---|---|---|---|
| 1 | `invalid_time` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `exit < entry` 或任一为 NaT | 数据 | P0 bug |
| 2 | `blocked_throttle_halt` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `risk_throttle.compute() → "halt"`（周/月回撤超限） | 风控 | P2 正常 |
| 3 | `htf_missing` | [interval_gate.py:212](../portfolio_logic/interval_gate.py) → 复制到 [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `htf_state` 无该 (symbol, exchange) 条目，或 TTL 已过 | HTF gate | **P0 bug**（METAL 案例）|
| 4 | `htf_conflict` | [interval_gate.py:224](../portfolio_logic/interval_gate.py) | 各 HTF interval 上 regime 无法形成 long/short 共识 | HTF gate | P1 配置 |
| 5 | `htf_opposite` | [interval_gate.py:230,236](../portfolio_logic/interval_gate.py) | `state="long_only"` 但方向 short（反之同理） | HTF gate | P2 正常 |
| 6 | `htf_unknown` | [interval_gate.py:242](../portfolio_logic/interval_gate.py) | HTF state 落到 `{both,none,long_only,short_only}` 之外 | HTF gate（bug 兜底） | P0 bug |
| 7 | `ranker_dropped` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | OpportunityRanker 评分后未入选 | 选股 | P1 配置 |
| 8 | `blocked_limit_move` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | 多单遇涨停 / 空单遇跌停 | 数据/规则 | P2 正常 |
| 9 | `blocked_pyramid_rule` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `PyramidManager.decide_add_layer() == False` | 加仓规则 | P2 正常 |
| 10 | `blocked_monthly_drawdown` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `month_dd_breached and block_new_entries_on_monthly_dd_breach` | 风控 | P2 正常 |
| 11 | `blocked_weekly_drawdown` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `week_dd_breached and block_new_entries_on_weekly_dd_breach` | 风控 | P2 正常 |
| 12 | `blocked_total_concurrent` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `total_count_now >= max_concurrent_positions_total` | 仓位 | P2 正常 |
| 13 | `blocked_symbol_concurrent` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `sym_count_now >= max_concurrent_positions_per_symbol`（非加仓） | 仓位 | P2 正常 |
| 14 | `blocked_symbol_cap` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | 单品种名义金额触顶（默认 30%） | 仓位 | P2 正常 |
| 15 | `blocked_cluster_cap` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | 所属 cluster 名义金额触顶（默认 50%） | 仓位 | P2 正常 |
| 16 | `blocked_weekly_budget` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | 周回撤预算耗尽，单笔潜在亏损放不下 | 风控 | P2 正常 |
| 17 | `blocked_daily_position` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | 当日新开名义金额超 `max_daily_new_notional_pct`（默认 100%） | 仓位 | P2 正常 |
| 18 | `blocked_margin_cash` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `cash / margin_rate ≤ 0` | 资金 | P2 正常 |
| 19 | `blocked_leverage` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | 总名义 / 权益 ≥ `max_total_leverage`（默认 2.0） | 杠杆 | P2 正常 |
| 20 | `blocked_portfolio_constraint` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | notional ≤ 0 但上述 cap 都 > 0（**理论上不应出现**） | bug 兜底 | P0 bug |
| 21 | `zero_notional` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | sizing 算出 ≤ 0 且无具体 reason | sizing | P1 调查 |
| 22 | `blocked_ma_cross_trend` | [oot_gates.py:apply_ma_cross_gate](../model/oot/oot_gates.py) → 由 [pipeline_oot_evaluation.py 串行调用](../model/oot/pipeline_oot_evaluation.py) | `ma_alignment >= 1` 多头排列禁 short / `<= -1` 空头排列禁 long；仅在 `cfg.ma_cross_enabled_by_cluster_interval` 显式 opt-in 的 (cluster, interval) 启用。设计文档 [ma_cross_regime_aware_design.md §3](./ma_cross_regime_aware_design.md) | 趋势过滤 | P2 灰度（默认 off） |
| 23 | `blocked_regime_short_filter` | [oot_gates.py:apply_regime_short_filter](../model/oot/oot_gates.py) → 由 [pipeline_oot_evaluation.py 串行调用](../model/oot/pipeline_oot_evaluation.py) | 真实 `regime_label ∈ {trend_up,...}` AND `side=short`；用 candidate 行的 regime_label（不依赖模型预测的 pred_regime_label）。仅在 `cfg.regime_short_filter_enabled_by_cluster_interval` 显式 opt-in 启用。设计文档 [ma_cross_regime_aware_design.md §4](./ma_cross_regime_aware_design.md) | 趋势过滤 | P2 灰度（默认 off） |
| 24 | `blocked_trade_filter` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `trade_filter_prob`（或分位）低于阈值 | 模型门控 | P2 正常 |
| 25 | `blocked_regime_gate` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `pred_regime_label` 与方向冲突 | 模型门控 | P2 正常 |
| 26 | `blocked_mfe_mae_gate` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `pred_mfe_atr - λ*pred_mae_atr < min_pred_edge_atr` | 模型门控 | P2 正常 |
| 27 | `blocked_final_decision_gate` | [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py) | `final_decision_score < threshold` | 模型门控 | P2 正常 |

### 1.2 候选样本期（[candidate_schema.py](../model/feature/candidate_schema.py)，5 种 fallback）

当上游没塞 `block_reason` 时，按 `sample_status` 兜底映射：

| # | block_reason | emit 位置 | 对应 sample_status | 优先级 |
|---|---|---|---|---|
| 28 | `filtered_by_rule` | [candidate_schema.py:36](../model/feature/candidate_schema.py) | `filtered_by_rule` | P3 历史回放 |
| 29 | `risk_rule_blocked` | [candidate_schema.py:37](../model/feature/candidate_schema.py) | `blocked_by_risk` | P3 历史回放 |
| 30 | `capacity_blocked` | [candidate_schema.py:38](../model/feature/candidate_schema.py) | `blocked_by_capacity` | P3 历史回放 |
| 31 | `execution_rule_blocked` | [candidate_schema.py:39](../model/feature/candidate_schema.py) | `blocked_by_execution` | P3 历史回放 |
| 32 | `next_bar_not_triggered` | [candidate_schema.py:40](../model/feature/candidate_schema.py) | `not_triggered_market` | P3 历史回放 |

---

## §2 block_reason 与 execution_status 的关系（易混淆点）

`block_reason` 与 `execution_status` 是 **两列**，codex 不能混用：

- `execution_status`：枚举字符串，描述这笔候选最终落到哪个"状态桶"。如 `"executed"`、`"blocked_htf_gate"`、`"blocked_throttle_halt"`、`"blocked_zero_notional"`、`"pending"`、`"invalid_time"` 等。
- `block_reason`：在 `execution_status` 是被阻拦类别时，记录**具体原因**。`"executed"` 时为空字符串 `""`。

特别注意 [pipeline_oot_evaluation.py:1167](../model/pipeline_oot_evaluation.py) 的 sentinel：

```python
selected.at[idx, "execution_status"] = reason or "blocked_zero_notional"
selected.at[idx, "block_reason"] = reason or "zero_notional"
```

- `blocked_zero_notional` 是 **execution_status** 字段值，**不是 block_reason**
- `zero_notional` 才是 **block_reason** 字段值

下游 details 报表分析（按 `block_reason` 分组）千万不能把 `blocked_zero_notional` 当成 reason 算进表里。

---

## §3 OOT 执行流：reason 触发顺序

`_evaluate_oot_real_execution` 按下面顺序拒单（**前面的 reason 优先级更高**）。codex 看 details 时按此顺序定位根因：

```
[每根 bar 循环]
  Stage 1：data sanity         → invalid_time（exit<entry / NaT）
  Stage 2：risk throttle       → blocked_throttle_halt（halt 直接清空本 bar 入场队列）
  Stage 3：HTF gate            → htf_missing / htf_conflict / htf_opposite / htf_unknown
  Stage 4：opportunity ranker  → ranker_dropped（评分低于阈值）
  Stage 5：limit move check    → blocked_limit_move（涨跌停）
  Stage 6：pyramid manager     → blocked_pyramid_rule（加仓规则不满足）
  Stage 7：portfolio caps      → blocked_monthly_drawdown
                                 blocked_weekly_drawdown
                                 blocked_total_concurrent
                                 blocked_symbol_concurrent
                                 blocked_symbol_cap
                                 blocked_cluster_cap
                                 blocked_weekly_budget
                                 blocked_daily_position
                                 blocked_margin_cash
                                 blocked_leverage
                                 blocked_portfolio_constraint（兜底）
  Stage 8：sizing              → zero_notional
  Stage 9：executed            → 进入实际成交模拟
```

**诊断方法**：先看 details `value_counts(block_reason)`，按 reason 类别定位 stage；再聚焦该 stage 的配置和数据。

---

## §4 htf_missing 深度分析（METAL day 案例）

### 4.1 复现路径

1. 用户跑 `python -m cta.model.model_pipeline --group-pool CLUSTER_METAL --interval day --start ... --end ...`
2. 输出落到 `20260517_GRP_CLUSTER_METAL_day/`
3. `_evaluate_oot_real_execution` 在 [pipeline_oot_evaluation.py:540](../model/pipeline_oot_evaluation.py) 检测：
   ```python
   use_pl_runtime = bool(getattr(cfg, "use_portfolio_logic_runtime", False))  # True
   use_pl_htf = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, "enable_htf_gate", False)))  # True
   ```
4. 在 [pipeline_oot_evaluation.py:564-571](../model/pipeline_oot_evaluation.py) 按 `htf_intervals=("day", "60min")` 切分 `htf_reference`：
   ```python
   for itv in pl_cfg.interval_gate.htf_intervals:
       ref_interval_norm = ref_interval.map(normalize_portfolio_interval)
       part = ref.loc[ref_interval_norm == normalize_portfolio_interval(itv)].copy()
       ...
       if not part.empty:
           htf_ref_by_interval[normalize_portfolio_interval(itv)] = part
   ```
   - `htf_reference` 默认 = 当次 `prediction_df`，**只有 day interval 的行**
   - 60min 切出来 `part.empty=True` → 不写入 `htf_ref_by_interval["60min"]`
5. 进入 `HtfGate.compute_htf_state` [interval_gate.py:71](../portfolio_logic/interval_gate.py)：
   - `latest_by_interval["60min"] = pd.DataFrame()`（空）
   - 循环每个 key 时，[interval_gate.py:121-127](../portfolio_logic/interval_gate.py)：
     ```python
     dfi = latest_by_interval.get(interval_key, pd.DataFrame())
     if dfi.empty:
         missing_interval = True
         continue
     ```
   - [interval_gate.py:131](../portfolio_logic/interval_gate.py)：`if self.cfg.require_consensus and missing_interval: continue`
   - 结果：`result = {}`（空 dict）
6. 进入 `HtfGate.filter` [interval_gate.py:199-213](../portfolio_logic/interval_gate.py)：
   ```python
   entry = htf_state.get(key)  # None
   if entry is None or not self.is_state_fresh(...):
       if self.cfg.fallback_when_htf_missing == "both":
           allowed_list.append(True); reason_list.append("")
       else:
           allowed_list.append(False); reason_list.append("htf_missing")
   ```
   - `fallback_when_htf_missing="skip"` 是 [config.py:75](../portfolio_logic/config.py) 默认值
   - → 每一笔都 emit `"htf_missing"`
7. 拷回 `selected.block_reason` [pipeline_oot_evaluation.py:933-935](../model/pipeline_oot_evaluation.py)。

### 4.2 根因总结

**单 interval 跑批 + 默认 HTF 配置 = 100% `htf_missing`**。是**配置/默认值不匹配单 interval 用法**，不是数据缺陷。

同样的 bug 会出现在所有"单 interval"的 group-pool 跑批：`*_day`、`*_60min`、`*_30min`、`*_15min`、`*_5min`、`*_minute`。METAL 只是首例。

### 4.3 诊断命令

```bash
# 查 details 里 block_reason 分布
python -c "
import pandas as pd
df = pd.read_csv('20260517_GRP_CLUSTER_METAL_day/details.csv', encoding='utf-8-sig')
print(df['block_reason'].value_counts(dropna=False))
print('htf_missing 占比:', (df['block_reason'] == 'htf_missing').mean())
"

# 查这次 run 的 htf_reference 有哪些 interval
python -c "
import pandas as pd
df = pd.read_csv('20260517_GRP_CLUSTER_METAL_day/predictions.csv', encoding='utf-8-sig')
print(df['interval'].value_counts())
"
```

预期看到的现象：
- 第 1 个命令：`htf_missing` 占比 > 90%
- 第 2 个命令：只有 `day` 一种 interval

---

## §5 htf_missing 修复 Fix-A：HTF intervals 运行时自适配（**根因修复**）

### 5.1 改动位置

文件 [cta/model/pipeline_oot_evaluation.py](../model/pipeline_oot_evaluation.py)，函数 `_evaluate_oot_real_execution`，行号区间 551-571。

### 5.2 改动 diff

**旧代码**（保留 `htf_state_cache` / `htf_gate` 变量初始化不变）：

```python
htf_gate: HtfGate | None = None
htf_ref_by_interval: dict[str, pd.DataFrame] = {}
htf_state_cache: dict[pd.Timestamp, dict[tuple[str, str], dict[str, Any]]] = {}
if use_pl_htf:
    htf_gate = HtfGate(pl_cfg.interval_gate)
    ref = htf_reference.copy()
    def _to_dt_per_interval(series: pd.Series) -> pd.Series:
        try:
            return pd.to_datetime(series, errors="coerce", format="mixed")
        except TypeError:
            return pd.to_datetime(series, errors="coerce")
    for itv in pl_cfg.interval_gate.htf_intervals:
        ref_interval = ref.get("interval", pd.Series([""] * len(ref), index=ref.index))
        ref_interval_norm = ref_interval.map(normalize_portfolio_interval)
        part = ref.loc[ref_interval_norm == normalize_portfolio_interval(itv)].copy()
        if "datetime" in part.columns:
            part["datetime"] = _to_dt_per_interval(part["datetime"])
        if not part.empty:
            htf_ref_by_interval[normalize_portfolio_interval(itv)] = part
```

**新代码**：

```python
import dataclasses  # 确认文件顶部已有 import，没有则加

htf_gate: HtfGate | None = None
htf_ref_by_interval: dict[str, pd.DataFrame] = {}
htf_state_cache: dict[pd.Timestamp, dict[tuple[str, str], dict[str, Any]]] = {}
if use_pl_htf:
    ref = htf_reference.copy()
    def _to_dt_per_interval(series: pd.Series) -> pd.Series:
        try:
            return pd.to_datetime(series, errors="coerce", format="mixed")
        except TypeError:
            return pd.to_datetime(series, errors="coerce")
    ref_intervals_seen: list[str] = []
    for itv in pl_cfg.interval_gate.htf_intervals:
        ref_interval = ref.get("interval", pd.Series([""] * len(ref), index=ref.index))
        ref_interval_norm = ref_interval.map(normalize_portfolio_interval)
        part = ref.loc[ref_interval_norm == normalize_portfolio_interval(itv)].copy()
        if "datetime" in part.columns:
            part["datetime"] = _to_dt_per_interval(part["datetime"])
        if not part.empty:
            key = normalize_portfolio_interval(itv)
            htf_ref_by_interval[key] = part
            ref_intervals_seen.append(key)

    if not ref_intervals_seen:
        # 完全没 HTF 数据 → 关闭 HTF gate，避免 100% htf_missing
        logger.warning(
            "HTF gate enabled but htf_reference contains no rows for any of %s; "
            "disabling HTF gate for this run (see cta/docs/block_reason.md §4)",
            pl_cfg.interval_gate.htf_intervals,
        )
        use_pl_htf = False
        htf_gate = None
    else:
        # 窄化 IntervalGateConfig：只保留实际有数据的 interval
        configured = tuple(
            normalize_portfolio_interval(i) for i in pl_cfg.interval_gate.htf_intervals
        )
        if set(ref_intervals_seen) != set(configured):
            narrowed_cfg = dataclasses.replace(
                pl_cfg.interval_gate,
                htf_intervals=tuple(ref_intervals_seen),
            )
            htf_gate = HtfGate(narrowed_cfg)
            logger.info(
                "HTF intervals narrowed from %s to %s (htf_reference only contains these)",
                configured, tuple(ref_intervals_seen),
            )
        else:
            htf_gate = HtfGate(pl_cfg.interval_gate)
```

### 5.3 关键注意点

1. **必须用 `dataclasses.replace`**：`IntervalGateConfig` 是 `@dataclass(frozen=True)`（[config.py:69](../portfolio_logic/config.py)）。直接 `pl_cfg.interval_gate.htf_intervals = (...)` 会抛 `FrozenInstanceError`。
2. **`HtfGate` 实例化时序调整**：原代码先实例化再切分；新代码改成切分后再决定是否实例化（用窄化 cfg）。
3. **保留 `htf_gate = None` 路径**：当 `ref_intervals_seen` 为空时关闭 gate，后续 `if use_pl_htf and htf_gate is not None` 守卫已存在（[pipeline_oot_evaluation.py:911](../model/pipeline_oot_evaluation.py)），不需要再加守卫。
4. **不要降级为 fallback="both"**：那会跨过严格语义；用户已经主动配 `require_consensus=True` 应当尊重。Fix-A 的语义是"按实际数据可用 interval 应用共识"，比 fallback 更精准。

### 5.4 配套测试（必加）

`cta/portfolio_logic/tests/test_interval_gate.py`（如不存在则创建）：

```python
"""HTF gate 行为测试。"""
from __future__ import annotations
import pandas as pd
import pytest
from cta.portfolio_logic.config import IntervalGateConfig
from cta.portfolio_logic.interval_gate import HtfGate


class TestHtfGate:
    def test_filter_returns_htf_missing_when_state_empty(self):
        """sanity check: 空 htf_state 必返回 htf_missing。"""
        gate = HtfGate(IntervalGateConfig())
        opps = pd.DataFrame([{
            "symbol": "RB0", "exchange": "SHFE",
            "direction": "long", "interval": "day",
        }])
        out = gate.filter(opps, htf_state={}, current_time=pd.Timestamp("2026-05-15"))
        assert list(out["htf_allowed"]) == [False]
        assert list(out["htf_block_reason"]) == ["htf_missing"]

    def test_filter_allows_when_fallback_both(self):
        cfg = IntervalGateConfig(fallback_when_htf_missing="both")
        gate = HtfGate(cfg)
        opps = pd.DataFrame([{
            "symbol": "RB0", "exchange": "SHFE",
            "direction": "long", "interval": "day",
        }])
        out = gate.filter(opps, htf_state={}, current_time=pd.Timestamp("2026-05-15"))
        assert list(out["htf_allowed"]) == [True]
        assert list(out["htf_block_reason"]) == [""]
```

---

## §6 htf_missing 修复 Fix-B：`fallback_when_htf_missing` 配置可见

### 6.1 默认值保持不变

`IntervalGateConfig.fallback_when_htf_missing` 默认 `"skip"`（严格语义），**不改**。Fix-A 已能解决全 miss 场景；fallback 是给"部分 symbol 部分 interval miss"的边缘 case 留的逃生口。

### 6.2 在 OOT eval config 加注释

文件 [cta/config/model_oot_eval_config.py](../config/model_oot_eval_config.py)，在 `portfolio_logic.interval_gate` 配置块附近加：

```python
# interval_gate.fallback_when_htf_missing:
#   "skip"（默认，严格）：缺 HTF 共识即拒单，emit block_reason=htf_missing。
#   "both"             ：缺 HTF 时中性放行（视为允许 long 与 short）。
#
# 单 interval 跑批（如 --interval day）通常不需要手动改这里 —— 见
# cta/model/pipeline_oot_evaluation.py 的 Fix-A，会自动把
# htf_intervals 窄化到实际可用的 interval 子集。
# 仅当某个 interval 数据**应该有但偶发缺失**时，才考虑改成 "both"。
```

### 6.3 单 interval 跑批 checklist（写在文档里供用户参考）

| 场景 | 推荐配置 |
|---|---|
| `--interval day` 单 interval | 不需改，依赖 Fix-A 自动窄化 |
| `--interval 60min` 单 interval | 不需改，依赖 Fix-A 自动窄化 |
| 多 interval 混合，HTF 数据应当齐全 | `fallback_when_htf_missing="skip"`（默认） |
| 多 interval 混合，HTF 数据**偶发**缺失（如 30min 模型还在训练） | `fallback_when_htf_missing="both"` |
| 关闭 HTF gate | `portfolio_logic.enable_htf_gate=False` |

---

## §7 htf_missing 修复 Fix-C：诊断输出

### 7.1 改动位置

文件 [cta/model/pipeline_oot_evaluation.py](../model/pipeline_oot_evaluation.py)，在 `_evaluate_oot_real_execution` 返回结果前（找 `trade_details_df` 已构造好的位置，grep `trade_details_df` 找到 return 处）。

### 7.2 改动代码

```python
# 在 return 前加这段
if not trade_details_df.empty and "block_reason" in trade_details_df.columns:
    reason_counts = (
        trade_details_df["block_reason"]
        .fillna("")
        .replace("", "__executed__")
        .value_counts(dropna=False)
        .to_dict()
    )
    total = int(len(trade_details_df))
    logger.info("OOT block_reason distribution [path=main] (total=%d): %s", total, reason_counts)
    htf_miss = int(reason_counts.get("htf_missing", 0))
    if htf_miss > total * 0.5:
        logger.warning(
            "More than 50%% of candidates blocked by htf_missing (%d/%d). "
            "Check htf_intervals vs available data (see cta/docs/block_reason.md §4-§6).",
            htf_miss, total,
        )
```

说明：
- `__executed__` 只是 logger 输出专用 sentinel，表示“该行实际成交、block_reason 为空”。
- 它**不属于** canonical block_reason，不在 `CANONICAL_BLOCK_REASONS` 集合中。

### 7.3 验收

跑任意 OOT eval 都能在 logger 看到类似：

```
INFO  OOT block_reason distribution [path=main] (total=842): {'__executed__': 612, 'blocked_symbol_cap': 89, 'htf_missing': 75, 'ranker_dropped': 66}
```

如果 `htf_missing > 50%` 还能看到 warning：

```
WARNING  More than 50% of candidates blocked by htf_missing (820/842). Check htf_intervals vs available data (see cta/docs/block_reason.md §4-§6).
```

---

## §7.5 htf_missing 修复 Fix-E：按 interval_rank 过滤 HTF（**跨 interval 共享 HTF 场景**）

### 7.5.1 漏掉的场景

Fix-A 解决了"单 interval 跑批无 60min 数据"的情形：候选 interval 在 `htf_reference` 里只有 day → 自动窄化到 `("day",)`。

但还有一个**对偶**漏洞：当 `_recompute_oot_with_shared_htf_reference` 把 day/60min/30min 三个 interval 预测拼成共享 HTF 参考时，**day 候选**会同时拿到 day 和 60min 两条参考，Fix-A 不会窄化（两个 interval 都有数据）。然后：

- `HtfGate.compute_htf_state` 给 day 候选构建 state，state 含 `computed_at_by_interval = {"day": ts_day, "60min": ts_60min}`
- `is_state_fresh` 按每个 interval 的 ttl 检查：60min ttl 默认 3600s（1 小时）
- day 候选 `as_of` 通常在 day 开盘或收盘时刻，最近一根 60min bar 距离 5-20 小时（隔夜、周末更长）→ **必然过期** → `is_state_fresh = False`
- → 整体 state 视作缺失 → emit `htf_missing`

**结果**：跨 interval 共享 HTF 后 day 目录里 100% `htf_missing`。复现案例：[20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/symbol_group_details/grp_cluster_index_day/](../report/backtest/20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/symbol_group_details/grp_cluster_index_day/)。

### 7.5.2 根因：HTF 语义错配

`htf_intervals=("day", "60min")` 的设计意图是给 **sub-hourly 候选**（5min/15min/30min/60min）查的"更高时间框架"。

对于 **day 候选**，60min 是**更低**的时间框架，根本不应该被当作"HTF 共识来源"。`IntervalGateConfig.interval_rank` 已经给出语义：day=1.0、60min=0.85、30min=0.70、15min=0.55、5min=0.40、min=0.25。HTF 按定义必须 rank ≥ 候选 interval rank。

### 7.5.3 改动位置

文件 [cta/model/pipeline_oot_evaluation.py](../model/pipeline_oot_evaluation.py)，函数 `_evaluate_oot_real_execution`，Fix-A 之后、`configured_intervals` 计算之前。

### 7.5.4 改动代码

```python
# Fix-E（语义修正，2026-05-18）：HTF 按定义是"更高时间框架"，必须不低于候选自身。
interval_rank = dict(pl_cfg.interval_gate.interval_rank or {})
pred_intervals_norm: set[str] = set()
if "interval" in df.columns:
    pred_intervals_norm = {
        normalize_portfolio_interval(v)
        for v in df["interval"].dropna().astype(str).unique()
        if str(v).strip()
    }
if len(pred_intervals_norm) == 1 and ref_intervals_seen:
    pred_interval = next(iter(pred_intervals_norm))
    pred_rank = float(interval_rank.get(pred_interval, 0.0))
    semantic_intervals = [
        k for k in ref_intervals_seen
        if float(interval_rank.get(k, 0.0)) >= pred_rank
    ]
    if set(semantic_intervals) != set(ref_intervals_seen):
        dropped = sorted(set(ref_intervals_seen) - set(semantic_intervals))
        logger.info(
            "HTF intervals filtered by rank for prediction interval=%s (rank=%.2f): "
            "kept=%s dropped=%s (HTF must be >= candidate interval rank, see Fix-E).",
            pred_interval, pred_rank, semantic_intervals, dropped,
        )
        for k in dropped:
            htf_ref_by_interval.pop(k, None)
        ref_intervals_seen = semantic_intervals
```

### 7.5.5 行为对照表

| 候选 interval | 候选 rank | 共享 HTF 含 | Fix-E 保留 | 实际生效 HTF |
|---|---|---|---|---|
| day | 1.0 | day + 60min | 仅 day | day（不再被 60min TTL 误杀）|
| 60min | 0.85 | day + 60min | day + 60min | 与默认一致 |
| 30min | 0.70 | day + 60min | day + 60min | 与默认一致 |
| 5min | 0.40 | day + 60min | day + 60min | 与默认一致 |

Fix-E **不影响** sub-hourly 候选的默认行为；只剪掉"对 day 候选无意义、还会 TTL 误杀"的低 rank HTF。

### 7.5.6 验收

```bash
# 1. 单测
pytest cta/model/tests/test_pipeline_oot_evaluation.py::test_fix_e_day_candidate_drops_lower_rank_htf_when_shared_reference -v
pytest cta/model/tests/test_pipeline_oot_evaluation.py::test_fix_e_minute_candidate_keeps_day_and_60min_htf -v

# 2. 端到端复现
python -m cta.model.model_pipeline --group-pool --group-by cluster \
    --only-clusters index --interval day 60min 30min \
    --start 2024-01-01 --end 2025-12-31 \
    --use-portfolio-logic-runtime

# 3. 检查 day 子目录 block_reason 分布
python -c "
import pandas as pd
df = pd.read_csv('<run>/symbol_group_details/grp_cluster_index_day/<...>_oot_trade_details.csv', encoding='utf-8-sig')
print(df['block_reason'].value_counts(dropna=False))
assert (df['block_reason']=='htf_missing').mean() < 0.05
"
```

期望：
- log 含 `HTF intervals filtered by rank for prediction interval=day (rank=1.00): kept=['day'] dropped=['60min']`
- day OOT trade_details 的 `htf_missing` 比例 < 5%（理想 0）

---

## §8 htf_conflict / htf_opposite / htf_unknown

### 8.1 `htf_conflict`（HTF 共识冲突）

**触发**：`HtfGate._state_from_regimes` 在某些 HTF interval 上的 regime label 互相冲突 → 返回 `"none"` → [interval_gate.py:222-225](../portfolio_logic/interval_gate.py) emit `htf_conflict`。

**何时是正常**：模型告诉你 day 看多但 60min 看空，方向矛盾，正确做法是不开仓。

**何时是 bug**：
- regime classifier 输出有 garbage label（如训练数据污染）。验证：`SELECT DISTINCT pred_regime_label FROM predictions WHERE interval='day'`，应只有 `{trend_up, trend_down, range}` 几种合法 label。
- 模型在某 interval 完全失效（AUC ~ 0.5），随机输出 → 导致与另一 interval 不断冲突。

**修复**：
- 数据修复：清洗训练数据，确保 regime label 在合法集合内（[interval_gate.py:13-15](../portfolio_logic/interval_gate.py) 的 `_UP_REGIMES / _DOWN_REGIMES / _RANGE_REGIMES`）。
- 配置修复：若模型确实信号弱，临时 `IntervalGateConfig(require_consensus=False)` 用并集语义。

### 8.2 `htf_opposite`（HTF 方向反对）

**触发**：HTF state 是 `long_only` 但 candidate 方向是 `short`（或对称情况）。

**判定**：**不是 bug，是设计**。模型告诉你这个方向不该交易。

**例外**：如果 details 中 `htf_opposite` 比例 > 30%，说明候选生成（baseline strategy）跟 regime classifier 完全不对齐。这可能是：
- candidate gen 没用 regime 做过滤 — 应当检查 [baseline_setup_detection.py](../strategy/baseline_setup_detection.py) 是否在 regime aware 模式下工作。
- regime model 出现 label leakage — 见 review 文档 §C1 / §H2。

**不修复策略**，但要在 docs 里强调"高 htf_opposite 比例 = 上游候选/regime 模型不协调，应当回到模型调参"。

### 8.3 `htf_unknown`（**bug 兜底**）

**触发**：[interval_gate.py:240-243](../portfolio_logic/interval_gate.py) — `state` 是 `_state_from_regimes` 返回值，但不在 `{both, none, long_only, short_only}` 中。

**为何是 bug**：`_state_from_regimes` 本应保证返回值在那 4 个里面（[interval_gate.py:53-69](../portfolio_logic/interval_gate.py)）。如果走到 `htf_unknown` 说明 `_state_from_regimes` 实现有遗漏或上游数据破坏了不变式。

**修复**：

[interval_gate.py:240](../portfolio_logic/interval_gate.py) 增强 warning：

```python
# 旧
logger.warning("Unknown HTF state=%s for %s", state, key)

# 新
logger.warning(
    "Unknown HTF state=%r for %s (entry=%r); falling back to block (htf_unknown). "
    "This indicates a bug in HtfGate._state_from_regimes — please file an issue.",
    state, key, dict(entry) if entry else None,
)
```

**长期方案**：在 `_state_from_regimes` 末尾把 `return "none"` 改成显式 enum/Literal，并加单元测试覆盖。

---

## §9 invalid_time

### 9.1 触发位置

[pipeline_oot_evaluation.py:782-790](../model/pipeline_oot_evaluation.py)（grep `invalid_time` 确认）：

```python
exi = pd.to_datetime(selected["exit_datetime"], errors="coerce")
ent = pd.to_datetime(selected["entry_datetime"], errors="coerce")
mask_bad = (exi < ent) | exi.isna() | ent.isna()
selected.loc[mask_bad, "execution_status"] = "invalid_time"
selected.loc[mask_bad, "block_reason"] = "invalid_time"
```

### 9.2 根因

上游候选生成器塞了 `exit < entry` 或 NaT 时间戳。

**最常见来源**：
- [baseline_setup_detection.py](../strategy/baseline_setup_detection.py) 在 horizon 内未找到出场点时把 `exit_datetime` 留作 NaT
- 行情数据停盘期间 entry bar 之后没有可成交 bar

### 9.3 修复路径

1. 在 `_resolve_candidate_entry`（[baseline_setup_detection.py](../strategy/baseline_setup_detection.py)）出口加 invariant：

```python
if pd.notna(entry_ts) and pd.notna(exit_ts):
    assert entry_ts < exit_ts, f"entry_ts >= exit_ts at row idx={idx}: {entry_ts} >= {exit_ts}"
```

2. 若是 NaT，在候选阶段就标记 `sample_status="exit_horizon_exceeded"`，不要丢给 OOT。

### 9.4 验收

```bash
pytest cta/strategy/tests/test_baseline_candidate_gen.py -k "invalid_time or exit_horizon" -v
```

`details.csv` 里 `invalid_time` 计数应为 0。

---

## §10 blocked_throttle_halt + RiskThrottle 阈值

### 10.1 触发位置

[pipeline_oot_evaluation.py:902-909](../model/pipeline_oot_evaluation.py)：当 `RiskThrottle.compute()` 返回 level.name=`"halt"` 时，**清空本 bar 的入场队列**。

### 10.2 不是 bug，是设计

`RiskThrottle` 在以下三种情况会 halt：
- 当日 drawdown 超阈值（`halt_drawdown_pct`，默认未知，查 [config.py:RiskThrottleConfig](../portfolio_logic/config.py)）
- 周收益超下限 `halt_weekly_return_pct`
- 月收益超下限 `halt_monthly_return_pct`

### 10.3 调参 checklist

1. 看 throttle log（`throttle_log.csv`）里 halt 阶段的 `drawdown_pct / weekly_return_pct / monthly_return_pct`
2. 若 halt 频繁但回撤实际并不大，检查 `EquityTracker.snapshot()` 周/月窗口是否正确滚动（review §H1 指出过窗口不重置 bug）
3. 配置入口：[cta/portfolio_logic/config.py:RiskThrottleConfig](../portfolio_logic/config.py)

### 10.4 验收

```bash
# 看 halt 频次
grep "throttle_level=halt" /path/to/run/*.log | wc -l

# 看 halt 时段
python -c "
import pandas as pd
df = pd.read_csv('throttle_log.csv', encoding='utf-8-sig')
print(df[df['level']=='halt'].describe())
"
```

---

## §11 ranker_dropped + OpportunityRanker

### 11.1 触发位置

[pipeline_oot_evaluation.py:943-1014](../model/pipeline_oot_evaluation.py)：`OpportunityRanker.score(...)` 评分后，未入选的 idx 被标 `execution_status="blocked_ranker"`、`block_reason="ranker_dropped"`。

### 11.2 调参

配置入口：[cta/portfolio_logic/config.py:OpportunityRankerConfig](../portfolio_logic/config.py)。

关键字段：
- `score_pctl_threshold`（默认 50.0）：低于此分位的候选丢弃
- `weights`：score 的特征加权（trade_filter_prob、pred_mfe_atr、htf_alignment 等）
- `max_selected_per_bar`：每根 bar 最多入选数

### 11.3 诊断

```python
# 看 details 里 ranker_score 分布
import pandas as pd
df = pd.read_csv('details.csv', encoding='utf-8-sig')
print(df['ranker_score'].describe())
print(df.loc[df['block_reason']=='ranker_dropped', 'ranker_score'].describe())
```

若 >70% 的候选 ranker_score 落在阈值以下，说明 `score_pctl_threshold` 过严或 `weights` 不合理。

---

## §12 blocked_limit_move + 涨跌停数据来源

### 12.1 触发位置

[pipeline_oot_evaluation.py:1018-1030](../model/pipeline_oot_evaluation.py)：

```python
if (side_now == "long" and limit_up_arr[idx]) or (side_now == "short" and limit_down_arr[idx]):
    selected.at[idx, "execution_status"] = "blocked_limit_move"
    selected.at[idx, "block_reason"] = "blocked_limit_move"
```

### 12.2 数据来源排查

grep `limit_up_arr` / `limit_down_arr` 找 array 构造源。通常来自行情 bar 的 `limit_up_price / limit_down_price` 与 `close` 比较。

排查 checklist：
- 行情数据是否包含涨跌停标记列？
- 标记列的语义是 "close 触及涨停" 还是 "bar high 触及涨停"？
- 是否所有合约都有标记（部分外盘可能没有）？

### 12.3 验收

选一个涨跌停频繁的品种（如 IM0、远月铁矿）人工核对：

```python
import pandas as pd
df = pd.read_csv('details.csv', encoding='utf-8-sig')
limit_blocked = df[df['block_reason']=='blocked_limit_move']
print(limit_blocked.groupby('symbol').size().sort_values(ascending=False).head(20))
```

---

## §13 blocked_pyramid_rule + PyramidManager 三条件

### 13.1 触发位置

[pipeline_oot_evaluation.py:1092-1116](../model/pipeline_oot_evaluation.py)：

```python
can_add = pyramid_manager.decide_add_layer(
    pos=current_pos_obj,
    new_interval=...,
    current_time=...,
    current_price=ent_for_add,
    min_profit_atr_to_add=float(pl_cfg.pyramid.min_profit_atr_to_add),
    htf_aligned=True,
)
if not can_add:
    selected.at[idx, "execution_status"] = "blocked_pyramid_rule"
    selected.at[idx, "block_reason"] = "blocked_pyramid_rule"
```

### 13.2 三条件（见 [cta/portfolio_logic/pyramid.py](../portfolio_logic/pyramid.py)）

1. 当前 layer 数 < `max_layers`
2. 距上次加仓时间 > `cooldown_seconds`
3. 价格变动满足 `min_profit_atr_to_add`（按 ATR 单位计量浮盈幅度）

### 13.3 不允许 codex 改默认值

只在文档里说明配置入口：[cta/portfolio_logic/config.py:PyramidConfig](../portfolio_logic/config.py)。

---

## §14 blocked_monthly_drawdown / blocked_weekly_drawdown

### 14.1 触发位置

[pipeline_oot_evaluation.py:1119-1125](../model/pipeline_oot_evaluation.py)：

```python
if bool(cfg.use_portfolio_constraints):
    if bool(cfg.block_new_entries_on_monthly_dd_breach) and month_dd_breached:
        notional = 0.0
        reason = "blocked_monthly_drawdown"
    elif bool(cfg.block_new_entries_on_weekly_dd_breach) and week_dd_breached:
        notional = 0.0
        reason = "blocked_weekly_drawdown"
```

### 14.2 风控正常工作，但放大效应警告

**关联**：[review 文档 §H1](review/202605170735.md) 指出 weekly/monthly peak 在跨周/月时**未重置**。这导致：
- 上一周的 peak 残留到本周，导致本周回撤计算虚高
- 本应可以重置的 peak 没重置 → 触发频率被放大

**修复**：见 review §H1（peak 重置 bug 修复）。本 reason 本身不改，但 codex 必须确认 review §H1 已修。

### 14.3 配置入口

- [cta/config/model_oot_eval_config.py](../config/model_oot_eval_config.py):
  - `weekly_max_drawdown_pct`（默认 3%）
  - `monthly_max_drawdown_pct`（默认 8%）
  - `block_new_entries_on_weekly_dd_breach`（默认 True）
  - `block_new_entries_on_monthly_dd_breach`（默认 True）

---

## §15 blocked_total_concurrent / blocked_symbol_concurrent

### 15.1 触发位置

[pipeline_oot_evaluation.py:1126-1131](../model/pipeline_oot_evaluation.py)：

```python
elif total_count_now >= int(cfg.max_concurrent_positions_total):
    notional = 0.0
    reason = "blocked_total_concurrent"
elif (not use_pl_pyramid) and sym_key and sym_count_now >= int(cfg.max_concurrent_positions_per_symbol):
    notional = 0.0
    reason = "blocked_symbol_concurrent"
```

### 15.2 配置入口

- `max_concurrent_positions_total`：组合总持仓上限（默认看 [model_oot_eval_config.py](../config/model_oot_eval_config.py)）
- `max_concurrent_positions_per_symbol`：单品种持仓上限
- 当 `use_pl_pyramid=True` 时跳过 per-symbol 限制（加仓由 pyramid manager 接管）

### 15.3 典型触发场景

- 组合并行候选过多（如 multi-cluster 跑批） → 调高 `max_concurrent_positions_total`
- 加仓未启用但单品种信号密集 → 启用 pyramid 或调高 per-symbol

---

## §16 blocked_symbol_cap + cluster cap

### 16.1 触发位置

[pipeline_oot_evaluation.py:1132-1154](../model/pipeline_oot_evaluation.py)：

```python
cap_symbol = max(0.0, float(entry_equity * float(pl_cfg.caps.max_symbol_notional_pct) - sym_notional_now))
cap_cluster = max(0.0, float(entry_equity * float(pl_cfg.caps.max_cluster_notional_pct) - cluster_notional_now))
...
if notional <= 0:
    if cap_symbol <= 0:
        reason = "blocked_symbol_cap"
    elif cap_cluster <= 0:
        reason = "blocked_cluster_cap"
```

### 16.2 公式

- `cap_symbol = entry_equity × max_symbol_notional_pct - sym_notional_now`
- `cap_cluster = entry_equity × max_cluster_notional_pct - cluster_notional_now`

### 16.3 配置入口

[cta/portfolio_logic/config.py:CapsConfig](../portfolio_logic/config.py)：
- `max_symbol_notional_pct`（默认 0.30）
- `max_cluster_notional_pct`（默认 0.50）

### 16.4 当前实现说明

cluster cap 现在已经拆分为独立 reason `blocked_cluster_cap`，因此可以在 details 里直接区分：
- `blocked_symbol_cap`：单品种 notional cap 触顶
- `blocked_cluster_cap`：cluster 维度 notional cap 触顶

---

## §17 blocked_daily_position / blocked_weekly_budget

### 17.1 触发位置

[pipeline_oot_evaluation.py:1141-1158](../model/pipeline_oot_evaluation.py)：

```python
cap_daily = max(0.0, float(day_start_equity * float(cfg.max_daily_new_notional_pct) - day_new_notional))
cap_week = float("inf")
if bool(cfg.enforce_weekly_dd_budget_on_entry) and float(cfg.weekly_max_drawdown_pct) > 0:
    used_dd_amt = max(0.0, float(week_peak_equity - entry_equity))
    remain_dd_amt = max(0.0, float(week_peak_equity * float(cfg.weekly_max_drawdown_pct) - used_dd_amt))
    if float(cfg.max_single_loss_pct) > 0:
        cap_week = float(remain_dd_amt / float(cfg.max_single_loss_pct))
    else:
        cap_week = 0.0
...
elif cap_week <= 0:
    reason = "blocked_weekly_budget"
elif cap_daily <= 0:
    reason = "blocked_daily_position"
```

### 17.2 公式

- `cap_daily` = 当日剩余可开仓 notional：`day_start_equity × max_daily_new_notional_pct - day_new_notional`
- `cap_week` = 周回撤预算还能承担多少笔最大单笔亏损：`remain_dd_amt / max_single_loss_pct`

### 17.3 配置入口

- `max_daily_new_notional_pct`（默认 1.0）
- `weekly_max_drawdown_pct`（默认 0.03）
- `max_single_loss_pct`（默认 0.005-0.01 量级）
- `enforce_weekly_dd_budget_on_entry`（默认 True）

---

## §18 blocked_margin_cash / blocked_leverage

### 18.1 触发位置

[pipeline_oot_evaluation.py:1135, 1159-1162](../model/pipeline_oot_evaluation.py)：

```python
cap_lev = max(0.0, float(entry_equity * float(cfg.max_total_leverage) - open_notional))
cap_cash = max(0.0, float(avail_cash / margin_rate))
...
elif cap_cash <= 0:
    reason = "blocked_margin_cash"
elif cap_lev <= 0:
    reason = "blocked_leverage"
```

### 18.2 公式

- `cap_cash = avail_cash / margin_rate`：可开 notional 上限 = 现金 / 保证金率
- `cap_lev = entry_equity × max_total_leverage - open_notional`：杠杆剩余空间

### 18.3 配置入口

- `max_total_leverage`（默认 2.0）
- `margin_rate`（每个 symbol 自带，从合约信息读取）

### 18.4 典型场景

- `blocked_margin_cash` 触发频繁 → 初始资金太小，或保证金率高的品种比例高
- `blocked_leverage` 触发频繁 → 组合并发持仓太多，需要调 `max_concurrent_positions_total` 或调高杠杆上限

---

## §19 blocked_portfolio_constraint / zero_notional（**兜底 bug**）

### 19.1 `blocked_portfolio_constraint` — 兜底，理论上不应出现

[pipeline_oot_evaluation.py:1163-1164](../model/pipeline_oot_evaluation.py)：

```python
else:
    reason = "blocked_portfolio_constraint"
```

这条触发的语义：notional ≤ 0 但上面六个 cap 都 > 0。**这是逻辑 bug**——只要 cap 都正常，notional 应当 > 0。

### 19.2 修复

加 logger.error 定位现场：

```python
else:
    reason = "blocked_portfolio_constraint"
    logger.error(
        "blocked_portfolio_constraint hit unexpectedly at idx=%d: notional=%.6f "
        "cap_daily=%.6f cap_lev=%.6f cap_cash=%.6f cap_week=%.6f cap_symbol=%.6f cap_cluster=%.6f",
        idx, notional, cap_daily, cap_lev, cap_cash, cap_week, cap_symbol, cap_cluster,
    )
```

### 19.3 `zero_notional`

[pipeline_oot_evaluation.py:1166-1168](../model/pipeline_oot_evaluation.py)：

```python
if notional <= 0:
    selected.at[idx, "execution_status"] = reason or "blocked_zero_notional"
    selected.at[idx, "block_reason"] = reason or "zero_notional"
```

当 `reason` 是空字符串时（即 `use_portfolio_constraints=False` 路径），fallback 到 `zero_notional`。这通常发生在：
- `pred_mae_atr ≤ 0`（模型预测负 MAE，关联 review §M2）
- `position_scale ≤ 0`

### 19.4 验收

跑全量回测：

```bash
grep "blocked_portfolio_constraint hit" /path/to/*.log
```

应当**空输出**。如果有任何 hit，按 logger.error 输出的 cap 数值定位逻辑漏洞。

---

## §20 候选期 5 种 reason（filtered_by_rule 等）

这 5 种由 [candidate_schema.py:32-43](../model/feature/candidate_schema.py) 兜底映射。**不在 OOT 执行期产生**，而是候选样本生成时由 baseline strategy / risk filter 标记。

### 20.1 sample_status → block_reason 映射表

| sample_status | block_reason | 来源 |
|---|---|---|
| `filtered_by_rule` | `filtered_by_rule` | baseline strategy 内部 quality gate 拒绝 |
| `blocked_by_risk` | `risk_rule_blocked` | 风险规则拒绝（如 max_loss > 阈值） |
| `blocked_by_capacity` | `capacity_blocked` | 候选生成时的 capacity gate |
| `blocked_by_execution` | `execution_rule_blocked` | 执行规则拒绝（如挂单未成交） |
| `not_triggered_market` | `next_bar_not_triggered` | 下一根 bar 价格未触发入场 |

### 20.2 对训练集 P/N 比的影响

这些样本进入训练集作为**反例**（label=0），影响 trade_filter / regime_classifier / mfe_mae 模型的样本均衡：

- `filtered_by_rule` 太多 → 规则过严，模型学不到正例足够多样
- `not_triggered_market` 太多 → 入场价格机制有问题（如 next_bar 偏离过远）

### 20.3 不需要修复

这些是**历史回放**，不在 live trading 路径上。但 codex 应该在 docs 里强调：**candidate quality / capacity / execution 规则的设计本身**会决定训练集质量，需要业务负责人决定，不在本次 fix 范围。

---

## §21 验收清单（codex 自查）

### 21.1 文档完成度

- [ ] `cta/docs/block_reason.md` 存在
- [ ] §1-§21 共 21 个章节齐全
- [ ] 26 个 reason 全部出现（含 §2 的 `blocked_zero_notional` 区分说明）
- [ ] 每条 reason 都给出 file:line 引用（markdown 链接格式）
- [ ] §5/§6/§7 三处 Fix-A/B/C 含完整代码 diff

### 21.2 代码改动

- [ ] [pipeline_oot_evaluation.py:551-571](../model/pipeline_oot_evaluation.py) 的 Fix-A 已落地，`dataclasses.replace` 用法正确（无 `FrozenInstanceError` 风险）
- [ ] [pipeline_oot_evaluation.py](../model/pipeline_oot_evaluation.py) 的 Fix-C 已落地，`logger.info("OOT block_reason distribution: ...")` 出现一次且仅一次
- [ ] [interval_gate.py:240](../portfolio_logic/interval_gate.py) 的 `htf_unknown` 路径 warning 包含 state 字符串和 entry dict
- [ ] [interval_gate.py:1163](../model/pipeline_oot_evaluation.py) 后加了 `blocked_portfolio_constraint` 的 logger.error
- [ ] [model_oot_eval_config.py](../config/model_oot_eval_config.py) 加了 `fallback_when_htf_missing` 注释
- [ ] **未改默认值**：
  - `IntervalGateConfig.fallback_when_htf_missing` 仍 `"skip"`
  - `IntervalGateConfig.require_consensus` 仍 `True`
  - `CapsConfig.*` 数值未改
  - `RiskThrottleConfig.*` 数值未改

### 21.3 测试

- [ ] 新增 `cta/portfolio_logic/tests/test_interval_gate.py`，至少 3 个测试：
  - `test_filter_returns_htf_missing_when_state_empty`
  - `test_filter_allows_when_fallback_both`
  - `test_htf_gate_narrows_when_only_day_data_available`（端到端验 Fix-A）
- [ ] 在 [cta/model/tests/test_pipeline_oot_evaluation.py](../model/tests/test_pipeline_oot_evaluation.py)（或 W9 拆分后对应文件）新增：
  - `test_block_reason_distribution_is_logged_at_info`（验 Fix-C）
  - `test_all_emitted_block_reasons_are_canonical`（用 ast 扫源码，断言字面量 ⊆ 26 个 canonical）
- [ ] `pytest cta/portfolio_logic/tests/test_interval_gate.py -v` 全绿
- [ ] `pytest cta/model/tests/test_pipeline_oot_evaluation.py -v` 全绿
- [ ] `pytest cta/ -x` 全绿，无回归

### 21.4 端到端复现

```bash
# 重新跑 METAL day 案例（最小窗口）
python -m cta.model.model_pipeline \
  --group-pool CLUSTER_METAL --interval day \
  --start 2024-01-01 --end 2025-12-31 \
  --output-root /tmp/block_reason_verify

# 检查 block_reason 分布
python -c "
import pandas as pd, glob
csv = glob.glob('/tmp/block_reason_verify/**/details.csv', recursive=True)[0]
df = pd.read_csv(csv, encoding='utf-8-sig')
counts = df['block_reason'].value_counts(dropna=False)
print(counts)
htf_pct = (df['block_reason'] == 'htf_missing').mean()
print(f'htf_missing 占比: {htf_pct:.2%}')
assert htf_pct < 0.05, f'Fix-A 失败，htf_missing 占比仍 {htf_pct:.2%}'
"
```

- [ ] `htf_missing` 占比 < 5%（理想 0%）
- [ ] log 包含 `OOT block_reason distribution: {...}` INFO 行
- [ ] log 包含 `HTF intervals narrowed from ('day', '60min') to ('day',)` INFO 行
- [ ] **没有** `More than 50% of candidates blocked by htf_missing` warning

### 21.5 不变量

```bash
# 默认值未改
git diff cta/portfolio_logic/config.py | grep -E "^\+.*=.*[0-9]" | grep -v "^\+\s*#"
# 应输出空（不允许任何数值字段被修改）

git diff cta/config/model_oot_eval_config.py | grep -E "^\+.*=.*[0-9]" | grep -v "^\+\s*#"
# 应输出空
```

- [ ] 上面两条 grep 都空输出（只允许注释和新增字段，禁止修改既有默认值）
- [ ] PR diff 总行数：文档 +800~1200 行，代码 +120~200 行，测试 +150~250 行

### 21.6 提交时附录

PR 描述里必须包含：
1. METAL day 复现命令的 stdout 截图（含 htf_missing 比例 < 5%）
2. `pytest cta/ -x` 的最后一行（如 `===== 543 passed in 12.4s =====`）
3. §21.1-§21.5 五个 checkbox 段的逐项打勾结果（不许写 "都做了"，要逐条 `[x]`）

---

> **修复后 follow-up**（不在本次范围）：
> - 拆 `blocked_symbol_cap` 出独立的 `blocked_cluster_cap`（§16.4 改进建议）
> - 把 reason 字面量改成 `Literal` 类型 / Enum，并把 [candidate_schema.py:32-43](../model/feature/candidate_schema.py) 的映射表常量化
> - 加 `block_reason` 维度的 markdown 报表（`oot/block_reason_breakdown.md`）
> - 关联 [review 文档](review/202605170735.md) §H1 修 weekly/monthly peak 重置 bug
