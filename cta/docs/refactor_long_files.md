# CTA 长文件拆分重构方案

---

## 0. Context（为什么改）

当前 `cta/` 包里有 4 个非测试 Python 文件超过 1000 行，已经接近或超过单文件可读极限。这些"god 文件"导致：

1. **改一处全文件 churn**：`model_pipeline.py` 一次小修改 git diff 经常 200+ 行飘移上下文
2. **测试文件爆炸**：`test_model_pipeline.py` 2390 行（源文件 60%），测试关注点散乱
3. **职责混合**：CLI 解析、配置归一、数据加载、训练、OOT 评估、产物落盘全挤在一起
4. **AI 协助效率低**：单次 Read 装不下，agent 调研要分批读

用户明确要求：
- **范围**：所有 >1000 行的源文件都拆（4 个文件）
- **策略**：直接破坏向后兼容、改测试（不留 deprecation shim）
- **测试**：源文件拆走后，测试文件按新模块边界一起拆

不在范围内：
- vnpy 主仓任何代码
- `cta/` 下 ≤1000 行的文件（即使有 600-900 行的也暂不动）
- 业务逻辑改动（只搬不改）

---

## 1. As-Is 现状

### 1.1 待重构文件清单

| 文件 | 行数 | 主要职责 | 关联测试 |
|------|------|---------|---------|
| `cta/model/model_pipeline.py` | 3868 | 模型 pipeline 编排：candidate gen → feature merge → walk-forward → 训 3 个模型 → OOT eval → 出报告。3 个 CLI 分支（group-pool/pool/single） | `tests/test_model_pipeline.py` (2390 行) |
| `cta/model/pipeline_oot_evaluation.py` | 1802 | OOT 真实成交评估：gate 过滤、仓位 sizing、intrabar 模拟、组合约束、指标汇总 | `tests/test_blind_spot_coverage.py`、`tests/test_backward_compat.py`（间接） |
| `cta/strategy/baseline_skill_suite.py` | 1578 | 4 种策略规则（Donchian/ATR/tight-range/pullback）+ feature 准备 + candidate 生成 + backtest 主程 | `tests/test_baseline_skill_suite.py` (652 行) |
| `cta/strategy/backtest_price_action_breakout.py` | 1099 | 单一策略 backtest 脚本：指标计算、setup 分类、仓位 sizing、月度统计、组合 equity | 无独立测试文件（standalone script） |

### 1.2 model_pipeline.py 内部结构（最复杂的一个）

3 个 `main()` 分支：
- group-pool（line 3627-3746）：symbols 按 tier/cluster 分组 → 每组一份模型
- single-pool（line 3763-3806）：所有 symbols 共享一份模型
- single-symbol multi-interval（line 3808-3857）：默认路径

核心 `run_model_pipeline()`：line 2000-3325，**1326 行**单函数。

按职责的 helper 簇：

| 簇 | 行数 | 代表函数 |
|---|------|---------|
| Symbol ranking & 加载 | ~180 | `_load_top_n_symbols_from_ranking`, `_load_symbol_groups_from_ranking`, `_resolve_run_exchange` |
| Feature curation | ~150 | `_load_causality_manifest`, `_apply_causality_manifest_filter`, `_filter_model_leakage_features`, `_safe_name` |
| Param grids & 调参 | ~280 | `_trade_filter_param_grid`, `_regime_classifier_param_grid`, `_mfe_mae_param_grid`, `_select_best_param_trial`, `_tune_*_model` |
| Split / walk-forward | ~130 | `_build_walk_forward_windows`, `_time_split`, `_normalize_intervals`, `_WalkForwardWindow` |
| 数据集准备 | ~470 | `_build_synthetic_candidate`, `_build_candidate_table`, `_ensure_training_columns`, `_select_feature_columns` |
| Diagnostics & alerts | ~520 | `_build_valid_test_gap_alerts`, `_build_top_feature_concentration_alerts`, `_dump_feature_manifest`, `_compute_feature_null_stats`, `_build_last_oot_decile_table` |
| Meta features | ~110 | `_regime_to_code`, `_build_final_decision_features`, `_validate_stop_loss_pct_consistency` |
| Provenance | ~70 | `_sha256_of_file`, `_sha256_of_text`, `_git_commit_short`, `_write_provenance` |
| Feature meaning | ~120 | `_load_feature_meaning_map`, `_feature_meaning`, `FEATURES_DOC_PATH` |

### 1.3 pipeline_oot_evaluation.py 结构

