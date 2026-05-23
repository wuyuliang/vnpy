# CTA OOT 评估结果展示规范

> 站在中国 CTA 团队从业者视角设计：研究员看明细、PM 看大盘、老板看一句话总结，群里贴简报。每次跑 OOT 评估生成一个**带时间戳的大目录**，按"维度"二级分子目录，便于横向对比、版本回溯、复盘归因。
>
> 落地形式：本文档定义最终目录与文件 schema；codex 按 §10 wave 实施，最终统一从 `cta/model/oot_report_writer.py` 入口生成。

---

## 0. 设计原则

1. **时间戳顶层目录**：`oot_{YYYYMMDD_HHMMSS}_{run_tag}/`。**禁止覆盖历史**，多次跑可并存可 diff。
2. **维度分子目录 ≫ 按 group 平铺**：研究员第一眼想看的是"哪个板块好、哪个周期好、哪个信号好"，不是"GRP_CLUSTER_BLACK 怎么样"。group 视图嵌进 `02_by_cluster/` 里。
3. **三档报告**：
   - `executive.html` —— 给 PM / 老板，5 张图 5 个数字
   - `analyst.html` —— 给研究员，全量图表 + 下钻表
   - `brief.md` —— 群消息版，300 字以内可直接贴
4. **CSV 是事实，HTML 是叙事，PNG 是直觉**。任何 HTML 数字必须在 CSV 找得到原始来源。
5. **复盘可重放**：`meta/` 子目录保留 `git_sha` / `cli_args` / `config_snapshot` / `pip_freeze`，半年后还能跑回同样结果。
6. **业绩口径中国 CTA 行业惯例**：年化用 252 交易日；月度 sharpe 年化乘 √12；最大回撤 = peak-to-trough；胜率分笔级 + 日级两套；展期成本独立列出。
7. **向后兼容现有产物**：现有 `_write_group_pool_runtime_bundle` 产出的 `*_aggregate_*_metrics.csv` 直接进 `01_aggregate/`；per-group 的 `oot_trade_details.csv` 进 `02_by_cluster/{cluster}/`。无破坏式改动。

---

## 1. 顶层目录结构

```
{output_root}/oot_{YYYYMMDD_HHMMSS}_{run_tag}/
├── 00_overview/             # 一眼看穿
├── 01_aggregate/            # 跨 group 总盘子
├── 02_by_cluster/           # 按板块拆（黑色/有色/化工/股指/国债...）
├── 03_by_symbol/            # 按品种拆（RB0/IF0/T0...）
├── 04_by_interval/          # 按周期拆（day / 60min / 30min）
├── 05_by_signal_type/       # 按信号类型拆（donchian / atr / tight_range / pullback）
├── 06_drilldown/            # 深度诊断（gate funnel / 滑点拆解 / 异常交易）
├── 07_benchmark/            # 对标（vs 沪深300 / 商品指数 / Buy&Hold）
├── 08_models/               # 模型产物（joblib + manifest）
├── 09_diagnostics/          # 模型质量（AUC / 校准 / leakage 审计）
├── reports/                 # 3 档报告
├── raw/                     # 原始合并产物
└── meta/                    # 复盘元数据
```

`run_tag` 推荐值：
- `prod` — 生产配置全量
- `index_pool` — 仅股指 cluster
- `bond_pool` — 仅国债 cluster
- `dryrun` — 研究试跑
- `param_sweep_{N}` — 参数扫描的第 N 次

例：`cta/backtest/oot_20260517_073500_prod/`

---

## 2. `00_overview/` — 一眼看穿

PM / 老板打开评估结果先看这里。**只放 5 个文件**，多了就稀释了。

| 文件 | 内容 |
|---|---|
| `executive_summary.md` | 三句话讲明白："过去 N 个月，组合年化 X%，最大回撤 Y%，月度胜率 Z%。最强板块 A，最弱板块 B。" |
| `headline_metrics.csv` | 单行 17 列：start / end / trade_count / win_rate / annualized_return_pct / max_drawdown_pct / monthly_sharpe / calmar_like / total_return_pct / avg_holding_days / longest_dd_recovery_days / cluster_count / symbol_count / interval_count / commission_pct_of_gross / slippage_pct_of_gross / git_sha |
| `equity_curve.png` | 总资金曲线（含回撤阴影），中国 CTA 常用样式 |
| `monthly_returns_heatmap.png` | 月度收益热力图（年 × 月，红绿渐变） |
| `drawdown_curve.png` | Underwater chart（回撤随时间） |

### 2.1 `executive_summary.md` 模板

