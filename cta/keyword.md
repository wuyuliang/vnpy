# CTA 专业词汇表（`keyword.md`）

按主题归类的术语手册，覆盖期货 CTA 量化研究、回测、模型、风控、实盘工程的常用名词。
每个条目包含**英文 / 中文 / 简短定义 / 在本项目里的字段或文件**。配合
[`cta/feature/FEATURES.md`](feature/FEATURES.md) /
[`cta/model/baseline_model.md`](model/baseline_model.md) /
[`cta/model/model.md`](model/model.md) 阅读。

---

## 1. K 线与行情

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **OHLC** | 开高低收 | 一根 K 线的 open / high / low / close 四个价格 | 所有 frame 的核心 4 列 |
| **Bar** | K 线 / 周期数据 | 在固定周期（1m/5m/...）内对 tick 聚合得到 OHLCV | `vnpy.trader.object.BarData` |
| **Tick** | 报价 | 交易所推送的最小报价单元（价格 + 量 + 持仓） | `vnpy.trader.object.TickData` |
| **Volume** | 成交量 | 该周期成交手数 | `volume` 列 |
| **Open Interest** | 持仓量 | 该周期末未平仓合约总数 | `open_interest` 列 |
| **Turnover** | 成交额 | 该周期成交金额 | `turnover` 列 |
| **Settlement** | 结算价 | 交易所每日的统一结算基准价 | `settlement`（akshare 来的列） |
| **Continuous Contract** | 连续合约 / 主力连续 | 把多个到期月份的合约按规则拼接成一条不间断时序 | `RB0` / `888` 后缀；`cta/data/origin/day/{SYMBOL}.csv` |
| **Rollover** | 换月 / 合约切换 | 主力合约移仓到下一个活跃月份 | `cta/skills/data_backtest/rollover_rules.py` / `continuous_contract.py` |
| **Limit Up / Down** | 涨 / 跌停 | 当日价格触及涨跌停板，单方向无法成交 | `EngineConfig.limit_move_pct`（事件驱动回测过滤一字板）|

---

## 2. 波动率与趋势指标

| 术语 | 全称 / 中文 | 定义 | 项目里 |
|------|------|------|------|
| **TR** | True Range | `max(H−L, |H−prev_C|, |L−prev_C|)` | `cta/skills/.../_common.py` |
| **ATR** | Average True Range | TR 的 N 周期平均（默认 Wilder EMA, 14） | `atr14` / `don_atr20` / `atr_value` 列 |
| **SMA** | Simple Moving Average | N 期算术平均 | `cta/skills/.../_common.py:sma` |
| **EMA** | Exponential MA | 指数加权平均 | `pandas.ewm(...).mean()` |
| **Donchian Channel** | 唐奇安通道 | 过去 N 期的最高/最低价上下沿 | `don_upper_entry`(55) / `don_lower_exit`(20) |
| **ATR Channel** | ATR 通道 | `MA ± k·ATR`（默认 k=2.5） | `atr_upper` / `atr_lower` |
| **Bollinger Band** | 布林带 | `MA ± k·σ` | `cta/feature/mean_reversion.py` 的 range setup 特征 |
| **MACD** | Moving Average Convergence Divergence | 快慢 EMA 之差及其信号线 | `cta/feature/momentum.py` |
| **RSI** | Relative Strength Index | 0–100 区间动量指标 | 同上 |

---

