# CTA 端到端运行手册（`run.md`）

本文给出从数据准备到模型训练、离线评估、仿真与实盘运维的可复制命令。

约定：
- 在仓库根目录执行：`/Users/wuyuliang/code/vnpy`
- Python 使用 `python3`
- 示例品种使用 `RB0`，可替换为其它品种

> **服务器端一把跑**：本文 §1–§4 的命令已经被整理成 [`cta/run.sh`](run.sh)，支持
> 按 `cta/feature/symbols_research_ranking.csv` 的 top-N 自动跑完
> 数据下载 → 校验 → 特征 → 候选样本 → per-symbol 训练 → POOL 池化训练。常用：
>
> ```bash
> export TUSHARE_TOKEN=...
> TOP_N=77 RUN_TAG=$(date +%Y%m%d) GLOBAL_SEED=${RUN_TAG} bash cta/run.sh all  # 一把全跑（覆盖商品+金融）
> bash cta/run.sh data                                        # 只跑数据下载
> bash cta/run.sh data_index_bond                             # 只补股指+国债+指数参考数据
> bash cta/run.sh spread_arbitrage                            # 只跑 spread 套件 + OOT 字段回归
> bash cta/run.sh -h                                          # 完整参数说明
> ```
>
> 仿真 / 实盘部分（§6/§7）涉及账号、长进程与告警 webhook，仍以独立脚本启动。

---

## 推荐流程（候选 → 特征 → 训练 → OOT）

当前 `cta.model.model_pipeline` 已按以下顺序执行并写入报告：
1. 生成候选样本（candidate events）
2. 拼接候选对应通用特征 + 模型训练特征
3. 训练三类模型（train/valid 选参）
4. OOT(test) 评估并输出报告

OOT 风控默认值提示：
- `weekly_max_drawdown_pct=0.03`（3%）

报告中会额外打印：
- train/valid/test 的时间周期与样本数量
- 特征空值统计（均值/最大值/空值列数量）
- 特征 IC 统计（label/regime/return）

最简单最全的两条命令：
nohup python3 -m cta.feature.run_all_features   --interval day 60min 30min 15min 5min min   --start 2010-01-01 --end 2025-12-31   --workers 4   > 20260522_features.out 2>&1 &

nohup python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --group-min-size 2 \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day 60min 30min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --train-end 2020-12-31 \
  --valid-end 2023-12-31 \
  --window-mode expanding \
  --max-walk-forward-windows 3 \
  --by-signal-type \
  --generic-mode auto \
  --use-portfolio-logic-runtime \
  --min-used-symbols 2 \
  --enable-cross-sectional-rotation \
  --cross-sectional-enabled-cells "*|day" \
  --enable-trailing-take-profit \
  --trailing-tp-enabled-cells "precious|day" \
  --enable-profit-aware-horizon \
  --profit-aware-horizon-enabled-cells "precious|day" \
  --enable-trend-aware-trade-filter \
  --trend-aware-trade-filter-enabled-cells "precious|day" \
  --seed 2026052208 \
  > 2026052310.out 2>&1 &

最小运行命令：

```bash
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
  --by-signal-type \
  --generic-mode auto \
  --min-train-samples 50 \
  --min-valid-samples 20 \
  --min-test-samples 20 \
  --max-unaudited-features 500 \
  --seed 20260512
```

---

## 0. 环境准备

```bash
cd /Users/wuyuliang/code/vnpy
python3 --version
```

分钟数据下载依赖 `TUSHARE_TOKEN`：

```bash
export TUSHARE_TOKEN="你的token"
```

快速自检（可选）：

```bash
python3 -m pytest cta/strategy/tests cta/model/tests cta/model/feature/tests -q
```

---

## 1. 原始数据下载与校验

### 1.1 下载全频率（按研究排名）

```bash
python3 -m cta.data_code.download_all \
  --intervals day minute60 minute30 minute15 minute5 minute \
  --max-rank 10 \
  --workers 4 \
  --rate-limit 450
```

### 1.1.1 只下载股指 + 国债 + 指数参考数据（补齐缺失）

方式 A（推荐，直接用 run.sh 专项动作）：

```bash
export TUSHARE_TOKEN=...
bash cta/run.sh data_index_bond
```

方式 B（直接调用 download_all）：

```bash
python3 -m cta.data_code.download_all \
  --intervals day minute60 minute30 minute15 minute5 minute \
  --only-symbols IF0 IH0 IC0 IM0 T0 TF0 TS0 \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --workers 4 \
  --rate-limit 450 \
  --include-financial \
  --include-index \
  --build-macro
```

### 1.2 只扩展分钟级（推荐日常增量）

```bash
python3 -m cta.data_code.expand_minute \
  --max-rank 10 \
  --intervals minute60 minute30 minute15 minute5 minute \
  --workers 4 \
  --rate-limit 450 \
  --validate-out cta/report/data/$(date +%Y%m%d)_minute_validate.csv
```

### 1.3 数据完整性校验

```bash
python3 -m cta.cli validate --interval day --max-rank 10 --out cta/report/data/$(date +%Y%m%d)_day_validate.csv
python3 -m cta.cli validate --interval minute60 --max-rank 10 --out cta/report/data/$(date +%Y%m%d)_minute60_validate.csv
```

---

## 2. 生成通用特征（`cta/data/feature`）

### 2.1 全量频率批量

```bash
python3 -m cta.feature.run_all_features \
  --interval all \
  --max-rank 10 \
  --workers 4
```

### 2.2 指定品种 + 指定频率

```bash
python3 -m cta.feature.run_all_features \
  --interval 60min 30min 15min 5min min day \
  --symbols RB0 \
  --start-date 2010-01-01 \
  --end-date 2019-12-31 \
  --workers 4
```

### 2.3 仅生成截面特征（可选）

```bash
python3 -m cta.feature.run_all_features --interval all --cross-section
```

## 3. 生成候选样本（`cta/data/model_feature`，parquet-only）

### 3.1 规则 baseline 先跑一遍（支持 topN + 多 interval 候选机会）

按 `cta/feature/symbols_research_ranking.csv` 取前 N 个品种，批量生成多个周期的候选机会：

```bash
python3 -m cta.strategy.baseline_skill_suite \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day,60min 30min,15min,5min,min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --trade-side-mode both
```

单品种版本（保留旧用法）：

```bash
python3 -m cta.strategy.baseline_skill_suite \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both
```

### 3.2 截面动量轮动（已正式合流）

`cross_sectional_momentum_rotation` 目前先提供以下可复用边界：
- `cta.feature.cross_sectional_rank`：多品种动量分数与 cluster 内排名；
- `cta.strategy.cross_sectional_momentum_rotation`：rebalance candidate 生成；
- `cta.portfolio_logic.cross_sectional_rotation_executor`：candidate 转组合层 intent；
- OOT trade-filter gate 默认对 `signal_type=cross_sectional_momentum` bypass。