```markdown
# OOT 评估摘要 — {run_tag}

**期间**: 2024-01-01 → 2026-05-16（28 个月）
**初始资金**: 1,000,000 元

## TL;DR
过去 28 个月，组合**年化收益 +14.3%**、**最大回撤 -8.2%**（恢复 47 天）、**月度年化夏普 1.42**。
最强板块 **股指（cluster_index）+22.7%**，最弱 **国债（cluster_bond）-1.8%**。
最近 3 个月 win_rate=58.3%，未触发月度熔断。

## 五个数字
- 总收益率：**+33.4%**
- 最大回撤：**-8.2%**（2025-Q2）
- 月度夏普（年化）：**1.42**
- 周度夏普（年化）：**1.18**
- 月度胜率：**67.9%**（19/28 月正收益）

## 操作建议
- ✅ index cluster 继续生产配置
- ⚠️ bond cluster 拉胯，建议下个 review 把 `--only-clusters` 排除掉
- 🔍 06_drilldown/block_reason_breakdown.csv 中 `blocked_weekly_dd_rows` 占总 candidates 12%，比上次 +3pp，需排查
```

### 2.2 `headline_metrics.csv` schema

```csv
field,value,unit,note
run_tag,prod,,
start_date,2024-01-02,,
end_date,2026-05-16,,
months,28,,
trade_count,1532,笔,
executed_rows,1532,笔,等于 trade_count
candidate_rows,18334,笔,候选总量（含被 gate 过滤的）
gate_pass_rate,0.0836,,executed/candidate
win_count,978,笔,net_pnl>0
win_rate,0.6383,,win/trade
avg_holding_hours,38.4,小时,平均持仓
gross_pnl,512_300,元,
commission_total,8_410,元,
slippage_total,5_120,元,
roll_cost_total,0,元,默认连续主连不收 roll
net_pnl,498_770,元,
total_return_pct,0.4988,,
annualized_return_pct,0.1428,,按 252 天年化
monthly_sharpe,1.42,,月度 excess_return 年化（×√12）
weekly_sharpe,1.18,,周度 excess_return 年化（×√52）
max_drawdown_pct,-0.0823,,
calmar_like,1.735,,annualized_return / |max_drawdown|
longest_dd_recovery_days,47,天,
cluster_count,7,,
symbol_count,42,,
interval_count,2,,day + 60min
git_sha,a3f1b2c,,
```

---

## 3. `01_aggregate/` — 跨 group 总盘子

现有 `group_pool_aggregate.py` 产物的家。

| 文件 | 来源 | 字段（关键） |
|---|---|---|
| `monthly_metrics.csv` | 已实现 | month, trade_count, win_count/rate, gross/net_pnl, return_pct, cum_equity, drawdown_pct |
| `weekly_metrics.csv` | 已实现 | week, …（同上）|
| `summary.csv` | 已实现 | monthly/weekly sharpe, max_dd, total_return, group_count |
| `daily_equity_curve.csv` | **新增** | date, equity, drawdown_pct, n_open_positions, open_notional |
| `trade_size_distribution.csv` | **新增** | bucket(2σ/1σ/median/-1σ/-2σ), trade_count, total_pnl |
| `holding_period_distribution.csv` | **新增** | bucket(<1h/1-4h/4-24h/1-3d/3-7d/>7d), trade_count, win_rate, avg_pnl |
| `block_reason_counts.csv` | **新增** | reason, count, pct_of_candidates（margin/leverage/weekly_dd/monthly_dd/symbol_cap/htf/ranker/throttle/pyramid）|

### 3.1 `daily_equity_curve.csv` 重要性

中国 CTA 客户/合作方常常要的就是**日级净值曲线**（不是月级）。从 trade_details 重建：
- 按 `final_exit_datetime` 的 date 累加 net_pnl
- 加上当天 open positions 的 mark-to-market（用 daily close）
- 输出 `equity = initial_capital + cum_realized_pnl + unrealized_mtm`

供路演 / 月报 / 客户尽调。

---

## 4. `02_by_cluster/` — 按板块拆（核心研究视角）

中国 CTA 研究员第一眼就看这里。

