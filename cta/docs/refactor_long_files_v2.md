# CTA 全量长文件拆分重构方案 v2

---

## 0. Context（v1 → v2 变更）

### v1 实施现状（codex 已动过的部分）

codex 按 v1 方案做了 wave 1-5、8、9，但实际效果：

- ✅ `cta/model/model_pipeline.py`：3868 → 16 行 shim
- ✅ `cta/strategy/baseline_skill_suite.py`：1578 → 126 行 shim
- ✅ `cta/strategy/backtest_price_action_breakout.py`：1099 → 86 行 shim
- ✅ 抽出 `oot_metrics.py` (80) / `oot_intrabar.py` (217) / `oot_position_lifetime.py` (81)
- ⚠️ **回归 1**：新建 `cta/model/pipeline_orchestrator.py` **4168 行**（比原 god 文件还大）— codex 没做 W1-W3 helper 抽取，把 model_pipeline.py 几乎原样搬过去
- ⚠️ **回归 2**：新建 `cta/strategy/baseline_candidate_gen.py` **634 行** —  一出生就违反 ≤500
- ⚠️ **未完成**：`pipeline_oot_evaluation.py` 仍 1489 行（主引擎 `_evaluate_oot_real_execution` 没拆）
- ⚠️ **未完成**：`tests/test_model_pipeline.py` 仍 2586 行（v1 §4.1 测试拆分没执行）

### v2 范围扩展（用户新约束）

**硬约束**：
1. **所有 >500 行非测试 .py 必须重构**（v1 只覆盖 >1000 行）
2. **所有新生成的 .py 必须 ≤500 行**（包括测试文件）
3. **业务零改动**：只搬不改

**v2 新增范围**（v1 未覆盖的 >500 行文件）：

| 文件 | 当前行数 | 责任域 |
|---|---|---|
| `cta/model/feature/candidate_training_dataset.py` | 992 | 候选样本→训练集标准化 |
| `cta/feature/price_action.py` | 946 | Al Brooks K 线分类 / 趋势 / 反转 |
| `cta/feature/price_action_context.py` | 935 | 市场结构 / 回调质量 / 突破质量 |
| `cta/data_code/download_all.py` | 913 | 批量数据下载主控 |
| `cta/feature/run_all_features.py` | 877 | 特征批量构建 |
| `cta/feature/price_action_advanced.py` | 767 | leg microstructure / pullback pattern |
| `cta/data_code/futures_downloader.py` | 641 | tushare 期货下载 API |

**v2 还需修复 v1 的遗留**：

| 文件 | 当前行数 | 必须降到 |
|---|---|---|
| `cta/model/pipeline_orchestrator.py` | 4168 | ≤500 行 × N 模块 |
| `cta/model/pipeline_oot_evaluation.py` | 1489 | ≤500 行 × N 模块 |
| `cta/strategy/baseline_candidate_gen.py` | 634 | ≤500 行 × 2 模块 |
| `cta/model/tests/test_model_pipeline.py` | 2586 | 拆 11 个 ≤500 行测试文件 |
| `cta/strategy/tests/test_baseline_skill_suite.py` | 652 | 拆 5 个测试文件（v1 §4.2 已规划）|
| `cta/model/feature/tests/test_candidate_training_dataset.py` | 581 | 拆 2-3 个测试文件 |

---

## 1. 设计原则

1. **硬限 ≤500 行**：任何 .py（含 `__init__.py` 与 tests）超 500 行视为 bug。新增 CI 检查（见 §6.4）。
2. **同目录平铺**：新文件放原源文件目录，不开新子目录。沿用现有 `pipeline_*` / `oot_*` / `baseline_*` / `price_action_*` 命名风格。
3. **公共入口保留 re-export**：已有 shim 的入口文件（`model_pipeline.py` / `baseline_skill_suite.py` / `backtest_price_action_breakout.py`）继续做 re-export，**不改文件名**。
4. **私有 helper 直接破坏**：以 `_` 开头的 helper 拆走后不 re-export。
5. **业务零改动**：W7 (`run_model_pipeline()` stage 拆解) 必须 bit-identical（hash 比对 .joblib / .csv / .json）；其它 wave 是纯搬运。
6. **每个 wave 测试必绿**：`pytest cta/ -x` 全绿才上下一波。
7. **size budget 优先**：单模块 ≤500 行内的前提下，可适度合并相关功能避免过度碎片化。理想分布 200-400 行/模块。

---

## 2. As-Is 文件结构清单（codex 改动后）

### 2.1 待拆文件（13 个，所有 >500 行）

#### 优先级 P0 — 修 codex 遗留（4 个文件）

| # | 文件 | 当前行数 | 目标模块数 |
|---|------|---------|-----------|
| 1 | `cta/model/pipeline_orchestrator.py` | 4168 | 拆 11-12 个 |
| 2 | `cta/model/pipeline_oot_evaluation.py` | 1489 | 拆 4-5 个 |
| 3 | `cta/strategy/baseline_candidate_gen.py` | 634 | 拆 2 个 |
| 4 | `cta/model/tests/test_model_pipeline.py` | 2586 | 拆 11 个测试文件 |