## 3. 价格行为（Al Brooks 风格）

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **Tight Range** | 窄幅整理 | 过去 N 根 K 线高低区间相对 ATR 较窄、且持续多根 | `tr_valid` / `tr_upper` / `tr_lower` / `tr_count` |
| **Breakout** | 突破 | 价格冲破之前形成的关键位（如 Donchian / Tight-range 上下沿） | `breakout_score` / `breakout_pass` |
| **Breakout Quality** | 突破质量 | 综合实体 / 位置 / 收盘相对 level 的 0–1 打分 | `cta/skills/filtering_scoring/breakout_quality.py` |
| **Pullback** | 回撤 / 回踩 | 突破后价格短暂回到 level 附近 | `bp_valid` / `bp_pullback_low` |
| **Confirmed Pullback** | 确认回踩 | 回踩后 close 重新穿越 level | `bp_confirmed` |
| **Bull / Bear Bar** | 阳线 / 阴线 | close > open / close < open | `cta/feature/pattern.py` |
| **Signal Bar** | 信号 K 线 | 触发交易决策的那根 K 线 | 候选事件以该 bar 的 datetime 索引 |
| **Setup** | 形态 | 触发条件就绪但尚未入场的状态 | `TightRangeSetup` / `PullbackSetup` |
| **Trigger** | 触发价 | 形态成立后用 stop 单等待的具体执行价 | `resolve_breakout_trigger(...)["trigger"]` |
| **Higher High / Lower Low** | 高点抬升 / 低点下降 | 趋势结构的微观判定 | `cta/feature/price_action_advanced.py` |
| **Trading Range** | 交易区间 / 震荡区 | 价格在固定上下沿之间反复 | 与 trend 状态对应的 regime 标签之一 |
| **Channel** | 通道 | 倾斜的 trading range，斜率 ≠ 0 | `cta/feature/price_action_advanced.py` |
| **Measured Move** | 测量移动 | 用第一段行情的高度预测第二段目标 | `cta/feature/price_action_advanced.py` |

---

## 4. 订单与成交

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **Market Order** | 市价单 | 立即按对手价成交 | `order_type="market"` |
| **Limit Order** | 限价单 | 指定价或更优价成交 | `order_type="limit"` |
| **Stop Order** | 止损 / 触发单 | 价格触及触发价后转市价 / 限价 | `order_type="stop"` |
| **Tick Size** | 最小变动价位 | 一个 tick 的金额 | `ContractSpec.tick_size` / `cta_engine.get_pricetick` |
| **Multiplier** | 合约乘数 | 1 手对应的标的数量（rb888=10t） | `ContractSpec.multiplier` / `cta_engine.get_size` |
| **Lots** | 手数 | 下单数量单位 | `lots` / `volume` |
| **Slippage** | 滑点 | 期望价与成交价之差 | `EngineConfig.slippage_ticks` / `transaction_cost.estimate_cost` |
| **Commission** | 手续费 | 交易所 + 期货公司收的费用 | `ContractSpec.commission_rate` |
| **Fill** | 成交 | 订单被对手方匹配 | `simulate_fill(...)` |
| **Stop Fill** | 止损成交价 | 止损被触发后的实际成交价（worst / exact） | `EngineConfig.stop_fill` |
| **Margin** | 保证金 | 持仓占用的资金 | 实盘由 vnpy 账户系统管理 |
| **Direction** | 方向 | LONG / SHORT | `vnpy.trader.constant.Direction` |
| **Offset** | 开平 | OPEN / CLOSE / CLOSETODAY / CLOSEYESTERDAY | `vnpy.trader.constant.Offset` |
| **Long / Short / Flat** | 多 / 空 / 平 | v1 策略订单 dict 中的 side 三态 | `order["side"]` |

---

## 5. 持仓与资金

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **Position** | 持仓 | 当前持有的方向 + 数量；正为多、负为空 | `strategy.pos` |
| **Initial Stop** | 初始止损 | 入场后立刻设置的固定止损价 | `_pending_initial_stop` / `initial_stop_atr_mult` |
| **Trailing Stop** | 跟踪止损 | 持仓极值方向移动后自动上提（多）/ 下降（空）的止损 | `BreakoutPullbackBaselineStrategy` 内部状态 |
| **Time Stop** | 时间止损 | 持仓 bar 数到达上限后强平 | `max_holding_bars` |
| **Risk Per Trade** | 单笔风险 | 单笔交易愿意承担的资金比例 | `risk_per_trade_pct`（默认 0.005）|
| **Position Sizing** | 仓位管理 | 根据 ATR 与风险预算反推手数 | `SkillTightRangeBreakoutStrategy.compute_lots` |
| **Equity Curve** | 净值曲线 | 累计权益随时间变化 | `run_backtest` 输出 `equity_curve` |
| **PnL** | 损益（Profit and Loss） | 已实现 + 浮动盈亏；本项目通常指已实现 | `trade_log["net_pnl"]` / `DailyPnlTracker.get_pnl()` |

