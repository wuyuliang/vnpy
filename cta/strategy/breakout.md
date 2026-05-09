
# `cta/strategy` 策略实现说明

本文档对应策略代码：
- `cta/strategy/skill_tight_range_breakout.py`
- `cta/strategy/skill_tight_range_backtest.py`

## 1. 策略假设
1. 商品在窄幅整理后，向上/向下突破更容易形成可交易波段。
2. 仅“突破质量”较高的信号值得参与。
3. 与更大级别趋势同向的突破更稳定。

## 2. 适用品种
- 本地优先验证：`RB0`（螺纹钢连续）
- 服务器扩展：`cta/data/origin/day/` 下其它连续合约（如 `HC0`/`I0`/`MA0`/`TA0` 等）

## 3. 适用周期
- 当前实现：`day/minute60/minute30/minute15/minute5/minute`
- CLI 输入别名：`day, 60min, 30min, 15min, 5min, min`

## 4. 信号定义
1. `tight_range_breakout.detect_tight_range` 检测窄幅区间与上下边界。
2. `resolve_breakout_trigger` 生成入场触发价与初始止损参考。
3. `breakout_quality.score_breakout` + gate 过滤低质量突破。
4. `market_regime.compute_trend_state` 作为方向过滤（可配置关闭）。

## 5. 开仓规则
1. 当前 bar 满足 tight-range 有效。
2. breakout quality 分数 >= 阈值。
3. （可选）方向与 trend filter 同向。
4. 方向模式过滤：`both/long/short`。
5. 下一根 bar 使用 stop 单触发入场。

## 6. 平仓规则
1. 触发 ATR 跟踪止损（在策略层判断）后下一根 bar 市价平仓。
2. 持仓超过 `max_holding_bars` 后下一根 bar 市价平仓。

## 7. 止损规则
1. 初始止损：`entry_price +/- ATR * initial_stop_atr_mult`
2. 跟踪止损：
   - 多头：`highest_since_entry - ATR * trailing_stop_atr_mult`
   - 空头：`lowest_since_entry + ATR * trailing_stop_atr_mult`

## 8. 仓位管理规则
1. 最小固定手数 `lots`。
2. 按单笔风险预算反推手数（`risk_per_trade_pct`），最终取二者较大值。

## 9. 手续费与滑点假设
1. 优先使用 `cta/strategy/brooks/config/symbols.py` 的合约元数据。
2. 若缺失则回退 `cta/config/futures_meta.py`。
3. 仍缺失时使用默认值：`size=10, tick=1, rate=1e-4, slippage_ticks=1`。

## 10. 可能失效的市场环境
1. 低波动震荡且假突破频发。
2. 高频跳空阶段，next-open 成交与止损期望偏离。
3. 成交量异常失真导致 breakout quality 失效。

## 11. 配置文件
- `cta/config/skill_tight_range_breakout_config.py`
  - `StrategyConfig`：策略参数
  - `BacktestConfig`：数据路径、输出路径、资金参数
  - 关键新增：
    - `trade_side_mode`: `both/long/short`
    - `interval`: 支持 `day/60min/30min/15min/5min/min`

## 12. 最小可运行回测命令（RB）
```bash
python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 \
  --exchange SHFE \
  --interval day \
  --trade-side-mode both \
  --start 2018-01-01 \
  --end 2024-12-31
```

## 12.1 分钟级回测命令（RB 60min）
```bash
python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --trade-side-mode both \
  --start 2020-01-01 \
  --end 2020-01-31
```

## 13. 其它品种回测命令（服务器兼容）
```bash
python3 -m cta.strategy.skill_tight_range_backtest \
  --symbol MA0 \
  --exchange CZCE \
  --interval 30min \
  --trade-side-mode short \
  --start 2018-01-01 \
  --end 2024-12-31
```

若不传 `--exchange`，程序会从 `cta/data/origin/day/symbols_list.csv` 自动解析。

## 14. 输出结果位置
- 目录：`cta/report/backtest/YYYYMMDD_skill_tight_range_breakout_<SYMBOL>_<INTERVAL>_<MODE>/`
- 产物：
  - `YYYYMMDD_<SYMBOL>_<MODE>_trades.csv`
  - `YYYYMMDD_<SYMBOL>_<MODE>_equity.csv`
  - `YYYYMMDD_<SYMBOL>_<MODE>_summary.csv`

## 15. 已知局限
1. 分钟级数据按“每日 parquet 文件”聚合读取，长时间区间会增加 I/O 开销。
2. 平仓由策略在当前 bar 判断并在下一 bar 开盘执行，和真实盘中触发仍有差异。
3. 组合层资金曲线/风险预算还未做多品种联动管理。

