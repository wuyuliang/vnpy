# `cta/model` 使用说明

本文说明 3 件事：
1. 如何从原始行情生成训练特征；
2. 如何构建候选训练样本并训练三类模型；
3. 如何在回测/推理中加载并使用模型结果。

---

## 1. 数据与目录约定

- 原始行情输入目录：`cta/data/origin/`
  - `day/`
  - `minute/`
  - `minute5/`
  - `minute15/`
  - `minute30/`
  - `minute60/`
- 通用特征目录：`cta/data/feature/`
- 模型训练样本目录：`cta/data/model_feature/`
- 模型与报告输出目录：`cta/report/backtest/..._model_pipeline/`

---

## 2. 端到端流程

说明：以下命令中的 `RB0` 仅为示例，替换为任意本地可用品种（如 `CU0`/`AU0`）即可运行。

`model_pipeline` 内部执行顺序已固定为：
1. 先生成候选样本；
2. 再拼接候选对应通用特征与模型特征；
3. 再按 train/valid 选参并训练三类模型；
4. 最后仅用 OOT(test) 做评估与收益分档。

实现拆分（便于局部 review）：
- `cta/model/model_pipeline.py`：稳定 CLI 入口（继续支持 `python3 -m cta.model.model_pipeline`）
- `cta/model/dataset/`：候选样本、通用特征拼接、特征筛选、walk-forward split、pool/group-pool 样本组织
- `cta/model/training/`：三段模型、final decision、模型 registry、参数搜索与 AUC gap 约束
- `cta/model/orchestration/`：CLI、单次 pipeline 主流程、多 interval / group-pool 调度
- `cta/model/oot/`：OOT 真实成交评估、gate、intrabar、仓位 sizing、组合约束、block_reason
- `cta/model/reporting/`：OOT 报告、HTML、aggregate、diagnostics、provenance

### Step A：生成通用特征（vn.py 特征）

```bash
# 全频率批量
python3 -m cta.feature.run_all_features --interval all

# 仅 60min + 指定品种
python3 -m cta.feature.run_all_features --interval 60min --symbols RB0
```

### Step B：生成候选事件 + 训练样本（候选特征 + 通用特征拼接）

`cta.model.feature.candidate_training_dataset` 现已支持：
- `--interval` 多值（空格 / 逗号 / 混合写法，自动去重）
- `--top-n-symbols N`（按 `cta/feature/symbols_research_ranking.csv` 的 `research_rank` 取前 N）

```bash
# 单品种 + 单一 interval（保留旧用法）
python3 -m cta.model.feature.candidate_training_dataset \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both \
  --run-tag 20260427

# 单品种 + 多 interval（空格分隔）
python3 -m cta.model.feature.candidate_training_dataset \
  --symbol RB0 \
  --exchange SHFE \
  --interval day 60min 30min 15min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both \
  --run-tag 20260427

# 单品种 + 多 interval（逗号分隔，混合写法）
python3 -m cta.model.feature.candidate_training_dataset \
  --symbol RB0 \
  --exchange SHFE \
  --interval day,60min 30min,15min,5min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both \
  --run-tag 20260427

# topN 品种批量（按 research_rank）
python3 -m cta.model.feature.candidate_training_dataset \
  --top-n-symbols 10 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both \
  --run-tag 20260427
```

说明：
- 多 interval / 多品种是顺序批量执行，单个 interval 失败不会阻断其它 interval。
- 输出目录按 `interval/symbol/run_tag` 隔离，互不覆盖。
- `cta/data/model_feature` 已改为 parquet-only，不再落地 csv。
产物示例：
- `cta/data/model_feature/minute60/RB0/20260427/*_candidate_events.parquet`
- `cta/data/model_feature/minute60/RB0/20260427/*_training_samples.parquet`
- `cta/data/model_feature/minute60/RB0/20260427/*_dataset_summary.parquet`
- `cta/data/model_feature/day/RB0/20260427/*_candidate_events.parquet`

### Step C：训练四层模型（命名可辨识）

训练口径说明：
- `Trade Filter`：使用全量候选样本训练（包含已成交 + 未成交样本）。
  - 当前默认是融合模型：`ensemble_histgb_elasticnet_avg`
  - 输出文件名：`trade_filter.joblib`