- Metrics 计算（line 25-103，~80 行）：`_max_drawdown_from_return_series`, `_count_roll_dates_between`, `_calc_roll_cost`
- Intrabar 模拟（line 104-315，~210 行）：`_IntrabarBarCache`, `_simulate_intrabar_exit`
- 仓位聚合（line 316-396，~80 行）：`_build_position_lifetime_table`
- 主引擎（line 397-1802，**1406 行**）：`_evaluate_oot_real_execution` — gate/ranker/throttle/pyramid + margin/leverage/drawdown + 交易模拟 + 月度/汇总/逐笔输出

### 1.4 baseline_skill_suite.py 结构

- Helpers & 工具（line 81-219，~140 行）：`_normalize_intervals`, `_compute_atr14`, `_side_allowed`, `_entry_order` 等
- Feature 准备（line 220-348，~130 行）：`prepare_master_feature_frame`（16 套特征：Donchian/ATR/tight-range/breakout-pullback/limits/one-way）
- 3 个策略类（line 350-532，~180 行）：`DonchianBaselineStrategy`, `ATRBreakoutBaselineStrategy`, `BreakoutPullbackBaselineStrategy`
- Factory + setup detection（line 534-960，~430 行）：`create_baseline_strategy`, `build_training_samples_from_trade_log`, `_infer_regime_label`, `_resolve_candidate_entry`, `_simulate_candidate_execution_path`, `_build_raw_setup_candidates`
- 主入口（line 963-1218，~256 行）：`generate_candidate_opportunities`
- Backtest 主程（line 1221-1503，~280 行）：`_compute_metrics`, `run_baseline_suite`, `run_baseline_suite_multi`, `_parse_args`, `main`

### 1.5 backtest_price_action_breakout.py 结构

- 工具 & 指标（line 116-262，~150 行）：`ensure_dir`, `parse_exchange`, `bars_to_df`, `load_daily_data`, `add_indicators`, `calc_annualized_vol`
- 策略规则（line 264-385，~120 行）：`classify_setup`, `gen_exit_signal`, `calc_position_size`
- 核心引擎（line 386-853，**~470 行**）：`backtest_one_symbol`
- 报表（line 854-1010，~155 行）：`calc_monthly_stats`, `build_portfolio_equity`, `select_low_vol_symbols`
- CLI（line 1013-1099，~85 行）：`main`

### 1.6 已知的外部依赖

- `cta/run.sh` 调用 `python -m cta.model.model_pipeline`（3 种用法：top-N、--pool、--group-pool、--use-portfolio-logic-runtime）— **module 路径必须保留**
- 跨模块 import：
  - `model_pipeline` 导入 `pipeline_oot_evaluation._evaluate_oot_real_execution`
  - `baseline_skill_suite.generate_candidate_opportunities` 被 `model_pipeline`、`candidate_training_dataset`、`config/model_oot_eval_config.py`、`config/tests/test_stop_loss_pct_consistency.py` 引用
  - `pipeline_oot_evaluation._evaluate_oot_real_execution` 被 `test_backward_compat`, `test_blind_spot_coverage` 引用
- `model_pipeline._private` helper 被 `tests/test_model_pipeline.py`、`tests/test_group_pool_mode.py` 引用（约 20 个符号）— **用户选择改测试，不保留 re-export**

---

## 2. 设计原则

1. **同目录平铺**：新文件落到原源文件所在目录（`cta/model/`, `cta/strategy/`），不开新子目录。符合现有 `pipeline_*.py` 命名习惯（`pipeline_pooling.py`, `pipeline_feature_enrichment.py`, `pipeline_html_report.py`）。
2. **CLI 路径保留**：`python -m cta.model.model_pipeline` 必须继续工作；同理 `baseline_skill_suite` 和 `backtest_price_action_breakout` 也保留薄 shim 入口。
3. **私有 helper 直接破坏**：以 `_` 开头的 helper 拆走后**不** re-export，测试随之改 import。
4. **公共入口保留 re-export**：`run_model_pipeline`、`run_model_pipeline_multi`、`ModelPipelineResult`、`generate_candidate_opportunities`、`prepare_master_feature_frame`、`_evaluate_oot_real_execution` 这 6 个有外部调用者的符号，在原文件里保留 `from new_module import X` 一行 re-export。
5. **业务零改动**：本次只搬代码 + 加 `from __future__ import annotations` + `__all__`，**不调整逻辑**。`run_model_pipeline()` 的 stage 拆解作为独立 wave，单独跑测试。
6. **每个 wave 测试必绿**：每完成一波就跑 `pytest cta/model/tests/ cta/strategy/tests/ -x`，绿了才上下一波。
7. **目标每文件 ≤500 行**：拆完后单文件不超过 500 行（极端情况 800 行，例如 oot 主引擎拆完后的剩余核心）。

---

## 3. 文件改造清单

### 3.1 新建（共 23 个新模块）

#### model_pipeline.py 拆分（→ 11 个新模块）

