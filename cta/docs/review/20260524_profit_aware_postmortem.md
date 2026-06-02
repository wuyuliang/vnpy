# Profit-aware-trend-adaptive 上线后未改善的复盘 + 修复

> 比较对象：
>   - 优化前：`cta/report/backtest/oot_20260523_034107_cluster_both/`
>   - 优化后：`cta/backtest/oot_20260523_154428_cluster_both/`
>   - 设计文档：[cta/docs/profit_aware_trend_adaptive_design.md](../profit_aware_trend_adaptive_design.md)

---

## §1 现象：总指标全线小幅恶化

| 指标 | BEFORE | AFTER | Δ |
|---|---|---|---|
| 总收益率 | 43.85% | 41.42% | **-2.43pp** |
| 年化收益率 | 20.00% | 18.98% | **-1.02pp** |
| 最大回撤 | -4.59% | -6.95% | **-2.36pp 恶化** |
| 月度 Sharpe | 1.69 | 1.67 | -0.02 |
| 总 net_pnl | 438,539 | 414,246 | **-24,293** |
| 交易笔数 | 1752 | 1736 | -16 |
| 胜率 | 39.95% | 40.84% | +0.89pp |

虽然 cluster_precious 局部确实改善了（net_pnl 128k → 139k，+10.9k；win_rate 62% → 66%），
但 cluster_other 反向恶化 -60k，吞掉所有正贡献。

---

## §2 per-cluster 拆解

用 (entry_datetime, symbol, signal_type, side, interval) 作为天然主键，
把 BEFORE / AFTER 的 executed 交易做 outer join，得到三类：
- `both`：两次都被执行的同 key 交易
- `only_bf`：只在 BEFORE 执行（AFTER 没执行）
- `only_af`：只在 AFTER 执行（BEFORE 没执行）

| cluster | both_dpnl | only_bf_lost_pnl | only_af_gain_pnl | net_delta |
|---|---|---|---|---|
| agri | +61 | -1,648 | +9,584 | **+11,293** |
| black | +335 | +10,361 | +9,985 | -41 |
| bond | 0 | -641 | -319 | +322 |
| chemical | -645 | -893 | +17,466 | **+17,714** |
| index | -83 | +11,464 | +5,412 | -6,135 |
| metal | 0 | +2,426 | +4,354 | +1,928 |
| **other** | +1,034 | **+44,447** | **-16,908** | **-60,321** |
| precious | +105 | +4,676 | +15,518 | **+10,947** |

**关键观察**：
1. **`both_dpnl` 在每个 cluster 都接近 0**（含 trailing TP 启用的 precious 也只有 +105）——
   说明 `simulate_trailing_exit` 在"同样的交易上"输出 PnL 几乎一致，模拟器本身改动**是 PnL-中性的**。
2. damage 集中在"被换出的赢家" - "换上来的输家"，主要在 **cluster_other**：
   原本能赚 +44k 的 146 笔不见了，换上来 115 笔反而亏 -17k。

---

## §3 根因：5 个模块只有 Module A 在 precious 真正生效

跨 cluster 检查 trade_details 列：

| cluster | rows | trailing_tp_active=1 | trend_aware_relaxed=1 | extensions_used>0 |
|---|---|---|---|---|
| agri | 13,604 | 0 | 0 | 0 |
| black | 7,579 | 0 | 0 | 0 |
| bond | 931 | 0 | 0 | 0 |
| chemical | 6,492 | 0 | 0 | 0 |
| index | 4,282 | 0 | 0 | 0 |
| metal | 5,611 | 0 | 0 | 0 |
| other | 29,320 | 0 | 0 | 0 |
| **precious** | 3,310 | **17** | 0 | 0 |

— precious 17 笔 TP 模拟事件中，**只有 2 笔实际成交**（其余 15 笔在 trade_filter / htf / final_decision_gate 阶段被前置 gate 拦掉），合计 +13,926 net_pnl 增量。