先用下面命令验证 phase-1 行为：

```bash
python3 -m pytest -q \
  cta/config/tests/test_cross_sectional_rotation_config.py \
  cta/feature/tests/test_cross_sectional_rank.py \
  cta/strategy/tests/test_cross_sectional_momentum_rotation.py \
  cta/portfolio_logic/tests/test_cross_sectional_rotation_executor.py \
  cta/model/tests/test_bull_mode_trade_filter_gate.py
```

当前行为（2026-05-22 起）：
- `group-pool + day` 训练时，`cross_sectional_momentum` 候选会自动并入 pool 候选与特征表；
- 非 `day` interval 默认不并入，避免分钟级噪音导致候选分布劣化；
- 仍保留原有 baseline 候选，不会覆盖或替换既有 signal_type。

全量 day grouped OOT（top-77）可直接运行：

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --group-min-size 2 \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2020-12-31 --valid-end 2023-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --by-signal-type --generic-mode auto \
  --use-portfolio-logic-runtime \
  --min-used-symbols 2 \
  --seed 20260522
```

### 3.3 跨品种/跨期价差套利专项校验（P3）

`run.sh` 已接入 `step_spread_arbitrage`，用于做 spread 模块的最小全链路回归：
- spread 特征 / 策略 / executor / 配置单测；
- OOT 交易明细字段透传回归（`spread_pair_key`、`spread_side`、`spread_leg_id`、`spread_zscore_at_entry`、`spread_zscore_at_exit`、`spread_pnl_pct`）。

命令：

```bash
bash cta/run.sh spread_arbitrage
```

### 3.4 候选事件 + 训练样本拼接（支持 topN + 多 interval）

```bash
python3 -m cta.model.feature.candidate_training_dataset \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day 60min 30min 15min 5min min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --trade-side-mode both \
  --run-tag $(date +%Y%m%d)
```

> `--interval` 接空格分隔或逗号分隔；混用也可被解析，但建议统一空格。

单品种版本：

```bash
python3 -m cta.model.feature.candidate_training_dataset \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both \
  --run-tag $(date +%Y%m%d)