```
02_by_cluster/
├── _comparison.csv                          # 7 行 × 15 列：横向对比
├── _heatmap_cluster_x_month.png             # 板块 × 月份热力图
├── cluster_index/                           # 股指
│   ├── summary.csv                          # 单行 summary（指标同 01_aggregate/summary.csv）
│   ├── monthly_metrics.csv                  # 该 cluster 月度
│   ├── weekly_metrics.csv                   # 该 cluster 周度
│   ├── trade_details.csv                    # 该 cluster 逐笔
│   ├── members.csv                          # 包含哪些品种（IF0/IH0/IC0/IM0 + research_rank + tier）
│   ├── equity_curve.png                     # 该 cluster 单独资金曲线
│   ├── drawdown_curve.png
│   └── monthly_heatmap.png
├── cluster_bond/                            # 国债（T0/TF0/TS0）
├── cluster_black/                           # 黑色（RB0/HC0/I0/J0/JM0/SS0）
├── cluster_metal/                           # 有色（CU0/AL0/ZN0/PB0/NI0/SN0）
├── cluster_chemical/                        # 化工（MA0/TA0/PP0/L0/V0/EB0/RU0/...）
├── cluster_agri/                            # 农产品（M0/Y0/P0/A0/B0/C0/CS0/RM0/OI0/CF0/SR0/...）
├── cluster_precious/                        # 贵金属（AU0/AG0）
└── cluster_energy/                          # 能源（SC0/LU0/FU0/BU0/PG0）
```

### 4.1 `_comparison.csv` schema

按 sharpe 降序排列：

```csv
cluster_name,cn_name,member_count,trade_count,win_rate,total_return_pct,annualized_return_pct,monthly_sharpe,max_drawdown_pct,calmar_like,avg_holding_hours,pnl_contribution_pct,best_month,worst_month,turnover_ratio
cluster_index,股指,4,412,0.6359,0.2270,0.0985,1.78,-0.0512,1.924,42.3,0.453,2025-03,2025-09,8.4
cluster_metal,有色,6,251,0.6534,0.1430,0.0623,1.21,-0.0654,0.953,48.7,0.286,2026-01,2024-08,4.2
cluster_black,黑色,6,318,0.5818,0.0980,0.0427,0.78,-0.0723,0.591,35.1,0.196,2025-11,2025-06,5.8
cluster_chemical,化工,10,201,0.5274,0.0420,0.0183,0.43,-0.0511,0.358,52.1,0.084,2026-02,2024-12,3.1
cluster_precious,贵金属,2,89,0.5618,0.0210,0.0091,0.39,-0.0289,0.315,68.4,0.042,2025-04,2025-08,2.2
cluster_agri,农产品,11,178,0.4663,-0.0050,-0.0022,-0.12,-0.0641,-0.034,41.7,-0.010,2026-03,2024-11,3.7
cluster_bond,国债,3,83,0.4458,-0.0180,-0.0078,-0.34,-0.0421,-0.185,82.5,-0.036,2024-05,2025-07,1.4
```

`pnl_contribution_pct` 是该 cluster 净盈亏占总组合净盈亏的比例（注意可以为负）。

### 4.2 每个 `cluster_*/members.csv`

```csv
symbol,exchange,cn_name,research_rank,tier,trade_count,win_rate,net_pnl,pnl_contribution_within_cluster_pct
IF0,CFFEX,沪深300股指期货,71,A,134,0.6493,178200,0.4123
IH0,CFFEX,上证50股指期货,72,A,98,0.6122,89400,0.2069
IC0,CFFEX,中证500股指期货,73,A,112,0.6429,124300,0.2876
IM0,CFFEX,中证1000股指期货,74,B,68,0.6471,40100,0.0928
```

---

## 5. `03_by_symbol/` — 按品种拆（深度归因）

```
03_by_symbol/
├── _ranking.csv                # ~42 行：所有品种按 sharpe / pnl_contribution 排
├── _heatmap_symbol_x_month.png # 品种 × 月份 PnL 热力图
├── _correlation_matrix.png     # 品种 PnL 相关性矩阵
├── RB0/
│   ├── summary.csv
│   ├── monthly_metrics.csv
│   ├── trade_details.csv
│   ├── equity_curve.png
│   └── signal_breakdown.csv    # 该品种各 signal_type 表现
├── IF0/
└── ...
```

### 5.1 `_ranking.csv` schema

按净盈亏排，便于一眼看到"哪个品种贡献最大、哪个拖后腿"：

```csv
rank,symbol,cn_name,cluster,trade_count,win_rate,net_pnl,pnl_contribution_pct,monthly_sharpe,max_dd_pct,avg_holding_hours,signal_concentration_top1
1,IF0,沪深300股指期货,cluster_index,134,0.6493,178200,0.357,2.13,-0.041,42.3,donchian_breakout:0.61
2,RB0,螺纹钢,cluster_black,98,0.6531,142800,0.286,1.65,-0.058,35.7,tight_range_breakout:0.53
3,CU0,沪铜,cluster_metal,76,0.6711,98700,0.198,1.42,-0.039,55.2,breakout_pullback:0.48
...
42,T0,十年国债期货,cluster_bond,28,0.3929,-21300,-0.043,-0.78,-0.052,96.4,atr_channel:0.78
```

