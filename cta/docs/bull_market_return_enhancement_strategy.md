# 牛市收益增强研发方案（CTA）

日期：2026-05-19  
范围：当前 `cta/` 架构内的策略、模型、组合执行与 OOT 评估

---

## 1. 目标

当前系统已经有候选信号、三类基础模型、`FinalDecisionModel`、`portfolio_logic` 运行时、分组池化和 OOT 真实成交评估。牛市收益偏弱时，优先不要简单放大杠杆，而要把“顺势进攻能力”补齐。

本方案目标：

1. 在牛市阶段提高多头收益捕获能力。
2. 保持 `weekly_max_drawdown_pct=0.03` 的风险底线。
3. 新增策略和模型必须接入现有 `candidate -> feature -> model -> oot -> portfolio_logic` 流程。
4. 所有阈值只用 train+valid 校准，OOT 只用于最终评估。

---

## 1.1 当前实现状态（2026-05-20 review）

下面是对当前代码的串联 review，区分“已落地”和“待接入”。

已落地：

1. 策略层：`trend_acceleration_breakout`、`bull_pullback_continuation`、`bull_volatility_contraction_breakout` 已接入 baseline 候选生成与策略工厂。
2. 特征层：`breakout_body_strength`、`trend_acceleration_score`、`pullback_quality`、`volatility_contraction_pctl` 已在 `prepare_master_feature_frame` 生成并进入训练特征。
3. 模型层：`BullRegimeStrengthModel`、`TrendPersistenceModel`、`PyramidEligibilityModel` 已训练并输出到 `*_predictions.csv`。
4. OOT Gate：`trade_filter` 已支持 `cluster|interval|side|bull_mode` 阈值与 attack 模式 long/short 偏移。
5. 报告层：`*_oot_trade_details.csv` 已包含 `bull_strength_proxy`、`bull_mode`、`trade_filter_gate_threshold` 等字段。

待接入（关键差距）：

1. `hold_extend_score` 目前只输出预测列，尚未直接驱动 `trailing/horizon` 出场决策。
2. `pyramid_add_score` 目前只输出预测列，尚未直接驱动 `pyramid_manager` 加仓倍率。
3. `feature_cluster_breadth_up / feature_cluster_momentum_rank` 等板块领涨特征尚未完整产出到训练主干。
4. `Side-Aware Final Decision` 目前是单模型 + `meta_side_code/meta_edge_side`，还未拆成 long/short 双模型版本。

执行建议：

1. 当前可先用已落地链路做牛市增强实验（见 `cta/run.md` 的“4.4 牛市增强闭环命令”）。
2. 下一阶段优先把 `hold_extend_score`、`pyramid_add_score` 接入 runtime 执行逻辑，再做收益归因对比。

---

## 2. 当前收益不足的可能根因

### 2.1 信号层

当前 baseline 更偏“突破是否发生”，对牛市中的二次启动、趋势加速、强回调后继续上涨覆盖不足。牛市里收益最大的交易往往不是第一次突破，而是趋势确认后的多次顺势介入。

### 2.2 模型层

`trade_filter` 和 `final_decision_score` 如果使用统一阈值，会把某些牛市长多机会过滤掉。尤其在 `cluster + interval + side` 分布差异较大时，全局阈值会误杀低 raw probability 但高右尾收益的机会。

### 2.3 出场层

固定 horizon 或固定止盈会截断牛市右尾。牛市里少数大盈利单贡献很高，过早退出会让胜率看起来不错，但组合收益不够厚。

### 2.4 组合层

静态仓位上限、静态每日开仓上限、过早触发的 ranker/drop 规则，会让组合在趋势最顺的时候“不够在场”。

---

## 3. 牛市定义

牛市不要只用单一均线判断，建议采用三层定义。

### 3.1 基础 Regime

使用当前 `Regime Classifier` 输出：

