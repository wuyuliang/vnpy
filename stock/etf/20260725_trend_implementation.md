# 日频趋势与震荡状态规则预测实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为任意单一 A 股股票或 ETF 实现严格因果的 1 日、3 日五状态规则预测，并对 `159915.SZ` 生成可审计的真实历史评估与最终结果文档。

**Architecture:** 计算层由纯函数组成，依次完成数据校验、因果指标、历史状态、未来状态预测、标签对齐和评估；数据层仅负责原始行情、逐日复权因子和交易日历；CLI 在暂存目录完成全部计算和审计后事务化发布。预测模块永远不接触未来标签，评估模块只在预测完成后按实际交易行追加标签。

**Tech Stack:** Python 3.10+、pandas、NumPy、SciPy、scikit-learn、Tushare（仅下载适配器）、pytest、Ruff。

---

## 成功标准

- [ ] `predict_regime` 在日期 `T` 只读取 `T` 及以前的数据，输出下一交易日与第三个交易日的分数和五状态。
- [ ] 篡改 `T` 之后全部 OHLCV、成交额或复权因子后，`T` 及以前全部输出逐值不变。
- [ ] 历史标签严格对应后续第 1、3 个实际行情行；最新未揭晓标签保持缺失。
- [ ] 评估同时输出规则、持久性和开发期多数类基线，且区间样本的预测日和标签日均在区间内。
- [ ] CLI 原子生成设计文档规定的全部 CSV/JSON，JSON 不包含 `NaN` 或 `Infinity`。
- [ ] 使用真实 Tushare 数据完成 `159915.SZ` 正式运行，输出目录为
  `stock/etf/output/20260725_chuangyeban_trend`。
- [ ] 新增 `stock/etf/20260725_chuangyeban_trend_results.md`，记录最终结果、数据来源、
  防穿越审计和第二阶段准入结论。
- [ ] 新增测试、ETF 测试集、Ruff 和编译检查全部通过，只提交到 `feature`。

## Task 1：数据契约、配置与状态边界

**Files:**

- Create: `stock/etf/regime_rules.py`
- Create: `stock/etf/tests/test_regime_rules.py`

- [ ] **Step 1：写失败测试**

测试下列公开行为：

```python
def test_score_to_state_uses_closed_boundaries() -> None: ...
def test_prepare_bars_rejects_duplicate_dates() -> None: ...
def test_prepare_bars_rejects_invalid_ohlcv() -> None: ...
def test_prepare_bars_requires_252_rows() -> None: ...
def test_prepare_bars_accepts_generic_symbol() -> None: ...
```

边界断言必须覆盖 `-3, -2, -1, 0, 1, 2, 3`，并明确 `-2/-1/1/2` 所属状态。
输入校验覆盖空标的、带时区日期、非有限价格、OHLC 关系错误、负成交量、重复
`symbol + datetime` 和未声明复权口径。

- [ ] **Step 2：确认 RED**

Run:

```bash
python3 -m pytest stock/etf/tests/test_regime_rules.py -q
```

Expected: 因 `stock.etf.regime_rules` 尚不存在而失败。

- [ ] **Step 3：实现最小数据模型**

实现：

```python
class RegimeState(StrEnum): ...

@dataclass(frozen=True)
class RegimeConfig:
    minimum_rows: int = 252
    price_adjustment_mode: Literal["raw", "point_in_time_adjusted"] = "raw"

def score_to_state(score: float) -> RegimeState: ...
def prepare_symbol_bars(bars: pd.DataFrame, config: RegimeConfig) -> pd.DataFrame: ...
```

`prepare_symbol_bars` 返回按日期升序的新 DataFrame，不静默去重、不补 K 线、不向后填充。
状态分数非有限或超出 `[-3, 3]` 时失败，缺失指标在指标表内保留缺失而不调用状态映射。