#### 优先级 P1 — v2 新增范围（7 个文件）

| # | 文件 | 当前行数 | 目标模块数 |
|---|------|---------|-----------|
| 5 | `cta/model/feature/candidate_training_dataset.py` | 992 | 拆 3 个 |
| 6 | `cta/feature/price_action.py` | 946 | 拆 3 个 |
| 7 | `cta/feature/price_action_context.py` | 935 | 拆 3 个 |
| 8 | `cta/data_code/download_all.py` | 913 | 拆 3 个 |
| 9 | `cta/feature/run_all_features.py` | 877 | 拆 2-3 个 |
| 10 | `cta/feature/price_action_advanced.py` | 767 | 拆 2 个 |
| 11 | `cta/data_code/futures_downloader.py` | 641 | 拆 2 个 |

#### 优先级 P2 — 测试拆分（2 个文件，v1 已规划但未做）

| # | 文件 | 当前行数 | 目标模块数 |
|---|------|---------|-----------|
| 12 | `cta/strategy/tests/test_baseline_skill_suite.py` | 652 | 拆 5 个（v1 §4.2 蓝图）|
| 13 | `cta/model/feature/tests/test_candidate_training_dataset.py` | 581 | 拆 2-3 个 |

---

## 3. Phase A：修 codex 遗留

### 3.1 A1：`pipeline_orchestrator.py` 4168 → 12 个 ≤500 行模块

codex 当前把整个 `model_pipeline.py` 搬到 `pipeline_orchestrator.py` 但没做内部拆分。沿用 v1 §3.1 的拆分蓝图，并新增 stage 拆解：

| 新模块（cta/model/）| 用途 | 估算 LOC | 来源（在 pipeline_orchestrator.py 中）|
|---|---|---|---|
| `pipeline_provenance.py` | sha256/git/provenance | ~70 | `_sha256_of_file`, `_sha256_of_text`, `_git_commit_short`, `_write_provenance` |
| `pipeline_feature_meaning.py` | feature 文档查表 + 缓存 | ~120 | `_load_feature_meaning_map*`, `_feature_meaning`, `FEATURES_DOC_PATH` |
| `pipeline_feature_curation.py` | causality + leakage 过滤 + `_safe_name` | ~210 | `_load_causality_manifest`, `_apply_causality_manifest_filter`, `_list_unaudited_features`, `_filter_model_leakage_features`, `_safe_name`, `CAUSALITY_MANIFEST_PATH` |
| `pipeline_symbol_ranking.py` | ranking csv + 分组 + exchange | ~200 | `_load_top_n_symbols_from_ranking`, `_load_symbol_groups_from_ranking`, `_resolve_run_exchange`, `SYMBOLS_RANKING_PATH` |
| `pipeline_param_grids.py` | 3 模型调参网格 + best trial 选择 | ~430 | `_trade_filter_param_grid`, `_regime_classifier_param_grid`, `_mfe_mae_param_grid`, `_select_best_param_trial`, `_auc_gap`, `_safe_float`, `_tune_*_model`, `_train_mfe_mae_or_skip`, `MFE_MAE_KIND_SKIPPED_NO_EXEC` |
| `pipeline_splits.py` | 时间切分 + walk-forward | ~180 | `_time_split`, `_build_walk_forward_windows`, `_normalize_intervals`, `_WalkForwardWindow`, `WindowMode` |
| `pipeline_dataset_prep.py` | 数据集装配 | ~470 | `_build_synthetic_candidate`, `_build_candidate_table`, `_ensure_training_columns`, `_select_feature_columns`, `_is_numeric_like_column`, `_ensure_binary_label_diversity`, `_resolve_generic_columns`, `GenericMode` |
| `pipeline_diagnostics.py` | alerts + decile + null/IC + cluster weight | ~490 | `_build_valid_test_gap_alerts`, `_build_top_feature_concentration_alerts`, `_tag_top_feature_importance`, `_dump_feature_manifest`, `_log_top_feature_importance`, `_final_model_importance_df`, `_empty_top_feature_importance_frame`, `_format_ts`, `_build_split_span`, `_compute_feature_null_stats`, `_compute_feature_ic_stats`, `_build_last_oot_decile_table`, `_build_symbol_cluster_sample_weight` |
| `pipeline_meta_features.py` | final decision 特征 + 止损一致性校验 | ~110 | `_regime_to_code`, `_build_final_decision_features`, `_validate_stop_loss_pct_consistency`, `_default_label_stop_loss_pct` |
| `pipeline_stages.py` | `run_model_pipeline` 拆出来的 stage 函数 | ~480 | 10-12 个 `_stage_*` 函数（见 §3.2）|
| `pipeline_cli.py` | `_parse_args` + `main` + 3 个 dispatch 分支 | ~350 | 现有 `_parse_args`、`main`、group-pool/pool/single 分发 |
| `pipeline_orchestrator.py`（保留） | 仅 `_PipelineContext` + `_WindowResult` + `ModelPipelineResult` + `run_model_pipeline()` 串联 stage + `run_model_pipeline_multi()` | ~280 | 拆解后只剩串联骨架 |