`signal_concentration_top1` 揭示该品种主要靠哪个 signal_type 盈利，便于诊断 signal × symbol 适配性。

---

## 6. `04_by_interval/` — 按周期拆

```
04_by_interval/
├── _comparison.csv             # day / 60min / 30min 横向对比
├── _alpha_decay_by_interval.png # 不同周期的 alpha 衰减曲线
├── day/
│   ├── summary.csv
│   ├── monthly_metrics.csv
│   ├── trade_details.csv
│   ├── per_cluster_breakdown.csv # day 周期下各 cluster 表现
│   └── equity_curve.png
├── 60min/
└── 30min/
```

### 6.1 `_comparison.csv` schema

```csv
interval,trade_count,win_rate,annualized_return_pct,monthly_sharpe,max_drawdown_pct,avg_holding_hours,best_cluster,worst_cluster
day,412,0.5849,0.0823,1.18,-0.0612,142.3,cluster_index,cluster_bond
60min,1120,0.6107,0.0985,1.42,-0.0521,28.4,cluster_metal,cluster_agri
30min,0,,,,,,,
```

中国 CTA 团队常见经验：60min 是分钟级里 alpha/cost 比最划算的。这表用来支持该判断。

---

## 7. `05_by_signal_type/` — 按信号类型拆

```
05_by_signal_type/
├── _comparison.csv
├── donchian_breakout/
│   ├── summary.csv
│   ├── monthly_metrics.csv
│   ├── trade_details.csv
│   ├── per_cluster_breakdown.csv  # donchian 在各 cluster 表现
│   ├── per_interval_breakdown.csv # donchian 在各 interval 表现
│   └── equity_curve.png
├── atr_channel/
├── tight_range_breakout/
└── breakout_pullback/
```

研究员看这里诊断"哪类信号靠谱"。和 `03_by_symbol/` 的 `signal_concentration_top1` 联合看，可发现"donchian 只在股指好用，pullback 在化工胜出"等模式。

---

## 8. `06_drilldown/` — 深度诊断（核心研究价值在这）

```
06_drilldown/
├── gate_funnel.csv                # 候选漏斗
├── block_reason_breakdown.csv     # block 原因 × 时间 × cluster
├── slippage_commission_breakdown.csv  # 成本拆解
├── drawdown_episodes.csv          # 回撤事件清单
├── outlier_trades.csv             # top-20 大赢家 / 大输家
├── gate_funnel.png                # 桑基图：candidate → executed
├── cost_pct_of_gross.png          # 成本占毛利百分比时间序列
└── drawdown_episodes_timeline.png # 回撤事件甘特图
```

### 8.1 `gate_funnel.csv` schema

漏斗 = 总候选 → 各 gate 拦截 → 最终执行：

```csv
stage,count,pct_of_candidates,pct_of_prev
candidates_total,18334,1.0000,1.0000
after_trade_filter_gate,8421,0.4593,0.4593
after_regime_gate,5673,0.3094,0.6736
after_mfe_mae_gate,3214,0.1753,0.5666
after_stacking_gate,2031,0.1108,0.6320
after_htf_gate,1893,0.1033,0.9320
after_ranker,1654,0.0902,0.8737
after_pyramid_cooldown,1612,0.0879,0.9746
after_margin_check,1598,0.0871,0.9913
after_leverage_check,1592,0.0868,0.9962
after_daily_position_cap,1587,0.0866,0.9969
after_weekly_dd_budget,1580,0.0862,0.9956
after_monthly_dd_hard_stop,1564,0.0853,0.9899
after_limit_move_check,1561,0.0851,0.9981
executed,1532,0.0836,0.9814
```

`pct_of_prev` 揭示**哪一道 gate 在拦最多**。若某 gate 拦了 >70%，要么阈值设错了，要么该 gate 真的在做事；要么就是冗余。

### 8.2 `block_reason_breakdown.csv` schema

```csv
month,cluster,block_reason,count,pct_within_month_cluster
2026-01,cluster_index,blocked_trade_filter,87,0.412
2026-01,cluster_index,blocked_regime,32,0.151
2026-01,cluster_index,blocked_weekly_dd,5,0.024
2026-01,cluster_index,blocked_margin,2,0.009
...
```