- `Regime Classifier`：使用全量候选样本训练（包含已成交 + 未成交样本）。
  - 输出文件名：`regime_classifier.joblib`
- `MFE/MAE`：仅在 `is_executed==1` 样本上训练。
  - 输出文件名：`mfe_mae.joblib`
- `Final Decision Stack`：将前三层输出作为 meta 特征，再训练最终决策模型。
  - meta 特征：`meta_trade_filter_prob / meta_regime_code / meta_pred_mfe_atr / meta_pred_mae_atr / meta_pred_edge_atr / meta_edge_side`
  - 输出文件名：`final_decision_stack.joblib`
- 参数搜索与过拟合控制：
  - 前三类基础模型做参数网格搜索；
  - 仅使用 `train + valid` 进行选参，约束 `abs(train_auc-valid_auc) <= 3%`（默认，可用 `--max-auc-gap` 调整）；
  - `OOT(test)` 严格不参与选参，只用于最终效果评估。
  - `Final Decision Stack` 的 train 侧 meta 特征改为 **time-series OOF** 生成，避免基模型对同一训练样本“原地预测”导致的 stacking 泄露。
  - walk-forward 每个窗口增加样本门槛：`--min-train-samples / --min-valid-samples / --min-test-samples`；
    不满足时该窗口跳过训练，并在 `metrics.csv` 标记 `insufficient_sample=1`。
  - causality 审计新增硬阈值：`--max-unaudited-features`，超过阈值直接失败，防止未审计特征悄悄入模。

OOT 执行评估默认优先使用 `final_decision_score` 作为 gate（可通过配置回退到旧三段 gate）。

#### 通用特征拼接策略（`--generic-mode`）

模型训练特征 = **候选特征 (`feature_*`) + 通用特征 (`generic_*`)**。
通用特征来自 `cta/data/feature/<interval>/<symbol>/*.parquet`，每个 parquet 通常含
约 400 个预先计算好的列（趋势 / 动量 / 波动 / pa_* / 综合评分 / regime 等）。

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `auto`（默认） | 自动取磁盘 parquet 上**所有**数值 / 布尔列（排除 OHLCV / 元数据 / object）作为 `generic_*` 输入 | 想充分利用 `cta/feature/` 的全部产出，让模型自动选择有用特征 |
| `whitelist` | 仅用 18 列窄白名单 `DEFAULT_GENERIC_COLUMNS`（`sma_20 / ema_20 / macd_dif / ... / regime_conf`） | 复现 2026-04 之前的历史结果；或资源受限时减少特征维度 |

CLI:
```bash
# 默认 auto
python3 -m cta.model.model_pipeline --symbol RB0 --interval 60min --start 2018-01-01 --end 2019-12-31

# 锁定 18 列白名单
python3 -m cta.model.model_pipeline --symbol RB0 --interval 60min --generic-mode whitelist

# 显式设置选参约束（示例：AUC gap <= 1.5%）
python3 -m cta.model.model_pipeline --symbol RB0 --interval 60min --max-auc-gap 0.015

# 样本门槛 + 未审计特征门槛（推荐）
python3 -m cta.model.model_pipeline --symbol RB0 --interval 60min \
  --min-train-samples 50 --min-valid-samples 20 --min-test-samples 20 \
  --max-unaudited-features 500
```

程序化：
```python
from cta.model.model_pipeline import run_model_pipeline
run_model_pipeline(symbol="RB0", interval="60min", generic_mode="auto")  # 默认
run_model_pipeline(symbol="RB0", interval="60min", generic_mode="whitelist")
```

`--interval` 既支持单值，也支持**多值数组**（空格 / 逗号分隔皆可，重复值会去重）。
传多个 interval 时，pipeline 会按顺序依次跑每个周期，输出各自的模型目录与报告，
**互不覆盖**；任意单一 interval 报错也不会阻断其余 interval。

`symbol` 支持两种模式：
- 单品种：`--symbol RB0 --exchange SHFE`
- topN 品种：`--top-n-symbols N`（自动从 `cta/feature/symbols_research_ranking.csv` 读取前 N 名）