- [ ] **Step 4：确认 GREEN 并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_rules.py -q
git add stock/etf/regime_rules.py stock/etf/tests/test_regime_rules.py
git commit -m "feat: validate causal regime inputs"
```

## Task 2：Wilder 指标与历史已实现状态

**Files:**

- Modify: `stock/etf/regime_rules.py`
- Modify: `stock/etf/tests/test_regime_rules.py`

- [ ] **Step 1：写指标参考测试**

新增测试：

```python
def test_wilder_atr_and_dmi_match_hand_calculation() -> None: ...
def test_efficiency_ratio_handles_zero_path_length() -> None: ...
def test_log_regression_matches_numpy_reference() -> None: ...
def test_realized_regime_classifies_synthetic_paths() -> None: ...
def test_realized_regime_does_not_depend_on_volume() -> None: ...
```

手工样例覆盖单调上涨、单调下跌、横盘和有偏震荡。参考值在测试中用独立循环或
`numpy.linalg.lstsq` 计算，不调用生产代码本身。

- [ ] **Step 2：确认 RED**

```bash
python3 -m pytest stock/etf/tests/test_regime_rules.py -q
```

Expected: 新指标接口不存在。

- [ ] **Step 3：实现因果指标**

内部实现以下右端滚动函数：

```python
def _wilder_dmi(bars: pd.DataFrame, period: int) -> pd.DataFrame: ...
def _efficiency_ratio(close: pd.Series, period: int) -> pd.Series: ...
def _rolling_log_regression(close: pd.Series, period: int) -> pd.DataFrame: ...
def _direction_quality_to_score(direction: pd.Series, quality: pd.Series) -> pd.Series: ...
```

Wilder 平滑使用 `ewm(alpha=1 / period, adjust=False, min_periods=period)`，不得使用
`center=True`。所有除法显式处理零分母，指标未成熟处保持 `NaN`。

- [ ] **Step 4：实现历史状态**

公开接口：

```python
def calculate_realized_regime(
    bars: pd.DataFrame,
    config: RegimeConfig,
) -> pd.DataFrame: ...
```

输出至少包含设计文档第 5 节的全部基础指标、方向分项、`direction_evidence`、
`trend_quality`、`realized_score` 和 `realized_state`。成交量与成交额不得参与答案定义。

- [ ] **Step 5：确认 GREEN 并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_rules.py -q
git add stock/etf/regime_rules.py stock/etf/tests/test_regime_rules.py
git commit -m "feat: calculate realized market regimes"
```

## Task 3：1 日、3 日纯规则预测与防穿越

**Files:**

- Modify: `stock/etf/regime_rules.py`
- Modify: `stock/etf/tests/test_regime_rules.py`

- [ ] **Step 1：写预测行为和因果性测试**

新增：

```python
def test_volume_confirmation_cannot_flip_direction() -> None: ...
def test_predict_regime_emits_one_and_three_day_rows() -> None: ...
def test_mutating_future_bars_does_not_change_prefix_predictions() -> None: ...
def test_prefix_recalculation_matches_vectorized_prediction() -> None: ...
def test_prediction_target_dates_use_exchange_calendar() -> None: ...
def test_calendar_must_cover_third_future_open_day() -> None: ...
```

前缀比较包括最终分数、状态和每一个方向/质量贡献列；浮点数使用严格容差，分类逐值相等。
交易日历夹具包含周末和人工休市日，证明不能使用普通工作日。

- [ ] **Step 2：确认 RED**

```bash
python3 -m pytest stock/etf/tests/test_regime_rules.py -q
```

- [ ] **Step 3：实现固定规则**

实现设计文档第 6 节：

```python
def confirm_direction_with_activity(
    price_direction: pd.Series,
    volume_pressure: pd.Series,
    activity_shock: pd.Series,
) -> pd.Series: ...

def predict_regime(
    bars: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    config: RegimeConfig,
) -> pd.DataFrame: ...
```

每个特征日输出 1 日和 3 日两行。输出含 `feature_asof_date`、`prediction_horizon`、
`prediction_for_date`、`max_feature_source_date`、分数、状态以及设计文档全部分项。
成交量仅在已有价格方向上作最多 15% 的确认，任何输入都不能令方向翻转。

