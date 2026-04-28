# Brooks v3 中国 CTA 策略

基于 Al Brooks 价格行为学,消费 `cta/feature/` 预计算的 ~100 个 pa_* 特征
(实测计数：`grep -h '"pa_' cta/feature/price_action*.py | grep -oE '"pa_[a-zA-Z0-9_]+"' | sort -u | wc -l`),做
**HTF(day) → MTF(minute60) → LTF(minute5) 多周期共振**,
加 **ATR 止损 + 0.1% per-trade 风控 + 组合回撤降仓**,最终由
**XGBoost 评分门控**决定是否入场。

离线/在线共享同一套 `core/` 代码,分别由 `backtest/` 和 `online/` 包装。

v1 (仅日线 + 自造指标 + 固定 1 手) 已被 v3 原地替换;v1 报告保留在
`report/20260419_103632/` 作为对比基线。

---

## 目录

```
cta/strategy/brooks/
├── brooks.md / brooks_v2.md / brooks_v3.md   # 任务书与实施方案
├── config/
│   ├── strategy.yaml                 # 所有运行时参数(周期/阈值/风控/模型)
│   ├── params.py                     # 从 yaml 加载 → BrooksV3Params
│   └── symbols.py                    # 按 ranking CSV 解析 top_n 品种
├── core/                             # 离线/在线共享,无 vnpy 依赖
│   ├── features/adapter.py           # 统一特征接口
│   ├── signal/{htf_bias,mtf_setup,ltf_entry}.py
│   ├── risk/{sizing,stops,portfolio}.py
│   ├── model/{labeler,dataset,train_xgb,score_gate}.py
│   ├── trade_log.py                  # TradeRecord + parquet writer
│   └── strategy.py                   # BrooksV3Core 组合器
├── backtest/
│   ├── engine.py                     # 自研轻量回测(不依赖 vnpy)
│   ├── runner.py                     # 批量 CLI
│   └── reporter.py                   # summary.csv + report.md
├── online/
│   ├── runner.py                     # dry-run 回放驱动
│   └── live_strategy.py              # vnpy CtaTemplate 壳
├── models/                           # 训练产物 (.ubj + .meta.json)
└── report/<YYYYMMDD_HHMMSS>/         # 每次运行输出
```

---

## 快速开始

### 1. 规则回测(无模型 sanity)

```bash
python3 -m cta.strategy.brooks.backtest.runner \
    --top-n 3 --start 2023-01-01 --end 2024-12-31 \
    --capital 1000000 --model none
```

当前只有 `RB0.SHFE` 已生成 minute5 特征,其它品种会被 `resolve_symbols` 自动过滤,
因此 `--top-n 3` 实际跑 1 品种。扩容时先跑 `cta/feature/run_all_features.py`。

输出:`cta/strategy/brooks/report/<ts>/summary.csv` + `report.md` + `per_run/<sym>/trades.parquet`。

### 2. 训练 XGBoost

**前置**:macOS 需 `brew install libomp`,xgboost 才能加载。

```bash
python3 -m cta.strategy.brooks.core.model.train_xgb \
    --top-n 8 --start 2018-01-01 --end 2022-12-31 \
    --target-rr 2.0 --target-bars 20 \
    --out-dir cta/strategy/brooks/models
```

时序划分:train 2018-2021 / val 2022(early stopping)/ test 留给 OOS 回测。

输出:`xgb_<ts>.ubj` + `xgb_<ts>.meta.json`(feature_cols、AUC、阈值建议)。

### 3. 带模型 OOS 回测

```bash
python3 -m cta.strategy.brooks.backtest.runner \
    --top-n 8 --start 2023-01-01 --end 2024-12-31 \
    --model cta/strategy/brooks/models/xgb_<ts>.ubj
```

`--model` 支持 `none`(关闭)、具体路径、默认不传(自动取 `models/` 下最新)。

### 4. 在线 dry-run 冒烟

```bash
python3 -m cta.strategy.brooks.online.runner \
    --symbol RB0.SHFE --interval minute5 \
    --warmup-start 2024-01-01 --warmup-end 2024-03-31 \
    --live-start 2024-04-01 --live-end 2024-06-30 --dry-run
```

从 parquet 回放 bar 流,不下真实单,仅打日志 + 写 `trades_dryrun.parquet`。

---

## 参数配置

`config/strategy.yaml` 是唯一真实来源,可通过 `BrooksV3Params` 直接读,
或在 CLI 通过 `--config <path>` 指定。关键字段:

- `symbols.top_n` / `max_tier` / `require_feature_interval` / `explicit_include/exclude`
- `intervals.htf/mtf/ltf`
- `signal.htf/mtf/ltf.*` 阈值(HTF 趋势强度、MTF pullback 深度范围、LTF 突破强度)
- `risk.per_trade_risk_pct=0.001`, `stop_atr_mult=1.0`, `trailing_atr_mult=2.0`,
  `max_holding_bars=30`, `fail_exit_bars=3`