`POOL` 模式（共享跨品种模型）：
- CLI 加 `--pool` 后，会把 topN 品种样本拼成一个训练集，只产出一套 `POOL_*` 模型。
- **重要变更**：
  - 缺分钟原始数据的品种会跳过（不回退 synthetic）；
  - 缺 `cta/data/feature/<interval>/<symbol>` 通用特征目录时，不中断训练，会自动从候选样本构造 `generic_auto_*` 与 `generic_model_*` 特征继续训练。
- 输出的 `*_pool_members.csv` 会新增 `used_in_training` 列，标识哪些品种实际参与训练。
- 新增 `--min-used-symbols`（默认 `2`）：若实际参与训练的品种数低于阈值会直接报错，避免“披着 POOL 的单品种模型”。
- 训练阶段会自动按 `symbol_cluster` 做样本权重平衡（`sqrt(total_symbols / symbols_in_cluster)`），降低单一板块样本过多导致的偏置。
- 训练前会读取 `cta/feature/causality_manifest.csv`，将显式标记为 `causal=0` 的特征剔除。

`GROUP-POOL` 模式（分组池化）：
- CLI 加 `--group-pool` 后，会先把 ranking 里的品种按 `--group-by` 分类，再按“组 × interval”分别训练。
- 默认 `--group-by tier`（A/B/C/D），也支持：
  - `--group-by cluster`（按 `symbol_cluster_config` 推断板块）
  - 或 ranking csv 任意列名（如 `exchange` / `recommended_stage`）。
- 每个组输出目录会带组名：`..._GRP_<GROUP>_<interval>_..._model_pipeline/`，模型命名可直接看出来源组别。
- 默认会应用 `symbol_disable_manifest` 过滤；若要覆盖 ranking 里的 70+ 全量品种，增加 `--include-disabled-symbols`。
- `--group-min-size` 控制最小组样本数（默认 2），过小组会被跳过。

```bash
# 单一 interval（保留旧用法）
python3 -m cta.model.model_pipeline \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --train-end 2017-12-31 \
  --valid-end 2018-12-31 \
  --window-mode expanding \
  --max-walk-forward-windows 3 \
  --by-signal-type

# rolling 3y/1y/1y（每 1 年滚动一次）
python3 -m cta.model.model_pipeline \
  --symbol RB0 \
  --exchange SHFE \
  --interval day \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --train-end 2014-12-31 \
  --valid-end 2015-12-31 \
  --window-mode rolling \
  --rolling-train-years 3 \
  --rolling-valid-years 1 \
  --rolling-test-years 1 \
  --rolling-step-years 1 \
  --max-walk-forward-windows 6

# 多个 interval 一次跑完（空格分隔）
python3 -m cta.model.model_pipeline \
  --symbol RB0 \
  --exchange SHFE \
  --interval day 60min 30min 15min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --train-end 2017-12-31 \
  --valid-end 2018-12-31

# 多个 interval（逗号分隔，混合写法也支持）
python3 -m cta.model.model_pipeline \
  --symbol RB0 \
  --exchange SHFE \
  --interval day,60min 30min,15min,5min \
  --start 2010-01-01 \
  --end 2019-12-31

# topN 品种批量（按 research_rank 取前 N）
python3 -m cta.model.model_pipeline \
  --top-n-symbols 10 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31

# POOL 共享模型（真实数据优先）
python3 -m cta.model.model_pipeline \
  --top-n-symbols 10 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval 60min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --train-end 2018-12-31 \
  --valid-end 2020-12-31 \
  --window-mode expanding \
  --max-walk-forward-windows 3 \
  --by-signal-type \
  --generic-mode auto \
  --pool \
  --min-used-symbols 2 \
  --seed 20260512

# GROUP-POOL：按 tier 分组，分别训练（覆盖全量 ranking 品种）
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by tier \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --top-n-symbols 0 \
  --include-disabled-symbols \
  --group-min-size 2 \
  --interval day 60min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --train-end 2020-12-31 \
  --valid-end 2023-12-31 \
  --window-mode expanding \
  --max-walk-forward-windows 3 \
  --by-signal-type \
  --generic-mode auto \
  --min-used-symbols 2 \
  --seed 20260515
```

输出示例：
- `*_candidates.csv`
- `*_feature_table.csv`
- `*_predictions.csv`
  - 含 `final_decision_score`（最终决策分）
- `*_metrics.csv`
  - 含 `model=final_decision_stack`