**预算核对**：12 个模块累计估算 ≈ 3390 行；codex 的 4168 行扣去 import / `__all__` / docstring / 空行约 200 行后大约对得上。每个模块都 <500，最大的 `pipeline_param_grids.py` 430 行。

### 3.2 A1 续：`run_model_pipeline()` 的 stage 拆解（≤500 硬约束下的拆法）

v1 §5 提出把 1326 行单函数拆 ~14 个 stage。在 ≤500 硬约束下：

```python
# cta/model/pipeline_stages.py（~480 行）

@dataclass(frozen=True)
class _PipelineContext: ...   # 25 行
@dataclass(frozen=True)
class _WindowResult: ...      # 15 行

# 总共 12 个 stage，平均 ~35 行/stage，最大 stage ≤80 行

def _stage_build_candidates(ctx, pool_symbols, ...) -> tuple[pd.DataFrame, str, Path]: ...     # ~50
def _stage_merge_features(ctx, candidate_df, is_pool) -> pd.DataFrame: ...                     # ~40
def _stage_drop_warmup(ctx, feature_df) -> pd.DataFrame: ...                                   # ~30
def _stage_persist_inputs(ctx, candidate_df, feature_df) -> tuple[pd.DataFrame, dict]: ...     # ~35
def _stage_split_by_signal_type(ctx, feature_df) -> list[tuple[str, pd.DataFrame]]: ...        # ~30
def _stage_train_window(ctx, signal_type_key, win) -> _WindowResult: ...                       # ~80
def _stage_save_models_and_calibrators(ctx, win, wr) -> _WindowResult: ...                     # ~45
def _stage_dump_feature_manifests(ctx, win, wr) -> _WindowResult: ...                          # ~30
def _stage_compute_split_metrics(ctx, win, wr) -> _WindowResult: ...                           # ~50
def _stage_build_predictions(ctx, win, wr) -> _WindowResult: ...                               # ~40
def _stage_concat_results(walk_results) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]: ... # ~25
def _stage_persist_outputs(ctx, metrics_df, prediction_df, top_feat_df, paths) -> None: ...    # ~35
```

`pipeline_orchestrator.py` 收尾后变成 ~280 行串联代码：

```python
# cta/model/pipeline_orchestrator.py（~280 行）

from cta.model.pipeline_stages import _stage_build_candidates, ..., _PipelineContext, _WindowResult

@dataclass(frozen=True)
class ModelPipelineResult: ...  # paths only, ~30 行

def run_model_pipeline(...) -> ModelPipelineResult:
    ctx = _build_context(...)                                           # 调一次
    cand, ex, pool_meta = _stage_build_candidates(ctx, pool_symbols)
    feat = _stage_merge_features(ctx, cand, pool_symbols is not None)
    feat = _stage_drop_warmup(ctx, feat)
    feat, paths = _stage_persist_inputs(ctx, cand, feat)
    signals = _stage_split_by_signal_type(ctx, feat)
    walk = []
    for stk, df in signals:
        for win in _build_walk_forward_windows(df, ...):
            wr = _stage_train_window(ctx, stk, win)
            wr = _stage_save_models_and_calibrators(ctx, win, wr)
            wr = _stage_dump_feature_manifests(ctx, win, wr)
            wr = _stage_compute_split_metrics(ctx, win, wr)
            wr = _stage_build_predictions(ctx, win, wr)
            walk.append(wr)
    metrics, preds, top_feat = _stage_concat_results(walk)
    _stage_persist_outputs(ctx, metrics, preds, top_feat, paths)
    _stage_write_provenance(ctx, paths, ...)
    alerts = _stage_emit_alerts(ctx, metrics, top_feat, feat)
    oot = _stage_run_oot_evaluation(ctx, preds)
    report = _stage_render_markdown_report(ctx, ...)
    return ModelPipelineResult(...)

def run_model_pipeline_multi(...) -> list[ModelPipelineResult]: ...   # ~50 行
```

**bit-identical 验证**（W7 之后强制跑）：

```bash
python -m cta.model.model_pipeline --symbol RB0 --interval 60min --start 2010-01-01 --end 2012-12-31 \
  --max-walk-forward-windows 1 --seed 42 --output-root /tmp/pre_a1
# 完成 A1 后跑同样命令到 /tmp/post_a1
find /tmp/pre_a1 -type f \( -name "*.joblib" -o -name "*.csv" -o -name "*.json" \) | sort | xargs sha256sum > /tmp/pre.sums
find /tmp/post_a1 -type f \( -name "*.joblib" -o -name "*.csv" -o -name "*.json" \) | sort | xargs sha256sum > /tmp/post.sums
diff /tmp/pre.sums /tmp/post.sums   # 只允许文件路径差异，hash 必须一致
```

### 3.3 A2：`pipeline_oot_evaluation.py` 1489 → 4 模块