下钻找 "为什么 2026 年某个月某板块 trade_count 突然减半"。

### 8.3 `slippage_commission_breakdown.csv`

```csv
month,gross_pnl,commission_total,slippage_total,roll_cost_total,net_pnl,cost_pct_of_gross
2024-01,21300,820,510,0,19970,0.0625
2024-02,-5400,610,380,0,-6390,-0.1833   # 亏损月成本占比可能 >100% 是常见的，单独标注
...
```

### 8.4 `drawdown_episodes.csv`

```csv
episode_id,start_date,trough_date,recovery_date,depth_pct,duration_days,recovery_days,worst_cluster,trigger_cause
1,2024-08-15,2024-09-23,2024-10-08,-0.0412,39,15,cluster_agri,"agri cluster 4 笔连亏"
2,2025-04-02,2025-06-18,2025-08-01,-0.0823,77,44,cluster_chemical,"原油暴跌+化工系统性回撤"
3,2025-11-12,2025-12-04,2026-01-09,-0.0356,22,36,cluster_black,"螺纹连续触发 weekly_dd"
```

PM 路演时讲这张表 = "我们的最大回撤来自 2025-Q2，原因是 X，做了 Y 改进"。

### 8.5 `outlier_trades.csv`

```csv
rank,direction,symbol,entry_dt,exit_dt,side,holding_hours,net_pnl,pnl_pct_of_capital,signal_type,exit_reason
top_1,winner,IF0,2025-03-12 09:30,2025-03-14 10:00,long,48.5,42100,0.0421,donchian_breakout,horizon_exit
top_2,winner,RB0,2025-11-04 21:00,2025-11-05 14:30,short,17.5,38900,0.0389,tight_range_breakout,trailing_stop
...
top_1,loser,T0,2024-05-20 09:15,2024-05-20 15:00,long,5.75,-18400,-0.0184,atr_channel,stop_loss
top_2,loser,SC0,2025-04-03 21:00,2025-04-04 10:00,long,13.0,-15800,-0.0158,breakout_pullback,stop_loss
```

研究员盯异常交易找 alpha 漏洞 / 找极端市场行为的应对。

---

## 9. `07_benchmark/` — 对标

```
07_benchmark/
├── vs_csi300_monthly.csv         # 月度 vs 沪深300
├── vs_csi500_monthly.csv         # 月度 vs 中证500
├── vs_commodity_index_monthly.csv # vs 商品综合指数
├── vs_buy_and_hold_per_symbol.csv # 各品种 vs 买入持有
├── alpha_beta_decomposition.csv   # CAPM 拆解
├── information_ratio.csv          # 信息比率（excess return / tracking error）
└── chart_relative_strength.png    # 相对强度曲线
```

中国 CTA 评估的"对标"惯例：
- 股指策略对沪深300 / 中证500
- 商品策略对南华商品综合指数
- 国债策略对 10 年期国债收益率反向构造的总收益指数
- 自身曲线 vs Buy&Hold 各底层

### 9.1 `alpha_beta_decomposition.csv`

```csv
benchmark,alpha_annual_pct,beta,r_squared,tracking_error_annual_pct,information_ratio,t_stat_alpha
csi300,0.1182,0.213,0.041,0.0934,1.266,2.142
csi500,0.1124,0.317,0.083,0.0891,1.261,2.135
南华商品综合,0.1041,0.471,0.224,0.0772,1.348,2.281
```

---

## 10. `08_models/` — 模型产物

```
08_models/
├── manifest.json                          # 哪个 group 用哪个 .joblib
├── cluster_registry.json                  # 线上路由用的注册表（与现有一致）
├── grp_cluster_index_60min/
│   ├── trade_filter.joblib
│   ├── regime_classifier.joblib
│   ├── mfe_mae.joblib
│   ├── final_decision_stack.joblib
│   ├── trade_filter_calibrator.joblib
│   ├── regime_classifier_calibrator.joblib
│   ├── mfe_mae_calibrator.joblib
│   ├── final_decision_calibrator.joblib
│   └── feature_manifest.json              # 该模型用了哪些 feature
├── grp_cluster_bond_day/
└── ...
```

### 10.1 `manifest.json`

```json
{
  "run_tag": "prod",
  "run_dt": "2026-05-17T07:35:00+08:00",
  "git_sha": "a3f1b2c",
  "groups": [
    {
      "group_name": "cluster_index",
      "interval": "60min",
      "members": ["IF0", "IH0", "IC0", "IM0"],
      "model_dir": "grp_cluster_index_60min",
      "training_window": {"start": "2018-01-01", "end": "2024-12-31"},
      "oot_window": {"start": "2025-01-01", "end": "2026-05-16"},
      "n_train_samples": 4231,
      "n_oot_samples": 612,
      "validation_auc": 0.6324,
      "test_auc": 0.6051
    }
  ]
}
```