```

---

## 4. 训练模型 + 离线评估

> 当前 pipeline 一次性 fit 7 个模型：4 个核心（`trade_filter` / `regime_classifier` /
> `mfe_mae` / `final_decision_stack`）+ 3 个 2026-05-21 引入的新模型
> （`bull_regime_strength` / `trend_persistence` / `pyramid_eligibility`，详见 §4.4.5）。
> 所有 7 个模型在每次 `cta.model.model_pipeline` 命令里自动联动训练，**无需新增 CLI flag**。
>
> **§4 目录速查**：
>
> - §4.1 单品种训练（60min）
> - §4.2 topN 品种 + 多周期批量训练
>   - §4.2.0 新增参数说明
>   - §4.2.1 多 symbol 池化训练（`--pool`）
>   - §4.2.2 多 symbol 分组池化训练（`--group-pool`）
> - §4.3 离线评估结果快速查看
> - **§4.4 牛市增强闭环命令（含 3 新模型）**
>   - §4.4.1 生成牛市增强候选
>   - §4.4.2 训练 + OOT（cluster 分组池化）
>   - §4.4.3 `cluster|interval|side|bull_mode` 阈值（程序化）
>   - §4.4.4 快速核对增强链路
>   - **§4.4.5 新增 3 训练模型在 day / 60min / 30min 三个频率上的联动命令**
> - §4.5 模型 → 回测 中间环节

训练会输出：
- `*_metrics.csv`
- `*_predictions.csv`
- `*_top10_feature_importance.csv`
- `*_last_oot_decile_returns.csv`
- `models/**.joblib` 与对应特征清单 `*_features.csv`

### 4.1 单品种训练（60min）

```bash
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
  --by-signal-type \
  --generic-mode auto \
  --seed 20260512
```

### 4.2 topN 品种 + 多周期批量训练（每个 symbol 各训一个模型）

```bash
python3 -m cta.model.model_pipeline \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day 60min 30min 15min 5min min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --train-end 2020-12-31 \
  --valid-end 2023-12-31 \
  --window-mode expanding \
  --max-walk-forward-windows 3 \
  --by-signal-type \
  --generic-mode auto \
  --seed 20260512
```

### 4.2.0 新增参数说明（用于清理 docs_sync 警告）

下面这些参数是 `cta.model.model_pipeline` / `pipeline_cli` 新增且常用的控制项：

- `--max-auc-gap`：训练集与验证集 AUC 最大允许差（防止过拟合选参）。
- `--max-valid-test-gap`：valid 与 test AUC 差异告警阈值，超阈值写入告警文件。
- `--no-by-signal-type`：关闭按 `signal_type` 分模型，改为混合训练。
- `--only-clusters`：`--group-pool` 模式下仅跑指定 cluster（如 `index`、`bond`）。
- `--output-root`：指定输出根目录（覆盖默认 `cta/backtest`）。
- `--rolling-train-years`：`rolling` 模式下 train 窗口长度（年）。
- `--rolling-valid-years`：`rolling` 模式下 valid 窗口长度（年）。
- `--rolling-test-years`：`rolling` 模式下 test 窗口长度（年）。
- `--rolling-step-years`：`rolling` 模式窗口每次向前滚动步长（年）。
- `--top-feature-alert-pct`：单特征重要度占比告警阈值（写入 suspect feature 报告）。
- `--enable-trailing-take-profit` + `--trailing-tp-enabled-cells`：开启盈利仓位追踪止盈（A 模块）。
- `--enable-profit-aware-horizon` + `--profit-aware-horizon-enabled-cells`：开启盈利后持仓期放宽（C 模块）。
- `--enable-trend-aware-trade-filter` + `--trend-aware-trade-filter-enabled-cells`：开启趋势下 trade_filter 阈值松绑（B 模块）。

示例（rolling + group_pool + cluster 过滤）：

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --only-clusters index bond \
  --interval day 60min \
  --window-mode rolling \
  --rolling-train-years 3 \
  --rolling-valid-years 1 \
  --rolling-test-years 1 \
  --rolling-step-years 1 \
  --max-auc-gap 0.03 \
  --max-valid-test-gap 0.10 \
  --top-feature-alert-pct 0.50 \
  --no-by-signal-type \
  --output-root cta/backtest/custom_run \
  --seed 20260519
```

示例（只在 `precious|day` 灰度启用 A/B/C）：

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --only-clusters precious \
  --interval day \
  --start 2018-01-01 --end 2025-12-31 \
  --train-end 2021-12-31 --valid-end 2023-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --use-portfolio-logic-runtime \
  --enable-trailing-take-profit \
  --trailing-tp-enabled-cells precious|day \
  --enable-profit-aware-horizon \
  --profit-aware-horizon-enabled-cells precious|day \
  --enable-trend-aware-trade-filter \
  --trend-aware-trade-filter-enabled-cells precious|day \
  --seed 20260523
```

### 4.2.1 多 symbol **池化**训练（一个共享模型）

day 等低频 interval 单 symbol 样本可能 < 200 笔，统计不显著。加 ``--pool`` 把
top-N 品种的样本合并训出**一个**共享模型，跨品种泛化更稳：

```bash
python3 -m cta.model.model_pipeline \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day 60min \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2020-12-31 --valid-end 2023-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --by-signal-type --generic-mode auto \
  --pool                                    # ← 关键 flag
  --min-used-symbols 2                      # ← 至少 2 个真实品种参与训练
  --seed 20260512
```

输出目录：``cta/backtest/{run_date}_POOL_{interval}_{side}_model_pipeline/``，
内含：
- ``..._pool_members.csv`` — 参与池化的 (symbol, exchange) 列表，含 ``used_in_training`` 标识
- ``..._feature_table.csv`` — 拼接后的样本（含 ``symbol`` 列保留来源）
- ``..._metrics.csv`` / ``..._predictions.csv`` / ``models/*.joblib``
- ``..._oot_trade_details.csv`` — 逐笔成交（含 `pos_id/layer_id`、`throttle_level_at_entry`）
- ``..._throttle_log.csv`` — 风控档位时序（drawdown/level/score_threshold）
- ``..._oot_position_lifetime.csv`` — 按 `pos_id` 聚合的持仓生命周期
- ``report_*.html`` — 统一 `cta/report/render` HTML 绩效报告（OOT 真实成交净值口径）

**应用模型时与单 symbol 完全一致**：
```python
from cta.live.model_filter import make_trade_filter
from cta.live.online_feature import OnlineFeatureLoader

# 跨品种共享同一个 POOL 模型
adapter.order_filter = make_trade_filter(
    "cta/backtest/{POOL_run}/models/trade_filter_xxx.joblib",
    threshold=0.55,
    feature_provider=OnlineFeatureLoader(),    # 推理时按 vt_symbol 取该品种特征
)
```

注意事项：
1. 池化训练默认只使用**真实本地数据**；缺失分钟原始数据的品种会跳过（不回退 synthetic）。
   但若仅缺 `cta/data/feature/<interval>/<symbol>` 通用特征目录，pipeline 会自动补
   候选衍生的 `generic_auto_*` 与 `generic_model_*` 特征继续训练，不会中断。
   可在 ``..._pool_members.csv`` 里查看 ``used_in_training``。
   若 `used_in_training < --min-used-symbols`，pipeline 会直接报错，防止误把单品种结果当作池化模型。
2. 池化训练要求各 symbol 的特征列**完全一致**（来自同一份 ``prepare_master_feature_frame``
   + ``cta/data/feature/`` 离线 parquet schema）；缺特征的 symbol 自动跳过，不阻塞整体。
3. 单 symbol 任务规模不足时（如 day interval RB0 仅 ~150 笔候选）才推荐池化；
   分钟级单品种已有足够样本时不必。
4. ``--pool`` 与 ``--top-n-symbols`` 配合使用；只用 ``--symbol RB0`` 加 ``--pool``
   退化为单品种（与不加 ``--pool`` 等价但路径多一层 ``POOL`` 目录）。

### 4.2.2 多 symbol **分组池化**训练（每组一个共享模型）

当 70+ 品种希望“先分类再训练”时，用 `--group-pool`：

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --top-n-symbols 0 \
  --include-disabled-symbols \
  --group-min-size 2 \
  --interval day 60min \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2020-12-31 --valid-end 2023-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --by-signal-type --generic-mode auto \
  --min-used-symbols 2 \
  --seed 2026051520
```

要点：
- `--group-by tier`：按 ranking 的 `tier`（A/B/C/D）分组。
- `--group-by cluster`：按 `cta/config/symbol_cluster_config.py` 的板块映射分组。
- 也可传 ranking 的其他列名，如 `exchange` / `recommended_stage`。
- 输出目录按组区分：`..._GRP_<GROUP>_<interval>_..._model_pipeline/`。
- 预测文件同目录下的 `*_predictions.csv`，直接就是该组模型的离线预测结果。
- 当同时开启 `--use-portfolio-logic-runtime` 时，会额外生成一个**上层聚合目录**：  
  `cta/backtest/{run_tag}_GROUP_POOL_{GROUP_BY}_{side}_portfolio_logic_runtime/`
  - `*_all_symbol_group_oot_trade_details.csv`：所有 symbol group 的逐笔交易明细聚合表
  - `*_symbol_group_run_manifest.csv`：每个 group/interval 对应的模型目录与源文件路径
  - `symbol_group_details/*/group_detail_manifest.json`：每个组的细化文件索引（含模型路径）
  - 同级新增结构化 OOT 总报告目录：`oot_{YYYYMMDD_HHMMSS}_{group}_{side}/`
    - `00_overview/headline_metrics.csv`
    - `01_aggregate/monthly_metrics.csv|weekly_metrics.csv|summary.csv`
    - `02_by_cluster/_comparison.csv`
    - `06_drilldown/gate_funnel.csv|block_reason_breakdown.csv`
    - `reports/executive.html|analyst.html|brief.md`
`day + 60min + 30min` 一把跑：

```bash
nohup python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --group-min-size 2 \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day 60min 30min \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2020-12-31 --valid-end 2023-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --by-signal-type --generic-mode auto \
  --use-portfolio-logic-runtime \
  --min-used-symbols 2 \
  --seed 20260521 > 20260521.out 2>&1 &
