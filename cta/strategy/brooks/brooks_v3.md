# Brooks v3 实施方案

> 基于 `brooks_v2.md` 的任务目标,结合 `cta/feature/` 已有 180+ pa_* 预计算特征,
> 原地替换 v1 规则骨架。本文档是实施蓝图(spec),与 `brooks.md` / `brooks_v2.md` 并列。
>
> **日期**: 2026-04-19
> **分支**: feature
> **状态**: 实施中

---

## 1. Context

v1 (`cta/strategy/brooks/` 现状) 已跑通 3 个规则骨架策略 × 8 品种日线,结果见
`cta/strategy/brooks/report/20260419_103632/`。但 v1 存在以下与 `brooks_v2.md` 不符之处:

1. **自造指标/模式**:v1 自己写了 `indicators/price_action.py` + `patterns/{tight_range,breakout,pullback,high2}.py`,而 `cta/feature/` 已经有 180+ 个 pa_* Brooks 特征预计算完成(详见 `cta/feature/FEATURES.md`),重复造轮子且口径可能不一致。
2. **单周期**:只用日线,没有 HTF(日) → MTF(60/30m) → LTF(5/1m) 共振。
3. **仓位固定 1 手**:未按 ATR 止损距离 + 账户风险百分比动态 sizing,也没有组合层回撤降仓。
4. **日志简陋**:只输出 vnpy trades.csv,缺少 MFE/MAE、退出原因、信号触发 bar 的特征快照。
5. **无模型评分**:brooks_v2.md 要求 XGBoost 打分门控,v1 没有。
6. **代码结构未分离**:`strategies/` 把回测和策略逻辑耦合,在线化需要大改。
7. **品种选择不系统**:v1 硬编码 8 品种,未按研究优先级排序,扩展困难。

v3 目标(**原地替换** v1):消费已有 pa_* 特征、多周期共振、动态风控(含 -2% 回撤降仓)、
完整成交日志、完整 XGBoost 训练+评分回路、核心逻辑抽到 `core/` 让 `backtest/` 和 `online/` 共享、
**品种按 `cta/feature/symbols_research_ranking.csv` 排序并通过参数选 top-N 参与**。

---

## 2. 品种选择(重要)

### 2.1 排序来源
`cta/feature/symbols_research_ranking.csv` 按 `research_rank` 升序给出 71 个中国商品连续合约,
分 A/B/C/D 四档。v3 的策略候选池和模型训练池统一从这里取。

前 20 个(A 档)顺序:
```
 1 RB0.SHFE  螺纹钢     11 TA0.CZCE  PTA
 2 HC0.SHFE  热轧卷板   12 EG0.DCE   乙二醇
 3 I0.DCE    铁矿石     13 PP0.DCE   聚丙烯
 4 JM0.DCE   焦煤       14 L0.DCE    塑料
 5 J0.DCE    焦炭       15 V0.DCE    PVC
 6 M0.DCE    豆粕       16 CU0.SHFE  铜
 7 P0.DCE    棕榈油     17 AL0.SHFE  铝
 8 Y0.DCE    豆油       18 ZN0.SHFE  锌
 9 OI0.CZCE  菜油       19 AU0.SHFE  黄金  (B 档起)
10 MA0.CZCE  甲醇       20 AG0.SHFE  白银
```

### 2.2 可调参数
`config/strategy.yaml` 新增:
```yaml
symbols:
  ranking_csv: cta/feature/symbols_research_ranking.csv
  top_n: 8              # 取前 N 个排名(策略回测 + 模型训练统一入口)
  max_tier: "A"         # 仅取 A/B/C/D 档及以上
  require_feature_interval: "minute5"  # 过滤掉 feature 目录下没有该 interval 特征的品种
  explicit_include: []  # 额外强制包含
  explicit_exclude: []  # 强制排除
```

CLI 同时支持覆盖:
```bash
python3 -m cta.strategy.brooks.backtest.runner --top-n 5
python3 -m cta.strategy.brooks.backtest.runner --symbols RB0.SHFE,I0.DCE
python3 -m cta.strategy.brooks.core.model.train_xgb --top-n 10 --max-tier A
```