1. `regime_label == trend_up`
2. `regime_conf` 高于 train+valid 的 60% 分位
3. `day` 与 `60min` 的 HTF 方向同为 long 或允许 long

### 3.2 价格确认

增加价格行为确认：

1. `close > ma60`
2. `ma20_slope > 0`
3. `donchian_high_break_count_20 > 0`
4. `atr_pct` 不处于极端高位，避免牛市末端追高

### 3.3 板块确认

对 `cluster` 做横截面确认：

1. 同 cluster 内上涨品种占比大于 50%
2. cluster 近 20/60 bar 动量排名在前 40%
3. cluster 内多头候选通过率上升

落地点：

1. 通用特征：`cta/feature/`
2. 候选样本特征：`cta/model/feature/`
3. OOT 执行：`cta/portfolio_logic/interval_gate.py` 与 `cta/model/oot/pipeline_oot_evaluation.py`

---

## 4. 新增候选策略

新增策略不替代现有 `Donchian / ATR / tight range / breakout pullback`，而是作为新的 `signal_type` 并入候选池。

### 4.1 Trend Acceleration Breakout

目的：捕捉牛市中趋势加速段。

信号定义：

1. `close` 创 N bar 新高。
2. `ma20_slope` 与 `ma60_slope` 同时为正。
3. 最近 M bar 回撤小于 `k * ATR`。
4. 当日/当前 bar 的实体占比高于过去 20 bar 中位数。

开仓：

1. 多头：下一根 K 线开盘或突破价触发。
2. 空头：默认关闭或需要更高阈值。

适用：

1. `day / 60min / 30min`
2. `INDEX / METAL / CHEMICAL / BLACK` 中趋势品种优先

建议 signal_type：

`trend_acceleration_breakout`

### 4.2 Bull Pullback Continuation

目的：牛市中不追第一根突破，而是在回调确认后做继续上涨。

信号定义：

1. 最近 20/60 bar 为上升趋势。
2. 回调低点没有跌破 `ma20` 或前突破位。
3. 回调期间成交量/波动收缩。
4. 再次收盘站上短周期高点。

开仓：

1. 回调后重新突破的小止损入场。
2. 初始 stop 放在回调低点下方。

建议 signal_type：

`bull_pullback_continuation`

### 4.3 Volatility Contraction Expansion

目的：捕捉牛市中的窄幅蓄势后扩张。

信号定义：

1. `atr_pct` 或 high-low range 进入过去 60 bar 低分位。
2. 价格保持在 `ma20/ma60` 上方。
3. 向上突破收缩区间高点。

和已有 tight range 的区别：

1. 必须有上升趋势背景。
2. 只允许多头或多头阈值显著更低。
3. 更关注突破后的持仓延展，而不是短 horizon 快速兑现。

建议 signal_type：

`bull_volatility_contraction_breakout`

### 4.4 Sector Leadership Rotation

目的：牛市中优先交易正在变强的板块。

信号定义：

1. cluster 20/60 bar 动量进入全市场前 30%。
2. cluster 内至少 50% 品种处于 `trend_up`。
3. 个体品种触发任一多头候选信号。

作用：

1. 不直接生成价格入场，可以作为候选加分项。
2. 接入 `OpportunityRanker` 的 `cluster_strength_score`。

建议新增特征：

1. `feature_cluster_momentum_rank`
2. `feature_cluster_breadth_up`
3. `feature_cluster_candidate_pass_rate`

---

## 5. 新增模型

### 5.1 Bull Regime Strength Model

目标：判断当前是否值得进入“牛市进攻模式”。

标签：

1. 未来 `H` bar 多头持有收益是否超过成本和基准。
2. 未来 `H` bar 最大回撤是否低于阈值。
3. cluster 维度是否持续强于全市场中位数。

输入特征：

1. `generic_*` 趋势、动量、波动、价量特征。
2. cluster breadth 与 cluster momentum。
3. HTF 对齐状态。