| 文件 | 用途 | 估算 LOC | 来源 |
|------|------|---------|------|
| `cta/model/pipeline_provenance.py` | sha256/git/provenance 写入 | ~70 | `_sha256_of_file`, `_sha256_of_text`, `_git_commit_short`, `_write_provenance` |
| `cta/model/pipeline_feature_meaning.py` | 特征文档查表 + 缓存 | ~120 | `_load_feature_meaning_map_cached`, `_load_feature_meaning_map`, `_feature_meaning`, `_FEATURE_MEANING_FALLBACK`, `FEATURES_DOC_PATH` |
| `cta/model/pipeline_feature_curation.py` | 因果 manifest + leakage 过滤 + `_safe_name` | ~210 | `_load_causality_manifest`, `_apply_causality_manifest_filter`, `_list_unaudited_features`, `_filter_model_leakage_features`, `_safe_name`, `CAUSALITY_MANIFEST_PATH` |
| `cta/model/pipeline_symbol_ranking.py` | Symbol ranking csv 加载 + 分组 + exchange 解析 | ~200 | `_load_top_n_symbols_from_ranking`, `_load_symbol_groups_from_ranking`, `_resolve_run_exchange`, `SYMBOLS_RANKING_PATH` |
| `cta/model/pipeline_param_grids.py` | 3 个模型的调参网格 + best trial 选择 + MFE/MAE skip 逻辑 | ~430 | `_trade_filter_param_grid`, `_regime_classifier_param_grid`, `_mfe_mae_param_grid`, `_select_best_param_trial`, `_auc_gap`, `_safe_float`, `_tune_trade_filter_model`, `_tune_regime_classifier_model`, `_tune_mfe_mae_model`, `_train_mfe_mae_or_skip`, `MFE_MAE_KIND_SKIPPED_NO_EXEC` |
| `cta/model/pipeline_splits.py` | 时间切分 + walk-forward 窗口 + interval 归一 | ~180 | `_time_split`, `_build_walk_forward_windows`, `_normalize_intervals`, `_WalkForwardWindow`, `WindowMode` |
| `cta/model/pipeline_dataset_prep.py` | 数据集装配（candidate 表、列对齐、特征列选择、pool 包装） | ~470 | `_build_synthetic_candidate`, `_build_candidate_table`, `_ensure_training_columns`, `_select_feature_columns`, `_is_numeric_like_column`, `_ensure_binary_label_diversity`, `_resolve_generic_columns`, `GenericMode`，外加 `_build_pooled_feature_df` 转调 `pipeline_pooling` 现有实现 |
| `cta/model/pipeline_diagnostics.py` | Alerts + feature importance + decile + null/IC 统计 + cluster sample weight | ~520 | `_build_valid_test_gap_alerts`, `_build_top_feature_concentration_alerts`, `_tag_top_feature_importance`, `_dump_feature_manifest`, `_log_top_feature_importance`, `_final_model_importance_df`, `_empty_top_feature_importance_frame`, `_format_ts`, `_build_split_span`, `_compute_feature_null_stats`, `_compute_feature_ic_stats`, `_build_last_oot_decile_table`, `_build_symbol_cluster_sample_weight` |
| `cta/model/pipeline_meta_features.py` | Final decision 特征工程 + 止损一致性校验 | ~110 | `_regime_to_code`, `_build_final_decision_features`, `_validate_stop_loss_pct_consistency`, `_default_label_stop_loss_pct` |
| `cta/model/pipeline_orchestrator.py` | `run_model_pipeline()` 拆成命名 stage + `run_model_pipeline_multi` + `ModelPipelineResult` | ~1000 | 当前 `run_model_pipeline` (1326 行) + `run_model_pipeline_multi` |
| `cta/model/pipeline_cli.py` | `_parse_args` + `main` + 3 个分发分支 | ~350 | 当前 `_parse_args` + `main` |

#### pipeline_oot_evaluation.py 拆分（→ 3 个新模块）

| 文件 | 用途 | 估算 LOC | 来源 |
|------|------|---------|------|
| `cta/model/oot_metrics.py` | OOT 计算工具：drawdown、roll cost、roll dates | ~80 | `_max_drawdown_from_return_series`, `_count_roll_dates_between`, `_calc_roll_cost` |
| `cta/model/oot_intrabar.py` | Intrabar 路径上的退出模拟 + bar cache | ~220 | `_IntrabarBarCache`, `_simulate_intrabar_exit` |
| `cta/model/oot_position_lifetime.py` | 仓位聚合（开仓→平仓→统计行） | ~85 | `_build_position_lifetime_table` |

拆走后 `pipeline_oot_evaluation.py` 只留 `_evaluate_oot_real_execution` 主引擎（~1410 行）。本次先把工具类抽走，主引擎保持原状，**作为 follow-up 在后续 Wave 进一步分 stage（不在本次范围）**。

