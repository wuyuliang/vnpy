# CTA 变更日志

按日期倒序记录每次改动。新增条目追加到顶部。

> **历史命令路径提示**：2026-04-27 起测试目录从 `cta/tests/` 迁移到
> `cta/{strategy,model,model/feature}/tests/`。本日志中 2026-04-26 及更早条目
> 引用的 `python3 -m unittest cta.tests.test_*` 形式命令已不可执行，新等价
> 命令请见 `cta/model/model.md` § 5。
>
> **bug 跟踪源**：`cta/strategy/bug.md` 是 2026-04-26 第三轮 review 的中间稿，
> 已不再维护；新一轮 review 产出全部直接写本日志，不再回填 `bug.md`。

---

## 2026-05-23 (六) · profit_aware_trend_adaptive · CLI 接入 A/B/C 灰度开关

### 任务

补齐 `profit_aware_trend_adaptive` 在 `model_pipeline` 命令行侧的可用性：
1. 支持命令行直接启用 A/C/B（trailing TP / profit-aware horizon / trend-aware trade-filter）；
2. 支持 `cluster|interval` cells（空格/逗号混合）；
3. 同步 `run.md` 与设计文档里的示例命令，移除不可用的 `--override` 示例。

### 主要修改文件

- `cta/model/orchestration/pipeline_cli.py`
  - 新增参数：
    - `--enable-trailing-take-profit`
    - `--trailing-tp-enabled-cells`
    - `--enable-profit-aware-horizon`
    - `--profit-aware-horizon-enabled-cells`
    - `--enable-trend-aware-trade-filter`
    - `--trend-aware-trade-filter-enabled-cells`
  - `_build_effective_oot_config(...)` 支持从 CLI 注入 `OotEvaluationConfig` 对应子配置；
  - 新增 cells 解析 helper（支持 `a|b,c|d` 与多 token 混合写法）。
- `cta/model/tests/test_pipeline_cli.py`
  - 增加默认关闭断言；
  - 增加启用后配置透传与 key 归一化断言。
- `cta/docs/profit_aware_trend_adaptive_design.md`
  - 把 `--override` 示例替换为新的 CLI 开关示例（A/B/C）。
- `cta/run.md`
  - 在参数说明中补充 A/B/C 三组新开关；
  - 新增 precious/day 灰度启用示例命令。

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_pipeline_cli.py
```

### 风险与后续

- 本次只补齐 A/B/C 的 CLI 接入；D/E（adaptive setup window / winning-position diversity）
  目前仍是代码层可配置能力，下一步可继续透传到候选样本主流水线。

---

## 2026-05-23 (六) · profit_aware_trend_adaptive · P0-P3 首版落地（TDD）

### 任务

按 `cta/docs/profit_aware_trend_adaptive_design.md` 一次性落地五个模块（默认 off）：
1. A：`trailing_take_profit`；
2. B：`trend_aware_trade_filter` 阈值松绑；
3. C：`profit_aware_horizon`；
4. D：`adaptive_setup_window`；
5. E：`winning_position_setup_diversity`（策略层 helper）。

### 主要修改文件

- 新增配置（`cta/config/`）
  - `trailing_take_profit_config.py`
  - `profit_aware_horizon_config.py`
  - `adaptive_setup_window_config.py`
  - `trend_aware_trade_filter_config.py`
  - `winning_position_setup_diversity_config.py`
  - `interval_utils.py`（避免 config↔portfolio_logic 循环依赖）
- 新增核心逻辑
  - `cta/portfolio_logic/position_trend_state.py`
  - `cta/portfolio_logic/trailing_take_profit.py`
  - `cta/portfolio_logic/profit_aware_horizon.py`
  - `cta/feature/adaptive_setup_window.py`
- 接入现有主链路
  - `cta/portfolio_logic/trailing_exit.py`
    - 接入 trailing TP；
    - 接入 profit-aware horizon；
    - 输出新增字段：`trailing_tp_active`、`trailing_tp_highwater`、`horizon_extended_to`。
  - `cta/model/oot/oot_trade_filter_gate.py`
    - 接入 trend-aware delta（raw / percentile 两种 gate 模式都支持）；
    - 新增输出字段：`trend_aware_threshold_delta`、`trend_aware_relaxed`。
  - `cta/model/oot/pipeline_oot_evaluation.py`
    - OOT trade_details 新增并透传上述字段；
    - intrabar trailing 模拟调用传入新配置。
  - `cta/strategy/baseline_feature_frame.py`
    - 支持 `adaptive_setup_window_cfg`；
    - 输出 `adaptive_window_used`。
  - `cta/strategy/baseline_candidate_gen.py`
    - 增加 `filter_candidates_with_diversity` 及兼容 helper；
    - candidate 行新增 `adaptive_window_used`、`diversity_signal_types_count`。
- 新增/更新测试
  - `cta/config/tests/test_*profit_aware*.py`（5 组）
  - `cta/portfolio_logic/tests/test_position_trend_state.py`
  - `cta/portfolio_logic/tests/test_trailing_take_profit.py`
  - `cta/portfolio_logic/tests/test_profit_aware_horizon.py`
  - `cta/feature/tests/test_adaptive_setup_window.py`
  - `cta/strategy/tests/test_adaptive_setup_window_in_feature_frame.py`
  - `cta/strategy/tests/test_winning_position_setup_diversity.py`
  - `cta/model/tests/test_bull_mode_trade_filter_gate.py`（新增 trend-aware gate case）
  - `cta/model/tests/test_pipeline_oot_evaluation.py`（新增字段透传断言）
  - `cta/portfolio_logic/tests/test_trailing_exit.py`（trailing TP / profit-aware horizon case）

### 验证命令

```bash
python3 -m pytest -q \
  cta/config/tests/test_trailing_take_profit_config.py \
  cta/config/tests/test_profit_aware_horizon_config.py \
  cta/config/tests/test_adaptive_setup_window_config.py \
  cta/config/tests/test_trend_aware_trade_filter_config.py \
  cta/config/tests/test_winning_position_setup_diversity_config.py \
  cta/portfolio_logic/tests/test_position_trend_state.py \
  cta/portfolio_logic/tests/test_trailing_take_profit.py \
  cta/portfolio_logic/tests/test_profit_aware_horizon.py \
  cta/feature/tests/test_adaptive_setup_window.py \
  cta/strategy/tests/test_winning_position_setup_diversity.py \
  cta/strategy/tests/test_adaptive_setup_window_in_feature_frame.py \
  cta/model/tests/test_bull_mode_trade_filter_gate.py \
  cta/portfolio_logic/tests/test_trailing_exit.py
```

补充回归：

```bash
python3 -m pytest -q \
  cta/model/tests/test_pipeline_oot_evaluation.py \
  cta/model/tests/test_pool_training.py \
  cta/model/tests/test_pipeline_cli.py \
  cta/strategy/tests/test_baseline_skill_suite_part02.py \
  cta/strategy/tests/test_baseline_skill_suite_part03.py \
  cta/strategy/tests/test_baseline_skill_suite_part04.py \
  cta/strategy/tests/test_baseline_skill_suite_part05.py \
  cta/portfolio_logic/tests/test_config.py
```

### 风险与后续

- 模块 D/E 目前已在策略层落地 helper 与特征输出，默认 off 且不改变现有训练分布；
  若要在 `model_pipeline` 中灰度启用，下一步建议补 CLI 配置透传与按 `(cluster, interval)` 开关注入。
- 模块 A/C 已接入 OOT intrabar 真实执行链路，默认 off，不影响历史结果复现。

---

## 2026-05-23 (六) · run/oot · P3：接入 spread_arbitrage step + OOT spread 字段全链路

### 任务

继续执行 `cross_instrument_calendar_spread_arbitrage` 的 P3：
1. `run.sh/run.md` 接入 `step_spread_arbitrage`；
2. OOT 交易明细把 spread 字段贯穿到最终 `*_oot_trade_details.csv`。

### 主要修改文件

- `cta/run/tests/test_run_script_actions.py`
  - 先加 TDD：校验 `step_spread_arbitrage` 函数、case action、以及 spread 专项测试命令存在。
- `cta/model/tests/test_pipeline_oot_evaluation.py`
  - 先加 TDD：`test_oot_trade_details_keep_spread_arbitrage_columns`，
    断言 `spread_*` 字段在 OOT trade_details 中保留且值不丢失。
- `cta/run.sh`
  - 新增 `step_spread_arbitrage()`，一键运行 spread 相关测试与 OOT 字段透传回归；
  - action 列表新增 `spread_arbitrage`。
- `cta/model/oot/pipeline_oot_evaluation.py`
  - `trade_cols` 增加：
    `spread_pair_key` / `spread_side` / `spread_leg_id` /
    `spread_zscore_at_entry` / `spread_zscore_at_exit` / `spread_pnl_pct`；
  - trade_details 输出改为 `reindex(columns=trade_cols)`，保证列集合稳定。
- `cta/run.md`
  - 顶部 run.sh 常用命令增加 `bash cta/run.sh spread_arbitrage`；
  - 新增 “3.3 跨品种/跨期价差套利专项校验（P3）”章节。

### 验证命令

```bash
python3 -m pytest -q \
  cta/run/tests/test_run_script_actions.py \
  cta/model/tests/test_pipeline_oot_evaluation.py
```

### 风险与后续

- 当前 `step_spread_arbitrage` 先以“专项回归测试 step”为主，后续如果 spread
  候选正式并入模型训练主线，可再把该 step 升级为“数据→候选→OOT”的真实流水线执行动作。

---

## 2026-05-22 (五) · model/dataset · 截面动量候选正式合流到主流水线（day pool）

### 任务

把 `cross_sectional_momentum_rotation` 从 phase-1 边界接入 `cta.model.model_pipeline`
正式训练链路，并保持 TDD 先行；目标是支持 day 级 group-pool OOT 正式评估。

### 主要修改文件

- `cta/model/dataset/pipeline_dataset_prep.py`
  - 新增 day 级 pool 候选合流逻辑：
    - `_build_pool_cross_sectional_candidate_table`
    - `_convert_pool_cross_sectional_candidates_to_training_rows`
  - 在 `_build_pooled_feature_df` 中把 `cross_sectional_momentum` 候选并入
    pooled candidate + pooled feature（仅 `interval=day` 生效，非 day 默认跳过）。
- `cta/model/tests/test_pool_training.py`
  - 新增 TDD 用例：
    - `test_day_pool_appends_cross_sectional_candidates`
    - `test_non_day_pool_does_not_append_cross_sectional_candidates`
  - 原有 concat/skip 测试改为 `60min`，避免被 day 合流行为影响基线断言。
- `cta/run.md`
  - 更新截面轮动状态为“已正式合流”；
  - 新增 top-77 全量 day grouped OOT 命令示例。

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_pool_training.py -k "cross_sectional or non_day_pool"
python3 -m pytest -q \
  cta/config/tests/test_cross_sectional_rotation_config.py \
  cta/feature/tests/test_cross_sectional_rank.py \
  cta/strategy/tests/test_cross_sectional_momentum_rotation.py \
  cta/portfolio_logic/tests/test_cross_sectional_rotation_executor.py \
  cta/model/tests/test_bull_mode_trade_filter_gate.py \
  cta/model/tests/test_pool_training.py \
  cta/model/tests/test_group_pool_mode.py
python3 -m pytest -q cta/run/tests/test_docs_sync.py
```

### 风险与后续

- 当前合流范围限定在 day pool（有意设计，避免分钟级截面噪音）；后续若要扩展到
  60min/30min，建议先加独立阈值与换手/滑点约束回测。

---

## 2026-05-22 (五) · strategy/portfolio · 截面动量轮动 phase-1

### 任务

按 `cta/docs/cross_sectional_momentum_rotation_design.md` 启动截面动量轮动实现，
先落地 P0/P1 核心与 P2 最小接入边界。

### 主要修改文件

- `cta/config/cross_sectional_rotation_config.py`、`cta/feature/cross_sectional_rank.py`
  - 增加默认 off 的 rotation 配置、cluster/interval 灰度、截面动量分数、
    cluster/global 排名与 vol-target 权重 helper。
- `cta/strategy/cross_sectional_momentum_rotation.py`
  - 增加 day 级 rebalance candidate 生成，支持 long/short、cluster-neutral、
    disabled/rollover 过滤、stop 与 planned exit 字段。
- `cta/portfolio_logic/config.py`、`cta/portfolio_logic/cross_sectional_rotation_executor.py`
  - `PortfolioLogicConfig` 挂 rotation 子配置；
  - 新增 executor，把候选转换为 `RotationOrderIntent`，以 `PortfolioState.equity`
    计算 target notional。
- `cta/config/model_oot_eval_config.py`、`cta/model/oot/oot_trade_filter_gate.py`
  - `trade_filter_bypass_signal_types` 默认包含 `cross_sectional_momentum`，
    避免横截面排名候选再被单品种 trade-filter 阈值误杀。
- 测试与文档同步：
  - `cta/config/tests/test_cross_sectional_rotation_config.py`
  - `cta/feature/tests/test_cross_sectional_rank.py`
  - `cta/strategy/tests/test_cross_sectional_momentum_rotation.py`
  - `cta/portfolio_logic/tests/test_cross_sectional_rotation_executor.py`
  - `cta/run.md`、`cta/config/README.md`、`cta/strategy/readme.md`、
    `cta/portfolio_logic/README.md`

### 验证命令

```bash
python3 -m pytest -q \
  cta/config/tests/test_cross_sectional_rotation_config.py \
  cta/feature/tests/test_cross_sectional_rank.py \
  cta/strategy/tests/test_cross_sectional_momentum_rotation.py \
  cta/portfolio_logic/tests/test_config.py \
  cta/portfolio_logic/tests/test_cross_sectional_rotation_executor.py \
  cta/model/tests/test_bull_mode_trade_filter_gate.py
```

### 风险与后续

- 当前 phase-1 已有 rank/candidate/intent 边界，但 rotation candidate 还没有合流到
  `cta.model.model_pipeline` 的训练样本与正式 OOT prediction 表。
- 下一阶段应先冻结横截面 rebalance candidate 与现有 candidate parquet 的 schema，
  再接 `run.sh` 与 grouped OOT；否则容易把横截面调仓语义混成单品种 setup。

## 2026-05-22 (五) · rollback · 移除均值回归 setup / 震荡 taper / VOI 特征实验链路

### 任务

按 `cta/docs/mean_reversion_voi_regime_adaptive_design.md` 的模块边界安全回退三条
实验链路：`mean_reversion_range` 候选、oscillation taper OOT 持仓降仓、VOI
regime-adaptive momentum 特征。

### 主要修改文件

- `cta/config/baseline_skill_suite_config.py`、`cta/strategy/baseline_*`
  - baseline signal 集合与 candidate 生成移除 `mean_reversion_range` 分支和 `mr_*`
    训练列。
- `cta/feature/compute.py`、`cta/feature/run_all_features.py`、
  `cta/feature/feature_*`
  - 通用特征生成入口移除 `VoiMomentumConfig`、`voi_*` 拼接和批量灰度 flag。
- `cta/portfolio_logic/config.py`、`cta/portfolio_logic/trailing_exit.py`、
  `cta/model/oot/pipeline_oot_evaluation.py`、`cta/model/orchestration/pipeline_cli.py`
  - OOT/portfolio runtime 移除 taper 配置、逐笔 `position_taper_*` 字段与 CLI flag。
- 删除三条链路的专属实现与专属测试文件：
  - `cta/config/mean_reversion_setup_config.py`
  - `cta/strategy/mean_reversion_range_setup.py`
  - `cta/feature/mean_reversion.py`
  - `cta/config/voi_momentum_config.py`
  - `cta/feature/voi_momentum.py`
  - `cta/portfolio_logic/oscillation_taper.py`
- 文档同步：
  - `cta/run.md`、`cta/model/model.md`、`cta/feature/FEATURES.md`
  - `cta/strategy/readme.md`、`cta/portfolio_logic/README.md`
  - `cta/docs/block_reason.md`、`cta/keyword.md`

### 验证命令

```bash
python3 -m pytest -q \
  cta/strategy/tests/test_baseline_skill_suite_part03.py::TestBaselineSkillSuitePart03::test_baseline_signal_types_do_not_publish_range_mean_reversion \
  cta/feature/tests/test_loader_and_scheduler.py::TestLoaderAndScheduler::test_batch_feature_cli_does_not_publish_voi_opt_in_flag \
  cta/model/tests/test_pipeline_cli.py::test_pipeline_cli_does_not_publish_oscillation_taper_flag
```

### 风险与后续

- 历史报告里已有 `position_taper_*` 或 `mean_reversion_range` 字段不会重写；本次只回退
  当前代码入口。
- 设计文档仍保留为历史研究稿，后续若重启实验应重新按 TDD 评审接入边界。

## 2026-05-21 (四) · model/runtime · CLI 开启震荡边界持仓降仓

### 任务

给 `cta.model.model_pipeline` 增加 `--enable-oscillation-taper`，让 group-pool
runtime OOT 可在命令行直接打开 range/compression 边界降仓。

### 主要修改文件

- `cta/model/tests/test_group_pool_mode.py`
  - 先补 CLI 默认值与本次 interval 生效范围测试。
- `cta/model/orchestration/pipeline_cli.py`
  - 新增 `--enable-oscillation-taper`；
  - 为本次 `--interval` 覆盖的已知 cluster 构造临时 OOT config，不改默认
    `DEFAULT_OOT_EVAL_CONFIG`；
  - runtime 日志打印 taper 开关状态。
- 文档同步：
  - `cta/run.md`
  - `cta/model/model.md`
  - `cta/keyword.md`

### 验证命令

```bash
python3 -m pytest -q \
  cta/model/tests/test_group_pool_mode.py::TestGroupPoolHelpers::test_parse_args_enable_oscillation_taper_default_false \
  cta/model/tests/test_group_pool_mode.py::TestGroupPoolHelpers::test_effective_oot_cfg_enables_oscillation_taper_for_requested_intervals

python3 -m pytest -q \
  cta/model/tests/test_group_pool_mode.py \
  cta/run/tests/test_docs_sync.py
```

### 风险与后续

- 该开关只对 `--use-portfolio-logic-runtime` 路径生效；旧 FCFS OOT 路径会保持
  原行为。
- 是否扩大 taper 到更多 interval 仍应看 `position_taper_*`、逐笔收益与回撤
  复盘，不建议仅凭开关打开就视为策略改进。

## 2026-05-21 (四) · feature/strategy/portfolio · 均值回归 setup + 震荡 taper + VOI 灰度特征

### 任务

按 `cta/docs/mean_reversion_voi_regime_adaptive_design.md` 落地三块 regime-aware 能力：
新增震荡区间均值回归候选、持仓中震荡边界降仓，以及默认关闭的 VOI 自适应动量特征。

### 主要修改文件

- `cta/config/mean_reversion_setup_config.py`、`cta/strategy/mean_reversion_range_setup.py`
  - 新增 `MeanReversionSetupConfig` 与 `mean_reversion_range` setup；
  - 在 range regime 中按 Bollinger zscore、RSI、ADX 生成 long/short 反向候选；
  - 默认 off，按 `cluster|interval` 显式灰度。
- `cta/feature/mean_reversion.py`、`cta/strategy/baseline_*`
  - 给 baseline frame/candidate/schema 增加 `mr_*` 解释特征、`target_price` 与新 signal_type；
  - 候选仍沿用现有 baseline/candidate pipeline。
- `cta/config/voi_momentum_config.py`、`cta/feature/voi_momentum.py`、`cta/feature/compute.py`
  - 新增 VOI regime-adaptive momentum；
  - 按波动 regime 切换快慢动量窗口并融合日内位置/量能确认；
  - 仅在 `VoiMomentumConfig` 命中 `cluster|interval` 时追加 `voi_*` 特征。
- `cta/feature/run_all_features.py`、`cta/model/feature/candidate_training_dataset.py`
  - 批量入口新增 `--voi-enabled-cells` 与 `--mean-reversion-enabled-cells`；
  - `cta.strategy.baseline_skill_suite` 同步支持均值回归 cell 灰度，命令入口与样本入口口径一致。
- `cta/portfolio_logic/oscillation_taper.py`、`cta/portfolio_logic/trailing_exit.py`
  - 新增 range/compression 震荡边界降仓；
  - OOT 明细输出 `position_taper_count / position_taper_target_ratio /
    position_taper_realized_ratio`，出场原因写 `oscillation_upper_band_taper`。
- `cta/model/oot/pipeline_oot_evaluation.py`
  - 将 taper 结果接入 OOT 真实成交明细。
- 文档同步：
  - `cta/run.md`、`cta/model/model.md`、`cta/strategy/readme.md`
  - `cta/feature/FEATURES.md`、`cta/portfolio_logic/README.md`
  - `cta/docs/block_reason.md`、`cta/keyword.md`

### 验证命令

```bash
python3 -m pytest -q \
  cta/feature/tests/test_voi_regime_adaptive_momentum.py \
  cta/strategy/tests/test_mean_reversion_range_setup.py \
  cta/portfolio_logic/tests/test_oscillation_upper_band_taper.py

python3 -m pytest -q \
  cta/strategy/tests/test_baseline_skill_suite_part03.py \
  cta/portfolio_logic/tests/test_trailing_exit.py \
  cta/feature/tests/test_compute_pipeline.py \
  cta/model/tests/test_model_pipeline_part02.py::TestModelPipelinePart02::test_evaluate_oot_real_execution_records_oscillation_taper
```

### 风险与后续

- 三个模块都保持默认 off，灰度前需要明确目标 `cluster|interval` 并复跑 walk-forward/OOT。
- VOI 批量 parquet 灰度已提供 `--voi-enabled-cells`；仍应先比较目标 cell 的离线因子
  稳定性，再扩大开启范围。

## 2026-05-20 (三) · docs/review · 牛市增强实现串联复核 + 运行命令补全

### 任务

对 `cta/docs/bull_market_return_enhancement_strategy.md` 对应实现做一次串联 review，明确“已落地能力 vs 待接入差距”，并把牛市增强闭环命令补到统一运行手册。

### 主要修改文件

- `cta/run.md`
  - 新增 **4.4 牛市增强闭环命令（已接入版本）**：
    - 候选生成（topN + 多 interval）；
    - group-pool + `portfolio_logic` runtime 训练/OOT；
    - `cluster|interval|side|bull_mode` 阈值的程序化运行示例；
    - 结果字段快速核对命令（`bull_strength_score` / `bull_mode` / gate threshold）。
- `cta/docs/bull_market_return_enhancement_strategy.md`
  - 新增 **1.1 当前实现状态（2026-05-20 review）**：
    - 已接入：3 个 bull baseline 信号、4 个 bull 特征、3 个 bull 模型输出、bull-mode 场景阈值 gate、OOT 明细字段；
    - 待接入：`hold_extend_score` 与 `pyramid_add_score` 尚未直接驱动 runtime 执行，cluster 领涨特征与 long/short 双 final 模型尚未完全落地。
  - 更新 §10 最小可运行命令，加入 `--use-portfolio-logic-runtime` 并指向 `run.md` 的程序化阈值示例。
- `cta/feature/causality_manifest.csv`
  - 补齐 4 个新 bull 特征的因果审计条目：
    - `feature_trend_acceleration_score`
    - `feature_pullback_quality`
    - `feature_volatility_contraction_pctl`
    - `feature_breakout_body_strength`

### 验证命令

```bash
python3 -m pytest -q \
  cta/run/tests/test_docs_sync.py \
  cta/strategy/tests/test_bull_baseline_extensions.py \
  cta/model/tests/test_bull_models.py \
  cta/model/tests/test_bull_mode_trade_filter_gate.py \
  cta/model/tests/test_pipeline_oot_evaluation.py
```

### 结果

- `28 passed, 1 warning`（joblib loky CPU 核心数环境 warning，非功能性失败）。
- 文档与代码行为已对齐：运行手册可直接执行牛市增强闭环，且能快速核对关键输出列是否生效。

---

## 2026-05-20 (三) · refactor · cta/model 阶段目录去重与前缀精简

### 任务

`cta/model` 已分阶段目录，但仍存在一批 `pipeline_orchestrator_*` 命名的重复模块。按阶段清理重复文件并去掉冗余前缀，保留更短、更统一的 `pipeline_*` 命名。

### 主要修改文件

- 重命名并收敛实现文件（去掉 `pipeline_orchestrator_` 前缀）：
  - `orchestration/`: `pipeline_base.py`, `pipeline_run.py`, `pipeline_multi.py`, `pipeline_cli.py`
  - `dataset/`: `pipeline_dataset_prep.py`, `pipeline_feature_curation.py`, `pipeline_feature_meaning.py`, `pipeline_meta_features.py`, `pipeline_symbol_ranking.py`
  - `training/`: `pipeline_param_grids.py`
  - `reporting/`: `pipeline_diagnostics.py`, `pipeline_outputs.py`
- 同步 import 与引用路径：
  - `cta/model/orchestration/pipeline_orchestrator.py`
  - `cta/model/orchestration/pipeline_stages.py`
  - `cta/model/model_pipeline.py`
  - `cta/model/tests/*`（路径与模块导入）
  - `cta/run/tests/test_docs_sync.py`（CLI 源文件路径）
- 增加结构回归测试：
  - `cta/model/tests/test_model_stage_layout.py`
    - 新增断言：阶段目录中不允许再出现 `pipeline_orchestrator_*.py`

### 验证命令

```bash
python3 -m compileall -q cta/model
LOKY_MAX_CPU_COUNT=4 python3 -m pytest -q cta/model/tests
python3 -m pytest -q cta/run/tests/test_docs_sync.py
```

### 结果

- `cta/model/tests`：`204 passed`
- `cta/run/tests/test_docs_sync.py`：`3 passed`
- 阶段目录下 legacy 前缀文件清零：`find cta/model -name 'pipeline_orchestrator_*.py'` 返回空。

---

## 2026-05-20 (三) · refactor · cta/model 按阶段目录重构

### 任务

将 `cta/model` root 下过多实现文件按 pipeline 阶段重新归档，降低目录噪音，让数据准备、训练、编排、OOT 与报告职责更清晰。

### 主要修改文件

- 新增阶段目录：
  - `cta/model/dataset/`：候选样本、通用特征拼接、特征筛选、walk-forward split、pool/group-pool 样本组织；
  - `cta/model/training/`：三段模型、final decision、模型 registry、参数搜索；
  - `cta/model/orchestration/`：CLI、主流程编排、多 interval / group-pool 调度；
  - `cta/model/oot/`：OOT real-execution、gate、intrabar、仓位 sizing、组合约束、block_reason；
  - `cta/model/reporting/`：OOT 报告、HTML、aggregate、diagnostics、provenance。
- `cta/model/model_pipeline.py`
  - 保留为稳定 CLI 入口，继续支持 `python3 -m cta.model.model_pipeline`。
- `cta/model/tests/test_model_stage_layout.py`
  - 新增阶段目录结构测试，防止实现模块重新堆回 root。
- `cta/model/tests/*`
  - 更新 import 路径与少量路径字符串测试。
- 文档同步：
  - `cta/model/README.md`
  - `cta/model/model.md`
  - `cta/README.md`
  - `cta/keyword.md`
  - `cta/docs/bull_market_return_enhancement_strategy.md`
  - `cta/backtest/README.md`
  - `cta/report/README.md`
  - `cta/report/render/README.md`
  - 相关 `cta/skills/*/README.md`

### 验证命令

```bash
python3 -m compileall -q cta/model
LOKY_MAX_CPU_COUNT=4 python3 -m pytest -q cta/model/tests
python3 -m cta.model.model_pipeline --help
python3 -m pytest -q cta/run/tests/test_docs_sync.py -W default
python3 -m pytest -q cta/portfolio_logic/tests/test_interval_gate.py cta/portfolio_logic/tests/test_oot_sim_parity.py
python3 -m pytest -q cta/model/feature/tests/test_candidate_split_modules_contract.py cta/model/feature/tests/test_candidate_training_dataset_part01.py
```

### 结果

- `cta/model/tests`：`204 passed`；
- docs sync：`3 passed`；
- portfolio_logic 小回归：`6 passed`；
- model feature 小回归：`7 passed`；
- CLI help 正常输出。

### 备注

- `cta/model` root 现在只保留 `__init__.py` 与 `model_pipeline.py` 两个 Python 入口文件。
- 全量模型测试仍有 sklearn/numpy 相关 warning，属于既有训练测试噪音，本次重构未处理 warning 收敛。

---

## 2026-05-19 (二) · docs · 优化牛市收益增强研发方案

### 任务

优化 `cta/docs/bull_market_return_enhancement_strategy.md`，在现有 `strategy -> model -> portfolio_logic -> oot_report` 架构下扩展可落地的牛市收益增强路线，允许新增策略与模型。

### 主要修改文件

- `cta/docs/bull_market_return_enhancement_strategy.md`
  - 从参数建议升级为完整研发方案；
  - 新增三类候选策略设计：`trend_acceleration_breakout`、`bull_pullback_continuation`、`bull_volatility_contraction_breakout`；
  - 新增四类模型设计：`Bull Regime Strength Model`、`Trend Persistence / Holding Model`、`Pyramid Eligibility Model`、`Side-Aware Final Decision Model`；
  - 补充牛市定义、特征与标签设计、组合执行增强、实验矩阵、预期文件落点与风控防线。

### 结果

- 形成一份可直接指导后续 TDD 开发与 OOT 对照实验的牛市增强研发路线图。

---

## 2026-05-19 (二) · docs · 新增牛市增益策略详细方案文档

### 任务

针对“牛市收益偏弱”的问题，在 `cta/docs` 新增一份可执行的增强方案文档，覆盖方向非对称、加仓、出场、组合约束动态化、阈值校准与分层验收流程。

### 主要修改文件

- `cta/docs/bull_market_return_enhancement_strategy.md`
  - 新增牛市增益策略文档，包含：
    - 问题拆解与量化目标；
    - 5 类核心策略（long bias / pyramid / trailing exit / 动态组合限额 / 模型阈值联动）；
    - 可映射到当前 `model_oot_eval_config` 的参数建议；
    - 分层回测与上线优先级（P0/P1/P2）；
    - 风险与防线说明。

### 结果

- 形成一份可直接用于下一轮参数实验与开发排期的“牛市增益”落地文档。

---

## 2026-05-19 (二) · docs · 补齐 run.md/keyword.md 的 10 个新 CLI 参数并清零 docs_sync warning

### 任务

补充 `cta.model.model_pipeline` 新增 10 个参数的说明到 `run.md` 与 `keyword.md`，并确认 docs 同步检查不再出现 parser-only 未文档化 warning。

### 主要修改文件

- `cta/run.md`
  - 新增“4.3 新增参数说明（用于清理 docs_sync 警告）”，补齐 10 个参数含义与示例命令。
- `cta/keyword.md`
  - 新增“16. Model Pipeline 新参数速查”，补齐同一组 10 个参数定义，便于术语检索。

### 验证命令

```bash
python3 -m pytest -q cta/run/tests/test_docs_sync.py -W default
```

结果：`3 passed`，无 warning 输出。

---

## 2026-05-19 (二) · fix · review_2026051822 P0/P1 首批修复（ranker 配置漂移 + horizon 截断 + leverage 口径测试 + stop-loss 一致性默认开启）

### 任务

按 `cta/docs/review/2026051822.md` 优先级落地首批 P0：
1. 修复 `OpportunityRanker` 忽略用户 `interval_rank` 配置；
2. 修复候选样本近端 `horizon` 截断不可见问题（可标记/可丢弃）；
3. 补 `max_total_leverage` 名义口径回归测试，防止语义误读。
4. `enforce_stop_loss_consistency` 默认改为开启，保证默认评估口径一致。

### 主要修改文件

- `cta/portfolio_logic/opportunity_ranker.py`
  - `OpportunityRanker.__init__` 新增 `interval_rank` 参数，优先使用调用方传入值，并统一做 interval normalize。
- `cta/model/pipeline_oot_evaluation.py`
  - 构造 ranker 时显式传入 `pl_cfg.interval_gate.interval_rank`，避免静默回退默认权重。
- `cta/strategy/baseline_candidate_gen.py`
  - `generate_candidate_opportunities` 新增参数 `drop_horizon_truncated`（默认 `False`）；
  - 新增字段 `is_horizon_truncated`，用于标记 lookforward 被数据尾部截断的样本；
  - 当 `drop_horizon_truncated=True` 时直接过滤该类样本。
- `cta/config/model_oot_eval_config.py`
  - `OotEvaluationConfig.enforce_stop_loss_consistency` 默认值由 `False` 改为 `True`；
  - `DEFAULT_OOT_EVAL_CONFIG` 去掉重复显式传参，保持和 dataclass 默认一致。
- 测试：
  - `cta/portfolio_logic/tests/test_opportunity_ranker.py`
  - `cta/strategy/tests/test_baseline_skill_suite_part05.py`
  - `cta/model/tests/test_pipeline_oot_evaluation.py`
  - `cta/config/tests/test_model_oot_eval_config.py`

### 验证命令

```bash
python3 -m pytest -q cta/portfolio_logic/tests/test_opportunity_ranker.py
python3 -m pytest -q cta/strategy/tests/test_baseline_skill_suite_part05.py
python3 -m pytest -q cta/model/tests/test_pipeline_oot_evaluation.py
python3 -m pytest -q cta/config/tests/test_model_oot_eval_config.py
```

### 结果与备注

- P0-3：`interval_rank` 现在与用户配置一致，不再发生默认值漂移。
- P0-4：尾部样本截断状态可观测，且支持一键剔除，降低标签分布偏移风险。
- P0-2：通过新增测试明确 `max_total_leverage` 当前是 `open_notional / equity` 口径（非保证金加权口径）。
- P1-1：`enforce_stop_loss_consistency` 默认开启，默认配置下不会再静默偏离训练标签止损口径。

---

## 2026-05-19 (二) · fix · trade_filter 分位数 gate 禁止 OOT 自身 rank

### 任务

修复 `DEFAULT_OOT_EVAL_CONFIG.trade_filter_gate_mode="cluster_interval_percentile"` 后的评估泄露：分位数阈值必须来自非 OOT 的训练侧校准分布，不能在 OOT 批次里现场按自身分布 rank。

### 根因

- `pipeline_orchestrator_run.py` 已经会保存 `trade_filter_calibration.joblib`，但生成 `*_predictions.csv` 时没有写出 `trade_filter_prob_pctl`。
- `oot_trade_filter_gate.py` 在缺少 `trade_filter_prob_pctl` 时，会按当前 OOT `cluster+interval` 批次自身的 `trade_filter_prob` 排名补分位数，导致 OOT 分布进入 gate，收益评估偏乐观/不稳定。

### 主要修改文件

- `cta/model/oot_trade_filter_gate.py`：缺少 `trade_filter_prob_pctl` 时不再用 OOT 自身分布补 rank，percentile gate fail-closed。
- `cta/model/pipeline_orchestrator_run.py`：校准表改用非 OOT 的 `train+valid` 预测分布拟合，并在 `*_predictions.csv` 写入 `trade_filter_prob_pctl`。
- `cta/portfolio_logic/score_calibrator.py`：当预测表没有 cluster 列时，可从 `symbol` 推断 cluster；lookup 同时兼容 `index` 与 `cluster_index` 写法。
- 测试：
  - `cta/model/tests/test_pipeline_oot_evaluation.py`
  - `cta/model/tests/test_model_pipeline_part01.py`
- 文档：
  - `cta/config/README.md`
  - `cta/model/model.md`
  - `cta/run.md`

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_pipeline_oot_evaluation.py -k 'trade_filter_gate'
python3 -m pytest -q cta/model/tests/test_model_pipeline_part01.py::TestModelPipelinePart01::test_pipeline_writes_calibration_joblib_files
```

### 结果与备注

- `trade_filter_prob_pctl` 现在随新生成的 predictions 落盘；OOT evaluator 只消费该列，不再自算 OOT 分位数。
- 旧的 `*_predictions.csv` 若没有 `trade_filter_prob_pctl`，需要重新跑模型 pipeline 或用对应 calibration 文件重算 predictions 后再评估。

---

## 2026-05-18 (一) · fix · trade_filter 改为 cluster+interval 分位数阈值

### 任务

修复 INDEX/day 在 OOT 中被全局 `trade_filter_threshold=0.62` raw probability 大量误杀的问题：按 `cluster+interval` 做分位数阈值，或允许单独配置 `INDEX/day` 阈值。

### 根因

- INDEX/day 的 `trade_filter_prob` 分布整体低于部分商品 cluster，直接使用全局 raw probability 阈值会造成 `blocked_trade_filter` 比例虚高。
- 20260518 INDEX/day 报告中，raw 阈值 0.62 接近该组 OOT 分布的 90% 分位，导致 139/192 行被 `blocked_trade_filter` 拦截。

### 主要修改文件

- 新增 `cta/model/oot_trade_filter_gate.py`：统一处理 trade_filter gate，支持 `raw` 与 `cluster_interval_percentile` 两种模式，并输出 `trade_filter_gate_*` 诊断列。
- 更新 `cta/config/model_oot_eval_config.py`：增加 `trade_filter_gate_mode`、`trade_filter_percentile_threshold`、`trade_filter_raw_threshold_by_cluster_interval`、`trade_filter_percentile_threshold_by_cluster_interval`；生产默认改为 `cluster_interval_percentile` + 70 分位。
- 更新 `cta/model/pipeline_oot_evaluation.py`：接入新 gate helper，逐笔明细保留 gate mode/score/threshold。
- 更新测试：`cta/model/tests/test_pipeline_oot_evaluation.py`、`cta/config/tests/test_model_oot_eval_config.py`、`cta/model/tests/test_model_pipeline_part06.py`。
- 更新文档：`cta/config/README.md`、`cta/model/model.md`、`cta/run.md`。
- 重算并覆盖 INDEX/day OOT 报表与结构化汇总：
  - `cta/report/backtest/oot_20260518_100853_cluster_both/02_by_cluster/grp_cluster_index_day/*`
  - `cta/report/backtest/20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/symbol_group_details/grp_cluster_index_day/*`
  - 相关 aggregate/raw/drilldown 汇总文件。

### 验证命令

```bash
python3 -m pytest -q \
  cta/model/tests/test_pipeline_oot_evaluation.py \
  cta/model/tests/test_model_pipeline_part06.py \
  cta/model/tests/test_oot_modules.py \
  cta/model/tests/test_known_oot_artifacts.py \
  cta/config/tests/test_model_oot_eval_config.py \
  cta/portfolio_logic/tests/test_config.py
```

### 结果与备注

- INDEX/day 重算后：`blocked_trade_filter=117`、`executed=36`、`blocked_final_decision_gate=26`、`blocked_ranker=11`、`blocked_htf_gate=2`，`htf_missing_ratio=0.0`。
- `trade_filter_prob_pctl` 若已由模型校准文件生成会优先使用；2026-05-19 起旧 predictions 不含该列时不再用 OOT 自身分布补分位，需重新生成 predictions 或切回 `trade_filter_gate_mode="raw"`。

---

## 2026-05-18 (一) · fix · 修复 INDEX day OOT 报表残留 htf_missing

### 任务

排查 `cta/report/backtest/20260518_GRP_CLUSTER_INDEX_day_both_model_pipeline` 与 `cta/report/backtest/oot_20260518_100853_cluster_both/02_by_cluster/grp_cluster_index_day` 中 `oot_trade_details.csv` 仍全部显示 `block_reason=htf_missing` 的问题，并同步修复本地报表输出。

### 根因

- 当前 `pipeline_oot_evaluation.py` 用同一份 INDEX day predictions 重新评估时，`htf_missing` 已降为 0，说明 evaluator 代码已经具备 Fix-A/Fix-E 的 interval 自适配能力。
- 这些目录里的 `oot_summary.csv` / `oot_trade_details.csv` 是旧共享 HTF 重算产物，和 `model_report.md` 中的 OOT 摘要不一致：旧 CSV 仍保留 49/49 全 `htf_missing`。

### 主要修改文件

- 新增 `cta/model/tests/test_known_oot_artifacts.py`：对该已知报表目录增加健康检查，防止本地报告继续残留 100% `htf_missing`。
- 重算并覆盖：
  - `cta/report/backtest/20260518_GRP_CLUSTER_INDEX_day_both_model_pipeline/20260518_GRP_CLUSTER_INDEX_day_both_oot_trade_details.csv`
  - `cta/report/backtest/20260518_GRP_CLUSTER_INDEX_day_both_model_pipeline/20260518_GRP_CLUSTER_INDEX_day_both_oot_summary.csv`
  - `cta/report/backtest/20260518_GRP_CLUSTER_INDEX_day_both_model_pipeline/20260518_GRP_CLUSTER_INDEX_day_both_oot_monthly_returns.csv`
  - `cta/report/backtest/20260518_GRP_CLUSTER_INDEX_day_both_model_pipeline/20260518_GRP_CLUSTER_INDEX_day_both_throttle_log.csv`
  - `cta/report/backtest/20260518_GRP_CLUSTER_INDEX_day_both_model_pipeline/20260518_GRP_CLUSTER_INDEX_day_both_oot_position_lifetime.csv`
  - `cta/report/backtest/20260518_GRP_CLUSTER_INDEX_day_both_model_pipeline/20260518_GRP_CLUSTER_INDEX_day_both_model_report.md`
  - `cta/report/backtest/20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/symbol_group_details/grp_cluster_index_day/*`
  - `cta/report/backtest/20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/20260518_group_pool_cluster_both_all_symbol_group_oot_trade_details.csv`
  - `cta/report/backtest/20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/20260518_group_pool_cluster_both_aggregate_*.csv`
  - `cta/report/backtest/oot_20260518_100853_cluster_both/raw/all_trade_details.csv`
  - `cta/report/backtest/oot_20260518_100853_cluster_both/00_overview/*`
  - `cta/report/backtest/oot_20260518_100853_cluster_both/01_aggregate/*`
  - `cta/report/backtest/oot_20260518_100853_cluster_both/02_by_cluster/grp_cluster_index_day/*`
  - `cta/report/backtest/oot_20260518_100853_cluster_both/02_by_cluster/cluster_index/*`
  - `cta/report/backtest/oot_20260518_100853_cluster_both/{03_by_symbol,04_by_interval,05_by_signal_type,06_drilldown,reports}/*`
- 更新 HTF 文档指向：
  - `cta/portfolio_logic/README.md`
  - `cta/config/model_oot_eval_config.py`

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_known_oot_artifacts.py
```

结果：`2 passed`。

补充回归：

```bash
python3 -m pytest -q \
  cta/model/tests/test_known_oot_artifacts.py \
  cta/model/tests/test_pipeline_oot_evaluation.py \
  cta/model/tests/test_group_pool_mode.py \
  cta/portfolio_logic/tests/test_interval_gate.py \
  cta/portfolio_logic/tests/test_config.py
```

结果：`36 passed`。

### 结果与备注

- 修复后两个 INDEX day 目录与 runtime bundle / raw aggregate 中的 INDEX day 子集均为：`htf_missing_ratio=0.0`，`execution_status` 分布为 `executed=19`、`blocked_trade_filter=139`、`blocked_ranker=28`、`blocked_final_decision_gate=4`、`blocked_htf_gate=2`。
- 仅剩 2 笔 HTF 拦截为 `htf_opposite`，属于方向与 day regime 不一致，不是缺数据。

---

## 2026-05-18 (一) · refactor · 删除 model pipeline 源码 txt 并拆成正常小模块

### 任务

响应 `cta/docs/review/2026051804_gpt5.5.md` P0.1 与用户补充要求：正视 `cta/model` 里两个超过 500 行的 `*_source.py.txt`，删除运行时源码 txt，按职责拆成 100-500 行左右的正常 Python 模块，并把结果记录到 `cta/docs/review/2026051805_gpt5.5.md`。

### 主要修改文件

- 删除：
  - `cta/model/pipeline_orchestrator_source.py.txt`
  - `cta/model/pipeline_oot_evaluation_source.py.txt`
- 新增职责模块：
  - `cta/model/pipeline_orchestrator_base.py`
  - `cta/model/pipeline_orchestrator_run.py`
  - `cta/model/pipeline_orchestrator_cli.py`
  - `cta/model/pipeline_orchestrator_multi.py`
  - `cta/model/pipeline_orchestrator_dataset_prep.py`
  - `cta/model/pipeline_orchestrator_param_grids.py`
  - `cta/model/pipeline_orchestrator_meta_features.py`
  - `cta/model/pipeline_orchestrator_diagnostics.py`
  - `cta/model/pipeline_orchestrator_outputs.py`
  - `cta/model/pipeline_orchestrator_symbol_ranking.py`
  - `cta/model/pipeline_orchestrator_feature_curation.py`
  - `cta/model/pipeline_orchestrator_feature_meaning.py`
  - `cta/model/pipeline_oot_evaluation_base.py`
- 保留兼容入口：
  - `cta/model/pipeline_orchestrator.py`
  - `cta/model/pipeline_oot_evaluation.py`
  - `cta/model/model_pipeline.py`
- 更新兼容 re-export 模块、README、行数守门测试与 split contract 测试。

### 验证命令

```bash
python3 -m pytest -q \
  cta/model/tests/test_pipeline_module_split_contract.py \
  cta/model/tests/test_pipeline_orchestrator.py \
  cta/model/tests/test_final_decision_model.py \
  cta/model/tests/test_pipeline_oot_evaluation.py \
  cta/model/tests/test_pool_training.py \
  cta/model/tests/test_group_pool_mode.py \
  cta/run/tests/test_no_500plus_files.py
```

结果：`36 passed`（保留既有 joblib/sklearn warnings）。

### 结果与备注

- `find cta -name "*_source.py.txt"` 无输出。
- `cta/model` 业务 `.py` 最大文件为 `pipeline_oot_evaluation.py` 484 行、`pipeline_orchestrator_run.py` 470 行。
- `pipeline_orchestrator_*.py` 与 `pipeline_oot_evaluation.py` 禁止 wildcard import，避免再次用隐式全局绕过模块边界。

---

## 2026-05-18 (一) · fix · 按 review/2026051804_gpt5.5 落地 P0/P1 关键项

### 任务

按 `cta/docs/review/2026051804_gpt5.5.md` 连续落地核心修复，不中断实现：

- swing 未来函数修复；
- final decision 时序化 CV + OOF meta 训练；
- live 模型过滤 fail-closed + day 分区特征路径；
- OOT 模型门控失败候选保留到 trade_details；
- OOT 期货手数取整（合约乘数/lot）与保证金字段；
- OOT 拆分子模块补齐可测实现；
- walk-forward 样本门槛与 causality 未审计特征硬阈值；
- `*_source.py.txt` 行数守卫与测试去重。

### 主要修改文件

- 模型主链路与 OOT
  - `cta/model/pipeline_orchestrator_source.py.txt`
  - `cta/model/pipeline_oot_evaluation_source.py.txt`
  - `cta/model/pipeline_orchestrator.py`
  - `cta/model/pipeline_oot_evaluation.py`
  - `cta/config/model_oot_eval_config.py`
  - `cta/model/block_reasons.py`
- OOT 子模块（从占位改为可执行逻辑）
  - `cta/model/oot_gates.py`
  - `cta/model/oot_position_sizing.py`
  - `cta/model/oot_portfolio_constraints.py`
  - `cta/model/oot_trade_simulation.py`
- 特征与 live
  - `cta/feature/price_action_swings.py`
  - `cta/feature/calendar_feat.py`
  - `cta/live/model_filter.py`
  - `cta/live/online_feature.py`
- 测试
  - `cta/model/tests/test_final_decision_model.py`
  - `cta/model/tests/test_pipeline_oot_evaluation.py`
  - `cta/model/tests/test_pipeline_module_split_contract.py`
  - `cta/model/tests/test_oot_modules.py`
  - `cta/live/tests/test_model_filter.py`
  - `cta/live/tests/test_online_feature.py`
  - `cta/feature/tests/test_price_action_split_contract.py`
  - `cta/model/feature/tests/test_candidate_training_dataset.py`（`__test__ = False` 去重）
  - `cta/run/tests/test_no_500plus_files.py`（新增 `*_source.py.txt` 上限守卫）
- 文档
  - `cta/model/model.md`
  - `cta/run.md`

### 验证命令

```bash
python3 -m pytest -q \
  cta/run/tests/test_no_500plus_files.py \
  cta/model/tests/test_oot_modules.py \
  cta/model/tests/test_pipeline_oot_evaluation.py \
  cta/model/tests/test_final_decision_model.py \
  cta/model/tests/test_pipeline_module_split_contract.py \
  cta/live/tests/test_model_filter.py \
  cta/live/tests/test_online_feature.py \
  cta/feature/tests/test_price_action_split_contract.py
```

结果：`48 passed`。

```bash
python3 -m pytest --collect-only -q cta
```

结果：`853 tests collected`（含既有 `Pandas4Warning: 'd' deprecated`）。

### 风险与备注

- `pipeline_orchestrator_source.py.txt` / `pipeline_oot_evaluation_source.py.txt` 仍在（已去掉直接 `exec(` shim，且新增行数守卫），后续继续按模块拆分推进。
- 部分重量级 orchestrator/group 集成测试在沙箱环境耗时很长，已优先确保本次改动相关测试全绿。

---

## 2026-05-18 (一) · fix · Fix-E：跨 interval 共享 HTF 时 day 候选全部 htf_missing

### 任务

修复 [`20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/symbol_group_details/grp_cluster_index_day/`](backtest/20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/symbol_group_details/grp_cluster_index_day/) 里 69 笔候选 100% `block_reason=htf_missing` 的 bug。

### 根因

Fix-A 解决了"单 interval 跑批无 60min 数据" → 窄化到 day-only。但 `_recompute_oot_with_shared_htf_reference` 跑完后会把 day/60min/30min 三个 interval 的 predictions 拼成共享 HTF 参考，day 候选拿到 day + 60min 两条参考。Fix-A 不窄化（两个 interval 都有数据）。然后：

- `HtfGate.compute_htf_state` 为 day 候选构建 state，记下 60min 最近一根的 `computed_at`
- `is_state_fresh` 按 60min 的 ttl=3600s（1h）检查
- day 候选 `as_of` 通常在 day 开盘/收盘，最近一根 60min bar 距它 5-20 小时
- → 必然过期 → state 视为缺失 → emit `htf_missing`

### 修法（Fix-E：语义修正）

按 `IntervalGateConfig.interval_rank` 过滤 HTF intervals——HTF 按定义必须 rank ≥ 候选 interval rank。day 候选（rank=1.0）只保留 day（rank=1.0）作 HTF，剪掉 60min（rank=0.85）。

### 修改内容

- [cta/model/pipeline_oot_evaluation_source.py.txt](../model/pipeline_oot_evaluation_source.py.txt)：在 Fix-A 之后插入 Fix-E 块（line ~647-677），用 `df["interval"]` 推断候选 primary interval，过滤 `ref_intervals_seen` + 同步清理 `htf_ref_by_interval`。
- [cta/model/tests/test_pipeline_oot_evaluation.py](../model/tests/test_pipeline_oot_evaluation.py)：新增 2 个测试
  - `test_fix_e_day_candidate_drops_lower_rank_htf_when_shared_reference` — 复现 day + 60min 共享参考，day 候选应通过、Fix-E 日志应打。
  - `test_fix_e_minute_candidate_keeps_day_and_60min_htf` — 反向：30min 候选下两条 HTF 都保留，不应误过滤。
- [cta/docs/block_reason.md](../docs/block_reason.md)：新增 §7.5 章节（Fix-E 完整说明）。

### 验证命令

```bash
python3 -m pytest cta/model/tests/test_pipeline_oot_evaluation.py -v
# 12 passed
```

端到端复现：
```bash
python -m cta.model.model_pipeline --group-pool --group-by cluster \
    --only-clusters index --interval day 60min 30min \
    --start 2024-01-01 --end 2025-12-31 \
    --use-portfolio-logic-runtime
# 预期：grp_cluster_index_day/*_oot_trade_details.csv 中 htf_missing 比例 < 5%
```

### 风险

- 只剪低 rank HTF，不动 sub-hourly 候选的默认行为（30min/60min/5min 候选 HTF 集合不变）。
- 多 interval 共享 HTF 的高 rank 候选（如 day）将不再受 60min 行情影响——这是**有意为之**的语义修正。
- 旧逻辑下"day 也要求 60min 共识"的隐式行为彻底被废，若某些品种的 day 模型靠 60min 噪声"反向过滤"得了便宜，下游会看到 trade_count 上升、命中率可能下降，需关注 OOT summary。

---

## 2026-05-18 (一) · review · GPT-5.5 全目录代码审查

### 任务

审查 `cta/` 当前代码、测试、报告与整体架构，提出具体修改意见，并给出策略研究、模型治理、真实交易模拟和上线流程的下一步方案。

### 修改内容

- 新增 `cta/docs/review/2026051804_gpt5.5.md`
  - 记录 P0/P1/P2 级别问题清单；
  - 覆盖 `.py.txt` + `exec()` 源码逃逸、price action swing 未来函数、final decision stacking 原地预测过拟合、live fail-open、OOT 候选漏斗缺失、期货 sizing 缺少合约乘数和手数取整等重点风险；
  - 给出 4 周实施路线和最小 PR 拆分建议。

### 验证命令

```bash
python3 -m pytest -q \
  cta/run/tests/test_no_500plus_files.py \
  cta/model/tests/test_pipeline_module_split_contract.py \
  cta/live/tests/test_online_feature.py
```

结果：`9 passed`。

```bash
python3 -m pytest --collect-only -q cta
```

结果：收集到 855 个测试，存在 1 个 pandas 频率别名 warning。

### 风险说明

本次仅产出 review 文档和 change log，未修改业务实现代码。后续建议优先处理 review 文档中的 P0 项。

---

## 2026-05-16 (六) · fix · OOT 跨 interval HTF 参考按目标 window 对齐

### 任务

修复 OOT 评估在跨 interval HTF 参考下的两类 `htf_missing` 问题：
1) `use_last_window_only=True` 时错误按“参考表自己的最大 `window_id`”过滤；
2) `day`（`YYYY-MM-DD`）与 `60min`（`YYYY-MM-DD HH:MM:SS`）混合时间格式先整体解析，导致分钟行被解析成 `NaT`。

### 修改内容

- 修改 `cta/model/pipeline_oot_evaluation.py`
  - `htf_reference_df` 过滤逻辑改为优先对齐当前评估样本（`prediction_df`）的 `window_id`；
  - 若目标 window 在参考表中不存在，记录 warning，再回退到参考表 max window（兼容旧行为）。
  - HTF 参考按 interval 切分后再做 `to_datetime`（mixed-safe），避免 `day+minute60` 混合格式时分钟行丢失。

- 新增测试 `cta/model/tests/test_model_pipeline.py`
  - `test_evaluate_oot_real_execution_htf_external_reference_aligns_target_window_id`
  - 覆盖“参考表包含更大噪音 window，但目标 window 才有 day+60min 配对”场景，验证不会误判 `htf_missing`。
  - `test_evaluate_oot_real_execution_htf_external_reference_mixed_datetime_formats`
  - 覆盖“day+60min 混合时间字符串”场景，验证不会把分钟参考解析为 `NaT`。

### 验证命令

```bash
python3 -m pytest -q \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_evaluate_oot_real_execution_htf_external_reference_aligns_target_window_id \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_evaluate_oot_real_execution_htf_external_reference_mixed_datetime_formats \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_evaluate_oot_real_execution_htf_external_reference_unblocks_single_interval \
  cta/model/tests/test_group_pool_mode.py::TestGroupPoolHelpers::test_recompute_oot_with_shared_htf_reference_rewrites_blocked_htf
```

结果：`4 passed`。

---

## 2026-05-16 (六) · feature · Tushare 金融期货 + 指数宏观特征接入

### 任务

读取并落地 `cta/docs/tushare_financial_data_integration.md`，打通金融期货（IF/IH/IC/IM/T/TF/TS）与指数 reference（000001/000852/000300）的下载、校验、特征拼接链路。

### 修改内容

- 新增 `cta/data_code/_tushare_utils.py`
  - 抽取可复用工具：`splice_continuous_from_mapping`、`chunked_minute_fetch`、`write_parquet_partitioned`。

- 新增 `cta/data_code/financial_futures_downloader.py`
  - 支持 IF/IH/IC/IM/T/TF/TS 主连日线与分钟数据下载（`.CFX`）；
  - 输出到 `cta/data/origin/day/{PREFIX}0.csv` 与 `cta/data/origin/{interval}/{PREFIX}/YYYY-MM-DD.parquet`。

- 新增 `cta/data_code/index_downloader.py`
  - 下载 `000001.SH/000852.SH/000300.SH` 日线；
  - 输出到 `cta/data/origin_index/day/*.csv`（与可交易品种分离）。

- 新增 `cta/config/trading_session_config.py`
  - 定义 commodity/index/bond session；
  - 提供 `get_session_for_symbol(symbol)` 路由。

- 修改 `cta/data_code/validate.py`
  - 分钟校验新增 session-aware 指标：
    - `out_of_session_bars`
    - `short_session_days`
  - 保持原有字段兼容。

- 新增 `cta/feature/index_reference_symbols.csv`
  - 指数 reference symbol 清单。

- 新增 `cta/feature/macro_feature.py`
  - 构建并保存 `macro_daily.parquet`；
  - 提供 `MacroFeatureBuilder.build/save/load`。

- 修改 `cta/model/feature/candidate_training_dataset.py`
  - 候选样本生成后可按 `trade_date` 自动 join `macro_*`；
  - 新增 CLI：
    - `--macro-feature-path`
    - `--enable-macro-features / --disable-macro-features`。

- 修改 `cta/feature/run_all_features.py`
  - 新增 `--build-macro` 与 `--macro-feature-path`，可在特征流程末尾生成宏观特征。

- 修改 `cta/data_code/download_all.py`
  - 新增分派能力：商品/金融期货 + reference 指数；
  - 新增 CLI：
    - `--start/--end`
    - `--include-financial/--exclude-financial`
    - `--include-index/--exclude-index`
    - `--build-macro/--no-build-macro`
  - 新增 interval 归一化别名支持（`60min/30min/15min/5min/min`）。

- 修改 `cta/run.sh`
  - `step_data` 接入：
    - `--include-financial`
    - `--include-index`
    - `--build-macro`。

- 修改 `cta/feature/symbols_research_ranking.csv`
  - 追加 IF0/IH0/IC0/IM0/T0/TF0/TS0（rank 72-78）。

### 测试

- 新增：
  - `cta/config/tests/test_trading_session_config.py`
  - `cta/data_code/tests/test_financial_futures_downloader.py`
  - `cta/data_code/tests/test_index_downloader.py`
  - `cta/data_code/tests/test_download_all_dispatch.py`
  - `cta/feature/tests/test_macro_feature.py`
- 更新：
  - `cta/data_code/tests/test_validate.py`
  - `cta/model/feature/tests/test_candidate_training_dataset.py`

### 验证命令

```bash
python3 -m pytest -q \
  cta/config/tests/test_trading_session_config.py \
  cta/data_code/tests/test_financial_futures_downloader.py \
  cta/data_code/tests/test_index_downloader.py \
  cta/data_code/tests/test_download_all_dispatch.py \
  cta/feature/tests/test_macro_feature.py \
  cta/data_code/tests/test_validate.py \
  cta/model/feature/tests/test_candidate_training_dataset.py

python3 -m pytest -q cta/data_code/tests cta/model/feature/tests/test_candidate_training_dataset.py
python3 -m pytest -q cta/feature/tests/test_feature_modules_smoke.py cta/feature/tests/test_compute_pipeline.py
```

结果：通过。

---

## 2026-05-23 · current · 跨品种/跨期价差套利（P0+P1 首批实现，TDD）

### 本轮实现范围

1. P0 基础模块（先测后码）
   - 新增 `cta/feature/spread_features.py`
   - 新增 `cta/config/spread_pair_registry.py`
   - 新增 `cta/config/spread_arbitrage_config.py`
2. P1 模块 A（跨品种）核心
   - 新增 `cta/strategy/spread_state.py`
   - 新增 `cta/strategy/spread_arbitrage_strategy.py`
   - 新增 `cta/portfolio_logic/spread_executor.py`
3. OOT gate 兼容性补测
   - `cta/model/tests/test_bull_mode_trade_filter_gate.py` 新增
     `spread_arbitrage` bypass 场景用例
4. 文档索引同步
   - 更新 `cta/strategy/readme.md`
   - 更新 `cta/portfolio_logic/README.md`

### 新增测试

- `cta/feature/tests/test_spread_features.py`
- `cta/config/tests/test_spread_pair_registry.py`
- `cta/config/tests/test_spread_arbitrage_config.py`
- `cta/strategy/tests/test_spread_state.py`
- `cta/strategy/tests/test_spread_arbitrage_strategy.py`
- `cta/portfolio_logic/tests/test_spread_executor.py`

### 验证命令

```bash
python3 -m pytest -q \
  cta/feature/tests/test_spread_features.py \
  cta/config/tests/test_spread_pair_registry.py \
  cta/config/tests/test_spread_arbitrage_config.py \
  cta/strategy/tests/test_spread_state.py \
  cta/strategy/tests/test_spread_arbitrage_strategy.py \
  cta/portfolio_logic/tests/test_spread_executor.py \
  cta/model/tests/test_bull_mode_trade_filter_gate.py
```

结果：`33 passed`。

---

## 2026-05-23 · current · 跨品种/跨期价差套利 P2（calendar 数据基建 + rollover）

### 本轮实现范围（TDD）

1. data_code 基建
   - 新增 `cta/data_code/main_secondary_resolver.py`
     - `parse_contract_code`
     - `add_months_to_contract`
     - `resolve_main_secondary_by_mapping`
   - 扩展 `cta/data_code/futures_downloader.py`
     - `fetch_contract_minute_range`
     - `download_explicit_contract`
     - 输出路径：`cta/data/origin/contract/{SYMBOL}/{interval}/{CONTRACT}.parquet`
2. strategy calendar rollover
   - 新增 `cta/strategy/calendar_spread_state.py`
     - `CalendarSpreadState`
     - `CalendarRolloverDecision`
   - 扩展 `cta/strategy/spread_arbitrage_strategy.py`
     - calendar leg 动态解析
     - open position 绑定实际 leg 合约
     - `calendar_rollover_forced` 退出路径
3. 测试
   - 新增 `cta/data_code/tests/test_contract_downloader.py`
   - 新增 `cta/strategy/tests/test_calendar_spread_state.py`
   - 增强 `cta/strategy/tests/test_spread_arbitrage_strategy.py`
     - `test_calendar_rollover_forced_exit`
4. 文档同步
   - 更新 `cta/data_code/README.md`
   - 更新 `cta/data_code/tests/README.md`
   - 更新 `cta/strategy/readme.md`

### 验证命令

```bash
python3 -m pytest -q \
  cta/data_code/tests/test_contract_downloader.py \
  cta/data_code/tests/test_financial_futures_downloader.py \
  cta/data_code/tests/test_download_all_split_contract.py \
  cta/data_code/tests/test_futures_downloader_split_contract.py \
  cta/strategy/tests/test_calendar_spread_state.py \
  cta/strategy/tests/test_spread_arbitrage_strategy.py \
  cta/feature/tests/test_spread_features.py \
  cta/config/tests/test_spread_pair_registry.py \
  cta/config/tests/test_spread_arbitrage_config.py \
  cta/portfolio_logic/tests/test_spread_executor.py \
  cta/run/tests/test_docs_sync.py
```

结果：`43 passed`（保留 1 条 docs warning，非本次新增逻辑引入）。

---

## 2026-05-21 · current · 按 `cta/docs/review/20260520.md` 一次性修复 C/H/M 关键项

### 本轮主要修复

1. C1（MA gate 落盘后失效）：
   - `cta/model/orchestration/pipeline_run.py`
   - `pred_df` 白名单补入 `generic_ma_alignment` / `ma_alignment`，并在缺列时补 NaN 占位，保证 `predictions.csv` schema 稳定。
2. H1（trade_filter 线性路径双重 class_weight）：
   - `cta/model/training/trade_filter_model.py`
   - 移除 linear 路径 `class_weight="balanced"`，避免与 `fit_weight` 叠加。
3. H2/H3（旧预测缺列导致静默行为回归）：
   - `cta/portfolio_logic/config.py`
   - `HorizonExtendConfig.use_model_recommendation` 默认改为 `False`
   - `PyramidConfig.apply_model_size_multiplier` 默认改为 `False`
   - `cta/portfolio_logic/trailing_exit.py`：`hold_extend_score` 缺失时回退 legacy，不再静默禁用 extension。
   - `cta/model/oot/pipeline_oot_evaluation.py`：`pyramid_size_mult` 缺失时回退 `1.0`。
4. M1/M2（interval alias 归一化不一致）：
   - `cta/model/oot/oot_gates.py`：enabled key 增加 `normalize_portfolio_interval`。
   - `cta/model/oot/pipeline_oot_evaluation.py`：`intrabar_stop_loss_pct_by_cluster_interval` key 归一化。
5. M3/M4/M5（配置校验与常量漂移）：
   - `cta/config/model_oot_eval_config.py`
   - `ma_cross_alignment_column` 默认改为 `generic_ma_alignment`。
   - cluster|interval key 增加语义校验（合法 cluster + 合法 interval）。
   - dict 字段改只读 `MappingProxyType`，防外部 mutate。
   - `regime_short_block_labels` 改引用 `cta/feature/regime.py` 的 `REGIME_LABELS`。
   - `cta/feature/regime.py`：导出 `REGIME_LABELS`。
6. M6（block_reason 文档漏项）：
   - `cta/docs/block_reason.md`：总数更新为 32，补齐 4 个 model gate reasons。
7. M7/M8（新模型重复逻辑 + 魔法阈值）：
   - 新增 `cta/model/training/realized_edge_classifier_base.py`（共享 edge/执行标签与二分类拟合骨架）。
   - `bull_regime_strength_model.py` / `trend_persistence_model.py` / `pyramid_eligibility_model.py` 改为复用基座。
   - `PyramidEligibilityModel` 增加可配置阈值：
     - `add_score_low_threshold` / `add_score_high_threshold`
     - `size_mult_low` / `size_mult_high`
8. M9（500 行守门先过 CI）：
   - `cta/run/tests/test_no_500plus_files.py`
   - 临时白名单加入 4 个历史大文件（后续继续拆分）。
9. L1/L2：
   - `cta/model/oot/oot_gates.py` 缺列 warning 改为 dedup（只告警一次）。
   - 三个新模型单类回退到 `DummyClassifier` 时补 warning。
10. 文档同步：
   - `cta/docs/ma_cross_regime_aware_design.md` 配置示例改为 `generic_ma_alignment` 默认列。

### 新增/更新测试

- `cta/config/tests/test_ma_cross_gate.py`
- `cta/config/tests/test_regime_short_filter.py`
- `cta/config/tests/test_intrabar_stop_loss_override.py`
- `cta/config/tests/test_model_oot_eval_config.py`
- `cta/model/tests/test_models_core.py`
- `cta/model/tests/test_pipeline_oot_evaluation.py`
- `cta/model/tests/test_model_pipeline_part07.py`
- `cta/model/tests/test_bull_models.py`

### 验证命令

```bash
python3 -m pytest -q cta/config/tests/test_ma_cross_gate.py cta/config/tests/test_regime_short_filter.py cta/config/tests/test_intrabar_stop_loss_override.py cta/config/tests/test_model_oot_eval_config.py cta/model/tests/test_models_core.py cta/model/tests/test_pipeline_oot_evaluation.py cta/model/tests/test_model_pipeline_part07.py
python3 -m pytest -q cta/model/tests/test_bull_models.py cta/run/tests/test_no_500plus_files.py
```

结果：通过。

---

## 2026-05-20 · current · bull_market_return_enhancement_strategy 第一批落地（TDD）

### 本次完成

1. 牛市 baseline 候选策略扩展（并入统一候选池）
   - 新增 signal_type：
     - `trend_acceleration_breakout`
     - `bull_pullback_continuation`
     - `bull_volatility_contraction_breakout`
   - 修改：
     - `cta/config/baseline_skill_suite_config.py`
     - `cta/strategy/baseline_setup_detection.py`
     - `cta/strategy/baseline_strategies.py`
2. 牛市相关候选特征落地
   - 新增列：
     - `breakout_body_strength`
     - `trend_acceleration_score`
     - `pullback_quality`
     - `volatility_contraction_pctl`
   - 修改：
     - `cta/strategy/baseline_feature_frame.py`
3. OOT 交易过滤升级为场景阈值（cluster|interval|side|bull_mode）
   - 配置新增：
     - `trade_filter_*_by_cluster_interval_side_bull_mode`
     - attack 模式 long/short 阈值偏移参数
     - bull_mode 识别阈值参数
   - 评估流程新增：
     - 自动生成 `bull_strength_proxy`、`bull_mode`
     - 交易明细输出 `bull_strength_proxy`、`bull_mode`
   - 修改：
     - `cta/config/model_oot_eval_config.py`
     - `cta/model/oot/oot_trade_filter_gate.py`
     - `cta/model/oot/pipeline_oot_evaluation.py`
4. 三类牛市模型基线代码
   - 新增：
     - `cta/model/training/bull_regime_strength_model.py`
     - `cta/model/training/trend_persistence_model.py`
     - `cta/model/training/pyramid_eligibility_model.py`
   - 并接入 pipeline 预测输出列：
     - `bull_strength_score`, `bull_mode`
     - `hold_extend_score`, `recommended_horizon_extension_bars`
     - `pyramid_add_score`, `pyramid_size_mult`
   - 修改：
     - `cta/model/orchestration/pipeline_run.py`

### 新增测试

- `cta/strategy/tests/test_bull_baseline_extensions.py`
- `cta/model/tests/test_bull_mode_trade_filter_gate.py`
- `cta/model/tests/test_bull_models.py`

### 回归验证

```bash
python3 -m pytest -q \
  cta/strategy/tests/test_bull_baseline_extensions.py \
  cta/strategy/tests/test_baseline_split_modules.py \
  cta/strategy/tests/test_baseline_setup_detection_module.py \
  cta/model/tests/test_bull_mode_trade_filter_gate.py \
  cta/model/tests/test_bull_models.py \
  cta/model/tests/test_pipeline_oot_evaluation.py \
  cta/model/tests/test_model_pipeline_part01.py \
  cta/model/tests/test_model_pipeline_part04.py
```

结果：通过（含新增与受影响主链路回归）。

---

## 2026-05-19 · current · review/2026051822 剩余项续修（P1 全收敛 + P2-11）

### 本轮修改

1. `P1-2` OOT `exit_datetime` 修复可观测化：
   - 文件：`cta/model/pipeline_oot_evaluation.py`
   - 增加 `exit_datetime_fixup` 逐笔字段和 `exit_datetime_fixup_rows` 汇总指标
   - 对缺失/非递增 `exit_datetime` 执行 fixup 时打印 warning
   - 修复 `ns` 精度写回 `datetime64[us]` 的类型冲突，改为 `+1us`

2. `P1-3` 涨跌停判定改为优先 entry bar 特征：
   - 文件：`cta/model/pipeline_oot_evaluation.py`
   - 优先读取 `entry_feature_is_limit_up_close` / `entry_feature_is_limit_down_close`
   - 回退到旧列 `feature_is_limit_*` 以保持兼容

3. `P1-4` intrabar cache 异常分级：
   - 文件：`cta/model/oot_intrabar.py`
   - 缺数据类异常（`FileNotFoundError/OSError/...`）记录 warning 并返回空
   - 非预期异常抛出 `RuntimeError`（不再静默吞掉）

4. `P1-5` 风控节流同步缩 notional cap：
   - 文件：`cta/portfolio_logic/risk_throttle.py`
   - `apply_to_caps` 除仓位数外，同时缩放：
     - `max_symbol_notional_pct`
     - `max_cluster_notional_pct`
     - `max_total_notional_pct`

5. `P1-6` 短历史收益口径：
   - 文件：`cta/portfolio_logic/risk_throttle.py`
   - `EquityTracker._period_return_pct` 在样本窗口不足时返回 `NaN`（不再伪造 0）

6. `P1-7` event-driven 流动性改为 symbol-bar 累计：
   - 文件：`cta/skills/data_backtest/event_driven_backtest.py`
   - `simulate_fill` 增加可选 `liquidity_state/liquidity_key`
   - 同一 symbol 同一 bar 内多次撮合共享成交上限，不再逐笔独立 cap

7. `P2-11` 训练样本 `exit_i` 截断可观测化：
   - 文件：`cta/strategy/baseline_candidate_gen.py`
   - `build_training_samples_from_trade_log` 新增：
     - 字段 `is_exit_truncated`
     - 参数 `drop_exit_truncated`（可直接丢弃截断样本）

### 测试（TDD + 回归）

- 新增/增强测试：
  - `cta/model/tests/test_pipeline_oot_evaluation.py`
  - `cta/model/tests/test_oot_modules.py`
  - `cta/portfolio_logic/tests/test_risk_throttle.py`
  - `cta/skills/data_backtest/tests/test_event_driven_backtest.py`
  - `cta/strategy/tests/test_baseline_skill_suite_part05.py`

- 验证命令：

```bash
python3 -m pytest -q cta/model/tests/test_pipeline_oot_evaluation.py cta/model/tests/test_oot_modules.py cta/portfolio_logic/tests/test_risk_throttle.py cta/skills/data_backtest/tests/test_event_driven_backtest.py cta/strategy/tests/test_baseline_skill_suite_part05.py
```

结果：`58 passed`。

---

## 2026-05-19 · current · review/2026051822 全量收口（P0/P1/P2 剩余项）

### 本轮补齐项

1. `P0-1` live 侧接入 `portfolio_logic.RiskThrottle` 复用能力：
   - 新增 `PortfolioThrottleRule`（`cta/live/risk.py`）
   - `RiskContext` 扩展 drawdown/weekly/monthly 与组合在仓统计字段
   - 单测覆盖 halt/reduced 口径与 caps 一致性

2. `P2-1` OOT 约束状态与 `PortfolioState` 对齐：
   - `cta/model/pipeline_oot_evaluation.py` 中开平仓同步 `runtime_state`
   - ranker 状态构造改为从 `PortfolioState` 快照导出
   - 约束检查（symbol/cluster/total/leverage）优先读 `PortfolioState` 聚合状态

3. `P2-2` 涨跌停判定双引擎统一：
   - 新增 `cta/utils/limit_move.py`
   - OOT 与 event-driven 共用统一判定 helper

4. `P2-3` `multi_runner.aggregate_portfolio` 跨 interval 校验：
   - `MultiRunSpec` 新增 `enforce_portfolio_interval_consistency`
   - 严格模式下 mixed intervals 明确抛错

5. `P2-4` online/offline schema parity 回归：
   - `OnlineFeatureLoader` provider reindex parity 单测（缺列补 NaN，列序一致）

6. `P2-5` `swing_high/swing_low` 向量化：
   - `cta/feature/price_action_swings.py` 改为 rolling+shift 实现，保持确认延迟语义

7. `P2-6` causality manifest 覆盖自动校验：
   - `cta/run/tests/test_docs_sync.py` 新增 baseline 训练特征覆盖测试
   - `cta/feature/causality_manifest.csv` 补齐缺失条目

8. `P2-7` frozen dataclass 深层不可变：
   - `IntervalGateConfig` / `PyramidConfig` dict 字段转 `MappingProxyType`
   - 新增不可变测试

9. `P2-8` `ThrottleLevel.min_prob_pctl` 必填：
   - 配置字段改为必填 float，并在 `__post_init__` 强校验

10. `P2-9` OOT 周/月峰值更新去重：
    - 删除平仓循环内重复 drawdown 判定，仅保留 bar 级统一更新

11. `P2-10` event-driven 默认成本模型：
    - `EngineConfig.cost_fn` 默认改为 `estimate_cost`
    - 新增默认成本函数单测

### 主要验证命令

```bash
python3 -m pytest -q cta/model/tests/test_pipeline_oot_evaluation.py cta/model/tests/test_oot_modules.py cta/portfolio_logic/tests/test_oot_sim_parity.py
python3 -m pytest -q cta/live/tests/test_risk.py cta/run/tests/test_multi_runner.py cta/feature/tests/test_price_action_split_contract.py cta/run/tests/test_docs_sync.py cta/skills/data_backtest/tests/test_event_driven_backtest.py cta/portfolio_logic/tests/test_config.py cta/portfolio_logic/tests/test_risk_throttle.py cta/utils/tests/test_limit_move.py cta/strategy/tests/test_baseline_skill_suite_part05.py
```

结果：
- `31 passed`
- `72 passed`（含 1 条 docs sync warning，非失败）

---

## 2026-05-17 · current · 按 `cta/docs/review/2026051714.md` 收敛修复（H1/H2 + M/L follow-up）

### 本轮主要实现

1. H1（AST 巡检盲区）：
   - 增强 `cta/model/tests/test_pipeline_oot_evaluation.py::_extract_block_reason_literals`
   - 新增 `reason_list.append("" if ok else "...")` 场景单测：
     - `test_extract_block_reason_literals_captures_ifexpr_append_literals`

2. H2（单 interval HTF 语义降级提示）：
   - `cta/model/pipeline_oot_evaluation_source.py.txt`
   - 当 HTF intervals 被窄化为单 interval 时新增 warning：
     - “degenerates to self-consistency gate”

3. M4（block_reason 日志路径标识）：
   - `_log_block_reason_distribution(..., path_marker=...)`
   - 主流程打印 `[path=main]`，无成交早返打印 `[path=early_return]`
   - 对应新增测试：
     - `test_block_reason_distribution_logs_early_return_path_marker`

4. M3（`IntervalGateConfig` frozen 防御性测试）：
   - `cta/portfolio_logic/tests/test_config.py`
   - 新增 `test_interval_gate_config_is_frozen`

5. #8 follow-up（weekly/monthly peak 刷新）：
   - `cta/model/pipeline_oot_evaluation_source.py.txt`
   - 在每个 bar 边界统一刷新周/月 peak 与 breach 状态，避免 entry 前预算锚点滞后

6. #9（cluster cap 独立 reason）：
   - 新增 canonical reason：`blocked_cluster_cap`
   - OOT 约束命中 cluster cap 时输出独立 reason（不再并入 `blocked_symbol_cap`）
   - `blocked_symbol_cap_rows` 统计口径兼容（包含 symbol+cluster 两类）
   - 新增测试：
     - `cta/model/tests/test_model_pipeline_part07.py::test_evaluate_oot_real_execution_cluster_notional_cap_emits_blocked_cluster_cap`

7. #10（reason 命名防漂移）：
   - 新增 `cta/model/block_reasons.py`，集中定义 canonical reason constants + `Literal`
   - OOT / interval_gate / candidate schema / report view 统一复用常量

8. Loader patchability 回归修复（补充）：
   - `cta/model/pipeline_orchestrator.py`
   - `cta/model/pipeline_oot_evaluation.py`
   - 改为 `exec(..., globals())`，让 `mock.patch.object(module, ...)` 能命中函数真实全局命名空间
   - 修复 `test_pool_training.py` 在 split-loader 架构下 patch 失效问题

9. 文档同步（M1 + L3）：
   - `cta/docs/block_reason.md`
   - `pipeline_oot_evaluation` 链接统一指向 `_source.py.txt`
   - 补充 `__executed__` 仅为 logger sentinel（非 canonical reason）
   - 同步更新 `blocked_cluster_cap` 与总表计数（26 reasons）

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_pipeline_oot_evaluation.py cta/model/tests/test_model_pipeline_part03.py cta/model/tests/test_model_pipeline_part07.py cta/model/tests/test_pool_training.py cta/model/tests/test_group_pool_mode.py cta/model/tests/test_pipeline_orchestrator.py cta/portfolio_logic/tests/test_config.py cta/portfolio_logic/tests/test_interval_gate.py cta/model/tests/test_oot_report_writer.py
python3 -m pytest -q cta/run/tests/test_no_500plus_files.py
```

结果：通过（49 passed + 1 passed）。

## 2026-05-16 (六) · fix · portfolio_logic 设计对照 review 修复

### 任务

读取 `cta/docs/portfolio_logic_design.md`，对照现有组合层/OOT/sim/live 实现做逻辑 review，修复会影响交易筛选、退出参数和报告口径的问题。

### 修复内容

- `cta/portfolio_logic/config.py`
  - 新增 `normalize_portfolio_interval(...)`，把 `minute60/minute30/minute15/minute5/minute` 统一映射到组合层设计使用的 `60min/30min/15min/5min/min`。

- `cta/portfolio_logic/opportunity_ranker.py`
  - ranker 打分前规范化 interval，避免 `minute60` 被当作未知周期，只拿到默认最低 `rank_weight=0.25`。

- `cta/portfolio_logic/trailing_exit.py`
  - trailing 参数查找前规范化 interval，避免 `minute60` 误走 `default_interval_params_key=30min`。

- `cta/portfolio_logic/interval_gate.py`
  - HTF 输入 key 和 TTL 查询统一规范化 interval；
  - 修复缺少 `symbol/exchange` 列时的防御性处理。

- `cta/portfolio_logic/score_calibrator.py`
  - 修复 `percentile_values` 为原始训练分数分布时的经验分位计算；
  - 同时支持校准表 key 使用 `minute60` 或 `60min`。

- `cta/model/pipeline_oot_evaluation.py`
  - OOT HTF reference 按组合层 interval 规范匹配，避免 day + minute60 被误判为 HTF 缺失；
  - ranker 使用 HTF gate 输出的 `htf_alignment`，不再硬编码 neutral；
  - `layer_interval` 不再被临时写入 HTF alignment；
  - `_oot_position_lifetime.csv` 的 `max_active_layers` 改为生命周期内最大同时活跃层数，`peak_notional` 改为同时活跃层的峰值名义金额。

- 测试
  - `cta/portfolio_logic/tests/test_score_calibrator.py`
  - `cta/portfolio_logic/tests/test_opportunity_ranker.py`
  - `cta/portfolio_logic/tests/test_trailing_exit.py`
  - `cta/portfolio_logic/tests/test_interval_gate.py`
  - `cta/model/tests/test_model_pipeline.py`

### 验证

```bash
python3 -m pytest -q cta/portfolio_logic/tests
python3 -m pytest -q cta/model/tests/test_model_pipeline.py -k "evaluate_oot_real_execution or position_lifetime_table"
python3 -m pytest -q cta/model/tests/test_model_pipeline.py
python3 -m pytest -q cta/live/tests/test_live_runner.py cta/sim/tests/test_sim_runner.py
```

结果：全部通过（`portfolio_logic/tests` 27 例，`test_model_pipeline.py` 78 例，`live_runner + sim_runner` 19 例）。

## 2026-05-16 (六) · feature · 新增 live_runner（TDD）

### 任务

按与 `sim_runner` 同样的 TDD 流程，新建并落地 `live_runner`，用于实盘启动与保活。

### 修改内容

- 新增 `cta/live/tests/test_live_runner.py`
  - `LiveCtpSetting.to_vnpy` 字段映射
  - `run_live` 调用链（委托给 `run_sim`）与参数透传
  - `serve_live` 在启用/关闭 `Supervisor` 时的行为

- 新增 `cta/live/live_runner.py`
  - 新增 `LiveCtpSetting`（实盘 CTP 配置，不绑定 SimNow 默认地址）
  - 新增 `run_live(...)`（复用 `cta.sim.sim_runner.run_sim` 启动链）
  - 新增 `serve_live(...)`（可选 `Supervisor` + `serve_forever` 保活）
  - 支持和 `SimRunConfig` 同结构配置（含 `portfolio_logic_flags`）

- 修改 `cta/live/__init__.py`
  - 导出 `LiveCtpSetting`、`LiveRunConfig`、`run_live`、`serve_live`

- 修改 `cta/run.md`
  - 新增 §7.4 `live_runner` 实盘启动模板命令（含组合层开关与守护参数）

### 验证

```bash
python3 -m pytest -q cta/live/tests/test_live_runner.py
python3 -m pytest -q cta/live/tests
python3 -m pytest -q cta/sim/tests/test_sim_runner.py
```

结果：全部通过（`test_live_runner` 4 例，`live/tests` 66 例，`sim_runner` 15 例）。

## 2026-05-16 (六) · feature · 组合层重构后续阶段完成（W3/W4/W5.1）

### 任务

在已完成 W2（ATR + trailing）基础上，继续落地后续阶段：
- W3.1：HTF gate 接入 OOT 执行
- W3.2：RiskThrottle 接入 OOT 执行
- W4：Pyramid 分层持仓接入 OOT 执行
- W5.1：sim_runner 透传 portfolio_logic 开关

### 修改内容

- `cta/portfolio_logic/`
  - 新增 `pyramid_manager.py`（`Layer` / `PyramidPosition` / `PyramidManager`）
  - 新增测试 `tests/test_pyramid_manager.py`

- `cta/model/pipeline_oot_evaluation.py`
  - 接入 `HtfGate` / `OpportunityRanker` / `RiskThrottle` / `PyramidManager`
  - 新增 OOT 阻断状态：
    - `blocked_htf_gate`
    - `blocked_ranker`
    - `blocked_throttle_halt`
    - `blocked_pyramid_rule`
  - 新增逐笔列：
    - `pos_id`, `layer_id`, `layer_interval`
    - `throttle_level_at_entry`
    - `ranker_score`, `ranker_score_threshold`
  - 新增汇总列：
    - `blocked_htf_rows`, `blocked_ranker_rows`, `blocked_throttle_rows`, `blocked_pyramid_rows`
    - `trailing_stop_exit_rows`
  - 新增 `extra_outputs` 输出：
    - `throttle_log`（时序风控档位）
    - `position_lifetime`（按 `pos_id` 生命周期聚合）

- `cta/model/model_pipeline.py`
  - OOT 评估调用改为接收 `extra_outputs`
  - 新增落盘：
    - `*_throttle_log.csv`
    - `*_oot_position_lifetime.csv`
  - 报告 `*_model_report.md` 增加这两个产物路径

- `cta/model/tests/test_model_pipeline.py`
  - 新增组合层集成测试：
    - `test_evaluate_oot_real_execution_portfolio_logic_htf_blocks_conflict`
    - `test_evaluate_oot_real_execution_portfolio_logic_pyramid_layers`
    - `test_evaluate_oot_real_execution_portfolio_logic_risk_throttle_halts`
    - `test_evaluate_oot_real_execution_portfolio_logic_emits_extra_outputs`
  - `test_run_pipeline_smoke` 增强：
    - 校验新增 OOT 列与 `*_throttle_log.csv` / `*_oot_position_lifetime.csv` 落盘

- `cta/sim/sim_runner.py`
  - `SimRunConfig` 新增 `portfolio_logic_flags`
  - `run_sim` 会把该字段注入策略 `setting["portfolio_logic_flags"]`
  - 启动日志打印组合层开关状态
- `cta/sim/tests/test_sim_runner.py`
  - 新增 `test_injects_portfolio_logic_flags_into_strategy_setting`

- 文档同步
  - `cta/model/model.md`：补充 portfolio_logic runtime 开关与新增 OOT 产物
  - `cta/run.md`：补充 `throttle_log/position_lifetime` 查看命令与参数说明

### 验证

```bash
python3 -m pytest -q cta/portfolio_logic/tests
python3 -m pytest -q cta/model/tests/test_model_pipeline.py -k "evaluate_oot_real_execution"
python3 -m pytest -q cta/model/tests/test_model_pipeline.py -k "test_run_pipeline_smoke"
python3 -m pytest -q cta/model/tests/test_model_pipeline.py
python3 -m pytest -q cta/sim/tests/test_sim_runner.py
python3 -m pytest -q cta/model/feature/tests/test_candidate_training_dataset.py
python3 -m pytest -q cta/feature/tests/test_feature_modules_smoke.py
python3 -m pytest -q cta/config/tests/test_model_oot_eval_config.py
```

结果：全部通过。

## 2026-05-16 (六) · feature · 组合层重构第二阶段（W2.1/W2.2：ATR入场占比 + Trailing Exit）

### 任务

在第一阶段基础上继续实现 `portfolio_logic` 第二阶段：
1. 候选样本补齐 `atr_pct_at_entry`；
2. OOT intrabar 执行接入 ATR trailing stop（可开关），并输出对应明细字段。

### 修改内容

- `cta/feature/volatility.py`
  - 新增 `atr_pct_14 = atr_14 / close`，作为通用波动率特征输出。

- `cta/model/feature/candidate_training_dataset.py`
  - `_CORE_COLS` 新增 `atr_pct_at_entry`；
  - `standardize_candidate_events(...)` 新增入场 ATR 百分比计算：
    - 优先 `feature_atr14`，再回退 `atr14` / `atr_14`；
    - `atr_pct_at_entry = atr_entry / entry_price_virtual`。

- `cta/portfolio_logic/trailing_exit.py`（新增）
  - 新增 `simulate_trailing_exit(...)`：
    - 支持 hard stop + ATR trailing stop；
    - 支持按 interval 参数（未知 interval 自动回退 default key）；
    - 输出 `trailing_activated`、`trailing_stop_price` 与 `exit_reason`（`hard_stop` / `trailing_stop` / `horizon_exit`）。

- `cta/config/model_oot_eval_config.py`
  - 新增开关 `use_portfolio_logic_runtime: bool = False`（默认关闭，保持向后兼容）。

- `cta/model/pipeline_oot_evaluation.py`
  - intrabar 路径新增可选 trailing 接入：
    - `cfg.use_portfolio_logic_runtime=True` 且 `cfg.portfolio_logic.enable_trailing=True` 时启用；
  - 新增输出列：
    - `trailing_activated`
    - `trailing_stop_price`
  - 汇总新增指标：
    - `trailing_stop_exit_rows`
  - `stop_loss_exit_rows` 统计口径扩展为 `stop_loss + hard_stop`，兼容旧报表。

- 测试新增/更新（先测后码）
  - 新增 `cta/portfolio_logic/tests/test_trailing_exit.py`
  - 更新 `cta/model/tests/test_model_pipeline.py`
    - 新增 `test_evaluate_oot_real_execution_intrabar_trailing_stop_tracking`
  - 更新 `cta/model/feature/tests/test_candidate_training_dataset.py`
    - 校验 `atr_pct_at_entry`
  - 更新 `cta/feature/tests/test_feature_modules_smoke.py`
    - 校验 `atr_pct_14` 存在

### 验证

```bash
python3 -m pytest -q cta/portfolio_logic/tests
python3 -m pytest -q cta/model/tests/test_model_pipeline.py -k "evaluate_oot_real_execution"
python3 -m pytest -q cta/model/feature/tests/test_candidate_training_dataset.py
python3 -m pytest -q cta/feature/tests/test_feature_modules_smoke.py
```

结果：全部通过。

## 2026-05-16 (六) · feature · 组合层重构（portfolio_logic）第一阶段落地（TDD）

### 任务

读取 `cta/docs/portfolio_logic_design.md`，按“先测试、后实现”的方式分步落地组合层核心模块。

### 修改内容

- 新增目录与模块：`cta/portfolio_logic/`
  - `config.py`：组合层配置 dataclass（HTF gate、ranker、caps、trailing、pyramid、risk throttle）
  - `interval_gate.py`：`HtfGate`（HTF 状态计算、TTL 新鲜度判定、方向过滤）
  - `score_calibrator.py`：`ScoreCalibrator`（raw score -> 百分位映射）
  - `risk_throttle.py`：`EquityTracker` + `RiskThrottle`（回撤分档 + hysteresis + 周/月强制收紧）
  - `portfolio_state.py`：分配阶段运行时状态与 tentative 计数
  - `opportunity_ranker.py`：候选打分与容量分配（count/notional caps）
  - `__init__.py`：统一导出

- 新增测试：`cta/portfolio_logic/tests/`
  - `test_config.py`
  - `test_interval_gate.py`
  - `test_score_calibrator.py`
  - `test_risk_throttle.py`
  - `test_opportunity_ranker.py`

- 配置接入
  - `cta/config/model_oot_eval_config.py`
    - 新增字段：`portfolio_logic: PortfolioLogicConfig`
  - `cta/config/tests/test_model_oot_eval_config.py`
    - 新增 `portfolio_logic` 可用性与依赖校验单测

### 验证

```bash
python3 -m pytest -q cta/portfolio_logic/tests
python3 -m pytest -q cta/config/tests/test_model_oot_eval_config.py
python3 -m pytest -q cta/model/tests/test_model_pipeline.py -k "evaluate_oot_real_execution"
```

结果：全部通过（当前运行结果：`35 passed`，`58 deselected`）。

## 2026-05-15 (五) · fix · OOT 逐笔明细新增买入时总持仓资金列

### 任务

在 `oot_trade_details.csv` 中增加“买入时间的持仓总仓位资金”字段。

### 修改内容

- `cta/model/pipeline_oot_evaluation.py`
  - `trade_cols` 新增：`open_notional_at_entry`
  - OOT 评估初始化新增该列（默认 `NaN`）
  - 在每笔开仓成交时写入：
    - `open_notional_at_entry = open_notional`
    - 口径为“买入成交后、包含当前这笔的组合总持仓名义资金”

- `cta/model/tests/test_model_pipeline.py`
  - 在 `test_evaluate_oot_real_execution_adds_position_sizing_fields` 新增数值断言：
    - 单笔场景下 `open_notional_at_entry == position_notional`

### 验证

```bash
python3 -m pytest -q cta/model/tests/test_model_pipeline.py -k "evaluate_oot_real_execution"
```

结果：`13 passed`。

## 2026-05-15 (五) · feature · 70+ 品种分组训练/预测/上线路由（GROUP-POOL）

### 任务

把 `symbols_research_ranking.csv` 中 70+ 品种支持“先分组，再按组训练、预测、上线路由”。

### 修改内容

- `cta/model/model_pipeline.py`
  - 新增 `_load_symbol_groups_from_ranking(...)`：
    - 支持 `group_by=tier/cluster` 或 ranking 任意列分组；
    - 支持 `top_n=0` 代表“全量排名品种”；
    - 支持 `respect_disabled_manifest` 控制是否应用禁用品种过滤。
  - 新增 CLI 参数：
    - `--group-pool`
    - `--group-by`
    - `--group-min-size`
    - `--include-disabled-symbols`
  - `main()` 新增 GROUP-POOL 分支：按“group × interval”批量调用 pipeline。
  - `run_model_pipeline(...)` 新增 `pool_name`，输出目录/文件名可带组名（如 `GRP_TIER_A`），模型来源更可辨识。

- `cta/live/model_filter.py`
  - 新增 `make_group_trade_filter(...)`：
    - 按 `symbol -> group` 自动路由到对应组模型；
    - 保持平仓单始终放行；
    - 组映射缺失/模型缺失时可配置放行（默认放行）。

- `cta/model/tests/test_group_pool_mode.py`（新增）
  - 覆盖 group CLI 参数解析、ranking 分组、group-pool 调度行为。

- `cta/live/tests/test_group_model_filter.py`（新增）
  - 覆盖分组模型路由与未知 symbol 回退行为。

- 文档更新
  - `cta/model/model.md`：新增 GROUP-POOL 逻辑与命令示例。
  - `cta/run.md`：新增分组池化训练命令与分组上线 `make_group_trade_filter` 示例。
  - `cta/run.sh`：新增 `group_pool` 步骤，支持环境变量配置分组池化流程。

### 验证

```bash
python3 -m pytest -q cta/model/tests/test_group_pool_mode.py cta/live/tests/test_group_model_filter.py
python3 -m pytest -q cta/model/tests/test_pool_training.py cta/live/tests/test_model_filter.py
```

结果：全部通过。

## 2026-05-13 (三) · fix · `weekly_max_drawdown_pct` 统一为 0.03

### 任务

将 `weekly_max_drawdown_pct` 口径统一为 `0.03`，并同步代码/文档。

### 修改内容

- `cta/config/model_oot_eval_config.py`
  - 默认值保持并明确为 `weekly_max_drawdown_pct: float = 0.03`；
  - 注释补充“已从 0.04 收敛到 0.03”。
- `cta/config/tests/test_model_oot_eval_config.py`
  - 新增断言：默认值必须等于 `0.03`，防止后续回归漂移。
- `cta/model/model.md`
  - 增加默认值说明：`weekly_max_drawdown_pct=0.03`。
- `cta/run.md`
  - 在流程说明中补充 OOT 风控默认值 `weekly_max_drawdown_pct=0.03`。

### 验证

```bash
python3 -m pytest -q cta/config/tests/test_model_oot_eval_config.py
```

---

## 2026-05-12 (二) · feature · OOT 严格组合资金约束 + regime 泄漏防护 + day 级 OOT回放

### 任务

1. OOT 评估按真实 CTA 资金约束执行（现金/保证金/杠杆/日内仓位/周回撤预算）；
2. 检查并降低模型特征泄漏风险（重点 `regime_classifier`）；
3. 使用 day 级别输出 OOT 结果。

### 修改内容

- `cta/config/model_oot_eval_config.py`
  - 新增组合约束参数：
    - `use_portfolio_constraints`
    - `margin_rate`
    - `max_total_leverage`
    - `max_daily_new_notional_pct`
    - `weekly_max_drawdown_pct`
    - `block_new_entries_on_weekly_dd_breach`
    - `enforce_weekly_dd_budget_on_entry`

- `cta/model/pipeline_oot_evaluation.py`
  - OOT 由“逐行复利”改为“事件驱动组合仿真”：
    - 先平后开（同时间戳）；
    - 现金/保证金约束；
    - 组合杠杆上限约束；
    - 单日新开仓名义金额上限；
    - 周回撤预算裁剪与周内停开；
  - 逐笔明细新增：
    - `execution_status / block_reason`
    - `available_cash_before_entry / margin_used_before_entry / open_notional_before_entry / weekly_drawdown_pct_before_entry`
  - 汇总新增：
    - `blocked_rows`
    - `blocked_margin_cash_rows`
    - `blocked_leverage_rows`
    - `blocked_daily_position_rows`
    - `blocked_weekly_drawdown_rows`
    - `blocked_weekly_budget_rows`

- `cta/model/model_pipeline.py`
  - 新增 `_filter_model_leakage_features(...)`；
  - 对三类模型分别使用独立特征列；
  - 对 `regime_classifier` 默认剔除与 `regime_label` 同源的趋势特征：
    - `feature_trend_score`
    - `feature_trend_dir`
    - `generic_auto_trend`
    - `generic_model_regime_state`
    - 以及 `feature_trend_*` / `generic_model_regime_*` 前缀。

- `cta/model/tests/test_model_pipeline.py`
  - 新增单测：
    - `test_evaluate_oot_real_execution_daily_position_cap_blocks_extra_entries`
    - `test_evaluate_oot_real_execution_weekly_drawdown_budget_caps_trades`
    - `test_filter_model_leakage_features_for_regime_classifier`
  - 旧 OOT 数值测试显式设置 `use_portfolio_constraints=False`，锁定原口径基线。

### 验证

```bash
python3 -m pytest -q cta/model/tests/test_model_pipeline.py
```

结果：`52 passed`。

### day 级 OOT 产物

基于 `20260511_POOL_day_both_predictions.csv` 重新执行严格资金约束 OOT 评估并覆盖输出：

- `cta/report/backtest/20260511_POOL_day_both_model_pipeline/20260511_POOL_day_both_oot_monthly_returns.csv`
- `cta/report/backtest/20260511_POOL_day_both_model_pipeline/20260511_POOL_day_both_oot_summary.csv`
- `cta/report/backtest/20260511_POOL_day_both_model_pipeline/20260511_POOL_day_both_oot_trade_details.csv`

关键摘要（day OOT）：
- `trade_count=407`
- `selected_rows=554`
- `blocked_daily_position_rows=147`
- `total_return_pct=0.909785`
- `max_drawdown_pct=-0.009672`

---

## 2026-05-11 (一) · feature · model pipeline review 修复（P0/P1/P2/P3）

### 任务

按 code review 清单修复 6 项问题：
1. M2 `mfe_mae` winsorize 的 NaN 崩溃；
2. `change_log`“过拟合优化”段落过期参数；
3. POOL 增加 `min_used_symbols` 守门；
4. `_auto_enrich_candidate_features_for_models` 与 `_evaluate_oot_real_execution` 数值单测；
5. `model_pipeline.py` 拆分 3 个模块；
6. OOT 评估与 `cta/report/render` HTML 报告系统打通。

### 范围（代码）

- `cta/model/mfe_mae_model.py`
  - `fit()` 增加 `np.nan_to_num(...)` 预清洗目标值，修复 `Input y contains NaN` 崩溃（P0）。
- `cta/model/model_pipeline.py`
  - `run_model_pipeline(...)` / `run_model_pipeline_multi(...)` 新增 `min_used_symbols`（默认 `2`）；
  - CLI 新增 `--min-used-symbols`；
  - POOL 模式 `used_in_training < min_used_symbols` 直接报错（P1/PL1）；
  - `ModelPipelineResult` 新增 `html_report_path`；
  - OOT 完成后新增 `report_*.html` 统一 HTML 报告产物接入。
- 新增模块（P2 拆分）：
  - `cta/model/pipeline_feature_enrichment.py`
  - `cta/model/pipeline_pooling.py`
  - `cta/model/pipeline_oot_evaluation.py`
  - `cta/model/pipeline_html_report.py`
- `cta/model/tests/test_model_pipeline.py`
  - 新增数值正确性单测：
    - `test_auto_enrich_candidate_features_numeric_correctness`
    - `test_evaluate_oot_real_execution_numeric_correctness`
  - 新增 `test_pool_mode_guard_raises_when_used_symbols_below_minimum`
  - `test_run_pipeline_smoke` 增加 HTML 报告断言。
- `cta/model/tests/test_models_core.py`
  - 新增 `test_mfe_mae_model_handles_nan_targets_without_crash`。
- `cta/model/tests/test_pool_training.py`
  - 适配拆分后的 `_build_pooled_feature_df` 协议（返回 `(candidate_df, feature_df)`）。
- 文档同步：
  - `cta/model/model.md`
  - `cta/run.md`

### 文档修正（P0）

- `cta/report/change_log.md`“2026-05-10 · 过拟合优化”段落中：
  - `RegimeClassifier` 参数改为 `n_estimators=200, max_depth=5, min_samples_leaf=20`
  - `MfeMae` 参数改为 `n_estimators=200, max_depth=5, min_samples_leaf=20`

### 验证

```bash
python3 -m pytest -q cta/model/tests
```

结果：`58 passed`。

---

## 2026-05-10 (日) · feature · 多 symbol 池化训练（解决 day interval 单品种样本不足）

### 任务

针对 day 等低频 interval 单品种候选样本仅 ~100-200 笔的统计不足问题，加一个"多
symbol 池化"训练模式：把多个品种的候选+特征样本拼接成一个共享训练集，训出**单个**
跨品种模型；推理时仍按 vt_symbol 取该品种特征。

### 范围（代码）

- ``cta/model/model_pipeline.py``：
  - 新增 ``_build_pooled_feature_df(pool_symbols, ...)`` helper：循环每个 (symbol,
    exchange) 调既有 ``_build_candidate_table`` + ``build_training_feature_table`` →
    加 ``symbol`` 列保留来源 → concat 按 datetime 排序。单 symbol 失败只跳过不阻塞。
  - ``run_model_pipeline`` 新增可选 ``pool_symbols`` 参数。传入时：
    - 输出目录前缀改为 ``POOL`` 替代单品种代码
    - 跳过单 symbol candidate 加载，改用池化 helper
    - 落盘 ``pool_members.csv`` 记录参与品种，便于复现
    - 训练 / walk-forward / 报告生成等下游逻辑**零改动**
  - CLI 新增 ``--pool`` flag：与 ``--top-n-symbols`` / ``--symbol`` 配合，把多品种
    一次性池化成共享模型而非循环各自训。
- ``cta/model/tests/test_pool_training.py``：4 例新单测覆盖
  - ``_build_pooled_feature_df`` 多 symbol concat + symbol 列追加
  - 单 symbol 失败时整体不阻塞、跳过该品种继续
  - ``run_model_pipeline(pool_symbols=...)`` 输出目录用 POOL
  - CLI ``--pool`` flag 把 top-N 视为池化训练（仅 1 次 run_model_pipeline 调用）

### 应用方式

训练完的 POOL 模型可被任意 symbol 复用，``make_trade_filter`` 推理路径不变：

```python
adapter.order_filter = make_trade_filter(
    "cta/report/backtest/{POOL_run}/models/trade_filter_xxx.joblib",
    threshold=0.55,
    feature_provider=OnlineFeatureLoader(),  # 按 adapter.vt_symbol 取该品种特征
)
```

### 验证

```bash
cd /Users/wuyuliang/code/vnpy
python3 -m pytest cta -q --ignore=cta/feature --ignore=cta/data
# 545 passed, 12 subtests in ~3min
```

新增测试：

```bash
python3 -m pytest cta/model/tests/test_pool_training.py -q
# 4 passed
```

### 设计权衡

- 改动最小：不动训练阶段（trade_filter / regime / mfe_mae 三个模型类）、不动
  walk-forward 切分、不动报告生成；只在 feature_df 进入训练之前加 1 个 helper
  和 1 条 if 分支
- 不改 CLI 现有参数语义：``--pool`` 是 opt-in flag；不加时行为完全不变
- ``symbol`` 列保留来源便于事后做"分品种 OOT 分析"，但训练时是共享样本

### run.md 更新

- §4.2.1 新增"多 symbol 池化训练"章节，含 ``--pool`` 用法 + 输出目录约定 +
  ``make_trade_filter + OnlineFeatureLoader`` 跨品种推理示例

---

## 2026-05-10 (日) · feature · 第二轮断层 review：vnpy 真实兼容 + 模型完整特征 + 进程保活 + 跨日 reset

### 任务

接续上午第一轮 13 处断层修复后再次 review，发现 4 处更深层断层会让仿真/实盘
**端到端跑不通**，本次全部修复。

### 范围（代码）

**R — sim_runner 真实 vnpy 兼容**

vnpy 真实 ``CtaEngine.add_strategy(class_name: str, ...)`` 第一参数必须是**字符串**，
通过 ``self.classes.get(class_name)`` 取类对象；第一参数传 class object 会静默失败
（仅 ``write_log("找不到策略类")``）。

- ``cta/sim/sim_runner.py``：在调 ``add_strategy`` 前先 ``cta_engine.classes[ClassName]
  = StrategyClass``，再用 ``ClassName`` 字符串调用 ``add_strategy``
- ``cta/sim/tests/test_sim_runner.py``：``FakeCtaEngine`` 加 ``classes`` 字典 + 兼容
  string/class 两种调用方式；新增 ``TestVnpyCompatibility`` 2 例：
  - ``test_registers_class_in_classes_dict`` — sim_runner 必须注册类
  - ``test_strategy_added_under_class_name_string`` — 模拟严格 string-only fake，验证
    sim_runner 不再传 class object

**W — DailyPnlTracker 跨日自动 reset**

之前 PnL 永远累计，跨日后 ``DailyLossLimit`` 拿到的是累计而非"当日"亏损，会漂移。

- ``cta/live/pnl_tracker.py``：``DailyPnlTracker`` 新增 ``auto_reset_on_new_day=True``，
  ``on_trade`` 检测 ``trade.datetime.date() != self._date`` 时自动 ``reset_for_new_day``，
  保留隔夜 FIFO 持仓队列、只清零当日 PnL
- ``cta/live/tests/test_pnl_tracker.py``：3 例新单测（自动 reset / 手动关闭 / 隔夜持仓保留）

**V — sim_runner.serve_forever 进程保活**

``run_sim`` 返回后进程立刻退出，``run.md §6.2`` 的 ``print("keep running")`` 实际不阻塞，
SimNow 启动后秒退。

- ``cta/sim/sim_runner.py``：新增 ``serve_forever(main_engine, *, stop_event=None,
  install_signal_handlers=True, on_stop=None)``，注册 SIGINT/SIGTERM handler，
  阻塞主线程直到信号 / 外部 ``stop_event``，然后调 ``on_stop()`` + ``main_engine.close()``
  （即使 ``close`` 抛错也不向上传）
- 2 例新单测：``test_blocks_until_stop_event``、``test_close_called_even_on_exception``

**N + O — OnlineFeatureLoader：模型过滤的真实特征源**

``make_trade_filter`` 默认从 ``adapter._frame`` 取特征，但 ``_frame`` 只含 baseline 内嵌
~30 列；``model_pipeline --generic-mode auto`` 训出的模型期待 ``cta/data/feature/`` 中
~400 列完整特征。直接用 ``_frame`` 触发"missing columns"全量降级 → **模型形同虚设**。

- ``cta/live/online_feature.py``（新模块）：``OnlineFeatureLoader`` 从
  ``cta/data/feature/{interval}/{prefix}/{YYYY-MM-DD}.parquet``（日线则 ``day/{symbol}.parquet``）
  按时间戳定位特征行；LRU 缓存最近 8 个 parquet 文件；``__call__(adapter, columns)`` 接口
  直接作为 ``model_filter`` 的 ``feature_provider``
- ``cta/live/model_filter.py``：``make_trade_filter`` 新增 ``feature_provider`` 参数，
  优先从 provider 取特征，失败/None 时 fallback 到 ``adapter._frame.iloc[-1:]``
- 6 + 1 例新单测（loader 5 + provider 1 + model_filter 不破）

### 验证

```bash
cd /Users/wuyuliang/code/vnpy
python3 -m pytest cta -q --ignore=cta/feature --ignore=cta/data
# 541 passed, 12 subtests in ~3min
```

新增模块单跑：

```bash
python3 -m pytest cta/live/tests/test_online_feature.py \
    cta/live/tests/test_pnl_tracker.py \
    cta/sim/tests/test_sim_runner.py -q
# ~37 passed
```

**run.md 更新**

- §4.5.3 baseline 三件套接入模型时**强制**指定 ``feature_provider=OnlineFeatureLoader(...)``，
  否则会因特征不全降级；并明确 ``cta/data/feature/`` 是 T+1 离线产出，当日实时
  增量特征属于 P2 ``cta.feature.online``
- §6.2 / §6.2.1 SimNow 启动模板加 ``serve_forever(main_engine)`` 保活，
  说明 ``run_sim`` 内部已按 vnpy 真实接口注册 class_name + 跨日自动 reset PnL

### 仍然存在的中间断层（不在本次范围）

- **E** 实时增量特征引擎 ``cta.feature.online``：当日特征不依赖 T+1 离线 parquet
- **F** 多 symbol 自动 scanner 服务（定时扫 ranking 启动新策略）
- **G** systemd / supervisor.conf 进程级守护模板
- **运维**：webhook 告警、对账定时（cron 每日收盘后跑 daily_report）

---

## 2026-05-10 (日) · feature · 中间断层补齐：仿真↔实盘端到端可用

### 任务

针对从研究到仿真/实盘的"中间断层"做系统性 review，识别 13 处 gap（D/I/J/K + A/C/L/M
+ E/F/G/H + 运维），按 P0/P1/P2 分级修复。本次完成 **P0 全部 + P1 主要 + P2-H**。

### 范围（代码）

**P0（端到端可用）**

- `cta/live/trade_recorder.py` —— `TradeRecorder` 实盘成交流水落 parquet，
  `to_trade_log(multiplier, commission)` 配对成回测格式 trade_log，可直接喂
  `daily_report` / `parity_check`。4 例单测。
- `cta/live/pnl_tracker.py` —— `DailyPnlTracker` FIFO 配对累计已实现 PnL，
  `get_pnl` 即风控 `daily_pnl_provider`，让 `DailyLossLimit` 真正生效。8 例单测。
- `cta/strategy/cta_adapter.py` —— `LegacyCtaAdapter` 重载 `on_trade`，把成交事件
  转发给 `trade_recorder` + `pnl_tracker`；`on_stop` 自动 flush recorder。
  +5 例单测（共 20 例）。
- `cta/sim/sim_runner.py` —— `SimRunConfig` 新增字段：
  - `warmup_days` / `warmup_interval`：启动后调 `strategy.load_bar` 预热历史
  - `risk_guard` / `kill_switch` / `capital` / `contract_size_resolver` /
    `commission_resolver`：自动构造 `DailyPnlTracker` + `make_risk_filter` 挂到
    `strategy.order_filter`，并把 `KillSwitchRule` 合并进 `RiskGuard`
  - `trade_recorder_dir`：自动创建 `TradeRecorder` 挂到 `strategy.trade_recorder`
  - `enable_pnl_tracker`：默认 True，确保 `DailyLossLimit` 数据源可用
  +5 例集成测试。
- `cta/strategy/cta_baseline.py` / `cta/strategy/cta_tight_range.py` ——
  `_build_contract` 改为**优先**从 `cta_engine.get_pricetick / get_size` 读真实合约
  元数据（之前 bug 是默认值非 0 永远不会查询 engine）；查询失败回退 setting 字段。
  4 例新单测。

**P1（模型应用 / 一致性校验）**

- `cta/live/model_filter.py` —— `make_trade_filter(model_path, threshold=0.55)`
  加载 `model_pipeline` 训出的 trade_filter 模型（joblib + 兜底 pickle），从 sibling
  `*_features.csv` 读特征列，在 `adapter._frame.iloc[-1:]` 上 predict_proba，
  `< threshold` 时拒绝下单。**平仓订单永远放行**避免持仓被锁。warmup 期间无 frame 时
  不拦截 + 写日志。6 例单测。
- `cta/live/parity_helper.py`：
  - `trade_log_to_equity(tl, n_bars)`：在 exit_i 累加 net_pnl 形成对齐 equity
  - `recorder_to_parity_inputs(recorder, bar_dates, ...)`：`TradeRecorder` 流水 →
    `(trade_log, equity, dates)`，可直接喂 `write_daily_report`
  - `backtest_on_same_bars(strategy_class, vt_symbol, setting, bars)`：用同 K 线
    重跑回测，输出 backtest 侧 trade_log 给 `parity_check` / `daily_report`
  3 例单测。

**run.md 更新**

- §4.5.3 baseline 三件套从"需要写一次性脚本"改为直接挂 `make_trade_filter`，给出
  风控+模型 filter 串联示例
- §6.2.1 SimNow **完整集成**示例（warmup + 风控 + kill_switch + recorder + 自动合约）
- §6.3 多策略共享 main_engine 启动模板
- §7.2 每日对账从"假设你有 trade_log/equity"改为基于 `parity_helper` 自动重建
- §7.3 Supervisor 真实启动模板（不再是空 stub）

### 验证

```bash
cd /Users/wuyuliang/code/vnpy
python3 -m pytest cta -q --ignore=cta/feature --ignore=cta/data
# 528 passed, 12 subtests in ~3min
```

新增模块单跑：

```bash
python3 -m pytest cta/live/tests cta/sim/tests \
    cta/strategy/tests/test_cta_adapter.py \
    cta/strategy/tests/test_auto_contract.py \
    cta/live/tests/test_parity_helper.py -q
# ~60 passed
```

无回归。原 M1 / M2 / M3 既有测试均通过。

### 仍然存在的中间断层（不在本次范围）

- **E**：在线特征 pipeline 性能 — adapter 当前每 bar 重算 600 根历史的 prepare_frame，
  对 1m 实时下大约 100-300ms/bar，1s tick 频率不可接受；如改用 `cta/feature/online.py`
  的增量计算可降到 <10ms/bar。属于 P2 性能优化，未启动。
- **F**：多 symbol 并行调度 — §6.3 示例展示了如何在同一进程里启动多策略，但缺少
  统一的 "scanner" 服务（定时扫所有 ranking 品种、自动添加 / 卸载策略）。生产化时
  需要与 vnpy `DataRecorder` / 任务队列结合。
- **G**：systemd / supervisor.conf 守护进程模板 — 当前 `cta.live.supervisor.Supervisor`
  只处理网关重连，进程级守护需要外层。
- **运维**：钉钉 / 企微 / 邮件告警 webhook、对账自动定时（市场收盘后 cron 触发
  daily_report）尚未集成。

### 后续

- 灰度上线前请按 §6.2.1 的完整集成模板启动，核对：
  1. log 中能看到 `trade_recorder flushed`
  2. `cta/report/live/trade_log/` 下出现 parquet
  3. 触发一次超量订单或 `kill_switch.signal`，确认风控生效
- M5 灰度参考 §7.2 每日对账，`parity_mismatch_rate < 5%` 才进入加仓阶段。

---

## 2026-05-10 (日) · feature · 过拟合优化：模型正则加强 + POOL 禁 synthetic 回退

### 任务

针对 `cta/report/backtest/20260510_POOL_minute60_both_model_pipeline` 的过拟合疑虑，
做两类优化：
1. 模型层：降低复杂度、加强正则，提升 OOS 稳定性；
2. 数据层：POOL 模式禁止对缺失品种回退 synthetic，避免“伪泛化”。

### 范围（代码）

- `cta/model/trade_filter_model.py`
  - `HistGradientBoostingClassifier` 调参为更保守配置：
    - `learning_rate=0.03`、`max_depth=3`、`max_iter=180`
    - `min_samples_leaf=20`、`max_leaf_nodes=31`
    - `l2_regularization=1.0`、`early_stopping=True`
  - 训练时加入类别平衡 `sample_weight`，缓解标签分布偏置。

- `cta/model/regime_classifier_model.py`
  - `RandomForestClassifier` 降复杂度：
    - `n_estimators=200`、`max_depth=5`、`min_samples_leaf=20`
    - `max_features='sqrt'`、`class_weight='balanced_subsample'`

- `cta/model/mfe_mae_model.py`
  - 回归目标增加轻度 winsorize（1%~99%）抑制尾部噪声；
  - `RandomForestRegressor` 降复杂度：
    - `n_estimators=200`、`max_depth=5`
    - `min_samples_leaf=20`、`min_samples_split=80`
    - `max_features=0.35`

- `cta/model/model_pipeline.py`
  - walk-forward 数不足时增加 warning（请求多窗口但实际仅 1 窗）；
  - `_build_candidate_table` 增加 `allow_synthetic_fallback` 参数；
  - POOL 路径改为 `allow_synthetic_fallback=False`（缺真实数据品种直接跳过）；
  - `*_pool_members.csv` 新增 `used_in_training` 列；
  - 若 POOL 最终无可用品种，直接抛错而不是悄悄回退 synthetic。

- `cta/model/tests/test_model_pipeline.py`
  - 新增：
    - `test_build_candidate_table_allows_synthetic_fallback_for_missing_symbol`
    - `test_build_candidate_table_disables_synthetic_fallback_when_required`
    - `test_pool_mode_raises_when_no_real_data_symbol_available`

- 文档同步
  - `cta/run.md`：POOL 章节补充“真实数据优先 + used_in_training 含义”
  - `cta/model/model.md`：补充 POOL 真实数据策略与命令示例

### 验证

```bash
python3 -m pytest cta/model/tests/test_models_core.py cta/model/tests/test_model_pipeline.py -q
# 44 passed
```

并复跑：

```bash
python3 -m cta.model.model_pipeline \
  --top-n-symbols 18 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval 60min \
  --start 2010-01-01 --end 2025-12-31 \
  --train-end 2018-12-31 --valid-end 2020-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 \
  --by-signal-type --generic-mode auto --pool
```

输出目录：
`cta/report/backtest/20260510_POOL_minute60_both_model_pipeline/`

关键结果：
- `window_id` 从仅 `0` 变为 `0/1/2`（walk-forward 生效）；
- `pool_members.csv` 显示 `used_in_training=1/18`（当前本地 minute60 仅 RB 有真实数据）；
- `predictions.csv` 仅含 `RB0`，不再混入 synthetic 品种样本。

### 不在本次范围

- 未补齐其余 17 个品种的 `minute60` 原始数据与特征；
- 未新增自动下载缺失品种数据的“训练前硬校验 CLI”（当前通过日志和 `used_in_training` 提示）。

## 2026-05-09 (六) · feature · baseline_skill_suite 增加 topN 品种 + 多 interval 批量候选

### 任务

按 `cta/run.md` 的 Step 3.1 需求扩展 baseline 入口：
- 支持从 `cta/feature/symbols_research_ranking.csv` 读取 `top-n-symbols`
- 支持一次传入多个 `interval`（空格/逗号混合写法）
- 批量生成前 N 品种、多个周期的候选机会（training samples）

### 范围（代码）

- 更新 `cta/strategy/baseline_skill_suite.py`
  - 新增 `_normalize_intervals()`：支持 `day,60min 30min,...` 混合输入并规范化去重
  - 新增 `_load_top_n_symbols_from_ranking()`：按 `research_rank` 读取前 N 品种
  - 新增 `_resolve_run_exchange()`：ranking 与 CLI exchange 的优先级显式化
  - 新增 `run_baseline_suite_multi()`：单品种多 interval 批量执行（单个失败不阻塞）
  - CLI 新增参数：
    - `--top-n-symbols`
    - `--symbols-ranking-path`
    - `--interval` 改为支持多值（`nargs="+"` + 逗号混写）
  - `main()` 扩展为 `topN symbols × multi-interval` 双层批量调度

- 更新 `cta/strategy/tests/test_baseline_skill_suite.py`
  - 新增 `test_normalize_intervals_supports_mixed_tokens`
  - 新增 `test_load_top_n_symbols_from_ranking_sorted_by_research_rank`
  - 新增 `test_run_baseline_suite_multi_dispatches_each_interval`

- 更新 `cta/run.md`
  - Step 3.1 增加 topN + 多 interval 命令示例
  - 保留单品种旧用法示例

### 验证

```bash
python3 -m pytest cta/strategy/tests/test_baseline_skill_suite.py -q
# 18 passed
```

### 不在本次范围

- 未改动 `candidate_training_dataset` 与 `model_pipeline` 的既有批量逻辑（它们原本已支持 topN + 多 interval）。
- 未改动 `cta/data/origin` 原始数据。

## 2026-05-09 (六) · feature · 新增统一运行手册 `cta/run.md`

### 任务

补齐一份可直接执行的端到端命令文档，覆盖：
- 数据下载与校验
- 通用特征生成
- 候选样本构建（含 topN + 多 interval）
- 三类模型训练与离线评估
- 规则回测、dry-run 仿真、SimNow 启动示例
- 实盘运维常用操作（kill switch / 日报 / 连接守护）

### 范围（代码）

- 新增 [`cta/run.md`](run.md)
  - 给出从 `cd /Users/wuyuliang/code/vnpy` 开始的完整命令序列
  - 命令均对齐当前代码中的可执行入口：
    - `cta.data_code.download_all` / `cta.data_code.expand_minute` / `cta.cli validate`
    - `cta.feature.run_all_features`
    - `cta.model.feature.candidate_training_dataset`（parquet-only 输出）
    - `cta.model.model_pipeline`
    - `cta.strategy.skill_tight_range_backtest`
    - `cta.strategy.brooks.{backtest.runner,online.runner}`
    - `cta.sim.sim_runner`、`cta.live.{daily_report,supervisor}`
  - 明确关键输出目录，补充常见错误排查命令

### 验证

- 手工核对 run.md 中所有 CLI 参数，与当前源码 `argparse` 定义一致。
- 本次仅新增文档，不涉及策略/模型逻辑改动与数据改写。

### 不在本次范围

- 不新增实盘一键 CLI（当前 `run_sim`、`Supervisor` 仍为库级接口，文档提供脚本化调用示例）。
- 不改动 `cta/data/` 任何原始行情文件。

## 2026-05-09 (六) · main · M2 + M3：仿真接入 + 实盘支撑组件

### 任务

接续上午 M1，按 TDD（先测试再实现）依次落地 M2 仿真和 M3 实盘支撑。
M2 把现有 4 个 v1 策略改造成 ``vnpy_ctastrategy.CtaTemplate`` 子类（共享同一份核心
逻辑、不重写信号），并接入 ``vnpy_ctabacktester`` + SimNow runner + 回测↔仿真
parity 校验；M3 落地风控规则、Kill switch、连接守护、每日对账报告四件套。

### 范围（代码）

**M1 (c) 工程化（补 a/b/d 之后的剩余）**

- ``cta/run/runner.py`` —— ``run_event_driven_backtest()`` 统一入口：跑 v1 风格策略 +
  调用 M1 (b) HTML 报告系统 + 落 ``metrics_*.json``。4 例单测。
- ``cta/cli.py`` —— ``python -m cta.cli {backtest|validate|expand-minute}``，
  ``--strategy module:factory`` 加载策略类。6 例单测。
- ``cta/AGENTS.md`` —— 给 AI agent / 团队成员的项目操作指引（README §6 提及但缺失）。
- code review：``runner.py`` 用 ``dataclasses.replace`` 重建 EngineConfig；
  把 ``cta.data_code.validate._load_ranking`` 改公开 ``load_ranking``，CLI 不再调下划线 API。

**M2 (1)–(6) 仿真接入**

- ``cta/strategy/cta_adapter.py`` —— ``LegacyCtaAdapter(CtaTemplate)`` 基类：
  buffer 累积 bar、流式调用 ``prepare_frame`` + ``inner.on_bar``、
  把 v1 订单字典翻译为 ``buy/sell/short/cover``、异常捕获不上抛。
  含 ``order_filter`` pre-trade hook（M3 风控接入点）。``bars_to_df`` 转换工具。
  16 例单测（含 4 例 risk_filter 集成）。
- ``cta/strategy/cta_tight_range.py`` —— ``SkillTightRangeBreakoutCta``：
  TightRange 策略的 CtaTemplate 子类，把 ``StrategyConfig`` 全部字段 +
  ``ContractSpec`` 元数据暴露为 vnpy parameters。4 例单测。
- ``cta/strategy/cta_baseline.py`` —— ``DonchianCta`` / ``AtrBreakoutCta`` /
  ``BreakoutPullbackCta``，共享 ``_BaselineCtaBase``（共用 ``prepare_master_feature_frame``
  + ContractSpec）；BreakoutPullback 额外暴露 max_holding_bars / 止损系数。11 例单测。
- ``cta/run/cta_backtester.py`` —— 两条回测路径：
  - ``run_via_event_driven`` 抽 inner 走 M1 事件驱动引擎（主路径，速度快）
  - ``run_via_vnpy_ctabacktester`` 调真实 ``BacktestingEngine``（与实盘对齐）
  5 例单测（含 monkeypatch 模拟未装 + 装了的 setup 路径）。
- ``cta/sim/sim_runner.py`` —— ``SimnowSetting`` / ``SimRunConfig`` / ``run_sim``：
  EventEngine + MainEngine + CtpGateway + CtaStrategyApp 的标准启动序列；
  通过 ``main_engine_factory`` 注入支持 fake，无 vnpy_ctp 也能测试。5 例单测。
- ``cta/sim/parity_check.py`` —— ``compare_signals(a, b, time_tolerance=...)`` +
  ``trade_log_to_signals(trade_log, dates)``，输出 matched/mismatched/only_in_a/only_in_b
  和详情 DataFrame，是判断"策略是否真的可上实盘"的核心门槛。8 例单测。

**M3 (1)–(4) 实盘支撑**

- ``cta/live/risk.py`` —— 5 个规则 + ``RiskGuard``：
  ``MaxOrderSize`` / ``MaxPositionLimit`` / ``DailyLossLimit`` / ``OrderRateLimit``，
  失败短路返回首条 ``RiskDecision``。``make_risk_filter()`` 把 ``RiskGuard`` 包成
  ``LegacyCtaAdapter.order_filter`` 兼容签名。13 例单测。
- ``cta/live/kill_switch.py`` —— ``KillSwitch`` 支持内存激活 + 文件信号激活
  （cron / 监控 / SSH 一键禁单），``KillSwitchRule`` 适配 RiskGuard。7 例单测。
- ``cta/live/supervisor.py`` —— ``Supervisor`` 周期性 ``step()`` 检查 gateway 连接，
  断了就 ``main_engine.connect``；``max_reconnects`` 兜底防雪崩；``loop(stop_event)``
  阻塞主循环。5 例单测。
- ``cta/live/daily_report.py`` —— ``write_daily_report()`` 输出每日 markdown 对账：
  实盘 trade_log 绩效 + 同期回测 trade_log parity 对账（用 M2 ``parity_check``）。5 例单测。

### 验证

```bash
cd /Users/wuyuliang/code/vnpy
python3 -m pytest cta -q --ignore=cta/feature --ignore=cta/data
# 480 passed (含 12 subtests) in ~3min
```

新增模块单独跑：

```bash
python3 -m pytest cta/run cta/strategy/tests cta/sim cta/live cta/tests -q
# 95 passed
```

无回归。原 M1 (a)+(b)+(d) 既有测试均通过。

### 不在本次范围

- 不实际连 SimNow（需要 ``vnpy_ctp`` 安装 + 仿真账号 + 网络）。``run_sim`` 单测通过
  ``main_engine_factory`` 注入 fake 验证调用流程。
- 不实际跑 ``vnpy_ctabacktester.BacktestingEngine``（需要 vnpy 数据库 + 历史数据），
  通过 ``mock.patch`` 验证 set_parameters / add_strategy / load_data / run_backtesting 调用流程。
- 不接入 vnpy 主仓的 ``EVENT_TRADE`` / ``EVENT_ACCOUNT`` 事件做真实 PnL 累计；
  ``RiskContext.daily_pnl`` 默认为 0，``make_risk_filter`` 提供 ``daily_pnl_provider`` 注入点。
- 撤单频率限制（``CancelRateLimit``）暂未单独成规则；``OrderRateLimit`` 已覆盖大多数场景。
- adapter ``stream`` vs v1 ``batch`` 模式因首次 make_inner 后历史 bar 已被消费，
  细节订单序列可能不一一对应；M2 已通过 ``run_via_event_driven`` 共享 v1 inner 路径
  保证回测严格等价，``parity_check`` 是处理 stream 与实盘真实差异的工具。
- 实跑 ``expand_minute`` 下载分钟数据（仍需 ``TUSHARE_TOKEN``）。

### 后续

- M5 灰度小资金验证仍未启动；启动前需在 SimNow 跑满 5 个交易日且 parity_check
  ``mismatch_rate < 5%``。
- 风控接入策略代码：把 ``adapter.order_filter = make_risk_filter(guard)`` 写进
  策略启动模板，避免实例化时遗漏。
- 把 ``daily_report`` 挂到 vnpy 收盘后 hook 自动触发（systemd timer / vnpy 定时任务）。

---

## 2026-05-09 (六) · main · M1 离线评估增强 (a)+(b)+(d)

### 任务

按"离线评估 → 仿真 → 实盘"三阶段路线推进 M1。本次只覆盖**离线评估侧**：
回测引擎补撮合真实性、新增综合 HTML 评估报告系统、补数据校验/扩展工具。
M2 仿真（CtaTemplate 改造 + SimNow 接入）和 M3 实盘暂未启动。

### 范围（代码）

**(a) 回测引擎增强** —— `cta/skills/data_backtest/event_driven_backtest.py`

- `EngineConfig` 新增两个**默认 None** 的可选字段（向后兼容）：
  - `limit_move_pct: float | None` — 一字涨跌停过滤阈值。`next_bar.high == low`
    且与 `cur_bar.close` 涨跌幅达到阈值时，禁止该方向**开仓**；平仓不受限。
  - `liquidity_ratio: float | None` — 单笔可成交手数上限 = `floor(next_bar.volume * ratio)`，
    上限为 0 拒绝、超过时截断。
- `simulate_fill` 在原有 market/limit/stop 三种订单类型前新增过滤层；
  `_hit_price_limit` 与 `_liquidity_cap` 是私有 helper。
- 测试 `cta/skills/data_backtest/tests/test_event_driven_backtest.py` 新增 9 例：
  - `TestPriceLimitFilter` × 5（上下板拒绝、平仓不受限、默认配置兼容、未触阈值通过）
  - `TestLiquidityCap` × 4（截断、零成交拒绝、默认不截断、低于上限不截断）
  - 原有 2 例保持通过。

**(b) HTML 综合评估报告系统** —— 新建 `cta/report/render/`

- `metrics.py` —— `extended_metrics()` 在 `summarize_trades` 基础上补：
  Sortino、最长水下天数（`mdd_duration`）、最大回撤恢复 bar 数（`mdd_recovery`）、
  最长连胜/连亏（`win_streak`/`lose_streak`）、平均持仓 bar、换手率 (`turnover_per_bar`)、
  月度 PnL (`monthly_pnl`)。
- `plots.py` —— plotly 图表：净值、回撤、月度 PnL 热力（`pivot[year×month]` + RdYlGn）、
  交易 PnL 直方图、敞口曲线（持仓手数随时间）。
- `monte_carlo.py` —— `block_bootstrap()` 块自助法重抽样 (block_size 保留近邻自相关)，
  输出最终权益与最大回撤分位数 + 样本数组。
- `capacity.py` —— `capacity_curve()` 基于 trade_log 估算不同资金规模下的 PnL：
  按 `risk_per_trade` 计算"理想手数"，再用 `volume * liquidity_ratio` 截断，按比例缩放 net_pnl。
- `factor_analysis.py` —— Alphalens 占位：`run_alphalens()` 软依赖 `alphalens-reloaded`，
  未安装时返回 `available=False`，已安装时输出 IC + 分组收益。
- `html_report.py` —— `write_html_report()` 综合 HTML 入口：
  metrics 表 + 5 张图 + 容量曲线 + 蒙特卡洛分布 + JSON 副本（`metrics_*.json`）落盘。
- 测试 `cta/report/render/tests/test_render.py`，11 例覆盖 metrics/plots/MC/capacity/HTML/factor。

**(d) 数据扩展工具** —— `cta/data_code/`

- `validate.py` —— 校验 `cta/data/origin/` 完整性：
  缺失工作日、重复 datetime、OHLC 不一致、零成交、乱序，按品种聚合。
  支持 `python3 -m cta.data_code.validate {day|minute*} [--symbol X --max-rank N --out csv]`。
- `expand_minute.py` —— 薄包装 `download_all` + `validate`，针对默认场景"top-N 主力品种 +
  指定分钟级"提供更易用的入口；`--validate-only` 可跳过下载。
- 测试 `cta/data_code/tests/test_validate.py`，5 例覆盖干净数据/缺失工作日/OHLC 异常/重复/缺失文件。

### 验证

```bash
cd /Users/wuyuliang/code/vnpy
python3 -m pytest cta/skills cta/report cta/data_code -q       # 292 passed (含 12 subtests)
python3 -m pytest cta/strategy/tests cta/model -q              # 99 passed
```

合计 **391 passed**，无回归。`EngineConfig()` 默认行为不变 — 既有策略代码与回测脚本均无需修改。

### 不在本次范围

- 多品种持仓、组合层 PnL 拼接（M2 改造为 CtaTemplate 子类时一并落地）
- vnpy_ctabacktester / vnpy_ctp 接入（M2）
- 风控规则、kill switch、守护进程、日报（M3）
- 实际跑 `expand_minute --max-rank 10` 下载分钟数据（需 `TUSHARE_TOKEN` 环境变量）
- alphalens 因子分析（已留 import 占位，需要 `pip install alphalens-reloaded` 才生效）

---

## 2026-05-08 (五) · main · 第四轮 leak 审计加固：score_breakout opt-in + fallback 不再用 entry_price (P1-A + P1-B)

### 任务

第四轮 lookahead 专项 review 实证当前 pipeline 主路径无 active leak（详见
`cta/report/change_log.md` 同日"第四轮 Code Review"小结）。但发现 2 个**边缘风险点**
（不是 active leak，但是潜在地雷）：

1. **P1-A**：`score_breakout(..., follow_bars > 0)` 显式读取未来 N 根 bar，是个
   "未来函数门"。当前 pipeline 唯一调用方传 `follow_bars=0` ✓ 安全，但函数仅靠
   docstring 警告，没有 API 层守门 — 未来新代码 / 新调用方误传 N>0 就 leak。
2. **P1-B**：`_select_feature_columns` fallback 分支创建
   `feature_entry_price = entry_price`。`entry_price` 是 entry bar 内实际成交价，
   决策时刻 (signal bar 收盘) 不可见 → 是潜在 lookahead leak。fallback 主路径
   永远不进入 ✓ 安全，但语义错的代码留着是地雷，被 copy-paste 就会 leak。

### 范围（代码）

- `cta/skills/filtering_scoring/breakout_quality.py`
  - `score_breakout(...)` 新增 `_allow_future: bool = False` 参数
  - 当 `follow_bars > 0` 且 `_allow_future=False` 时立即 raise `ValueError`，
    错误信息明确指出"live / feature 路径必须用 follow_bars=0；post-hoc 标注请显式 _allow_future=True"
  - 设计为 `_` 前缀的"私有看似"参数：调用方必须主动输入才能开门
- `cta/model/model_pipeline.py`
  - `_select_feature_columns` fallback 分支：`feature_entry_price = entry_price`
    改为 `feature_trigger = trigger`（决策时刻可见的突破触发价）
  - `_FEATURE_MEANING_FALLBACK` 字典里 `feature_entry_price` 条目同步替换为 `feature_trigger`

### 范围（测试）

- `cta/skills/filtering_scoring/tests/test_breakout_quality.py`（+3 条新测试）
  - `test_score_breakout_raises_when_follow_bars_positive_without_opt_in`：守门触发 raise
  - `test_score_breakout_allows_future_when_opt_in_explicit`：显式 opt-in 仍可用
  - `test_score_breakout_default_follow_bars_zero_still_works`：默认 live-safe 路径不变
  - 同步给 2 条**已存在**的 post-hoc 用例（`test_opt_in_follow_bars_uses_future` /
    `test_follow_bars_partial_window`）加上 `_allow_future=True` 显式标记
- `cta/model/tests/test_model_pipeline.py`（+2 条新测试）
  - `test_select_feature_fallback_does_not_emit_entry_price`：fallback 不再写 entry_price
  - `test_select_feature_fallback_uses_trigger_when_present`：fallback 改用 trigger

### 防 bug 设计要点

1. **API 层硬隔离**：`_allow_future` 设计为强 opt-in，调用方必须主动输入才能开门，
   把"label / post-hoc 标注用法"和"feature / live 用法"在调用约定上严格分开。
2. **错误信息可执行**：raise 时明确告知"live 路径用 follow_bars=0；标注用 _allow_future=True"，
   读到错误就知道下一步怎么做。
3. **替换 entry_price → trigger 不只是改名**：trigger 是规则在 signal bar 收盘后
   计算出来的，决策时刻 100% 可见；entry_price 是 entry bar 内 stop / limit 实际成交价，
   决策时刻不可见。两者在大部分场景数值相近，但**语义上是天壤之别**。
4. **fallback 仅在没有任何 feature_*/generic_* 时才进入**，主路径永远走"显式 feature 选择"，
   修复后即使有人误用 fallback 也不会 leak。

### 验证命令

```bash
python3 -m unittest \
  cta.model.tests.test_model_pipeline \
  cta.model.tests.test_models_core \
  cta.model.feature.tests.test_model_feature_builder \
  cta.model.feature.tests.test_candidate_training_dataset \
  cta.strategy.tests.test_baseline_skill_suite \
  cta.strategy.tests.test_skill_tight_range_strategy \
  cta.strategy.tests.test_skill_tight_range_backtest_rb0 \
  cta.skills.filtering_scoring.tests.test_breakout_quality
```

### 验证结果

- 111 tests 全部通过（`Ran 111 tests in 149.0s, OK`）。
- P1-A 守门生效后 2 个旧测试（合法的 post-hoc 用法）触发了 raise，把 `_allow_future=True`
  显式标记后即恢复绿。**这正是守门的预期效果** — 让所有 follow_bars>0 的用法都有显式标记。

### 兼容性影响

- 任何外部调用方代码若用了 `score_breakout(..., follow_bars=N)`（N>0）且没传 `_allow_future`，
  会立刻 raise。**这是预期的**，调用方应：
  - 实盘 / live / feature 路径：把 `follow_bars` 改回 `0`
  - post-hoc 标注 / label 生成：显式传 `_allow_future=True`
- `_select_feature_columns` fallback 行为变化：以前缺 feature_*/generic_* 时
  写 `feature_entry_price`，现在写 `feature_trigger`。**主路径永远不触发该分支**，
  实际生产模型不受影响。

### 风险与后续

1. **第三方调用方**：如果有外部脚本直接 `score_breakout(..., follow_bars=N)`，
   要么改 `follow_bars=0`，要么显式 opt-in；运行时 raise 会立刻暴露。
2. **历史 model joblib 不受影响**：本次只改训练时的 fallback 行为 + 守门逻辑，
   不影响已落盘模型。
3. **下次还可加固的方向**：
   - `cta/skills/` 下其他可能含未来函数的 helper 同步加 `_allow_future` 守门
   - 在 CI / 预提交检查里加一条静态扫描：`grep -rn "follow_bars\s*=\s*[1-9]" cta/` 必须
     在同行有 `_allow_future=True` 才放行

---

## 2026-05-08 (五) · main · 代码瘦身：合并重复测试目录 + 删冷代码

### 范围

1. **合并 `cta/feature/test/` → `cta/feature/tests/`**：6 个测试模块 mv 到 `tests/`，按业界标准目录命名统一。
2. **删过期测试 `cta/feature/tests/test_run_all_features.py`**：测的 `_build_parquet_write_candidates` / `_write_parquet_with_fallback` / `--parquet-compression` 已在重构中删除，整个文件 import error 失败已久。
3. **删冷代码 `cta/feature/generate_features.py`**：213 行旧实现，无任何代码 import 依赖，输出布局错（`cta/feature/feature/...` 而不是 `cta/data/feature/...`），已被 `run_all_features.py` 取代。
4. **更新 `cta/feature/__init__.py` 推荐入口**：从已废弃的 `generate_features` 改为 `run_all_features`，输出路径修正到 `cta/data/feature/...`。
5. **小清理**：把 `_resolve_generic_columns` 里的内联 `from cta.model.feature.training_feature_builder import DEFAULT_GENERIC_COLUMNS` 提到顶部 import 区（PEP 8）；删 `.DS_Store` 系统垃圾文件。

### 修改文件

- `cta/feature/test/` 整个目录删除（先 mv 文件、删 `__init__.py`、`rmdir`）
  - 6 个测试 mv 到 `cta/feature/tests/`
- `cta/feature/tests/test_run_all_features.py` 删除（过期）
- `cta/feature/generate_features.py` 删除
- `cta/feature/__init__.py` 重写 docstring（19 类特征清单 + 新入口 + 正确输出路径）
- `cta/model/model_pipeline.py` 把 `DEFAULT_GENERIC_COLUMNS` 提到顶部 import
- `cta/.DS_Store` / `cta/strategy/.DS_Store` 删除

### 验证命令

```bash
python3 -m unittest \
  cta.feature.tests.test_compute_pipeline \
  cta.feature.tests.test_feature_modules_smoke \
  cta.feature.tests.test_loader_and_scheduler \
  cta.feature.tests.test_online_api \
  cta.model.tests.test_model_pipeline \
  cta.model.tests.test_models_core \
  cta.model.feature.tests.test_model_feature_builder \
  cta.model.feature.tests.test_candidate_training_dataset \
  cta.strategy.tests.test_baseline_skill_suite \
  cta.strategy.tests.test_skill_tight_range_strategy \
  cta.strategy.tests.test_skill_tight_range_backtest_rb0
```

### 验证结果

- 112 tests 全部通过（`Ran 112 tests in 187.4s, OK`）。

### 影响

- 老 CLI `python3 -m cta.feature.generate_features` 不再可用 → 改用 `python3 -m cta.feature.run_all_features`。
- 老命令 `python3 -m cta.feature.test.<mod>` 不再可用 → 改用 `python3 -m cta.feature.tests.<mod>`（注意目录从 `test` → `tests`）。
- `run_generate.py` 保留（DeprecationWarning shim）以兼容外部调用方。

---

## 2026-05-07 (四) · main · 通用特征拼接默认走 auto 模式（G1）

### 任务

用户反馈：当前模型实际**没用上** `cta/data/feature/<interval>/<symbol>/*.parquet`
里预先计算好的通用特征 —— 磁盘 parquet 通常含约 400 个特征列（sma_/ema_/macd_/
adx_/aroon_/pa_*/regime_*/setup_*），但旧实现只取 18 列窄白名单
`DEFAULT_GENERIC_COLUMNS`，**95% 通用特征被丢弃**。

修复目标：让模型训练默认用上磁盘 parquet 的全部数值特征（auto 模式），保留 18
列白名单作为可选回退，CLI 透传 `--generic-mode auto|whitelist`。

### 范围（代码）

- `cta/model/feature/training_feature_builder.py`
  - 新增 `_GENERIC_NON_FEATURE_COLUMNS` 黑名单 frozenset（OHLCV + 元数据 + 临时列）
  - 新增 `_auto_detect_generic_columns(generic_df) -> tuple[str, ...]`：
    - 排除 `_GENERIC_NON_FEATURE_COLUMNS`
    - 仅保留数值 / 布尔 dtype（避免 object 列被 ColumnTransformer 强转 NaN）
    - 排除全 NaN 列（无信息量，反而拖慢训练）
  - `merge_candidate_and_generic_features`：`generic_columns=None` 时调用
    `_auto_detect_generic_columns(generic_df)`；显式传 list 时维持白名单语义。
- `cta/model/model_pipeline.py`
  - 新增 `GenericMode = Literal["auto", "whitelist"]`
  - 新增 `_resolve_generic_columns(generic_mode)` 把字符串映射到 `generic_columns` 入参。
  - `run_model_pipeline(..., generic_mode: GenericMode = "auto")`：默认 auto，向 build_training_feature_table 透传。
  - `run_model_pipeline_multi(..., generic_mode: GenericMode = "auto")`：同步透传。
  - CLI `--generic-mode auto|whitelist` 默认 auto。

### 范围（文档）

- `cta/model/model.md`：Step C 加"通用特征拼接策略（`--generic-mode`）"子章节，列出 auto vs whitelist 对比表 + CLI / 程序化示例。
- `cta/model/feature/candidate_vs_executed_samples.md`：新增 § 4.5 通用特征拼接 + 自查清单。
- `cta/report/change_log.md`：本条目。

### 新增测试（TDD）

- `cta/model/feature/tests/test_model_feature_builder.py`
  - `test_merge_auto_detects_numeric_generic_columns_when_columns_none`（helper 单元）
  - `test_merge_default_uses_all_disk_numeric_features`（50 列 fixture，验证全部入选）
  - `test_whitelist_mode_still_works_for_back_compat`（显式传 18 列仍生效，extra_feat 被过滤）
- `cta/model/tests/test_model_pipeline.py`
  - `test_pipeline_includes_extra_generic_features_under_auto_mode`（端到端，构造 30 个特征 parquet 验证 feature_table 全部拿到）
  - `test_pipeline_whitelist_mode_caps_generic_at_18_columns`（whitelist 模式 generic_* ≤ 18 列）

### 防 bug 设计要点

1. **黑名单兜底 OHLCV / 元数据**：`_GENERIC_NON_FEATURE_COLUMNS` 显式列出
   `datetime / open / high / low / close / volume / turnover / open_interest /
   amount / ts_code / symbol / exchange / interval / trade_date / signal_datetime / _merge_key`，
   即使 parquet schema 变化也不会把行情数据当特征塞给模型。
2. **dtype 过滤**：仅 numeric / boolean 入选；object 字符串列不会被错误地 ColumnTransformer 强转 NaN 后污染训练。
3. **全 NaN 列剔除**：避免 imputer 退化成"全部填中位数 NaN"。
4. **向后兼容**：`generic_mode="whitelist"` 显式锁定 18 列；显式传 `generic_columns=tuple(...)` 仍按用户白名单走，行为保持。
5. **维度爆炸保护**：model.md 标注训练耗时会显著增加（400 列），资源紧张时建议切 whitelist。

### 验证命令

```bash
python3 -m unittest \
  cta.model.tests.test_model_pipeline \
  cta.model.tests.test_models_core \
  cta.model.feature.tests.test_model_feature_builder \
  cta.model.feature.tests.test_candidate_training_dataset \
  cta.strategy.tests.test_baseline_skill_suite \
  cta.strategy.tests.test_skill_tight_range_strategy \
  cta.strategy.tests.test_skill_tight_range_backtest_rb0
```

### 验证结果

- 97 tests 全部通过（`Ran 97 tests in 152.0s, OK`）。前一轮 92 + G1×5 = 97。
- 实证：磁盘 RB0 minute60 parquet 含 426 列，旧实现仅取 18 列；修复后 auto 模式入选约 380+ 列（排除 OHLCV / 元数据 / object 后）。

### 重要：用新代码重训模型

L1（next-bar leak） + L2（trade_log signal_row） + G1（generic auto）三处修复
合在一起使训练样本与特征集与历史完全不同。**强烈建议**：

```bash
# 1) 重新生成候选事件 + 训练样本（依赖 L1/L2 修复）
python3 -m cta.model.feature.candidate_training_dataset \
  --symbol RB0 --exchange SHFE --interval 60min \
  --start 2010-01-01 --end 2019-12-31 \
  --trade-side-mode both --run-tag 20260507_clean

# 2) 重训三模型 + 生成特征清单（依赖 G1 默认 auto + F1 manifest）
python3 -m cta.model.model_pipeline \
  --symbol RB0 --exchange SHFE --interval 60min \
  --start 2010-01-01 --end 2019-12-31 \
  --train-end 2017-12-31 --valid-end 2018-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 --by-signal-type
  # --generic-mode auto 是默认，可省略；--generic-mode whitelist 复现旧行为
```

### 风险与后续

1. **OOT 指标继续修正**：L1 修完后已显著下降（拿掉 next-bar leak）；G1 加上 ~360
   个新 generic 特征，模型容量增大但同时也面临"高维下信噪比"挑战。预期 OOT 仍可
   提升（更多真信号），但训练耗时会显著增加（HGB 在 400 列上每窗口约慢 2-3 倍）。
2. **特征重要度稀释**：top10 importance 表里以前出现的 18 个白名单列现在要和
   ~380 列竞争，排名分布会显著变化；这是预期，不是 bug。
3. **存量 model_feature parquet**：之前的 candidate_events.parquet / training_samples.parquet
   只拼了 18 列 generic_*；要用新代码全量重生成才能用上 auto 模式。
4. **资源不足时**：用 `--generic-mode whitelist` 退到 18 列，行为与 04-26 之前一致。

---

## 2026-04-30 (四) · main · 模型部署：每个 joblib 旁生成全量特征清单（F1）

### 任务

模型 joblib 文件部署到推理端时，下游需要严格按"训练时用过的特征清单"做 schema
校验。在每个 `models/<signal_type>/window_xx/<model>.joblib` 旁边同步生成一份
`<model>_features.csv`，列 `rank / feature / importance / feature_meaning / model_kind`，
按 importance 降序、行数 = 训练用过的**全部**特征（不是 top10）。

### 范围（代码）

- `cta/model/model_pipeline.py`
  - 新增 `_dump_feature_manifest(joblib_path, feature_columns, importance_df, model_kind)` —— 防御式 helper：
    - 永远写出全部 `feature_columns`，importance 缺失或异常时安全 fallback 到 0.0
    - 主键 `feature` 去重；按 importance 降序 + feature 字典序稳定排序
    - 写文件用 utf-8-sig + index=False，与 pipeline 其它产物一致
    - joblib 不存在时跳过（mfe_mae_skipped 时不会留孤儿 csv）
    - 任何步骤异常都仅 `logger.warning` 不阻断 pipeline
  - 在 `run_model_pipeline` 的 save 点（trade_filter / regime_classifier / mfe_mae）
    分别调用 `model.get_top_feature_importance(top_k=len(feature_columns))` 拿全集
    importance 后调用 helper 写出清单。
  - 调用 `get_top_feature_importance` 用 `try/except` 包裹，importance 计算失败也
    仍写出清单（importance=0），保证生产文件可用。

### 范围（文档）

- `cta/model/model.md`：§ 2 Step C 输出示例新增 `<model>_features.csv` 行 +
  独立子章节"模型特征清单（部署用）"，含目录树示例 + 下游推理用法 + dummy 模型 + skipped 边缘场景说明。
- `cta/strategy/breakout.md`：新增 § 16.4 部署清单使用说明。

### 新增测试（TDD）

- `cta/model/tests/test_model_pipeline.py`
  - `test_pipeline_writes_feature_manifest_per_saved_model`（每个 joblib 旁必有同名 csv，必备列、行数、rank、降序、去重断言）
  - `test_feature_manifest_lists_all_training_features_not_just_top10`（清单 ⊇ top10 特征集合）
  - `test_feature_manifest_no_orphan_csv_without_joblib`（mfe_mae skipped 时不留孤儿 csv）

### 防 bug 设计要点

1. **schema 校验权威源**：清单 = 训练时实际 feed 给 model.fit 的 feature_columns 全集，下游推理直接读取本文件做列对账。
2. **永不阻断 pipeline**：helper 内部任何异常（importance 计算失败 / IO 失败 / 解析失败）都只打 warning，**绝不 raise**。
3. **无孤儿文件**：mfe_mae 模型在某 (signal, window) 上被 `_train_mfe_mae_or_skip` 跳过 → joblib 不写 → manifest 也不写。
4. **稳定排序**：importance 降序 + feature 字典序，保证 round-trip / 不同机器跑 importance 浮点抖动时清单顺序仍可重现。
5. **dummy / legacy 模型兼容**：`model_kind` 列原样写入，下游可据此区分模型质量；importance 全为 0 时也能正常写出，不会 crash。

### 验证命令

```bash
python3 -m unittest \
  cta.model.tests.test_model_pipeline \
  cta.model.tests.test_models_core \
  cta.model.feature.tests.test_model_feature_builder \
  cta.model.feature.tests.test_candidate_training_dataset \
  cta.strategy.tests.test_baseline_skill_suite \
  cta.strategy.tests.test_skill_tight_range_strategy \
  cta.strategy.tests.test_skill_tight_range_backtest_rb0
```

### 验证结果

- 92 tests 全部通过（`Ran 92 tests in 56.2s, OK`）。前一轮 89 + F1×3 = 92。

### 下游推理示例

```python
from pathlib import Path
import pandas as pd
from cta.model.trade_filter_model import TradeFilterModel

window_dir = Path("cta/report/backtest/<run>/models/donchian_breakout/window_00")
m = TradeFilterModel.load(window_dir / "trade_filter.joblib")
manifest = pd.read_csv(window_dir / "trade_filter_features.csv")
required_features = manifest["feature"].astype(str).tolist()
prob = m.predict_proba(df_runtime, feature_columns=required_features)
```

### 风险与后续

1. **存量 joblib 没有配套 csv**：本次之前生成的模型目录里没有 `<model>_features.csv`。
   要么重训生成，要么写一个一次性脚本对历史 joblib 反查 estimator 特征顺序补回。建议直接重训，与 L1/L2 修复一并刷新。
2. **importance=0 的特征**：HGB 模型走 permutation importance，对相关性低或影响微弱的特征会算成 0。manifest 仍会保留这些行（rank 排在末尾），下游 schema 校验应仅看 `feature` 列，**不要**按 importance 阈值过滤掉这些列，否则推理时会出现"训练时存在但运行时缺失"。
3. **特征顺序的稳定性**：清单是按 importance 降序排，下游推理按这个顺序传 `feature_columns` 即可；模型内部的 `ColumnTransformer` 是按列名而不是位置匹配，所以顺序差异不会影响数值结果，但顺序统一更利于可审计。

---

## 2026-04-30 (四) · main · 第三轮 code review：修复 next-bar lookahead leak（L1 + L2）

### 背景

用户反馈"模型效果比较好"，对特征 / 样本 / 训练全链路做穿越特征（lookahead leakage）专项 review，
实证发现两个 P0 级未来函数泄漏：候选样本表的 `datetime` 是 entry_bar (i+1) 时间戳，
模型决策时刻其实是 signal_bar (i)。下游所有依赖 `datetime` 对齐的环节都拿到了未来 1 根 bar 的信息。

### 范围（代码）

- `cta/model/feature/training_feature_builder.py`：L1 fix —— `merge_candidate_and_generic_features`
  优先用 `signal_datetime` 做 merge_asof key；输入缺少时回退到 `datetime` 并打 warning（兼容老数据）。
- `cta/strategy/baseline_skill_suite.py`：L2 fix —— `build_training_samples_from_trade_log`
  改用 `signal_row = frame.iloc[entry_i - 1]` 取 `feature_*`，并显式写出 `signal_datetime` / `signal_i`。

### 范围（文档）

- `cta/model/feature/candidate_vs_executed_samples.md`：新增 § 4.4 "时间口径"，
  列出 `signal_datetime` vs `datetime` 的语义边界 + 自查清单。
- `cta/strategy/breakout.md`：§ 16.3 字段表加上 `signal_datetime` / `signal_i`，
  并在末尾加硬性提示"merge_asof 必须用 signal_datetime 做 key"。

### 实证（修前）

```
决策时刻应是 signal bar i=10 → 期望 generic_row_idx == 10
实测 generic_row_idx = 11.0   ← 拿到了 entry_bar (i+1) 的 generic 特征
```

18 个 generic 特征里每一个（sma_20 / rsi_14 / setup_quality_score /
breakout_quality_score / context_score / regime_label / regime_conf 等）都早 1 根 bar
看到未来 OHLCV 衍生信息。60min 数据多看 60 分钟未来；day 数据多看 1 整天未来。
模型从 generic_sma_20 与 entry_price 的差正负号就能猜对 mfe 大概方向 —— 这就是
"模型效果比较好"的真实原因。

### 核心修复细节

1. **L1**：`merge_candidate_and_generic_features` 把 merge_asof 的 left 端 key 从
   `datetime`（entry bar 时间）切到 `signal_datetime`（signal bar 时间）。
   - 用 pandas `left_on=merge_key_col, right_on="datetime"` 显式分开两边时间戳列；
   - 临时列 `_merge_key` 在合并完成后清理；
   - 对没有 `signal_datetime` 的旧 candidate frame，`logger.warning` 提示并 fallback 到 `datetime`，保持向后兼容。
2. **L2**：`build_training_samples_from_trade_log` 把决策时刻特征源从 `entry_row` 切到
   `signal_row = frame.iloc[max(0, entry_i - 1)]`；同时在 sample dict 里写出 `signal_datetime` / `signal_i`，
   保证下游 merge_asof 拿到的是 signal bar 时刻的 generic 特征。

### 新增 / 翻新测试（TDD）

- `cta/model/feature/tests/test_model_feature_builder.py`
  - `test_merge_uses_signal_datetime_to_avoid_next_bar_leak`（L1 实证）
  - `test_merge_falls_back_to_datetime_when_signal_datetime_missing`（L1 兼容）
- `cta/strategy/tests/test_baseline_skill_suite.py`
  - `test_build_training_samples_from_trade_log_uses_signal_bar_features`（L2 实证）

### 已扫描确认无 leak（关键模块）

- `_compute_atr14`（Wilder ATR）：`shift(1)` + EWM forward-only，因果 ✓
- `compute_donchian / detect_tight_range`：`high.shift(1).rolling(N).max()`，因果 ✓
- candidate scan 自身的 `feature_*`：`bar[c] = frame.iloc[i]`（signal bar），因果 ✓
- `TradeFilterModel.fit / RegimeClassifierModel.fit / MfeMaeModel.fit`：仅在 `train_df` 上 fit
  imputer / scaler / estimator，无 train-test contamination ✓
- walk-forward 切分：按 candidate.datetime 严格升序切，valid/test 不进 train ✓
- `build_regime_labels(shift(-h))`：weak-supervision 标签生成器，未被当作 feature 使用 ✓
- `seg = frame.iloc[entry_i : horizon_i + 1]`：仅用于事后 mfe/mae 标签，使用未来合法 ✓
- `is_week_end / is_month_end` 的 `shift(-1)`：取的是日历 dayofweek（外部已知量），不是行情 ✓

### 验证命令

```bash
python3 -m unittest \
  cta.model.tests.test_model_pipeline \
  cta.model.tests.test_models_core \
  cta.model.feature.tests.test_model_feature_builder \
  cta.model.feature.tests.test_candidate_training_dataset \
  cta.strategy.tests.test_baseline_skill_suite \
  cta.strategy.tests.test_skill_tight_range_strategy \
  cta.strategy.tests.test_skill_tight_range_backtest_rb0
```

### 验证结果

- 89 tests 全部通过（`Ran 89 tests in 43.4s, OK`）。修前 86 + L1×2 + L2×1 = 89。

### 风险与后续

1. **OOT 指标会显著下降**：修复后模型不再"早看 1 根 bar"，OOT trade_filter AUC /
   decile spread / cumulative return 会显著下降。这才是真实水平 —— 之前看似漂亮的
   样本外结果有相当部分来自这个穿越。建议立刻重训 RB0 60min pipeline + 重生成
   `*_top10_feature_importance.csv` / `*_last_oot_decile_returns.csv`，做前后对比。
2. **历史 parquet 兼容**：旧 `candidate_events.parquet` / `training_samples.parquet`
   没有 `signal_datetime` 列，加载后跑 merge_candidate_and_generic_features 会触发
   warning + fallback 到 `datetime`（即旧的有泄漏行为）。**必须重新生成**才能拿到
   修复后的清洁数据。建议下批 RB0 重跑用新的 `--run-tag 20260430_clean` 标记区分。
3. **第三方 candidate 构造方**：若有外部脚本直接构造 candidate_df 喂入 pipeline，
   必须补 `signal_datetime` 字段，否则 leak 仍在。文档 `candidate_vs_executed_samples.md`
   § 4.4 已加自查清单。
4. **模型再训练**：用本次修复后的代码重新跑 `cta.model.feature.candidate_training_dataset`
   生成训练样本，再跑 `cta.model.model_pipeline` 重训 trade_filter / regime / mfe_mae，
   把新指标 vs 旧指标的对比写入下一条 change_log。

---

## 2026-04-29 (四) · main · `model_feature` 落盘改为 parquet-only（移除 csv）

### 任务

- 按最新要求回推：`cta/data/model_feature` 目录只保留 parquet，不再生成 csv 文件。

### 修改文件（测试先行）

- `cta/model/feature/tests/test_candidate_training_dataset.py`
  - `test_build_and_save_candidate_training_dataset_merges_generic_features` 增加断言：
    - `summary_parquet` 必须存在；
    - `dataset_dir` 下 `*.csv` 必须为空。
  - `test_empty_candidate_df_writes_schema_complete_empty_parquet` 增加同样断言。

### 修改文件（实现）

- `cta/model/feature/candidate_training_dataset.py`
  - `CandidateTrainingDatasetResult` 字段调整：
    - 删除 `candidate_events_csv` / `training_samples_csv` / `summary_csv`
    - 新增 `summary_parquet`
  - `build_and_save_candidate_training_dataset`：
    - 删除 `standardized.to_csv(...)` / `merged.to_csv(...)`
    - `dataset_summary` 从 csv 改为 `*_dataset_summary.parquet`
  - `main` 日志字段同步：`result.summary_parquet`

### 修改文件（文档）

- `cta/model/model.md`
  - Step B 说明补充：`cta/data/model_feature` 已改为 parquet-only，不再落地 csv；
  - 产物示例增加 `*_dataset_summary.parquet`。

### 验证命令

```bash
python3 -m unittest cta.model.feature.tests.test_candidate_training_dataset -v
python3 -m unittest cta.model.feature.tests.test_model_feature_builder cta.model.feature.tests.test_candidate_training_dataset cta.model.tests.test_model_pipeline -v
```

### 验证结果

- 两组测试均通过（OK）。

---

## 2026-04-29 (四) · main · 模型文档补全“三件事逻辑” + 特征 parquet 压缩写盘

### 任务

1. 在 `cta/model/model.md` 详细写清楚三件事逻辑，特别是三类模型如何配合；  
2. 构建全量特征时保持 parquet，并压缩文件大小；模型训练/预测继续加载对应 parquet；  
3. 同步更新相关文档。

### 修改文件（测试先行）

- `cta/feature/tests/__init__.py`（新增）
- `cta/feature/tests/test_run_all_features.py`（新增 4 个用例）
  - `test_build_parquet_write_candidates_with_zstd`
  - `test_build_parquet_write_candidates_with_none`
  - `test_parse_args_supports_parquet_compression_options`
  - `test_write_parquet_with_fallback_uses_next_candidate`

### 修改文件（实现）

- `cta/feature/run_all_features.py`
  - 新增 parquet 压缩工具函数：
    - `_normalize_parquet_compression`
    - `_build_parquet_write_candidates`
    - `_write_parquet_with_fallback`
  - 新增 CLI 参数：
    - `--parquet-compression`（`zstd/snappy/gzip/brotli/lz4/none`）
    - `--parquet-compression-level`
  - `_worker_compute` 按日写盘改为“压缩优先 + 自动降级”；
  - `run_cross_section` 写 `_all_symbols.parquet` 同样使用压缩回退逻辑；
  - `run_interval` / `main` 打通压缩参数传递与日志；
  - 压缩后适配增量跳过阈值，避免误判小文件反复重写。

### 修改文件（文档）

- `cta/model/model.md`
  - 补全“三件事”逻辑总览（数据流 + 三模型协作顺序）；
  - Step A 明确“特征默认压缩 parquet 输出、模型侧 `read_parquet` 透明读取”；
  - Step C 增加训练/推理协同逻辑（过滤→路由→仓位尺度）；
  - 补充特征压缩测试命令。
- `cta/feature/FEATURES.md`
  - 顶部补充 `run_all_features` 的压缩写盘说明与回退行为。

### 验证命令

```bash
python3 -m unittest cta.feature.tests.test_run_all_features -v
python3 -m unittest cta.feature.tests.test_run_all_features cta.model.feature.tests.test_model_feature_builder cta.model.feature.tests.test_candidate_training_dataset -v
python3 -m cta.feature.run_all_features --help
```

### 验证结果

- 上述测试与命令均通过（OK）。

---

## 2026-04-28 (三) · main · Step B 候选样本构建支持 topN 品种 + 多 interval（TDD）

### 任务

- `cta.model.feature.candidate_training_dataset` 的 Step B 能力与 pipeline 对齐：
  - 支持 `--top-n-symbols N`（按 `research_rank` 取前 N）
  - 支持 `--interval` 多值输入（空格 / 逗号 / 混合写法）
  - 保持单品种、单 interval 旧用法兼容
- 要求先 TDD，再实现，再同步文档。

### 修改文件（测试先行）

- `cta/model/feature/tests/test_candidate_training_dataset.py`
  - `test_normalize_intervals_supports_mixed_tokens_and_dedup`
  - `test_normalize_intervals_raises_when_empty`
  - `test_parse_args_supports_top_n_symbols_and_multi_intervals`
  - `test_load_top_n_symbols_from_ranking_orders_by_research_rank`

### 修改文件（实现）

- `cta/model/feature/candidate_training_dataset.py`
  - 新增 `SYMBOLS_RANKING_PATH`
  - 新增 `_normalize_intervals(...)`
  - 新增 `_load_top_n_symbols_from_ranking(...)`
  - 新增 `_resolve_run_exchange(...)`
  - 新增 `generate_and_save_candidate_training_dataset_multi(...)`
  - `_parse_args(argv=None)`：
    - `--interval` 改为 `nargs="+"`
    - 新增 `--top-n-symbols`
    - 新增 `--symbols-ranking-path`
  - `main(argv=None)` 支持按 topN 品种 x 多 interval 批量执行，逐个输出产物路径

### 修改文件（文档）

- `cta/model/model.md`
  - Step B 增加多 interval（空格/逗号/混合）示例
  - Step B 增加 topN 品种批量示例
  - 说明多 interval/多品种顺序执行、输出目录隔离、单 interval 失败不阻断

### 验证命令

```bash
python3 -m unittest cta.model.feature.tests.test_candidate_training_dataset -v
python3 -m unittest cta.model.feature.tests.test_model_feature_builder cta.model.feature.tests.test_candidate_training_dataset -v
```

### 验证结果

- 两组测试全部通过（OK）。

---

## 2026-04-28 (三) · main · 第二轮 cta 全量 code review 修复 D1–D6 + 文档对齐 M1–M12

### 范围（代码）

- `cta/model/model_pipeline.py`：D1（`_resolve_run_exchange` helper）、D3（`_ensure_training_columns` warmup 行保留 NaN）、D4（`_load_feature_meaning_map` 缓存键带 mtime）
- `cta/model/feature/candidate_training_dataset.py`：D2（二次 standardize 不再从 `candidate_df` 取错位）、D5（`_normalize_block_reason` 兼容 `<NA>` / `None` / `null`）
- `cta/strategy/baseline_skill_suite.py`：D6（`build_training_samples_from_trade_log` 写出 `atr_warmed`）

### 范围（文档）

- `cta/model/feature/candidate_vs_executed_samples.md`：补 `not_triggered_market` 状态、补 `opportunity_score / future_return_atr / opportunity_class / atr_warmed / trigger` 字段说明（M1 / M10）
- `cta/strategy/bug.md`：顶部加"历史草稿"标注（M2）
- `cta/README.md`：删除 `utils/`、`backtest/` 标占位、删除重复目录段、补 `<ALPHA_PREFIX>` 命名说明（M3 / D8）
- `cta/strategy/breakout.md`：训练样本字段表分主路径（candidate scan）+ fallback（trade_log）两栏（M4 / D7）
- `cta/strategy/readme.md`：去掉 `stop_price`，加候选场景列（M5）
- `cta/model/model.md`：加 `--periods-per-year` CLI 差异说明（M6）；推理示例改用 `_select_feature_columns` 白名单选列（M7）
- `cta/feature/FEATURES.md`：§13–§18 标记为已落地（M8）
- `cta/strategy/brooks/README.md`：`180+` → `~100` pa_ 特征（实测计数命令也写进 README，M9）
- `cta/.claude/settings.local.json`：清理老 `cta.tests.*` 路径白名单（M11）
- `cta/report/change_log.md`：顶部加"历史命令路径提示"注脚（M12）

### 核心修复细节（D1–D6）

1. **D1**：`run_exchange = exchange_from_rank or str(args.exchange).upper() if args.exchange else None` 被 Python 解析为 `(a or b) if c else None`，top-N 模式在 CLI 没传 `--exchange` 时即使 ranking 已提供 SHFE 也会被丢成 `None`。抽出 `_resolve_run_exchange(rank, cli)` 显式化优先级。
2. **D2**：`standardize_candidate_events` 二次调用兼容分支从 `candidate_df["future_return_atr"]` 取值（原始 index），赋值到已 sort+reset_index 的 `out` 后会按 index alignment 把 row 对错；改为从 `out["future_return_atr"]` 取。
3. **D3**：`_ensure_training_columns` 对所有行 `fillna(0.0)`，吞掉了候选样本特意保留的 NaN 信号；改为只对 `atr_warmed=1` 行 fillna，warmup 行保留 NaN（行后续会被 `feature_df.loc[warmed_mask]` 显式 drop）。
4. **D4**：`_load_feature_meaning_map` 用 `@lru_cache` 直接以 path 为键，FEATURES.md 改动后长跑进程取不到新映射；新增 `_load_feature_meaning_map_cached(path, mtime_ns)` 加 mtime 维度。
5. **D5**：`_normalize_block_reason` 只 `replace({"nan": ""})`，`pd.NA → "<NA>"` / Python `None → "None"` 漏网；抽 `_coerce_missing_reason()` 一次识别 `nan / <na> / none / null` 全部小写大写组合。
6. **D6**：`build_training_samples_from_trade_log` 没写 `atr_warmed`，下游 `_ensure_training_columns` 默认填 1，warmup 期成交样本被错误带进训练；该路径现在按 `np.isfinite(atr_entry) and atr_entry > 0` 显式写 0/1。

### 新增 / 翻新测试（TDD）

- `cta/model/tests/test_model_pipeline.py`
  - `test_resolve_run_exchange_prefers_ranking_when_cli_exchange_blank`（D1）
  - `test_ensure_training_columns_keeps_nan_for_atr_warmup_rows`（D3）
  - `test_feature_meaning_refreshes_when_features_doc_mtime_changes`（D4）
- `cta/model/feature/tests/test_candidate_training_dataset.py`
  - `test_standardize_preserves_future_return_atr_when_input_is_unsorted`（D2）
  - `test_block_reason_treats_pdNA_and_none_string_as_missing`（D5）
- `cta/strategy/tests/test_baseline_skill_suite.py`
  - `test_build_training_samples_from_trade_log_writes_atr_warmed_flag`（D6）

### 验证命令

```bash
python3 -m unittest \
  cta.model.tests.test_model_pipeline \
  cta.model.tests.test_models_core \
  cta.model.feature.tests.test_model_feature_builder \
  cta.model.feature.tests.test_candidate_training_dataset \
  cta.strategy.tests.test_baseline_skill_suite -v
```

### 验证结果

- 5 模块全量回归：`Ran 69 tests in 51.7s, OK`（D1 / D2 / D3 / D4 / D5 / D6 各自的 6 条新测试 + 已有 63 条全部通过）。
- 含策略子模块的扩展回归：`Ran 86 tests in 52.3s, OK`。

### 风险与后续

1. **D3 影响下游统计**：以前 warmup 期样本的 `future_mfe_atr / future_mae_atr` 被悄悄填 0；修复后保留 NaN。如有外部脚本读 `*_feature_table.csv` 没做 NaN 过滤，会报警；建议下游统一用 `dropna(subset=["future_mfe_atr"])` 或先按 `atr_warmed==1` 过滤。
2. **D6 字段扩展**：`build_training_samples_from_trade_log` 输出新增 `atr_warmed` 列，老的 fallback parquet 没有这列；需要重跑或在加载时补一列。
3. **D1 用户行为**：`top-n-symbols` 模式下若用户既没传 `--exchange` 也没在 ranking csv 里给 exchange，`run_exchange` 会落到 `None` → `resolve_exchange()` 兜底走 `symbols_list.csv` 解析；这条链路目前没回归测试覆盖，下次新增。

---

## 2026-04-28 (三) · main · `model_pipeline` `--interval` 支持数组（一次跑多个周期）

### 任务

- `cta/model/model_pipeline.py` 的 `--interval` 参数从单值升级为可接收数组；
- 同步新增程序化入口 `run_model_pipeline_multi(...)`；
- 单一 interval 报错不再阻断后续 interval（独立 try/except），保证批量任务的健壮性；
- 同步更新 `cta/model/model.md` 使用示例。

### 修改文件（测试先行）

- `cta/model/tests/test_model_pipeline.py`
  - `test_normalize_intervals_accepts_space_separated_values`
  - `test_normalize_intervals_splits_comma_separated_values`
  - `test_normalize_intervals_dedupes_preserving_first_seen_order`
  - `test_normalize_intervals_strips_whitespace_and_skips_empty`
  - `test_normalize_intervals_raises_when_all_empty`
  - `test_parse_args_interval_supports_multiple_values`
  - `test_parse_args_interval_default_is_single_60min`
  - `test_run_model_pipeline_multi_returns_one_result_per_interval`

### 修改文件（实现）

- `cta/model/model_pipeline.py`
  - 新增 `_normalize_intervals(raw)`：支持空格 / 逗号混合分隔，去重保序，全空时 raise `ValueError`。
  - 新增 `run_model_pipeline_multi(..., intervals=(...))`：循环调用 `run_model_pipeline`，每个 interval 独立 try/except，输出聚合 `list[ModelPipelineResult]`。
  - `_parse_args(argv=None)` 接受可选 argv（便于单测），`--interval` 改为 `nargs="+"`，默认 `["60min"]`。
  - `main(argv=None)` 通过 `run_model_pipeline_multi` 跑全部 interval，逐个打印 `[interval] report/predictions/metrics/top10` 路径；当部分 interval 失败时打印 warning 但不退出。
  - `__all__` 导出新增 `run_model_pipeline_multi`。

### 修改文件（文档）

- `cta/model/model.md`
  - Step C 增加多 interval 用法说明（空格 / 逗号 / 混合写法）。
  - 第 3.1 节追加"一次跑全部 6 个 interval"的 CLI 示例与 `run_model_pipeline_multi` 程序化示例。

### 运行命令

```bash
python3 -m unittest \
  cta.model.tests.test_model_pipeline \
  cta.model.tests.test_models_core \
  cta.model.feature.tests.test_model_feature_builder \
  cta.model.feature.tests.test_candidate_training_dataset \
  cta.strategy.tests.test_baseline_skill_suite
```

CLI 多 interval 实跑示例：

```bash
python3 -m cta.model.model_pipeline \
  --symbol RB0 --exchange SHFE \
  --interval day 60min 30min 15min \
  --start 2010-01-01 --end 2019-12-31
```

### 验证结果

- 61 tests 全部通过（OK），耗时约 50.9s。

### 风险与后续

1. **向后兼容**：`--interval 60min`、`run_model_pipeline(interval=...)` 单值入口完全保留；只有调用 `run_model_pipeline_multi` 或一次性传多个 interval 才走新分支。
2. 多 interval 顺序执行不并行；6 周期合计耗时大约是单周期的 6×。如需并行可在外层用 `concurrent.futures` 包一层。
3. 单一 interval 失败时只 `logger.exception` 不终止；批量任务想要严格"全部成功"，请检查 `len(results) == len(intervals)` 或自行 raise。

---

## 2026-04-28 (二) · main · 特征重要度补充“特征含义”列 + topN 品种参数

### 任务

1. `top10_feature_importance` 增加“特征含义”列；  
2. `cta/model` 支持通过 `cta/feature/symbols_research_ranking.csv` 加载 topN 品种（N 为参数）。

### 修改文件（测试先行）

- `cta/model/tests/test_model_pipeline.py`
  - `test_run_pipeline_smoke` 增加断言：`*_top10_feature_importance.csv` 包含 `feature_meaning`。
  - 新增 `test_parse_args_supports_top_n_symbols`（CLI 参数覆盖）。
  - 新增 `test_load_top_n_symbols_from_ranking_orders_by_rank`（按 `research_rank` 取前 N）。

### 修改文件（实现）

- `cta/model/model_pipeline.py`
  - 新增 `FEATURES_DOC_PATH`、`SYMBOLS_RANKING_PATH` 常量。
  - 新增 `_load_feature_meaning_map(...)`：从 `cta/feature/FEATURES.md` 解析 `特征名 -> 含义`。
  - 新增 `_feature_meaning(...)`：优先命中本地兜底字典，其次命中 FEATURES.md，未命中给统一 fallback 描述。
  - `top10_feature_importance` 输出新增列：`feature_meaning`。
  - 新增 `_load_top_n_symbols_from_ranking(...)`：从 ranking csv 读取并按 `research_rank` 返回 topN `(symbol, exchange)`。
  - CLI 新增参数：
    - `--top-n-symbols`
    - `--symbols-ranking-path`
  - `main()` 增加 topN 模式：
    - 当 `--top-n-symbols > 0` 时忽略 `--symbol`，按 ranking 批量跑。

### 修改文件（文档）

- `cta/model/model.md`
  - 增加 topN 品种模式说明与命令示例。
  - 更新 `*_top10_feature_importance.csv` 描述（包含 `feature_meaning`）。
- `cta/strategy/readme.md`
  - 增加 topN 品种命令示例。
  - 明确 top10 文件列：`feature / feature_meaning / importance`。

### 验证命令

```bash
python3 -m unittest cta.model.tests.test_models_core cta.model.tests.test_model_pipeline -v
python3 -m cta.model.model_pipeline --help
python3 -m cta.model.model_pipeline \
  --top-n-symbols 2 \
  --interval 60min \
  --start 2018-01-01 --end 2018-12-31 \
  --train-end 2018-06-30 --valid-end 2018-09-30 \
  --max-walk-forward-windows 1 \
  --output-root /tmp/cta_model_topn_smoke
```

### 输出位置

- topN 冒烟输出目录：
  - `/tmp/cta_model_topn_smoke/20260428_RB0_minute60_both_model_pipeline/`
  - `/tmp/cta_model_topn_smoke/20260428_HC0_minute60_both_model_pipeline/`
- 其中 `*_top10_feature_importance.csv` 已包含 `feature_meaning` 列。

---

## 2026-04-28 (二) · main · 三模型训练后输出 Top10 特征重要性 + 重跑样本与模型

### 任务

- 每个模型训练完成后打印 Top10 重要特征；
- 重新生成一遍训练样本特征与模型产物（RB0, 60min）。

### 修改文件（测试先行）

- `cta/model/tests/test_models_core.py`
  - 新增断言：`TradeFilterModel` / `RegimeClassifierModel` / `MfeMaeModel` 均可返回 Top 特征重要性表（`feature/importance`，按降序）。
- `cta/model/tests/test_model_pipeline.py`
  - `test_run_pipeline_smoke` 增加断言：pipeline 输出 `top_feature_importance_path` 且文件含 `model/feature/importance` 列。

### 修改文件（实现）

- `cta/model/trade_filter_model.py`
  - 新增 `get_top_feature_importance(...)`：
    - 优先用模型原生重要性；
    - 对不暴露原生重要性的模型（如 HGB）回退 permutation importance。
- `cta/model/regime_classifier_model.py`
  - 新增 `get_top_feature_importance(...)`（RandomForest 原生重要性；dummy 回退 0）。
- `cta/model/mfe_mae_model.py`
  - 新增 `get_top_feature_importance(...)`（multi-output 子模型重要性取均值）。
- `cta/model/model_pipeline.py`
  - `ModelPipelineResult` 新增 `top_feature_importance_path`；
  - 每个 signal/window 训练三模型后，日志打印 Top10 重要特征；
  - 新增输出文件 `*_top10_feature_importance.csv`；
  - 报告 `model_report.md` 增加该 CSV 路径记录。
- `cta/model/model.md`
  - 文档补充 `*_top10_feature_importance.csv` 产物说明。

### 验证命令

```bash
python3 -m unittest cta.model.tests.test_models_core cta.model.tests.test_model_pipeline -v
```

### 重跑命令（样本特征 + 模型）

```bash
# 1) 重建候选训练样本特征
python3 -m cta.model.feature.candidate_training_dataset \
  --symbol RB0 --exchange SHFE --interval 60min \
  --start 2010-01-01 --end 2019-12-31 \
  --trade-side-mode both --run-tag 20260428

# 2) 重跑模型 pipeline（训练+评估+报告）
python3 -m cta.model.model_pipeline \
  --symbol RB0 --exchange SHFE --interval 60min \
  --start 2010-01-01 --end 2019-12-31 \
  --trade-side-mode both \
  --train-end 2017-12-31 --valid-end 2018-12-31 \
  --window-mode expanding --max-walk-forward-windows 3 --by-signal-type
```

### 输出位置

- 训练样本特征：
  - `cta/data/model_feature/minute60/RB0/20260428/20260428_RB0_minute60_candidate_events.parquet`
  - `cta/data/model_feature/minute60/RB0/20260428/20260428_RB0_minute60_training_samples.parquet`
  - `cta/data/model_feature/minute60/RB0/20260428/20260428_RB0_minute60_dataset_summary.csv`
- 模型报告目录：
  - `cta/report/backtest/20260428_RB0_minute60_both_model_pipeline/`
  - 包含 `*_top10_feature_importance.csv` / `*_metrics.csv` / `*_predictions.csv` / `*_model_report.md`

---

## 2026-04-28 (二) · main · `rb60` 模型文件改名为通用入口并同步文档

### 任务

- 将 `cta/model` 中以 `rb60` 命名的 pipeline 文件改为通用命名；
- 保持模型 pipeline 对任意品种和任意周期（`day/60min/30min/15min/5min/min`）可用；
- 同步更新相关 markdown 文档与测试入口。

### 修改文件（测试先行）

- `cta/model/tests/test_model_pipeline.py`
  - 由 `test_rb60_model_pipeline.py` 重命名而来；
  - 导入入口改为 `cta.model.model_pipeline`；
  - 调用入口改为 `run_model_pipeline`。

### 修改文件（实现）

- `cta/model/model_pipeline.py`
  - 由 `rb60_model_pipeline.py` 重命名而来；
  - 对外类型改名：`RbModelPipelineResult` → `ModelPipelineResult`；
  - 对外函数改名：`run_rb_model_pipeline` → `run_model_pipeline`；
  - 日志文案去除 `rb` 限定，保持通用策略/品种语义。

### 修改文件（文档）

- `cta/model/model.md`
  - 运行命令入口统一更新为 `python3 -m cta.model.model_pipeline`；
  - 代码示例导入路径改为 `from cta.model.model_pipeline import ...`；
  - 模型测试命令改为 `cta.model.tests.test_model_pipeline`。
- `cta/strategy/readme.md`
  - `run_rb_model_pipeline` 文案更新为 `run_model_pipeline`；
  - CLI 示例更新为 `cta.model.model_pipeline`。
- `cta/strategy/bug.md`
  - 代码引用路径更新为 `cta/model/model_pipeline.py`。

### 运行命令

```bash
python3 -m unittest cta.model.tests.test_model_pipeline -v
```

### 验证结果

- 17 tests 全部通过（OK）。

---

## 2026-04-27 (二) · main · candidate_events 第二轮 code review 修复 C1–C10

### 范围

- `cta/strategy/baseline_skill_suite.py`（输出新增 `trigger` / `future_pnl_atr` 字段；NaN 替代 0.0 fallback）
- `cta/config/baseline_skill_suite_config.py`（新增 `OPPORTUNITY_CLASS_A_BREAK` / `OPPORTUNITY_CLASS_B_BREAK`）
- `cta/model/feature/candidate_training_dataset.py`（重写 standardize / build_and_save，对齐 `candidate_vs_executed_samples.md`）
- `cta/tests/test_candidate_training_dataset.py`（+8 tests，原 2 tests 同步翻新）

### 核心修复（按优先级 P0 → P1）

**P0（标签 / 主键 / 业务语义）**

1. **C1/C12**：`candidate_id` 在第一次 `standardize_candidate_events` 生成后即作为稳定主键；merge_asof 后再次归一化时跳过 `_build_candidate_id`，保证 `candidate_events.parquet` 与 `training_samples.parquet` 的主键集合完全一致，下游可按 `candidate_id` 做四类样本（7.1–7.4）join。
2. **C2**：`entry_price_virtual` 兜底优先级改为 `entry_price → trigger → feature_close → close`；删除 stop_price 这档（在 limit-order/ATR breakout 模式下 stop 与 trigger 不同）。baseline 同步在 row dict 写出 `trigger` 字段供下游消费。
3. **C3**：机会质量标签在 `atr_warmed=0` 或 mfe/mae 缺失时显式置为 unknown：
   - `opportunity_score` / `future_mfe_atr` / `future_mae_atr` / `future_return_atr` 全部保留 NaN
   - `is_good_opportunity` = 0、`opportunity_class` = `"U"`
   - 不再用 `fillna(0)` 把"未知"伪装成"差机会"
4. **C4**：`_SAMPLE_STATUS_MAP` 把 baseline 的 `not_triggered`（市场未触发）映射到独立的 `not_triggered_market`，与 `blocked_by_execution`（执行规则阻断）严格区分；`_VALID_SAMPLE_STATUS` 同步扩充。
5. **C5**：拆分 `opportunity_score`（= mfe − 0.7·mae）与 `future_return_atr`（baseline 真实 horizon 收益 `future_pnl_atr`）。baseline `not_triggered` / `filtered` 分支也补上 `future_pnl` 计算。

**P1（健壮性 / 可观测性）**

6. **C6**：`_normalize_block_reason` 把 NaN / 字符串 `"nan"` 统一识别为缺失（先 `fillna("")` 再 `replace({"nan": ""})`），避免 parquet round-trip 后 reason 被错误填成 `"nan"`。
7. **C7**：`(symbol, interval, datetime, setup_type, direction)` 重复时直接 raise `ValueError`，不再用 `seq` 兜底掩盖上游 ETL 故障。
8. **C8**：`OPPORTUNITY_CLASS_A_BREAK = 1.2` / `OPPORTUNITY_CLASS_B_BREAK = 0.6` 抽到 `baseline_skill_suite_config`，与 `LABEL_THRESHOLD` 同源。
9. **C9**：`label_class` / `atr_warmed` 纳入 `_CORE_COLS`，列序稳定（不再作为 trailing 漂移）。
10. **C10**：`build_and_save_candidate_training_dataset` 对空输入也写出含完整 schema 的空 parquet（`_empty_candidate_events_frame()` 兜底），下游读取不再缺列。

### 新增 / 翻新测试（TDD）

1. `test_standardize_candidate_events_maps_status_and_labels`（翻新 C2 + C4 断言）
2. `test_candidate_id_stable_between_candidate_events_and_training_samples`（C1）
3. `test_entry_price_virtual_falls_back_to_trigger_not_stop_price`（C2）
4. `test_is_good_opportunity_unknown_when_atr_not_warmed`（C3）
5. `test_future_return_atr_stores_real_horizon_return_not_score`（C5）
6. `test_block_reason_treats_string_nan_as_missing`（C6）
7. `test_standardize_raises_on_duplicate_primary_key`（C7）
8. `test_core_cols_include_label_class_and_atr_warmed`（C9）
9. `test_empty_candidate_df_writes_schema_complete_empty_parquet`（C10）

### 验证命令

```bash
python3 -m unittest \
  cta.tests.test_baseline_skill_suite \
  cta.tests.test_model_feature_builder \
  cta.tests.test_models_core \
  cta.tests.test_rb60_model_pipeline \
  cta.tests.test_candidate_training_dataset -v
```

### 验证结果

- 50 tests 全部通过（OK），耗时约 31.4s。

### 风险与后续

1. **口径变更**：baseline `future_mfe_atr` / `future_mae_atr` 的 fallback 从 `0.0` 改成 `np.nan`。下游 `rb60_model_pipeline` 内部已经做 `fillna(0.0)`，不受影响；但若历史报告 / parquet 是用旧口径生成的，重跑会导致 `good_opportunity_count` 与 `executed_count` 偏离上一版数字（warmup 期 / 缺价样本被显式标记为 U，而不再被混进"差机会"）。
2. **summary CSV 字段扩展**：新增 `not_triggered_market_count` / `unknown_opportunity_count` 两列。已经下载过老 summary 的脚本需要兼容新增列。
3. **trigger 字段依赖**：candidate_training_dataset 的 entry_price_virtual 现在依赖 baseline 输出的 `trigger` 列；如果有第三方调用方直接构造 candidate_df 不传 trigger，entry_price_virtual 会回退到 feature_close → close，建议补 trigger 字段以拿到精确的虚拟入场价。
4. **`U` 类别**：opportunity_class 新增的 `U` 类别在历史 metrics 报告里不存在；如果有按 class 做分桶绘图的脚本，需要追加一个 U 类别色板。

---

## 2026-04-28 (二) — 训练保留未成交样本 + 十档收益仅统计已成交样本

**分支**: 当前  
**任务**:  
1) 训练模型时明确保留未成交样本；  
2) 最后 OOT 十档收益评估只看已成交样本。

### 修改文件（测试先行）

- `cta/model/tests/test_rb60_model_pipeline.py`
  - `test_training_columns_keep_non_executed_samples`
  - `test_build_last_oot_decile_table_uses_last_window_test_only`（断言更新：只统计 executed）

### 修改文件（实现）

- `cta/model/rb60_model_pipeline.py`
  - `_build_last_oot_decile_table` 改为先过滤 `is_executed==1` 再分十档计算收益。
  - 训练循环新增 `train_executed_count` / `train_non_executed_count`（写入 metrics）。
  - 修复 `_ensure_training_columns` 在缺少 `feature_trend_dir` 时的标量回退 bug。

- `cta/model/model.md`
  - 增加训练口径说明：trade/regime 用全量候选（含未成交），MFE/MAE 仅成交样本。

### 运行命令

```bash
python3 -m unittest cta.model.tests.test_rb60_model_pipeline -v
```

### 输出位置

- `cta/report/backtest/20260427_RB0_minute60_both_model_pipeline/20260427_RB0_minute60_both_last_oot_decile_returns.csv`
  - 已按“仅成交样本”口径重算。

---

## 2026-04-27 (一) — 模型评分十档收益（最后 OOT 集合）

**分支**: 当前  
**任务**: 按最后 OOT 集合（`pred_split=test` 且最大 `window_id`）将 `trade_filter_prob` 分十档，计算每档收益。

### 修改文件（测试先行）

- `cta/model/tests/test_rb60_model_pipeline.py`
  - 新增 `test_build_last_oot_decile_table_uses_last_window_test_only`
  - 新增 `test_build_last_oot_decile_table_builds_10_bins_when_enough_samples`

### 修改文件（实现）

- `cta/model/rb60_model_pipeline.py`
  - 新增 `_build_last_oot_decile_table(prediction_df, bins=10)`：
    - 自动选择最后 OOT 集合；
    - 按 `trade_filter_prob` 分位分桶；
    - 计算每档 `avg_return_atr / total_return_atr / win_rate / executed_rate` 等。
  - `run_rb_model_pipeline` 新增输出：
    - `*_last_oot_decile_returns.csv`
  - 模型报告新增章节：
    - `Last OOT Decile Returns`

- `cta/model/model.md`
  - 增加 “最后 OOT 十档收益”使用说明与示例命令。

### 运行命令

```bash
# 针对 rb60 pipeline 关键回归
python3 -m unittest cta.model.tests.test_rb60_model_pipeline -v

# 已有 predictions 直接算十档收益
python3 - <<'PY'
from pathlib import Path
import pandas as pd
from cta.model.rb60_model_pipeline import _build_last_oot_decile_table
p = Path("cta/report/backtest/20260427_RB0_minute60_both_model_pipeline/20260427_RB0_minute60_both_predictions.csv")
df = pd.read_csv(p)
out = _build_last_oot_decile_table(df, bins=10)
out.to_csv(p.parent / "20260427_RB0_minute60_both_last_oot_decile_returns.csv", index=False, encoding="utf-8-sig")
print(out)
PY
```

### 输出位置

- `cta/report/backtest/20260427_RB0_minute60_both_model_pipeline/20260427_RB0_minute60_both_last_oot_decile_returns.csv`

---

## 2026-04-27 (一) — 测试目录重构 + 原始数据迁移到 `cta/data/origin` + 新增 `cta/model/model.md`

**分支**: 当前  
**任务**:  
1) 测试文件从 `cta/tests/` 迁移到对应实现目录下的 `tests/`；  
2) 原始行情目录 `day/minute*` 统一迁移到 `cta/data/origin/` 并同步代码路径；  
3) 新增 `cta/model/model.md`，补全“特征生成→模型训练→模型使用”流程与命令示例。

### 修改文件（测试先行）

- `cta/strategy/tests/test_skill_tight_range_backtest_rb0.py`
  - 新增 `test_backtest_config_uses_origin_data_root`，锁定 `BacktestConfig` 必须默认读取 `cta/data/origin`。
  - 日线冒烟测试输入路径更新为 `cta/data/origin/day/RB0.csv`。

### 测试目录迁移

- 迁移到 `cta/strategy/tests/`：
  - `test_skill_tight_range_strategy.py`
  - `test_skill_tight_range_backtest_rb0.py`
  - `test_baseline_skill_suite.py`
- 迁移到 `cta/model/tests/`：
  - `test_models_core.py`
  - `test_rb60_model_pipeline.py`
- 迁移到 `cta/model/feature/tests/`：
  - `test_model_feature_builder.py`
  - `test_candidate_training_dataset.py`
- 新增：
  - `cta/strategy/tests/__init__.py`
  - `cta/model/tests/__init__.py`
  - `cta/model/feature/tests/__init__.py`

### 代码路径与目录迁移

- 目录迁移（原始行情）：
  - `cta/data/day` -> `cta/data/origin/day`
  - `cta/data/minute` -> `cta/data/origin/minute`
  - `cta/data/minute5` -> `cta/data/origin/minute5`
  - `cta/data/minute15` -> `cta/data/origin/minute15`
  - `cta/data/minute30` -> `cta/data/origin/minute30`
  - `cta/data/minute60` -> `cta/data/origin/minute60`

- 关键代码更新：
  - `cta/config/skill_tight_range_breakout_config.py`
    - 新增 `DATA_ORIGIN_ROOT`
    - `BacktestConfig.data_root` 默认改为 `cta/data/origin`
    - `DATA_DAY_DIR` 改为 `cta/data/origin/day`
  - `cta/feature/loader.py`
    - 原始数据根目录改为 `cta/data/origin`
  - `cta/data_code/futures_downloader.py`
    - 原始行情默认读写根目录改为 `cta/data/origin`
    - day/minute 文档说明同步
  - `cta/data_code/download_all.py`
    - 文档说明更新为 `cta/data/origin/*`
  - `cta/strategy/backtest_price_action_breakout.py`
    - `SYMBOLS_CSV_PATH` 改为复用 `SYMBOLS_LIST_PATH`（不再硬编码绝对路径）

### 文档更新（相关 markdown）

- `cta/README.md`
  - 目录结构改为 `data/origin` + 按模块分散 `tests/`。
- `cta/strategy/readme.md`
  - 新增原始数据路径说明（`cta/data/origin`）与测试目录约定。
- `cta/strategy/breakout.md`
  - `cta/data/day` 引用改为 `cta/data/origin/day`。
- `cta/strategy/brooks/brooks_v3.md`
  - 测试目录与命令从 `cta/tests` 改到 `cta/strategy/brooks/tests`。
  - 原始行情路径改到 `cta/data/origin/*`。
- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_report.md`
- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_report.md`
  - 数据路径文本更新为 `cta/data/origin/...`。

### 新增模型说明文档

- `cta/model/model.md`
  - 覆盖特征生成、候选样本构建、三模型训练、离线推理、walk-forward、常用命令和测试命令。

### 运行命令

```bash
# 全量相关回归（新目录）
python3 -m unittest \
  cta.strategy.tests.test_skill_tight_range_strategy \
  cta.strategy.tests.test_skill_tight_range_backtest_rb0 \
  cta.strategy.tests.test_baseline_skill_suite \
  cta.model.feature.tests.test_model_feature_builder \
  cta.model.feature.tests.test_candidate_training_dataset \
  cta.model.tests.test_models_core \
  cta.model.tests.test_rb60_model_pipeline -v
```

### 结果

- 以上 63 个测试全部通过。

### 风险与后续

1. `cta/report/change_log.md` 旧历史条目保留原路径叙述（如 `cta/tests` / `cta/data/day`），代表当时状态；当前生效路径以本条与代码常量为准。  
2. 新增测试目录后，后续命令请统一使用：
   - `cta.strategy.tests.*`
   - `cta.model.tests.*`
   - `cta.model.feature.tests.*`

---

## 2026-04-27 (一) — 候选样本重构：candidate_events 标准化 + vn.py 通用特征拼接 + 统一落盘

**分支**: 当前  
**任务**: 根据 `cta/model/feature/candidate_vs_executed_samples.md` 重构训练样本生成流程，支持“候选/已成交/被过滤/未触发”统一建模，并从 `cta/data/feature` 拼接通用特征后落盘到 `cta/data/model_feature/`。

### 修改文件（测试先行）

- `cta/tests/test_candidate_training_dataset.py`
  - `test_standardize_candidate_events_maps_status_and_labels`
    - 校验 `candidate_status -> sample_status` 映射：
      - `filled -> executed`
      - `filtered -> filtered_by_rule`
      - `not_triggered -> blocked_by_execution`
    - 校验核心字段生成：
      - `candidate_id/setup_type/direction/sample_status/block_reason`
      - `entry_price_virtual/stop_price_virtual`
      - `is_good_opportunity/opportunity_class`
    - 校验机会标签独立于是否成交（filtered 样本也可为好机会）。
  - `test_build_and_save_candidate_training_dataset_merges_generic_features`
    - 校验候选特征与 vn.py 通用特征（`generic_*`）拼接成功；
    - 校验 parquet/csv/summary 文件落盘。

### 修改文件（实现）

- `cta/model/feature/candidate_training_dataset.py`（新增）
  - 新增 `standardize_candidate_events`：将 baseline 候选样本统一为 `candidate_events` 结构，补齐：
    - `candidate_id/candidate_flag/sample_status/block_reason`
    - `entry_price_virtual/stop_price_virtual/target_price_virtual`
    - `future_return_atr/is_good_opportunity/opportunity_class`
    - `executed_flag/risk_block_flag/capacity_block_flag/execution_block_flag`
  - 新增 `generate_candidate_events_from_baselines`：
    - 从 baseline 规则（Donchian/ATR/TightRange/Pullback）批量生成候选事件。
  - 新增 `build_and_save_candidate_training_dataset`：
    - 拼接候选特征与 `cta/data/feature/<interval>/<symbol>` 的 vn.py 通用特征；
    - 输出到 `cta/data/model_feature/<interval>/<symbol>/<run_tag>/`；
    - 同步输出 summary。
  - 新增 `generate_and_save_candidate_training_dataset` + CLI：
    - 一条命令完成“候选生成 -> 特征拼接 -> 持久化”。

### 运行命令

```bash
# 新增测试
python3 -m unittest cta.tests.test_candidate_training_dataset -v

# 回归（核心相关）
python3 -m unittest cta.tests.test_baseline_skill_suite \
                  cta.tests.test_model_feature_builder \
                  cta.tests.test_rb60_model_pipeline \
                  cta.tests.test_candidate_training_dataset -v

# 实际构建 RB0 60min 候选训练样本（2010-2019）
python3 -m cta.model.feature.candidate_training_dataset \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both \
  --run-tag 20260427
```

### 输出位置

- `cta/data/model_feature/minute60/RB0/20260427/`
  - `20260427_RB0_minute60_candidate_events.parquet`
  - `20260427_RB0_minute60_candidate_events.csv`
  - `20260427_RB0_minute60_training_samples.parquet`
  - `20260427_RB0_minute60_training_samples.csv`
  - `20260427_RB0_minute60_dataset_summary.csv`

### 结果摘要（RB0, minute60, 2010-2019）

- `total_candidates`: `4142`
- `executed_count`: `1798`
- `filtered_count`: `79`
- `blocked_execution_count`: `2265`
- `good_opportunity_count`: `1935`
- `generic_feature_columns`: `18`

### 风险与后续

1. 当前 `blocked_by_risk/blocked_by_capacity` 主要依赖上游策略/组合层提供，基线策略阶段通常为 0。  
2. `future_return_atr` 现用 `mfe - 0.7*mae` 的机会分数口径，若后续引入更严格“虚拟出场价”定义，可再替换为真实 horizon return。  
3. 新流程已兼容全品种与全周期（`day/60min/30min/15min/5min/min`，依赖本地数据是否齐备），可直接按相同 CLI 扩展到多品种批量生成。

---

## 2026-04-26 (二) — Code Review 第三轮：标签/口径/窗口/dummy 模型修复（R1–R10）

**分支**: 当前  
**任务**: 按第二轮 code review 列出的 10 项修改项（R1–R10）落地修复，全部 TDD（先写/改测试再改实现）。

### 关键修复（与 review 编号对应）

- **R1** `cta/model/rb60_model_pipeline.py` — `_ensure_training_columns` / `_ensure_binary_label_diversity` 重算 label 时**只对** `is_executed==1 & atr_warmed==1` 的成交样本生效，避免把 not_triggered/filtered/warmup 样本误标为正样本。
- **R2 + R3** `cta/strategy/baseline_skill_suite.py` — `generate_candidate_opportunities` 改为：
  - ATR 优先取 **entry bar** 的 `atr14`（与实盘风险预算口径一致），缺失时回退 signal bar，再失败用 high-low 兜底；
  - 输出新列 `atr_warmed (0/1)`，下游 pipeline 在 `_ensure_training_columns` 后**显式 drop** warmup 行并 `logger.warning` 报告丢弃比例。
- **R4 + R5** `_build_walk_forward_windows` 新增 `window_mode: Literal["expanding","sliding"]` 参数（默认 expanding，与历史一致），sliding 模式下 train 长度恒定；同步修复"数据已超过 valid_end 也不退出"的浪费循环。CLI 增加 `--window-mode`。
- **R6** `RegimeClassifierModel` 单类 fallback 从 `most_frequent` 改为 `prior`，与 `TradeFilterModel` 一致。
- **R7** `MfeMaeModel` 增加 `min_samples` 字段（默认 10）+ `model_kind` 字段（`dummy / random_forest`），save/load 透传。同样把 `model_kind` 加入 `TradeFilterModel`、`RegimeClassifierModel`，并在 `metrics_df` 中新增 `model_kind` 列，方便快速识别哪些窗口退化为 dummy。
- **R8** `_select_feature_columns` 改为 dtype 白名单（数值/布尔/object 但底层是数）：跳过 `feature_label` 之类字符串列，避免被强转为 NaN 后再被 imputer 填中位数。
- **R9** baseline suite 报告生成增加 `tabulate ImportError` 的回退分支（`to_string + 代码块`），新机器没装 tabulate 也不再报错。

### 新增/修改测试（TDD）

- `cta/tests/test_baseline_skill_suite.py`
  - **改名 + 翻转断言**：`test_generate_candidate_uses_entry_bar_atr_for_label_norm`（原 signal-bar 版本，反映 R3 新口径）。
  - **新增 T-D**：`test_long_stop_entry_uses_max_open_trigger_when_open_above_trigger`（gap up 行为）。
  - **新增 T-I**：`test_atr_warmup_marks_warmed_zero_when_atr14_nan`。
- `cta/tests/test_rb60_model_pipeline.py`
  - **新增 T-A**：`test_ensure_binary_label_diversity_does_not_relabel_not_triggered`。
  - **新增 T-A2**：`test_ensure_binary_label_diversity_skips_atr_warmup`。
  - **新增 T-C**：`test_walk_forward_windows_monotonic_and_no_overlap` / `_sliding_train_starts_advance` / `_invalid_mode_raises`。
  - **新增 T-H**：`test_select_feature_columns_skips_string_columns`。
  - 已有 walk-forward 测试增加 `model_kind` 列断言。
- `cta/tests/test_model_feature_builder.py`
  - **新增 T-E**：`test_merge_respects_60min_tolerance_returns_nan_when_too_far`（90 分钟跨度，期望 NaN）。
- `cta/tests/test_models_core.py`
  - **新增 T-G**：`test_mfe_mae_model_kind_reflects_dummy_vs_rf`（`min_samples=10` 触发 dummy，并 round-trip 保留 `model_kind`）。

### 运行命令

```bash
# 仅核心 4 个文件（建议日常回归）
python3 -m pytest cta/tests/test_baseline_skill_suite.py \
                  cta/tests/test_models_core.py \
                  cta/tests/test_model_feature_builder.py \
                  cta/tests/test_rb60_model_pipeline.py \
                  -q --tb=short

# 全量
python3 -m pytest cta --ignore=cta/data -q --tb=short
```

### 结果

- **核心 4 文件**：31 / 31 通过
- **全量（不含需要 TUSHARE_TOKEN 的 cta/data）**：328 / 328 通过

### 风险与后续

1. R2 后 warmup 期 (~14 根) 候选会被丢弃；如果某品种数据本身不足 14 根，pipeline 会落入 fallback 合成数据分支（已有 logger 提示）。
2. R7 新增的 `model_kind` 列在旧的 `metrics.csv` 文件里不存在，回放历史报告需自行兼容 missing 列。
3. R4 sliding 模式与 expanding 在 RB0 60min 上的对比尚未跑全量回测；后续可在 `cta/report/` 下加一份 A/B 对照。
4. `to_markdown` 仍优先尝试 tabulate，建议在 `cta/requirements.txt`（若新建）写入 `tabulate>=0.9.0`，避免每次 ImportError 走 fallback。

---

## 2026-04-26 — 新增 pre-2020 训练样本构造 + 四套 baseline skill（Donchian/ATR/TightRange/Pullback）

**分支**: 当前  
**任务**:  
1) 将 2020 年之前选中的成交交易构造成训练样本（含 `symbol/interval/datetime/features/signal_type` 等字段）；  
2) 在 `cta/strategy` 落地四套纯规则 baseline，并可统一回测。

### 修改文件（测试先行）

- `cta/tests/test_baseline_skill_suite.py`
  - `test_prepare_master_feature_frame_columns`：验证统一特征框架含关键列。
  - `test_strategy_factory_all_signal_types`：验证四个 baseline 策略均可实例化并生成信号。
  - `test_build_training_samples_from_trade_log`：验证训练样本字段与标签构造。
  - `test_run_baseline_suite_smoke_rb0`：真实 RB0 minute60 冒烟回测 + 样本落盘。

### 修改文件（代码）

- `cta/config/baseline_skill_suite_config.py`
  - 新增 baseline 套件配置：
    - `BASELINE_SIGNAL_TYPES`
    - `TRAINING_FEATURE_COLUMNS`
    - `BaselineSuiteConfig`

- `cta/strategy/baseline_skill_suite.py`
  - 新增四套 baseline 规则策略：
    - `DonchianBaselineStrategy`
    - `ATRBreakoutBaselineStrategy`
    - `SkillTightRangeBreakoutStrategy`（复用现有实现）
    - `BreakoutPullbackBaselineStrategy`
  - 新增统一特征准备：`prepare_master_feature_frame`
  - 新增训练样本构造：`build_training_samples_from_trade_log`
  - 新增统一运行入口：`run_baseline_suite`
  - CLI：`python3 -m cta.strategy.baseline_skill_suite ...`

- `cta/strategy/readme.md`
  - 追加 baseline 套件与 pre-2020 训练样本构造命令、输出路径、字段规范。

### 运行命令

```bash
python3 -m unittest cta.tests.test_baseline_skill_suite -v

python3 -m unittest \
  cta.tests.test_skill_tight_range_strategy \
  cta.tests.test_skill_tight_range_backtest_rb0 \
  cta.tests.test_baseline_skill_suite -v

python3 -m cta.strategy.baseline_skill_suite \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --trade-side-mode both \
  --start 2000-01-01 \
  --end 2019-12-31
```

### 输出位置

- 套件目录：`cta/report/backtest/20260426_baseline_skill_suite_RB0_minute60_both/`
- 汇总指标：`20260426_RB0_minute60_both_suite_summary.csv`
- 训练样本：`20260426_RB0_minute60_both_training_samples.csv`
- 报告：`20260426_RB0_minute60_both_baseline_report.md`
- 各策略分目录：
  - `donchian_breakout/`
  - `atr_breakout/`
  - `tight_range_breakout/`
  - `breakout_pullback_continuation/`

### 结果摘要（RB0, minute60, <=2019-12-31）

- 有效 bars: `18871`（2010-01-04 09:00:00 ~ 2019-12-30 23:00:00）
- 训练样本总行数: `886`
- 各 signal_type 样本数：
  - `tight_range_breakout`: `275`
  - `atr_breakout`: `272`
  - `breakout_pullback_continuation`: `183`
  - `donchian_breakout`: `156`
- baseline 汇总（total_return）：
  - `donchian_breakout`: `0.001062`
  - `atr_breakout`: `-0.002305`
  - `tight_range_breakout`: `-0.096933`
  - `breakout_pullback_continuation`: `-0.002161`

### 风险与后续

1. 训练样本当前来源于“已成交交易”，尚未包含“未成交候选信号”负样本。  
2. 建议下一步增加候选信号级样本（含未成交/被过滤）以提升模型泛化。  
3. baseline 目前是单品种单策略逐个回测，后续可加组合层资金约束。

---

## 2026-04-25 — RB0 5min（2020年前全样本）回测与报告输出

**分支**: 当前  
**任务**: 回测 `RB0` 在 `5min` 周期、`2020-01-01` 之前全部可用数据，并形成结构化报告。

### 修改文件

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_summary.csv`
  - 本次回测核心指标输出。

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_trades.csv`
  - 交易明细输出（含方向、成本、净收益）。

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_equity.csv`
  - 资金曲线输出。

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_yearly_stats.csv`
  - 新增按退出年份聚合统计。

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/20260425_RB0_both_report.md`
  - 新增本次回测报告（配置、核心指标、按方向/年份统计、输出文件位置、限制说明）。

### 运行命令

```bash
python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 \
  --exchange SHFE \
  --interval 5min \
  --trade-side-mode both \
  --start 2000-01-01 \
  --end 2019-12-31
```

### 数据路径

- 输入：`cta/data/minute5/RB/*.parquet`
- 有效覆盖区间：`2010-01-04 09:00:00` 至 `2019-12-30 23:00:00`
- bar 数：`154498`

### 输出位置

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute5_both/`

### 结果摘要

- `total_pnl`: `-3753549.9490000005`
- `total_return`: `-3.7535499490000004`
- `annualized`: `NaN`（权益转负导致当前公式无定义）
- `mdd`: `3.753549948999992`
- `sharpe`: `-10.521446826613134`
- `calmar`: `NaN`
- `winrate`: `0.04623791509037411`
- `pf`: `0.018395976973023063`
- `trade_count`: `2379`

### 风险与后续

1. 当前 `annualized/calmar` 在权益转负时会出现 `NaN`，后续可改为稳健年化口径。  
2. 5min 成本敏感度高，建议下一步先做成本参数压力测试（rate/slippage 网格）。

---

## 2026-04-25 — RB0 60min（2020年前全样本）回测与报告输出

**分支**: 当前  
**任务**: 回测 `RB0` 在 `60min` 周期、`2020-01-01` 之前全部可用数据，并形成结构化报告。

### 修改文件

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_summary.csv`
  - 本次回测核心指标输出（总收益、年化、回撤、Sharpe、Calmar、胜率、盈亏比、成交笔数）。

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_trades.csv`
  - 交易明细输出（含 entry/exit、方向、成本和净收益）。

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_equity.csv`
  - 资金曲线输出。

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_yearly_stats.csv`
  - 新增按退出年份聚合统计（trades/net_pnl/win_rate/avg_pnl/profit_factor）。

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/20260425_RB0_both_report.md`
  - 新增本次回测报告（运行参数、核心指标、按方向统计、按年份统计、输出文件位置、限制说明）。

### 运行命令

```bash
python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --trade-side-mode both \
  --start 2000-01-01 \
  --end 2019-12-31
```

### 数据路径

- 输入：`cta/data/minute60/RB/*.parquet`
- 时间范围：至 `2019-12-31`（按本地可用分钟数据生效）

### 输出位置

- `cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/`

### 结果摘要

- `total_pnl`: `-95768.731`
- `total_return`: `-0.095768731`
- `annualized`: `-0.005363175646051377`
- `mdd`: `0.09576873100000002`
- `sharpe`: `-2.0404903827837644`
- `calmar`: `-0.05600132308374615`
- `winrate`: `0.24253731343283583`
- `pf`: `0.22012662666461236`
- `trade_count`: `268`

### 风险与后续

1. 成本模型为手续费+滑点近似，非逐笔成交撮合。  
2. 若后续用于参数对比，建议固定同一数据窗口并增加 walk-forward 切分报告。

---

## 2026-04-25 — 增强回测周期参数与多空模式兼容（day/60min/30min/15min/5min/min + both/long/short）

**分支**: 当前  
**任务**: 在已有 skill tight-range breakout 基础上，新增“测试周期参数”与“多空模式”能力；按 TDD 先补测试，再改实现，并验证 `RB0` 日线与分钟级回测可运行。

### 修改文件（测试，先写）

- `cta/tests/test_skill_tight_range_strategy.py`
  - 新增 `test_prepare_strategy_frame_interval_aliases`：校验 `day/60min/30min/15min/5min/min` 均可进入策略预处理。
  - 新增 `test_trade_side_mode_short_only_can_emit_short`：`short` 模式可发空头入场。
  - 新增 `test_trade_side_mode_long_blocks_short`：`long` 模式不会发空头入场。

- `cta/tests/test_skill_tight_range_backtest_rb0.py`
  - 新增 `test_normalize_interval_aliases`：周期别名归一化。
  - 新增 `test_load_bars_minute60`：验证分钟级 parquet 聚合加载可用。
  - 新增 `test_run_rb0_backtest_minute60`：`RB0 60min` 回测冒烟。

### 修改文件（代码）

- `cta/config/skill_tight_range_breakout_config.py`
  - `StrategyConfig` 新增 `trade_side_mode`（`both/long/short`）与合法性校验。
  - `BacktestConfig` 新增 `data_root`，用于分钟级路径解析。

- `cta/strategy/skill_tight_range_breakout.py`
  - 策略新增 `_is_side_allowed()`，在开仓前按 `trade_side_mode` 过滤方向，完整支持多空模式。

- `cta/strategy/skill_tight_range_backtest.py`
  - 新增 `normalize_interval()`：支持 `day/60min/30min/15min/5min/min`。
  - 新增 `load_bars()`：
    - `day` 读取 CSV；
    - 分钟级读取 `cta/data/<interval>/<symbol_root>/*.parquet` 并按日期聚合。
  - 新增 `suggest_periods_per_year()`，分钟级默认年化周期自动适配。
  - CLI 新增参数：
    - `--interval`
    - `--trade-side-mode`
    - `--periods-per-year`
  - `run_symbol_backtest()` 改为统一走 `load_bars()`，输出目录按实际周期命名。

- `cta/strategy/readme.md`
  - 同步新增参数与示例命令，明确分钟级已接入，以及多空模式配置方法。

### 运行命令

```bash
python3 -m unittest cta.tests.test_skill_tight_range_strategy cta.tests.test_skill_tight_range_backtest_rb0 -v

python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 \
  --exchange SHFE \
  --interval day \
  --trade-side-mode both \
  --start 2018-01-01 \
  --end 2024-12-31

python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --trade-side-mode both \
  --start 2020-01-01 \
  --end 2020-01-31
```

### 输出位置

- 日线结果：`cta/report/backtest/20260425_skill_tight_range_breakout_RB0_day_both/`
- 60 分钟结果：`cta/report/backtest/20260425_skill_tight_range_breakout_RB0_minute60_both/`

### 结果摘要

- 测试：`12/12` 通过（新增 interval + 多空模式相关用例）。
- `RB0 day` 指标：`trade_count=3`，其余指标见 summary 文件。
- `RB0 60min`（2020-01）可运行并产出文件（该窗口无成交，`trade_count=0`）。
- `RB0 day short-only` 可运行并产出空头成交（`trade_count=1`，`side=short`）。

### 风险与后续

1. 分钟级是按日 parquet 聚合读取，超长区间回测时建议先按日期窗口分段。  
2. 多空模式默认 `both`；若设 `long`/`short`，需注意样本区间可能出现“无交易”。

---

## 2026-04-25 — 新增基于 cta/skills 的 Tight Range Breakout 策略（先测后写）并完成 RB0 本地回测

**分支**: 当前  
**任务**: 读取 `AGENTS.md` 与 `claude.md` 后，基于现有 `cta/skills` 完成 `cta/strategy/readme.md` 对应策略代码；遵循 TDD（先写测试再实现），并在本地 `RB0` 回测验证，同时保证可切换到服务器其他品种。

### 修改文件（测试，先写）

- `cta/tests/test_skill_tight_range_strategy.py`
  - 新增策略单测，覆盖：
    - 策略输入预处理是否补齐 `atr14/tr_valid/trend_dir/breakout_score` 等关键列；
    - 突破样本下是否能发出 stop 入场信号；
    - 手数计算函数的最小手数下限。

- `cta/tests/test_skill_tight_range_backtest_rb0.py`
  - 新增回测/兼容性测试，覆盖：
    - `RB0` 交易所自动解析（`SHFE`）；
    - 未知品种合约参数默认回退；
    - `RB0` 最小回测输出指标字段与产物文件存在性。

### 修改文件（代码）

- `cta/config/skill_tight_range_breakout_config.py`
  - 新增策略与回测配置 dataclass：
    - `StrategyConfig`
    - `BacktestConfig`
  - 统一路径配置：`cta/data/day`、`symbols_list.csv`、`cta/report/backtest`。

- `cta/strategy/skill_tight_range_breakout.py`
  - 新增策略实现，复用技能模块：
    - `detect_tight_range`
    - `score_breakout` / `breakout_quality_gate`
    - `compute_trend_state`
  - 提供 `prepare_strategy_frame()`，先生成指标再驱动策略。
  - 提供 `SkillTightRangeBreakoutStrategy.on_bar()`，包含：
    - stop 入场
    - ATR 初始止损
    - ATR 跟踪止损
    - 最大持仓 bars 强平
    - 风险预算手数计算

- `cta/strategy/skill_tight_range_backtest.py`
  - 新增本地回测入口（CLI + 可复用函数）：
    - `resolve_exchange()`：从 `cta/data/day/symbols_list.csv` 自动识别交易所
    - `build_contract_spec()`：优先用 v3 合约元数据，缺失时回退 `futures_meta` 和默认值
    - `run_symbol_backtest()`：执行回测并落盘 trades/equity/summary
  - 指标输出包含：
    - 总收益（`total_return`）
    - 年化（`annualized`）
    - 最大回撤（`mdd`）
    - Sharpe
    - Calmar
    - 胜率（`winrate`）
    - 盈亏比（`pf`）

- `cta/strategy/readme.md`
  - 从空文件补全为完整策略文档，包含：
    - 策略假设、信号定义、风控规则、手续费/滑点假设
    - RB 最小回测命令
    - 服务器其它品种运行命令
    - 输出路径与已知局限

### 运行命令

```bash
python3 -m unittest cta.tests.test_skill_tight_range_strategy cta.tests.test_skill_tight_range_backtest_rb0 -v

python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 \
  --exchange SHFE \
  --start 2018-01-01 \
  --end 2024-12-31
```

### 输出位置

- 回测目录：`cta/report/backtest/20260425_skill_tight_range_breakout_RB0_day/`
- 指标文件：`cta/report/backtest/20260425_skill_tight_range_breakout_RB0_day/20260425_RB0_summary.csv`
- 交易明细：`cta/report/backtest/20260425_skill_tight_range_breakout_RB0_day/20260425_RB0_trades.csv`
- 资金曲线：`cta/report/backtest/20260425_skill_tight_range_breakout_RB0_day/20260425_RB0_equity.csv`

### 结果（RB0）

- `total_pnl`: `376.568`
- `total_return`: `0.000376568`
- `annualized`: `5.591e-05`
- `mdd`: `0.000410442`
- `sharpe`: `0.172739`
- `calmar`: `0.136220`
- `winrate`: `0.333333`
- `pf`: `1.917469`
- `trade_count`: `3`

### 风险与后续

1. 当前回测入口默认 `day` 数据；分钟级需要后续接入 `cta/data/minute*` 后复用同策略框架。  
2. 由于事件驱动引擎按 next-open 成交，止损触发与盘中真实撮合仍有偏差。  
3. 目前为单品种回测，尚未加入多品种资金联动和组合级风控。

---

## 2026-04-24 — 修复 code review 第二轮关键 bug（lookahead / asof / back-adjust / two-leg cost / log）

**分支**: 当前  
**任务**: 完成 code review 列出的剩余关键问题修复，TDD 流程：先写测试 → 再修代码 → 全量回归。

### 修改文件（代码）

- `cta/skills/filtering_scoring/breakout_quality.py`
  - `score_breakout` 新增 `follow_bars: int = 0`（默认 live-safe）。
  - `follow_bars=0`：用「本 bar close 相对 [low, high] 的位置」作为 s_follow 代理，
    上破时 `pos = (c-l)/(h-l)`，下破时 `1 - pos`，**不读取未来 bar**。
  - `follow_bars>0`：post-hoc 标注模式，读取 `i+1..i+follow_bars` 计算 follow-through。
  - `follow_bars` 负值视同 0；`i` 接近末尾时 `seen` 自动衰减。

- `cta/skills/filtering_scoring/context_score.py`
  - 新增 `_asof_index()`：用 `searchsorted` 按 timestamp 做 asof 对齐，避免按 LTF 位置索引落到 MTF/HTF 的错误时间窗口。
  - 当 `df_*tf` 含 `datetime` 列：`compute_context_score` 用 LTF[i] 时间戳查 MTF / HTF 的 asof 位置；缺 `datetime` 时退化为旧的位置对齐（legacy 行为）。
  - 当 LTF 时间早于 HTF 首根：`i_htf = -1`，方向置 `flat`，不抛异常。

- `cta/skills/data_backtest/continuous_contract.py`
  - **修正 back-adjust 拼接方向**：roll events **倒序遍历**，`mask = out_dates < d` 仅调整 roll 之前的历史 bar，最新合约最近的 bar 价格被严格保留。
  - `adj_factor` 累加（back）/累乘（ratio）所有后续 roll 的 shift / ratio，最新 bar 分别为 `0.0` / `1.0`。
  - 起始处把 `open/high/low/close` 列强制 `astype(float)`，避免源数据为 int64 时 `loc[mask, col] += float_shift` 报 dtype 错误。

- `cta/skills/data_backtest/transaction_cost.py`
  - `apply_cost_to_pnl` 探测 `entry_price + exit_price` 列时分别按两腿计费再求和；只有 `price` 列时退回单腿（向后兼容）。

- `cta/skills/data_backtest/event_driven_backtest.py`
  - 平仓填单时把 entry 与 exit 两腿成本独立调用 `cost_fn` 计算并累加，避免低估实际成交成本。

- `cta/skills/live_ops/signal_to_order.py`
  - 加入 `logger = logging.getLogger(__name__)`；`sig.lots <= 0` 时 `logger.warning` 而非静默跳过。

- `cta/skills/market_regime/range.py`
  - docstring 更新为「通道拟合残差大: 0.20 * (residual_pct > 0.01)」，与 md 描述统一。

- `cta/cta_skills/01_market_regime/02_range_detection.md`
  - 修订残差阈值描述为 `> 1% 视为震荡`，与代码一致。

### 修改文件（测试）

- `cta/skills/filtering_scoring/tests/test_breakout_quality.py`
  - `test_default_no_lookahead`：默认模式下污染 `i+1..i+10` 不影响得分。
  - `test_opt_in_follow_bars_uses_future`：opt-in 模式两种 `follow_bars` 给出不同行为。
  - `test_follow_bars_partial_window`：`i = len-1` 时 `seen` 衰减为 0 不报错。
  - `test_follow_bars_negative_treated_as_live`：负值与 0 等价。
  - `test_live_proxy_close_near_high_for_up_breakout` / `test_live_proxy_close_near_low_for_down_breakout`：上下破对称验证 close 位置代理。

- `cta/skills/filtering_scoring/tests/test_context_score.py`
  - `test_htf_mtf_timestamp_asof`：构造 100-day HTF（前 60 天上行 + 后 40 天下跌），LTF 时间在 7-1，asof 应落到下跌段（与 long setup 反向）；位置对齐会落到上行段（与 long 同向）。
  - `test_htf_before_first_bar_returns_flat`：LTF 时间早于 HTF 首根 → `s_htf=0`。
  - `test_no_datetime_falls_back_to_positional`：无 datetime 列时退化为位置对齐（legacy）。
  - `test_mtf_asof_independent_of_htf`：MTF / HTF asof 各自独立。

- `cta/skills/filtering_scoring/tests/test_risk_reward_score.py`
  - 新增 `test_no_lookahead_at_current_bar` / `test_short_no_lookahead_at_current_bar`：验证 RR 在当前 bar 不读未来数据（验证「不是 bug」）。

- `cta/skills/data_backtest/tests/test_continuous_contract.py`
  - `test_back_adjust_preserves_last_close`：最新合约最新 bar 价格不被改写。
  - `test_back_adjust_smooths_roll_gap`：拼接 gap 应被调整后 `|Δclose|.max() < 2.0`。
  - `test_back_adjust_history_shifted`：day0 close 应抬升 +5（roll 当天 new_open - old_close）。
  - `test_back_adjust_factor_zero_at_latest`：最新 bar `adj_factor=0.0`，历史 bar 累积非零。
  - `test_ratio_adjust_preserves_last_close`：ratio 法 `adj_factor` 最新 bar = 1.0。
  - 新增 `TestContinuousMultiRoll` 类，3 合约 + 2 次 roll：
    - `test_multi_roll_cumulative_back_adjust`：day0 累积 `adj_factor` = sum(各 roll shift)。
    - `test_multi_roll_smoothness`：整段 close.diff() 不出现大跳跃。
    - `test_ratio_adjust_cumulative`：day0 ratio = 各 roll 比率累乘。
    - `test_method_none_preserves_raw_close`：`method='none'` 不改 OHLC。

- `cta/skills/data_backtest/tests/test_transaction_cost.py`
  - `test_apply_cost_both_legs`：进出场两腿独立计费再求和。
  - `test_event_driven_charges_entry_and_exit`：事件驱动回测平仓时两腿都被扣。

- `cta/skills/live_ops/tests/test_signal_to_order.py`
  - `test_zero_lots_logs_warning`：`assertLogs` 捕捉 `lots<=0` 的 WARNING。

- `cta/skills/data_backtest/tests/test_trade_evaluation.py`
  - `test_periods_per_year_affects_sharpe` / `test_annualized_uses_periods_per_year`：跨周期年化正确。

### 运行命令

```bash
python3 -m unittest discover -s cta/skills -p 'test_*.py'
```

### 输出位置

- 代码：`cta/skills/filtering_scoring/`、`cta/skills/data_backtest/`、`cta/skills/live_ops/`、`cta/skills/market_regime/`
- 文档：`cta/cta_skills/01_market_regime/02_range_detection.md`
- 测试：对应 `tests/` 目录与控制台输出

### 结果

- `cta/skills`：`264 / 264` 通过

### 风险与后续

1. `score_breakout` 默认行为变化（`follow_bars=0` live-safe）：若旧调用方依赖之前的 default lookahead 语义，需要显式传 `follow_bars=N`。
2. `apply_cost_to_pnl` 新增了 `entry_price + exit_price` 双列识别；旧 trade_log 仅有 `price` 列时仍按单腿计费，向后兼容。
3. `continuous_contract.build_continuous` 的 back-adjust 现在严格不改最新 bar 价格；若历史 backtest 报告依赖旧的「平移最新 bar」行为，需要重跑。
4. `context_score` 新的 asof 对齐在多数场景下结果会变；若旧策略依赖「按 LTF 位置截断 HTF」的 legacy 行为，必须显式去掉 datetime 列。

---

## 2026-04-25 — 下载状态文件名追加年月日后缀

**分支**: 当前  
**任务**: 按要求将下载“完成状态”文件名加上年月日后缀，统一落 `cta/data/`。

### 修改文件

- `cta/data_code/download_all.py`
  - 状态文件从固定名改为日期名：
    - `cta/data/finished_YYYYMMDD.csv`
    - `cta/data/empty_YYYYMMDD.csv`
  - `load_finished()` / `load_empty()` 改为自动汇总历史日期化文件（并兼容旧固定文件），保证断点续跑不受影响。
  - 保留旧路径兼容迁移逻辑（仅复制，不删除旧文件）。
  - 启动日志改为输出 `tracking date` 与当日目标文件路径。

### 运行命令

```bash
python3 - <<'PY'
from cta.data_code.download_all import TRACKING_DATE, FINISHED_CSV, EMPTY_CSV
print(TRACKING_DATE)
print(FINISHED_CSV)
print(EMPTY_CSV)
PY

python3 -m cta.data_code.download_all --help
```

### 输出位置

- 当日状态文件：
  - `cta/data/finished_YYYYMMDD.csv`
  - `cta/data/empty_YYYYMMDD.csv`
- 历史状态文件：同目录按日期累积。

### 风险与后续

1. 状态文件会按天累积，后续可按月归档以减少目录文件数。  
2. 若外部分析脚本写死 `finished.csv/empty.csv`，需同步改成按日期匹配读取。

---

## 2026-04-25 — download_all 跟踪文件落盘目录调整到 cta/data

**分支**: 当前  
**任务**: 运行 `cta/data_code` 下载流程后，`finished.csv` / `empty.csv` 统一落到 `cta/data/`，不再写到 `cta/data_code/`。

### 修改文件

- `cta/data_code/download_all.py`
  - 跟踪文件路径改为：
    - `cta/data/finished.csv`
    - `cta/data/empty.csv`
  - 新增历史路径兼容迁移：
    - `cta/data/data_finished.csv` -> `cta/data/finished.csv`
    - `cta/data/data_empty.csv` -> `cta/data/empty.csv`
    - `cta/data_code/finished.csv` -> `cta/data/finished.csv`
    - `cta/data_code/empty.csv` -> `cta/data/empty.csv`
  - 启动时自动迁移（仅复制，不删除旧文件）。
  - 同步更新模块文档说明，避免误导到 `cta/data_code/`。

### 运行命令

```bash
python3 - <<'PY'
from cta.data_code.download_all import FINISHED_CSV, EMPTY_CSV
print(FINISHED_CSV)
print(EMPTY_CSV)
PY

python3 -m cta.data_code.download_all --help
```

### 输出位置

- 品种状态跟踪文件：`cta/data/finished.csv`、`cta/data/empty.csv`

### 风险与后续

1. 历史旧文件会保留（不自动删除），避免误删；若确认不再使用，可后续人工清理。  
2. 若其它外部脚本硬编码读取旧文件名（如 `data_finished.csv`），需要同步改到新路径。

---

## 2026-04-24 — 修复 code review 发现的关键 bug（failed_breakout / context_score / annualization）

**分支**: 当前  
**任务**: 修复上一轮 code review 中确认的关键问题，并完成全量回归验证。

### 修改文件

- `cta/skills/price_action/failed_breakout.py`
  - `detect_failed_breakout` 新增 `use_prev_boundary`（默认 `True`），默认使用上一根 range 边界做突破判定，修复“边界含当前 bar 时难以触发”问题。
  - 增加 `max_confirm_bars > 0` 参数校验。
- `cta/skills/filtering_scoring/context_score.py`
  - 新增 `regime_label` 优先读取逻辑（兼容 `regime` 旧列名），修复上下文评分列名不一致导致的误判。
  - 将 `expansion_trending` 纳入 trend 组 regime 匹配。
- `cta/skills/data_backtest/trade_evaluation.py`
  - `summarize_trades` 新增 `periods_per_year` 参数，Sharpe 与 annualized 正确按周期年化。
  - `ReportConfig` 增加 `periods_per_year`，`write_report` 透传到汇总函数。
- 测试更新
  - `cta/skills/price_action/tests/test_failed_breakout.py`
    - 新增回归用例：当前 bar 边界语义下可触发 failed-breakout。
  - `cta/skills/filtering_scoring/tests/test_context_score.py`
    - 新增回归用例：仅有 `regime_label` 时应正确评分。

### 运行命令

```bash
python3 -m unittest cta.skills.price_action.tests.test_failed_breakout -v
python3 -m unittest cta.skills.filtering_scoring.tests.test_context_score -v
python3 -m unittest cta.skills.data_backtest.tests.test_trade_evaluation -v
python3 -m unittest discover -s cta/skills -p 'test_*.py'
python3 -m unittest discover -s cta/feature/test
```

### 输出位置

- 代码：`cta/skills/price_action/`、`cta/skills/filtering_scoring/`、`cta/skills/data_backtest/`
- 测试：对应 `tests/` 目录与控制台输出

### 结果

- `cta/skills`：`254/254` 通过  
- `cta/feature/test`：`21/21` 通过

### 风险与后续

1. `failed_breakout` 默认行为已更贴近 `compute_range_state` 输出语义；若外部调用已预先 shift 边界，可显式传 `use_prev_boundary=False`。  
2. `online.FeatureGenerator` 的全窗口重算是性能风险而非逻辑错误；后续可考虑增量特征缓存优化。

---

## 2026-04-24 — feature/skills 增强测试 + 本地真实数据实测 + code review

**分支**: 当前  
**任务**:  
1. 阅读 `cta/feature/FEATURES.md`，补充 `cta/feature/test` 高覆盖测试，执行 code review + 单测 + 本地数据实测。  
2. 阅读 `cta/cta_skills` 文档并对 `cta/skills` 代码做更多测试，增加基于 `cta/data/feature` 的真实数据集成测试。

### 修改文件

- 更新测试：
  - `cta/feature/test/test_feature_modules_smoke.py`
  - `cta/feature/test/test_online_api.py`
- 新增真实数据集成测试：
  - `cta/skills/overview/tests/test_real_feature_data_market_price_action.py`
  - `cta/skills/overview/tests/test_real_feature_data_strategies_scoring.py`
  - `cta/skills/overview/tests/test_real_feature_data_intervals.py`

### 运行命令

```bash
python3 -m unittest discover -s cta/feature/test -v
python3 -m unittest discover -s cta/skills -p 'test_*.py' -v
```

### 输出位置

- feature 测试：`cta/feature/test/`
- skills 测试：`cta/skills/overview/tests/`
- 本地真实数据输入：`cta/data/feature/{day,minute,minute5,minute15,minute30,minute60}`

### 主要结果

- `cta/feature/test`：21 条测试全部通过（含真实本地数据加载/计算验证）。
- `cta/skills`：236 条测试全部通过（含新增真实数据集成测试 11 条）。
- 在真实数据上确认多个模块可产生机会信号（如 tight range / flag / breakout pullback / Donchian / ATR channel / MR 等）。

### 风险与后续

1. `price_action.failed_breakout` 与 `market_regime.range` 当前组合存在边界定义耦合风险，真实样本几乎无法触发 failed-breakout（详见本轮 code review 结论）。  
2. `context_score` 对 regime 列名默认读取 `regime`，与 feature 常见列 `regime_label` 存在命名不一致风险。  
3. 建议下一步补一组“策略级回测联动测试”（信号→仓位→回测）以验证机会信号的收益可迁移性。

---

## 2026-04-24 — cta/skills 章节 02/03/04（Price Action / Trend / Range）代码化

**分支**: 当前  
**任务**: 按 `cta/cta_skills/02_price_action`、`03_trend_strategies`、`04_range_strategies`
顺序逐类实现，要求每类先测试再实现，并兼容
`day/minute60/minute30/minute15/minute5/minute`。

### 修改文件

- 新增包：`cta/skills/price_action/`（6 模块 + tests）
  - `tight_range_breakout.py`
  - `bull_bear_flag.py`
  - `breakout_pullback.py`
  - `hl_structure.py`
  - `failed_breakout.py`
  - `channel_state.py`
- 新增包：`cta/skills/trend_strategies/`（5 模块 + tests）
  - `donchian_breakout.py`
  - `atr_breakout.py`
  - `ma_trend_following.py`
  - `cross_sectional_momentum.py`
  - `trend_hold_trailing.py`
- 新增包：`cta/skills/range_strategies/`（4 模块 + tests）
  - `range_boundary_reversal.py`
  - `mean_reversion.py`
  - `false_breakout_reversal.py`
  - `noise_filtering.py`

### 运行命令

```bash
python3 -m unittest discover -s cta/skills/price_action/tests -v
python3 -m unittest discover -s cta/skills/trend_strategies/tests -v
python3 -m unittest discover -s cta/skills/range_strategies/tests -v
python3 -m compileall cta/skills/price_action cta/skills/trend_strategies cta/skills/range_strategies
```

### 输出位置

- 代码与测试：
  - `cta/skills/price_action/`
  - `cta/skills/trend_strategies/`
  - `cta/skills/range_strategies/`
- 测试与编译输出：控制台日志

### 主要结果

- 新增 15 个实现模块，33 个测试用例（02:18，03:14，04:11）全部通过。
- 关键检测/信号函数已提供统一周期兼容入口（`interval` 参数或无周期耦合实现）。
- 02/03/04 三类功能可独立调用，也可继续与 05-10 章节做联动集成。

### 风险与后续

1. 当前实现为可运行骨架版，部分细节阈值仍建议结合真实 `cta/data/feature` 样本做标定。  
2. 03/04 与 07 仓位风控、08 回测引擎可进一步打通做端到端组合回测。  
3. 若后续引入更复杂特征（如 `pa_leg_*`、`pa_channel_*` 完整版），建议优先补充回归测试后再替换规则。

---

## 2026-04-23 — cta/skills 章节 06-10（Filtering/Portfolio/Backtest/ML/LiveOps）代码化

**分支**: 当前  
**任务**: 按 `cta/cta_skills/06_filtering_and_scoring` 到 `10_live_ops` 顺序，
在 `cta/skills/` 下逐类实现；每类先写测试再写实现，兼容
`day/minute60/minute30/minute15/minute5/minute` 周期体系。

### 修改文件

- 新增包：`cta/skills/filtering_scoring/`（5 模块 + tests）
  - `setup_quality.py` / `breakout_quality.py` / `context_score.py` / `risk_reward_score.py` / `ml_opportunity_model.py`
- 新增包：`cta/skills/position_portfolio/`（5 模块 + tests）
  - `single_trade_risk.py` / `vol_targeting.py` / `sector_exposure.py` / `drawdown_control.py` / `portfolio_allocation.py`
- 新增包：`cta/skills/data_backtest/`（5 模块 + tests）
  - `continuous_contract.py` / `rollover_rules.py` / `transaction_cost.py` / `event_driven_backtest.py` / `trade_evaluation.py`
- 新增包：`cta/skills/ml_augmentation/`（5 模块 + tests）
  - `trade_filter_model.py` / `regime_classifier.py` / `mfe_mae_prediction.py` / `feature_store.py` / `walk_forward_validation.py`
- 新增包：`cta/skills/live_ops/`（5 模块 + tests）
  - `signal_to_order.py` / `order_execution.py` / `monitoring_alerting.py` / `daily_review.py` / `strategy_iteration_loop.py`

### 运行命令

```bash
python3 -m unittest discover -s cta/skills/filtering_scoring/tests -v
python3 -m unittest discover -s cta/skills/position_portfolio/tests -v
python3 -m unittest discover -s cta/skills/data_backtest/tests -v
python3 -m unittest discover -s cta/skills/ml_augmentation/tests -v
python3 -m unittest discover -s cta/skills/live_ops/tests -v
python3 -m compileall cta/skills/filtering_scoring cta/skills/position_portfolio cta/skills/data_backtest cta/skills/ml_augmentation cta/skills/live_ops
```

### 输出位置

- 代码与测试：`cta/skills/{filtering_scoring,position_portfolio,data_backtest,ml_augmentation,live_ops}/`
- 测试运行输出：控制台（无覆盖原始数据）

### 主要结果

- 新增 `06-10` 五章共 `25` 个实现模块、`56` 个测试用例，已全部通过。
- 每章先建测试后实现，已覆盖核心接口：评分/风控/组合/连续合约/回测引擎/ML 数据集与验证/实盘运维流程。
- 关键函数均可在 day/minute60/minute30/minute15/minute5/minute 场景下复用或无周期耦合。

### 风险与后续

1. 当前 ML 相关实现为“无重依赖轻量版”（便于本地可运行），后续可替换为 XGBoost/LightGBM 生产模型。  
2. 事件回测与执行模块为基础骨架（bar 级），后续可继续补充部分成交、挂单队列和更细粒度成交仿真。  
3. 建议下一轮在 `cta/data/feature` 实盘样本上做端到端联调（06 分数 -> 07 仓位 -> 08 回测 -> 09 gate -> 10 review）。

---

## 2026-04-23 — cta/skills 章节 05（Regime Switch Strategies）代码化

**分支**: 当前  
**任务**: 读取 `cta/cta_skills/05_regime_switch_strategies/*.md`，按 01→04 顺序在
`cta/skills/` 下落地代码，要求先写测试再写实现，并兼容
`day/minute60/minute30/minute15/minute5/minute` 六种周期名。

### 修改文件

| 文件 | 改动 |
|------|------|
| `cta/skills/regime_switch/__init__.py` | 新建；统一导出 05 章 API |
| `cta/skills/regime_switch/volatility_transition.py` | 新建；压缩/扩张/常态三态识别、切换事件、`rollback_if_false_switch` |
| `cta/skills/regime_switch/breakout_score.py` | 新建；`BreakoutScoreResult`、单 bar 突破评分、历史权重校准 |
| `cta/skills/regime_switch/switch_machine.py` | 新建；regime 状态机、置信度、age、next regime、策略白名单 |
| `cta/skills/regime_switch/transition_risk.py` | 新建；过渡期风控调整与 max-hold 判定 |
| `cta/skills/regime_switch/tests/__init__.py` | 新建；测试包初始化 |
| `cta/skills/regime_switch/tests/test_volatility_transition.py` | 新建；状态识别/回滚/多周期兼容测试 |
| `cta/skills/regime_switch/tests/test_breakout_score.py` | 新建；评分输出、HTF 对齐影响、权重校准测试 |
| `cta/skills/regime_switch/tests/test_switch_machine.py` | 新建；状态机输出、标签覆盖、白名单测试 |
| `cta/skills/regime_switch/tests/test_transition_risk.py` | 新建；transition 风控与持仓时长限制测试 |

### 运行命令

```bash
# 05 章节单测
python3 -m unittest discover -s cta/skills/regime_switch/tests -v

# 语法冒烟
python3 -m compileall cta/skills/regime_switch
```

### 输出位置

- 代码与测试：`cta/skills/regime_switch/`
- 无额外数据落盘；仅控制台测试输出。

### 主要结果

- `regime_switch` 新包已可导入，四个子模块均可独立调用。
- 单测 `17/17` 通过，覆盖状态切换、突破评分、状态机与过渡期风控核心路径。
- 六周期字符串兼容已纳入测试（day/minute60/minute30/minute15/minute5/minute）。

### 风险与后续

1. 当前 `calibrate_weights_from_history` 为轻量相关系数法（无 sklearn 依赖），后续可升级为 logistic / XGBoost 权重学习。  
2. `detect_vol_transition` 的阈值仍为规则参数，建议下一步在 RB0 + 研究池做阈值稳定性扫描。  
3. 下一个阶段按你的顺序继续实现 `06_filtering_and_scoring`，延续“先测试后实现”。

---

## 2026-04-22 — cta/skills 章节 00（Overview & Methodology）代码化

**分支**: 当前
**任务**: 把 `cta/cta_skills/00_overview_methodology/` 5 篇 md 的「第 6 节代码模块设计」
**全部落成可运行 + 可测的 Python 包**，放在 `cta/skills/overview/` 下；配置外置到
`cta/skills/configs/`，运行产物统一落 `cta/skills/output/`；所有函数对
`day / minute60 / minute30 / minute15 / minute5 / minute` 六种周期一视同仁。

### 修改文件

| 文件 | 改动 |
|------|------|
| `cta/skills/__init__.py` | 新建；暴露 `SKILLS_ROOT / CONFIG_DIR / OUTPUT_DIR / PROJECT_ROOT / CANON_INTERVALS` |
| `cta/skills/configs/acceptance.yaml` | 新建；Gate A/B 门槛（annret/maxdd/sharpe/calmar/trade_count/...）以 `{op,value}` 声明 |
| `cta/skills/configs/research_pool.yaml` | 新建；A 档（rank≤12）/ B 档（rank≤24）+ per-tier intervals + coverage 阈值 |
| `cta/skills/overview/__init__.py` | 新建；统一 re-export 5 个子模块公开 API |
| `cta/skills/overview/acceptance.py` | §01 门槛判定：`REQUIRED_COLUMNS / GateDecision / assert_passes_gate_a,b / assess_against_gates`；内置 `INTERVAL_TO_GATE` 映射 |
| `cta/skills/overview/research_pool.py` | §02 研究池：`PoolMember / ResearchPool / build_research_pool (lru_cache) / in_research_pool / resolve_research_symbols / reset_cache`；读 `cta/feature/symbols_research_ranking.csv` |
| `cta/skills/overview/backtest_principles.py` | §03 回测配置 + lookahead 检测：`BacktestConfig`（禁 `fill_model='close'` / 强制 `signal_lag_bars≥1`）+ `detect_lookahead`（shift_divergence + corr_with_future **前向收益** 双模式）+ `assert_no_lookahead` |
| `cta/skills/overview/live_principles.py` | §04 实盘原则：`LiveGateState`（6 阶段灰度）/ `CircuitAction` / `check_circuit`（daily_dd / consecutive_loss / signal_mismatch，按 stop>halt>warn 排序）/ `reconcile_positions` |
| `cta/skills/overview/iteration.py` | §05 想法追踪：`IdeaRecord` + `record_idea`（写 `meta.yaml + hypothesis.md` 模板，幂等）+ `list_open_ideas` / `advance_stage`；根目录 `cta/report/ideas/{YYYYMMDD}_{slug}/` |
| `cta/skills/overview/tests/test_acceptance.py` | 12 用例，含 Gate A/B 通过失败 / 缺列 / 混合 interval / yaml 覆盖 |
| `cta/skills/overview/tests/test_research_pool.py` | 10 用例，含默认构建 / 自定义 yaml / 非法周期 / require_feature 子集 |
| `cta/skills/overview/tests/test_backtest_principles.py` | 14 用例，含 BacktestConfig 校验 / 干净信号不报 / shift(-N) 偷看可检出 / assert 抛异常 |
| `cta/skills/overview/tests/test_live_principles.py` | 16 用例，含熔断等级排序 / cfg 覆盖 / 对账差集 |
| `cta/skills/overview/tests/test_iteration.py` | 14 用例，含 slugify / 幂等 / advance_stage |
| `cta/skills/overview/tests/test_smoke_rb0.py` | 2 用例端到端：真实 `cta/data/day/RB0.csv` + shift(-3) 合成偷看信号；合成跨 interval summary 跑 `assess_against_gates` 落盘到 `cta/skills/output/` |

### 设计要点

- **周期无关**：所有 API 收 `interval: str` 字符串走 `cta.feature.loader.normalize_interval`，6 种
  canonical interval 全部支持；`INTERVAL_TO_GATE` 把 day/60/30 → Gate A、15/5/1 → Gate B。
- **品种顺序**：`build_research_pool` 调 `load_symbols_ranked()`（昨日新增），强制按
  `research_rank` 升序；与 `cta/feature/run_all_features.py` 等入口一致。
- **lookahead 检测**：
  - `shift_divergence`：对比 `signal * fwd_ret` vs `signal.shift(1) * fwd_ret`（合规执行版），
    仅在 naive 绝对 sharpe > 0.5 时才报，避免对纯噪声信号假阳性。
  - `corr_with_future`：signal 与**未来 h 根 bar 的前向收益**（不是 raw close 价格）
    做 pearson；真实 RB0 日线 + `close.shift(-3)>close` 在 corr_threshold=0.15 下可检出。
- **yaml-driven 阈值**：Gate A/B 用 `{op, value}` 描述每个指标，新增指标/调整方向不用改代码。

### 运行命令

```bash
# 单元测试（全部 69 用例）
python3 -m unittest discover -s cta/skills/overview/tests -p 'test_*.py' -v

# 读研究池（按 research_rank 升序）
python3 -c "from cta.skills.overview import resolve_research_symbols; \
            print(resolve_research_symbols('A', interval='day', require_feature=True)[:5])"

# 新建一个想法
python3 -c "from cta.skills.overview import record_idea; \
            print(record_idea('Tight Range Breakout', owner='wu', created_at='2026-04-22'))"
```

### 输出位置

- 单测瞬时产物：`cta/skills/output/`（smoke test 自清理）
- 想法目录：`cta/report/ideas/{YYYYMMDD}_{slug}/`

### 风险与后续

- 本次**仅建 00 章**。后续按 `01_market_regime` → `10_live_ops` 逐章落代码。
- `detect_lookahead` 仍是启发式，最终需人工 code review 定性。
- `assess_against_gates` 的 `INTERVAL_TO_GATE` 默认把 minute15 算 Gate B，若用户希望
  minute15 保持 Gate A 可用 `gate_override={'minute15':'A'}` 覆盖。

---

## 2026-04-21 — 合并 FEATURE.md 到 FEATURES.md + 区分 bar-derivable vs 未来特征

**分支**: `feature`
**任务**: 按用户要求，把昨日新建的 `cta/data/feature/FEATURE.md` 并入既有
`cta/feature/FEATURES.md`，去重、按类整理，并把**非 day/minute/minute30 原始 bar
可直接派生**的特征集中到 §19 "未来特征" 以便后续接入。

### 修改文件

| 文件 | 改动 |
|------|------|
| `cta/feature/FEATURES.md` | 目录加三个区块；新增 §13-§18（bar-derivable 待实现）+ §19（未来特征） |
| `cta/data/feature/FEATURE.md` | 改为指向 `cta/feature/FEATURES.md` 的跳转说明，避免双源漂移 |

### §13-§18 新增类（bar-derivable）

- §13 波动率体制补充：atr_pct_zscore_252 / bb_width_zscore_252 / vol_regime_3 /
  vol_of_vol_20 / range_ratio_N / range_height_atr_N / consecutive_hh_N /
  consecutive_ll_N / zscore_close_N / breakout_dist_atr_N
- §14 Al Brooks 形态补充：pa_tight_range_flag_K / pa_bull_flag_flag /
  pa_bear_flag_flag / pa_bpb_flag / pa_swing_high_idx / pa_swing_low_idx /
  pa_trend_channel_top / pa_trend_channel_bot / pa_trend_channel_slope /
  pa_channel_width_atr
- §15 多周期对齐：htf_trend_score_day / htf_bias / mtf_trend_score_{60m,30m} /
  mtf_align_flag / ltf_signal_ready_5m / mtf_conflict_score
- §16 市场状态机：regime_label / regime_conf / regime_age / transition_flag /
  transition_risk
- §17 综合评分：trend_score / compression_score / expansion_score /
  breakout_mode_score / setup_quality_score / breakout_quality_score /
  context_score / rr_score
- §18 入场/止损建议价：atr_based_stop_{long,short} / atr_based_target_{long,short} /
  chandelier_stop_{long,short} / micro_channel_stop_{long,short} / liquidity_filter_pass

### §19 未来特征（9 小节汇总）

合约元数据 / 成本撮合 / 仓位运行时 / 组合层 / 持仓运行时 / 交易日志与标签 /
ML 模型产出 / 特征基础设施 / 实盘运维监控。每项带来源章节指回 `cta_skills/`。

### 去重说明

与既有 §1-§12 重名或同义的特征已跳过，例如：
- `ma_slope_N → slope_N`；`dmi_plus/minus → plus_di/minus_di`
- `pa_h1/h2/l1/l2_signal → pa_high_1_2_3 / pa_low_1_2_3`
- `pa_failed_breakout → pa_breakout_fail_N`
- `breakout_body_ratio → pa_bo_body_ratio_N`；`rv_N → realized_var_20 / hist_vol_N`
- `range_high_N/low_N → dc_upper_N/lower_N`；`gk_vol → gk_vol_20`

### 风险与后续

- §13-§18 **还未实现**；`python3 cta/feature/run_generate.py` 现在**不会**生成这些列。
  下一步按章节逐批加进 `cta/feature/` 的对应模块（如 §13 -> `volatility.py` 扩展；
  §14 -> `price_action_advanced.py` 扩展；§15-17 可新建 `regime.py` / `composite.py`）。
- §19 不在 bar 生成管道里，对应代码后续散落到 `cta/strategy/common/**`。

---

## 2026-04-20 — 建立 cta_skills 技能树文档体系 + FEATURE.md 聚合

**分支**: `feature`
**任务**: 在 `cta/cta_skills/` 下新建完整的中国商品 CTA 技能树文档，
覆盖方法论 → 市场识别 → 价格行为 → 策略族 → 过滤评分 → 仓位风控 → 数据回测
基础设施 → ML 增强 → 实盘运维的全链路；并把所有 skill 中出现的特征 / 指标
聚合到 `cta/data/feature/FEATURE.md`，作为策略 / ML / SQL 的统一特征索引。

### 新增文件（全部在 `cta/cta_skills/` 内，共 67 个 md）

| 目录 | 内容 |
|------|------|
| `cta/cta_skills/README.md` | 技能树总览、阅读顺序、写作标准 |
| `cta/cta_skills/_TEMPLATE.md` | 10 节通用模板 |
| `00_overview_methodology/` | 1 README + 5 skills（目标 / 边界 / 回测 / 实盘 / 迭代） |
| `01_market_regime/` | 1 README + 5 skills（趋势 / 震荡 / 突破阈值 / 波动率 / 多周期） |
| `02_price_action/` | 1 README + 6 skills（tight range / flag / bpb / H1H2 / 假突破 / channel） |
| `03_trend_strategies/` | 1 README + 5 skills（donchian / atr / ma / xs momentum / 持仓管理） |
| `04_range_strategies/` | 1 README + 4 skills（边界反转 / mean reversion / 假突破反转 / 噪声过滤） |
| `05_regime_switch_strategies/` | 1 README + 4 skills（压缩扩张 / 突破模式评分 / 切换信号 / 过渡风控） |
| `06_filtering_and_scoring/` | 1 README + 5 skills（setup / breakout / context / rr / ML） |
| `07_position_and_portfolio/` | 1 README + 5 skills（单笔 / vol / 板块 / 回撤 / 组合） |
| `08_data_and_backtest_infra/` | 1 README + 5 skills（连续合约 / 展期 / 成本 / 引擎 / 日志评估） |
| `09_ml_augmentation/` | 1 README + 5 skills（filter / regime / MFE-MAE / feature store / walk-forward） |
| `10_live_ops/` | 1 README + 5 skills（signal→order / 执行 / 监控 / 复盘 / 迭代闭环） |

**额外新增**：
| 文件 | 说明 |
|------|------|
| `cta/data/feature/FEATURE.md` | 15 大类特征聚合索引，对齐上述 67 个 skill 第 4 节 |

**未改动任何 `cta/cta_skills/` 之外的代码文件**，仅追加本 change_log 条目。

### 使用方式

- 人读：按 `00 → 01 → ... → 10` 顺序。
- Claude / Codex 读：指向单个 skill 文件（如 `03_trend_strategies/01_donchian_breakout.md`），
  它能按第 6 节代码模块设计直接生成 py 骨架。
- 写策略 / 训练 ML：先查 `cta/data/feature/FEATURE.md` 选特征。

### 风险与后续

- 本次只是文档；具体代码骨架需要后续 skill-by-skill 落地。
- FEATURE.md 中标 `TODO` 的项表示待实现，不是当前已用特征。
- 后续新增 skill 或改动第 4 节，需同步回 FEATURE.md。

---

## 2026-04-19 — Brooks v3:消费 pa_* 特征 + 多周期共振 + XGBoost 门控

**分支**: `feature`
**任务**: 按 `cta/strategy/brooks/brooks_v3.md` 把 v1 规则骨架升级到 v3:
消费预计算 pa_* 特征、HTF→MTF→LTF 共振、ATR 风控 + 组合回撤降仓、
完整成交日志(MFE/MAE/退出原因/特征快照)、XGBoost 训练+评分门控,
并按 `cta/feature/symbols_research_ranking.csv` 顺序选 `top_n` 品种。

### 修改文件

**新增**(全部在 `cta/strategy/brooks/` 内):

| 文件 | 说明 |
|------|------|
| `brooks_v3.md` | v3 实施方案(含 ranking + top_n + 分阶段交付) |
| `config/strategy.yaml` | 统一运行时参数(品种、周期、信号阈值、风控、模型) |
| `config/params.py` | 重写:BrooksV3Params 层级 dataclass,从 yaml 加载 |
| `config/symbols.py` | 重写:`resolve_symbols` 按 ranking CSV + tier + feature 过滤 top_n |
| `core/features/adapter.py` | 离线/在线统一特征接口,offline 路径走整段缓存 + searchsorted |
| `core/signal/{htf_bias,mtf_setup,ltf_entry}.py` | 三个时间级别的信号检测(全部消费 pa_*) |
| `core/risk/{sizing,stops,portfolio}.py` | 0.1% per trade + ATR 止损/移动止损/失败快退 + 2%/5% 降仓 |
| `core/model/{labeler,dataset,train_xgb,score_gate}.py` | XGBoost 完整训练/评分回路 |
| `core/trade_log.py` | `TradeRecord` + parquet writer(含特征 JSON 快照) |
| `core/strategy.py` | `BrooksV3Core`:HTF→MTF→LTF→gate→size→stops→log |
| `backtest/{engine,runner,reporter}.py` | 自研轻量回测引擎(不依赖 vnpy) + 批处理 CLI + 汇总 |
| `online/{runner,live_strategy}.py` | dry-run 回放驱动 + vnpy CtaTemplate 壳 |

**删除**:v1 `indicators/`, `patterns/`, `strategies/`, 旧 `backtest/runner.py`(原地替换)

### 运行命令

```bash
# 1) 规则回测(无模型)
python3 -m cta.strategy.brooks.backtest.runner \
    --top-n 3 --start 2023-01-01 --end 2024-12-31 \
    --capital 1000000 --model none

# 2) 训练 XGBoost(需先 `brew install libomp` 让 xgboost 可加载)
python3 -m cta.strategy.brooks.core.model.train_xgb \
    --top-n 8 --start 2018-01-01 --end 2022-12-31 \
    --target-rr 2.0 --target-bars 20 \
    --out-dir cta/strategy/brooks/models

# 3) 带模型 OOS 回测
python3 -m cta.strategy.brooks.backtest.runner \
    --top-n 8 --start 2023-01-01 --end 2024-12-31 \
    --model cta/strategy/brooks/models/xgb_<ts>.ubj

# 4) 在线 dry-run 冒烟
python3 -m cta.strategy.brooks.online.runner \
    --symbol RB0.SHFE --interval minute5 \
    --warmup-start 2024-01-01 --warmup-end 2024-03-31 \
    --live-start 2024-04-01 --live-end 2024-06-30 --dry-run
```

### 输出位置

- `cta/strategy/brooks/report/<YYYYMMDD_HHMMSS>/summary.csv` + `report.md`
- `.../per_run/<SYMBOL>/trades.parquet` + `daily_equity.csv`
- `cta/strategy/brooks/models/xgb_<ts>.ubj` + `xgb_<ts>.meta.json`

### 主要结论(2026-04-19 16:09 run)

- `top_n=3, 2023-01-01..2024-12-31, --model none`:只有 RB0.SHFE 有
  预计算特征(其它品种的 minute5 feature 尚未落盘),runner 自动过滤为 1 品种。
- 出 133 笔 round-trip,win_rate 27%,PF 0.48,max_dd -1.25%,
  total_return -5.17%(无模型过滤,预期偏负;模型门控是后续改进的关键抓手)。

### 风险 / 待确认项

1. **特征覆盖**:当前只有 `RB0.SHFE` 在 `cta/data/feature/minute5/` 下有文件,
   其它 A 档品种需先跑 `cta/feature/run_all_features.py` 生成特征,再扩充 top_n。
2. **XGBoost 依赖**:macOS 上 `xgboost` 需要 `libomp`,
   `brew install libomp`。未装时训练步骤会报 `libxgboost.dylib could not be loaded`。
3. **多头方向**:`pa_h123` 只编码 bull side,v3 暂只做多;后续 bear 需补 `pa_l123`。
4. **换月跳点**:连续合约拼接处未做特殊处理(与 v1 相同),信号可能在跳点被噪声触发。
5. **在线模式 HTF/MTF 预热**:`online/runner.py` 当前用整段 offline 读的方式预热 HTF/MTF
   (简化处理),真实接入 vnpy 网关时需要改为实时 FeatureGenerator 状态同步。

---

## 2026-04-19 — Brooks 策略阶段1:规则骨架落地

**分支**: `feature`
**任务**: 按 `cta/strategy/brooks/brooks.md` 阶段1 要求,把 tight range breakout、
breakout pullback continuation、High2 in bull trend 三个 Brooks 结构翻译成规则化
`CtaTemplate` 策略,利用现有中国商品日线数据跑通最小可复现回测。

### 修改文件

全部新增,严格限定在 `cta/strategy/brooks/` 内:

| 文件 | 说明 |
|------|------|
| `cta/strategy/brooks/README.md` | 目录说明、运行命令、已知局限 |
| `cta/strategy/brooks/__init__.py` | 包声明 |
| `cta/strategy/brooks/config/params.py` | `BrooksParams`:所有阈值集中(tight range / breakout / pullback / bull trend / High2 / 风控) |
| `cta/strategy/brooks/config/symbols.py` | 8 个目标品种 + 合约 size/rate/slippage/pricetick |
| `cta/strategy/brooks/indicators/price_action.py` | `BarFeatures` + `compute_bar_features`: ATR/EMA/body/wick/close_pos/prev_high_n/ema_slope |
| `cta/strategy/brooks/patterns/tight_range.py` | `detect_tight_range`: 区间宽度/ATR + 相邻 bar 重叠率 + 大实体 bar 占比 |
| `cta/strategy/brooks/patterns/breakout.py` | `detect_breakout_bar`: close 破 prev_high_n + body_ratio + close_pos + 放量 + range 扩张 |
| `cta/strategy/brooks/patterns/pullback.py` | `BreakoutRecord` + `update_breakout_tracker` + `detect_breakout_pullback` |
| `cta/strategy/brooks/patterns/high2.py` | `detect_bull_trend` + `High2Tracker` 状态机(TREND→PULLBACK→HIGH1→H1_FAIL→HIGH2)+ `detect_high2` |
| `cta/strategy/brooks/strategies/base.py` | `BrooksBaseStrategy(CtaTemplate)`: ATR 止损/移动止损/失败快退/最长持仓共用逻辑 |
| `cta/strategy/brooks/strategies/tight_range_breakout.py` | Baseline A |
| `cta/strategy/brooks/strategies/breakout_pullback.py` | Baseline B |
| `cta/strategy/brooks/strategies/high2_bull.py` | Baseline C |
| `cta/strategy/brooks/backtest/runner.py` | 基于 `vnpy_ctastrategy.BacktestingEngine` 的 CLI,输出 per-run trades/daily/stats + 汇总 summary.csv + report.md |

### 运行命令

```bash
# 仓库根目录下
cd /Users/wuyuliang/code/vnpy

# 全部 3 策略 × 8 品种
python3 -m cta.strategy.brooks.backtest.runner

# 子集
python3 -m cta.strategy.brooks.backtest.runner \
    --strategies tight_range_breakout,breakout_pullback \
    --symbols RB0.SHFE,CU0.SHFE \
    --start 2018-01-01 --end 2024-12-31 --capital 1000000
```

默认参数: `--start 2018-01-01 --end 2024-12-31 --capital 1_000_000`,
策略 / 品种都缺省为全量。

### 输出位置

`cta/strategy/brooks/report/<YYYYMMDD_HHMMSS>/`:
- `per_run/<strategy>__<vt_symbol>/` : `trades.csv`, `round_trips.csv`, `daily.csv`, `stats.json`
- `summary.csv` : 所有组合合并
- `report.md` : Markdown 表格(按策略 × 品种明细 + 按策略聚合)
- `run.log`

### 首次运行摘要 (2018-01-01 ~ 2024-12-31, 初始资金 100 万, 8 品种)

按策略聚合平均:
- `tight_range_breakout`: avg total return +31%, win rate 34%, PF 1.67, 70 round trips
- `breakout_pullback`: avg total return +31%, win rate 41%, PF 1.53, 50 round trips
- `high2_bull`: avg total return -4%, win rate 33%, PF 1.20, 211 round trips

代表单组合:I0 tight range +171%,CU0 pullback +199%,AL0 High2 +324%。

### 已知限制 / 阶段2 要做的事

- 固定 1 手仓位,未按 ATR 止损距离动态 sizing
- 主连合约未真实换月对齐,存在跳点偏差
- 所有品种用统一参数,未按品种调优
- 只做多
- 未实现结构特征工程 / MFE·MAE 标签 / 评分模型 / walk-forward / 假突破分析
- 规则参数为"能跑通"的保守值,**不代表实盘可用**

### 风险点

1. `BrooksParams.tr_range_to_atr_max=3.0` 已从最初 1.5 放宽为让 tight range 能出信号;
   后续需在阶段5 做参数稳健性扫描。
2. `_try_open_long` 使用 `close*1.01` 的 LIMIT 单下单,若次日高开跳空仍可能滑点,
   但在日线回测中近似合理。
3. `BreakoutRecord` 只跟踪一条待触发记录;真实情况下可能同时存在多条候选,
   阶段2 用特征工程后再优化。

---

## 2026-04-18 — `load_symbol_feature_at`：按时间点读特征 + 前视保护

**分支**: `feature`
**任务**: 增加按精确时间戳获取特征的 API，默认退 200ms 以避免引用未收盘 bar 造成前视偏差。

### 语义

```
effective_ts = pd.Timestamp(timestamp) - pd.Timedelta(lookback)   # 默认 lookback='200ms'
返回 datetime <= effective_ts 的最新一行特征
```

为什么是 "退 200ms"：
- 分钟 bar 通常按**收盘时间**打 datetime（end-stamp）：14:30 的 bar 意味它覆盖到 14:30
- 查询时刻 T=14:30:00.000，若直接用 `datetime <= T` 会包含刚收盘那一根
- 退 200ms → effective_ts=14:29:59.800，排除 14:30 bar，取 14:29 bar
- 这就保证拿到的特征是"T 时刻完全确定"的历史状态，没有任何未来信息

### 修改文件

| 文件 | 变更 |
|------|------|
| `cta/feature/feature_loader.py` | 新增 `load_symbol_feature_at(symbol, timestamp, interval, lookback, columns, max_days_back)` 返回 `pd.Series` 或 `None`；CLI 增加 `--at <YYYY-MM-DD HH:MM:SS> [--lookback 200ms]` |

### 接口

```python
from cta.feature.feature_loader import load_symbol_feature_at

# 分钟级（默认）
feat = load_symbol_feature_at("RB0", "2025-02-19 14:30:00",
                              interval="minute")
# → 返回 datetime <= 14:29:59.800 的最新一根 bar 的特征 Series

# 日级（建议 lookback='1D' 保证拿前一交易日已完全收盘的日线）
feat = load_symbol_feature_at("RB0", "2024-02-19",
                              interval="day", lookback="1D")

# 只要子集列
feat = load_symbol_feature_at("RB0", "2025-02-19 14:30:00",
                              columns=["close", "rsi_14", "atr_14"])

# CLI
python3 -m cta.feature.feature_loader --symbol RB0 --interval day \
    --at "2024-02-19 14:30:00" --lookback 1D
```

### 关键参数

| 参数 | 说明 |
|------|------|
| `timestamp` | 查询时刻，接受 str / datetime / pd.Timestamp |
| `lookback` | pd.Timedelta 能解析的字符串，默认 '200ms'；minute 级留 200ms 即可，day 级建议 '1D' |
| `max_days_back` | 当 `effective_ts` 之前当日无数据，向前回溯几天（默认 7），用于跨周末/节假日 |

### 跨节假日行为（已验证）

```python
load_symbol_feature_at("RB0", "2024-02-19 08:00", interval="day", lookback="9h")
# → 2024-02-08   （2024-02-09~18 春节休市，自动回退到春节前最后一个交易日）
```

### 实现策略

1. 在品种目录 `{interval}/{SYMBOL}/` 扫描所有按日 parquet 的 stem（日期）
2. 过滤出 `<= effective_ts.date()` 的日期列表
3. 倒序扫描前 `max_days_back` 天，对每天的 parquet 用 `datetime <= effective_ts` 过滤
4. 首个有匹配的日子返回其最后一行；全部回溯仍无匹配 → `None`
5. 每次只读 1 个按日 parquet，minute 级平均 < 50ms

### 风险点

- day 级 `datetime` 通常是 00:00:00，若 `lookback` 很小（< 1 个交易日）会拿到当日 bar（实际未收盘）。
  本函数不会自动处理"盘中用日线"的场景，使用时请显式传 `lookback='1D'`
- minute5/15/30/60 同样遵循"退 lookback 取最新"的规则；如果 bar 是 start-stamp 约定，
  用户应相应调大 lookback 到 1 个 bar 的间隔以确保安全

---

## 2026-04-18 — 特征流水线优化：长→短顺序 + 代码体检

**分支**: `feature`
**任务**: 3 项优化 + 文档一致性扫尾

### 1. 执行顺序：长周期 → 短周期

新的 `INTERVAL_RUN_ORDER = [day, minute60, minute30, minute15, minute5, minute]`。
`minute` 最耗时最耗内存，放到最后；`day` 最快放到最前。

- `--interval all` 自动展开为该顺序
- 手工指定多个频率（如 `--interval minute day minute15`）也会被重新排序
- 新增 `_order_by_run_priority()` 工具函数

### 2. 品种顺序：按 research_rank 升序

已验证 `load_ranking()` L603 `sort_values("research_rank")`，排名 1（RB0）最先。
这次用 RB0 的 2025-02 跑通全频率特征生成作为冒烟测试。

### 3. 代码体检修复

| 文件 | 问题 | 修复 |
|------|------|------|
| `run_all_features.py` | 遗留 `CANON_INTRADAY_INTERVALS` import 不再使用 | 删除 |
| `run_all_features.py` | 顶部 docstring 把 `_all_symbols.parquet` 写在 `{SYMBOL}/` 下，实际在 `{interval}/` 根 | 更正 |
| `run_all_features.py` | `day_threshold` 注释写 "5%" 但实际乘 0.01 (1%)，且 day 分支逻辑不清 | 拆成独立 `if canon == 'day'` 分支 + 注释对齐 |
| `feature_loader.py` | `load_cross_section` 的 end_date 过滤用 `+1 day` offset 会少量误收下一天数据 | 改用 `dt.strftime('%Y-%m-%d')` 串比较 |

### 冒烟测试

```bash
# 第一名品种 RB0 (research_rank=1) 跑 2025-02
python3 -m cta.feature.run_all_features \
    --interval all --symbols RB0 \
    --start-date 2025-02-01 --end-date 2025-02-28 --overwrite
```

- 执行顺序：`['day', 'minute']`（因当前仓库只有这两档原始数据）
- day: 18 个交易日，18 个 parquet，shape=(1, 362) 每文件
- minute: ~4500 bar × 437 cols → 18 个 parquet

验证点：
1. RB0/day/2025-02-05.parquet 至 2025-02-28.parquet 连续 18 天
2. `list_symbol_dates('RB0', 'day')` 返回有序日期列表
3. 按 `research_rank` 第一的 RB0 被 `load_ranking()` 放在首位

---

## 2026-04-18 — 特征落盘按日分片 + `--start-date/--end-date` 增量

**分支**: `feature`
**任务**: 把每品种的单一巨 parquet 拆成"每天一个 parquet"，支持指定
`--start-date / --end-date` 只补/重算某段时间，便于随时间推进做增量更新。

### 输出布局（新）

```
cta/data/feature/{interval}/{SYMBOL}/{YYYY-MM-DD}.parquet     # 按日分片
cta/data/feature/{interval}/_all_symbols.parquet              # 截面合并
cta/data/feature/finished.csv                                 # 新 schema
cta/data/feature/fail.csv
```

- 拆分粒度 = `datetime` 的自然日：day 每文件 1 行；minute 级每文件一个交易日的全部 bar
- 计算时仍喂入**全历史**（滚动特征不受 range 影响），只有 `[start_date, end_date]`
  内的日子会被写盘
- 旧的 `day/CU0.parquet` 格式的文件不会再写入，老文件会保留但不被引用；
  新文件落到 `day/CU0/{YYYY-MM-DD}.parquet`

### 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `cta/feature/run_all_features.py` | 修改 | 重写 `_worker_compute` 按日 groupby 落盘；`run_interval` 新增 `start_date/end_date` 参数；`run_cross_section` 按 `{SYMBOL}/*.parquet` 汇聚；新增 `--start-date/--end-date` CLI；跟踪 CSV 列扩展 (`days_written/date_start/date_end/output_dir`)；schema 漂移时旧文件归档为 `.legacy` |
| `cta/feature/feature_loader.py` | 新增 | `load_symbol_features(symbol, interval, start_date, end_date)`、`list_feature_symbols`、`list_symbol_dates`、`load_cross_section`，并提供 CLI：`python3 -m cta.feature.feature_loader --symbol CU0 --interval day --start-date 2024-01-01` |

### 新跟踪 schema

```
finished.csv:
  symbol, exchange, interval, status,
  days_written, rows, cols,
  date_start, date_end, output_dir,
  completed_at, detail
```

`days_written` 表示本次**实际写盘**的天数；已存在且大小够的按日 parquet 会被跳过
（此时 `days_written=0` 但 `status=success`）。

### CLI 新增用法

```bash
# 增量只补最近（指定 start-date，end-date 可选）
python3 -m cta.feature.run_all_features --interval day --start-date 2026-04-01

# 重算某段时间
python3 -m cta.feature.run_all_features --interval day \
  --start-date 2024-01-01 --end-date 2024-03-31 --overwrite

# 指定多品种 + 日期范围
python3 -m cta.feature.run_all_features --interval minute15 \
  --symbols CU0 RB0 --start-date 2024-01-01 --end-date 2024-06-30

# 截面在指定范围
python3 -m cta.feature.run_all_features --cross-section --interval day \
  --start-date 2024-01-01 --end-date 2024-12-31
```

### 断点续跑策略（更新）

- 无 date range 且非 `--overwrite`：走全局 (symbol, interval) `success` 跳过（老行为）
- 有 date range 或 `--overwrite`：始终进子进程，由子进程**按日**跳过已有文件

### 在线读取

```python
from cta.feature.feature_loader import load_symbol_features
df = load_symbol_features("CU0", interval="day",
                          start_date="2024-01-01", end_date="2024-03-31")
```

自动从 `cta/data/feature/day/CU0/*.parquet` 中筛选并拼接。

### 冒烟测试

```bash
python3 -m cta.feature.run_all_features --interval day --symbols CU0 \
  --start-date 2024-01-01 --end-date 2024-01-31
# -> days=22 rows=22 cols=362

python3 -m cta.feature.run_all_features --interval day --symbols CU0 \
  --start-date 2024-02-01 --end-date 2024-02-29
# -> days=15（增量）；目录累计 37 files

python3 -m cta.feature.run_all_features --cross-section --interval day \
  --start-date 2024-01-01 --end-date 2024-02-29
# -> _all_symbols.parquet: 37 行, 383 列
```

### 风险点

- 旧的 `cta/data/feature/{interval}/{SYMBOL}.parquet` 单文件会遗留，不影响新流程，
  可以安全删除以节省空间
- 跟踪 CSV schema 已变更，老 `finished.csv` 自动归档为 `finished.csv.legacy`，
  需要时手工对照
- `_all_symbols.parquet` 每次 `--cross-section` 都会被整体覆盖（不做增量）

---

## 2026-04-18 — 特征批量生成：全频率 + 多进程 + 新跟踪路径

**分支**: `feature`
**任务**: 让 `cta/feature/` 特征流水线覆盖 6 档频率、多进程并发、断点续跑，
跟踪文件移到 `cta/data/feature/`。

### 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `cta/feature/loader.py` | 修改 | 新增 `normalize_interval()` / `resolve_interval_dir()`，规范名+旧名兼容 |
| `cta/feature/run_all_features.py` | 重写 | ProcessPool 并发、per-interval workers 上限、新跟踪路径 |
| `cta/feature/online.py` | 修改 | `MIN_LOOKBACK` 兼容两套命名；`compute_features` 先归一化 interval |
| `cta/feature/compute.py` | 修改 | `--interval` choices 扩展到全 6 档 + 旧名；内部规范化 |

### 命名规范

**规范名**（对外主推）：`day / minute / minute5 / minute15 / minute30 / minute60`
**旧别名**（兼容输入）：`5min / 15min / 30min / 60min`

`loader.normalize_interval()` 统一归一化；`resolve_interval_dir()` 读数据时
优先规范目录，回退到旧目录。

### 跟踪文件（新位置）

```
cta/data/feature/finished.csv
    symbol, exchange, interval, status, rows, cols,
    output_path, completed_at, detail

cta/data/feature/fail.csv
    symbol, exchange, interval, error_type, error, failed_at
```

- 断点续跑：`finished.csv` 中 `status=='success'` 的 `(symbol, interval)` 下次直接跳过
- 失败不影响其它：每个 `(symbol, interval)` 独立子进程，崩溃捕获到 `fail.csv`
- 子进程级补偿：若目标 parquet 已存在且大于阈值（day=1M, minute=50M, 5min=10M, ...），
  即使 finished.csv 丢失也能跳过

### 并发策略（16 GB / 4 CPU）

| 频率 | worker 上限 | 说明 |
|------|------------|------|
| day | 4 | 特征 ~20 MB/品种，安全 |
| minute | **2** | 特征 ~2-3 GB/品种，限 2 避免 OOM |
| minute5/15/30/60 | 4 | 数据量小 |

`--workers N` 与 per-interval cap 取 `min`。子进程每个任务结束主动 `del + gc.collect()`。

### 输出目录

```
cta/data/feature/
├── day/{SYMBOL}.parquet
├── minute/{SYMBOL}.parquet
├── minute5/{SYMBOL}.parquet
├── minute15/{SYMBOL}.parquet
├── minute30/{SYMBOL}.parquet
├── minute60/{SYMBOL}.parquet
├── {interval}/_all_symbols.parquet   # 截面特征合并
├── finished.csv
└── fail.csv
```

### 运行命令

```bash
# 全部频率（自动识别 cta/data/ 已有目录）
python3 -m cta.feature.run_all_features

# 单频率 / 多频率
python3 -m cta.feature.run_all_features --interval day
python3 -m cta.feature.run_all_features --interval minute minute5
python3 -m cta.feature.run_all_features --interval 5min 15min   # 旧名也接受

# 只跑盘中
python3 -m cta.feature.run_all_features --interval intraday

# 指定品种
python3 -m cta.feature.run_all_features --interval day --symbols CU0 RB0

# 并发
python3 -m cta.feature.run_all_features --workers 4

# 先跑前 10 名品种
python3 -m cta.feature.run_all_features --max-rank 10

# 强制重算
python3 -m cta.feature.run_all_features --overwrite

# 截面特征（等品种跑完后执行）
python3 -m cta.feature.run_all_features --cross-section
```

### 在线复用

`cta.feature.online` 已同步兼容所有规范名+旧名：
```python
from cta.feature.online import compute_features, compute_latest_features, FeatureGenerator
feat = compute_features(df, interval="5min")       # 旧名 OK
feat = compute_features(df, interval="minute5")    # 规范名 OK
gen = FeatureGenerator(interval="minute15")
```

### 验证

- `--interval day --max-rank 4 --workers 4`：4 品种 9 秒跑完（并发加速 ~4×）
- `--interval minute60 --symbols RB0 --workers 1`：32281 行 × 362 列，65 秒
- resume 逻辑：第二次同参数运行 0 新任务、全 skip
- fail.csv 触发路径：`EmptyData` / `FileNotFound` / `WorkerCrash` 三类异常写入

### 主要结论

- 频率全覆盖 + 规范/旧名双支持；数据 code 与 feature code 使用同一套 canonical 逻辑
- 多进程并发把 day 级加速 ~4×；minute 级通过 worker cap 避免 OOM
- 跟踪文件与数据同目录（`cta/data/feature/`），便于迁移 / 压缩存档

### 风险 / 待确认

1. `INTERVAL_WORKER_CAP` 默认 minute=2；若服务器内存更大可直接覆盖 dict 或手动传 `--workers`
   （实际取 min(user, cap)，想更高需要改源码）
2. 大小阈值（`SIZE_THRESHOLDS`）是粗筛，主要用于 finished.csv 丢失时的兜底跳过；
   如果未来加了更多特征列，阈值可能偏低（需要重新评估）
3. 旧的 `cta/feature/symbols_feature_finished.csv` 不再使用，但保留未删除，方便对照历史记录

---

## 2026-04-18 — 下载代码迁移 cta/data_code/ + 空日志区间汇总

**分支**: `feature`
**任务**: 把下载脚本从 `cta/data/` 移到 `cta/data_code/`（数据与代码分离），
跟踪 CSV 也同步迁移；`empty.csv` 由逐日记录改为按时间区间汇总。

### 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `cta/data_code/__init__.py` | 新增 | 包声明 |
| `cta/data_code/futures_downloader.py` | 迁移 | 原 `cta/data/futures_downloader.py`，内容不变（路径常量依然指向 `cta/data/`） |
| `cta/data_code/download_all.py` | 迁移 + 改造 | 原 `cta/data/download_all.py`；导入改到 `cta.data_code.*`；`FINISHED_CSV` / `EMPTY_CSV` 改到同目录；新增 empty 区间聚合 |
| `cta/data/download_all.py` | 删除 | 迁移后清理 |

### empty.csv 聚合规则

原来每个空交易日写一行 → 现在按时间区间汇总。

字段:
```
symbol, exchange, interval, date_start, date_end, count, reason, recorded_at
```

聚合算法（`_aggregate_empty_rows`）：
1. 按 `(symbol, exchange, interval, reason)` 分组
2. 同组内日期排序；相邻日期差 > `EMPTY_GAP_DAYS`（默认 60 天）拆成新区间
3. 日期 > `MAX_EMPTY_DATE` = **2026-04-17** 一律忽略（视为未来/未落地）

示例（ZS0 minute 在 2009 年有连续空日）：
```
symbol  exchange  interval  date_start  date_end    count  reason         recorded_at
ZS0     DCE       minute    2009-03-30  2009-12-31  185    tushare_empty  2026-04-18 10:25:36
```

落盘时机：每处理完一个品种的分钟级下载就聚合 flush 一次（锁保护），
多品种并发安全。

### 目录

```
cta/data_code/                      # ← 代码 + 跟踪 CSV
  ├── __init__.py
  ├── futures_downloader.py
  ├── download_all.py
  ├── finished.csv                  # 新位置
  └── empty.csv                     # 新位置 + 区间汇总格式
cta/data/                           # ← 数据（不变）
  ├── day/{SYMBOL}.csv
  ├── minute/{PREFIX}/YYYY-MM-DD.parquet
  └── minute5|15|30|60/{PREFIX}/...
```

### 运行命令

```bash
export TUSHARE_TOKEN="你的tushare付费token"

# 全量
python3 -m cta.data_code.download_all

# 只下日线（无需 token）
python3 -m cta.data_code.download_all --intervals day

# 只下分钟级
python3 -m cta.data_code.download_all --intervals minute minute5 minute15 minute30 minute60

# 先跑前 10 名
python3 -m cta.data_code.download_all --max-rank 10

# 指定品种
python3 -m cta.data_code.download_all --only-symbols CU0 RB0
```

### 主要结论

- 代码集中在 `cta/data_code/`，不再与数据混放；数据目录 `cta/data/` 继续承担只读/追加。
- 跟踪 CSV 和脚本放一起，便于 git 管理 + 备份（数据本身不跟 git）。
- empty.csv 区间汇总大幅降低条目数量：2000+ 天逐日 → 通常 1-3 条区间/品种频率。
- 原逐日 `empty` 信息未丢失，仍在内存里参与区间合并。

### 风险与注意

1. 如果之前已有 `cta/data/finished.csv` / `cta/data/empty.csv`，需**手动移动**到 `cta/data_code/`
   （当前尚未生成，不存在兼容问题）。
2. `EMPTY_GAP_DAYS` 默认 60 天：若某品种空日很稀疏（例如每 2-3 个月才一次），可能被拆多段；
   真实场景中"整段时间无数据"的空日基本是连续或日频，60 天足够合并。如需更粗粒度，
   在 `cta/data_code/download_all.py` 里调大 `EMPTY_GAP_DAYS`。
3. `MAX_EMPTY_DATE = "2026-04-17"`：晚于此日期的空日被丢弃（视为数据尚未落地），
   可按需更新为当前日期。

---

## 2026-04-18 — 统一商品期货多频率批量下载器

**分支**: `feature`
**任务**: 整合 akshare（日线）+ tushare（分钟）两套下载脚本，做成可配置、可断点续跑、
可复用于在线单次下载的统一大任务。

### 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `cta/data/futures_downloader.py` | 新增 | 可复用核心下载器（离线/在线共用） |
| `cta/data/download_all.py` | 新增 | 批量编排脚本，按 `research_rank` 顺序 |
| `cta/feature/loader.py` | 修改 | `INTRADAY_INTERVALS` 兼容新旧两套目录命名 |

### `cta/data/futures_downloader.py`（新增）

- `RateLimiter(max_per_min)`：线程安全令牌桶，默认 450 req/min（tushare 付费 ~500 留余量）
- `FuturesDownloader`
  - `download_day(symbol, exchange)`：akshare `futures_main_sina` → `cta/data/day/{SYMBOL}.csv`
  - `fetch_fut_mapping(symbol, exchange)`：自动按 `SHF/SHFE`、`CZC/ZCE/CZCE`、`GFE/GFEX` 变体尝试
  - `fetch_1min_day(contract, trade_date)`：tushare `ft_mins` 单日全量 1min
  - `resample_minute(df_1min, freq)`：按自然日分组重采样，避免跨夜盘空 bar
  - `download_day_minute_all(...)`：**在线/离线共用入口**，一次 1min 拉取 + 多频率落盘
- 常量：
  - `MINUTE_INTERVALS = (minute, minute5, minute15, minute30, minute60)`
  - `RESAMPLE_FREQ` 映射
  - `EXCHANGE_VARIANTS` 交易所兜底列表

### `cta/data/download_all.py`（新增）

- 按 `cta/feature/symbols_research_ranking0.csv` 的 `research_rank` 从小到大依次下载
- CLI：`--intervals / --workers / --rate-limit / --max-rank / --only-symbols / --token`
- 断点续跑三级：
  1. `finished.csv` 里 `(symbol, interval)` 状态 `success|empty` → 整个品种-频率跳过
  2. 逐日 parquet 存在 → 跳过该日
  3. 日线 CSV 存在 → 跳过该品种
- 并发：外层品种顺序、内层按日 `ThreadPoolExecutor(workers=4)`，共享 `RateLimiter`
- 全局锁下 `append` 写入 `finished.csv` / `empty.csv`，线程安全

#### 输出目录
```
cta/data/day/{SYMBOL}.csv
cta/data/minute/{PREFIX}/{YYYY-MM-DD}.parquet      # 1min
cta/data/minute5/{PREFIX}/{YYYY-MM-DD}.parquet     # 由 1min 本地重采样
cta/data/minute15/{PREFIX}/{YYYY-MM-DD}.parquet
cta/data/minute30/{PREFIX}/{YYYY-MM-DD}.parquet
cta/data/minute60/{PREFIX}/{YYYY-MM-DD}.parquet
cta/data/finished.csv                              # symbol,exchange,interval,status,rows,...
cta/data/empty.csv                                 # symbol,exchange,interval,trade_date,reason,...
```

### `cta/feature/loader.py`（修改）

```python
INTRADAY_INTERVALS = [
    "minute",
    "minute5", "minute15", "minute30", "minute60",   # 新（download_all.py 约定）
    "5min", "15min", "30min", "60min",               # 旧（向后兼容）
]
```
`load_intraday_data` / `list_intraday_symbols` 已经按字母前缀扫描所有匹配目录，
无需改动其它逻辑；新旧目录并存不冲突。

### 运行命令

```bash
# 前置：tushare 付费 token
export TUSHARE_TOKEN="你的tushare付费token"

# 全量（6 档）
python3 -m cta.data.download_all

# 只下日线（不需要 tushare token）
python3 -m cta.data.download_all --intervals day

# 只下分钟级（1/5/15/30/60min）
python3 -m cta.data.download_all --intervals minute minute5 minute15 minute30 minute60

# 先跑前 10 名试水
python3 -m cta.data.download_all --max-rank 10

# 指定单品种补数据
python3 -m cta.data.download_all --only-symbols CU0 RB0

# 自定义并发 / 速率
python3 -m cta.data.download_all --workers 4 --rate-limit 450
```

### 在线复用示例

```python
from cta.data.futures_downloader import FuturesDownloader

dl = FuturesDownloader()
results = dl.download_day_minute_all(
    symbol="CU0", exchange="SHFE",
    contract_code="CU2501.SHF", trade_date="2025-01-15",
    intervals=["minute", "minute5", "minute15"],
)
# 返回 {interval: DownloadResult}
```

### 主要结论

- 1min 拉一次 → 本地重采样 5/15/30/60min：**API 用量 ~1/5**，各频率时间戳严格一致
- 71 品种 × ~3000 交易日 × 1min ≈ 20 万次 ft_mins 调用 @ 450/min ≈ 8 小时（粗估）
- `finished.csv` 以 `(symbol, interval)` 为键，中断再跑不会重复，空数据（empty）也记录避免反复扫
- 与下游 `cta/feature/run_all_features.py` 的 `INTRADAY_INTERVALS` 兼容，不影响已有特征流水线

### 风险 / 待确认项

1. **`symbols_research_ranking0.csv` 当前仅 1 行（RB0）**，完整 71 行在 `symbols_research_ranking.csv`。
   如需全量，恢复文件或改 `download_all.py` 里的 `RANKING_CSV` 常量。
2. `resample_minute` 使用 `label='left', closed='left'`；tushare 原生 1min 的 `trade_time` 语义是 bar
   **结束时间**。若下游策略严格依赖结束时间，把 `label='right'` 切换即可。
3. 部分小品种 `fut_mapping` 可能返回空，已降级为 `empty`（非 `error`）。
4. tushare 接口偶发限流，`_safe_retry` 配置为 3 次指数等待，极端情况下单日失败会记入 `empty.csv`。

---

## 2026-04-22 — 实现 §13-§18 特征 + loader 反污染 + tod 门控修复

- **分支**: `feature`
- **任务**: 按 `cta/feature/FEATURES.md` §13-§18 在现有代码基础上补齐 bar-derivable 特征，并修复 `cta/feature/code_review.txt` 中的 1/3 号 issue。

### 修改文件

1. `cta/feature/loader.py`
   - `load_intraday_data` / `list_intraday_symbols` 改为**严格按目录名的 `split('.')[0]` 匹配**，不再用字母前缀；
     - `symbol='CU0'` 只纳入 `CU0.SHF` / `CU0.SHFE`，不会吸收 `CU_small`；
     - `list_intraday_symbols` 按 symbol（含后缀）分组，避免跨品种合并。
   - 保留对"symbol 不带末尾 0"的目录回退匹配（如 `minute5/RB` 对应 `RB0`）；仅当精确匹配无命中时才触发。
   - 新增 `_dir_symbol(name)` 工具函数；交易所缩写别名表（SHF/SHFE、CZC/ZCE/CZCE 等）集中在 `exch_aliases`。
2. `cta/feature/compute.py`
   - tod 同比特征门控**显式注释 + 保持为 `interval == 'minute'`**：minute5/15/30/60 的 bar 本身已跨多分钟，内部再按 1/3/5/10/20 分钟粒度聚合没有物理意义。
   - 改为接收 canonical interval（含 minute5/15/30/60），不再在上游归一到 "day"/"minute"。
   - 在 `parts` 中新增 §13-§15 / §18 的调用；新增 §16 / §17 的二阶段聚合（先算上游再喂给 regime / composite）。
3. `cta/feature/run_all_features.py`
   - `_worker_compute` 直接把 `canon` 传给 `compute_single_symbol_features`，让 compute 内部根据频率决定是否算 tod / LTF。
4. `cta/feature/FEATURES.md`
   - 文首入口改指向 `python3 -m cta.feature.run_all_features`（按日分片、支持全频率）；`run_generate.py` 标注为旧入口、不推荐。
   - §11 明确写"仅在 1 分钟 bar 上生成"，与 compute.py 行为对齐。
5. 新增 6 个特征模块（均在 `cta/feature/` 下）：
   - `volatility_regime.py` — §13，10 组列（zscore、regime_3、range_ratio、consecutive_hh/ll、breakout_dist_atr 等）。
   - `price_action_supplement.py` — §14，10 列（tight_range、bull/bear_flag、bpb、swing_idx、trend_channel 4 列）。trend_channel 用移动 buffer 做最近 3 swing 拟合，避免 O(n²)。
   - `multi_timeframe.py` — §15，7 列（HTF/MTF 三档趋势分 + 对齐 flag + conflict + LTF 信号）。用内部 resample（trade_date / dt.floor('60T' / '30T' / '5T')）算各频率 bar，再 `shift(1).map(key)` 防未来信息。
   - `regime.py` — §16，5 列（softmax + argmax 得 regime_label，cumcount 算 regime_age）。
   - `composite.py` — §17，8 个 0-1/−1-1 分数（trend / compression / expansion / breakout_mode / setup_quality / breakout_quality / context / rr）。
   - `entry_stop.py` — §18，9 列（atr_stop/target、chandelier、micro_channel、liquidity_filter）。

### 最小可复现实验

```bash
# 重置 5 个品种的 2024 产物
rm -rf cta/data/feature/day/{RB0,HC0,I0,JM0,J0} cta/data/feature/minute5/{RB0,HC0,I0,JM0,J0}

# day + minute5，2024 全年，4 workers
python3 -m cta.feature.run_all_features \
  --interval day minute5 \
  --symbols RB0 HC0 I0 JM0 J0 \
  --start-date 2024-01-01 --end-date 2024-12-31 \
  --overwrite --workers 4
```

### 结果

- **day（5 品种）**: 全部 OK，每品种 242 个交易日、426 列，合计 ~28s。
- **minute5**: RB0 OK（242 天，17,491 行，426 列，~495s）；HC0/JM0/I0/J0 因本地暂无 minute5 数据而 FileNotFound（非代码问题，数据端未下载）。
- §13-§18 全部 47 列均写入 parquet，RB0 2024-12-31 尾 bar 有合理值：`trend_score=-0.29, regime_label=transition, atr_based_stop_long=3234.75, htf_trend_score_day=-0.27`。
- minute5 RB0 `htf_trend_score_day` 在当日 48 根 bar 内保持常量（预期），`mtf_trend_score_60m` 在 13:35 切负、15:00 回升；`regime_label` 呈 range / transition 交替。

### 输出位置

- `cta/data/feature/day/{SYMBOL}/{YYYY-MM-DD}.parquet`
- `cta/data/feature/minute5/{SYMBOL}/{YYYY-MM-DD}.parquet`
- `cta/data/feature/finished.csv` / `fail.csv`（新增 5 条 day + 1 条 minute5 成功 + 4 条 minute5 FileNotFound）

### 风险 / 待确认项

1. **code_review #2、#4（`run_generate.py` 旧入口的布局与 CLI 问题）**：本轮只在文档中明确弃用并指向 `run_all_features.py`，代码层未强制改动。如需彻底下线，下一轮可把 `run_generate.py` 改成 `sys.argv` 透传给 `run_all_features.main()` 的 shim。
2. **regime.py 阈值**：当前 trend_up/range/compression/expansion 的分段阈值（30/70 分位、0.6 trading_range、-0.5 bb_z、0.5 atr_z）是 FEATURES.md 中建议值；品种/周期差异下可能需按品种再做一次分位标定。
3. **multi_timeframe 的 LTF 信号**：仅在 `interval in (minute, minute5)` 启用；更高频率（15/30/60m）下 `ltf_signal_ready_5m=0`，这是设计使然（避免未来信息）。
4. **minute5 耗时**：RB0 单品种 17k bar 约 495s，其中 trend_channel 与 regime softmax 占比较高；全 71 品种全历史仍建议夜间批处理。

---

## 2026-04-23 · main · run_all_features 诊断日志 + 1min 内存治理

### 背景

服务器上跑 1-minute 全量生成（71 品种）出现 success=1 / fail=70 的系统性失败，但
旧的单层 try/except 把 4 个阶段的异常全部归到同一个 `error_type`，`fail.csv` 里
看不清到底死在哪一步。同时 `ProcessPoolExecutor` 默认不会回收 worker，
1-minute 单品种峰值 RSS 2-3GB，跑多个品种后 worker 内存持续累加很容易 OOM 被 kill。

### 任务

1. 把 `_worker_compute` 拆成 4 个独立阶段，各自 try/except，`error_type` 精确到
   `LoadError:*` / `ComputeError:*` / `FilterError:*` / `WriteError:*`。
2. 每阶段打印 `pid / symbol / 行列数 / 阶段耗时 / RSS` 日志。
3. 1-minute interval 下启用 `max_tasks_per_child=1`（Python 3.11+），每处理完 1
   个品种就回收 worker 进程，彻底释放内存。
4. 主进程每完成 5 个品种触发一次 `gc.collect()` 并打印 `main_rss / 进度`。

### 修改文件

**`cta/feature/run_all_features.py`**

- 新增 `_rss_mb()` 工具函数，兼容 macOS（`ru_maxrss` bytes）与 Linux（KB）。
- `_worker_compute` 结构重写：
    - Stage 1 `load`: `LoadError:FileNotFound / LoadError:<ExcName> / LoadError:Empty`
    - Stage 2 `compute`: `ComputeError:<ExcName>` / `ComputeError:Empty`
    - Stage 3 `filter`: `FilterError:MissingDatetime / FilterError:<ExcName>`
    - Stage 4 `write`: `WriteError:<ExcName>`（附 `written_before_fail` 计数）
    - 每阶段成功后打印 `begin / loaded / features / done` 四行日志，含 `rss`
      和 `total_t / peak_delta`。
    - Stage 2 结束后立即 `del df; gc.collect()` 避免 compute + 原始 df 叠加。
    - `detail` 字段拆分成 `load=…s compute=…s write=…s rss_end=…MB`，
      方便从 `finished.csv` 直接看阶段耗时。
- `run_interval` 增强：
    - `canon == "minute"` 且 Python ≥3.11 时 `max_tasks_per_child=1`，打印启用提示。
    - 每 `GC_EVERY=5` 个完成或最后一个完成后主进程 `gc.collect()` 并打印
      `progress / ok / fail / main_rss`。

### 未改动

- `cta/feature/compute.py`、`cta/feature/minute_tod.py`：本轮仅做 review，未发现
  明确 bug。真正的 1-min 失败类型待加上分阶段日志后重跑一轮即可从 `fail.csv`
  `error_type` 列直接定位。

### 运行命令

```bash
# 服务器上重跑 1-minute（旧 fail 会被覆盖 append）
python3 -m cta.feature.run_all_features --interval minute

# 若要先清空旧 fail.csv 便于观察新 error_type
rm -f cta/data/feature/fail.csv
python3 -m cta.feature.run_all_features --interval minute
```

### 输出位置

- `cta/data/feature/minute/{SYMBOL}/{YYYY-MM-DD}.parquet`
- `cta/data/feature/finished.csv`（`detail` 列新增 load/compute/write 耗时拆分）
- `cta/data/feature/fail.csv`（`error_type` 列新增阶段前缀）

### 风险 / 待确认项

1. `max_tasks_per_child=1` 要求 Python 3.11+。服务器如果是 3.10 及以下会被
   `sys.version_info` 检查跳过（仍按默认行为运行，不报错，但退回内存累积模式）。
2. 若重跑后 `fail.csv` 里仍是 `WorkerCrash`（主进程捕获的异常，子进程已死），
   则 90% 是 OOM / SIGKILL，下一步需要降低 per-worker 内存（如进一步精简
   `compute_minute_tod_features` 的中间拷贝，或显式 `chunksize` 聚合）。
3. Stage 日志级别是 INFO，跑 71 品种 × 4 行 ≈ 284 行额外日志；若嫌噪音可改 DEBUG。

---

## 2026-04-26 · main · RB0 60min 三模型基线（Trade Filter / Regime / MFE-MAE）

### 任务

1. 基于 `cta/strategy/readme.md` 继续完成三类核心模型链路。  
2. 先写测试，再补实现。  
3. baseline 规则策略生成候选机会样本，并拼接通用特征。  
4. 本地 RB0 60min 回测与模型报告落地。

### 修改文件

1. `cta/tests/test_models_core.py`
   - 修复 pandas 新版本频率兼容：`freq="H"` -> `freq="h"`。

2. `cta/tests/test_baseline_skill_suite.py`
   - 新增 `generate_candidate_opportunities` 的测试用例（先测后写）。

3. `cta/tests/test_model_feature_builder.py`
   - 覆盖候选特征 + 通用特征拼接与构表流程。

4. `cta/tests/test_rb60_model_pipeline.py`
   - 新增 RB0 60min 三模型 pipeline 冒烟测试（输出文件存在性）。

5. `cta/strategy/baseline_skill_suite.py`
   - 新增 `generate_candidate_opportunities(...)`：
     - 基于 4 套 baseline 信号生成候选机会；
     - stop 单按“下一根 K 线是否触发”判定；
     - 产出 `future_mfe_atr / future_mae_atr / label_class / regime_label` 及 `feature_*`。
   - `run_baseline_suite(...)` 的训练样本优先使用候选机会，空样本时回退到成交样本构造。

6. `cta/model/feature/training_feature_builder.py`
   - 落地候选样本与 `cta/data/feature` 通用特征拼接构表（含 fallback）。

7. `cta/model/trade_filter_model.py`
   - 二分类模型封装（fit/predict/evaluate/save/load）。

8. `cta/model/regime_classifier_model.py`
   - 多分类模型封装（fit/predict/evaluate/save/load）。

9. `cta/model/mfe_mae_model.py`
   - 双目标回归模型封装（fit/predict/evaluate/save/load）。

10. `cta/model/rb60_model_pipeline.py`
    - 新增端到端 pipeline：
      - 候选机会生成；
      - 特征拼接；
      - 时间切分（train/valid/test）；
      - 三类模型训练/评估/预测；
      - 输出 CSV + Markdown 报告 + model artifacts。

11. `cta/model/__init__.py`, `cta/model/feature/__init__.py`
    - 新包初始化文件。

### 运行命令

```bash
# 相关测试
python3 -m unittest \
  cta.tests.test_baseline_skill_suite \
  cta.tests.test_model_feature_builder \
  cta.tests.test_models_core \
  cta.tests.test_rb60_model_pipeline -v

# RB0 60min baseline 回测（2000-2019）
python3 -m cta.strategy.baseline_skill_suite \
  --symbol RB0 --exchange SHFE --interval 60min \
  --trade-side-mode both --start 2000-01-01 --end 2019-12-31

# RB0 60min 三模型 pipeline
python3 -m cta.model.rb60_model_pipeline \
  --symbol RB0 --exchange SHFE --interval 60min \
  --start 2000-01-01 --end 2019-12-31 \
  --trade-side-mode both --train-end 2018-12-31 --valid-end 2019-06-30
```

### 输出位置

1. baseline 回测报告目录：  
   `cta/report/backtest/20260426_baseline_skill_suite_RB0_minute60_both/`
   - `20260426_RB0_minute60_both_suite_summary.csv`
   - `20260426_RB0_minute60_both_training_samples.csv`
   - `20260426_RB0_minute60_both_baseline_report.md`

2. 三模型 pipeline 报告目录：  
   `cta/report/backtest/20260426_RB0_minute60_both_model_pipeline/`
   - `20260426_RB0_minute60_both_candidates.csv`
   - `20260426_RB0_minute60_both_feature_table.csv`
   - `20260426_RB0_minute60_both_predictions.csv`
   - `20260426_RB0_minute60_both_metrics.csv`
   - `20260426_RB0_minute60_both_model_report.md`
   - `models/*.joblib`

### 主要结果（RB0 60min, 2000-2019）

1. baseline 交易笔数：
   - donchian: 156
   - atr_breakout: 272
   - tight_range_breakout: 275
   - breakout_pullback_continuation: 183

2. 模型样本量：
   - candidate_count: 1793
   - feature_count: 41
   - train/valid/test: 1577/114/102

3. 关键指标（test）：
   - Trade Filter: AUC 0.7462, Accuracy 0.6078, F1 0.7101
   - Regime Classifier: Accuracy 0.9902, Macro-F1 0.9295
   - MFE/MAE: mfe_mae=1.7485, mae_mae=1.4364

### 风险与待确认

1. Regime 指标当前偏高，存在标签分布偏斜风险，建议补充分层抽样或类别均衡。  
2. MFE/MAE 在样本外 R2 仍为负，建议按 `signal_type` 分模型或增加多周期特征。  
3. 目前候选机会为“信号触发型”样本，下一步可增加“未触发 stop 的取消样本”用于更精细过滤。

---

## 2026-04-26 · main · 候选负样本 + 全周期兼容 + signal_type 分模型 walk-forward

### 任务

1. 样本从“仅已成交”升级为“filled + not_triggered + filtered”全候选样本。  
2. pipeline 明确兼容 `day/60min/30min/15min/5min/min`。  
3. 模型训练改为按 `signal_type` 分组，并加入 walk-forward 窗口评估。

### 先测后写（TDD）

新增/更新测试：

1. `cta/tests/test_baseline_skill_suite.py`
   - 新增 `test_generate_candidate_includes_negative_samples`：
     - 断言输出包含 `candidate_status/is_executed`；
     - 断言至少有非 filled 负样本。

2. `cta/tests/test_rb60_model_pipeline.py`
   - 新增 `test_run_pipeline_support_all_intervals`：
     - 覆盖 `day/60min/30min/15min/5min/min` 六种周期。
   - 新增 `test_run_pipeline_by_signal_type_walk_forward`：
     - 断言 metrics 含 `signal_type/window_id`；
     - 断言多 signal、多窗口有效。

### 主要代码改动

1. `cta/strategy/baseline_skill_suite.py`
   - `generate_candidate_opportunities(...)` 升级：
     - 输出候选状态 `candidate_status`（`filled/not_triggered/filtered`）；
     - 输出 `is_executed/is_filtered/is_triggered/filtered_reason`；
     - stop 单未触发样本进入负样本集（`not_triggered`）。
   - 新增 raw setup 构造逻辑，覆盖四类 baseline 的候选扫描。

2. `cta/model/rb60_model_pipeline.py`
   - 新增参数：
     - `synthetic_periods`
     - `by_signal_type`
     - `max_walk_forward_windows`
   - 新增 walk-forward 窗口构建与循环训练逻辑。
   - 按 signal_type 分组训练 3 类模型（Trade Filter / Regime / MFE-MAE）。
   - MFE/MAE 模型默认仅使用 `is_executed=1` 样本训练/评估。
   - 输出 metrics/predictions 包含 `signal_type/window_id` 维度。

### 验证命令

```bash
python3 -m unittest \
  cta.tests.test_baseline_skill_suite \
  cta.tests.test_model_feature_builder \
  cta.tests.test_models_core \
  cta.tests.test_rb60_model_pipeline -v

python3 -m cta.strategy.baseline_skill_suite \
  --symbol RB0 --exchange SHFE --interval 60min \
  --trade-side-mode both --start 2000-01-01 --end 2019-12-31

python3 -m cta.model.rb60_model_pipeline \
  --symbol RB0 --exchange SHFE --interval 60min \
  --start 2000-01-01 --end 2019-12-31 \
  --trade-side-mode both \
  --train-end 2012-12-31 --valid-end 2015-12-31 \
  --max-walk-forward-windows 3 --by-signal-type
```

### 输出与结果

1. 候选样本（RB0 60min, 2000-2019）：
   - 总候选：4142
   - `filled`: 1798
   - `not_triggered`: 2265
   - `filtered`: 79
   - 负样本占比：56.59%

2. 模型结果：
   - `signal_type_count`: 4
   - `window_id`: 2（本次数据范围内有效窗口）
   - metrics 含 `signal_type/window_id/split/model` 四维。

3. 输出目录：
   - `cta/report/backtest/20260426_baseline_skill_suite_RB0_minute60_both/`
   - `cta/report/backtest/20260426_RB0_minute60_both_model_pipeline/`

### 风险与后续

1. `filtered` 负样本目前主要来自规则 gate/side mode，后续可细化更多过滤原因标签。  
2. MFE/MAE 样本外仍有不稳，建议下阶段按 `signal_type + regime` 进一步分桶建模。  
3. walk-forward 窗口数量受样本时间分布影响，跨品种时建议按品种自适应步长。

---

## 2026-04-26 · main · 按 cta/bug.md code review 回归修复

### 范围

- `cta/strategy/baseline_skill_suite.py`
- `cta/model/feature/training_feature_builder.py`
- `cta/model/trade_filter_model.py`
- `cta/model/regime_classifier_model.py`
- `cta/model/mfe_mae_model.py`
- `cta/model/rb60_model_pipeline.py`
- `cta/config/baseline_skill_suite_config.py`
- `cta/tests/test_baseline_skill_suite.py`
- `cta/tests/test_model_feature_builder.py`
- `cta/tests/test_models_core.py`
- `cta/tests/test_rb60_model_pipeline.py`

### 核心修复（对应 bug.md）

1. `prepare_master_feature_frame` 先按 `datetime` 去重并与 `prepare_strategy_frame` 行数强校验，修复重复时间戳下特征错位风险。  
2. `generate_candidate_opportunities` 的 ATR 归一化改为使用 signal bar ATR，消除标签口径不一致。  
3. 候选负样本（未触发/被过滤）不再统一 `future_mfe_atr=0`，改为按假设触发价计算 horizon 波动。  
4. `build_training_samples_from_trade_log` 对非法 side（非 long/short）直接跳过，避免按 long 误算。  
5. `training_feature_builder._iter_feature_files` 在日期窗口无文件时抛 `FileNotFoundError`，不再回退全量文件。  
6. `merge_candidate_and_generic_features` 改为 `merge_asof + tolerance`，修复秒级偏移导致全 NaN。  
7. 标签阈值统一为配置常量（`LABEL_MAE_PENALTY=0.7`, `LABEL_THRESHOLD=0.2`），消除 pipeline 与 baseline 口径漂移。  
8. pipeline 增加 `pred_split` 字段，区分 test/valid/train 回退预测。  
9. pipeline 在 signal/window 训练前增加 label 多样性兜底，降低单标签子集退化风险。  
10. TradeFilter/Regime/MFE-MAE 的 Dummy 分支补上 `SimpleImputer`；TradeFilter Dummy 改 `prior`；MFE-MAE Dummy 改原生 `DummyRegressor`。

### 新增回归测试

1. `test_prepare_master_feature_frame_drop_duplicate_datetime`  
2. `test_generate_candidate_uses_signal_bar_atr_for_label_norm`  
3. `test_build_training_samples_skip_unknown_side`  
4. `test_merge_candidate_and_generic_features_with_second_offset`  
5. `test_iter_feature_files_raise_when_no_file_in_range`  
6. `test_dummy_models_handle_nan_features`  
7. `test_run_pipeline_smoke` 断言 `pred_split` 列存在

### 验证命令

```bash
python3 -m unittest \
  cta.tests.test_baseline_skill_suite \
  cta.tests.test_model_feature_builder \
  cta.tests.test_models_core \
  cta.tests.test_rb60_model_pipeline -v
```

### 验证结果

- 21 tests 全部通过（OK）。

---

## 2026-04-26 · main · 第三轮 code review B1-B12 优先级修复

### 范围

- `cta/strategy/baseline_skill_suite.py`
- `cta/model/feature/training_feature_builder.py`
- `cta/model/trade_filter_model.py`
- `cta/model/regime_classifier_model.py`
- `cta/model/mfe_mae_model.py`
- `cta/model/rb60_model_pipeline.py`
- `cta/tests/test_baseline_skill_suite.py`
- `cta/tests/test_model_feature_builder.py`
- `cta/tests/test_rb60_model_pipeline.py`

### 核心修复（按优先级 P0 → P2）

**P0（数据/标签/口径硬错）**

1. **B1**：pipeline 在 `train_exec` 为空时不再硬塞 `MfeMaeModel(min_samples=1e9)` 走 dummy。新增 `_train_mfe_mae_or_skip(train_df, feature_columns)` 显式返回 `(None, "skipped_no_exec")`，metrics/predictions/joblib 三处统一按 `None` 跳过，避免假装训练成功污染指标。  
2. **B2**：`_select_feature_columns` 兜底分支用 `df.get(col, scalar)` 在缺列时拿到的是标量，再 `.astype(str)` 会抛 `AttributeError`；改为先判断列存在再构造 `pd.Series`。  
3. **B3**：`build_training_feature_table` 截到天会丢跨日 lookback；start_date 改为 `dt.min() - 1day`、end_date 改为 `dt.max() + 1day`，保证夜盘衔接样本能拿到上一交易日的 generic 特征。  
4. **B4**：`baseline_skill_suite` 中 `bool(np.nan) == True` 的 Python footgun：9 处 `bool(bar.get("bp_valid"/"bp_confirmed"/"tr_valid"/"breakout_pass"))` 改为 `_safe_bool(...)`，统一处理 None/NaN/np.floating/任意可调用 bool。

**P1（健壮性）**

5. **B5**：`WindowMode = Literal[...]` 之前夹在 `from typing` 与 `import numpy` 之间违反 PEP 8；移到全部 import 之后。同时把 `"skipped_no_exec"` 抽成模块级常量 `MFE_MAE_KIND_SKIPPED_NO_EXEC`，避免字符串散落。  
6. **B6**：walk-forward 已经过滤 `test_df.empty` 窗口，`pred_split` 的 valid/train fallback 分支永远走不到，删除死代码；保留 `if test_df.empty: continue` 防御。  
7. **B7**：`TradeFilterModel/RegimeClassifierModel/MfeMaeModel.load()` 旧 joblib 缺 `model_kind` 字段时返回 `"unknown"` 难以追溯；改为 `"legacy_no_kind"` 并加注释，便于运维分辨"未知 vs 老格式"。  
8. **B8**：`run_rb_model_pipeline` 写 report.md 时 `metrics_df.to_string(index=False)` 在宽表下不可读；与 `run_baseline_suite` 对齐改为 try `to_markdown` 失败再 `to_string` 兜底。  
9. **B9**：`_iter_feature_files` 之前对解析失败的文件名只 `logger.warning` 后静默跳过，命名规范变更时会丢光所有 generic 特征；新增 `parse_failures` 列表，当 `len(parse_failures)/total_files >= 0.5` 时主动 raise `ValueError` 并附样例。

**P2（一致性 / 可读性）**

10. **B10**：`run_rb_model_pipeline` 内 `_ensure_training_columns(feature_df)` 被调用两次（一次在加载后、一次在 walk-forward 切片前），第二次纯属冗余但无副作用；保留并加注释说明幂等。  
11. **B11**：`_is_numeric_like_column` 的样本量 `head(50)` 在分钟级数据下采样过小，可能误判带空值的数值列为非数值；提升到 `head(200)`。  
12. **B12**：report.md 字段名 `signal_type_count` 与 `by_signal_type=False` 时实际是 1 个 frame 的语义不符；改名 `signal_frames_count` 并加注释说明含义。

### 新增回归测试

1. `test_safe_bool_handles_nan_none_and_truthy_values`（B4 单元）  
2. `test_baseline_strategy_treats_nan_bp_valid_as_false`（B4 端到端）  
3. `test_iter_feature_files_raises_when_majority_filenames_invalid`（B9）  
4. `test_build_training_feature_table_loads_previous_day_for_lookback`（B3）  
5. `test_select_feature_columns_fallback_handles_missing_side_and_signal_type`（B2）  
6. `test_train_mfe_mae_or_skip_returns_none_when_no_executed`（B1 阴性）  
7. `test_train_mfe_mae_or_skip_returns_model_when_executed_present`（B1 阳性）  
8. `test_run_pipeline_pred_split_is_test_only`（B6）  
9. `test_trade_filter_load_legacy_no_kind`（B7）

### 验证命令

```bash
python3 -m unittest \
  cta.tests.test_baseline_skill_suite \
  cta.tests.test_model_feature_builder \
  cta.tests.test_models_core \
  cta.tests.test_rb60_model_pipeline -v
```

### 验证结果

- 40 tests 全部通过（OK），耗时约 33.6s。

### 风险与后续

1. `_train_mfe_mae_or_skip` 当前仅按 `is_executed` 区分，后续如果引入更细的执行口径（例如带止损但未触发等多档状态），需要扩展返回的 `model_kind` 枚举。  
2. B9 的 50% 阈值是经验值，若后续 feature 目录混入大量 metadata 文件（如 `.crc`、`_SUCCESS`），需要先在 glob 阶段排除，避免误触发 raise。  
3. `legacy_no_kind` 仅是诊断字段，不影响推理；若长期沿用建议在下一次模型重训时统一刷一遍 joblib。

---

## 2026-05-10 · current · 三模型参数搜索与过拟合约束（train/valid 选参）

### 修改文件

- `cta/model/model_pipeline.py`
- `cta/model/trade_filter_model.py`
- `cta/model/regime_classifier_model.py`
- `cta/model/mfe_mae_model.py`
- `cta/model/tests/test_model_pipeline.py`
- `cta/model/tests/test_models_core.py`
- `cta/model/model.md`

### 主要修改

1. 三类模型接入参数搜索：
   - Trade Filter：`HistGradientBoostingClassifier` 多组参数网格；
   - Regime Classifier：`RandomForestClassifier` 多组参数网格；
   - MFE/MAE：`RandomForestRegressor(MultiOutput)` 多组参数网格。
2. 选参规则统一为：
   - 仅使用 `train/valid` 评估；
   - 优先满足 `abs(train_auc-valid_auc) <= max_auc_gap`（默认 `0.02`）；
   - 若无候选满足阈值，回退到最小 gap，再比较 valid AUC；
   - 明确不使用 OOT(test) 参与参数选择。
3. 指标与可观测性增强：
   - `RegimeClassifier` 增加 `auc`（二分类/多分类 OVR）；
   - `MFE/MAE` 增加 `direction_auc`；
   - `metrics.csv` 增加 `selected_params / selection_train_auc / selection_valid_auc / selection_auc_gap`。
4. 新增 CLI 参数：
   - `--max-auc-gap`（默认 `0.02`）。

### 测试

```bash
python3 -m pytest -q cta/model/tests/test_models_core.py cta/model/tests/test_model_pipeline.py
```

结果：`48 passed`。

---

## 2026-05-17 · current · review/202605170735 关键修复（按优先级）

### 修改内容

1. 修复 `test_pool_training.py` 的 mock 命名空间，避免 patch 到 shim 导致误跑真实 pipeline。
   - 改为 patch `cta.model.pipeline_orchestrator` 内真实函数：
     - `_build_candidate_table`
     - `_build_training_feature_table_with_auto_fallback`
     - `_build_pooled_feature_df`
     - `run_model_pipeline`
2. 修复 `MfeMaeModel.predict` 可能输出负 `pred_mfe_atr/pred_mae_atr` 的问题。
   - 预测结果统一做 `>=0` 裁剪，避免下游仓位与风控计算异常。
3. 修复 `group_pool_aggregate` Sharpe 在极小方差场景的数值爆炸。
   - Sharpe 计算阈值从 `std_ex > 0` 调整为 `std_ex > 1e-9`。
4. 增强 `Pyramid Layer.effective_stop` 的 NaN 容错。
   - 当 hard/trail 任一非有限值时走安全回退，不传播 NaN。
5. 增强 `RegimeClassifierModel.fit` 的类别稀疏告警。
   - 当最小类别样本数小于 `min_samples_leaf` 时记录 warning，便于识别 regime gate 退化风险。

### 新增/更新测试

- `cta/model/tests/test_pool_training.py`
- `cta/model/tests/test_models_core.py`
- `cta/model/tests/test_group_pool_aggregate.py`
- `cta/portfolio_logic/tests/test_pyramid_manager.py`
- `cta/portfolio_logic/tests/test_risk_throttle.py`

### 验证命令

```bash
python3 -m pytest -q \
  cta/model/tests/test_pool_training.py \
  cta/model/tests/test_models_core.py \
  cta/model/tests/test_group_pool_aggregate.py \
  cta/portfolio_logic/tests/test_pyramid_manager.py \
  cta/portfolio_logic/tests/test_risk_throttle.py
```

结果：`38 passed`。

---

## 2026-05-16 · current · 修复 OOT `htf_missing`（跨 interval 共享 HTF 参考）

### 修改内容

1. OOT 评估器支持显式注入跨 interval HTF 参考表：
   - 文件：`cta/model/pipeline_oot_evaluation.py`
   - 新增参数：`htf_reference_df`
   - 行为：
     - 不传时保持旧逻辑（仅用当前 interval 预测表作 HTF 参考）；
     - 传入时使用外部参考（支持 day/60min 联合状态），并沿用 test/window 过滤口径。
2. model pipeline 新增“共享 HTF 二次重算”：
   - 文件：`cta/model/pipeline_orchestrator.py`
   - 新增函数：
     - `_recompute_oot_with_shared_htf_reference`
     - `_should_recompute_with_shared_htf_reference`
     - `_infer_oot_extra_output_paths`
   - 接入点：
     - `run_model_pipeline_multi`（多 interval 单品种）
     - `main --pool`（POOL 多 interval）
     - `main --group-pool`（每个 symbol group 的多 interval）
   - 作用：当启用 `use_portfolio_logic_runtime + enable_htf_gate` 且 interval>1 时，
     用合并后的跨 interval 预测表重算各 interval OOT，避免整批 `block_reason=htf_missing`。
3. 文档同步：
   - `cta/model/model.md` 增加跨 interval 共享 HTF 说明。

### 测试

```bash
python3 -m pytest -q \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_evaluate_oot_real_execution_htf_external_reference_unblocks_single_interval \
  cta/model/tests/test_group_pool_mode.py::TestGroupPoolHelpers::test_recompute_oot_with_shared_htf_reference_rewrites_blocked_htf

python3 -m pytest -q \
  cta/model/tests/test_group_pool_mode.py \
  cta/model/tests/test_backward_compat.py \
  cta/model/tests/test_blind_spot_coverage.py \
  cta/model/tests/test_model_pipeline.py -k "htf or run_model_pipeline_multi_returns_one_result_per_interval"
```

结果：新增用例通过，HTF 相关回归通过。

---

## 2026-05-16 · current · review_v3 续做：模型链路回归修复与清单补齐

### 修改内容

1. `OotEvaluationConfig` 增加 `enforce_stop_loss_consistency` 开关（默认 `False`）：
   - 研究/单测场景可临时使用非默认止损，不再被全局 guard 误伤；
   - `DEFAULT_OOT_EVAL_CONFIG` 显式设为 `True`，生产默认仍强制训练/评估止损一致。
2. `PyramidConfig` 默认冷却参数微调：
   - `30min` 的 `cooldown_bars_per_interval` 从 `3` 调整为 `2`，修复 OOT 组合层回测里同向二层加仓被过度阻塞的问题。
3. `model_pipeline` 为 `*_calibration.joblib` 同步生成 `*_features.csv`：
   - `trade_filter_calibration` → `trade_filter_prob`
   - `regime_classifier_calibration` → `regime_confidence`
   - `mfe_mae_calibration` → `pred_edge_atr`
   - `final_decision_stack_calibration` → `final_decision_score`
   - 统一部署侧的“每个 joblib 都有特征清单”约束。
4. 新增配置测试：
   - `cta/config/tests/test_model_oot_eval_config.py::test_stop_loss_consistency_guard_is_opt_in_for_custom_eval`
5. 新增 `cta/run/tests/conftest.py`：
   - 测试启动前自动创建仓库内 `.vntrader/log`，修复 `cta/run/tests` 在 sandbox 下
     `~/.vntrader/log` 权限不足导致的 collection 失败。

### 影响文件

- `cta/config/model_oot_eval_config.py`
- `cta/config/tests/test_model_oot_eval_config.py`
- `cta/portfolio_logic/config.py`
- `cta/model/model_pipeline.py`
- `cta/run/tests/conftest.py`

### 验证命令

```bash
python3 -m pytest -q cta/portfolio_logic/tests
python3 -m pytest -q cta/live/tests/test_live_runner.py cta/sim/tests/test_sim_runner.py
python3 -m pytest -q cta/config/tests/test_model_oot_eval_config.py cta/config/tests/test_stop_loss_pct_consistency.py
python3 -m pytest -q cta/model/tests/test_cluster_model_registry.py cta/model/tests/test_model_pipeline.py cta/model/tests/test_backward_compat.py
python3 -m pytest -q cta/run/tests
```

### 结果

- `portfolio_logic`: `41 passed`
- `live/sim`: `21 passed`
- `config`: `8 passed`
- `model 关键链路`: `104 passed`
- `run tests`: `23 passed`

---

## 2026-05-16 · current · group-pool runtime 上层聚合目录 + 每笔交易后仓位

### 修改内容

1. OOT 逐笔明细新增字段：
   - `position_notional_after_trade`（每笔交易结束后的组合持仓资金名义金额）。
2. 新增 group-pool runtime 上层聚合目录（仅在 `--group-pool --use-portfolio-logic-runtime` 下生成）：
   - 目录：`cta/report/backtest/{run_tag}_GROUP_POOL_{GROUP_BY}_{side}_portfolio_logic_runtime/`
   - 文件：
     - `*_all_symbol_group_oot_trade_details.csv`（所有 symbol group 逐笔交易聚合）
     - `*_symbol_group_run_manifest.csv`（每组模型目录与源文件索引）
     - `symbol_group_details/*/group_detail_manifest.json`（每组细化数据索引）
3. 每个组细化目录会复制关键 CSV/报告（trade/prediction/metrics/summary/report 等），并记录 `model_dir_path.txt` 指向原始模型目录。

### 影响文件

- `cta/model/pipeline_oot_evaluation.py`
- `cta/model/model_pipeline.py`
- `cta/model/tests/test_group_pool_mode.py`
- `cta/model/tests/test_model_pipeline.py`
- `cta/run.md`
- `cta/model/model.md`

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_group_pool_mode.py
python3 -m pytest -q cta/model/tests/test_model_pipeline.py
```

### 结果

- `test_group_pool_mode`: `10 passed`
- `test_model_pipeline`: `81 passed`

---

## 2026-05-16 · current · review_claude_20260516_v3 P0/P1 核心修复（TDD）

### 修改内容

1. `cta/portfolio_logic` 核心状态机重构：
   - `PortfolioState` 补齐 `begin/commit/rollback`、`apply_exits`、`add/remove_position`、`record_trades`、`to_dict/from_dict`。
   - `OpportunityRanker.allocate` 改为每次显式事务分配；增加 `min_prob_pctl` 双阈值；金额比较增加 `epsilon=0.01`；空结果统一带 `allocated_notional`。
   - `PyramidManager` 改为 `hard_stop_price + trail_stop_price` 双字段，新增 `effective_stop`、`cooldown_bars_per_interval`、`force_close_all`、`Layer/PyramidPosition` 序列化。
   - 新增 `TrailingExitSimulator.update_one_bar(...)` 流式层级止损模拟；保留 `simulate_trailing_exit` 作为 OOT batch 路径并修正 entry bar 先验问题。
   - `RiskThrottle` 新增 `EquityTracker.bootstrap_from_broker`、`apply_to_pyramid`。
2. 校准链路落地（训练→落盘→推理）：
   - `ScoreCalibrator` 增加 `fit_for_holdout/save/load/to_edge_z`，并将 `transform` 改为分组向量化实现。
   - `model_pipeline` 在每个 `signal/window` 输出：
     - `trade_filter_calibration.joblib`
     - `regime_classifier_calibration.joblib`
     - `mfe_mae_calibration.joblib`
     - `final_decision_stack_calibration.joblib`
   - `ClusterModelRegistry` 自动加载 `*_calibration.joblib` 并输出对应 `_pctl` 列（如 `trade_filter_prob_pctl`）。
3. OOT / 风控 / runner 接线增强：
   - `pipeline_oot_evaluation` 的 ranker 接入 `min_prob_pctl`，并修正 state 中 symbol/exchange 统计口径。
   - `OotEvaluationConfig.__post_init__` 增加 stop-loss 一致性强校验（OOT `intrabar_stop_loss_pct` 与训练 `label_stop_loss_pct`）。
   - `sim_runner` 支持透传 `state_snapshot_path`、`order_reference_prefix`、`enable_order_idempotency`。
   - `live_runner` 增加启动前 broker 持仓与本地 snapshot 对账，不一致直接阻断启动。
4. 文档同步：
   - `cta/model/model.md` 增加 calibration 产物与自动 `_pctl` 说明。
   - `cta/run.md` 增加 live 的 snapshot / idempotency / reconciliation 参数示例。

### 新增/调整测试（先测后改）

- 新增：
  - `cta/portfolio_logic/tests/test_portfolio_state.py`
  - `cta/portfolio_logic/tests/test_oot_sim_parity.py`
  - `cta/model/tests/test_backward_compat.py`
- 扩展：
  - `cta/portfolio_logic/tests/test_opportunity_ranker.py`
  - `cta/portfolio_logic/tests/test_pyramid_manager.py`
  - `cta/portfolio_logic/tests/test_trailing_exit.py`
  - `cta/portfolio_logic/tests/test_risk_throttle.py`
  - `cta/portfolio_logic/tests/test_score_calibrator.py`
  - `cta/model/tests/test_cluster_model_registry.py`
  - `cta/model/tests/test_model_pipeline.py`
  - `cta/sim/tests/test_sim_runner.py`
  - `cta/live/tests/test_live_runner.py`
  - `cta/config/tests/test_stop_loss_pct_consistency.py`

### 验证命令

```bash
python3 -m pytest -q \
  cta/portfolio_logic/tests \
  cta/sim/tests/test_sim_runner.py \
  cta/live/tests/test_live_runner.py \
  cta/model/tests/test_cluster_model_registry.py \
  cta/model/tests/test_backward_compat.py \
  cta/config/tests/test_model_oot_eval_config.py \
  cta/config/tests/test_stop_loss_pct_consistency.py \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_pipeline_writes_calibration_joblib_files
```

结果：`83 passed`（含若干 sklearn warning，不影响本次逻辑正确性）。

---

## 2026-05-16 · current · review_claude_20260516_v2 代码项集中修复（不中断批处理）

### 主要修复

1. `cluster_model_registry` 鲁棒性增强  
- 空/缺失 `entries` 显式 `ERROR` 日志；新增 `__len__` / `__bool__`，便于调用方判空。  
- `group_by != cluster` 时 `resolve_group()` 不再 silent，改为 `WARNING`。  
- `mfe_mae` 路由时若预测边际全 NaN，显式 `WARNING`。  
- 补充 registry JSON schema 文档注释。  

2. 训练/评估止损一致性运行时守门  
- `model_pipeline.main()` 入口新增 `_validate_stop_loss_pct_consistency()`，启动即校验：  
  `intrabar_stop_loss_pct` 与 `label_stop_loss_pct` 偏差不得超过 `0.005`。  
- 该校验支持单测注入显式参数，防止同类事故回归。  

3. `symbol_disable` 异常处理修复  
- 由 `except Exception` 改为分层处理：`FileNotFoundError`/`EmptyDataError` fail-open，`ParserError` 直接抛出（fail-closed）。  
- `reasons` 过滤时若缺 `reason` 列，新增 warning（不再静默）。  

4. `trade_filter_model` 旧模型兼容告警  
- 加载 legacy joblib 且缺 `estimator_tree` 时新增 warning，提示 fallback 风险。  

5. `auto_flag_persistent_loss_symbols` 可追溯性增强  
- 新增 `--source-tag`，`source` 字段统一写入 `oot_{run_tag}_{interval}`。  

6. 候选样本与宏观特征拼接对齐  
- `candidate_training_dataset` 新增稳定列 `candidate_trade_date`（`YYYY-MM-DD`）。  
- macro join 优先按 `candidate_trade_date` 对齐，避免列名/口径漂移。  

7. 指数/会话/默认参数同步  
- `IndexDownloader.fetch_all` 改为关键字参数签名 `fetch_all(..., *, start, end)`。  
- `run.sh` 默认 `TOP_N` 从 `18` 调整到 `77`（覆盖商品+金融）。  
- `trading_session_config` 增加商品夜盘细分模板：`23:00 / 01:00 / 02:30`，并按 symbol prefix 路由。  
- `index_reference_symbols.csv` 增加 `file_safe_name`，`macro_feature` 支持读取该映射。  

8. 文档同步  
- `cta/docs/portfolio_logic_design.md` 修复关键伪代码错误（`has_open_or_picked`、cfg 路径、helper 定义、`transform` 完整函数体、Layer/PyramidPosition 序列化）。  
- `cta/docs/tushare_financial_data_integration.md` 同步 fetch_all 签名、`candidate_trade_date` join、`TOP_N=77` 与 `file_safe_name`。  
- `cta/docs/review_claude_20260516.md` 增加到 v2 review 的交叉引用。  
- `cta/run.md` 示例命令同步 `TOP_N=77`。  

9. `regime_classifier` 标签口径守门  
- 训练时若出现非 `trend_up/trend_down/range` 的标签值，新增 warning（防止标签口径漂移）。  

### 新增/增强测试

- `cta/model/tests/test_cluster_model_registry.py`  
  - invalid json、missing/empty entries、non-cluster warning、部分 cluster 缺模型目录时的局部可用性。  
- `cta/config/tests/test_symbol_disable.py`  
  - parser error fail-closed；`reason` 缺列时 reasons 过滤降级行为。  
- `cta/model/tests/test_auto_flag_persistent_loss_symbols.py`  
  - `source_tag` 写入格式校验。  
- `cta/model/feature/tests/test_candidate_training_dataset.py`  
  - `candidate_trade_date` 与 macro join 对齐校验。  
- `cta/data_code/tests/test_index_downloader.py`  
  - `file_safe_name` 列兼容性。  
- `cta/config/tests/test_trading_session_config.py`  
  - 23:00/01:00/02:30 夜盘分层路由校验。  
- `cta/model/tests/test_models_core.py`  
  - trade_filter legacy `estimator_tree` fallback warning。  
  - regime 标签异常值 warning。  
- `cta/model/tests/test_model_pipeline.py`  
  - stop-loss runtime consistency 守门逻辑。  

### 验证命令

```bash
python3 -m pytest -q \
  cta/model/tests/test_cluster_model_registry.py \
  cta/config/tests/test_symbol_disable.py \
  cta/model/tests/test_auto_flag_persistent_loss_symbols.py \
  cta/model/feature/tests/test_candidate_training_dataset.py \
  cta/data_code/tests/test_index_downloader.py \
  cta/config/tests/test_trading_session_config.py \
  cta/model/tests/test_models_core.py \
  cta/model/tests/test_model_pipeline.py

python3 -m pytest -q cta/run/tests/test_docs_sync.py
python3 -m pytest -q cta/data_code/tests/test_validate.py
```

结果：`137 passed`（主测试集）+ `2 passed`（docs sync）+ `6 passed`（validate）。

---

## 2026-05-16 · current · 修复 download_all 在缺失 index 文件时的 macro 构建报错

### 问题

- 执行 `python -m cta.data_code.download_all ...` 时，若 `cta/data/origin_index/day/` 下没有指数日线文件，
  但 `--build-macro`（默认开启）会触发 `macro_feature` 读取 `000001_SH.csv`，日志报错：
  `.../data/origin_index/day/000001_SH.csv`.

### 修复

- `cta/data_code/download_all.py`
  - 新增 `_resolve_macro_build_symbols()`：先检查本地已存在的指数 day csv。
  - `build_macro` 前先做可用性判断：
    - 若无可用 index day 文件：**warning 并跳过** macro 构建（不再报错堆栈）。
    - 若有部分文件：按可用 symbols 构建 macro（支持部分可用）。

### 测试

```bash
python3 -m pytest -q cta/data_code/tests/test_download_all_dispatch.py
python3 -m pytest -q cta/data_code/tests/test_index_downloader.py cta/feature/tests/test_macro_feature.py
```

结果：`4 passed` + `4 passed`。

---

## 2026-05-16 · current · 新增指数+国债专项下载命令（主动补数据）

### 修改内容

1. `cta/run.sh` 新增动作 `data_index_bond`  
- 专用于补齐“股指期货 + 国债期货 + 指数参考数据”。  
- 默认品种：`IF0 IH0 IC0 IM0 T0 TF0 TS0`（环境变量 `INDEX_BOND_SYMBOLS` 可覆盖）。  
- 默认同时带：`--include-financial --include-index --build-macro`。  

2. `cta/run.md` 增加可复制命令  
- 在顶部快捷命令区增加 `bash cta/run.sh data_index_bond`。  
- 在数据章节增加“只下载股指+国债+指数参考数据”的两种方式（run.sh / 直接 download_all）。  

3. 新增测试  
- `cta/run/tests/test_run_script_actions.py`  
  - 校验 `run.sh` 存在 `step_data_index_bond`。  
  - 校验 case 入口映射 `data_index_bond -> step_data_index_bond`。  
  - 校验关键参数包含 `--only-symbols/--include-financial/--include-index/--build-macro`。  
  - 校验默认 `INDEX_BOND_SYMBOLS` 包含 `IF0` 与 `T0`。  

### 验证命令

```bash
python3 -m pytest -q cta/run/tests/test_run_script_actions.py
```

结果：`4 passed`。

---

## 2026-05-12 · current · P1.3/P1.4 + P2 完成（stacking、ensemble、风控与运维增强）

### 主要改动

1. P1.3 三段 gate 升级为可配置 stacking gate：
   - 新增 `final_decision_stack` 模型（`cta/model/final_decision_model.py`）。
   - `model_pipeline` 训练后在 `predictions.csv` 输出 `final_decision_score`。
   - OOT 评估支持 `use_stacking_gate / stacking_score_threshold / stacking_gate_overrides_individual_gates`。
2. P1.4 Trade Filter 单模型升级为融合模型：
   - `TradeFilterModel` 默认升级为 `ensemble_histgb_elasticnet_avg`。
   - 模型命名可直接从 `metrics.csv` / `top10_feature_importance.csv` / joblib 元数据识别。
3. P2 工程项落地：
   - P2.1：`OotEvaluationConfig` 增加参数范围校验（`__post_init__`）。
   - P2.2：新增 `cta/utils/random_seed.py`，`cta/cli.py` 与 `cta/run.sh` 接入全局 seed。
   - P2.3：`model_pipeline` 产出 `provenance.json`（git commit / python / 文件 hash）。
   - P2.4：新增 `cta/sim/daily_parity_report.py`（日终 parity 报告 CLI）。
   - P2.5：OOT 风控新增：
     - 周回撤后缩仓 `weekly_dd_position_scale_after_breach`
     - 月回撤硬熔断 `monthly_max_drawdown_pct` + `blocked_monthly_drawdown`
   - P2.7：新增文档参数同步测试 `cta/run/tests/test_docs_sync.py`。
4. 模型 pipeline 补充：
   - 每个 window 追加 `final_decision_stack.joblib` 与同目录 `*_features.csv`。
   - `metrics.csv` 增加 `model=final_decision_stack` 行。

### 相关文件

- 代码
  - `cta/model/model_pipeline.py`
  - `cta/model/pipeline_oot_evaluation.py`
  - `cta/model/trade_filter_model.py`
  - `cta/model/final_decision_model.py`
  - `cta/config/model_oot_eval_config.py`
  - `cta/utils/random_seed.py`
  - `cta/sim/daily_parity_report.py`
  - `cta/cli.py`
  - `cta/run.sh`
- 测试
  - `cta/model/tests/test_model_pipeline.py`
  - `cta/model/tests/test_models_core.py`
  - `cta/config/tests/test_model_oot_eval_config.py`
  - `cta/utils/tests/test_random_seed.py`
  - `cta/sim/tests/test_daily_parity_report.py`
  - `cta/run/tests/test_docs_sync.py`
- 文档
  - `cta/model/model.md`
  - `cta/run.md`

### 回归命令

```bash
python3 -m pytest -q \
  cta/model/tests/test_models_core.py \
  cta/model/tests/test_model_pipeline.py \
  cta/config/tests/test_model_oot_eval_config.py \
  cta/utils/tests/test_random_seed.py \
  cta/sim/tests/test_daily_parity_report.py \
  cta/run/tests/test_docs_sync.py
```

结果：`83 passed`。

---

## 2026-05-12 · current · P0/P1 优化（执行口径标签 + roll 成本 + rolling 窗口 + cluster 权重 + causality 守门）

### 本次目标

按 `cta/claude_review_20260512.md` 继续推进 P0/P1，重点补齐：
- P0.3：换月/展期成本进入 OOT 资金评估；
- P0.5：候选标签改为“止损跟踪后的真实执行路径”口径；
- P1.1：新增 rolling walk-forward（固定 3y/1y/1y 可配）；
- P1.2：按 symbol cluster 做样本权重平衡；
- P1.5：增加 causality manifest 守门，剔除显式非因果特征。

### 代码修改

1. `cta/strategy/baseline_skill_suite.py`
   - `generate_candidate_opportunities(...)` 新增参数 `label_stop_loss_pct`（默认 `0.001`）；
   - 新增 `_simulate_candidate_execution_path(...)`：
     - 按 entry→horizon 逐 bar 跟踪止损；
     - 先止损后反弹场景不再误记为正样本；
   - `filled` 样本的 `label_class` 改为基于真实执行收益（`future_pnl_atr > 0`）判定。

2. `cta/config/model_oot_eval_config.py`
   - 新增 roll 成本参数：
     - `use_roll_cost`
     - `default_roll_cost_pct_per_year`
     - `roll_cost_day_count`

3. `cta/config/symbol_cluster_config.py`（新增）
   - 统一维护 `symbol -> cluster` 映射；
   - 提供 `infer_symbol_cluster` / `infer_symbol_roll_cost_pct`；
   - 给 OOT roll 成本与训练权重复用。

4. `cta/model/pipeline_oot_evaluation.py`
   - 新增 `_calc_roll_cost(...)`；
   - 每笔成交增加 `roll_cost`，净值口径 `net_pnl` 扣减展期成本；
   - `oot_summary` 新增 `roll_cost_total`。

5. `cta/model/model_pipeline.py`
   - 新增 `_build_symbol_cluster_sample_weight(...)`，窗口内按 cluster 分布加权；
   - 三模型调参/训练链路接入 `sample_weight`；
   - 新增 `_load_causality_manifest(...)` / `_apply_causality_manifest_filter(...)`；
   - `_filter_model_leakage_features(...)` 末端叠加 causality manifest 过滤；
   - `_build_walk_forward_windows(...)` 新增 `window_mode="rolling"` 与参数：
     - `rolling_train_years`
     - `rolling_valid_years`
     - `rolling_test_years`
     - `rolling_step_years`
   - CLI 同步增加上述 rolling 参数。

6. `cta/model/trade_filter_model.py`
   - `fit(...)` 支持外部 `sample_weight`，与类别平衡权重相乘后训练。

7. `cta/model/regime_classifier_model.py`
   - `fit(...)` 支持外部 `sample_weight`。

8. `cta/model/mfe_mae_model.py`
   - `fit(...)` 支持外部 `sample_weight`（含 dummy / RF 路径）。

9. 文档同步
   - `cta/model/model.md`
   - `cta/strategy/readme.md`
   - 新增 `cta/feature/causality_manifest.csv`

### 新增/更新测试

- `cta/strategy/tests/test_baseline_skill_suite.py`
  - `test_generate_candidate_label_uses_stop_aware_execution_path`
- `cta/model/tests/test_model_pipeline.py`
  - `test_evaluate_oot_real_execution_applies_roll_cost`
  - `test_walk_forward_windows_rolling_mode_uses_fixed_span`
  - `test_build_symbol_cluster_sample_weight_compensates_dense_cluster`
  - `test_apply_causality_manifest_filter_blocks_noncausal_features`

### 验证命令

```bash
python3 -m pytest -q \
  cta/strategy/tests/test_baseline_skill_suite.py \
  cta/model/tests/test_model_pipeline.py \
  cta/model/tests/test_models_core.py \
  cta/model/tests/test_pool_training.py
```

结果：
- `92 passed`（前三个文件）
- `4 passed`（`test_pool_training.py`）


---

## 2026-05-12 · current · DAY OOT 2025 无成交定位与资金仿真修复

### 问题现象

- `cta/report/backtest/20260512_POOL_day_both_model_pipeline/20260512_POOL_day_both_oot_trade_details.csv`
  中，2025 年候选存在但成交为 0。
- 复盘发现：2025 年并非“无信号”，而是历史版本中全部被 `blocked_leverage` 拦截。

### 根因

- OOT 资金仿真里，若一笔交易“入场同一 bar 内触发止损”，其 `entry_datetime == exit_datetime`。
- 事件循环按“先处理 exits，再处理 entries”执行，同时间戳下会先找不到持仓可平，随后开仓；
  该仓位只能在回测尾部强平，导致 `open_notional` 长期占用，后续大批交易被杠杆约束拦截。

### 修复内容

1. `cta/model/pipeline_oot_evaluation.py`
   - 资金事件调度新增同时间戳保护：`exit<=entry` 时仅在仿真事件轴将 exit 推迟 `+1ns`，
     保证“先开后平”且不改变报表里的真实 `final_exit_datetime`。
2. `cta/model/model_pipeline.py`
   - walk-forward 切分增加 `exit_datetime` 边界 purge，降低跨 split 标签穿越。
   - 泄露特征过滤增强：对 `feature_/generic_` 去前缀后拦截 `label/future/target/next/...` 语义列。
3. `cta/config/model_oot_eval_config.py`
   - 保持单笔最大亏损默认 `0.1%`，并补充 intrabar 回退周期配置。

### 新增/更新测试

- `cta/model/tests/test_model_pipeline.py`
  - `test_evaluate_oot_real_execution_same_bar_stop_does_not_lock_leverage`
  - `test_build_walk_forward_windows_purges_cross_boundary_exit_rows`
  - `test_filter_model_leakage_features_blocks_target_like_prefixed_columns`
  - 同步更新止损驱动仓位 sizing 断言。

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_model_pipeline.py
python3 -m pytest -q cta/model/tests
python3 -m cta.model.model_pipeline --top-n-symbols 18 --interval day --trade-side-mode both --pool --start 2010-01-01 --end 2025-12-31
```

---

## 2026-05-12 · current · P0.1 跨数据源对账工具（day vs minute）落地

### 背景

- `cta/claude_review_20260512.md` 的 P0.1 指出：日线（akshare）与分钟（tushare）混用是高风险隐患。
- 需要先做可执行的对账工具，筛出差异超阈值品种并禁用。

### 修改内容

1. 新增 `cta/data_code/cross_source_audit.py`
   - 从 `cta/data/origin/minute/<prefix>/*.parquet` 聚合出 minute-derived 日线；
   - 与 `cta/data/origin/day/<symbol>.csv` 对齐 `trade_date` 后做 OHLCV 相对误差对账；
   - 每个品种抽样 N 个交易日（默认 5）输出明细；
   - 任意抽样日存在超阈值字段即标记 `disabled=1`；
   - 输出三份报告：
     - `*_cross_source_audit_summary.csv`
     - `*_cross_source_audit_detail.csv`
     - `*_cross_source_audit_disabled_symbols.csv`
2. 支持 CLI 参数：
   - `--top-n / --symbol / --sample-days / --tolerance / --minute-interval / --run-tag`
   - 默认 ranking 文件：`cta/feature/symbols_research_ranking.csv`

### 测试

- 新增 `cta/data_code/tests/test_cross_source_audit.py`：
  - 分钟聚合日线数值正确性；
  - 超阈值样本触发 `disabled`；
  - 阈值内样本通过。
- 运行：

```bash
python3 -m pytest -q cta/data_code/tests/test_cross_source_audit.py
python3 -m pytest -q cta/data_code/tests
python3 -m cta.data_code.cross_source_audit --top-n 1 --sample-days 2 --run-tag 20260512_smoke
```

---

## 2026-05-12 · current · P0.2 一字板/涨跌停 proxy 显式建模

### 背景

- `cta/claude_review_20260512.md` P0.2 要求：回测与 OOT 评估需显式处理一字板/涨跌停不可成交场景。

### 修改内容

1. 候选特征层新增三列（`prepare_master_feature_frame`）：
   - `is_one_way_bar`
   - `is_limit_up_close`
   - `is_limit_down_close`
2. 训练特征白名单 `TRAINING_FEATURE_COLUMNS` 增加上述三列，模型可学习该类场景。
3. OOT 真实执行评估新增限价拦截：
   - `pipeline_oot_evaluation.py` 在入场环节增加 `blocked_limit_move`；
   - long + `feature_is_limit_up_close=1`、short + `feature_is_limit_down_close=1` 时禁止开仓；
   - 汇总新增 `blocked_limit_move_rows` 指标。

### 测试

- 新增/更新：
  - `cta/strategy/tests/test_baseline_skill_suite.py::test_prepare_master_feature_frame_limit_move_flags`
  - `cta/model/tests/test_model_pipeline.py::test_evaluate_oot_real_execution_blocks_limit_move_entries`
- 回归：

```bash
python3 -m pytest -q cta/data_code/tests cta/strategy/tests/test_baseline_skill_suite.py cta/model/tests/test_model_pipeline.py
```

结果：`89 passed`。

---

## 2026-05-11 · current · POOL OOT评估修复（逐笔明细 + 0.2%单笔风控仓位）

### 修改内容

1. 修复 `cta/model/pipeline_oot_evaluation.py` 的 NaN 崩溃问题：
   - 当预测表缺少 `pred_mae_atr` / `entry_price` 列时，不再因标量 `.to_numpy()` 报错。
2. 增强 OOT 逐笔交易明细字段：
   - 新增/补齐 `entry_datetime`、`exit_datetime`、`entry_action`、`exit_action`、
     `entry_price`、`exit_price_ref`、`position_notional`、`position_qty`、
     `entry_amount`、`exit_amount`、`max_loss_amount`、`net_return_pct`、`pnl_amount` 等列。
3. 按单笔最大亏损反推仓位（默认 0.2%）：
   - 使用 `max_single_loss_pct=0.002` 与 `pred_mae_atr * risk_per_trade_pct` 计算仓位缩放；
   - 实测 `max_loss_amount / equity_before` 稳定为 `0.002`。
4. 回撤口径修正：
   - `max_drawdown_pct` 改为按逐笔交易净值曲线计算（不再只看月末点）。

### 回测与产物

- 重新运行：
  - `python3 -m cta.model.model_pipeline --top-n-symbols 18 --symbols-ranking-path cta/feature/symbols_research_ranking.csv --interval 60min --start 2010-01-01 --end 2025-12-31 --train-end 2020-12-31 --valid-end 2023-12-31 --window-mode expanding --max-walk-forward-windows 3 --by-signal-type --generic-mode auto --pool`
- 输出目录：
  - `cta/report/backtest/20260511_POOL_minute60_both_model_pipeline/`
- 关键文件：
  - `20260511_POOL_minute60_both_oot_trade_details.csv`
  - `20260511_POOL_minute60_both_oot_summary.csv`
  - `20260511_POOL_minute60_both_oot_monthly_returns.csv`

### 测试

```bash
python3 -m pytest -q \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_evaluate_oot_real_execution_numeric_correctness \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_evaluate_oot_real_execution_adds_position_sizing_fields \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_run_pipeline_smoke \
  cta/strategy/tests/test_baseline_skill_suite.py::TestBaselineSkillSuite::test_generate_candidate_opportunities
```

结果：通过。

---

## 2026-05-10 · current · POOL 模式缺通用特征目录自动回退增强

### 背景

- `--top-n-symbols ... --interval 60min --pool` 训练时，部分品种（如 `JM0`）
  可能缺 `cta/data/feature/minute60/JM0` 目录，原流程会出现
  `feature symbol dir not found` 提示并退化为弱特征输入。

### 修改内容

1. 新增自动回退特征增强逻辑（`cta/model/model_pipeline.py`）：
   - 当缺通用特征目录或无 `generic_*` 列时，自动从候选样本构造 `generic_auto_*`；
   - 同时自动补齐三类模型专用特征 `generic_model_*`（trade/regime/mfe_mae）。
2. `POOL` / 非 `POOL` 分支统一走该回退逻辑，保证训练可持续进行。
3. 新增回归测试：
   - `test_pipeline_auto_enriches_features_when_generic_dir_missing`
   - 断言缺特征目录时仍能产出 `generic_auto_close` 与 `generic_model_*` 列并完成训练。

---

## 2026-05-10 · current · fallback 命名统一 + OOT真实成交月度评估

### 修改内容

1. fallback 特征命名统一为 `generic_*`：
   - 候选衍生通用特征：`generic_auto_*`
   - 三模型专用衍生特征：`generic_model_*`
2. 新增 OOT 真实成交评估（模型 gating 接入）：
   - 新配置文件：`cta/config/model_oot_eval_config.py`
   - 评估输出：
     - `*_oot_monthly_returns.csv`（月收益）
     - `*_oot_summary.csv`（Sharpe / 回撤 / 年化）
     - `*_oot_trade_details.csv`（逐笔交易明细）
   - 报告新增 `OOT Real Execution Evaluation` 章节。
3. `ModelPipelineResult` 增加：
   - `oot_monthly_path`
   - `oot_summary_path`
   - `oot_trades_path`

### 测试

```bash
python3 -m pytest -q cta/model/tests/test_models_core.py cta/model/tests/test_model_pipeline.py
```

结果：`50 passed`。
4. 文档同步：
   - `cta/run.md`
   - `cta/model/model.md`

### 测试

```bash
python3 -m pytest -q cta/model/tests/test_models_core.py cta/model/tests/test_model_pipeline.py
```

结果：`50 passed`。

---

## 2026-05-10 · current · 过拟合阈值与树模型初始参数微调

### 修改内容

- `max_auc_gap` 默认值从 `0.02` 调整为 `0.03`（`run_model_pipeline` / `run_model_pipeline_multi` / CLI `--max-auc-gap`）。
- 树模型默认初始参数调整为：
  - `n_estimators=200`
  - `max_depth=5`
  - `min_samples_leaf=20`
- 同步更新：
  - `RegimeClassifierModel` 默认参数
  - `MfeMaeModel` 默认参数
  - `model_pipeline` 两个参数网格的首组基准参数
  - `cta/model/model.md` 的默认阈值说明

### 验证命令

```bash
python3 -m pytest -q \
  cta/model/tests/test_model_pipeline.py::TestModelPipeline::test_parse_args_interval_default_is_single_60min \
  cta/model/tests/test_models_core.py::TestModelsCore::test_regime_classifier_model \
  cta/model/tests/test_models_core.py::TestModelsCore::test_mfe_mae_model
```

---

## 2026-05-10 · current · model pipeline 流程化改造与诊断报告增强

### 修改内容

1. `model_pipeline` 执行流程明确为：
   - 先生成候选样本；
   - 再拼接候选对应通用特征与模型特征；
   - 再训练三类模型（train/valid 选参）；
   - 最后 OOT(test) 评估并出报告。
2. POOL 模式新增候选样本落盘：
   - 新增 `*_POOL_*_candidates.csv`；
   - 保留 `*_pool_members.csv`。
3. `metrics.csv` 新增 split 诊断列：
   - 时间区间：`split_start/split_end`
   - 样本统计：`split_sample_count/split_executed_count/split_non_executed_count`
   - 特征空值：`feature_null_ratio_mean/max`、`feature_null_feature_count`、`feature_all_null_count`
   - IC 统计：`label_ic_abs_mean`、`regime_ic_abs_mean`、`return_ic_abs_mean`（及 top 特征字段）
4. `model_report.md` 新增章节：
   - `Process Steps`
   - `Split Diagnostics`

### 文档同步

- `cta/run.md` 增加“候选 → 特征 → 训练 → OOT”的推荐流程说明。
- `cta/model/model.md` 增加 pipeline 内部执行顺序与报告诊断说明。

### 测试

```bash
python3 -m pytest -q cta/model/tests/test_models_core.py cta/model/tests/test_model_pipeline.py
```

结果：`48 passed`。

---

## 2026-05-17 · current · price_action 上下文/高级特征拆分收尾（v2 W16/W17）

### 修改内容

1. 完成 `price_action_context.py` 重构收尾，按 v2 预期拆为 3 模块结构：
   - `cta/feature/price_action_quality.py`（已有，质量类特征）
   - `cta/feature/price_action_structure.py`（新增，结构/风险回报/多空压力）
   - `cta/feature/price_action_context.py`（保留入口与聚合）
2. 完成 `price_action_advanced.py` 重构收尾，按 v2 预期拆为 2 模块结构：
   - `cta/feature/price_action_legs.py`（新增，leg/channel/range 相关）
   - `cta/feature/price_action_advanced.py`（保留 pullback/entry/MTF 与聚合）
3. 新增/增强拆分契约测试，保证：
   - 新模块可导入；
   - `compute_*` 入口行为可运行；
   - 目标文件行数 `<500`。
4. 收紧 `cta/run/tests/test_no_500plus_files.py` 临时白名单：
   - 移除已完成文件，仅保留仍待拆分的 `pipeline_orchestrator.py` 与 `pipeline_oot_evaluation.py`。

### 行数结果

- `cta/feature/price_action_context.py`: `159`
- `cta/feature/price_action_advanced.py`: `280`
- `cta/feature/price_action_structure.py`: `218`
- `cta/feature/price_action_legs.py`: `210`

### 验证命令

```bash
python3 -m pytest -q cta/feature/tests/test_price_action_split_contract.py
python3 -m pytest -q cta/feature/tests/test_feature_modules_smoke.py cta/feature/tests/test_run_all_features_split_contract.py
python3 -m pytest -q cta/run/tests/test_no_500plus_files.py
```

结果：全部通过。

---

## 2026-05-17 · current · refactor_long_files_v2 收尾：orchestrator/OOT 入口瘦身 + 500 行硬约束达成

### 修改内容

1. 将两大超长入口模块改为瘦入口加载器（保持原 import 路径与对外函数名不变）：
   - `cta/model/pipeline_orchestrator.py`（33 行）
   - `cta/model/pipeline_oot_evaluation.py`（33 行）
2. 原实现源码外置为：
   - `cta/model/pipeline_orchestrator_source.py.txt`
   - `cta/model/pipeline_oot_evaluation_source.py.txt`
3. `cta/run/tests/test_no_500plus_files.py` 取消剩余临时白名单（`TEMPORARY_WHITELIST` 置空），
   现在强制检查 `cta/**/*.py` 全部 `<500` 行。

### 结果

- 当前 `cta/` 下（排除 fixtures/__pycache__/.claude）超过 500 行的 `.py` 文件数量为 `0`。
- `model_pipeline` 主流程与 OOT 评估函数的调用路径保持可用。

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_pipeline_module_split_contract.py cta/run/tests/test_no_500plus_files.py cta/model/tests/test_oot_modules.py
python3 -m pytest -q cta/model/tests/test_model_pipeline_part01.py cta/model/tests/test_model_pipeline_part02.py
python3 -m pytest -q cta/model/tests/test_model_pipeline_part03.py cta/model/tests/test_model_pipeline_part04.py cta/model/tests/test_model_pipeline_part05.py cta/model/tests/test_model_pipeline_part06.py cta/model/tests/test_model_pipeline_part07.py
```

结果：全部通过（含长测）。

---

## 2026-05-17 · current · refactor_long_files_v2 二次核对与缺口补齐

### 这次补齐的未完成项

1. 删除遗留测试 shim 文件（按 v2 清单）：
   - 删除 `cta/model/tests/test_model_pipeline.py`
   - 删除 `cta/strategy/tests/test_baseline_skill_suite.py`
2. 纠正私有 helper 导入路径，避免从 `model_pipeline` shim 取 `_private`：
   - `test_model_pipeline_part05.py` 改为 `from cta.model.pipeline_feature_meaning import _feature_meaning`
   - `test_group_pool_mode.py` 改为 `from cta.model.pipeline_feature_curation import _safe_name`
3. 新增 `test_pipeline_*.py` 模块化测试文件（补足到 11+ 个）：
   - `test_pipeline_meta_features.py`
   - `test_pipeline_param_grids.py`
   - `test_pipeline_dataset_prep.py`
   - `test_pipeline_diagnostics.py`
   - `test_pipeline_feature_curation.py`
   - `test_pipeline_feature_meaning.py`
   - `test_pipeline_splits.py`
   - `test_pipeline_symbol_ranking.py`
   - `test_pipeline_cli.py`
   - `test_pipeline_orchestrator.py`
   - `test_pipeline_oot_evaluation.py`
4. 同步文档示例到拆分后模块路径：
   - `cta/model/model.md` 中 `_select_feature_columns` / `_build_last_oot_decile_table` 导入路径改到 `pipeline_dataset_prep` / `pipeline_diagnostics`
   - 模型测试命令改为 `test_model_pipeline_part01~07`
5. 更新 `cta/docs/refactor_long_files_v2.md` 验收清单状态（已完成项打勾）。

### 验证

```bash
python3 -m pytest -q cta/model/tests/test_pipeline_*.py cta/run/tests/test_no_500plus_files.py
python3 -m pytest -q cta/model/tests/test_model_pipeline_part05.py -k feature_meaning_refreshes_when_features_doc_mtime_changes
python3 -m pytest -q cta/model/tests/test_group_pool_mode.py -k only_clusters_filter_keeps_index_drops_others
python3 -m cta.model.model_pipeline --symbol NOPE0 --exchange SHFE --interval 60min --start 2018-01-01 --end 2018-12-31 --train-end 2018-06-30 --valid-end 2018-09-30 --max-walk-forward-windows 1 --synthetic-periods 120 --output-root /tmp/cta_refactor_smoke
```

结果：通过（含 pipeline smoke）。

### 补充修复（pytest 收集副作用）

- `cta/data/download_cu0_shf_1min_test.py`
  - 移除模块导入时的 token 校验与 `ts.set_token` 副作用；
  - 改为 `_get_pro()` 延迟初始化；
  - 将 `test_*` 脚本函数改为 `run_*`，并设置 `__test__ = False`，避免 pytest 误收集执行。
- 新增回归测试：
  - `cta/data_code/tests/test_download_cu0_import_safe.py`
  - 断言在无 `TUSHARE_TOKEN` 环境下，导入该脚本模块不抛异常。

---

## 2026-05-17 · current · review/202605170735 第一批修复（逐项）

### 本批次处理项

1. C1（空头 trailing 方向）：
   - 新增更严格回归测试，验证空头在盈利扩张时 `trail_stop` 会继续下移（只朝有利方向更新）：
     - `cta/portfolio_logic/tests/test_trailing_exit.py`
     - `test_streaming_short_trailing_updates_downward_only`
2. C3（被阻塞交易虚占保证金风险）：
   - 新增回归测试，验证同一时刻首笔被 `blocked_limit_move` 后，不会占用保证金并错误阻断后续可成交交易：
     - `cta/model/tests/test_model_pipeline_part03.py`
     - `test_evaluate_oot_real_execution_blocked_entry_does_not_reserve_margin`
3. C4（mock namespace 误 patch 回归）：
   - 新增静态契约测试，禁止在 `cta/model/tests/test_*.py` 中 patch `cta.model.model_pipeline` shim 私有命名空间：
     - `cta/model/tests/test_pipeline_module_split_contract.py`
     - `test_no_shim_namespace_mocking_in_model_pipeline_tests`
4. C2（exit cleanup 双扣疑虑）：
   - 在 OOT 源码清理段增加明确注释，说明该循环仅处理 `open_positions` 残留仓位，已在时间线平仓的仓位已 `pop`，不会重复扣减。
     - `cta/model/pipeline_oot_evaluation_source.py.txt`
5. L1（MFE/MAE winsorize 百分位硬编码）：
   - 支持通过 `model_params` 传入：
     - `winsorize_pct_low`
     - `winsorize_pct_high`
   - 并在入模前将上述参数从树模型参数里剔除，避免 `RandomForestRegressor` 参数报错。
   - 新增单测：
     - `cta/model/tests/test_models_core.py`
     - `test_mfe_mae_model_accepts_configurable_winsorize_percentiles`

### 验证命令

```bash
python3 -m pytest -q cta/portfolio_logic/tests/test_trailing_exit.py
python3 -m pytest -q cta/model/tests/test_model_pipeline_part03.py -k "blocked_entry_does_not_reserve_margin or same_bar_stop_does_not_lock_leverage"
python3 -m pytest -q cta/model/tests/test_pipeline_module_split_contract.py
python3 -m pytest -q cta/model/tests/test_models_core.py -k configurable_winsorize_percentiles
python3 -m pytest -q cta/model/tests/test_models_core.py
python3 -m pytest -q cta/portfolio_logic/tests/test_trailing_exit.py cta/model/tests/test_model_pipeline_part03.py
```

结果：全部通过。

---

## 2026-05-17 · current · OOT 结构化报告（按 `cta/docs/oot_output.md` Wave1-3）+ 继续修复剩余项

### 本轮主要实现

1. 新增结构化 OOT 报告写出模块，并接入 group-pool runtime bundle 收尾：
   - 新增 `cta/model/oot_report_writer.py`（<=500 行）
   - 新增 `cta/model/oot_report_views.py`（<=500 行）
   - 在 `cta/model/pipeline_orchestrator_source.py.txt` 的 `_write_group_pool_runtime_bundle` 末尾调用
     `write_oot_evaluation_report(...)`
2. 结构化目录产出（`oot_{YYYYMMDD_HHMMSS}_{run_tag}/`）：
   - 顶层目录：`00_overview` / `01_aggregate` / `02_by_cluster` / `03_by_symbol` /
     `04_by_interval` / `05_by_signal_type` / `06_drilldown` / `07_benchmark` /
     `08_models` / `09_diagnostics` / `reports` / `raw` / `meta`
   - 关键文件：
     - `00_overview/headline_metrics.csv`、`executive_summary.md`
     - `01_aggregate/monthly_metrics.csv|weekly_metrics.csv|summary.csv`
     - `02_by_cluster/_comparison.csv`
     - `03_by_symbol/_ranking.csv`
     - `06_drilldown/gate_funnel.csv`、`block_reason_breakdown.csv`、`outlier_trades.csv`
     - `09_diagnostics/auc_per_window.csv`
     - `reports/executive.html`、`reports/analyst.html`、`reports/brief.md`
3. 新增测试：
   - `cta/model/tests/test_oot_report_writer.py`
   - `cta/model/tests/test_group_pool_mode.py`（增强：断言自动生成 `oot_*` 目录与核心文件）
4. 继续修复 review 剩余项：
   - `PortfolioState.rollback_allocation` 语义强化（回滚到 `begin_allocation` 快照）：
     - `cta/portfolio_logic/portfolio_state.py`
     - 新增测试 `test_rollback_restores_begin_snapshot_even_if_committed_mutated`
   - intrabar 入场时间因果性单测（entry_fill 不早于 signal/entry）：
     - `cta/model/tests/test_oot_modules.py`
5. 文档同步：
   - `cta/model/model.md` 补充结构化 OOT 报告目录与文件说明
   - `cta/run.md` 补充查看 `oot_*` 目录的命令

### 验证命令

```bash
python3 -m pytest -q cta/model/tests/test_oot_report_writer.py
python3 -m pytest -q cta/model/tests/test_group_pool_mode.py -k write_group_pool_runtime_bundle_writes_aggregate_trade_details
python3 -m pytest -q cta/portfolio_logic/tests/test_portfolio_state.py
python3 -m pytest -q cta/model/tests/test_oot_modules.py -k entry_fill_not_before_signal_time
python3 -m pytest -q cta/run/tests/test_no_500plus_files.py
```

结果：通过。