## 16. Baseline Skill Suite（四套纯规则基线）

新增实现文件：
- `cta/strategy/baseline_skill_suite.py`
- `cta/config/baseline_skill_suite_config.py`

包含四个 baseline：
1. `donchian_breakout`
2. `atr_breakout`
3. `tight_range_breakout`
4. `breakout_pullback_continuation`

### 16.1 pre-2020 训练样本构造命令（RB0 60min）
```bash
python3 -m cta.strategy.baseline_skill_suite \
  --symbol RB0 \
  --exchange SHFE \
  --interval 60min \
  --trade-side-mode both \
  --start 2000-01-01 \
  --end 2019-12-31
```

### 16.2 训练样本输出
- 顶层目录：`cta/report/backtest/YYYYMMDD_baseline_skill_suite_RB0_minute60_both/`
- 关键文件：
  - `YYYYMMDD_RB0_minute60_both_training_samples.csv`（主路径产物，候选样本汇总）
  - `YYYYMMDD_RB0_minute60_both_suite_summary.csv`（策略级 metrics 汇总）
  - `YYYYMMDD_RB0_minute60_both_baseline_report.md`
- 子目录（每个 signal_type 一个）：
  - `donchian_breakout/`、`atr_breakout/`、`tight_range_breakout/`、`breakout_pullback_continuation/`
  - 每个子目录含 `*_<st>_trades.csv` / `*_<st>_equity.csv` / `*_<st>_summary.csv`

### 16.3 训练样本字段

实际产出按 `run_baseline_suite` 的两条分支区分：

**主路径（候选事件扫描，`generate_candidate_opportunities` 产出）**
- 基础列：`symbol`, `exchange`, `interval`, `signal_type`, `side`
- 时间列：`signal_datetime`（signal bar 时间 = 决策时刻）、`datetime`（entry bar 时间 = 成交时刻 / 标签 anchor）
- 决策列：`trigger`（触发价）、`candidate_status`（filled/filtered/not_triggered）、`is_executed`、`is_filtered`、`filtered_reason`
- 标签列：`future_mfe_atr`、`future_mae_atr`、`future_pnl_atr`、`atr_warmed`、`label_class`、`regime_label`
- 特征列：`feature_close / feature_volume / feature_atr14 / feature_don_upper_entry / feature_breakout_score / feature_bp_breakout_level / ...`（按 `TRAINING_FEATURE_COLUMNS` 自动派生 `feature_*`，**全部从 signal bar 取**，因果可推理）

**Fallback 路径（candidate 扫描为空时退到 `build_training_samples_from_trade_log`）**
- 基础列同上 + `signal_i`、`entry_i`、`exit_i`、`holding_bars`、`entry_price`、`exit_price`
- 时间列：与主路径一致，`signal_datetime` / `datetime` 双时间戳。
- 标签列：`label_gross_pnl`、`label_cost`、`label_net_pnl`、`label_win`、`label_mfe_atr`、`label_mae_atr`、`atr_warmed`
- L2 修复后特征列同样从 `signal_row = frame.iloc[entry_i - 1]` 取，避免 next-bar 穿越。
- 注：D6 修复后 fallback 路径也写 `atr_warmed`（0/1），下游 `_ensure_training_columns` 不再误把 warmup 行带进训练。

> **⚠️ 时间口径硬性要求**：下游 `merge_candidate_and_generic_features` 拼 generic
> 特征**必须**用 `signal_datetime` 做 merge_asof key。用 `datetime` 会让 generic 特征
> 拿到 entry_bar (i+1) 时刻的值，构成 next-bar lookahead leak。详见
> `cta/model/feature/candidate_vs_executed_samples.md` § 4.4。

### 16.4 模型部署：`<model>_features.csv` 清单

`run_model_pipeline` 在每个 `models/<signal_type>/window_xx/<model>.joblib` 旁边
都会同步写一份 `<model>_features.csv`：列 `rank / feature / importance /
feature_meaning / model_kind`，按 importance 降序，行数 = 训练用过的**全部**特征。

部署侧推理流程必须以此为权威 schema：
```python
manifest = pd.read_csv("models/<signal>/<window>/trade_filter_features.csv")
features = manifest["feature"].astype(str).tolist()
prob = TradeFilterModel.load(...).predict_proba(df_runtime, feature_columns=features)
```
列顺序 / 缺列 / 多列三种最常见的部署 bug 都会在 schema 校验阶段提前暴露。