---

## 6. 绩效指标

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **Sharpe Ratio** | 夏普比 | `mean(r) / std(r) × √N`，按周期年化 | `extended_metrics["sharpe"]` |
| **Sortino Ratio** | 索提诺比 | 同 Sharpe 但分母仅用下行波动 | `extended_metrics["sortino"]` |
| **Calmar Ratio** | 卡尔玛比 | `年化收益 / 最大回撤` | `extended_metrics["calmar"]` |
| **MDD** | Max Drawdown / 最大回撤 | 净值曲线从历史峰到谷的最大相对跌幅 | `extended_metrics["mdd"]` |
| **MDD Duration** | 水下天数 | 净值最长低于历史峰的连续 bar 数 | `mdd_duration` |
| **MDD Recovery** | 回撤恢复期 | 从最大回撤底回到峰值所用 bar 数 | `mdd_recovery` |
| **Win Rate** | 胜率 | 盈利交易笔数 / 总笔数 | `winrate` |
| **Profit Factor (PF)** | 盈亏比 | `总盈利 / 总亏损绝对值` | `pf` |
| **Total PnL** | 总损益 | 全期累计 net PnL | `total_pnl` |
| **Annualized Return** | 年化收益 | 用 CAGR 公式年化后的收益率 | `annualized` |
| **Win Streak / Lose Streak** | 最长连胜 / 连亏 | 连续盈利 / 亏损交易的最大笔数 | `win_streak` / `lose_streak` |
| **Turnover (per bar)** | 换手率 | 每根 bar 平均换仓手数（双边） | `turnover_per_bar` |
| **Monthly PnL** | 月度损益 | 按自然月聚合的 PnL，常画热力图 | `monthly_pnl` dict + `monthly_heatmap_fig` |
| **Capacity** | 资金容量 | 在流动性约束下不同资金规模的 PnL 衰减 | `cta/report/render/capacity.py` |
| **Monte Carlo** | 蒙特卡洛 | 收益序列 block bootstrap 重抽样 → 给出最终权益 / MDD 分位数 | `cta/report/render/monte_carlo.py` |

---

## 7. 风险偏移：MFE / MAE

| 术语 | 全称 | 定义 | 项目里 |
|------|------|------|------|
| **MFE** | Maximum **Favorable** Excursion | 持仓期内对该方向**最有利**的最大偏移（多单：max(high) − entry；空单：entry − min(low)） | `future_mfe_atr` 标签列；`cta/model/training/mfe_mae_model.py` |
| **MAE** | Maximum **Adverse** Excursion | 持仓期内对该方向**最不利**的最大偏移（多单：entry − min(low)；空单：max(high) − entry） | `future_mae_atr` 标签列 |
| **MFE/MAE Ratio** | 利不利比 | 衡量入场点质量；好的 setup 应有 MFE > MAE | 离线分析、`MfeMaeModel` 回归目标 |

> 在本项目里 MFE/MAE 都被 ATR14 归一化（`mfe_atr = mfe / atr14`），方便跨品种 / 跨
> 周期统一阈值。MfeMaeModel 输出预测后可用于动态止损：
> `stop = entry − k × predicted_mae × atr14`。

---

## 8. 信息系数与命中率

| 术语 | 全称 | 定义 | 项目里 |
|------|------|------|------|
| **IC** | Information Coefficient | 因子 / 模型预测与未来收益的 Spearman / Pearson 相关 | `*_metrics.csv` 中的 `ic` 列 |
| **IR** | Information Ratio | `mean(IC) / std(IC)`，因子稳定性 | `ir` |
| **Hit Rate** | 命中率 | 预测方向与未来真实方向一致的比例 | `oos_hit_rate` |
| **Decile Returns** | 十分位收益 | 按预测概率十分位分组的实际收益 | `*_last_oot_decile_returns.csv` |
| **Top-K Selection** | 头部组合 | 按预测概率取前 K 个候选下单 | `cta/strategy/equity_demo_strategy.py`（vnpy/alpha 风格） |

---