#### baseline_skill_suite.py 拆分（→ 5 个新模块）

| 文件 | 用途 | 估算 LOC | 来源 |
|------|------|---------|------|
| `cta/strategy/baseline_helpers.py` | 工具：interval 归一、type coerce、ATR、side filter、order builder | ~140 | `_normalize_intervals`, `_load_top_n_symbols_from_ranking`, `_resolve_run_exchange`, `_safe_float`, `_safe_bool`, `_compute_atr14`, `_side_allowed`, `_entry_order`, `BaselineSuiteRunResult` |
| `cta/strategy/baseline_feature_frame.py` | `prepare_master_feature_frame`（16 套特征） | ~135 | `prepare_master_feature_frame` |
| `cta/strategy/baseline_strategies.py` | 3 个策略类 + factory | ~220 | `DonchianBaselineStrategy`, `ATRBreakoutBaselineStrategy`, `BreakoutPullbackBaselineStrategy`, `create_baseline_strategy` |
| `cta/strategy/baseline_candidate_gen.py` | Setup 检测 + 执行路径模拟 + 主入口 `generate_candidate_opportunities` + 训练样本构建 | ~520 | `build_training_samples_from_trade_log`, `_infer_regime_label`, `_resolve_candidate_entry`, `_simulate_candidate_execution_path`, `_build_raw_setup_candidates`, `generate_candidate_opportunities` |
| `cta/strategy/baseline_backtest_cli.py` | Backtest harness + CLI | ~285 | `_compute_metrics`, `run_baseline_suite`, `run_baseline_suite_multi`, `_parse_args`, `main` |

#### backtest_price_action_breakout.py 拆分（→ 4 个新模块）

| 文件 | 用途 | 估算 LOC | 来源 |
|------|------|---------|------|
| `cta/strategy/price_action_breakout_indicators.py` | 工具 + 指标 + 数据加载 | ~150 | `ensure_dir`, `safe_div`, `parse_exchange`, `get_contract_meta`, `bars_to_df`, `load_daily_data`, `add_indicators`, `calc_annualized_vol`, `round_money`, `pct_str` |
| `cta/strategy/price_action_breakout_rules.py` | 策略规则：setup 分类、退出信号、仓位 sizing | ~125 | `classify_setup`, `gen_exit_signal`, `calc_position_size`, `TradeRecord` |
| `cta/strategy/price_action_breakout_engine.py` | 核心 backtest 引擎（单 symbol） | ~470 | `backtest_one_symbol` |
| `cta/strategy/price_action_breakout_report.py` | 报表 + 组合：月度统计、组合 equity、低波动筛选 | ~160 | `calc_monthly_stats`, `build_portfolio_equity`, `select_low_vol_symbols` |

拆走后 `backtest_price_action_breakout.py` 只剩 `main()` CLI（~85 行）+ re-export shim。

### 3.2 修改的现有文件

| 文件 | 修改点 |
|------|--------|
| `cta/model/model_pipeline.py` | 砍到 ≤30 行：`from cta.model.pipeline_cli import main` + 6 个公共符号 re-export + `if __name__ == "__main__": main()` |
| `cta/model/pipeline_oot_evaluation.py` | 删除 `_max_drawdown_from_return_series` / `_count_roll_dates_between` / `_calc_roll_cost` / `_IntrabarBarCache` / `_simulate_intrabar_exit` / `_build_position_lifetime_table`，改为从新模块 import；主引擎保留 |
| `cta/strategy/baseline_skill_suite.py` | 砍到 ≤30 行：re-export `generate_candidate_opportunities`, `prepare_master_feature_frame`, `BaselineSuiteRunResult`, `run_baseline_suite`, `run_baseline_suite_multi`，CLI shim |
| `cta/strategy/backtest_price_action_breakout.py` | 砍到 ≤30 行：`from cta.strategy.price_action_breakout_engine import backtest_one_symbol` 等 + `if __name__ == "__main__": main()` |
| `cta/model/tests/test_model_pipeline.py` | **删除**，所有 test 拆到对应新文件（见 §4） |
| `cta/model/tests/test_group_pool_mode.py` | 私有 helper 导入改为 `from cta.model.pipeline_feature_curation import _safe_name` |
| `cta/model/tests/test_backward_compat.py` | `from cta.model.pipeline_oot_evaluation import _evaluate_oot_real_execution` 保持（主引擎仍在原文件） |
| `cta/model/tests/test_blind_spot_coverage.py` | 同上保持 |
| `cta/strategy/tests/test_baseline_skill_suite.py` | 拆成多个 test 文件（见 §4.2） |
| `cta/model/pipeline_pooling.py` | 当前接受 `_build_candidate_table` 等回调，将 import 路径从 `model_pipeline` 改为新模块（如果存在直接 import） |
| `cta/model/feature/candidate_training_dataset.py` | `from cta.strategy.baseline_skill_suite import generate_candidate_opportunities` — 保留旧路径靠 re-export，不改 |
| `cta/config/model_oot_eval_config.py` | 同上，import 路径不变（依赖 re-export） |