- `*_top10_feature_importance.csv`（每个 signal/window/model 的 Top10 特征重要性，含 `feature_meaning`）
- `*_last_oot_decile_returns.csv`（最后 OOT 集合按模型分十档收益，仅统计已成交样本）
- `*_oot_monthly_returns.csv`（OOT 真实成交按月收益）
- `*_oot_summary.csv`（OOT 真实成交汇总：Sharpe / 回撤 / 年化）
- `*_oot_trade_details.csv`（OOT 真实成交逐笔明细：净值前后、单笔收益、成本等）
  - 新增 `position_notional_after_trade`：每笔交易结束后的组合持仓资金（名义金额）
- `*_throttle_log.csv`（portfolio_logic 风控档位时序日志：equity/drawdown/level/score_threshold）
- `*_oot_position_lifetime.csv`（按 `pos_id` 聚合的持仓生命周期：layer_count/peak_notional）
- `report_*.html`（通过 `cta/report/render` 统一渲染的 OOT 绩效 HTML 报告）
- `oot_{YYYYMMDD_HHMMSS}_{run_tag}/`（结构化 OOT 目录，见 `cta/docs/oot_output.md`）
  - `00_overview/`：`headline_metrics.csv`、`executive_summary.md`
  - `01_aggregate/`：月/周/summary 聚合
  - `02_by_cluster/`、`03_by_symbol/`、`04_by_interval/`、`05_by_signal_type/`
  - `06_drilldown/`：`gate_funnel.csv`、`block_reason_breakdown.csv`、`outlier_trades.csv`
  - `09_diagnostics/auc_per_window.csv`
  - `reports/executive.html`、`reports/analyst.html`、`reports/brief.md`
- `models/<signal_type>/window_xx/*.joblib`
- `models/<signal_type>/window_xx/*_calibration.joblib`
  - `trade_filter_calibration.joblib`
  - `regime_classifier_calibration.joblib`
  - `mfe_mae_calibration.joblib`
  - `final_decision_stack_calibration.joblib`
- `models/<signal_type>/window_xx/<model>_features.csv`（**每个模型的全量特征清单**，按 importance 降序）

OOT 评估参数集中在：
- `cta/config/model_oot_eval_config.py`
- 包含：`max_single_loss_pct`、`trade_filter_gate_mode`、`trade_filter_threshold`、`trade_filter_percentile_threshold`、`use_regime_gate`、`min_pred_edge_atr`、
  `use_roll_cost` / `default_roll_cost_pct_per_year`（展期成本扣减）、
  `use_stacking_gate` / `stacking_score_threshold`（最终决策 gate）、
  `weekly_dd_position_scale_after_breach`（周回撤后缩仓系数）、
  `monthly_max_drawdown_pct`（月回撤硬熔断）等。
- 当前默认：`weekly_max_drawdown_pct=0.03`（3%）。
- 当前生产默认：`trade_filter_gate_mode="cluster_interval_percentile"` 且
  `trade_filter_percentile_threshold=70.0`。即 trade filter 不再用一个全局 raw
  probability 阈值筛掉所有 cluster/interval，而是优先用 `trade_filter_prob_pctl`
  在各自 `cluster+interval` 内做分位数 gate。这个 `_pctl` 必须由训练流程用
  **非 OOT 的 train+valid 预测分布**校准生成，不能在 OOT 评估时用 OOT 自身分布
  现场 rank；缺失 `_pctl` 时 percentile gate 会 fail-closed。可以通过
  `trade_filter_percentile_threshold_by_cluster_interval={"index|day": 65.0}`
  单独放宽或收紧 INDEX/day。若要回退旧口径，设 `trade_filter_gate_mode="raw"`，
  并可用 `trade_filter_raw_threshold_by_cluster_interval={"index|day": 0.45}` 覆盖。

portfolio_logic 运行时开关（默认向后兼容关闭）：
- `use_portfolio_logic_runtime=False`
- 打开后会启用 `portfolio_logic` 模块中的能力（按配置开关）：
  - HTF gate（`blocked_htf_gate`）
  - ranker 机会排序（`blocked_ranker`）
  - risk throttle（`blocked_throttle_halt`）
  - pyramid 分层持仓（`pos_id` / `layer_id`）
  - trailing stop（`trailing_stop_exit_rows`）