## 9. 回测与样本切分

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **In-Sample (IS)** | 样本内 | 用于训练 / 优化参数的时间区间 | `start ~ train_end` |
| **Out-of-Sample (OOS)** | 样本外 | 训练后回测验证用 | `train_end ~ valid_end` |
| **Out-of-Time (OOT)** | 时间外 | 严格在所有训练 / 验证之后的真正测试段 | `valid_end ~ end` |
| **Walk-Forward** | 滚动窗口 | 训练截止 + 验证 + 测试三段沿时间向前滚动多次 | `_build_walk_forward_windows`，`window_mode ∈ {expanding, sliding}` |
| **Look-ahead Bias / Lookahead Leak** | 向前看偏差 | 计算特征时用了未来才能见到的信息 | `breakout_quality.score_breakout(_allow_future=False)` 守门 |
| **Synthetic Periods** | 合成时段 | 训练样本不足时用合成数据填充 | `--synthetic-periods 400` |
| **By-signal-type** | 按信号类型分组 | 同一 walk-forward 内 Donchian / ATR / TightRange / BreakoutPullback 各训各的 | `--by-signal-type` |
| **POOL** | 多 symbol 池化 | 把多个品种的样本拼成一个共享训练集，训出跨品种模型 | `--pool` flag, 输出 `..._POOL_..._model_pipeline/` |

---

## 10. 信号与候选事件

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **Signal Type** | 信号类型 | baseline setup 的标识 | `signal_type ∈ {donchian_breakout, atr_breakout, tight_range_breakout, breakout_pullback, ...}` |
| **Candidate Event** | 候选事件 | 满足信号触发条件的一行样本（不一定真成交） | `cta/data/model_feature/candidate_*.parquet` |
| **is_executed** | 是否成交 | 候选在回测引擎中是否真的开仓（受撮合规则影响） | candidate_df 列 |
| **atr_warmed** | ATR 已 warm-up | atr14 已稳定（前 14 根之外） | candidate_df 列 |
| **Setup Bar** | 信号 bar | 形态成立的那根 K 线（决策时刻） | candidate_df 中的 datetime |
| **Entry Bar** | 入场 bar | 实际成交的 K 线（通常是 setup_bar 的下一根） | `entry_i` |
| **Exit Bar** | 出场 bar | 平仓的 K 线 | `exit_i` |

---

## 11. 模型类型