- [ ] **Step 4：确认 GREEN 并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_rules.py -q
git add stock/etf/regime_rules.py stock/etf/tests/test_regime_rules.py
git commit -m "feat: predict causal one and three day regimes"
```

## Task 4：标签、准确率、基线与状态切换

**Files:**

- Create: `stock/etf/regime_evaluation.py`
- Create: `stock/etf/tests/test_regime_evaluation.py`

- [ ] **Step 1：写标签和指标失败测试**

```python
def test_labels_follow_actual_symbol_rows() -> None: ...
def test_latest_predictions_keep_unknown_labels() -> None: ...
def test_period_requires_feature_and_label_dates_inside() -> None: ...
def test_metrics_match_hand_calculation() -> None: ...
def test_constant_scores_return_null_spearman() -> None: ...
def test_majority_baseline_uses_development_period_only() -> None: ...
def test_transition_matching_does_not_double_count() -> None: ...
```

手工表必须能复算精确准确率、balanced accuracy、方向准确率、结构准确率、MAE、
Spearman、类别样本数和混淆矩阵。缺失类别的 balanced accuracy 只平均该区间真实出现的
类别，并在输出记录出现类别数。

- [ ] **Step 2：确认 RED**

```bash
python3 -m pytest stock/etf/tests/test_regime_evaluation.py -q
```

- [ ] **Step 3：实现评估纯函数**

公开接口：

```python
def attach_realized_labels(
    predictions: pd.DataFrame,
    realized: pd.DataFrame,
) -> pd.DataFrame: ...

def evaluate_regime_predictions(
    labeled: pd.DataFrame,
    development_end: str = "2022-12-30",
    validation_start: str = "2023-01-01",
    validation_end: str = "2024-12-31",
    oos_start: str = "2025-01-01",
) -> RegimeEvaluation: ...
```

`RegimeEvaluation` 提供可直接写出的：

```text
accuracy_by_period
confusion_matrix_1d
confusion_matrix_3d
transition_accuracy
summary
```

持久性基线取特征日已实现状态；多数类只从开发观察期拟合。切换匹配按设计文档的一对一
规则实现，未匹配预测算误报。非有限相关系数在 Python 结果中使用 `None`。

- [ ] **Step 4：确认 GREEN 并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_evaluation.py -q
git add stock/etf/regime_evaluation.py stock/etf/tests/test_regime_evaluation.py
git commit -m "feat: evaluate regime forecasts and baselines"
```

## Task 5：逐日复权与真实数据适配器

**Files:**

- Create: `stock/etf/regime_data.py`
- Create: `stock/etf/tests/test_regime_data.py`

- [ ] **Step 1：写因果复权失败测试**

```python
def test_raw_mode_preserves_prices() -> None: ...
def test_point_in_time_adjustment_uses_same_or_prior_factor_only() -> None: ...
def test_missing_initial_factor_fails() -> None: ...
def test_mutating_future_factor_does_not_change_adjusted_prefix() -> None: ...
def test_trade_calendar_normalization_keeps_open_days() -> None: ...
def test_download_adapter_never_persists_token() -> None: ...
```

逐日复权价格定义为 `raw_ohlc * 当日已知因子`，成交量为
`raw_volume / 当日已知因子`，成交额不变。缺因子只能从过去向前填充，禁止 `bfill`。

- [ ] **Step 2：确认 RED**

```bash
python3 -m pytest stock/etf/tests/test_regime_data.py -q
```

- [ ] **Step 3：实现数据纯函数**

```python
def build_causal_bars(
    daily: pd.DataFrame,
    factors: pd.DataFrame | None,
    price_adjustment_mode: str,
) -> pd.DataFrame: ...

def normalize_trade_calendar(calendar: pd.DataFrame) -> pd.DataFrame: ...
```

保留原始列和因子日期审计列，逐行满足 `factor_source_date <= datetime`。

- [ ] **Step 4：实现可选 Tushare 下载**

