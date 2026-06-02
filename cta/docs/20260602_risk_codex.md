# 20260602 风控评估与改进建议（Codex）

## 1. 范围与口径

- 评估对象：`cta/backtest/oot_20260601_212509_unified_v10`
- 交易明细：`raw/all_trade_details.csv`
- 诊断辅助：
  - `09_diagnostics/concentration_diagnostics.csv`
  - `09_diagnostics/daily_trade_position_distribution.csv`
  - `09_diagnostics/monthly_trade_position_distribution.csv`
  - `09_diagnostics/walk_forward_summary.csv`
- 执行口径：`execution_status == executed`

## 2. 关键结论（先看这个）

1. 策略收益为正，但对资金占用上限依赖极强。  
执行 2441 笔，净利润约 2246 万；`open_notional_at_entry/equity_before` 的 P90/P95 基本贴着 1.5 上限运行。

2. 收益分布偏“厚尾”，中位数交易很薄。  
平均每笔净利约 9201，但中位数仅约 28；说明靠少量大盈利交易拉动，抗参数扰动能力一般。

3. 成本侵蚀仍重。  
估算成本约 1789 万，约占毛利 71.8%。当前组合仍需继续“降换手 + 提边际质量”。

4. 止损触发率不低，且 day 维度风险特征明显。  
执行单中 `hard_stop` 占 26.96%；day 级信号里，`bull_pullback_continuation` 止损率 89.8%，`breakout_pullback_continuation` 止损率 74.3%。

5. 结构集中在少数信号类型。  
两类 pullback（bull/breakout）合计 2382 笔，占执行笔数约 97.6%。  
`bull_volatility_contraction_breakout` 4 笔全亏（-2.59 万），应直接禁用。

6. 存在“同 bar 入场并同 bar 止损”的执行风险。  
这类交易 120 笔，占执行约 4.92%，全部是 `hard_stop`，属于典型微观流动性/跳空风险暴露点。

## 3. 主要风险画像（数据证据）

## 3.1 执行与拦截结构

- 总样本 18363，执行 2441（13.29%）
- 主要拦截来源：
  - `blocked_trade_filter`: 5675
  - `blocked_ranker`: 5459
  - `blocked_final_decision_gate`: 1819
  - `blocked_signal_type_concurrent`: 1140
  - `blocked_zero_notional`: 1029
  - `blocked_htf_gate`: 727

解读：当前框架已是“重门控”模型，后续风控更应优先优化“执行后损失结构”，而不是再盲目加门。

## 3.2 资金与杠杆占用

- `open_notional/equity`：
  - P50 = 1.3408
  - P90 = 1.4999
  - P95 = 1.5000
  - Max = 1.5000
- `>=1.2` 的交易占 65.75%，`>=1.0` 占 84.23%
- 日终名义仓位（`monthly_trade_position_distribution.csv`）：
  - 月均约 100%~116%
  - 月内最大约 149.96%
- 日终保证金占用峰值约 18.08%

解读：名义仓位经常贴顶运行，说明组合波动对“上限参数”非常敏感。

## 3.3 收益质量与尾部

- 胜率 52.56%
- 净利润 2245.95 万，毛利 2490.93 万
- 交易净利中位数 28.24，显著低于均值 9200.95
- 最差单笔约 -6.64 万，最差 5 笔合计约 -32.41 万
- 单日最差约 -17.02 万（2025-12-11）
- 日度路径最大回撤约 -1.56%

解读：总体回撤可控，但单笔尾部与收益偏态明显，需压“高占用 + 低边际”的尾部交易。

## 3.4 止损与退出结构

- `exit_reason`：
  - `horizon_exit`: 1783
  - `hard_stop`: 658
- `stop_triggered_rate`: 26.96%
- `extensions_used > 0`: 0%（本次评估未实际触发延长持有）
- 同 bar 入场/离场（<=0 分钟）120 笔，全部 `hard_stop`

解读：当前主要风险不是“延长持有失控”，而是“入场后短时间触发硬止损”的执行质量问题。

## 3.5 分层风险（interval/signal/cluster）

- interval：
  - `minute30`: 1469 笔，胜率 55.68%，净利约 1197.7 万
  - `minute60`: 794 笔，胜率 52.77%，净利约 755.2 万
  - `day`: 178 笔，胜率 25.84%，净利约 293.0 万（明显依赖少数大盈）