主引擎 `_evaluate_oot_real_execution` 1410 行是 god function。按 OOT 逻辑流分 stage：

| 新模块（cta/model/）| 用途 | 估算 LOC |
|---|---|---|
| `oot_gates.py` | gate 过滤：trade_filter / regime / mfe_mae / stacking 阈值；HTF gate 桥接；ranker 调用 | ~280 |
| `oot_position_sizing.py` | 仓位 sizing：按 max_single_loss 反推 / max_position_scale / 累计名义金额限额 | ~200 |
| `oot_portfolio_constraints.py` | 组合约束：margin、leverage、daily new notional、weekly/monthly drawdown、cluster cap | ~300 |
| `oot_trade_simulation.py` | 单笔模拟：intrabar 路径 + roll cost + 退出时刻 / 实际成交价 | ~250 |
| `pipeline_oot_evaluation.py`（保留） | `_evaluate_oot_real_execution` 主串联（按 stage 调用 oot_gates → sizing → constraints → simulation → metrics）+ 月度/汇总/逐笔输出 | ~450 |

**stage 串联骨架（pipeline_oot_evaluation.py）**：

```python
def _evaluate_oot_real_execution(prediction_df, *, cfg) -> dict[str, pd.DataFrame]:
    decisions = oot_gates.filter_candidates(prediction_df, cfg)       # ~5 行
    sized = oot_position_sizing.compute_sizes(decisions, cfg)         # ~5 行
    constrained = oot_portfolio_constraints.apply_all(sized, cfg)     # ~10 行
    trades = oot_trade_simulation.simulate(constrained, cfg)          # ~5 行
    monthly = _aggregate_monthly(trades, cfg)                         # 保留 ~80 行
    summary = _compute_summary(trades, monthly, cfg)                  # 保留 ~80 行
    return {"monthly": monthly, "summary": summary, "trades": trades, ...}
```

**bit-identical 验证**：同 §3.2 的方法，但用 `oot/monthly_returns.csv` / `oot/summary.csv` 做 hash 比对。

### 3.4 A3：`baseline_candidate_gen.py` 634 → 2 模块

| 新模块（cta/strategy/）| 用途 | 估算 LOC |
|---|---|---|
| `baseline_setup_detection.py` | `_resolve_candidate_entry`, `_simulate_candidate_execution_path`, `_build_raw_setup_candidates`, `_infer_regime_label` | ~310 |
| `baseline_candidate_gen.py`（保留）| `generate_candidate_opportunities` 主入口 + `build_training_samples_from_trade_log` | ~330 |

注意 `generate_candidate_opportunities` 是外部 import 入口，必须保留在 `baseline_candidate_gen.py`（baseline_skill_suite.py shim 已 re-export 它）。

### 3.5 A4：`test_model_pipeline.py` 2586 → 11 个测试文件

按 v1 §4.1 蓝图（基于实测的 54 个测试 + line 编号）执行。codex 之前没做这一步。具体映射参见本文档 §8（测试拆分清单）。

---

## 4. Phase B：v2 新范围

### 4.1 B1：`candidate_training_dataset.py` 992 → 3 模块

| 新模块（cta/model/feature/）| 用途 | 估算 LOC |
|---|---|---|
| `candidate_schema.py` | `standardize_candidate_events`（事件 schema 标准化）+ schema 常量 + `CandidateTrainingDatasetResult` dataclass | ~350 |
| `candidate_baseline_bridge.py` | `generate_candidate_events_from_baselines`（从 baseline_skill_suite 输出转事件）+ feature 列合并 | ~330 |
| `candidate_training_dataset.py`（保留）| `build_and_save_candidate_training_dataset`, `generate_and_save_candidate_training_dataset_multi`, `main`, `_parse_args` | ~340 |

### 4.2 B2：`price_action.py` 946 → 3 模块

按 Al Brooks 概念域分：

| 新模块（cta/feature/）| 用途 | 估算 LOC |
|---|---|---|
| `price_action_bars.py` | 单根 K 线分类：`bar_range`, `bar_body`, `is_trend_bar`, `is_doji`, `bull_reversal_bar`, `inside_bar`, `outside_bar`, `gap_bar` 等 ~12 个 | ~340 |
| `price_action_swings.py` | 摆动 / leg / 高低点：`higher_high`, `lower_low`, `swing_high`, `leg_up`, `leg_count` 等 | ~310 |
| `price_action.py`（保留）| 入口聚合：reversal patterns + `register_price_action_features` + 对外暴露列名 | ~290 |

### 4.3 B3：`price_action_context.py` 935 → 3 模块

| 新模块（cta/feature/）| 用途 | 估算 LOC |
|---|---|---|
| `price_action_quality.py` | 质量度量：`range_over_atr`, `breakout_body_ratio`, `pullback_retrace_ratio`, `trend_bar_ratio`, `inside_bar_ratio` | ~320 |
| `price_action_structure.py` | 市场结构：`ema_slope`, `support_resistance_zones`, `multi_timeframe_alignment`, `space_to_prev_high`, `expected_reward` | ~330 |
| `price_action_context.py`（保留）| 入口聚合：`register_context_features` + `compute_context_panel` 主调度 | ~280 |