```python
def download_regime_inputs(
    symbol: str,
    start_date: str,
    end_date: str,
    token: str | None = None,
) -> RegimeInputs: ...
```

仅在函数内导入 Tushare；默认从 `TUSHARE_TOKEN` 读取凭据。下载 `fund_daily`、
`fund_adj` 和对应交易所 `trade_cal`，分页或按日期分块确保完整，原始返回按主键去重前先
检查冲突。`source_audit` 只记录接口、请求区间、行数、日期范围和哈希，不记录 token。

- [ ] **Step 5：确认 GREEN 并提交**

```bash
python3 -m pytest stock/etf/tests/test_regime_data.py -q
git add stock/etf/regime_data.py stock/etf/tests/test_regime_data.py
git commit -m "feat: load point in time regime data"
```

## Task 6：事务化 CLI 与完整产物

**Files:**

- Create: `stock/etf/run_regime_analysis.py`
- Create: `stock/etf/tests/test_run_regime_analysis.py`

- [ ] **Step 1：写端到端失败测试**

```python
def test_runner_writes_complete_artifact_set() -> None: ...
def test_runner_rejects_nonempty_output_without_overwrite() -> None: ...
def test_runner_failure_preserves_previous_output() -> None: ...
def test_summary_is_strict_json() -> None: ...
def test_latest_predictions_have_target_dates_without_fake_labels() -> None: ...
```

夹具使用至少 320 行确定性行情和带休市日的交易日历。测试期望以下文件：

```text
daily_predictions.csv
labeled_predictions.csv
accuracy_by_period.csv
confusion_matrix_1d.csv
confusion_matrix_3d.csv
transition_accuracy.csv
summary.json
source_audit.json
```

- [ ] **Step 2：确认 RED**

```bash
python3 -m pytest stock/etf/tests/test_run_regime_analysis.py -q
```

- [ ] **Step 3：实现运行器**

CLI 参数：

```text
--symbol                 默认 159915.SZ
--start                  默认 2017-08-14
--end                    默认最近已完成交易日
--daily-csv
--factors-csv
--calendar-csv
--download
--price-adjustment-mode  raw 或 point_in_time_adjusted，必须显式
--output-dir             默认 stock/etf/output/20260725_chuangyeban_trend
--overwrite
```

`--download` 与本地 CSV 输入互斥。运行器先在输出目录同级暂存目录计算、写入和重新读取
校验，全部成功后才替换目标目录；失败时清理暂存并保留既有成功目录。写 JSON 时设置
`allow_nan=False`，CSV 日期使用 `YYYY-MM-DD`。

- [ ] **Step 4：确认 GREEN 并提交**

```bash
python3 -m pytest stock/etf/tests/test_run_regime_analysis.py -q
git add stock/etf/run_regime_analysis.py stock/etf/tests/test_run_regime_analysis.py
git commit -m "feat: add transactional regime analysis runner"
```

## Task 7：局部质量门禁与独立代码审查

**Files:**

- Modify only files introduced in Tasks 1-6

- [ ] **Step 1：运行局部验证**

```bash
python3 -m pytest \
  stock/etf/tests/test_regime_rules.py \
  stock/etf/tests/test_regime_evaluation.py \
  stock/etf/tests/test_regime_data.py \
  stock/etf/tests/test_run_regime_analysis.py -q
python3 -m ruff check \
  stock/etf/regime_rules.py \
  stock/etf/regime_evaluation.py \
  stock/etf/regime_data.py \
  stock/etf/run_regime_analysis.py \
  stock/etf/tests/test_regime_rules.py \
  stock/etf/tests/test_regime_evaluation.py \
  stock/etf/tests/test_regime_data.py \
  stock/etf/tests/test_run_regime_analysis.py
python3 -m ruff format --check \
  stock/etf/regime_rules.py \
  stock/etf/regime_evaluation.py \
  stock/etf/regime_data.py \
  stock/etf/run_regime_analysis.py \
  stock/etf/tests/test_regime_rules.py \
  stock/etf/tests/test_regime_evaluation.py \
  stock/etf/tests/test_regime_data.py \
  stock/etf/tests/test_run_regime_analysis.py
```