### 2.3 解析逻辑(`config/symbols.py::resolve_symbols`)
1. 读 ranking CSV → 按 research_rank 升序
2. `max_tier` 过滤:`tier <= max_tier` (A < B < C < D)
3. `explicit_exclude` 剔除
4. `require_feature_interval`: 对每个 symbol,`list_symbol_dates(sym, interval)` 非空才保留
5. 取前 `top_n` 个
6. `explicit_include` 追加到尾部(若 feature 存在)

`symbol + exchange` → `vt_symbol`,同时查表获取 `contract_multiplier` / `price_tick` /
`rate` / `slippage`(默认值 + 可选覆盖)。

---

## 3. 目标目录结构(原地替换 v1)

```
cta/strategy/brooks/
├── brooks.md                       # 原任务书(保留)
├── brooks_v2.md                    # v2 任务书(保留)
├── brooks_v3.md                    # 本实施方案(本文件)
├── README.md                       # 重写为 v3 运行说明
├── __init__.py                     # 新模块导出
├── config/
│   ├── params.py                   # BrooksV3Params:阈值 + 风控 + 模型参数(从 yaml 加载)
│   ├── symbols.py                  # resolve_symbols() + ContractMeta 查表
│   └── strategy.yaml               # 路径/周期/风险/模型/品种筛选 参数
├── core/                           # 离线/在线共享,纯计算 + dataclass,无 vnpy 依赖
│   ├── __init__.py
│   ├── features/
│   │   ├── __init__.py
│   │   └── adapter.py              # 统一 offline(feature_loader) / online(FeatureGenerator)
│   ├── signal/
│   │   ├── __init__.py
│   │   ├── htf_bias.py             # 日线 bull/bear/sideway 分类
│   │   ├── mtf_setup.py            # 60/30m pullback/H2 setup
│   │   └── ltf_entry.py            # 5/1m breakout 确认
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── sizing.py               # 0.1% per trade + ATR 止损距离
│   │   ├── stops.py                # ATR 止损/移动止损/失败快退/max-holding
│   │   └── portfolio.py            # -2%/-5% 回撤分级降仓
│   ├── model/
│   │   ├── __init__.py
│   │   ├── labeler.py              # MFE/MAE + RR 目标标注
│   │   ├── dataset.py              # pa_* 特征 × 标签 → 时序划分
│   │   ├── train_xgb.py            # XGBoost 训练脚本(带 CLI)
│   │   └── score_gate.py           # 模型加载 + 阈值门控
│   ├── trade_log.py                # TradeRecord + parquet writer (顶层,非 logging/ 子目录避开标准库名)
│   └── strategy.py                 # BrooksV3Core:HTF→MTF→LTF→gate→risk→log
├── backtest/
│   ├── __init__.py
│   ├── engine.py                   # 封装 vnpy BacktestingEngine,注入 FeatureAdapter
│   ├── runner.py                   # CLI:策略 × 品种 × 时段
│   └── reporter.py                 # round-trip FIFO + stats.json + report.md
├── online/
│   ├── __init__.py
│   ├── runner.py                   # 分钟级流式驱动,dry-run 支持
│   └── live_strategy.py            # CtaTemplate 外壳,桥接 core.strategy
├── models/                         # 训练产物(.ubj + metadata.json)
└── report/<YYYYMMDD_HHMMSS>/       # 每次运行输出
```

**删除**:`cta/strategy/brooks/{indicators,patterns,strategies}/` 全部;v1 `backtest/runner.py` 会被新版覆盖。

---

## 4. 分阶段交付

### Phase A — 清理 v1 + 搭空架子
- 删除 `indicators/` `patterns/` `strategies/`(v1 的手写指标/模式/策略)
- 保留并扩展 `config/params.py` `config/symbols.py`
- 新增 `config/strategy.yaml` 作为运行期配置中心
- 所有新目录创建 `__init__.py`

### Phase B — `core/features/adapter.py`
复用 `cta.feature.feature_loader` / `cta.feature.online`:
- 离线:`load_symbol_features(symbol, interval, start, end)` + `load_symbol_feature_at(...lookback='200ms')`
- 在线:`FeatureGenerator(interval).warmup(df).update(bar)`
- 统一接口 `FeatureAdapter.get(symbol, interval, ts) → pd.Series`、`get_range(symbol, interval, start, end) → pd.DataFrame`

### Phase C — `core/signal/`(HTF → MTF → LTF)