```

### 4.3 离线评估结果快速查看

```bash
ls -lah cta/backtest/*_model_pipeline/
```

查看最后 OOT 十分位收益（只统计已成交样本）：

```bash
cat cta/backtest/*_model_pipeline/*_last_oot_decile_returns.csv
```

查看 OOT 真实成交月收益与 Sharpe 汇总：

```bash
cat cta/backtest/*_model_pipeline/*_oot_monthly_returns.csv
cat cta/backtest/*_model_pipeline/*_oot_summary.csv
cat cta/backtest/*_model_pipeline/*_oot_trade_details.csv
cat cta/backtest/*_model_pipeline/*_throttle_log.csv
cat cta/backtest/*_model_pipeline/*_oot_position_lifetime.csv
ls -lah cta/backtest/*_model_pipeline/report_*.html

# group-pool + portfolio_logic runtime 的上层聚合目录
ls -lah cta/backtest/*_GROUP_POOL_*_portfolio_logic_runtime/
cat cta/backtest/*_GROUP_POOL_*_portfolio_logic_runtime/*_all_symbol_group_oot_trade_details.csv
cat cta/backtest/*_GROUP_POOL_*_portfolio_logic_runtime/*_symbol_group_run_manifest.csv

# 新版结构化 OOT 报告目录
ls -lah cta/backtest/oot_*_*/
cat cta/backtest/oot_*_*/00_overview/headline_metrics.csv
cat cta/backtest/oot_*_*/06_drilldown/gate_funnel.csv
```

OOT 绩效参数配置文件：
- `cta/config/model_oot_eval_config.py`
- 关键参数示例：
  - `max_single_loss_pct = 0.002`（单笔最大亏损，默认 0.2%）
  - `trade_filter_gate_mode` / `trade_filter_percentile_threshold` / `trade_filter_threshold` / `use_regime_gate` / `min_pred_edge_atr`（各模型过滤阈值）
  - `use_portfolio_logic_runtime`（是否启用组合层运行时）
  - `portfolio_logic.enable_*`（HTF gate / ranker / trailing / pyramid / throttle 分项开关）

trade filter 默认使用 `cluster_interval_percentile`：按 `cluster+interval`
内的分位数阈值筛选，避免用一个全局 raw probability 阈值误杀
`INDEX/day`。这里的 `trade_filter_prob_pctl` 由训练流程用非 OOT 的
`train+valid` 预测分布校准后写入 `*_predictions.csv`；OOT 评估不会再用
OOT 自身分布现场 rank，缺失 `_pctl` 时 percentile gate 会 fail-closed。
常用配置例子：

```python
from dataclasses import replace
from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG

cfg = replace(
    DEFAULT_OOT_EVAL_CONFIG,
    trade_filter_percentile_threshold_by_cluster_interval={"index|day": 65.0},
)

raw_cfg = replace(
    DEFAULT_OOT_EVAL_CONFIG,
    trade_filter_gate_mode="raw",
    trade_filter_raw_threshold_by_cluster_interval={"index|day": 0.45},
)
```

`*_metrics.csv` 内含 IC / Sharpe / hit-rate 等离线指标，是判断模型是否值得进入回测阶段的门槛。
经验阈值：``oos_ic > 0.02`` 且 ``oos_hit_rate > 0.52`` 才推到下一步。

---

### 4.4 牛市增强闭环命令（已接入版本）

这部分对应 `cta/docs/bull_market_return_enhancement_strategy.md` 当前已经落地的能力：
- 新增 3 类 baseline 信号：`trend_acceleration_breakout` / `bull_pullback_continuation` / `bull_volatility_contraction_breakout`
- 训练阶段新增 3 个牛市模型输出列：`bull_strength_score`、`hold_extend_score`、`pyramid_add_score`
- OOT gate 支持 `cluster|interval|side|bull_mode` 场景阈值

#### 4.4.1 生成牛市增强候选（topN + 多周期）

```bash
python3 -m cta.strategy.baseline_skill_suite \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day 60min 30min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --trade-side-mode both
```

#### 4.4.2 训练 + OOT（按 cluster 分组池化，接 portfolio runtime）

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --top-n-symbols 77 \
  --interval day 60min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --train-end 2020-12-31 \
  --valid-end 2023-12-31 \
  --window-mode expanding \
  --max-walk-forward-windows 3 \
  --by-signal-type \
  --generic-mode auto \
  --use-portfolio-logic-runtime \
  --min-used-symbols 2 \
  --seed 20260520
```

#### 4.4.3 指定 `cluster|interval|side|bull_mode` 阈值（程序化）

CLI 目前不直接暴露该参数，使用 Python 入口传 `oot_eval_config`：

```bash
python3 - <<'PY'
from dataclasses import replace
from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG
from cta.model.orchestration.pipeline_run import run_model_pipeline

cfg = replace(
    DEFAULT_OOT_EVAL_CONFIG,
    use_portfolio_logic_runtime=True,
    trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode={
        "index|day|long|attack": 65.0,
        "index|day|short|attack": 85.0,
        "index|60min|long|attack": 60.0,
    },
)

run_model_pipeline(
    symbol="RB0",
    exchange="SHFE",
    interval="day",
    start_date="2010-01-01",
    end_date="2025-12-31",
    train_end="2020-12-31",
    valid_end="2023-12-31",
    oot_eval_config=cfg,
    seed=20260520,
)
PY
```

#### 4.4.4 快速核对增强链路是否生效

```bash
LATEST_RUN=$(ls -td cta/backtest/*_model_pipeline | head -n 1)
echo "$LATEST_RUN"
python3 - <<'PY'
from pathlib import Path
import pandas as pd
run_dir = Path(sorted(Path("cta/backtest").glob("*_model_pipeline"))[-1])
pred = pd.read_csv(next(run_dir.glob("*_predictions.csv")))
trd = pd.read_csv(next(run_dir.glob("*_oot_trade_details.csv")))
print("pred cols:", [c for c in ["bull_strength_score","bull_mode","hold_extend_score","pyramid_add_score"] if c in pred.columns])
print("trade cols:", [c for c in ["bull_strength_proxy","bull_mode","trade_filter_gate_threshold"] if c in trd.columns])
PY
```

#### 4.4.5 新增 3 个训练模型在 day / 60min / 30min 三个频率上的联动命令

2026-05-21 起 `cta.model.orchestration.pipeline_run.py:174-179` 在每次训练时自动 fit
下面 3 个模型（**不需要新增 CLI flag**，跟随 trade_filter / regime / mfe_mae / final_decision
一起跑）：

| 新模型 | 类位置 | 写入 predictions.csv 的列 | 主战场 interval |
|---|---|---|---|
| `BullRegimeStrengthModel` | [bull_regime_strength_model.py](../model/training/bull_regime_strength_model.py) | `bull_strength_score` / `bull_mode` | **day**（trend regime 判定） |
| `TrendPersistenceModel` | [trend_persistence_model.py](../model/training/trend_persistence_model.py) | `hold_extend_score` / `recommended_horizon_extension_bars` | **60min**（horizon 延长） |
| `PyramidEligibilityModel` | [pyramid_eligibility_model.py](../model/training/pyramid_eligibility_model.py) | `pyramid_add_score` / `pyramid_size_mult` | **30min**（pyramid 加仓） |

所以**为验证新 3 模型完整闭环，必须在 day / 60min / 30min 三个频率各跑一遍**。
推荐用 `--group-pool` + `--use-portfolio-logic-runtime`，因为 `hold_extend_score`
和 `pyramid_size_mult` 只在 portfolio_logic 运行时被消费（见 [trailing_exit.py:248-266](../portfolio_logic/trailing_exit.py) 和 [pipeline_oot_evaluation.py:487-501](../model/oot/pipeline_oot_evaluation.py)）。