| 术语 | 全称 / 中文 | 定义 | 项目里 |
|------|------|------|------|
| **Trade Filter** | 交易过滤模型 | 二分类，给候选事件打"该不该开仓"的概率 | `cta/model/training/trade_filter_model.py` / `make_trade_filter` |
| **Regime Classifier** | 市场状态分类 | 多分类，识别趋势 / 震荡 / 破坏等 | `cta/model/training/regime_classifier_model.py` |
| **MFE/MAE Model** | 风险预测模型 | 回归未来 N bar 的 MFE / MAE | `cta/model/training/mfe_mae_model.py` |
| **Generic Features** | 通用特征 | 按 cta/feature/* 算出的 ~400 个跨品种特征 | `cta/data/feature/{interval}/{prefix}/{date}.parquet` |
| **Generic Mode = auto** | 自动取全部 | 训练时把 parquet 上所有数值列纳入特征 | `--generic-mode auto`（默认）|
| **Generic Mode = whitelist** | 白名单 18 列 | 仅取 `DEFAULT_GENERIC_COLUMNS` | `--generic-mode whitelist` |

---

## 12. 实盘工程（vnpy 接入）

| 术语 | 全称 / 中文 | 定义 | 项目里 |
|------|------|------|------|
| **vt_symbol** | vnpy 合约代码 | `<symbol>.<exchange>`，例 `rb888.SHFE` | 所有 strategy / order / pos 的合约标识 |
| **MainEngine** | 主引擎 | vnpy 顶层调度，挂载所有 gateway / app | `cta.sim.sim_runner.run_sim` 返回 |
| **EventEngine** | 事件引擎 | vnpy 内部事件分发循环 | `vnpy.event.EventEngine` |
| **Gateway** | 网关 | 对接 broker / 交易所的接入层 | `vnpy_ctp.CtpGateway` |
| **CTP** | China Trading Protocol | 上期所等期货市场 broker 标准接入协议 | `vnpy_ctp` 包 |
| **SimNow** | CTP 仿真环境 | 上期所提供的免费 7×24 仿真账号 | `cta.sim.sim_runner.SimnowSetting` |
| **CtaTemplate** | CTA 策略基类 | vnpy_ctastrategy 提供的策略模板 | `cta.strategy.cta_adapter.LegacyCtaAdapter`（子类） |
| **CtaEngine** | CTA 策略引擎 | vnpy_ctastrategy 内的策略管理器 | 通过 `main_engine.get_engine("CtaStrategy")` 取 |
| **on_bar / on_tick** | bar / tick 回调 | 行情新数据触发的策略入口 | `LegacyCtaAdapter.on_bar` |
| **on_trade / on_order** | 成交 / 订单回调 | 成交回报与订单状态变化 | adapter 重载 `on_trade` 转发到 trade_recorder / pnl_tracker |
| **buy / sell / short / cover** | vnpy 4 种下单 | 开多 / 平多 / 开空 / 平空 | adapter `_dispatch_order` 翻译 v1 订单 dict |
| **load_bar(days, interval)** | 历史预热 | CtaTemplate 内置接口，加载 N 天历史 K 线 | `SimRunConfig.warmup_days` |

---

## 13. 风控与守护

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **RiskGuard** | 风控总开关 | 多条规则短路评估，任一拒绝即整体拒绝 | `cta/live/risk.py` |
| **MaxOrderSize** | 单笔最大下单 | 按 vt_symbol 限制单笔手数 | risk rule |
| **MaxPositionLimit** | 最大持仓 | 开仓后总持仓不超过限制；平仓不阻止 | risk rule |
| **DailyLossLimit** | 日内亏损上限 | 日内已实现 PnL 跌破阈值禁止开仓；平仓允许 | risk rule + DailyPnlTracker |
| **OrderRateLimit** | 委托速率限制 | 每秒最大委托数 | risk rule |
| **Kill Switch** | 紧急停单 | 内存激活 / 文件信号触发，全局禁单 | `cta/live/kill_switch.py` |
| **Pre-trade Hook** | 下单前钩子 | 订单分发前的过滤层 | `LegacyCtaAdapter.order_filter` |
| **Supervisor** | 连接守护 | 周期检查 gateway 连接、断了重连 | `cta/live/supervisor.py` |
| **Parity Check** | 对账校验 | 实盘信号 vs 同 K 线下回测信号的失配率 | `cta/sim/parity_check.py` / `mismatch_rate` |
| **Trade Recorder** | 成交记录器 | 实盘 trade 流水落 parquet | `cta/live/trade_recorder.py` |
| **PnL Tracker** | PnL 累加器 | 日内已实现 PnL FIFO 配对累计、跨日 auto reset | `cta/live/pnl_tracker.py` |

---

## 14. 数据流相关

| 术语 | 中文 | 定义 | 项目里 |
|------|------|------|------|
| **alpha_prefix** | 字母前缀 | 品种代码字母部分大写（CU0 → CU），用作目录名 | `cta/data_code/futures_downloader.py:alpha_prefix` |
| **Origin** | 原始行情 | 下载下来的 OHLCV 未加工数据 | `cta/data/origin/` |
| **Feature** | 通用特征 | 由 `cta.feature.run_all_features` 离线生成 | `cta/data/feature/` |
| **Model Feature** | 候选样本 | 候选事件 + 通用特征拼接后的训练样本 | `cta/data/model_feature/` |
| **Tushare** | 数据源 | 提供分钟级期货数据，需要 token | `TUSHARE_TOKEN` 环境变量 |
| **akshare** | 数据源 | 免费的日线数据源 | `cta/data_code/futures_downloader.py:download_day` |
| **Rate Limiter** | 限速器 | 令牌桶，控制 tushare 请求速率 | 同上 |

---

## 15. 缩写速查

| 缩写 | 全称 | 出现位置 |
|------|------|---------|
| ATR | Average True Range | §2 |
| MFE | Maximum Favorable Excursion | §7 |
| MAE | Maximum Adverse Excursion | §7 |
| MDD | Maximum Drawdown | §6 |
| PnL | Profit and Loss | §5 / §6 |
| PF | Profit Factor | §6 |
| IC | Information Coefficient | §8 |
| IR | Information Ratio | §8 |
| OHLC | Open / High / Low / Close | §1 |
| OI | Open Interest | §1 |
| MA / SMA / EMA | Moving Average / Simple / Exponential | §2 |
| IS / OOS / OOT | In/Out-of-Sample / Out-of-Time | §9 |
| OHLCV | OHLC + Volume | §1 |
| CTP | China Trading Protocol | §12 |
| SimNow | CTP 仿真环境 | §12 |
| POOL | 多 symbol 池化训练 | §9 / §11 |

---

## 16. Model Pipeline 新参数速查

以下参数已在 `run.md` 与 `pipeline_cli.py` 对齐，便于快速检索：

| 参数 | 含义 |
|------|------|
| `--max-auc-gap` | 训练集与验证集 AUC 最大允许差，用于抑制过拟合选参。 |
| `--max-valid-test-gap` | valid 与 test AUC 差值告警阈值，超阈值输出告警文件。 |
| `--no-by-signal-type` | 关闭按 `signal_type` 分模型，改为混合训练。 |
| `--only-clusters` | `--group-pool` 模式下仅运行指定 cluster（如 `index`/`bond`）。 |
| `--output-root` | 指定报告与模型输出根目录。 |
| `--rolling-train-years` | `rolling` 模式 train 窗口长度（年）。 |
| `--rolling-valid-years` | `rolling` 模式 valid 窗口长度（年）。 |
| `--rolling-test-years` | `rolling` 模式 test 窗口长度（年）。 |
| `--rolling-step-years` | `rolling` 模式窗口前滚步长（年）。 |
| `--top-feature-alert-pct` | 单特征重要度占比告警阈值，超阈值进入可疑特征报告。 |
| `--strict-fail-fast` | 批量 group/interval 任务任一子任务失败时立即退出（严格模式）。 |
| `--no-strict-fail-fast` | 关闭严格模式，允许批量任务在局部失败后继续执行其它子任务。 |
| `--enable-cross-sectional-rotation` | 启用截面动量轮动候选并入（默认关闭）。 |
| `--cross-sectional-enabled-cells` | 指定允许并入的 `cluster|interval` 单元（可多值）。 |
| `--cross-sectional-long-only` | 截面轮动候选仅保留多头方向（默认 long+short）。 |
| `--enable-risk-system` | 启用 risk orchestrator（quantile threshold + sizing 链路）。 |
| `--risk-quantile-field` | risk quantile 阈值档位（`p50~p95`，默认 `p70`）。 |
| `--risk-manifest-path` | 指定 quantile manifest 路径（默认可空，按配置 fallback）。 |
| `--risk-enable-bucket-scaling` | 打开 bucket PnL 缩仓（默认关闭，需 state 预热）。 |
| `--risk-disable-linear-dd-scaler` | 关闭 linear DD 缩仓（默认开启）。 |
| `--risk-disable-dynamic-bump` | 关闭 DD 驱动的阈值抬升（默认开启）。 |
| `--risk-disable-quantile-threshold` | 关闭 quantile threshold adjuster（默认开启）。 |
| `--enable-impact-cost` | OOT / train 评估启用 ADV 参与率冲击成本，适合容量压力测试。 |
| `--impact-cost-k` | 冲击成本系数 `k`，公式为 `k * sqrt(order_lots / adv_lots)`。 |
| `--disable-liquidity-floor` | 关闭 OOT 流动性下限 guard；默认开启且缺指标 fail-open。 |
| `--note` | eval-only 报告备注，写入 `executive_summary.md` 与 `meta/cfg_fingerprint.json`。 |

---

## 17. 参考

- 特征详细定义：[`cta/feature/FEATURES.md`](feature/FEATURES.md)
- baseline 策略原理：[`cta/model/baseline_model.md`](model/baseline_model.md)
- 模型管道：[`cta/model/model.md`](model/model.md)
- 端到端运行：[`cta/run.md`](run.md) / [`cta/run.sh`](run.sh)
- vnpy 文档：<https://www.vnpy.com>
- Al Brooks 价格行为学：*Reading Price Charts Bar by Bar*（2009）/ *Trading Price Action* 三部曲
