# 上游特征流水线塌缩诊断 — 2026-05-31

> 定位：**诊断报告 + 推荐修复方案**。本轮**未改任何生产代码**（曾试改 Fix-1~5 又全部回退，见 §5.3）。
> 关联 review：[20260531.md](20260531.md)（signal_type 精细化 + cap）。

---

## 0. TL;DR

- **现象**：用今早重训的 `20260531_GRP_CLUSTER_*` 上游产物跑 OOT（`oot_20260531_220809_cluster_both_sigtype_v10`），
  相比用旧 `20260529_GRP_*` 上游的 `oot_20260531_163014_*`，trade_count 5400→361、年化 94.7%→4.86%、Sharpe 5.90→0.59。
- **真因**：不是 OOT/cfg/cap/代码 bug（这些两次 run 完全一致）。是**磁盘特征数据覆盖缺口**——
  `cta/data/feature/minute30/<symbol>/` 对 INDEX 全簇（IF0/IC0/IH0/IM0）+ CU0 等品种**只有 2026-01~05 的 85 个文件**，
  缺 2010-2025 历史。训练窗口 2015–2020 在这些品种上 `merge_asof` 一行都对不上 → 415 列全 NaN → 模型在噪声上训练 → AUC≈0.5。
- **关键真相**：20260529 那个"好结果"（5400 笔 / 277%）**根本没用磁盘上的 415 个真实特征**，
  它跑的是 21 个候选派生合成特征（`generic_auto_*`/`generic_model_*`）。那时 2026-only parquet 还没生成，磁盘 merge 落空 → silent fallback → 21 列。
- **本轮交付**：仅本诊断文档。修复（补数据 + 显式 fallback 模式）留待下一轮，用户已定策略（§5.2）。

---

## 1. 现象与量化对比

### 1.1 OOT 指标（同 cfg `cfg_sigtype_target_v10.json`、同 git_sha `bb6a2b58`、同 cap）

| 指标 | `163014`（旧上游 20260529_GRP_*） | `220809`（今早上游 20260531_GRP_*） |
|---|---|---|
| trade_count | **5400** | **361**（↓ 93%） |
| total_return | 277.5% | 11.17% |
| annualized | 94.7% | 4.86% |
| monthly_sharpe | 5.90 | 0.59 |
| max_drawdown | -1.86% | -3.15% |

### 1.2 各 signal_type 命中量塌缩

| signal_type | 163014 trades | 220809 trades |
|---|---|---|
| bull_pullback_continuation | 1993 | **42** |
| breakout_pullback_continuation | 1713 | **82** |
| cross_sectional_momentum | 127 | 132 |
| atr_breakout | 843 | 65 |
| cluster_index（簇维度）| 348 | **0** |

主力两类 pullback signal_type 成交量被吃掉 95%+。

### 1.3 上游训练矩阵异常（13/24 桶崩坏）

扫 `_metrics.csv` 的 `feature_count / feature_null_ratio_mean / feature_all_null_count / selection_valid_auc`：

| cluster_interval | 20260529 旧 | 20260531 新 |
|---|---|---|
| INDEX_minute30 | 52 特征 / 5.5% NaN / **0 全NaN** / va_auc 0.527 | 446 / **93.7% NaN** / **415 全NaN** / va_auc **0.497** |
| AGRI_minute30 | 52 / 5.5% / 0 / 0.522 | 446 / 93.7% / **415** / 0.510 |
| AGRI_minute60 | 52 / 5.5% / 0 / 0.526 | 446 / 93.7% / **415** / 0.520 |
| OTHER_minute30 | 52 / 5.4% / 0 / 0.542 | 446 / 93.7% / **415** / 0.541 |
| PRECIOUS_minute30 | 52 / 5.4% / 0 / 0.454 | 446 / 93.7% / **415** / 0.452 |
| BOND_minute30 | 52 / 5.6% / 0 / 0.555 | 446 / 93.7% / **415** / 0.538 |
| BLACK_minute30/60 | OK | **整目录无 metrics（被静默跳过）** |
| CHEMICAL_minute30/60 | OK | **整目录无 metrics** |
| 各 day 桶 | OK | feat≈445 / 低 NaN / 0 全NaN（**没崩**，见 §2.4）|

---

## 2. 真因：逐品种磁盘特征数据覆盖缺口

### 2.1 per-symbol 覆盖（`cta/data/feature/minute30/<symbol>/`）

| symbol | minute30 磁盘覆盖 | 训练窗口(2015-2020)能否对齐 |
|---|---|---|
| RB0 | 2010-01-04 ~ 2026-04-17（3954 文件）| ✓ 全历史 |
| TA0 | 2010-01-04 ~ 2026-05-15（3227 文件）| ✓ 全历史 |
| **IF0 / IC0 / IH0 / IM0**（全 INDEX）| **2026-01-05 ~ 2026-05-15（各 85 文件）** | ✗ 仅近 5 个月 |
| **CU0** | **2026-01-05 ~ 2026-05-15（85 文件）** | ✗ 仅近 5 个月 |