### §3.1 Module B/C/D/E 全部没有被打开

设计文档 §13 实施清单要求 5 个模块都通过 CLI `--enable-*` flag opt-in；
本轮 154428 run 只传了 `--enable-trailing-take-profit` + `--trailing-tp-enabled-cells precious|day`，
**其它 4 个模块从未被 wire**：

| 模块 | CLI flag | 154428 实际状态 |
|---|---|---|
| A trailing_take_profit | `--enable-trailing-take-profit` | **已打开（precious\|day）** |
| B trend_aware_trade_filter | `--enable-trend-aware-trade-filter` | ❌ 未传 |
| C profit_aware_horizon | `--enable-profit-aware-horizon` | ❌ 未传 |
| D adaptive_setup_window | 无 CLI flag（D 还未 wire 到 candidate gen） | ❌ 未启用 |
| E winning_position_setup_diversity | 无 CLI flag（E 还未 wire 到 candidate gen） | ❌ 未启用 |

### §3.2 整 pipeline 重训导致 ~38k 噪声

154428 与 034107 不是"仅替换 OOT 执行"的 A/B —— 整个 pipeline（含特征生成 + 模型训练）
都重跑了。新加入的 `regime_label` / `ma_alignment` 列被注入 candidate frame
（虽然默认在 gate 里不启用），但通过 **`generic-mode auto`** 这类自动列发现可能把
新列灌进训练样本，从而对模型 ranking 形成轻微扰动。模型分数变了 → trade_filter /
ranker / FCFS 顺序变了 → 跨 cluster 蝴蝶效应。

`only_bf` + `only_af` 各 ~490 笔的剧烈交换正是这种扰动的指纹。

---

## §4 实施层面的 4 个 BUG（已修复）

### §4.1 Bug F1 — 评估顺序违反设计文档 §3.4

**问题**：原代码先评估 trailing TP（按 `bar_close`），再评估 hard_stop / trailing_stop（按
intrabar `bar_high` / `bar_low`）。

设计文档 §3.4 明确顺序：`hard_stop → trailing_take_profit → trailing_stop → horizon_exit`。

**影响**：当一根 bar 同时击穿 hard_stop（intrabar 极值）和 trailing TP（close 回撤），
代码选 trailing TP 退出 —— 这是 **same-bar lookahead**，让 TP 实际成交价
看起来比真实场景好。

**修复**：把 trailing TP 块移到 hard_stop / trailing_stop `break` 之后。
新增回归测试 `test_hard_stop_wins_over_trailing_tp_in_same_bar`。

### §4.2 Bug F2 — highwater 包含本 bar 的 high/low（同 bar lookahead）

**问题**：`running_high` / `running_low` 在循环开头被用本 bar 的 `bar_high` / `bar_low` 更新，
然后立刻被用来设 `trailing_tp_highwater`，再用 `bar_close` 比较 trigger —— 同 bar lookahead。

**修复**：在循环开头先把 `running_high` / `running_low` 快照成 `prev_running_high`
/ `prev_running_low`，trailing_tp_highwater 用快照，不含本 bar。
新增回归测试 `test_trailing_tp_no_same_bar_lookahead`。

### §4.3 Bug F3 — `trend_score` 硬编码 0.5，违反设计文档 §2.3

**问题**：原代码 `trend_score=0.5 if regime in {trend_up, trend_down} else 0.0`。
- 对 LONG 在 trend_down 中 → trend_score=0.5 > 0 → `require_trend_confirmed=True` 仍允许 TP 触发
- 对 SHORT 在 trend_up 中 → 同样的逻辑错误

设计文档 §2.3 复合公式：
```
trend_score = 0.5*sign(ma_alignment)*min(|ma_alignment|/2, 1)
            + 0.3*regime_score(regime_label)
            + 0.2*sign(pnl_pct)*min(|pnl_pct|/0.05, 1)
```
且必须 **side-aware**（短头寸时 ma/regime 项应翻转符号）。