输出：

1. `bull_strength_score`
2. `bull_mode = normal / attack / late_risk`

落地点：

1. 新模型文件可放 `cta/model/training/bull_regime_strength_model.py`
2. 预测列进入 `*_predictions.csv`
3. OOT 用它调整 long/short gate 与组合限额

### 5.2 Trend Persistence / Holding Model

目标：判断盈利仓位是否应该继续持有，而不是按固定 horizon 退出。

标签：

1. 当前持仓继续持有 `H` bar 后是否仍有正 edge。
2. 继续持有期间是否触发不可接受回撤。

输入：

1. 当前浮盈 ATR 倍数。
2. trailing stop 距离。
3. regime 持续性。
4. cluster 强度是否仍在。

输出：

1. `hold_extend_score`
2. `recommended_horizon_extension_bars`

落地点：

1. 接入 `cta/portfolio_logic/trailing_exit.py`
2. 与 `HorizonExtendConfig` 配合，决定是否延长持仓

### 5.3 Pyramid Eligibility Model

目标：判断盈利仓位是否值得加仓。

标签：

1. 加仓后到退出的增量收益是否为正。
2. 加仓后组合回撤是否不显著恶化。

输入：

1. 当前仓位层数。
2. 首仓浮盈。
3. `trade_filter_prob_pctl`
4. `final_decision_score`
5. `bull_strength_score`
6. cluster 当前拥挤度与组合风险预算。

输出：

1. `pyramid_add_score`
2. `pyramid_size_mult`

落地点：

1. 接入 `cta/portfolio_logic/pyramid_manager.py`
2. 替代单纯的 `min_profit_atr_to_add` 规则

### 5.4 Side-Aware Final Decision Model

目标：让 long 与 short 的最终决策边界分开。

实现方式：

1. 轻量方案：同一个模型增加 `side`、`side_cluster`、`side_interval` 交互特征。
2. 稳健方案：按 `signal_side` 分别训练 `final_decision_long` 与 `final_decision_short`。
3. 组合方案：先分 side 训练，再由 cluster/interval 分位数阈值校准。

建议优先做轻量方案，样本足够后再拆分模型。

落地点：

1. `cta/model/training/final_decision_model.py`
2. `cta/model/dataset/pipeline_meta_features.py`
3. `cta/model/oot/oot_trade_filter_gate.py`

---

## 6. 组合执行增强

### 6.1 牛市进攻模式

当 `bull_strength_score` 进入 train+valid 70% 分位，并且 HTF 允许 long：

1. long 的 `trade_filter_percentile_threshold` 从 70 降到 60-65。
2. short 的阈值提高到 75-85。
3. `max_total_positions` 从 10 提到 12-14。
4. `max_total_notional_pct` 从 1.5 提到 1.8。
5. `max_daily_new_notional_pct` 从 1.0 提到 1.2-1.3。

风险底线：

1. `weekly_max_drawdown_pct` 仍为 0.03。
2. 月度熔断不放松。
3. 触发 reduced/hard throttle 后立即退出进攻模式。

### 6.2 右尾保护

盈利仓位进入趋势延展后：

1. 用 ATR trailing stop 代替固定止盈。
2. 盈利超过 `+1R` 后 stop 至少移动到 breakeven 附近。
3. 盈利超过 `+2R` 后允许第二层加仓。
4. 每次加仓后重新计算组合风险预算。

已有代码可复用：

1. `cta/portfolio_logic/trailing_exit.py`
2. `cta/portfolio_logic/pyramid_manager.py`
3. `cta/portfolio_logic/risk_throttle.py`
4. `cta/portfolio_logic/portfolio_state.py`

### 6.3 交易过滤从“全局阈值”升级到“场景阈值”

阈值 key 建议从：

`cluster|interval`

升级为：

`cluster|interval|side|bull_mode`

示例：