### 4.4 B4：`download_all.py` 913 → 3 模块

按现有的 commodity / financial / index 三分支已经存在的事实拆：

| 新模块（cta/data_code/）| 用途 | 估算 LOC |
|---|---|---|
| `download_all_dispatch.py` | symbol → downloader 分派 + RateLimiter 共享 + workers 调度 | ~330 |
| `download_all_progress.py` | tracking_finished / tracking_empty / 重试 / exchange fallback | ~280 |
| `download_all.py`（保留）| `main` + `_parse_args` + 顶层流程 + macro feature 构建调用 | ~310 |

### 4.5 B5：`run_all_features.py` 877 → 3 模块

| 新模块（cta/feature/）| 用途 | 估算 LOC |
|---|---|---|
| `feature_compute_dispatch.py` | 多进程 worker pool + date slicing + 失败重试 | ~300 |
| `feature_interval_runner.py` | 单 interval × 单 symbol 的特征构建调度（day / minute / 5/15/30/60）| ~290 |
| `run_all_features.py`（保留）| `main` + `_parse_args` + 顶层 orchestration | ~290 |

### 4.6 B6：`price_action_advanced.py` 767 → 2 模块

| 新模块（cta/feature/）| 用途 | 估算 LOC |
|---|---|---|
| `price_action_legs.py` | leg microstructure + channel details + trading range advances | ~390 |
| `price_action_advanced.py`（保留）| pullback pattern classification + entry/exit timing + 入口聚合 | ~380 |

### 4.7 B7：`futures_downloader.py` 641 → 2 模块

| 新模块（cta/data_code/）| 用途 | 估算 LOC |
|---|---|---|
| `tushare_client.py` | RateLimiter + `_safe_retry` + `_get_pro` + 通用工具 | ~270 |
| `futures_downloader.py`（保留）| `FuturesDownloader` 类 + day OHLCV + 1min 拼接 + 主连切换 | ~370 |

---

## 5. Phase C：测试拆分

### 5.1 C1：`test_model_pipeline.py` 2586 → 11 个测试文件

执行 v1 §4.1 蓝图（基于实测 line 编号的 54 个测试，加上 codex 期间新增的若干测试 → 估约 60 个）。具体映射见 §8。

### 5.2 C2：`test_baseline_skill_suite.py` 652 → 5 个测试文件

执行 v1 §4.2 蓝图（5 个新文件，按 baseline_helpers / feature_frame / strategies / candidate_gen / backtest_cli 分）。

### 5.3 C3：`test_candidate_training_dataset.py` 581 → 3 个测试文件

| 新测试文件（cta/model/feature/tests/）| 包含的测试 |
|---|---|
| `test_candidate_schema.py` | `standardize_candidate_events_*` 相关测试 |
| `test_candidate_baseline_bridge.py` | `generate_candidate_events_from_baselines_*` 相关测试 |
| `test_candidate_training_dataset.py`（保留）| `build_and_save_*`, `main`, end-to-end smoke |

每个 ≤300 行。原文件按 grep test 名拆分，**catch-all 规则**：grep 漏的归 `test_candidate_training_dataset.py`。

---

## 6. Wave 拆分（落地顺序）

### 6.1 总览（22 个 wave）

每个 wave 一个 PR；wave 之间存在依赖。每 wave 完成跑 `pytest cta/ -x` 与 §6.4 size check。