INDEX 的 minute60 目录甚至**完全为空（0 文件）**。覆盖缺口是逐品种、非全局的——这正是为什么只有部分簇崩。

### 2.2 时间轴对不上

- 候选样本 datetime 范围：**2015-01-08 ~ 2026-05-15**（含 2015-2020 训练段）。
- INDEX 簇磁盘特征：**仅 2026-01-05 起**。
- `merge_asof(direction="backward", tolerance=30min)`：2015-2025 的候选行向前找不到任何 generic 行（最早的 generic 在 2026）→ 全部 NaN。
  只有落在 2026-01~05 的极少数候选能匹配 → 整列 NaN 比例 93.7%、训练段（2015-2020）100% NaN → `feature_all_null_count=415`。

### 2.3 调用链（代码本身行为是"正确但太宽容"）

1. [pipeline_run.py:19](../../model/orchestration/pipeline_run.py) `generic_mode='auto'`（默认）
2. [pipeline_dataset_prep.py:399-413](../../model/dataset/pipeline_dataset_prep.py) `_resolve_generic_columns('auto')` → `None`
3. [training_feature_builder.py:68-96](../../model/feature/training_feature_builder.py) `_auto_detect_generic_columns` 扫盘拿到 415 数值列（它们在 2026 文件里确实有值，所以没被"全 NaN"过滤剔除）
4. [training_feature_builder.py:252-260](../../model/feature/training_feature_builder.py) `merge_asof` 对 2015-2020 候选**静默**全 miss → 415 列全 NaN
5. 无 post-merge 校验 → 垃圾矩阵直接进 [pipeline_run.py](../../model/orchestration/pipeline_run.py) 训练 → valid_auc≈0.5 落盘 model.bin

### 2.4 为何 day 桶 / 部分簇没崩

- INDEX 的 **day 目录为空 / minute60 目录为空** → [training_feature_builder.py](../../model/feature/training_feature_builder.py) `load_generic_feature_frame` 抛 `FileNotFoundError`
  → `build_training_feature_table` 的 `try/except` **silent fallback 到 candidate-only**
  → [pipeline_feature_enrichment.py](../../model/dataset/pipeline_feature_enrichment.py) `_auto_enrich_candidate_features_for_models(force_generic_fallback=True)`
  → 产出 21 个 `generic_auto_*`/`generic_model_*` 候选派生特征（全历史、健康）→ 训练正常。
- 关键区别：**"目录空/无文件" → fallback（健康）；"目录有 2026 文件但训练段全 NaN" → 静默放行（垃圾）**。
  后者才是这次塌缩的触发条件。

---

## 3. 关键真相：20260529 "好结果" 没用真实特征

直接读 `feature_table.csv` 的列：

| 批次 | INDEX_minute30 总列数 | generic_ 列数 | generic 列内容 |
|---|---|---|---|
| 20260529 | 83 | **21** | 全是 `generic_auto_close/open/.../body_ratio` + `generic_model_trade_setup/.../cluster_momentum_rank`（候选派生合成特征）|
| 20260531 | 477 | **415** | 全是 `generic_sma_3/ema_3/macd_*/ma_alignment/...`（真实磁盘技术指标）|

**结论**：
- 20260529 的 5400 笔 / 277% 跑的是 **21 个合成兜底特征 + signal_type size 杠杆系数**，**0 个真实磁盘特征**。
- 磁盘那 415 个真实特征的**实际增益从未被验证**——它第一次真正参与训练（20260531）就因数据缺口塌了。
- 含义：不要把 277% 当成"真实特征模型的能力"。它是 fallback 路径 + 杠杆放大的产物。后续若补齐数据用真实特征，**结果可能更好也可能更差，必须重新基准**。

---

## 4. 调研过程

### 4.1 已排除的假设（确认不是这些）

| 假设 | 排除依据 |
|---|---|
| trade_filter 关卡变严 | 两次 run 在 `is_executed=1` 候选上通过率几乎一致：bull pass@25pctl 84.5% vs 83.7%、breakout pass@35pctl 84.6% vs 86.8% |
| cap / 仓位上限不同 | `cfg_fingerprint.json` 两次 run 的 caps（max_total_positions=10 / per_symbol=1 / per_cluster=8 / notional pct 0.3/0.5/1.5）完全一致 |
| 代码版本不同 | git_sha 均为 `bb6a2b58` |
| signal_type delta 配置漂移 | size_multiplier / max_concurrent / threshold_delta 三个 dict 两次完全一致 |
| OOT pipeline 逻辑变了 | OOT 评估脚本同一份，只是 `--pattern` 指向不同上游目录 |

### 4.2 Explore agent 诊断要点（特征流水线）