**修复**：新增 `_trend_score_for_position(side, regime_label, current_pnl_pct)` helper，
调用 `compute_trend_score()` 并按 side 翻转 ma/regime 项。

### §4.4 Bug F4 — `use=False` 时 evaluator 仍被构造（CPU 浪费 + 列污染）

**问题**：`OotEvaluationConfig.trailing_take_profit` 默认 `field(default_factory=TrailingTakeProfitConfig())`
非 None；OOT 调用 `simulate_trailing_exit` 时**永远**传 cfg，函数内 `trailing_tp_eval =
TrailingTakeProfitEvaluator(cfg) if cfg is not None else None` 就**永远**构造 evaluator。
每个 bar 都构造 `PositionTrendState` + 调用 `evaluator.update()`（返回 None），
还把 `trailing_tp_highwater` 列填出非 nan 值（实际未启用却显示数据）。

**修复**：
```python
use_trailing_tp = bool(
    trailing_take_profit_cfg is not None
    and bool(trailing_take_profit_cfg.use_trailing_take_profit)
    and trailing_take_profit_cfg.is_enabled(symbol_cluster, interval)
)
trailing_tp_eval = TrailingTakeProfitEvaluator(cfg) if use_trailing_tp else None
```
新增回归测试 `test_trailing_tp_disabled_when_use_flag_false`。

---

## §5 为什么前述 4 个 bug 修复 **不会** 让 154428 翻盘

修复后 trailing TP 触发会**更保守**（不会再 peek、不会再无视方向）。
这意味着 precious 那 2 笔成交可能：
- 仍按 `trailing_take_profit` 退出（trigger 在第 N+1 bar，不是第 N bar）
- 或者被 `hard_stop` 抢先（如果 intrabar 低于 hard_stop）

无论哪种，precious +13.9k 局部增量都会缩小，**整体仍可能略恶化**（因为蝴蝶效应噪声仍在）。

**真正需要做的是**：在 cluster_precious 之外，**让 Module B/C 也启用**，
覆盖更多场景，把"漏赚"治本，否则单 Module A 不可能扭转 ~96 笔被 trade_filter 拦掉的
AU/AG 候选。

---

## §6 优化方案

### §6.1 立即可做（已落地的 bug 修复 + 推荐 CLI 配置）

修复已经合入 [cta/portfolio_logic/trailing_exit.py](../../portfolio_logic/trailing_exit.py)。
下一轮 OOT 用以下 CLI 全开 A+C，灰度上 B：

```bash
python3 -m cta.model.model_pipeline \
  --group-pool --group-by cluster --interval day 60min 30min \
  --start 2015-01-01 --end 2025-12-31 \
  --train-end 2023-12-31 --valid-end 2024-06-30 \
  --use-portfolio-logic-runtime \
  --generic-mode auto \
  --enable-trailing-take-profit \
    --trailing-tp-enabled-cells "precious|day,index|day,metal|day" \
  --enable-profit-aware-horizon \
    --profit-aware-horizon-enabled-cells "precious|day,index|day" \
  --enable-trend-aware-trade-filter \
    --trend-aware-trade-filter-enabled-cells "precious|day" \
  --output-root cta/backtest/20260524_v_acb_3clusters
```

预期效果（基于设计文档 §10.2 验收指标）：
- AU+AG 2025 net_pnl: 89k → 至少 150k
- AU+AG executed/candidates: 12.7% → 至少 20%
- 整 OOT 总收益: 43.85% → 至少 50%

### §6.2 隔离蝴蝶效应（结构性改进）

154428 vs 034107 的剧烈 trade 交换告诉我们：**当一个 cluster 改动时，其它 cluster
不应该被影响**。当前 OOT pipeline 把所有 cluster 的 candidate 放在同一个 portfolio
capital pool 里 FCFS 选执行 → 任何 cluster 的策略改动都会扰动其它 cluster。