### 3.3 不修改

- `cta/run.sh` — 因为保留了 CLI 入口的 module 路径
- `cta/model/__init__.py` — 不加包级 re-export，符合现状
- `cta/portfolio_logic/`, `cta/config/`, `cta/data_code/`, `cta/feature/` 下任何文件（本次范围外）

---

## 4. 测试拆分

### 4.1 test_model_pipeline.py (2390 行) 拆分

按 test 函数名 grep 后归属到新文件：

| 新测试文件 | 包含的 test 函数（来自原 test_model_pipeline.py） |
|---|---|
| `tests/test_pipeline_param_grids.py` | `test_select_best_param_trial_*`, `test_train_mfe_mae_or_skip_*` |
| `tests/test_pipeline_meta_features.py` | `test_validate_stop_loss_pct_*` |
| `tests/test_pipeline_splits.py` | `test_build_walk_forward_windows_*`, `test_walk_forward_windows_*`, `test_normalize_intervals_*` |
| `tests/test_pipeline_dataset_prep.py` | `test_build_candidate_table_*`, `test_training_columns_*`, `test_select_feature_columns_*`, `test_select_feature_fallback_*`, `test_ensure_binary_label_diversity_*`, `test_pool_mode_*`, `test_auto_enrich_candidate_features_*`, `test_pipeline_auto_enriches_features_*` |
| `tests/test_pipeline_diagnostics.py` | `test_build_top_feature_concentration_alerts_*`, `test_build_valid_test_gap_alerts_*`, `test_build_last_oot_decile_table_*`, `test_build_symbol_cluster_sample_weight_*`, `test_pipeline_writes_feature_manifest_*`, `test_feature_manifest_*` |
| `tests/test_pipeline_feature_curation.py` | `test_filter_model_leakage_features_*`, `test_apply_causality_manifest_filter_*`, 新增 `test_safe_name_*` |
| `tests/test_pipeline_feature_meaning.py` | `test_feature_meaning_refreshes_when_features_doc_mtime_changes` |
| `tests/test_pipeline_symbol_ranking.py` | `test_load_top_n_symbols_from_ranking_*`, `test_resolve_run_exchange_*` |
| `tests/test_pipeline_cli.py` | `test_parse_args_*`，新增 `test_dispatch_branches_pass_all_runtime_kwargs`（参数化测 group-pool/pool/single 都正确传递 `oot_eval_config`） |
| `tests/test_pipeline_orchestrator.py` | `test_run_pipeline_smoke`, `test_run_pipeline_support_all_intervals`, `test_run_pipeline_by_signal_type_walk_forward`, `test_pipeline_writes_calibration_joblib_files`, `test_run_model_pipeline_multi_*`, `test_run_pipeline_pred_split_is_test_only`, `test_run_model_pipeline_writes_provenance_json`, `test_run_model_pipeline_outputs_final_decision_model_naming`, `test_pipeline_includes_extra_generic_features_*`, `test_pipeline_whitelist_mode_caps_generic_at_18_columns` |
| `tests/test_pipeline_oot_evaluation.py`（新建） | `test_evaluate_oot_real_execution_*`, `test_build_position_lifetime_table_*` |

拆完后 `tests/test_model_pipeline.py` **删除**。

### 4.2 test_baseline_skill_suite.py (652 行) 拆分

| 新测试文件 | 来源 |
|---|---|
| `cta/strategy/tests/test_baseline_helpers.py` | `_normalize_intervals`, `_compute_atr14`, `_side_allowed`, `_entry_order` 相关测试 |
| `cta/strategy/tests/test_baseline_feature_frame.py` | `prepare_master_feature_frame` 相关测试 |
| `cta/strategy/tests/test_baseline_strategies.py` | 3 个策略类 on_bar 相关测试 |
| `cta/strategy/tests/test_baseline_candidate_gen.py` | `generate_candidate_opportunities` end-to-end 测试、`_resolve_candidate_entry`、`_simulate_candidate_execution_path` |
| `cta/strategy/tests/test_baseline_backtest_cli.py` | `run_baseline_suite`、`_compute_metrics`、`_parse_args` |

### 4.3 backtest_price_action_breakout 测试

当前无独立测试文件。**本次不强制新增测试**（搬运不改逻辑），但为新模块提供至少 1 个 smoke test：