- 多 interval（如 `day,60min`）场景下，pipeline 会在各 interval 首轮评估后，
  自动用“跨 interval 合并预测表”重算一次 OOT HTF gate，
  让 `day` 与 `60min` 互相提供趋势状态，避免单 interval 评估出现整批 `htf_missing`。
- 当运行 `--group-pool --use-portfolio-logic-runtime` 时，额外生成上层目录：  
  `*_GROUP_POOL_{GROUP_BY}_{side}_portfolio_logic_runtime/`，用于汇总所有 symbol group 的
  交易明细与每组详细文件索引（`*_all_symbol_group_oot_trade_details.csv` /
  `*_symbol_group_run_manifest.csv` / `symbol_group_details/*/group_detail_manifest.json`）。
  并在同级自动生成 `oot_..._{group}_{side}/` 结构化 OOT 总报告目录。

`*_model_report.md` 会额外打印：
- `Process Steps`：候选生成 → 特征拼接 → 训练 → OOT 评估的全过程
- `Split Diagnostics`：train/valid/test 时间区间、样本数、特征空值统计、IC 统计

训练日志会同步打印每个模型的 Top10 特征重要性（便于快速复盘）。

#### 模型特征清单（部署用）

每个保存的 joblib 模型旁边都会生成一份配套的 `<model>_features.csv`，作为
**模型部署时下游 schema 校验**的权威清单。例如：

```text
models/donchian_breakout/window_00/
├── trade_filter.joblib
├── trade_filter_features.csv         ← rank/feature/importance/feature_meaning/model_kind
├── regime_classifier.joblib
├── regime_classifier_features.csv
├── mfe_mae.joblib
└── mfe_mae_features.csv
```

清单约定：
- 列：`rank`（1..N）/ `feature` / `importance` / `feature_meaning` / `model_kind`
- 排序：`importance` 降序、`feature` 字典序（同分时稳定）
- **行数 = 训练用过的全部特征数**（不是 top10）
- importance 来源：模型原生 `feature_importances_`（RF / HGB+permutation 退化）
- model_kind = `dummy` / `hist_gradient_boosting` / `random_forest` / `legacy_no_kind`

下游推理建议：
```python
from cta.model.trade_filter_model import TradeFilterModel
import pandas as pd

m = TradeFilterModel.load(Path("models/donchian_breakout/window_00/trade_filter.joblib"))
manifest = pd.read_csv("models/donchian_breakout/window_00/trade_filter_features.csv")
required_features = manifest["feature"].astype(str).tolist()

# 严格按训练时的特征清单 + 顺序做推理（避免列漏 / 列错位 / 列穿越）
prob = m.predict_proba(df_runtime, feature_columns=required_features)
```

若启用 cluster 路由推理（`ClusterModelRegistry`），会自动加载同目录下
`*_calibration.joblib`，并在输出里追加对应 `_pctl` 百分位列（例如 `trade_filter_prob_pctl`）。

mfe_mae 模型在该 signal/window 没有成交样本（`_train_mfe_mae_or_skip` 返回 None）
时 joblib 不写出，对应 `mfe_mae_features.csv` 也不会写出（无孤儿文件）。

---

## 3. 常用命令示例

> **CLI 差异说明**：`cta.strategy.baseline_skill_suite` 的 CLI 接受
> `--periods-per-year`（默认按 interval 自动推断）；`cta.model.model_pipeline`
> 不需要这个参数，pipeline 内部 `_compute_metrics` 会自动调
> `suggest_periods_per_year(interval_norm)` 推断。

### 3.1 不同周期快速跑