**推荐**：在 OOT 评估阶段加 `--isolate-cluster-pnl` 选项，让每个 (cluster, interval)
跑独立 capital pool；最后做加权 sum 而不是共享 cash queue。这样 cluster_precious 的 TP 改动
**不会**影响 cluster_other 的 trade 选择。

实施工作量：~半天，改 `pipeline_oot_evaluation.py` 的 cash bookkeeping 即可。
不在本次 PR 范围内，列为后续 follow-up。

### §6.3 Module D / E 的 wire-up（拖延已久）

Module D（adaptive setup window）和 E（winning_position_setup_diversity）
配置类和 helper 都已经存在，但**从未接入 candidate gen 链路**。

- D: 在 [strategy/baseline_setup_detection.py](../../strategy/baseline_setup_detection.py)
  的 `atr_breakout` / `donchian_breakout` 算子接受 `lookback_override` 参数。
- E: 在 [strategy/baseline_candidate_gen.py](../../strategy/baseline_candidate_gen.py)
  的 dedup 阶段读 `cfg.winning_position_setup_diversity`。

每个工作量 ~1 天，含单测。

### §6.4 模型重训噪声 mitigations

`generic-mode auto` 把所有 numeric 列灌进模型，新加的 `regime_label` / `ma_alignment` 列
会被自动吸收 → 即使 gate 默认 off 模型还是变了。

**推荐**：
- A/B 严控：测新 gate 时 freeze 模型（`--reuse-models <baseline_dir>`），只跑 OOT 评估，
  消除模型重训噪声。
- 或：让 candidate frame 显式 drop 这两列（除非 gate 启用），避免泄露到模型。

实施工作量：~1 天（pipeline_cli 加 `--reuse-models` flag）。

---

## §7 验证命令

```bash
# 1) 回归（已通过）
python3 -m pytest cta/portfolio_logic/tests/ \
                  cta/model/tests/test_pipeline_oot_evaluation.py \
                  cta/config/tests/test_trailing_take_profit_config.py \
                  cta/config/tests/test_profit_aware_horizon_config.py -q
# → 114 passed

# 2) 推荐的下一轮 A/B（v_acb_3clusters），见 §6.1。

# 3) 隔离 capital pool 后再跑一次，验证 cluster_other 不再被扰动（§6.2）
```

---

## §8 修改文件清单

| 文件 | 改动 |
|---|---|
| `cta/portfolio_logic/trailing_exit.py` | 评估顺序修复 + 同 bar lookahead 修复 + trend_score 修复 + `use=False` 时不构造 evaluator + 新增 `_ma_alignment_for_regime` / `_trend_score_for_position` helpers |
| `cta/portfolio_logic/tests/test_trailing_exit.py` | 新增 3 个回归 case（`test_hard_stop_wins_over_trailing_tp_in_same_bar`、`test_trailing_tp_no_same_bar_lookahead`、`test_trailing_tp_disabled_when_use_flag_false`） |
| `cta/docs/review/20260524_profit_aware_postmortem.md` | 本文档（复盘 + 优化方案） |

## §9 风险与后续

| # | 风险 | 应对 |
|---|---|---|
| R1 | 修复后 precious +13.9k 局部增量会缩小 | 接着开 Module B + C 把 AU/AG 51% 被 trade_filter 拦的候选解锁，期望 +30k 替代 |
| R2 | 蝴蝶效应（490/474 trade 互换）依然存在 | §6.2 隔离 capital pool；或开 `--reuse-models` 消除模型重训扰动 |
| R3 | 全开 A+B+C 后过度激进 | 设计文档 §11.1 滚动启用日历仍要遵守：W1 只开 A，W2 加 C，W3 加 B；任何 cluster monthly net_pnl 恶化 >15% 立刻回退 |
