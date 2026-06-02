# eval-only 统一组合评估设计 — 对齐 sim/live（2026-06-01）

> 状态：**设计文档，代码未实现**（本轮仅出方案）。实现见 §5，留待下一轮。
> 决策：统一组合模式设为**默认/唯一**，per-cluster 独立评估弃用。
> 关联：[20260531.md](review/20260531.md) §H2、[sim_plan.md](sim_plan.md)、[sim_live_integration_roadmap2.md](sim_live_integration_roadmap2.md)。

---

## 0. 目标一句话

eval-only 从"**每 cluster 独占 1000 万 + 各自 150% 总额**"改为"**全 cluster / interval / symbol 共享单一
1000 万池子 + 单一 150% 总额，机会在每根 bar 全局竞争**"，与 sim/live 同引擎、同账本、同口径。

这是 CLAUDE.local.md 铁律"离线评估 / 仿真 / 实盘逻辑一致"的直接要求。

---

## 1. 现状：为什么现在不一致

### 1.1 调用链（每 cluster 独立跑一次）

```
eval_only_cli.main
  └─ run_oot_eval_batch                          (eval_only_run.py:570-624)
       └─ _group_train_runs_by_pool              (eval_only_run.py:321-329)
            分组键 = (run_date, pool_name, side)   ← 关键：按 pool 拆开
       └─ for group: rerun_oot_for_train_group   (eval_only_run.py:332-426)
            └─ _evaluate_oot_real_execution(pred_df, cfg)   每组各调一次
       └─ _aggregate_bundle                       (eval_only_run.py:429-558)
```

### 1.2 后果：每 pool 一份独立账本

`_evaluate_oot_real_execution` 内每次调用都新建一份状态（[pipeline_oot_evaluation.py:322](../model/oot/pipeline_oot_evaluation.py)）：

```python
cash = float(cfg.initial_capital)          # 每个 pool 各自 1000 万
runtime_state = PortfolioState(equity=cash)
signal_type_open_counts: dict[str, int] = {}
```

→ 8 个 cluster 各吃 1000 万、各自 150% 总额 → **全局实际 ~8×150% 杠杆**；
`max_total_positions=10` / `max_total_per_cluster=8` 退化成 per-cluster 口径；
**不同 cluster/symbol 的机会互不竞争**，每簇自顾自把额度吃满。

### 1.3 聚合不重算全局约束

`_aggregate_bundle` 只是把各 pool 已算好的 `oot_trade_details.csv` 纯 `pd.concat`
（[eval_only_run.py:486-494](../model/eval_only_run.py)），**不会**回头检查"合并后总并发是否超 10 / 总 notional 是否超 150%"。

### 1.4 已有的"对的一半"

同一 pool 跨 interval 已经合并成一次回放（[eval_only_run.py:359-400](../model/eval_only_run.py)）——
即 **cross-interval 竞争已成立**。统一模式只是把这套"concat→单次回放"机制从"pool 内"放大到"全局"。

---

## 2. sim/live 的全局逻辑（要对齐的目标）

### 2.1 单一全局 PortfolioState
[portfolio_logic/portfolio_state.py:29-154](../portfolio_logic/portfolio_state.py)：
`symbol_counts` / `cluster_counts` / `symbol_notional` / `cluster_notional` / `total_positions` /
`total_open_notional` + tentative/commit 事务（`begin_allocation` / `tentative_apply` /
`commit_allocation` / `rollback_allocation`）。

### 2.2 OpportunityRanker.allocate（全局竞争）
[opportunity_ranker.py:154-237](../portfolio_logic/opportunity_ranker.py)：按 score 排序 → 逐候选
cap 检查 → notional 单调 → `tentative_apply` → `commit`。所有 symbol/cluster 候选竞争同一池。

### 2.3 三层 cap 共用同一 equity 分母
[opportunity_ranker.py:126-152](../portfolio_logic/opportunity_ranker.py) `_enforce_notional_caps`，
[config.py:152-168](../portfolio_logic/config.py) `CapsConfig`：
`max_total_notional_pct=1.5` / `max_cluster_notional_pct=0.5` / `max_symbol_notional_pct=0.3`
→ `allowed = min(notional, room_symbol, room_cluster, room_total)`，争同一 150% 总额。

### 2.4 引擎本身已是全局口径 —— 核心结论
[pipeline_oot_evaluation.py:458-518](../model/oot/pipeline_oot_evaluation.py)：每根 bar 把当根
**所有** symbol/cluster 候选汇成 `cand_df` → `ranker.score` → `ranker.allocate(scored, state, caps)`
对单一全局 state + caps 竞争，bar 末 commit 到 `runtime_state`。

> **因此：跨 cluster/symbol/interval 的全局竞争 + 150% 总额，只要把所有 cluster 的 predictions
> 合并成一次 `_evaluate_oot_real_execution` 调用就天然成立——引擎一行都不用改。**
> 瓶颈纯在编排层 `_group_train_runs_by_pool` 把 pool 拆开了。

---

## 3. 设计：统一组合评估

### 3.1 核心改动点（编排层，1 处）
把"按 pool 分组各跑一次"改为"**全部 metas 的 predictions 合并成 1 个 `pred_df`，跑 1 次
`_evaluate_oot_real_execution`**"。

复用现成机制：`rerun_oot_for_train_group` 已经会把多个 prediction frame `pd.concat` 后跑一次
（[eval_only_run.py:359-400](../model/eval_only_run.py)）。只需把分组粒度从
`(run_date, pool_name, side)` 放大到 `(run_date, side)`（或单一全局组），让所有 cluster 的
predictions 进同一次回放。