- signal_type：
  - `bull_pullback_continuation`: 1246 笔，净利约 1060.1 万
  - `breakout_pullback_continuation`: 1136 笔，净利约 1174.9 万
  - `cross_sectional_momentum`: 55 笔，净利约 13.5 万
  - `bull_volatility_contraction_breakout`: 4 笔全亏
- cluster（按 symbol 映射）：
  - `index` 止损率约 38.69%，显著偏高
  - `other` 交易最多（1013 笔），止损率约 32.18%

解读：day 与 index 组合更像“高尾部、高离散”的风险源，需单独设防。

## 4. 建议方案（按优先级）

## P0（立即执行）

1. 全链路禁用 `bull_volatility_contraction_breakout`。  
理由：本样本 4 笔全亏，无统计优势。

2. 下调总名义仓位上限（分两步灰度）。  
建议：
  - Phase-1：`max_total_notional_pct: 1.5 -> 1.2`
  - Phase-2：若收益回撤比改善，再评估 `1.0`

3. 下调单 cluster 名义上限。  
建议：`max_cluster_notional_pct: 0.6 -> 0.50`  
目的：降低同风格相关性冲击。

4. day 级别单独降杠杆。  
建议对 day 候选增加二级缩放（可通过 `signal_type_size_multiplier` 的 day 版本或 interval-aware 规则）：
  - day 的 pullback 仓位系数下调 30%~50%
  - day 的 trade_filter percentile 阈值额外提高 5~10 pct

5. 启用/强制成交量参与率约束并落地“超量顺延”。  
建议确认以下参数在 eval/sim/live 一致启用：
  - `enforce_intrabar_bar_volume_cap = true`
  - `intrabar_max_bar_volume_participation_pct = 0.01`
并记录“顺延成交”日志，用于复盘同 bar 止损是否显著下降。

## P1（1-2 周内）

6. 硬止损率控制目标化。  
目标：`hard_stop_rate` 从 26.96% 降到 `<22%`。  
手段：
  - 对 `index`、`day`、`other` 高频亏损子集提高入场门槛（`min_pred_edge_atr` 或 ranker 阈值）
  - 对 stop_rate 高于 35% 的 `(cluster, interval, signal_type)` 自动降仓

7. 增加“同 bar 止损”专项风控。  
新增监控指标：`same_bar_stop_rate`，目标 `<2%`。  
若连续 5 日 >2%，自动：
  - 下调该组仓位 20%
  - 或提升 trade_filter/ranker 阈值

8. 成本治理（降换手而非盲目扩笔数）。  
当前 `cost/gross ≈ 71.8%`，建议把低边际交易再过滤一层：  
仅对 `pred_edge_atr` 低分位（如后 20%）加 stricter gate，优先砍“成本后为负”的交易。

## P2（制度化）

9. 建立每日“风险四联表”（建议写入 live review）。  
- 杠杆：`open_notional/equity` 的 P90/P95/Max  
- 尾部：单日最差、单笔最差、最差 5 笔和  
- 执行：`hard_stop_rate`、`same_bar_stop_rate`  
- 集中：`top5_symbol_pnl_pct`、`top1_trade_pnl_pct`

10. 周度风控回归流程。  
对每个参数调整做 A/B：仅变更一项并固定 4 周观察窗口，避免多参数同改导致归因失真。

## 5. 建议阈值（可直接作为风控 KPI）

- `open_notional/equity_before`：P95 <= 1.1（当前约 1.50）
- `hard_stop_rate`：< 22%（当前 26.96%）
- `same_bar_stop_rate`：< 2%（当前 4.92%）
- `cost_to_gross_ratio`：< 60%（当前约 71.8%）
- `day interval win_rate`：> 40%（当前约 25.84%）
- `index cluster stop_rate`：< 30%（当前约 38.69%）

## 6. 执行顺序建议

1. 先做 P0 的 1/2/3/5（不改模型，仅改风控与执行约束）  
2. 再做 P1 的 6/7（专项压 hard stop 与同 bar 止损）  
3. 最后做成本优化和制度化监控（P1-8 + P2）

---

以上建议重点不是“追求更高名义杠杆”，而是把当前已有效的 alpha 在真实执行里做“降尾部 + 降摩擦 + 降相关性拥挤”，让 OOT 收益更可持续地迁移到 sim/live。
