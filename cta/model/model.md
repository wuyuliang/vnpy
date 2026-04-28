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

### Step A：生成通用特征（vn.py 特征）

```bash
# 全频率批量
python3 -m cta.feature.run_all_features --interval all

# 仅 60min + 指定品种
python3 -m cta.feature.run_all_features --interval 60min --symbols RB0
```

### Step B：生成候选事件 + 训练样本（候选特征 + 通用特征拼接）

```bash
# RB0 60min（2010-2019）
python3 -m cta.model.feature.candidate_training_dataset \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --trade-side-mode both \
  --run-tag 20260427
```

产物示例：
- `cta/data/model_feature/minute60/RB0/20260427/*_candidate_events.parquet`
- `cta/data/model_feature/minute60/RB0/20260427/*_training_samples.parquet`

### Step C：训练三类模型（Trade Filter / Regime / MFE-MAE）

训练口径说明：
- `Trade Filter` / `Regime Classifier`：使用全量候选样本训练（包含已成交 + 未成交样本）。
- `MFE/MAE`：仅在 `is_executed==1` 样本上训练。

`--interval` 既支持单值，也支持**多值数组**（空格 / 逗号分隔皆可，重复值会去重）。
传多个 interval 时，pipeline 会按顺序依次跑每个周期，输出各自的模型目录与报告，
**互不覆盖**；任意单一 interval 报错也不会阻断其余 interval。

`symbol` 支持两种模式：
- 单品种：`--symbol RB0 --exchange SHFE`
- topN 品种：`--top-n-symbols N`（自动从 `cta/feature/symbols_research_ranking.csv` 读取前 N 名）

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
```

输出示例：
- `*_candidates.csv`
- `*_feature_table.csv`
- `*_predictions.csv`
- `*_metrics.csv`
- `*_top10_feature_importance.csv`（每个 signal/window/model 的 Top10 特征重要性，含 `feature_meaning`）
- `*_last_oot_decile_returns.csv`（最后 OOT 集合按模型分十档收益，仅统计已成交样本）
- `models/<signal_type>/window_xx/*.joblib`

训练日志会同步打印每个模型的 Top10 特征重要性（便于快速复盘）。

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
from cta.model.model_pipeline import _select_feature_columns

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
from cta.model.model_pipeline import _build_last_oot_decile_table

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
python3 -m unittest cta.model.tests.test_model_pipeline -v

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