---

## 11. `09_diagnostics/` — 模型质量

```
09_diagnostics/
├── feature_importance_top20.csv      # 各 group top20 feature 合并表
├── feature_importance_consensus.csv  # 跨 group 都重要的 feature（共识）
├── auc_per_window.csv                # 每个 walk-forward window 的 train/valid/test AUC
├── auc_gap_alerts.csv                # AUC train-valid gap 报警
├── calibration_curves.png            # 各模型校准曲线
├── score_distribution_per_split.png  # train/valid/test 概率分布对比（应当一致）
├── leakage_audit.json                # 因果 manifest 走过哪些 feature
└── feature_null_stats.csv            # 各特征 NaN 占比（>10% 的需要警觉）
```

### 11.1 `auc_per_window.csv` schema

```csv
group,interval,window_id,signal_type,model_kind,train_auc,valid_auc,test_auc,train_valid_gap,valid_test_gap,alert
cluster_index,60min,0,donchian_breakout,trade_filter,0.7821,0.6512,0.6087,0.1309,0.0425,
cluster_index,60min,0,donchian_breakout,regime_classifier,0.8104,0.6234,0.5891,0.1870,0.0343,train_valid_gap_high
cluster_index,60min,1,donchian_breakout,trade_filter,0.7654,0.6678,0.6201,0.0976,0.0477,
...
```

`alert` 为非空时在 HTML 报告会标红。

### 11.2 `feature_importance_consensus.csv`

```csv
feature,cn_name,n_groups_in_top20,avg_importance_rank,direction
generic_pa_breakout_quality,突破质量,12,3.2,positive
generic_atr_normalized_range,ATR 标准化区间,11,4.7,positive
regime_trend_strength,趋势强度,10,5.1,positive
generic_volume_anomaly,成交量异动,9,6.8,positive
...
```

跨 group 都进 top20 的 feature = 共识 alpha，下次研究 priority 最高。

---

## 12. `reports/` — 三档报告

### 12.1 `executive.html`（PM / 老板）

只放：
1. 顶部 5 个大数字（年化 / 回撤 / sharpe / 胜率 / 交易笔数）
2. 资金曲线大图
3. 月度热力图
4. 板块横向对比表（5 行）
5. 最近 3 个月业绩对比 vs 上次评估

风格：极简、白底、字号大、不放细节表。

### 12.2 `analyst.html`（研究员）

包含全部 chapter：
1. 业绩摘要
2. 净值 + 回撤
3. 月度收益表（年 × 月）
4. 分板块表现
5. 分品种排名
6. 分周期对比
7. 分信号类型对比
8. Gate funnel 桑基图
9. Block 原因拆解
10. 异常交易 top-20
11. 模型质量（AUC 折线 + 校准曲线）
12. 对标基准
13. 配置快照 + 复现指令

风格：紧凑、字号小、图表密、表格可点击展开。

### 12.3 `brief.md`（群消息版）

300 字以内，可直接复制贴微信群 / 钉钉：

```markdown
🔔 **CTA OOT 评估快报（2026-05-17 07:35，prod）**

🗓️ 期间 2024-01-01 ~ 2026-05-16（28 个月）
💰 净值 +33.4%（年化 +14.3%），最大回撤 -8.2%（已恢复）
📊 月度 sharpe 1.42，月度胜率 67.9%（19/28 月正收益）

🏆 **板块榜**
1. 股指 +22.7%（贡献度 45.3%）
2. 有色 +14.3%
3. 黑色 +9.8%
⚠️ 国债 -1.8%（建议下版本排除）

🔍 **最近 3 个月**：trade_count=178，win_rate=58.3%，
未触发月度熔断，weekly_dd 触发 2 次（均缩仓后恢复）

📁 全量明细：`{output_root}/oot_20260517_073500_prod/`
```

---

## 13. `raw/` — 原始合并产物

供研究员自己 pandas / DuckDB 起飞用：

```
raw/
├── all_trade_details.csv          # 全部成交逐笔（合并所有 group）
├── all_predictions.csv            # 全部预测（含未成交的）
├── all_candidates.csv             # 全部候选
├── all_monthly_returns.csv        # 各 group 的 monthly_returns concat
└── all_features_used.csv          # 所有训练实际用过的 feature 列名 unique 并集
```

