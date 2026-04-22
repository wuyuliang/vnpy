# CTA Skills 技能树 / CTA Skills Tree

> 中国商品 CTA 策略的**技能/方法论文档库**。本目录下的每一个 `.md` 都可直接喂给 Claude / Codex 生成对应的代码骨架。

## 目录定位（和仓库其它文档的关系）

| 层级 | 位置 | 职责 |
|------|------|------|
| 顶层：技能 / 方法论 | `cta/cta_skills/`（本目录） | 怎么做研究、怎么识别行情、怎么设计策略族 |
| 中层：特征工程 | `cta/feature/`（`FEATURES.md`） | pa_* 特征、context 特征、特征落盘管线 |
| 底层：策略实现 | `cta/strategy/`（如 `brooks/`） | 具体某策略的信号 / 风控 / 回测 / 在线 |
| 数据层 | `cta/data/`、`cta/data_code/` | 原始行情、分钟/日线数据下载 |
| 元数据层 | `cta/config/futures_meta.py` | 合约乘数、手续费、保证金 |

**本目录只写 markdown，不写任何 py 代码。** 代码由具体策略目录（如 `cta/strategy/{name}/`）承接。

## 完整技能树

```text
CTA_Skills/
├── 00_overview_methodology           # CTA 总纲与研究方法论
│   ├── 01_objectives                 # 目标设定
│   ├── 02_research_boundary          # 研究边界
│   ├── 03_backtest_principles        # 回测原则
│   ├── 04_live_trading_principles    # 实盘原则
│   └── 05_iteration_workflow         # 迭代流程
│
├── 01_market_regime                  # 市场结构识别
│   ├── 01_trend_detection            # 趋势识别
│   ├── 02_range_detection            # 震荡识别
│   ├── 03_breakout_threshold_detection # 突破临界识别
│   ├── 04_volatility_regime          # 波动率状态识别
│   └── 05_multi_timeframe_alignment  # 多周期状态对齐
│
├── 02_price_action                   # 价格行为与形态理解（Al Brooks）
│   ├── 01_tight_range_breakout
│   ├── 02_bull_flag_bear_flag
│   ├── 03_breakout_pullback_continuation
│   ├── 04_high1_high2_low1_low2
│   ├── 05_failed_breakout
│   └── 06_micro_channel_trend_channel
│
├── 03_trend_strategies               # 趋势类策略
│   ├── 01_donchian_breakout
│   ├── 02_atr_breakout
│   ├── 03_ma_trend_following
│   ├── 04_cross_sectional_momentum
│   └── 05_trend_hold_and_trailing
│
├── 04_range_strategies               # 震荡类策略
│   ├── 01_range_boundary_reversal
│   ├── 02_mean_reversion
│   ├── 03_false_breakout_reversal
│   └── 04_noise_filtering
│
├── 05_regime_switch_strategies       # 状态切换类策略
│   ├── 01_volatility_compression_expansion
│   ├── 02_breakout_mode_scoring
│   ├── 03_regime_switch_signal
│   └── 04_transition_risk_control
│
├── 06_filtering_and_scoring          # 策略过滤与机会评分
│   ├── 01_setup_quality_score
│   ├── 02_breakout_quality_score
│   ├── 03_context_score
│   ├── 04_risk_reward_score
│   └── 05_ml_opportunity_model
│
├── 07_position_and_portfolio         # 仓位管理与组合风控
│   ├── 01_single_trade_risk
│   ├── 02_vol_targeting
│   ├── 03_sector_exposure_control
│   ├── 04_drawdown_control
│   └── 05_portfolio_allocation
│
├── 08_data_and_backtest_infra        # 数据工程与回测基础设施
│   ├── 01_continuous_contract
│   ├── 02_rollover_rules
│   ├── 03_transaction_cost_model
│   ├── 04_event_driven_backtest
│   └── 05_trade_log_and_evaluation
│
├── 09_ml_augmentation                # 机器学习增强
│   ├── 01_trade_filter_model
│   ├── 02_regime_classifier
│   ├── 03_mfe_mae_prediction
│   ├── 04_feature_store
│   └── 05_walk_forward_validation
│
└── 10_live_ops                       # 实盘执行与运维
    ├── 01_signal_to_order
    ├── 02_order_execution
    ├── 03_monitoring_and_alerting
    ├── 04_daily_review
    └── 05_strategy_iteration_loop
```