使用的 pa_* 列(从实际 parquet 确认):
- HTF(day): `pa_always_in_dir`、`pa_trend_strength_{3,10,20}`、`pa_ema_slope_20`、`pa_ema_slope_60`、`pa_ema_slope_accel`
- MTF(60m/30m): `pa_pullback_depth_{10,20,60}`、`pa_h123`、`pa_two_leg_pullback`、`pa_tight_channel_{10,20}`、`pa_pullback_to_ema_20`
- LTF(5m/1m): `pa_breakout_up_{3,10,20}`、`pa_breakout_strength_{3,10,20}`、`pa_breakout_fail_{3,10,20}`、`pa_signal_strength_{3,10,20}`、`pa_expected_rr`、`pa_is_trend_bar`

每层产出统一的 `Signal(direction, setup_type, strength, trigger_price, stop_price, features)` dataclass。

### Phase D — `core/risk/`
- `sizing.py`:`qty = floor(equity * risk_pct * leverage_mult / (stop_distance * contract_multiplier))`,0 则 warning 并跳过
- `stops.py`:ATR 止损 / 2×ATR 移动止损 / 3 根 fail-exit / max 30 根持仓,全部纯函数
- `portfolio.py`:`PortfolioRiskManager` 跟踪 peak_equity + current_dd,返回 leverage_mult(-2% → 0.5x,-5% → 0.25x,新高回 1.0x)

### Phase E — `core/model/` XGBoost 完整回路
- `labeler.py`:setup 时点 t 向前 N bar,`label = 1 if 先触达 target_rr*stop_distance 再比止损优先,否则 0`
- `dataset.py`:按 top_n 品种聚合所有 setup,time-based split(2018-2021 train / 2022 val / 2023-2024 test)
- `train_xgb.py`:`XGBClassifier` + early stopping,输出 `.ubj` + `metadata.json`
- `score_gate.py`:`ScoreGate.load(path).score(features) → (prob, accept)`,阈值默认 0.55

### Phase F — `core/strategy.py`
`BrooksV3Core.on_bar(ts, symbol, bar)`:
1. FeatureAdapter 拉 day/MTF/LTF 三路特征
2. HTF bias → MTF setup → LTF entry,任何一层不过则返回
3. score_gate.score(features) → 不过阈值记 rejected_by_model 并返回
4. portfolio_risk.leverage → sizing → 下单,注册 stops
5. on_trade → 写 TradeRecord

### Phase G — `backtest/` + `online/`
- `backtest/engine.py`:`run_single(strategy_key, vt_symbol, start, end, capital, model_path)`
- `backtest/runner.py`:CLI `--top-n --symbols --start --end --capital --model [path|none]`
- `backtest/reporter.py`:round-trip FIFO + `stats.json` + `report.md`(含 model_prob 分位表 + 降仓事件)
- `online/runner.py`:订阅 bar → FeatureGenerator.update,`--dry-run` 只打日志
- `online/live_strategy.py`:CtaTemplate 子类 → delegate `BrooksV3Core(mode="online")`

### Phase H — 测试 + 最小可复现实验
`cta/tests/`:
- `test_brooks_v3_symbols.py`:`resolve_symbols(top_n=3)` 在 ranking 前 3 名(RB0/HC0/I0)中取交集
- `test_brooks_v3_adapter.py`:offline vs online 同 ts 特征一致性(1e-6 精度)
- `test_brooks_v3_signal.py`:合成 bar 喂入 HTF/MTF/LTF 分别命中
- `test_brooks_v3_risk.py`:sizing 数学 + -2%/-5% 降仓
- `test_brooks_v3_labeler.py`:已知序列标签正确

### Phase I — change_log.md + README.md 更新

---

## 5. 关键文件清单

**新增**:
- `config/strategy.yaml`
- `core/**` 所有 .py
- `backtest/{engine,runner,reporter}.py`
- `online/{runner,live_strategy}.py`
- `cta/tests/test_brooks_v3_*.py`

**修改**:
- `config/params.py`(从 yaml 加载)
- `config/symbols.py`(ranking CSV 驱动)
- `README.md`(改写为 v3)
- `cta/report/change_log.md`(追加)
- `__init__.py`(新导出)

**删除**(全目录):
- `indicators/`  `patterns/`  `strategies/`
- v1 `backtest/runner.py` 会被新版覆盖