- `cta/strategy/tests/test_price_action_breakout_engine.py`：用合成数据跑 `backtest_one_symbol` 30 天，断言不报错且返回 TradeRecord 列表非空

### 4.4 test_model_oot_eval_config.py、test_oot_sim_parity.py

保持不动，import `_evaluate_oot_real_execution` 路径仍是 `cta.model.pipeline_oot_evaluation`（主引擎没搬走）。

---

## 5. `run_model_pipeline()` 的 stage 拆解（Wave 6）

当前 1326 行单函数。**Wave 6 改造为命名 stage 函数**（不引入 class，因为没有跨 stage 可变状态值得封装）。

```python
# pipeline_orchestrator.py

@dataclass(frozen=True)
class _PipelineContext:
    """Run-wide invariants (paths, naming, configs)."""
    run_date: str
    sym: str
    ex: str
    interval_norm: str
    trade_side_mode: str
    out_dir: Path
    rng_seed: int
    by_signal_type: bool
    max_walk_forward_windows: int
    window_mode: WindowMode
    rolling_train_years: int
    rolling_valid_years: int
    rolling_test_years: int
    rolling_step_years: int
    max_auc_gap: float
    max_valid_test_gap: float
    top_feature_importance_alert_pct: float
    generic_mode: GenericMode
    oot_eval_config: OotEvaluationConfig
    train_end: str
    valid_end: str
    min_used_symbols: int
    feature_root: Path

@dataclass(frozen=True)
class _WindowResult:
    """Per-walk-forward-window outputs."""
    window_id: int
    signal_type_key: str
    trade_filter_model: Any
    regime_classifier_model: Any
    mfe_mae_model: Any
    final_decision_model: Any
    selected_features: dict[str, list[str]]
    saved_paths: dict[str, Path]
    metrics_rows: list[dict]
    prediction_df: pd.DataFrame
    top_feature_importance_df: pd.DataFrame

def run_model_pipeline(...) -> ModelPipelineResult:
    ctx = _build_context(...)
    candidate_df, ex, pool_meta_path = _stage_build_candidates(ctx, pool_symbols, ...)
    feature_df = _stage_merge_features(ctx, candidate_df, pool_symbols is not None)
    feature_df = _stage_drop_warmup(feature_df)
    feature_df, feature_paths = _stage_persist_inputs(ctx, candidate_df, feature_df)
    signal_frames = _stage_split_by_signal_type(ctx, feature_df)

    walk_results: list[_WindowResult] = []
    for signal_type_key, sig_df in signal_frames:
        windows = _build_walk_forward_windows(sig_df, ...)
        for win in windows:
            wr = _stage_train_window(ctx, signal_type_key, win)
            wr = _stage_save_models_and_calibrators(ctx, win, wr)
            wr = _stage_dump_feature_manifests(ctx, win, wr)
            wr = _stage_compute_split_metrics(ctx, win, wr)
            wr = _stage_build_predictions(ctx, win, wr)
            walk_results.append(wr)

    metrics_df, prediction_df, top_feat_df = _stage_concat_results(walk_results)
    _stage_persist_metrics(ctx, metrics_df, prediction_df, top_feat_df, feature_paths)
    _stage_write_provenance(ctx, feature_paths, metrics_path, prediction_path, top_feat_path)
    alert_paths = _stage_emit_alerts(ctx, metrics_df, top_feat_df, feature_df)
    oot_outputs = _stage_run_oot_evaluation(ctx, prediction_df)
    report_path = _stage_render_markdown_report(ctx, ...)
    return ModelPipelineResult(...)
```

每个 `_stage_*` 函数目标 ≤150 行；签名是 `(ctx, ...) → tuple|dataclass`。

**为什么不用 class？** 没有跨 stage 的可变状态需要封装；`_PipelineContext`（frozen）+ `_WindowResult`（frozen）已经表达了所有 stage 间数据流。class 在这里只是命名空间，module 已经是命名空间。

---

## 6. CLI 入口设计

```python
# cta/model/pipeline_cli.py

def _parse_args(argv): ...  # 原样搬

def _build_oot_config(args) -> OotEvaluationConfig:
    """构造 effective OotEvaluationConfig（处理 use_portfolio_logic_runtime）."""
    if bool(getattr(args, "use_portfolio_logic_runtime", False)):
        return dc_replace(DEFAULT_OOT_EVAL_CONFIG, use_portfolio_logic_runtime=True)
    return DEFAULT_OOT_EVAL_CONFIG

def _resolve_symbols(args) -> list[tuple[str, str | None]]:
    """top-N or single fallback."""

def _run_group_pool(args, intervals, output_root, oot_cfg) -> None: ...
def _run_pool(args, intervals, symbols, output_root, oot_cfg) -> None: ...
def _run_per_symbol(args, intervals, symbols, output_root, oot_cfg) -> None: ...

def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO)
    _validate_stop_loss_pct_consistency()
    seed_all_from_env("CTA_GLOBAL_SEED")
    args = _parse_args(argv)
    intervals = _normalize_intervals(args.interval)
    output_root = Path(args.output_root).resolve() if args.output_root else None
    oot_cfg = _build_oot_config(args)

    if args.group_pool:
        return _run_group_pool(args, intervals, output_root, oot_cfg)
    symbols = _resolve_symbols(args)
    if args.pool:
        return _run_pool(args, intervals, symbols, output_root, oot_cfg)
    return _run_per_symbol(args, intervals, symbols, output_root, oot_cfg)
```

