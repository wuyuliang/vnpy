# CTA 端到端运行手册（`run.md`）

本文给出从数据准备到模型训练、离线评估、仿真与实盘运维的可复制命令。

约定：
- 在仓库根目录执行：`/Users/wuyuliang/code/vnpy`
- Python 使用 `python3`
- 示例品种使用 `RB0`，可替换为其它品种

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

---

## 3. 生成候选样本（`cta/data/model_feature`，parquet-only）

### 3.1 规则 baseline 先跑一遍（支持 topN + 多 interval 候选机会）

按 `cta/feature/symbols_research_ranking.csv` 取前 N 个品种，批量生成多个周期的候选机会：

```bash
python3 -m cta.strategy.baseline_skill_suite \
  --top-n-symbols 10 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day,60min 30min,15min,5min,min \
  --start 2010-01-01 \
  --end 2019-12-31 \
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

### 3.2 候选事件 + 训练样本拼接（支持 topN + 多 interval）

```bash
python3 -m cta.model.feature.candidate_training_dataset \
  --top-n-symbols 10 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day 60min 30min 15min 5min min \
  --start 2010-01-01 \
  --end 2019-12-31 \
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

## 4. 训练三类模型 + 离线评估

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
  --generic-mode auto
```

### 4.2 topN 品种 + 多周期批量训练

```bash
python3 -m cta.model.model_pipeline \
  --top-n-symbols 10 \
  --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
  --interval day 60min 30min 15min 5min min \
  --start 2010-01-01 \
  --end 2019-12-31 \
  --train-end 2017-12-31 \
  --valid-end 2018-12-31 \
  --window-mode expanding \
  --max-walk-forward-windows 3 \
  --by-signal-type \
  --generic-mode auto
```

### 4.3 离线评估结果快速查看

```bash
ls -lah cta/report/backtest/*_model_pipeline/
```

查看最后 OOT 十分位收益（只统计已成交样本）：

```bash
cat cta/report/backtest/*_model_pipeline/*_last_oot_decile_returns.csv
```

`*_metrics.csv` 内含 IC / Sharpe / hit-rate 等离线指标，是判断模型是否值得进入回测阶段的门槛。
经验阈值：``oos_ic > 0.02`` 且 ``oos_hit_rate > 0.52`` 才推到下一步。

---

## 4.5 模型 → 回测 中间环节（**关键衔接**）

第 4 步 ``model_pipeline`` 输出的是**离线评估指标 + 模型权重**，并不是真正的 PnL 回测；
要从模型走到第 5 步带 PnL 的策略回测，**必须**显式做以下三件事：

### 4.5.1 选定模型文件

```bash
# brooks v3 路径（推荐用作模型驱动的样板）
ls -lah cta/strategy/brooks/models/ | tail -n 5
LATEST_MODEL=$(ls -t cta/strategy/brooks/models/xgb_*.ubj 2>/dev/null | head -n 1)
echo "model: $LATEST_MODEL"

# baseline 三件套训练出的模型
ls -lah cta/report/backtest/*_model_pipeline/models/
```

### 4.5.2 校验离线指标 vs 上线门槛

```bash
# 找出最新一轮离线评估
LATEST_RUN=$(ls -td cta/report/backtest/*_model_pipeline | head -n 1)
echo "latest run: $LATEST_RUN"
column -t -s, "$LATEST_RUN/$(ls $LATEST_RUN | grep _metrics.csv | head -n 1)"
```

不达标的模型**不要**进入第 5 步，回到第 3 / 第 4 重新调样本或参数。

### 4.5.3 把模型注入策略

- **Brooks v3**：``runner.py --model <path>``（见 5.3 第二条）。runner 内部加载 ``.ubj`` →
  在 ``BrooksV3LiveStrategy.on_bar`` 中调用模型 ``predict_proba`` 过滤候选 → 触发下单。
- **Baseline 三件套**（Donchian / ATR / BreakoutPullback）：当前 ``model_pipeline`` 训的
  ``trade_filter`` 模型默认仅用于离线评估；要在回测中应用，需要在 ``baseline_skill_suite``
  跑出的 raw ``trade_log`` 上按模型概率过滤后重算 PnL（写一个一次性脚本或扩展
  ``baseline_skill_suite`` 接收 ``--trade-filter-model`` 参数后再跑）。

### 4.5.4 单组合上线前 sanity check

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
  --out-dir cta/report/backtest/$(date +%Y%m%d)_double_ma_rb0 \
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
    out_dir=f"cta/report/backtest/$(date +%Y%m%d)_tight_range_rb0_cta",
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
    out_dir=f"cta/report/backtest/$(date +%Y%m%d)_tight_range_multi",
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

> 月度收益看板：``open cta/report/backtest/<run_dir>/monthly_pnl_pivot.csv``，
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

```bash
python3 - <<'PY'
from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim
from cta.strategy.brooks.online.live_strategy import BrooksV3LiveStrategy

cfg = SimRunConfig(
    strategy_class=BrooksV3LiveStrategy,
    strategy_name="BrooksV3LiveRB0",
    vt_symbol="RB0.SHFE",
    setting={
        "config_path": "cta/strategy/brooks/config/strategy.yaml",
        "model_path": "none",
        "trade_log_dir": "cta/report/live/trade_log",
        "initial_capital": 1_000_000.0,
    },
)
sim = SimnowSetting(
    userid="你的SimNow账号",
    password="你的SimNow密码",
)
main_engine = run_sim(cfg, sim)
print("strategy started; keep process running")
PY
```

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

### 7.2 每日对账报告（live vs backtest）

```bash
python3 - <<'PY'
import pandas as pd
from cta.live.daily_report import write_daily_report

live_trade_log = pd.read_parquet("cta/report/live/trade_log/RB0_SHFE_live.parquet")
live_equity = pd.read_csv("cta/report/live/equity.csv")["equity"]
dates = pd.to_datetime(pd.read_csv("cta/report/live/equity.csv")["datetime"])
backtest_trade_log = pd.read_parquet("cta/strategy/brooks/report/<ts>/per_run/RB0_SHFE/trades.parquet")

res = write_daily_report(
    live_trade_log=live_trade_log,
    live_equity=live_equity,
    dates=dates,
    out_dir="cta/report/live/daily",
    title="RB0 Daily Reconcile",
    backtest_trade_log=backtest_trade_log,
)
print(res.report_path)
PY
```

### 7.3 连接守护（Supervisor）示例

```bash
python3 - <<'PY'
import threading
from cta.live.supervisor import Supervisor

# 这里假设你已持有一个已启动的 main_engine 对象
# sup = Supervisor(main_engine, gateway_name="CTP", connect_setting=sim_setting, check_interval=10.0)
# stop = threading.Event()
# sup.loop(stop)
print("see cta/live/supervisor.py for integration template")
PY
```

---

## 8. 关键输出目录

- 原始行情：`cta/data/origin/`
- 通用特征：`cta/data/feature/`
- 候选训练样本（parquet-only）：`cta/data/model_feature/`
- 模型与离线评估：`cta/report/backtest/*_model_pipeline/`
- 策略回测报告：`cta/report/backtest/`
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