跟 `01_aggregate/` 的差别：`raw/` 是 concat 原始数据没做聚合；`01_aggregate/` 是已经按时间桶 + 全 portfolio 聚合后的结果。

---

## 14. `meta/` — 复盘元数据

```
meta/
├── cli_args.txt              # 这次跑的完整命令行
├── git_sha.txt               # 代码版本（含 dirty flag）
├── git_diff.patch            # 如果 dirty，dump 出 unstaged diff
├── config_snapshot.json      # OotEvaluationConfig 完整字段
├── data_snapshot.json        # 数据范围 + 各品种行数 + ranking csv hash
├── pip_freeze.txt            # python 依赖快照
├── python_version.txt        # 3.13.3 之类
├── host_info.txt             # hostname / OS / CPU / 内存
├── start_dt.txt              # 开始时间
├── end_dt.txt                # 结束时间
└── runtime_log.txt           # 这次跑的完整 stdout/stderr
```

**复盘 SLA**：6 个月之后翻出来这个目录，应该能 `bash cta/run.sh reproduce <output_dir>` 一键复现（前提是数据没变）。

---

## 15. 必备图表清单（按重要性）

| 图 | 位置 | 用途 | 风格要求 |
|---|---|---|---|
| 资金曲线（含 drawdown shading） | `00_overview/`, `01_aggregate/`, 各 cluster | 一眼看穿表现 | 蓝线 + 灰阴影回撤段 |
| 月度收益热力图（年 × 月） | `00_overview/`, 各 cluster | 看季节性 / 黑天鹅月 | 红绿渐变（绿=赚） |
| 回撤曲线（underwater chart） | `00_overview/`, `06_drilldown/` | 看回撤深度 + 持续时间 | 红色填充 |
| Gate funnel 桑基图 | `06_drilldown/` | 看候选漏斗 | 多色桑基 |
| 品种 × 月份 PnL 热力图 | `03_by_symbol/` | 找品种季节性 | 红绿渐变 |
| 板块 × 月份热力图 | `02_by_cluster/` | 找板块季节性 | 红绿渐变 |
| 相关性矩阵 | `03_by_symbol/` | 看分散度 | 蓝白红渐变 |
| Rolling sharpe（6m / 12m） | `06_drilldown/` | 看 alpha 稳定性 | 双线对比 |
| 持仓占比时间序列 | `06_drilldown/` | 看资金利用率 | 堆叠面积 |
| Calibration curves（per model） | `09_diagnostics/` | 看概率校准度 | 多 subplot |

---

## 16. 命名约定

- 所有 CSV：`utf-8-sig` 编码（Excel 直接打开不乱码，中国团队必备）
- 所有 PNG：DPI ≥ 150，宽 1600px，纸面打印能看清
- 所有时间戳：本地时区 `+08:00`（不要 UTC，迷惑研究员）
- 所有金额：人民币元，整数（不要 0.01 元粒度）
- 所有百分比 CSV 字段：以小数形式存（0.0823 不是 8.23）；展示层 ×100 转字符串
- 文件名禁止空格 / 中文（CSV 路径用英文 ascii；CSV 内部中文 OK）

---

## 17. 实施 wave（codex 落地建议）

### Wave 1：搭骨架 + 兼容现有产物（1 个 PR）

1. 新建 `cta/model/oot_report_writer.py`（≤500 行）
   - 公开入口 `write_oot_evaluation_report(bundle_dir, run_records, *, run_tag, cfg)`
   - 创建顶层时间戳目录 + 12 个子目录
   - 把现有 `_write_group_pool_runtime_bundle` 产出的 csv 复制 / 软链到 `01_aggregate/`、`02_by_cluster/`
   - 把现有 `group_pool_aggregate.py` 的 monthly/weekly/summary CSV 移到 `01_aggregate/`
2. 写 `00_overview/headline_metrics.csv`、`executive_summary.md`、`brief.md`
3. 在 `_write_group_pool_runtime_bundle` 末尾调用 `write_oot_evaluation_report`
4. 单测：`test_oot_report_writer.py`，构造 fake run_records，断言目录树齐全 + 关键 CSV 字段存在

### Wave 2：分维度子目录（1 个 PR）

5. 实现 `02_by_cluster/`、`03_by_symbol/`、`04_by_interval/`、`05_by_signal_type/`
6. 每个维度的 `_comparison.csv`
7. 单测：构造 3 组 × 2 周期 × 2 signal_type 的合成 trades，断言所有维度切片正确