#### day（trend filter 主战场 — INDEX 2024 bias 治本场景）

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --group-min-size 2 \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2020-12-31 --valid-end 2023-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --by-signal-type --generic-mode auto \
  --use-portfolio-logic-runtime \
  --min-used-symbols 2 \
  --seed 20260521
```

#### 60min（horizon 延长 / mfe_mae 主战场）

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --group-min-size 2 \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval 60min \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2020-12-31 --valid-end 2023-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --by-signal-type --generic-mode auto \
  --use-portfolio-logic-runtime \
  --min-used-symbols 2 \
  --seed 20260521
```

#### 30min（pyramid 加仓主战场）

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --group-min-size 2 \
  --top-n-symbols 77 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval 30min \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2020-12-31 --valid-end 2023-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --by-signal-type --generic-mode auto \
  --use-portfolio-logic-runtime \
  --min-used-symbols 2 \
  --seed 20260521
```

#### 三 interval 一把跑 + 自动校验（用 `run.sh bull_models`）

```bash
# 默认覆盖 day 60min 30min
TOP_N=77 RUN_TAG=20260521 bash cta/run.sh bull_models

# 或自定义 interval 列表（注意需用空格分隔）
BULL_MODELS_INTERVALS="day 60min" bash cta/run.sh bull_models
```

`run.sh:step_bull_models` 在每个 interval 训练完毕后会自动跑一段 Python 校验脚本，
读最新 `*_predictions.csv` 并断言以下 6 列**全部存在**：

```
bull_strength_score, bull_mode,
hold_extend_score, recommended_horizon_extension_bars,
pyramid_add_score, pyramid_size_mult
```

任一缺失会以非零退出码报错并提示 "**3 new models pipeline broken at this interval**"。

#### 各 interval 下应观察到的关键 OOT 变化

| interval | 关键变化点 | 验证目标 |
|---|---|---|
| **day** | `pred` 表多 6 个新列；details.csv `bull_mode` 列非空、`pyramid_size_mult_used` 列非 NaN | 新 3 模型在 day 频率成功 fit + predict |
| **60min** | `hold_extend_score` 分布合理（0~1）；trailing_exit 真实触发 `horizon_extend`（当 `use_model_recommendation=True` 时） | TrendPersistenceModel 在分钟级有信号区分度 |
| **30min** | `pyramid_add_score` 分布合理；details.csv 有 `is_add_layer=True` 行 | PyramidEligibilityModel 实战生效 |

如果某 interval 下新列**全部为 NaN 或常量**，看 [bull_regime_strength_model.py:55](../model/training/bull_regime_strength_model.py) 类似的 `on_dummy_fallback` warning，
通常是单类标签（pos=0 或 neg=0）导致退化到 DummyClassifier。

---

### 4.5 模型 → 回测 中间环节（**关键衔接**）

第 4 步 ``model_pipeline`` 输出的是**离线评估指标 + 模型权重**，并不是真正的 PnL 回测；
要从模型走到第 5 步带 PnL 的策略回测，**必须**显式做以下三件事：

#### 4.5.1 选定模型文件

```bash
# brooks v3 路径（推荐用作模型驱动的样板）
ls -lah cta/strategy/brooks/models/ | tail -n 5
LATEST_MODEL=$(ls -t cta/strategy/brooks/models/xgb_*.ubj 2>/dev/null | head -n 1)
echo "model: $LATEST_MODEL"