1. `chemical|day|long|attack = 62`
2. `chemical|day|short|attack = 82`
3. `index|60min|long|attack = 60`
4. `index|day|short|attack = 85`

注意：

所有分位数阈值必须来自 train+valid，不允许用 OOT 自身分布。

---

## 7. 特征与标签设计

### 7.1 新增候选特征

1. `feature_trend_acceleration_score`
2. `feature_pullback_quality`
3. `feature_volatility_contraction_pctl`
4. `feature_breakout_body_strength`
5. `feature_cluster_breadth_up`
6. `feature_cluster_momentum_rank`
7. `feature_htf_long_alignment_score`
8. `feature_distance_to_trailing_stop_atr`
9. `feature_unrealized_profit_atr`
10. `feature_pyramid_layer_count`

### 7.2 新增标签

1. `label_bull_attack`: 未来 H bar 是否适合提高 long 风险预算。
2. `label_hold_extend`: 当前位置继续持有是否优于按原 horizon 退出。
3. `label_pyramid_add`: 在当前盈利状态下加仓是否提升增量收益。
4. `label_long_edge`: 多头方向独立 edge。
5. `label_short_edge`: 空头方向独立 edge。

### 7.3 泄露控制

1. 所有 label 只使用 entry 之后的未来数据。
2. 所有 feature 必须在 entry 当时可见。
3. cluster breadth 只能用当时已完成 bar。
4. 阈值校准只用 train+valid。
5. OOT 报告必须输出 suspect feature 与 null/IC 诊断。

---

## 8. 实验矩阵

### 8.1 第一轮：不新增模型，只调执行

目的：确认收益弱是不是被执行层压住。

组别：

1. `baseline`: 当前默认参数。
2. `long_bias_gate`: long/short 阈值分开。
3. `long_bias_gate_plus_pyramid`: 加入进攻模式下 pyramid。
4. `long_bias_gate_plus_trailing`: 加入更宽 trailing。

验收：

1. 牛市子样本年化收益提升。
2. 最大回撤不超过 baseline 的 1.15 倍。
3. 大盈利单贡献占比上升。

### 8.2 第二轮：新增候选策略

新增：

1. `trend_acceleration_breakout`
2. `bull_pullback_continuation`
3. `bull_volatility_contraction_breakout`

验收：

1. 新 signal_type 的 OOT decile 收益单调性。
2. 新 signal_type 的 top decile 净收益为正。
3. 新 signal_type 没有明显集中在少数 symbol。

### 8.3 第三轮：新增模型

新增：

1. `Bull Regime Strength Model`
2. `Trend Persistence / Holding Model`
3. `Pyramid Eligibility Model`
4. `Side-Aware Final Decision Model`

验收：

1. train/valid AUC gap 仍控制在 3% 以内。
2. OOT 不参与参数与阈值选择。
3. 每个模型都有 top10 feature importance 与含义。
4. 按 cluster/interval/side 输出收益拆解。

---

## 9. 推荐实施顺序

### P0：快速验证收益瓶颈

1. 增加 `cluster|interval|side|bull_mode` 阈值设计。
2. 在 OOT trade details 中输出 `bull_mode`、`bull_strength_proxy`、`side_gate_threshold`。
3. 做 `long_bias_gate` 对照实验。
4. 输出牛市子样本 KPI。

### P1：新增策略

1. 新增 `trend_acceleration_breakout`。
2. 新增 `bull_pullback_continuation`。
3. 新增 `bull_volatility_contraction_breakout`。
4. 每个新增 signal_type 先用规则 baseline 做 OOT，不急着入复杂模型。

### P2：新增模型

1. 训练 `Bull Regime Strength Model`。
2. 训练 `Side-Aware Final Decision Model`。
3. 再训练 `Trend Persistence / Holding Model` 与 `Pyramid Eligibility Model`。

### P3：上线前稳健性