### Wave 3：drilldown + 模型诊断（1 个 PR）

8. `06_drilldown/gate_funnel.csv` + `block_reason_breakdown.csv` + `drawdown_episodes.csv` + `outlier_trades.csv`
9. `09_diagnostics/auc_per_window.csv`（从现有 `metrics.csv` 转）+ `feature_importance_consensus.csv`
10. 单测覆盖 gate funnel 数学正确性（漏斗每段比例 = 上下游计数比）

### Wave 4：图表生成（1 个 PR）

11. 新建 `cta/model/oot_charts.py`（≤500 行）— matplotlib，无 seaborn 依赖
12. 实现 §15 表里的 10 张图
13. 在 `oot_report_writer.write_oot_evaluation_report` 收尾时调用

### Wave 5：HTML 报告（1 个 PR）

14. 新建 `cta/model/oot_html_report.py`（≤500 行）— jinja2 模板
15. 三个模板：`executive.html.j2`、`analyst.html.j2`、`brief.md.j2`
16. 引用前面生成的 CSV / PNG

### Wave 6：benchmark + meta（1 个 PR）

17. `07_benchmark/` 跟 `cta/feature/macro_feature.py` 已经下载的指数数据对齐
18. `meta/` 全部字段
19. 接入 `cta/run.sh` 末尾的 reproduce 子命令（可选）

---

## 18. 验收清单

- [ ] 每次跑评估都产出一个 `oot_{YYYYMMDD_HHMMSS}_{run_tag}/` 目录
- [ ] 顶层 12 个子目录都存在
- [ ] `00_overview/executive_summary.md` 三句话 + 5 个数字齐全
- [ ] `00_overview/headline_metrics.csv` 17 列字段都有值（缺失为 NaN 不留空）
- [ ] `01_aggregate/` 已存在的 3 个 aggregate CSV 在正确位置
- [ ] `02_by_cluster/_comparison.csv` 行数 == cluster 数
- [ ] `03_by_symbol/_ranking.csv` 按 net_pnl 降序排
- [ ] `06_drilldown/gate_funnel.csv` 漏斗每段 count 单调不增
- [ ] `09_diagnostics/auc_per_window.csv` 每行有 alert 字段（空或非空）
- [ ] `reports/executive.html`、`analyst.html`、`brief.md` 都可打开
- [ ] `meta/git_sha.txt` 非空
- [ ] CSV 编码全部 utf-8-sig（Excel 不乱码）
- [ ] 所有 PNG DPI ≥ 150

---

## 19. 与现有代码的衔接

| 现有产物 | v2 plan 后位置 | 本设计后位置 |
|---|---|---|
| `_aggregate_monthly_metrics.csv`（`group_pool_aggregate.py` 产） | bundle root | `01_aggregate/monthly_metrics.csv` |
| `_aggregate_weekly_metrics.csv` | bundle root | `01_aggregate/weekly_metrics.csv` |
| `_aggregate_summary.csv` | bundle root | `01_aggregate/summary.csv`（+ 复制到 `00_overview/headline_metrics.csv` 子集）|
| `_all_symbol_group_oot_trade_details.csv` | bundle root | `raw/all_trade_details.csv` |
| `_symbol_group_run_manifest.csv` | bundle root | `08_models/manifest.json`（转 json）|
| `symbol_group_details/grp_*/` | bundle root | `02_by_cluster/cluster_*/` |
| per-group `oot_trade_details.csv` | `symbol_group_details/grp_*/` 内 | `02_by_cluster/cluster_*/trade_details.csv` |
| `cluster_registry.json` | `{output_root}/` | `08_models/cluster_registry.json` |

**迁移策略**：Wave 1 不破坏老路径，新目录里放**软链**或**复制**指向老路径。Wave 5 之后老路径下线，只保留新目录。

---

## 20. 不在本次范围

- 实时监控大盘（线上盘中看的，不是 OOT 评估场景）
- 客户专属报告（KYC + 数据脱敏，单独立项）
- 多账户聚合（目前单组合）
- 实时 Web UI（用静态 HTML 足够；如要 web，单独立项接 Streamlit / Gradio）

---

## 21. Follow-up 想法（不在本次实施）

- **MoM 对比**：连续两次评估之间的差异表（trade_count diff / sharpe diff / 新增 outlier trades）
- **A/B 模型对比**：同时跑两套配置，自动拆 alpha 来源
- **客户路演 PPT 自动生成**：把 `00_overview/` 5 张图打包成 PPT
- **告警接入**：max_drawdown > 阈值时自动钉钉/企微推送 brief.md