## 使用方式

### A. 给 Claude / Codex 读（让它写代码）

```text
# 让 Claude 按某个 skill 生成策略骨架
> 阅读 cta/cta_skills/03_trend_strategies/01_donchian_breakout.md，
> 按第 6 节"代码模块设计"在 cta/strategy/donchian_breakout/ 下建立骨架，
> 复用 cta/feature/price_action.py 中的 ATR 实现。
```

每个 skill 的第 6 节都有：目录结构 + 函数签名 + 依赖的现有模块，Claude / Codex 可以直接照着建 py 文件。

### B. 给人读（系统化学习）

推荐顺序：`00 → 01 → 02 → 03/04/05（任选策略族）→ 06 → 07 → 08 → 09 → 10`。

- 先看 `00` 明白「为什么研究」
- 再看 `01 / 02` 建立「看盘 / 看行情」的视觉 / 指标映射
- 然后挑一个策略族（趋势 / 震荡 / 状态切换）深入
- 最后过一遍风控 / 数据 / ML / 实盘的配套

### C. 新增 / 修改 skill 时

1. 从 `_TEMPLATE.md` 复制 10 节模板
2. 填完第 4 / 5 / 6 节（指标 + 策略映射 + 代码设计）才能算及格
3. 补第 10 节「与其他 skills 的关系」，至少 3 条双向链接
4. 在 `cta/report/change_log.md` 追加一条记录

## Skill 文件写作标准（硬性下限）

| 小节 | 最低要求 |
|------|----------|
| 4. 核心指标 | ≥ 3 行指标表，含参数建议 + 典型阈值 + 现有实现引用（或 `TODO: 待实现`） |
| 5. 常见策略映射 | ≥ 1 条完整策略：信号公式、开仓、止损、平仓、仓位 |
| 6. 代码模块设计 | 给出文件树 + ≥ 2 个带类型注解的函数签名 |
| 7. 回测评估重点 | ≥ 6 个指标 + ≥ 3 种稳健性检验 |
| 10. 与其他 skills 的关系 | ≥ 3 条双向链接 |

## 约定

- 所有路径均相对于**仓库根** `cta/...`，不使用 `./` 或绝对路径。
- 文件名 snake_case 英文；文件内主标题中文。
- 品种代码：`{SYMBOL}0.{EXCHANGE}`（`RB0.SHFE`）。
- 频率：`day | minute | minute5 | minute15 | minute30 | minute60`。
- 时间戳：运行结果用 `YYYYMMDD_HHMMSS`，数据文件用 `YYYY-MM-DD`。

## 已有可复用资产速查

| 文件 / 目录 | 能力 |
|-------------|------|
| `cta/feature/price_action.py` | 180+ `pa_*` 特征（K 线分类、signal bar、H1/H2/L1/L2、micro channel 等） |
| `cta/feature/price_action_advanced.py` | 高级价格行为特征 |
| `cta/feature/price_action_context.py` | 上下文 / 场景打分特征 |
| `cta/feature/feature_loader.py` | `load_symbol_features` / `load_symbol_feature_at` |
| `cta/feature/run_all_features.py` | 多进程 + 断点续跑 + 多频率批量生成 |
| `cta/strategy/brooks/core/` | 多周期共振 + ATR 风控 + XGBoost 门控参考实现 |
| `cta/strategy/brooks/backtest/runner.py` | 无 vnpy 依赖的事件驱动回测 |
| `cta/data_code/futures_downloader.py` | akshare 日线 + tushare 分钟 统一下载 |
| `cta/config/futures_meta.py` | 合约乘数 / 手续费 / 保证金 元数据 |