3 个分支函数不抽象为 class — 它们零共享状态，只共享 `args`，写成 class 是 ceremony。group-pool 分支里的 `registry_payload` JSON 写盘逻辑保留在 `_run_group_pool` 内。

---

## 7. Wave 拆分（落地顺序）

每个 wave 一个 PR；wave 之间不能并行（互相依赖 import 路径）。每 wave 完成后跑 `pytest cta/ -x` 确保绿。

| Wave | 内容 | 测试影响 |
|------|------|---------|
| W1 | 叶节点工具：`pipeline_provenance`, `pipeline_feature_meaning`, `pipeline_feature_curation` | `tests/test_pipeline_feature_curation.py`, `test_pipeline_feature_meaning.py` 新建并搬测试；原 test_model_pipeline.py 对应测试删除 |
| W2 | 调参 + split + meta：`pipeline_param_grids`, `pipeline_splits`, `pipeline_meta_features` | `tests/test_pipeline_param_grids.py`, `test_pipeline_splits.py`, `test_pipeline_meta_features.py` 新建 |
| W3 | Symbol ranking + dataset prep + diagnostics：`pipeline_symbol_ranking`, `pipeline_dataset_prep`, `pipeline_diagnostics` | `tests/test_pipeline_symbol_ranking.py`, `test_pipeline_dataset_prep.py`, `test_pipeline_diagnostics.py` 新建。修 `test_group_pool_mode.py` 的 `_safe_name` import |
| W4 | Orchestrator 整体搬：`pipeline_orchestrator.py` 含 `run_model_pipeline` 原样 + `run_model_pipeline_multi` + `ModelPipelineResult` | `tests/test_pipeline_orchestrator.py` 新建并搬 run_pipeline_smoke 等大块测试 |
| W5 | CLI 抽离：`pipeline_cli.py` 含 `_parse_args` + `main` + 3 分支函数；`model_pipeline.py` 砍到 shim ≤30 行 | `tests/test_pipeline_cli.py` 新建；删除 `tests/test_model_pipeline.py`（彻底空了） |
| W6 | `run_model_pipeline()` 拆 stage（§5）；不改公开 API | `tests/test_pipeline_orchestrator.py` 是保险网，预期不用动 |
| W7 | OOT 工具抽离：`oot_metrics.py`, `oot_intrabar.py`, `oot_position_lifetime.py`；`pipeline_oot_evaluation.py` 留主引擎 | 修 `test_blind_spot_coverage.py`、`test_backward_compat.py`（如有用到子函数） |
| W8 | baseline_skill_suite 拆 5 模块；保留 `baseline_skill_suite.py` 为 re-export shim | 拆 `test_baseline_skill_suite.py` 为 5 个新文件；下游 import（candidate_training_dataset 等）靠 re-export 不改 |
| W9 | backtest_price_action_breakout 拆 4 模块;保留主 `backtest_price_action_breakout.py` 为 CLI shim | 新增 `test_price_action_breakout_engine.py` smoke test |

**关键依赖链**：
- W1-W3 互相独立（叶节点工具）
- W4 依赖 W1-W3 完成（orchestrator import 新模块）
- W5 依赖 W4
- W6 依赖 W5（orchestrator 已独立成文件才好拆 stage）
- W7-W9 独立于 model_pipeline.py 主线，可在 W4 之后并行启动

---

## 8. 风险与回退

