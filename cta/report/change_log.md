# CTA 变更日志

按日期倒序记录每次改动。新增条目追加到顶部。

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