```bash
# day
python3 -m cta.model.model_pipeline --symbol RB0 --exchange SHFE --interval day --start 2010-01-01 --end 2019-12-31

# 30min
python3 -m cta.model.model_pipeline --symbol RB0 --exchange SHFE --interval 30min --start 2010-01-01 --end 2019-12-31

# 15min
python3 -m cta.model.model_pipeline --symbol RB0 --exchange SHFE --interval 15min --start 2010-01-01 --end 2019-12-31

# 5min
python3 -m cta.model.model_pipeline --symbol RB0 --exchange SHFE --interval 5min --start 2010-01-01 --end 2019-12-31

# 1min
python3 -m cta.model.model_pipeline --symbol RB0 --exchange SHFE --interval min --start 2010-01-01 --end 2019-12-31

# 一次跑全部 6 个 interval（按顺序，每个 interval 输出独立目录）
python3 -m cta.model.model_pipeline \
  --symbol RB0 --exchange SHFE \
  --interval day 60min 30min 15min 5min min \
  --start 2010-01-01 --end 2019-12-31

# 程序化调用：使用 run_model_pipeline_multi 一次跑多个 interval
python3 - <<'PY'
from cta.model.model_pipeline import run_model_pipeline_multi

results = run_model_pipeline_multi(
    symbol="RB0",
    exchange="SHFE",
    intervals=("day", "60min", "30min", "15min"),
    start_date="2010-01-01",
    end_date="2019-12-31",
)
for r in results:
    print(r.report_path)
PY
```

### 3.2 滑动窗口 walk-forward

```bash
python3 -m cta.model.model_pipeline \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --train-end 2017-12-31 \
  --valid-end 2018-12-31 \
  --window-mode sliding \
  --max-walk-forward-windows 4
```

### 3.3 基线策略先产候选，再训模型

```bash
python3 -m cta.strategy.baseline_skill_suite \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both
```

---

## 4. 预测使用（离线）

示例：加载 Trade Filter 模型并打分

```python
from pathlib import Path
import pandas as pd
from cta.model.trade_filter_model import TradeFilterModel
from cta.model.pipeline_dataset_prep import _select_feature_columns

model_path = Path("cta/report/backtest/<run>/models/donchian_breakout/window_00/trade_filter.joblib")
model = TradeFilterModel.load(model_path)

df = pd.read_csv("cta/report/backtest/<run>/<...>_feature_table.csv")
# 注意：必须复用 pipeline 的白名单选列函数，简单 prefix 抓 cols 会把
# `feature_label` 这类字符串列也带上，再被 ColumnTransformer 强转 NaN，
# 推理分布会跟训练对不上。
df_clean, feature_cols = _select_feature_columns(df)
score = model.predict_proba(df_clean, feature_columns=feature_cols)
print(score[:10])
```

按“最后 OOT 集合”分十档统计收益（直接用已产出的 predictions）：

```bash
python3 - <<'PY'
from pathlib import Path
import pandas as pd
from cta.model.pipeline_diagnostics import _build_last_oot_decile_table

p = Path("cta/report/backtest/<run>/<...>_predictions.csv")
df = pd.read_csv(p)
dec = _build_last_oot_decile_table(df, bins=10)
out = p.parent / "<...>_last_oot_decile_returns.csv"
dec.to_csv(out, index=False, encoding="utf-8-sig")
print(dec)
PY
```

---

## 5. 测试命令（按新目录布局）

```bash
# 策略测试
python3 -m unittest cta.strategy.tests.test_skill_tight_range_strategy -v
python3 -m unittest cta.strategy.tests.test_skill_tight_range_backtest_rb0 -v
python3 -m unittest cta.strategy.tests.test_baseline_skill_suite -v

# 模型测试
python3 -m unittest cta.model.tests.test_models_core -v
python3 -m unittest cta.model.tests.test_model_pipeline_part01 -v
python3 -m unittest cta.model.tests.test_model_pipeline_part02 -v
python3 -m unittest cta.model.tests.test_model_pipeline_part03 -v
python3 -m unittest cta.model.tests.test_model_pipeline_part04 -v
python3 -m unittest cta.model.tests.test_model_pipeline_part05 -v
python3 -m unittest cta.model.tests.test_model_pipeline_part06 -v
python3 -m unittest cta.model.tests.test_model_pipeline_part07 -v

# 特征拼接与样本构建测试
python3 -m unittest cta.model.feature.tests.test_model_feature_builder -v
python3 -m unittest cta.model.feature.tests.test_candidate_training_dataset -v
```

---

## 6. 常见问题

1. `load_bars` 找不到数据目录  
确认原始数据已迁移到 `cta/data/origin/<interval>/`。

2. 样本很多但正样本少  
先检查 `candidate_status/sample_status` 分布，再检查 `label_class` 阈值口径。

3. 某些窗口模型退化为 dummy  
查看 `metrics.csv` 的 `model_kind` 列，通常是样本量或标签多样性不足。