| 风险 | 应对 |
|------|------|
| **循环 import**：`pipeline_dataset_prep` 调用 `pipeline_pooling._build_pooled_feature_df`，而 `pipeline_pooling` 当前接受 `_build_candidate_table` 等回调 | 保留现有依赖注入模式（pooling 接 callable，不 import module）。`pipeline_dataset_prep` 文件底部再 import `pipeline_pooling`，避免顶层循环 |
| **路径常量分裂**：`FEATURES_DOC_PATH`、`CAUSALITY_MANIFEST_PATH`、`SYMBOLS_RANKING_PATH` 三个常量散到不同模块易漂移 | 每个常量只在拥有其消费者的模块定义一次；其它模块 from 那里 import。例：`pipeline_provenance` 写 provenance 时 `from cta.model.pipeline_feature_meaning import FEATURES_DOC_PATH` |
| **OOT 配置传递错路**：`effective_oot_cfg` 在 3 个 dispatch 分支里穿透；任一分支漏传 `use_portfolio_logic_runtime=True` 都会被静默吃掉 | W5 新增 `test_dispatch_branches_pass_all_runtime_kwargs` 参数化测试，mock `pipeline_orchestrator.run_model_pipeline`，断言所有分支传到的 kwargs 一致（除 pool-specific 字段） |
| **CLI flag drift**：W5 cut-paste main → 3 分支函数时可能丢 kwarg | 同上参数化测试 + 原有 `run/tests/test_docs_sync.py` 兜底（每个 CLI flag 文档同步） |
| **`_safe_name` 重复定义**：被 `pipeline_symbol_ranking`、`pipeline_cli`、`test_group_pool_mode.py` 三处用 | 只在 `pipeline_feature_curation.py` 定义一次；其它三处 import。锁语义：`tests/test_pipeline_feature_curation.py::test_safe_name_unicode_and_punctuation` |
| **`pipeline_oot_evaluation` 主引擎太大未拆**：1410 行 `_evaluate_oot_real_execution` 仍是 god function | 本次仅 W7 抽工具；主引擎留作后续 Wave（不在本次 plan 范围）。文档里明示 follow-up |
| **CLI 入口路径破坏**：`python -m cta.model.model_pipeline` 在 run.sh 用了 4 次 | model_pipeline.py 保留为 30 行 shim：`from cta.model.pipeline_cli import main` + `if __name__ == "__main__": main()`。同理 baseline_skill_suite.py / backtest_price_action_breakout.py |
| **回退**：某 wave 出问题 | 每 wave 一个 git commit/PR；revert 单 wave 即回到上一稳态。模块文件可直接 `git rm` 恢复 |

---

## 9. 给 codex 的执行指引

1. **不修改 vnpy 主仓代码**：所有改动在 `cta/` 下
2. **每 wave 一次 PR**，按 §7 顺序；用户工作流要求 feature 分支开发、**禁止自动 commit**，每 wave 收尾让人工 review
3. **代码风格**：参考现有 `cta/model/pipeline_pooling.py`、`cta/model/pipeline_html_report.py` 的文件头注释、`from __future__ import annotations`、`__all__` 写法
4. **不引入新依赖**
5. **测试 framework**：unittest 风格，与源文件同级 `tests/` 目录
6. **import 调整原则**：拆文件时**只搬不改**。若发现现有函数有 bug，单独提 issue 不在本次范围
7. **W4-W5 之间必跑 smoke**：`python -m cta.model.model_pipeline --symbol RB0 --interval 60min --start 2010-01-01 --end 2012-12-31 --max-walk-forward-windows 1 --output-root /tmp/refactor_smoke`，输出 `report.md` 非空
8. **W4 前先 git stash 一份 `--help` 输出**作为基线，W5 完成后比较 byte-identical
9. **`__all__` 写法**：每个新模块顶部声明，列公开符号
10. **`from __future__ import annotations`**：每个新文件首行加，避免 forward reference 问题

---

## 10. 验收清单

- [ ] `cta/model/model_pipeline.py` 行数 ≤ 30
- [ ] `cta/model/pipeline_oot_evaluation.py` 行数 ≤ 1500（主引擎仍在，工具抽走 ~400 行）
- [ ] `cta/strategy/baseline_skill_suite.py` 行数 ≤ 30
- [ ] `cta/strategy/backtest_price_action_breakout.py` 行数 ≤ 50（保留 main + shim）
- [ ] 所有新模块 LOC ≤ 600（极端情况下 `pipeline_orchestrator.py` 可到 ~1000，但 stage 拆完后单 stage ≤150）
- [ ] `pytest cta/ -x` 全绿
- [ ] `python -m cta.model.model_pipeline --help` 输出与 W1 前 baseline byte-identical
- [ ] `bash cta/run.sh` 全部步骤跑通（data/validate/feature/candidate/train/oot 全链路）
- [ ] 私有 helper 不再从 `model_pipeline` shim 导入（全仓搜索应无该类导入）
- [ ] `grep -r "from cta.strategy.baseline_skill_suite import" cta/` 命中的符号只剩 `generate_candidate_opportunities`, `prepare_master_feature_frame`, `BaselineSuiteRunResult`, `run_baseline_suite`, `run_baseline_suite_multi`
- [ ] `tests/test_model_pipeline.py` 已删除
- [ ] `tests/test_pipeline_*.py` 至少 10 个文件存在