1. 按 cluster、interval、年份、signal_type 做收益拆解。
2. 对最强收益来源做特征泄露审计。
3. 对最差 cluster 做禁用或降权 manifest。
4. 输出 live 可读的配置快照。

---

## 10. 最小可运行实验命令

建议优先按 `cta/run.md` 的“4.4 牛市增强闭环命令”执行。下面给一个最小版本：

```bash
python3 -m cta.model.model_pipeline \
  --group-pool \
  --group-by cluster \
  --top-n-symbols 77 \
  --interval day 60min \
  --start 2010-01-01 \
  --end 2025-12-31 \
  --train-end 2020-12-31 \
  --valid-end 2022-12-31 \
  --window-mode rolling \
  --rolling-train-years 6 \
  --rolling-valid-years 2 \
  --rolling-test-years 1 \
  --max-auc-gap 0.03 \
  --generic-mode auto \
  --use-portfolio-logic-runtime \
  --seed 20260519
```

若要显式控制 `cluster|interval|side|bull_mode` 阈值，用 Python 入口传 `oot_eval_config`
（详见 `cta/run.md` 4.4.3 示例）。

实验报告重点看：

1. `*_oot_trade_details.csv`
2. `*_oot_monthly_returns.csv`
3. `*_last_oot_decile_returns.csv`
4. `*_top10_feature_importance.csv`
5. `09_diagnostics/auc_per_window.csv`

---

## 11. 预期文件落点

新增策略：

1. `cta/strategy/baseline_strategies.py`
2. `cta/strategy/baseline_setup_detection.py`
3. `cta/strategy/baseline_candidate_gen.py`

新增模型：

1. `cta/model/training/bull_regime_strength_model.py`
2. `cta/model/training/trend_persistence_model.py`
3. `cta/model/training/pyramid_eligibility_model.py`
4. `cta/model/training/final_decision_model.py`

新增组合逻辑：

1. `cta/portfolio_logic/config.py`
2. `cta/portfolio_logic/opportunity_ranker.py`
3. `cta/portfolio_logic/pyramid_manager.py`
4. `cta/portfolio_logic/trailing_exit.py`

新增报告：

1. `cta/model/reporting/oot_report_writer.py`
2. `cta/model/reporting/pipeline_diagnostics.py`
3. `cta/backtest/.../bull_market_kpi.csv`

---

## 12. 风险与防线

### 12.1 牛市误判

风险：震荡市被识别为牛市，导致过早提高仓位。

防线：

1. 必须同时满足 regime、价格、cluster 三层确认。
2. `weekly_max_drawdown_pct` 不放松。
3. throttle 进入 reduced/hard 后立即关闭进攻模式。

### 12.2 加仓放大回撤

风险：趋势末端加仓，利润回吐。

防线：

1. 加仓必须已有浮盈。
2. 加仓后 stop 上移。
3. 加仓使用递减 size decay。
4. 单 symbol 与 cluster notional cap 不放松或只小幅放松。

### 12.3 模型过拟合

风险：新增模型看起来提升 OOT，但实际来自阈值或特征泄露。

防线：

1. train/valid gap 仍控制在 3% 以内。
2. OOT 不参与阈值选择。
3. 每个新增模型都输出 `feature_importance + feature_meaning`。
4. 对收益最大的前 20 笔逐笔复核成交路径。

---

## 13. 结论

牛市收益增强的主线是：

1. 新增更适合牛市的候选策略，覆盖趋势加速、回调继续、低波扩张。
2. 新增模型判断什么时候进入进攻模式、什么时候延长持仓、什么时候加仓。
3. 组合执行从静态限制升级为“牛市进攻、回撤收缩”的动态状态机。
4. 保持 OOT 严格性，不用 OOT 自身分布做阈值，不放松核心回撤约束。

这条路线不需要推翻现有架构，只是在现有 `strategy -> model -> portfolio_logic -> oot_report` 链路上补齐牛市右尾收益捕获能力。