- `risk.portfolio.dd_threshold_half=0.02`(-2% 降到 0.5x), `dd_threshold_quarter=0.05`
- `model.enabled/threshold/target_rr/target_bars`, `model.xgb.*`

---

## 品种选择(ranking-driven)

`cta/feature/symbols_research_ranking.csv` 已按研究打分降序排列 71 个品种,分 A/B/C/D 档。
`resolve_symbols(cfg)` 按以下规则选:

1. 读 ranking CSV → 按 `research_rank` 升序
2. `max_tier` 过滤(A < B < C < D)
3. `explicit_exclude` 剔除
4. `require_feature_interval` 过滤(对应 `cta/data/feature/<interval>/<symbol>/*.parquet` 非空)
5. 截取前 `top_n`
6. `explicit_include` 追加

独立工具:

```bash
python3 -m cta.strategy.brooks.config.symbols --top-n 8 --max-tier B --interval minute5
```

---

## 策略假设与规则摘要

- **假设**:A 档流动性品种趋势段常在 HTF 形成明显 bias,MTF 做 h1/h2/h3 回调浅深不等,
  LTF 在 bar 级别给出清晰 breakout 触发。pa_* 特征已把这些形态量化成数值列。
- **信号**:
  - HTF bull = `sign(pa_always_in_dir) > 0` 且 `|pa_trend_strength_20|` 过阈值
    且 `pa_ema_slope_20` 同向。
  - MTF setup = `pa_h123 ∈ {1,2,3}`,`pa_pullback_depth_20 ∈ [0.10, 0.95]`,
    `pa_two_leg_pullback / pa_tight_channel_*` 加分。
  - LTF entry = `pa_breakout_up_10 == 1`,`pa_breakout_strength_10 > θ`,
    `pa_signal_strength_5 > θ`,`pa_expected_rr > 1.2`。
- **仓位**:`qty = floor(equity * 0.001 * leverage_mult / (stop_dist * contract_size))`。
- **止损**:ATR(stop_atr_mult=1.0)初始止损 + ATR(trailing_atr_mult=2.0)移动止损
  + 3 根 fail-exit + 30 根 max-holding。
- **组合风控**:peak-to-now drawdown > 2% → 杠杆 0.5x;> 5% → 0.25x;新高恢复 1.0x。
- **模型门控**:pa_* + HTF/MTF 上下文 → XGBoost prob >= threshold 才入场;
  透传模式 `--model none` 关闭。
- **方向**:v3 只做多(`pa_h123` 仅 bull);bear 侧待 `pa_l123` 落地后补。

---

## 成交日志字段

`trades.parquet` 每一行一个 round-trip,含:

- 组合键:`vt_symbol`, `open_ts`, `close_ts`, `direction`, `qty`
- 价格:`entry_price`, `exit_price`, `initial_stop`, `final_stop`
- 绩效:`pnl`, `pnl_net`(扣手续费+滑点), `mfe`, `mae`, `holding_bars`
- 上下文:`setup_type` (h1/h2/h3), `htf_direction`, `htf_trend_strength`,
  `mtf_pullback_depth`
- 模型:`model_prob`, `model_accepted`
- 退出:`exit_reason` ∈ {`atr_stop`, `trailing_stop`, `fail_exit`, `max_holding`}
- 风控快照:`leverage_mult`, `equity_at_open`
- 特征快照:`features_json`(JSON 字符串,便于事后再训练/归因)

---

## 已知局限

1. **minute5 特征仅 RB0 就绪**:扩品种前需先跑 `cta/feature/run_all_features.py`。
2. **macOS + xgboost**:需 `brew install libomp`,否则 `import xgboost` 失败。
3. **单方向(多)**:`pa_h123` 只编码 bull 侧;空头需等 `pa_l123` 实现。
4. **换月跳点**:连续合约拼接未做特殊处理,跳点附近可能误触发。
5. **在线 HTF/MTF 预热**:`online/runner.py` dry-run 模式用 offline 读的方式预热 HTF/MTF,
   真实 vnpy 网关接入时需要通过 FeatureGenerator 补完实时状态同步。
6. **组合层 equity 估算**:`BrooksV3Core` 每根 bar 从已实现 PnL + 浮动 PnL 估算 equity,
   精度可接受但非净值曲线逐笔校准(换券/资金调整不覆盖)。

---

## 迁移自 v1

- 保留:`config/symbols.py` 架构(扩展为 ranking-driven)、任务书 `brooks.md` / `brooks_v2.md`、
  旧报告目录 `report/20260419_103632/`。
- 删除:`indicators/`, `patterns/`, `strategies/`, v1 `backtest/runner.py`。
- 替换:核心三层(core/backtest/online)+ 所有新的 pa_* 特征消费路径。

详见 `brooks_v3.md` 和 `cta/report/change_log.md` 2026-04-19 条目。