| Wave | 内容 | 主要变更 | 期望中间态最大文件 |
|------|------|---------|------------------|
| **W1** | A1.1 拆 leaf 工具：`pipeline_provenance`、`pipeline_feature_meaning`、`pipeline_feature_curation` | orchestrator.py 4168 → ~3750 | orchestrator.py 3750 |
| **W2** | A1.2 拆 splits + param_grids + meta_features | orchestrator.py 3750 → ~2850 | orchestrator.py 2850 |
| **W3** | A1.3 拆 symbol_ranking + dataset_prep + diagnostics | orchestrator.py 2850 → ~1500 | orchestrator.py 1500（仍超限，临时）|
| **W4** | A1.4 拆 CLI：`pipeline_cli.py` | orchestrator.py 1500 → ~1330 | orchestrator.py 1330（仍超限，临时）|
| **W5** | A1.5 拆 stage：`pipeline_stages.py` + bit-identical 验证（§3.2）| orchestrator.py 1330 → ~280 | orchestrator.py ≤500 ✅ |
| **W6** | A2.1 拆 `oot_gates.py` + `oot_position_sizing.py` | oot_evaluation.py 1489 → ~1010 | oot_evaluation.py 1010 |
| **W7** | A2.2 拆 `oot_portfolio_constraints.py` + `oot_trade_simulation.py` + bit-identical 验证 | oot_evaluation.py 1010 → ~450 | oot_evaluation.py ≤500 ✅ |
| **W8** | A3 拆 `baseline_setup_detection.py` | baseline_candidate_gen.py 634 → ~330 | baseline_candidate_gen.py ≤500 ✅ |
| **W9** | C1 拆 `test_model_pipeline.py` 2586 → 11 个文件 | tests 全部 ≤500 ✅ | 删除原文件 |
| **W10** | C2 拆 `test_baseline_skill_suite.py` → 5 文件 | tests ≤500 ✅ | 删除原文件 |
| **W11** | B1 拆 `candidate_training_dataset.py` → 3 模块 | candidate_training_dataset.py 992 → ~340 ✅ | |
| **W12** | C3 拆 `test_candidate_training_dataset.py` → 3 文件 | ≤500 ✅ | |
| **W13** | B7 拆 `futures_downloader.py` → 2 模块 | futures_downloader.py 641 → ~370 ✅ | |
| **W14** | B4 拆 `download_all.py` → 3 模块 | download_all.py 913 → ~310 ✅ | |
| **W15** | B2 拆 `price_action.py` → 3 模块 | price_action.py 946 → ~290 ✅ | |
| **W16** | B6 拆 `price_action_advanced.py` → 2 模块 | price_action_advanced.py 767 → ~380 ✅ | |
| **W17** | B3 拆 `price_action_context.py` → 3 模块 | price_action_context.py 935 → ~280 ✅ | |
| **W18** | B5 拆 `run_all_features.py` → 3 模块 | run_all_features.py 877 → ~290 ✅ | |
| **W19** | 收尾：CI size check 接入（§6.4）+ 全量 smoke run | | 所有 .py ≤500 ✅ |

### 6.2 关键依赖链

- W1-W5 严格串行（同改 orchestrator.py，并发 PR 会大量 conflict）
- W6-W7 串行（同改 oot_evaluation.py）
- W8 独立
- W9-W10、W12 是测试拆分，依赖对应源拆分完成
- W11-W18 各自独立，可调换顺序
- W19 必须最后（依赖前 18 wave 全完成）

### 6.3 中间态超限的临时白名单

W3 / W4 完成后 `pipeline_orchestrator.py` 仍 >500（1500 / 1330），W5 收尾才回归。这是计划内的**唯一**中间态超限。PR 描述里显式标注"中间态超限，W5 收尾"。其它任何 wave 中间态都必须 ≤500。

### 6.4 CI size check（W19 接入）

新增 `cta/run/tests/test_no_500plus_files.py`：

```python
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # cta/
MAX_LINES = 500
# 临时白名单：W6 之前 pipeline_oot_evaluation.py 主引擎还没拆，允许 ≤1500
TEMPORARY_WHITELIST = {
    # 加白名单条目时必须附 issue / wave 编号
    # 例如：("cta/model/pipeline_oot_evaluation.py", 1500, "W7 之前"),
}

class TestNoOver500LinePyFiles(unittest.TestCase):
    def test_all_py_files_under_500_lines(self):
        offenders: list[tuple[str, int]] = []
        for p in REPO_ROOT.rglob("*.py"):
            if any(part in {"__pycache__", "tests"} for part in p.parts):
                # tests 也要查，但 fixtures 目录排除
                if "fixtures" in p.parts:
                    continue
            line_count = sum(1 for _ in p.open(encoding="utf-8"))
            rel = str(p.relative_to(REPO_ROOT.parent))
            if line_count > MAX_LINES:
                allowed = next((max_l for path, max_l, _ in TEMPORARY_WHITELIST if path == rel), None)
                if allowed is None or line_count > allowed:
                    offenders.append((rel, line_count))
        self.assertEqual(offenders, [], f"Files exceed {MAX_LINES} LOC: {offenders}")
```

也可同时加 `pre-commit` hook：
```yaml
# .pre-commit-config.yaml（如果项目用 pre-commit）
- repo: local
  hooks:
    - id: cta-500-line-limit
      name: cta files must be <=500 lines
      entry: python -c "import sys, pathlib; [print(f'{p}: {sum(1 for _ in p.open())} lines') for p in [pathlib.Path(f) for f in sys.argv[1:]] if p.suffix == '.py' and sum(1 for _ in p.open()) > 500] or sys.exit(0)"
      language: system
      files: ^cta/.*\.py$
```

---

## 7. 风险与回退