---

## 6. 复用的现有代码

- `cta/feature/feature_loader.py`:`load_symbol_features` / `load_symbol_feature_at(lookback='200ms')` / `list_feature_symbols` / `list_symbol_dates`
- `cta/feature/online.py`:`FeatureGenerator(interval).warmup(df).update(bar)` / `compute_features` / `compute_latest_features`
- `cta/feature/FEATURES.md`:pa_* 特征列目录(实际为 180 列,已与 parquet 核对)
- `cta/feature/symbols_research_ranking.csv`:品种排序
- `cta/data/day/{SYMBOL}.csv` + `cta/data/minute/{symbol}/YYYY-MM-DD.parquet` + `cta/data/feature/{interval}/{symbol}/YYYY-MM-DD.parquet`
- `vnpy.trader.database.get_database().load_bar_data()`:日线已导入 SQLite
- `vnpy_ctastrategy.backtesting.BacktestingEngine`:回测引擎(同 v1)
- `vnpy.trader.constant.Exchange`:品种交易所映射

---

## 7. 验证

1. **单元测试**:`python3 -m pytest cta/tests/test_brooks_v3_*.py -v` 全绿
2. **品种解析**:`python3 -m cta.strategy.brooks.config.symbols --top-n 3` 输出 `['RB0.SHFE','HC0.SHFE','I0.DCE']`(若 HC0/I0 缺失 minute5 特征则自动回退到下一名)
3. **离线/在线特征一致性**:`test_brooks_v3_adapter` 抽 10 个 (symbol, interval, ts) 逐列 ≤ 1e-6
4. **规则回测 sanity**(无模型,top-3):
    ```bash
    python3 -m cta.strategy.brooks.backtest.runner \
        --top-n 3 --start 2022-01-01 --end 2024-12-31 \
        --capital 1000000 --model none
    ```
    预期:report/<ts>/trades.parquet 非空,round-trip 含 MFE/MAE/exit_reason
5. **XGBoost 训练**:
    ```bash
    python3 -m cta.strategy.brooks.core.model.train_xgb \
        --top-n 8 --start 2018-01-01 --end 2022-12-31 \
        --target-rr 2.0 --target-bars 20 \
        --out-dir cta/strategy/brooks/models
    ```
    预期:`xgb_<ts>.ubj` + metadata.json,val AUC 记录
6. **带模型 out-of-sample**:
    ```bash
    python3 -m cta.strategy.brooks.backtest.runner \
        --top-n 8 --start 2023-01-01 --end 2024-12-31 \
        --model cta/strategy/brooks/models/xgb_<ts>.ubj
    ```
    预期:相对无模型 baseline,win_rate 或 profit_factor 其一改善 OR max_dd 减小
7. **回撤降仓**:report.md 含 "leverage changes" 表
8. **在线冒烟**:
    ```bash
    python3 -m cta.strategy.brooks.online.runner \
        --symbols RB0.SHFE --interval 5m --dry-run
    ```

---

## 8. 风险与注意

- **特征覆盖**:实际截至 2026-04-19 `cta/data/feature/` 仅有 RB0 一个品种的特征。v3 通过 `require_feature_interval` 自动过滤,`top_n=8` 可能只返回 1 个品种。需配合先跑 `cta.feature.run_all_features` 扩充。
- **多周期对齐**:vnpy `BacktestingEngine` 一次一个周期。day/MTF 特征在 LTF 每根 bar 触发时用 `load_symbol_feature_at(..., lookback='200ms')` 拉最近已收盘 bar,防未来函数。
- **样本量**:top_n=8 × 7 年 × 5m 级 setup 数量应足够;若稀疏则放宽 setup 阈值或合并品种训练
- **0.1% per trade**:日线 ATR 下可能算出 0 手,跳过并记录;v3 主力放 5m
- **组合回撤降仓**:`BacktestingEngine.daily_results` 提供 equity 曲线;实时估算 equity=已平仓 PnL + 浮盈
- **过拟合**:time-series split + early stopping + 高强度特征单调约束
- **v1 报告保留**:`cta/strategy/brooks/report/20260419_103632/` 不删,作为 baseline
- **YAML**:`config/strategy.yaml` 默认;CLI `--config <path>` 可覆盖