- [ ] **Step 2：按审查清单逐项检查**

- 禁止 `shift(-...)`、`bfill`、`center=True` 和全样本标准化。
- 预测代码不导入评估标签，不读取 `realized_score` 的未来行。
- 复权合并中不存在未来因子。
- 1 日与 3 日标签均按行位置而不是自然日偏移。
- 所有输出列都能追溯到设计文档，重复逻辑不造成两套公式。
- 非有限值不能进入 JSON，缺失标签不进入分母。
- 错误路径不会破坏既有输出。

- [ ] **Step 3：修复审查发现并提交**

```bash
git add stock/etf/regime_rules.py stock/etf/regime_evaluation.py stock/etf/regime_data.py stock/etf/run_regime_analysis.py stock/etf/tests/test_regime_rules.py stock/etf/tests/test_regime_evaluation.py stock/etf/tests/test_regime_data.py stock/etf/tests/test_run_regime_analysis.py
git commit -m "fix: harden causal regime analysis"
```

若没有需要修复的内容，不创建空提交。

## Task 8：真实 Tushare 正式运行与结果文档

**Files:**

- Create directory: `stock/etf/output/20260725_chuangyeban_trend/`
- Create: `stock/etf/20260725_chuangyeban_trend_results.md`

- [ ] **Step 1：下载并审计真实数据**

```bash
python3 -m stock.etf.run_regime_analysis \
  --symbol 159915.SZ \
  --start 2017-08-14 \
  --end 2026-07-25 \
  --download \
  --price-adjustment-mode point_in_time_adjusted \
  --output-dir stock/etf/output/20260725_chuangyeban_trend
```

结束日先由交易日历收缩到最近已完成开市日；2026-07-25 是周六时，行情最后日期应为
2026-07-24 或数据源实际可用的更早日期。正式样本外结果只运行一次，不根据结果调权重。

- [ ] **Step 2：独立复算正式结果**

从 `labeled_predictions.csv` 重新计算 1 日、3 日样本数、精确准确率、balanced
accuracy 和持久性基线，并与 `accuracy_by_period.csv`、`summary.json` 比对。检查：

```text
max_feature_source_date <= feature_asof_date < label_date
factor_source_date <= datetime
最新 1 日/3 日预测存在 prediction_for_date
最新未实现标签为空
```

- [ ] **Step 3：生成最终结果文档**

`stock/etf/20260725_chuangyeban_trend_results.md` 必须包含：

- 数据源、价格口径、请求区间、实际行情区间和样本数。
- 最新 1 日与 3 日预测的目标日期、分数、状态。
- 全期、开发期、验证期、样本外期及逐年准确率。
- 规则与持久性基线的 1 日、3 日 balanced accuracy 对比。
- 各状态样本数、混淆矩阵摘要和切换识别指标。
- 防穿越测试与数据审计结论。
- 是否同时超过两个样本外持久性基线，因而允许进入第二阶段。
- 结果局限：历史状态是规则定义而非不可观测的客观真值。

- [ ] **Step 4：运行完整 ETF 回归验证**

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
git diff --check
```

- [ ] **Step 5：只提交本任务文件**

```bash
git add \
  stock/etf/20260725_chuangyeban_trend_results.md \
  stock/etf/regime_rules.py \
  stock/etf/regime_evaluation.py \
  stock/etf/regime_data.py \
  stock/etf/run_regime_analysis.py \
  stock/etf/tests/test_regime_rules.py \
  stock/etf/tests/test_regime_evaluation.py \
  stock/etf/tests/test_regime_data.py \
  stock/etf/tests/test_run_regime_analysis.py
git commit -m "feat: deliver chuangyeban regime analysis"
```

输出目录若受 `.gitignore` 管理则保留为本机可复现产物，不强行加入版本库；结果文档必须
进入 `feature`。提交前再次确认没有暂存用户已有的 `stock/etf/data.py`、
`stock/etf/tests/test_data.py` 或其他无关文件。