| # | 风险 | 应对 |
|---|------|------|
| R1 | **W5 stage 拆解破坏 bit-identical**：副作用顺序、模型 random state、tmp dir 命名变化 → joblib hash 漂移 | §3.2 强制 hash 比对；任一差异立刻 revert W5 |
| R2 | **测试拆分时漏测**：grep miss 的 `def test_*` 没被搬走，原文件删除后丢测试 | §5.1 catch-all 规则；W9 完成后 `pytest cta/model/tests/test_pipeline_*.py -v --collect-only` 列出测试数，与 v2 §8 表格逐一核对 |
| R3 | **price_action 模块循环 import**：bars → swings → reversals → context 可能形成环 | 顶层从 `price_action.py` import；子模块只允许向"更基础"的方向 import（bars 不 import swings；context 可以 import 两者）|
| R4 | **download 拆分破坏 RateLimiter 共享**：分到不同模块的 downloader 持有各自 limiter → tushare 限速被突破 | RateLimiter 实例化集中在 `download_all.py` 顶层，作为参数注入到下游模块；新增测试断言所有 downloader 共享同一 limiter id |
| R5 | **CI size check 误伤**：fixtures / generated code 被纳入 | §6.4 排除 `fixtures` 子目录；临时白名单机制（必须附 wave 编号）|
| R6 | **拆分顺序错导致依赖断裂**：B 系列 wave 调换顺序 | §6.2 关键依赖链；W19 全量 smoke 兜底 |
| R7 | **codex 单文件并发改动 conflict**：W1-W5 都改 orchestrator.py 不能并行 | §6.2 显式声明串行；每 wave 一 PR |
| R8 | **回退**：某 wave 出问题 | 每 wave 一 commit / PR，revert 单 wave 回到上一稳态 |
| R9 | **CLI flag drift**：W4 cut-paste main → 3 分支函数时可能丢 kwarg | 沿用 v1 §8 R3：`test_dispatch_branches_pass_all_runtime_kwargs` 参数化测试 + `cta/run/tests/test_docs_sync.py` doc-sync 兜底 |
| R10 | **新模块 import 路径写错**：codex 实际写 import 时拼错模块名 | W19 全量 `python -c "import cta.model.pipeline_stages; ..."` import-smoke；CI 加 `python -m compileall cta/` |

---

## 8. 测试拆分详细映射（精确 line 编号）

### 8.1 `test_model_pipeline.py` 2586 → 11 个文件（54+ tests）

精确映射，**class 拆分规则**：原 `TestModelPipeline` 单 class 拆解为多个新 class，class 名按文件 CamelCase（`TestPipelineParamGrids`、`TestPipelineDiagnostics` 等）。若 setUp/tearDown 有共享，提到 `cta/model/tests/_pipeline_test_base.py`。

| 新测试文件 | class 名 | 来自原文件的测试（line: name）|
|---|---|---|
| `tests/test_pipeline_meta_features.py` | `TestPipelineMetaFeatures` | L43, L46（`test_validate_stop_loss_pct_*` 2 个）|
| `tests/test_pipeline_param_grids.py` | `TestPipelineParamGrids` | L54, L62, L71（`test_select_best_param_trial_*` 3 个）+ L1681, L1694（`test_train_mfe_mae_or_skip_*` 2 个）|
| `tests/test_pipeline_dataset_prep.py` | `TestPipelineDatasetPrep` | L79, L94, L107, L125, L149, L209, L1451, L1475, L1531, L1549, L1656, L1670, L1974, L1994, L2115（15 个）|
| `tests/test_pipeline_diagnostics.py` | `TestPipelineDiagnostics` | L190, L228, L1174, L1188, L1272, L2014, L2059, L2090（8 个）|
| `tests/test_pipeline_feature_curation.py` | `TestPipelineFeatureCuration` | L1205, L1222, L1239, L1254 + **新增** `test_safe_name_unicode_and_punctuation`（5 个）|
| `tests/test_pipeline_feature_meaning.py` | `TestPipelineFeatureMeaning` | L1851（1 个）|
| `tests/test_pipeline_splits.py` | `TestPipelineSplits` | L1293, L1566, L1586, L1606, L1643 + 5 个 `test_normalize_intervals_*`（10 个）|
| `tests/test_pipeline_symbol_ranking.py` | `TestPipelineSymbolRanking` | L1807, L2144（2 个）|
| `tests/test_pipeline_cli.py` | `TestPipelineCli` | L1777, L1787, L1794 + **新增** `test_dispatch_branches_pass_all_runtime_kwargs`（4 个）|
| `tests/test_pipeline_orchestrator.py` | `TestPipelineOrchestrator` | L169, L1334, L1430, L1507, L1709, L1732, L1820, L1875, L1924, L2337, L2369（11 个）|
| `tests/test_pipeline_oot_evaluation.py` | `TestPipelineOotEvaluation` | **18 个** `test_evaluate_oot_real_execution_*`：L249, L314, L376, L449, L531, L573, L615, L661, L711, L786, L856, L910, L950, L1007, L1059, L1122, L2161, L2219, L2275 + L754 `test_build_position_lifetime_table_*`（19 个）|

**catch-all**：未列出的 `def test_*` 默认归 `test_pipeline_orchestrator.py`。
**估算大小**：最大的 `test_pipeline_oot_evaluation.py`（19 测试）≈ 450 行；最大的 `test_pipeline_dataset_prep.py`（15 测试）≈ 420 行 — 都 ≤500。

### 8.2 `test_baseline_skill_suite.py` 652 → 5 个文件（20 tests）