> **已实施（2026-06-01）**：`run_oot_eval_batch(unified_portfolio=True)` 默认；CLI `--per-cluster-portfolio` 回退旧行为。
>
> **引擎缺陷修复（必须，否则 day 消失）**：统一合并把 day（日期-only datetime）+ minute（带时分秒）混进一列，
> 引擎主路径裸 `pd.to_datetime` 会把其中一种 coerce 成 NaT 再 dropna，输出塌成单一 interval。
> 已加 `_to_dt_mixed`（`format='mixed'`）修复，见 [pipeline_oot_evaluation.py](../model/oot/pipeline_oot_evaluation.py)
> + 回归测试 `test_mixed_interval_replay_keeps_day_and_minute`。这是 §0 设计假设"merge 即可"之外必须补的一环。

### 3.2 cluster 维度报表如何保留（必须改）
**当前** `02_by_cluster/` 是从 `symbol_group_details/{pool_name}_{interval}/` 目录**拷贝**而来
（[oot_report_writer.py:456-468](../model/reporting/oot_report_writer.py)），强绑定 per-pool 输出。

**统一模式下**只有一次合并回放 → 没有 per-pool 子目录。需把 by_cluster breakdown 改为
**从统一 `trade_details` 按 `cluster_name` 列 group 重切**（`cluster_name` 由 `symbol` 经
`infer_symbol_cluster` 推出，trade 行已带）。同理 `_comparison.csv` / by_interval / by_signal_type
都从统一 trades 按对应列聚合，不再依赖 pool 切分。

### 3.3 资金口径
单一 `cfg.initial_capital`（1000 万）贯穿全程；不再 per-pool 各 1000 万。

### 3.4 时间回放
合并后的 `pred_df` 由引擎按 `datetime` 线性回放，跨 cluster 候选自然落在同根 bar 的 `cand_df`
里竞争——天然正确，无需额外排序逻辑。

### 3.5 signal_type 杠杆 / 并发 cap / trade_filter delta
全部沿用引擎现有逻辑，**现在是全局口径**（之前是 per-cluster 口径）。例如 bull_pullback 的
`max_concurrent=20` 之前是"每 cluster 最多 20"，统一后变成"全局最多 20"。

---

## 4. 影响与风险

| # | 风险 | 说明 / 缓解 |
|---|---|---|
| 4.1 | **历史不可比** | 8 簇共享一个 150% → 总成交量、年化大幅下降。旧报告（如 277%）**作废**，须重建基准，不可直接对标。 |
| 4.2 | **内存/规模** | 合并所有 cluster×interval 的 predictions（~数十万行）一次回放。需评估内存峰值；退路：按时间分块回放（保持单一 state 跨块延续）或先降采样验证。 |
| 4.3 | **by_cluster 报表口径迁移** | §3.2：report writer 现按 pool 子目录拷贝，必须改为按 trade 行 `cluster_name` 聚合，否则统一模式下 02_by_cluster 为空。 |
| 4.4 | **空 predictions 缺席** | BLACK/CHEMICAL 等"整目录无产出"的桶在统一模式下直接缺席竞争。合并前须逐 meta 校验 predictions 非空，缺失 **loud warning**（关联 [20260531_fix_features.md](review/20260531_fix_features.md)）。 |
| 4.5 | HTF shared_ref | 已是全 metas 合并（[eval_only_run.py:589-602](../model/eval_only_run.py)），与统一模式一致，无需改。 |
| 4.6 | **per-cluster 模式弃用** | 删除/降级 `_group_train_runs_by_pool` 的 pool 拆分路径；需同步更新依赖它的测试与文档。 |

---

## 5. 实现步骤（下一轮做，本轮仅记录）

- **S1 编排层**：新增"统一分组"——所有 metas 合并为单组（或按 `side` 分），复用
  `rerun_oot_for_train_group` 的 concat→单次评估机制；让它成为 `run_oot_eval_batch` 默认路径。
- **S2 报表源迁移**：by_cluster / by_interval / by_signal_type 改为从统一 `trade_details` 按对应列
  group（§3.2），去掉对 `symbol_group_details/{pool}_{interval}` 的依赖。
- **S3 健壮性**：合并前 predictions 非空校验 + loud warning（§4.4）。
- **S4 弃用 per-cluster**：删 `_group_train_runs_by_pool` 的 pool 拆分（或保留为内部工具但非默认）。
- **S5 单测**：构造 2 个 cluster 的候选在**同一 bar** 撞 150% 总额 → 断言只成交到 150% 为止，
  且跨 cluster 按 score 竞争（高分 cluster 挤掉低分 cluster）；再断言 `max_total_positions` 全局生效。
- **S6 收尾**：change_log + 重跑统一 OOT 重建基准 + 更新 model.md 命令说明。

---

## 6. 验证（实现后）

- **单测**：跨 cluster 同 bar 竞争 + 150% 总额封顶 + 全局 `max_total_positions`。
- **全量回归**：`pytest cta/model/tests cta/portfolio_logic/tests -q`。
- **重跑统一 OOT**：确认 peak 总 notional ≤ 150%（per-cluster 模式下曾到 ~579%，见
  [20260531.md](review/20260531.md) §H2）、全局并发 ≤ 配置值、trade_count 大幅低于旧 per-cluster。
- **与 sim 回放对拍**：同 cfg 同期，eval-only 的成交序列应与 sim 回放一致（这是"同引擎同账本"的最终验收）。

---

## 7. 关联文档
- [review/20260531.md](review/20260531.md) §H2：per-cluster notional cap 失守 —— 统一模式正是其根治。
- [sim_plan.md](sim_plan.md) / [sim_live_integration_roadmap2.md](sim_live_integration_roadmap2.md)：OOT=sim=live 一致性目标。
- [review/20260531_fix_features.md](review/20260531_fix_features.md)：空 predictions / 上游数据缺口（§4.4 相关）。