- 主入口：[training_feature_builder.py](../../model/feature/training_feature_builder.py) `merge_candidate_and_generic_features`（拼接点）
  + [pipeline_feature_enrichment.py](../../model/dataset/pipeline_feature_enrichment.py) `_build_training_feature_table_with_auto_fallback`（fallback 决策点）
  + [pipeline_pooling.py:61-62](../../model/dataset/pipeline_pooling.py) `except Exception: ...; continue`（pool 内单 symbol 失败被静默吞 → BLACK/CHEMICAL 整目录无产出的嫌疑点）
- agent 初判 "merge_asof tolerance 不匹配"，方向对，但**精确根因是数据时间覆盖缺口**（agent 当时未读磁盘文件日期范围，本诊断补齐了这一证据）。

---

## 5. 推荐修复（未实施，待下轮确认后做）

### 5.1 数据侧（真正根因，优先级最高）

补齐 INDEX（IF0/IC0/IH0/IM0）、CU0 等品种的 **2010-2025 全历史 minute30/minute60 特征 parquet**。
排查特征生成 job 为何只产出了 2026-01~05 的近月文件（疑似 job 用了受限的 start_date 或中断）。

### 5.2 代码侧（用户已定策略：显式 fallback 模式）

1. 新增 `generic_mode='candidate_only'`：显式走 21 合成特征路径（不碰磁盘），用于**确定性复现 20260529** 与 OOT/sim/live 一致性兜底。
2. 默认 `generic_mode='auto'` 保持 **fail-loud**：merge 后任一 generic 列 ≥99% NaN → `raise`，杜绝垃圾矩阵静默落盘。
3. 在 fallback 决策处**区分两种情形**：
   - "目录空 / 无文件 / 文件不覆盖训练段范围" → **loud warning + fallback 到 candidate_only**（保住现在能跑的 day/空目录桶）
   - "目录有文件但 merge 后训练段全 NaN" → **raise**（捕获本次塌缩的精确触发条件）
4. pool 内单 symbol 失败累积后聚合 raise（替代 [pipeline_pooling.py:61](../../model/dataset/pipeline_pooling.py) 的静默 continue），让 BLACK/CHEMICAL 整目录无产出能被立刻发现。

### 5.3 附录：本轮已写又回退的 Fix-1~5 草案

为快速验证可行性，本会话曾在主仓库落地过 5 处改动（fail-loud merge 校验、源端 fill-ratio 门槛、pool 聚合 raise、移除 silent fallback、训练入口 feature-health gate + 新建 `pipeline_health.py`）。
**已全部回退**，原因：
- 与"显式 fallback 模式"的最终策略冲突（Fix-4 直接移除 fallback 会误伤现在健康运行的 day/空目录桶）；
- 用户明确本轮"代码先不动，只写诊断"。
回退用逐条反向 Edit（**非 `git checkout`**，因相关文件含其它会话的未提交改动），回退后 `import` 验证通过、无残留标记。这些草案的思路并入 §5.2 作为下轮蓝本。

---

## 6. 用户验证步骤（Claude 不执行）

1. **补数据**：检查 / 重生成 INDEX、CU0 等品种 2010-2025 的 minute30/minute60 特征 parquet。
   ```bash
   ls cta/data/feature/minute30/IF0/ | sort | head -1   # 应回到 2010 附近
   ```
2. **选 generic_mode**：
   - 要确定性复现 20260529 的 5400 笔 → 待实现的 `generic_mode='candidate_only'`；
   - 要用真实磁盘特征 → 数据补齐后用 `generic_mode='auto'`（届时会 fail-loud 守门）。
3. **重训 + 重跑 OOT**，确认 `cluster_index` trade_count 从 0 恢复，并**重新基准**真实特征模型的收益（不要直接对标 277%，见 §3）。

---

## 7. Follow-up

- 特征生成 job 加"全历史覆盖自检"：最早文件日期 vs 要求训练起点，缺口直接报错。
- `generic_mode='auto'` 配 `max_null_pct` 门槛常量，集中可调。
- A/B 验证：磁盘 415 真实特征 vs 21 合成特征的真实增益（消除"277% 是否可复制"的疑问）。
- 排查 BOND `trade_filter_prob` 退化（20260529 饱和到 1.000 / 20260531 压到 0.007-0.04），单开 ticket（疑似 class imbalance，独立于本数据缺口问题）。

---

## 8. change_log 摘要

- 日期：2026-05-31
- 分支：`claude/musing-bohr-512f6e`
- 任务：诊断 220809 OOT 收益塌缩（仅诊断 + 回退过早落地代码，未实施修复）
- 修改文件：本诊断文档（新增）；`training_feature_builder.py` / `pipeline_pooling.py` / `pipeline_feature_enrichment.py` / `pipeline_run.py` 的本轮试改已**全部回退**；删除 `pipeline_health.py`
- 主要结论：根因为逐品种磁盘特征数据覆盖缺口（INDEX/CU0 仅 2026 数据）；20260529 "好结果"实为 21 合成特征路径，未用真实磁盘特征；修复策略=补数据 + 显式 fallback 模式（下轮做）