| 新测试文件 | class 名 | tests（line: name）|
|---|---|---|
| `cta/strategy/tests/test_baseline_helpers.py` | `TestBaselineHelpers` | L363, L367, L446, L500（4 个）|
| `cta/strategy/tests/test_baseline_feature_frame.py` | `TestBaselineFeatureFrame` | L61, L78, L338（3 个）|
| `cta/strategy/tests/test_baseline_strategies.py` | `TestBaselineStrategies` | L99, L409, L457（3 个）|
| `cta/strategy/tests/test_baseline_candidate_gen.py` | `TestBaselineCandidateGen` | L115, L166, L192, L226, L260, L297, L537, L597（8 个）|
| `cta/strategy/tests/test_baseline_backtest_cli.py` | `TestBaselineBacktestCli` | L346, L380（2 个）|

最大文件 `test_baseline_candidate_gen.py`（8 测试）≈ 280 行 — 全部 ≤500。

### 8.3 `test_candidate_training_dataset.py` 581 → 3 个文件（待 grep）

W12 执行前先 `grep -n "def test_" /Users/wuyuliang/code/vnpy/cta/model/feature/tests/test_candidate_training_dataset.py`，按 §5.3 的归属规则归类。

---

## 9. 给 codex 的执行指引

1. **不修改 vnpy 主仓代码**：所有改动在 `cta/` 下
2. **每 wave 一次 PR**，按 §6.1 顺序；用户工作流要求 feature 分支开发、**禁止自动 commit**
3. **代码风格**：参考现有 `cta/model/pipeline_pooling.py`、`cta/model/pipeline_html_report.py` 的 header / `from __future__ import annotations` / `__all__` 写法
4. **不引入新依赖**
5. **import 调整原则**：拆文件**只搬不改**逻辑；W5、W7 是 stage 拆解，必须 bit-identical
6. **W5 / W7 必跑 hash 比对**（§3.2 / §3.3）：任一 joblib hash 漂移立即 revert
7. **每 wave 后必跑 size check**：`python -m pytest cta/run/tests/test_no_500plus_files.py -v`（W19 之后才存在；之前用脚本临时检查 `find cta -name '*.py' | xargs wc -l | awk '$1 > 500'`）
8. **CLI baseline**：W1 前用 `python -m cta.model.model_pipeline --help > /tmp/help_baseline.txt` 留底，每 wave 后比对
9. **smoke run**：每 wave 后跑 `python -m cta.model.model_pipeline --symbol RB0 --interval 60min --start 2010-01-01 --end 2012-12-31 --max-walk-forward-windows 1 --output-root /tmp/smoke_wN`，确认 `report.md` 非空
10. **`__all__` 声明**：每个新模块顶部声明，列公开符号
11. **`from __future__ import annotations`**：每个新文件首行加

---

## 10. 验收清单

### 10.1 文件大小

- [x] `find /Users/wuyuliang/code/vnpy/cta -name "*.py" -not -path "*/fixtures/*" -not -path "*/__pycache__/*" | xargs wc -l | awk '$1 > 500 {print}'` 输出空
- [x] `cta/model/pipeline_orchestrator.py` ≤ 500
- [x] `cta/model/pipeline_oot_evaluation.py` ≤ 500
- [x] `cta/strategy/baseline_candidate_gen.py` ≤ 500
- [x] `cta/model/feature/candidate_training_dataset.py` ≤ 500
- [x] `cta/feature/price_action*.py` 全部 ≤ 500（包括拆出的子模块）
- [x] `cta/data_code/download_all.py` ≤ 500，`futures_downloader.py` ≤ 500
- [x] `cta/feature/run_all_features.py` ≤ 500
- [x] 所有新建测试文件 ≤ 500

### 10.2 测试

- [ ] `pytest cta/ -x` 全绿
- [x] `pytest cta/run/tests/test_no_500plus_files.py -v` 绿（W19 之后）
- [x] `cta/model/tests/test_model_pipeline.py` 已删除
- [x] `cta/strategy/tests/test_baseline_skill_suite.py` 已删除
- [x] `cta/model/tests/test_pipeline_*.py` 至少 11 个文件存在
- [x] `cta/strategy/tests/test_baseline_*.py` 至少 5 个文件存在
- [x] 私有 helper 不再从 `model_pipeline` shim 导入

### 10.3 行为一致性

- [ ] `python -m cta.model.model_pipeline --help` 输出与 W1 前 baseline byte-identical
- [ ] W5 完成后 `.joblib` hash 与 W4 前 baseline 一致（§3.2）
- [ ] W7 完成后 OOT 输出 (`monthly_returns.csv`, `summary.csv`) hash 与 W6 前 baseline 一致（§3.3）
- [ ] `bash cta/run.sh` 全部步骤跑通（data / validate / feature / candidate / train / oot）

### 10.4 公开 API

- [x] `from cta.model.model_pipeline import run_model_pipeline, run_model_pipeline_multi, ModelPipelineResult, main` 工作
- [x] `from cta.strategy.baseline_skill_suite import generate_candidate_opportunities, prepare_master_feature_frame` 工作
- [ ] `python -m cta.model.model_pipeline ...` CLI 调用与 v1 前行为一致
- [ ] `python -m cta.data_code.download_all ...` CLI 调用与 v1 前行为一致