# baseline 三件套训练出的模型
ls -lah cta/backtest/*_model_pipeline/models/
```

#### 4.5.2 校验离线指标 vs 上线门槛

```bash
# 找出最新一轮离线评估
LATEST_RUN=$(ls -td cta/backtest/*_model_pipeline | head -n 1)
echo "latest run: $LATEST_RUN"
column -t -s, "$LATEST_RUN/$(ls $LATEST_RUN | grep _metrics.csv | head -n 1)"
```

不达标的模型**不要**进入第 5 步，回到第 3 / 第 4 重新调样本或参数。

#### 4.5.3 把模型注入策略

- **Brooks v3**：``runner.py --model <path>``（见 5.3 第二条）。runner 内部加载 ``.ubj`` →
  在 ``BrooksV3LiveStrategy.on_bar`` 中调用模型 ``predict_proba`` 过滤候选 → 触发下单。
- **Baseline 三件套 / TightRange**（Donchian / ATR / BreakoutPullback / TightRange）：
  通过 ``cta.live.model_filter.make_trade_filter`` 把 ``trade_filter`` 模型挂在 adapter 上。
  **注意特征源**：默认 ``make_trade_filter`` 从 ``adapter._frame`` 取特征，但 ``_frame``
  只含 baseline 内嵌的 ~30 列；``model_pipeline --generic-mode auto`` 训出的模型期待
  ``cta/data/feature/`` 中通用特征（~400 列）。要让模型真正生效，**必须**注入
  ``OnlineFeatureLoader`` 从离线 parquet 加载完整特征：

```python
from cta.live.model_filter import make_trade_filter
from cta.live.online_feature import OnlineFeatureLoader

loader = OnlineFeatureLoader(feature_root="cta/data/feature")
adapter.order_filter = make_trade_filter(
    "cta/backtest/20260509_RB0_60min_model_pipeline/models/trade_filter_xyz.joblib",
    threshold=0.55,
    feature_provider=loader,           # ← 关键：从 cta/data/feature 加载完整特征
)
```

默认行为更新（P0）：`make_trade_filter` 现在是 **fail-closed**。
如果 `feature_provider` 缺特征 / 预测报错，开仓单默认拒绝（平仓单始终放行）。
若在仿真预热阶段需要旧行为，可显式传 `fail_open=True`。

分组模型上线（按 symbol 自动路由）：

```python
from cta.live.model_filter import make_group_trade_filter
from cta.live.online_feature import OnlineFeatureLoader

loader = OnlineFeatureLoader(feature_root="cta/data/feature")
adapter.order_filter = make_group_trade_filter(
    group_model_paths={
        "tier_a": "cta/backtest/<run_a>/models/trade_filter_xxx.joblib",
        "tier_b": "cta/backtest/<run_b>/models/trade_filter_xxx.joblib",
    },
    symbol_to_group={
        "RB0": "tier_a",
        "HC0": "tier_a",
        "AU0": "tier_b",
        "AG0": "tier_b",
    },
    feature_provider=loader,
    threshold=0.55,
)
```

注意事项：
1. ``make_trade_filter`` 自动从 sibling ``*_features.csv`` 读取特征列；如需指定路径用
   ``feature_columns_csv=...``。
2. 平仓单永远放行，避免持仓被锁住无法离场。
3. ``feature_provider=None`` 时 fallback 到 ``adapter._frame.iloc[-1:]``，仅适用于
   ``--generic-mode whitelist`` 训出的小特征模型；否则会触发"missing columns"全量降级。
4. ``cta/data/feature/`` 是离线产出，**当日**特征要在收盘后由 ``cta.feature.run_all_features``
   生成；实时增量计算（``cta.feature.online``）属于 P2 大改，目前未集成。
5. 若同时使用风控（``make_risk_filter``）和模型过滤，串联挂在一个 lambda 里：
   ```python
   from cta.live.risk import make_risk_filter
   rf = make_risk_filter(guard, capital=1_000_000, daily_pnl_provider=tracker.get_pnl)
   mf = make_trade_filter(model_path, threshold=0.55, feature_provider=loader)
   adapter.order_filter = lambda o, a: rf(o, a) and mf(o, a)
   ```
   仿真启动器（``run_sim``，见 §6.2）自动挂 ``risk_filter``；模型过滤需要在
   ``run_sim`` 返回后给 ``main_engine.cta_engine.strategies[name]`` 二次包装。

#### 4.5.4 单组合上线前 sanity check

```bash
python3 -m cta.strategy.brooks.online.runner \
  --symbol RB0.SHFE \
  --interval minute5 \
  --warmup-start 2024-01-01 --warmup-end 2024-03-31 \
  --live-start 2024-04-01 --live-end 2024-06-30 \
  --model "$LATEST_MODEL" \
  --dry-run
```

dry-run 与第 5 步的批量回测**用同一份模型加载逻辑**，二者结果应一致；不一致说明模型 IO
或数据时序有问题，先排查再继续。

---

## 5. 回测命令

### 5.1 Tight Range 策略回测（支持 day/60/30/15/5/1min，多空）

```bash
python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both
```

### 5.2 通用事件驱动回测 CLI（v1 风格策略）

```bash
python3 -m cta.cli backtest \
  --strategy cta.strategy.demos:make_double_ma \
  --bars cta/data/origin/day/RB0.csv \
  --out-dir cta/backtest/$(date +%Y%m%d)_double_ma_rb0 \
  --title "DoubleMA / RB0 / day" \
  --limit-move-pct 0.07 \
  --liquidity-ratio 0.1
```

`cta.strategy.demos:make_double_ma` 是一个无参 factory，返回符合
``on_bar(i, bar, position) -> list[order_dict]`` 接口的 v1 风格策略；自定义策略只要
满足同一签名都可以用 `module:factory` 形式接入。

### 5.2.1 CtaTemplate 子类回测（与 SimNow / 实盘共享同一份策略代码）

```bash
python3 - <<'PY'
import pandas as pd
from cta.run.cta_backtester import run_via_event_driven
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

bars = pd.read_csv("cta/data/origin/day/RB0.csv", encoding="utf-8-sig")
res = run_via_event_driven(
    strategy_class=SkillTightRangeBreakoutCta,
    vt_symbol="RB0.SHFE",
    setting={"lookback": 10, "alpha": 1.5, "min_count": 5,
             "trade_side_mode": "both", "multiplier": 10.0, "tick_size": 1.0},
    bars=bars,
    out_dir=f"cta/backtest/$(date +%Y%m%d)_tight_range_rb0_cta",
    title="TightRange / RB0 / day (CtaTemplate)",
)
print(res.report_path, res.stats.get("sharpe"), res.stats.get("calmar"))
PY
```

替换 ``SkillTightRangeBreakoutCta`` 为 ``DonchianCta`` / ``AtrBreakoutCta`` /
``BreakoutPullbackCta`` 即可跑其他基线策略；这些类也是 SimNow 仿真直接可用的策略类。

### 5.3 Brooks v3 规则回测 / 模型回测

```bash
python3 -m cta.strategy.brooks.backtest.runner \
  --top-n 3 \
  --start 2023-01-01 \
  --end 2024-12-31 \
  --capital 1000000 \
  --model none
```

```bash
python3 -m cta.strategy.brooks.backtest.runner \
  --top-n 3 \
  --start 2023-01-01 \
  --end 2024-12-31 \
  --capital 1000000 \
  --model cta/strategy/brooks/models/xgb_<timestamp>.ubj
```

### 5.4 多 symbol × 多 interval 批量回测（Sharpe + 月度收益对比）

`cta.run.multi_runner.run_multi` 对每个 ``(symbol, interval)`` 组合各跑一次
``run_via_event_driven``，输出可直接对比的指标矩阵：

- ``summary.csv``           各组合 sharpe / sortino / calmar / mdd / total_pnl / trades_count
- ``sharpe_pivot.csv``      行=symbol，列=interval 的 Sharpe 透视（一眼看哪个组合最优）
- ``monthly_pnl.csv``       long-form 月度 PnL（列：symbol, interval, ym, pnl）
- ``monthly_pnl_pivot.csv`` 行=月份，列=（symbol,interval）的月度收益矩阵
- ``per_combo/<sym>_<itv>/report_*.html`` 每个组合的综合 HTML 报告

```bash
python3 - <<'PY'
import pandas as pd
from pathlib import Path
from cta.run.multi_runner import MultiRunSpec, run_multi
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

DATA = Path("cta/data/origin")

def get_bars(symbol: str, interval: str) -> pd.DataFrame:
    """日线读 csv，分钟级读 parquet 目录拼接。"""
    if interval == "day":
        return pd.read_csv(DATA / "day" / f"{symbol}.csv", encoding="utf-8-sig")
    prefix = symbol[:2].upper() if symbol[1:2].isalpha() else symbol[:1].upper()
    files = sorted((DATA / interval / prefix).glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no parquet under {DATA/interval/prefix}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

def get_setting(symbol: str, interval: str) -> dict:
    return {
        "lookback": 10, "alpha": 1.5, "min_count": 5,
        "trade_side_mode": "both",
        "multiplier": 10.0, "tick_size": 1.0,
        "commission_rate": 0.0001, "slippage_ticks": 1.5,
    }

spec = MultiRunSpec(
    strategy_class=SkillTightRangeBreakoutCta,
    combos=[
        ("RB0", "day"), ("RB0", "minute60"), ("RB0", "minute30"),
        ("HC0", "day"), ("HC0", "minute60"),
        ("I0",  "day"), ("I0",  "minute60"),
    ],
    get_bars=get_bars,
    get_setting=get_setting,
    out_dir=f"cta/backtest/$(date +%Y%m%d)_tight_range_multi",
    monte_carlo_iter=200,
)
res = run_multi(spec)
print(res.summary[["symbol","interval","sharpe","calmar","mdd","total_pnl","trades_count"]].to_string(index=False))
print()
print("=== Sharpe pivot ===")
print(res.summary.pivot_table(index="symbol", columns="interval", values="sharpe").to_string())
print()
print("=== Portfolio (equal-weight) ===")
print({k: res.portfolio_metrics.get(k) for k in ("sharpe","sortino","calmar","mdd","annualized")})
PY
```

> 月度收益看板：``open cta/backtest/<run_dir>/monthly_pnl_pivot.csv``，
> Excel/Numbers 打开后可直接做条件格式（绿正红负）。
> ``portfolio_equity`` 是各组合等权聚合的净值曲线；如需按风险平价 / Kelly 分配权重，
> 在 ``aggregate_portfolio`` 之外自行加一层加权。

如果只关心**模型驱动**的多组合：用 brooks runner 的 ``--top-n`` 已内置多 symbol，
跨 interval 通过修改 ``cta/strategy/brooks/config/strategy.yaml`` 的 ``intervals.ltf``
后重复跑，把每次 ``cta/strategy/brooks/report/<ts>/per_run/`` 下的 ``trades.parquet``
+ ``equity.csv`` 喂给上面的 ``run_multi`` 同款汇总（自己写 5 行胶水即可）。

---

## 6. 仿真命令

### 6.1 离线 dry-run（不连真实网关）

```bash
python3 -m cta.strategy.brooks.online.runner \
  --symbol RB0.SHFE \
  --interval minute5 \
  --warmup-start 2024-01-01 \
  --warmup-end 2024-03-31 \
  --live-start 2024-04-01 \
  --live-end 2024-06-30 \
  --dry-run
```

### 6.2 SimNow 启动单策略（需要 `vnpy_ctp` 与仿真账号）

最小可用版（不带风控、不预热历史）：

```bash
python3 - <<'PY'
from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

cfg = SimRunConfig(
    strategy_class=SkillTightRangeBreakoutCta,
    strategy_name="TightRangeRB0",
    vt_symbol="rb888.SHFE",
    setting={"lookback": 10, "alpha": 1.5, "min_count": 5,
             "trade_side_mode": "both"},
)
sim = SimnowSetting(userid="你的SimNow账号", password="你的SimNow密码")
main_engine = run_sim(cfg, sim)
# 进程保活，等 SIGINT/SIGTERM 后优雅 close
from cta.sim.sim_runner import serve_forever
serve_forever(main_engine)
PY
```

### 6.2.1 推荐：完整集成（历史预热 + 风控 + Kill switch + 成交记录）

```bash
python3 - <<'PY'
from cta.live.kill_switch import KillSwitch
from cta.live.risk import (DailyLossLimit, MaxOrderSize, MaxPositionLimit,
                           OrderRateLimit, RiskGuard)
from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

guard = RiskGuard(rules=[
    MaxOrderSize(limits={"rb888.SHFE": 5}),
    MaxPositionLimit(limits={"rb888.SHFE": 10}),
    DailyLossLimit(max_loss=50_000),    # 自动从 PnlTracker 取当日已实现 PnL
    OrderRateLimit(max_per_second=3),
])
ks = KillSwitch(signal_file="cta/report/live/kill_switch.signal")

cfg = SimRunConfig(
    strategy_class=SkillTightRangeBreakoutCta,
    strategy_name="TightRangeRB0",
    vt_symbol="rb888.SHFE",
    setting={"lookback": 10, "alpha": 1.5, "min_count": 5,
             "trade_side_mode": "both",
             # multiplier / tick_size 留空 → run_sim 自动从 cta_engine.get_size /
             # get_pricetick 读真实合约元数据
             "commission_rate": 0.0001, "slippage_ticks": 1.5},
    # 历史预热：策略启动时往回拉 10 天 1min K 线灌进 buffer，让特征算稳
    warmup_days=10,
    warmup_interval="1m",
    # 集成
    risk_guard=guard,
    kill_switch=ks,
    capital=1_000_000.0,
    contract_size_resolver=lambda vt: {"rb888.SHFE": 10}.get(vt, 1),
    commission_resolver=lambda vt, p, v: p * v * 1e-4,   # 万 1
    trade_recorder_dir="cta/report/live/trade_log",       # 自动每笔 trade 落 parquet
)
sim = SimnowSetting(userid="你的SimNow账号", password="你的SimNow密码")
main_engine = run_sim(cfg, sim)

# 进程保活：阻塞直到收到 SIGINT/SIGTERM，然后优雅 close
from cta.sim.sim_runner import serve_forever
serve_forever(main_engine)
PY
```

`run_sim` 内部自动完成：
- 在 ``cta_engine.classes`` 注册策略类，再用字符串类名调 ``add_strategy``（与 vnpy
  真实接口一致；之前直接传 class object 在生产会静默失败）
- 调 `strategy.load_bar(warmup_days, warmup_interval)` 灌历史
- 创建 `DailyPnlTracker`（``auto_reset_on_new_day=True``，跨日成交自动清零累计 PnL，
  防 ``DailyLossLimit`` 漂移）并挂到 `strategy.pnl_tracker`，作为 `DailyLossLimit` 的数据源
- 把 `RiskGuard` + `KillSwitchRule` 合并成 `make_risk_filter(...)` 挂到 `strategy.order_filter`
- 创建 `TradeRecorder` 挂到 `strategy.trade_recorder`，每笔成交自动 append + 停止时 flush parquet
- 不再立刻退出：调用方应紧跟 ``serve_forever(main_engine)`` 阻塞主线程

### 6.3 多策略 / 多 symbol 同时启动（共享一个 main_engine）

```bash
python3 - <<'PY'
from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim
from cta.strategy.cta_baseline import DonchianCta
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

sim = SimnowSetting(userid="...", password="...")
main_engine = None
for cfg in [
    SimRunConfig(SkillTightRangeBreakoutCta, "TightRB", "rb888.SHFE", {}, warmup_days=10),
    SimRunConfig(DonchianCta, "DonchHC", "hc888.SHFE", {"trade_side_mode":"both"}, warmup_days=10),
]:
    main_engine = run_sim(cfg, sim, main_engine_factory=(lambda: main_engine) if main_engine else None)
print("two strategies running on same main_engine")
PY
```

> 注意：vnpy 的 `run_sim` 默认每次 new 一个 MainEngine；要在同一个进程里复用，需要把
> 已创建的 main_engine 通过 `main_engine_factory` 注入（如示例）。生产环境建议用
> 单独脚本封装，不要混在一行 lambda 里。

---

## 7. 实盘运维常用命令

### 7.1 紧急停单（Kill Switch 文件触发）

```bash
mkdir -p cta/report/live
echo "manual_emergency_stop" > cta/report/live/kill_switch.signal
```

解除停单：

```bash
rm -f cta/report/live/kill_switch.signal
```

### 7.2 每日对账报告（live vs backtest，自动一键）

`cta.live.parity_helper` 自动从 ``TradeRecorder`` 输出的成交流水重建 trade_log/equity，
并用同一份策略对当日 K 线**重跑回测**生成 backtest 侧 trade_log，最后调用
``write_daily_report`` 输出 markdown 对账（含 parity 失配率）：

```bash
python3 - <<'PY'
import pandas as pd
from cta.live.daily_report import write_daily_report
from cta.live.parity_helper import (
    backtest_on_same_bars, recorder_to_parity_inputs,
)
from cta.live.trade_recorder import TradeRecorder
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

# 1. 还原一个 TradeRecorder 视图（成交流水落盘后再装载）
trades = pd.read_parquet("cta/report/live/trade_log/trades_rb888_SHFE_*.parquet")
recorder = TradeRecorder(out_dir=".", vt_symbol="rb888.SHFE")
recorder._rows = trades.to_dict(orient="records")  # 直接灌入

# 2. 同 K 线下用同一份策略类重跑回测，得到回测侧 trade_log
bars = pd.read_csv("cta/data/origin/day/RB0.csv", encoding="utf-8-sig")
bt_tl = backtest_on_same_bars(
    strategy_class=SkillTightRangeBreakoutCta,
    vt_symbol="rb888.SHFE",
    setting={"lookback": 10, "alpha": 1.5, "min_count": 5,
             "trade_side_mode": "both",
             "multiplier": 10.0, "tick_size": 1.0,
             "commission_rate": 0.0001, "slippage_ticks": 1.5},
    bars=bars,
)

# 3. 把实盘流水转换为 trade_log + equity + dates，喂给 daily_report
live_tl, live_eq, dates = recorder_to_parity_inputs(
    recorder, bar_dates=pd.to_datetime(bars["datetime"]),
    multiplier=10.0, commission=0.5,
)

res = write_daily_report(
    live_trade_log=live_tl,
    live_equity=live_eq,
    dates=dates,
    out_dir="cta/report/live/daily",
    title=f"RB0 Daily Reconcile {pd.Timestamp.now().date()}",
    backtest_trade_log=bt_tl,
)
print(res.report_path, "parity_mismatch_rate=", res.parity_mismatch_rate)
PY
```

> Parity 失配率 ``> 5%`` 应触发告警；常见原因是滑点 / 撮合规则差异，
> 在 ``backtest_on_same_bars(engine_cfg=EngineConfig(slippage_ticks=...))`` 里调整对齐。

也可直接用日运维命令（CSV → 报告）：

```bash
python3 -m cta.sim.daily_parity_report \
  --live-signals-csv cta/report/live/signals/live_$(date +%Y%m%d).csv \
  --replay-signals-csv cta/report/live/signals/replay_$(date +%Y%m%d).csv \
  --out-dir cta/report/live/parity \
  --title "Parity Daily $(date +%Y%m%d)" \
  --mismatch-alert-threshold 0.05 \
  --time-tolerance 1min
```

### 7.3 连接守护（Supervisor）

```bash
python3 - <<'PY'
import threading
from cta.live.supervisor import Supervisor
from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

sim = SimnowSetting(userid="...", password="...")
cfg = SimRunConfig(SkillTightRangeBreakoutCta, "RB", "rb888.SHFE", {}, warmup_days=10)
main_engine = run_sim(cfg, sim)

sup = Supervisor(
    main_engine, gateway_name="CTP",
    connect_setting=sim.to_vnpy(),
    check_interval=10.0,           # 10 秒一次心跳
    max_reconnects=100,            # 防雪崩
)
stop = threading.Event()
try:
    sup.loop(stop)
except KeyboardInterrupt:
    stop.set()
    main_engine.close()
PY
```

每次断线（gateway.connected=False 或 query_account 抛错）``Supervisor`` 自动重连，
日志写在 vnpy loguru 中；超过 ``max_reconnects`` 次数后返回 ``give_up`` 不再尝试。

### 7.4 实盘启动（`live_runner`，TDD 落地版）

```bash
python3 - <<'PY'
from cta.live.live_runner import LiveCtpSetting, LiveRunConfig, run_live, serve_live
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta

cfg = LiveRunConfig(
    strategy_class=SkillTightRangeBreakoutCta,
    strategy_name="TightRangeRB_live",
    vt_symbol="rb888.SHFE",
    setting={"lookback": 10, "alpha": 1.5, "min_count": 5, "trade_side_mode": "both"},
    warmup_days=10,
    warmup_interval="1m",
    portfolio_logic_flags={
        "enable_htf_gate": True,
        "enable_ranker": True,
        "enable_trailing": True,
        "enable_pyramid": True,
        "enable_score_calibration": True,
        "enable_risk_throttle": True,
    },
    # 启用订单幂等 reference（策略侧下单时使用 order_reference_prefix）
    enable_order_idempotency=True,
    order_reference_prefix="TightRangeRB_live",
    # 组合状态快照（重启恢复/核对）
    state_snapshot_path="cta/report/live/TightRangeRB_live_state_snapshot.json",
    # 启动前 broker 持仓核对（不一致直接阻断启动）
    enable_broker_reconciliation=True,
    broker_positions_provider=lambda: [
        # 实盘里替换成你的 broker REST / query_position 结果
        # {"symbol": "RB0", "exchange": "SHFE", "direction": "long"},
    ],
)

ctp = LiveCtpSetting(
    userid="你的CTP账号",
    password="你的CTP密码",
    brokerid="你的券商代码",
    td_address="tcp://你的交易前置",
    md_address="tcp://你的行情前置",
    auth_code="你的授权码",
    appid="你的APPID",
)

main_engine = run_live(cfg, ctp, gateway_name="CTP")
serve_live(
    main_engine,
    connect_setting=ctp.to_vnpy(),
    gateway_name="CTP",
    enable_supervisor=True,
    supervisor_check_interval=10.0,
    supervisor_max_reconnects=100,
)
PY
```

---

## 8. 关键输出目录

- 原始行情：`cta/data/origin/`
- 通用特征：`cta/data/feature/`
- 候选训练样本（parquet-only）：`cta/data/model_feature/`
- 模型与离线评估：`cta/backtest/*_model_pipeline/`
- 策略回测报告：`cta/backtest/`
- Brooks v3 报告：`cta/strategy/brooks/report/`
- 仿真 / 实盘日志与日报：`cta/report/live/`

---

## 9. 常见问题

1. `分钟数据下载失败`
   - 先检查 `echo $TUSHARE_TOKEN`
   - 降低 `--rate-limit`（例如 `300`）

2. `模型样本为空`
   - 先跑 Step 2（通用特征）和 Step 3（候选样本）
   - 检查 interval 是否与数据目录一致（`day/minute60/minute30/minute15/minute5/minute`）

3. `xgboost 导入失败（macOS）`
   - `brew install libomp`
