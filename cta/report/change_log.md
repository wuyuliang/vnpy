# CTA 变更日志

按日期倒序记录每次改动。新增条目追加到顶部。

---

## 2026-04-24 — cta/skills 章节 02/03/04（Price Action / Trend / Range）代码化

**分支**: 当前  
**任务**: 按 `cta/cta_skills/02_price_action`、`03_trend_strategies`、`04_range_strategies`
顺序逐类实现，要求每类先测试再实现，并兼容
`day/minute60/minute30/minute15/minute5/minute`。

### 修改文件

- 新增包：`cta/skills/price_action/`（6 模块 + tests）
  - `tight_range_breakout.py`
  - `bull_bear_flag.py`
  - `breakout_pullback.py`
  - `hl_structure.py`
  - `failed_breakout.py`
  - `channel_state.py`
- 新增包：`cta/skills/trend_strategies/`（5 模块 + tests）
  - `donchian_breakout.py`
  - `atr_breakout.py`
  - `ma_trend_following.py`
  - `cross_sectional_momentum.py`
  - `trend_hold_trailing.py`
- 新增包：`cta/skills/range_strategies/`（4 模块 + tests）
  - `range_boundary_reversal.py`
  - `mean_reversion.py`
  - `false_breakout_reversal.py`
  - `noise_filtering.py`

### 运行命令

```bash
python3 -m unittest discover -s cta/skills/price_action/tests -v
python3 -m unittest discover -s cta/skills/trend_strategies/tests -v
python3 -m unittest discover -s cta/skills/range_strategies/tests -v
python3 -m compileall cta/skills/price_action cta/skills/trend_strategies cta/skills/range_strategies
```

### 输出位置

- 代码与测试：
  - `cta/skills/price_action/`
  - `cta/skills/trend_strategies/`
  - `cta/skills/range_strategies/`
- 测试与编译输出：控制台日志

### 主要结果

- 新增 15 个实现模块，33 个测试用例（02:18，03:14，04:11）全部通过。
- 关键检测/信号函数已提供统一周期兼容入口（`interval` 参数或无周期耦合实现）。
- 02/03/04 三类功能可独立调用，也可继续与 05-10 章节做联动集成。

### 风险与后续

1. 当前实现为可运行骨架版，部分细节阈值仍建议结合真实 `cta/data/feature` 样本做标定。  
2. 03/04 与 07 仓位风控、08 回测引擎可进一步打通做端到端组合回测。  
3. 若后续引入更复杂特征（如 `pa_leg_*`、`pa_channel_*` 完整版），建议优先补充回归测试后再替换规则。

---

## 2026-04-23 — cta/skills 章节 06-10（Filtering/Portfolio/Backtest/ML/LiveOps）代码化

**分支**: 当前  
**任务**: 按 `cta/cta_skills/06_filtering_and_scoring` 到 `10_live_ops` 顺序，
在 `cta/skills/` 下逐类实现；每类先写测试再写实现，兼容
`day/minute60/minute30/minute15/minute5/minute` 周期体系。

### 修改文件

- 新增包：`cta/skills/filtering_scoring/`（5 模块 + tests）
  - `setup_quality.py` / `breakout_quality.py` / `context_score.py` / `risk_reward_score.py` / `ml_opportunity_model.py`
- 新增包：`cta/skills/position_portfolio/`（5 模块 + tests）
  - `single_trade_risk.py` / `vol_targeting.py` / `sector_exposure.py` / `drawdown_control.py` / `portfolio_allocation.py`
- 新增包：`cta/skills/data_backtest/`（5 模块 + tests）
  - `continuous_contract.py` / `rollover_rules.py` / `transaction_cost.py` / `event_driven_backtest.py` / `trade_evaluation.py`
- 新增包：`cta/skills/ml_augmentation/`（5 模块 + tests）
  - `trade_filter_model.py` / `regime_classifier.py` / `mfe_mae_prediction.py` / `feature_store.py` / `walk_forward_validation.py`
- 新增包：`cta/skills/live_ops/`（5 模块 + tests）
  - `signal_to_order.py` / `order_execution.py` / `monitoring_alerting.py` / `daily_review.py` / `strategy_iteration_loop.py`

### 运行命令

```bash
python3 -m unittest discover -s cta/skills/filtering_scoring/tests -v
python3 -m unittest discover -s cta/skills/position_portfolio/tests -v
python3 -m unittest discover -s cta/skills/data_backtest/tests -v
python3 -m unittest discover -s cta/skills/ml_augmentation/tests -v
python3 -m unittest discover -s cta/skills/live_ops/tests -v
python3 -m compileall cta/skills/filtering_scoring cta/skills/position_portfolio cta/skills/data_backtest cta/skills/ml_augmentation cta/skills/live_ops
```

### 输出位置

- 代码与测试：`cta/skills/{filtering_scoring,position_portfolio,data_backtest,ml_augmentation,live_ops}/`
- 测试运行输出：控制台（无覆盖原始数据）

### 主要结果

- 新增 `06-10` 五章共 `25` 个实现模块、`56` 个测试用例，已全部通过。
- 每章先建测试后实现，已覆盖核心接口：评分/风控/组合/连续合约/回测引擎/ML 数据集与验证/实盘运维流程。
- 关键函数均可在 day/minute60/minute30/minute15/minute5/minute 场景下复用或无周期耦合。

### 风险与后续

1. 当前 ML 相关实现为“无重依赖轻量版”（便于本地可运行），后续可替换为 XGBoost/LightGBM 生产模型。  
2. 事件回测与执行模块为基础骨架（bar 级），后续可继续补充部分成交、挂单队列和更细粒度成交仿真。  
3. 建议下一轮在 `cta/data/feature` 实盘样本上做端到端联调（06 分数 -> 07 仓位 -> 08 回测 -> 09 gate -> 10 review）。

---

## 2026-04-23 — cta/skills 章节 05（Regime Switch Strategies）代码化

**分支**: 当前  
**任务**: 读取 `cta/cta_skills/05_regime_switch_strategies/*.md`，按 01→04 顺序在
`cta/skills/` 下落地代码，要求先写测试再写实现，并兼容
`day/minute60/minute30/minute15/minute5/minute` 六种周期名。

### 修改文件

| 文件 | 改动 |
|------|------|
| `cta/skills/regime_switch/__init__.py` | 新建；统一导出 05 章 API |
| `cta/skills/regime_switch/volatility_transition.py` | 新建；压缩/扩张/常态三态识别、切换事件、`rollback_if_false_switch` |
| `cta/skills/regime_switch/breakout_score.py` | 新建；`BreakoutScoreResult`、单 bar 突破评分、历史权重校准 |
| `cta/skills/regime_switch/switch_machine.py` | 新建；regime 状态机、置信度、age、next regime、策略白名单 |
| `cta/skills/regime_switch/transition_risk.py` | 新建；过渡期风控调整与 max-hold 判定 |
| `cta/skills/regime_switch/tests/__init__.py` | 新建；测试包初始化 |
| `cta/skills/regime_switch/tests/test_volatility_transition.py` | 新建；状态识别/回滚/多周期兼容测试 |
| `cta/skills/regime_switch/tests/test_breakout_score.py` | 新建；评分输出、HTF 对齐影响、权重校准测试 |
| `cta/skills/regime_switch/tests/test_switch_machine.py` | 新建；状态机输出、标签覆盖、白名单测试 |
| `cta/skills/regime_switch/tests/test_transition_risk.py` | 新建；transition 风控与持仓时长限制测试 |

### 运行命令

```bash
# 05 章节单测
python3 -m unittest discover -s cta/skills/regime_switch/tests -v

# 语法冒烟
python3 -m compileall cta/skills/regime_switch
```

### 输出位置

- 代码与测试：`cta/skills/regime_switch/`
- 无额外数据落盘；仅控制台测试输出。

### 主要结果

- `regime_switch` 新包已可导入，四个子模块均可独立调用。
- 单测 `17/17` 通过，覆盖状态切换、突破评分、状态机与过渡期风控核心路径。
- 六周期字符串兼容已纳入测试（day/minute60/minute30/minute15/minute5/minute）。

### 风险与后续

1. 当前 `calibrate_weights_from_history` 为轻量相关系数法（无 sklearn 依赖），后续可升级为 logistic / XGBoost 权重学习。  
2. `detect_vol_transition` 的阈值仍为规则参数，建议下一步在 RB0 + 研究池做阈值稳定性扫描。  
3. 下一个阶段按你的顺序继续实现 `06_filtering_and_scoring`，延续“先测试后实现”。

---

## 2026-04-22 — cta/skills 章节 00（Overview & Methodology）代码化

**分支**: 当前
**任务**: 把 `cta/cta_skills/00_overview_methodology/` 5 篇 md 的「第 6 节代码模块设计」
**全部落成可运行 + 可测的 Python 包**，放在 `cta/skills/overview/` 下；配置外置到
`cta/skills/configs/`，运行产物统一落 `cta/skills/output/`；所有函数对
`day / minute60 / minute30 / minute15 / minute5 / minute` 六种周期一视同仁。

### 修改文件

| 文件 | 改动 |
|------|------|
| `cta/skills/__init__.py` | 新建；暴露 `SKILLS_ROOT / CONFIG_DIR / OUTPUT_DIR / PROJECT_ROOT / CANON_INTERVALS` |
| `cta/skills/configs/acceptance.yaml` | 新建；Gate A/B 门槛（annret/maxdd/sharpe/calmar/trade_count/...）以 `{op,value}` 声明 |
| `cta/skills/configs/research_pool.yaml` | 新建；A 档（rank≤12）/ B 档（rank≤24）+ per-tier intervals + coverage 阈值 |
| `cta/skills/overview/__init__.py` | 新建；统一 re-export 5 个子模块公开 API |
| `cta/skills/overview/acceptance.py` | §01 门槛判定：`REQUIRED_COLUMNS / GateDecision / assert_passes_gate_a,b / assess_against_gates`；内置 `INTERVAL_TO_GATE` 映射 |
| `cta/skills/overview/research_pool.py` | §02 研究池：`PoolMember / ResearchPool / build_research_pool (lru_cache) / in_research_pool / resolve_research_symbols / reset_cache`；读 `cta/feature/symbols_research_ranking.csv` |
| `cta/skills/overview/backtest_principles.py` | §03 回测配置 + lookahead 检测：`BacktestConfig`（禁 `fill_model='close'` / 强制 `signal_lag_bars≥1`）+ `detect_lookahead`（shift_divergence + corr_with_future **前向收益** 双模式）+ `assert_no_lookahead` |
| `cta/skills/overview/live_principles.py` | §04 实盘原则：`LiveGateState`（6 阶段灰度）/ `CircuitAction` / `check_circuit`（daily_dd / consecutive_loss / signal_mismatch，按 stop>halt>warn 排序）/ `reconcile_positions` |
| `cta/skills/overview/iteration.py` | §05 想法追踪：`IdeaRecord` + `record_idea`（写 `meta.yaml + hypothesis.md` 模板，幂等）+ `list_open_ideas` / `advance_stage`；根目录 `cta/report/ideas/{YYYYMMDD}_{slug}/` |
| `cta/skills/overview/tests/test_acceptance.py` | 12 用例，含 Gate A/B 通过失败 / 缺列 / 混合 interval / yaml 覆盖 |
| `cta/skills/overview/tests/test_research_pool.py` | 10 用例，含默认构建 / 自定义 yaml / 非法周期 / require_feature 子集 |
| `cta/skills/overview/tests/test_backtest_principles.py` | 14 用例，含 BacktestConfig 校验 / 干净信号不报 / shift(-N) 偷看可检出 / assert 抛异常 |
| `cta/skills/overview/tests/test_live_principles.py` | 16 用例，含熔断等级排序 / cfg 覆盖 / 对账差集 |
| `cta/skills/overview/tests/test_iteration.py` | 14 用例，含 slugify / 幂等 / advance_stage |
| `cta/skills/overview/tests/test_smoke_rb0.py` | 2 用例端到端：真实 `cta/data/day/RB0.csv` + shift(-3) 合成偷看信号；合成跨 interval summary 跑 `assess_against_gates` 落盘到 `cta/skills/output/` |

### 设计要点

- **周期无关**：所有 API 收 `interval: str` 字符串走 `cta.feature.loader.normalize_interval`，6 种
  canonical interval 全部支持；`INTERVAL_TO_GATE` 把 day/60/30 → Gate A、15/5/1 → Gate B。
- **品种顺序**：`build_research_pool` 调 `load_symbols_ranked()`（昨日新增），强制按
  `research_rank` 升序；与 `cta/feature/run_all_features.py` 等入口一致。
- **lookahead 检测**：
  - `shift_divergence`：对比 `signal * fwd_ret` vs `signal.shift(1) * fwd_ret`（合规执行版），
    仅在 naive 绝对 sharpe > 0.5 时才报，避免对纯噪声信号假阳性。
  - `corr_with_future`：signal 与**未来 h 根 bar 的前向收益**（不是 raw close 价格）
    做 pearson；真实 RB0 日线 + `close.shift(-3)>close` 在 corr_threshold=0.15 下可检出。
- **yaml-driven 阈值**：Gate A/B 用 `{op, value}` 描述每个指标，新增指标/调整方向不用改代码。

### 运行命令

```bash
# 单元测试（全部 69 用例）
python3 -m unittest discover -s cta/skills/overview/tests -p 'test_*.py' -v

# 读研究池（按 research_rank 升序）
python3 -c "from cta.skills.overview import resolve_research_symbols; \
            print(resolve_research_symbols('A', interval='day', require_feature=True)[:5])"

# 新建一个想法
python3 -c "from cta.skills.overview import record_idea; \
            print(record_idea('Tight Range Breakout', owner='wu', created_at='2026-04-22'))"
```

### 输出位置

- 单测瞬时产物：`cta/skills/output/`（smoke test 自清理）
- 想法目录：`cta/report/ideas/{YYYYMMDD}_{slug}/`

### 风险与后续

- 本次**仅建 00 章**。后续按 `01_market_regime` → `10_live_ops` 逐章落代码。
- `detect_lookahead` 仍是启发式，最终需人工 code review 定性。
- `assess_against_gates` 的 `INTERVAL_TO_GATE` 默认把 minute15 算 Gate B，若用户希望
  minute15 保持 Gate A 可用 `gate_override={'minute15':'A'}` 覆盖。

---

## 2026-04-21 — 合并 FEATURE.md 到 FEATURES.md + 区分 bar-derivable vs 未来特征

**分支**: `feature`
**任务**: 按用户要求，把昨日新建的 `cta/data/feature/FEATURE.md` 并入既有
`cta/feature/FEATURES.md`，去重、按类整理，并把**非 day/minute/minute30 原始 bar
可直接派生**的特征集中到 §19 "未来特征" 以便后续接入。

### 修改文件

| 文件 | 改动 |
|------|------|
| `cta/feature/FEATURES.md` | 目录加三个区块；新增 §13-§18（bar-derivable 待实现）+ §19（未来特征） |
| `cta/data/feature/FEATURE.md` | 改为指向 `cta/feature/FEATURES.md` 的跳转说明，避免双源漂移 |

### §13-§18 新增类（bar-derivable）

- §13 波动率体制补充：atr_pct_zscore_252 / bb_width_zscore_252 / vol_regime_3 /
  vol_of_vol_20 / range_ratio_N / range_height_atr_N / consecutive_hh_N /
  consecutive_ll_N / zscore_close_N / breakout_dist_atr_N
- §14 Al Brooks 形态补充：pa_tight_range_flag_K / pa_bull_flag_flag /
  pa_bear_flag_flag / pa_bpb_flag / pa_swing_high_idx / pa_swing_low_idx /
  pa_trend_channel_top / pa_trend_channel_bot / pa_trend_channel_slope /
  pa_channel_width_atr
- §15 多周期对齐：htf_trend_score_day / htf_bias / mtf_trend_score_{60m,30m} /
  mtf_align_flag / ltf_signal_ready_5m / mtf_conflict_score
- §16 市场状态机：regime_label / regime_conf / regime_age / transition_flag /
  transition_risk
- §17 综合评分：trend_score / compression_score / expansion_score /
  breakout_mode_score / setup_quality_score / breakout_quality_score /
  context_score / rr_score
- §18 入场/止损建议价：atr_based_stop_{long,short} / atr_based_target_{long,short} /
  chandelier_stop_{long,short} / micro_channel_stop_{long,short} / liquidity_filter_pass

### §19 未来特征（9 小节汇总）

合约元数据 / 成本撮合 / 仓位运行时 / 组合层 / 持仓运行时 / 交易日志与标签 /
ML 模型产出 / 特征基础设施 / 实盘运维监控。每项带来源章节指回 `cta_skills/`。

### 去重说明

与既有 §1-§12 重名或同义的特征已跳过，例如：
- `ma_slope_N → slope_N`；`dmi_plus/minus → plus_di/minus_di`
- `pa_h1/h2/l1/l2_signal → pa_high_1_2_3 / pa_low_1_2_3`
- `pa_failed_breakout → pa_breakout_fail_N`
- `breakout_body_ratio → pa_bo_body_ratio_N`；`rv_N → realized_var_20 / hist_vol_N`
- `range_high_N/low_N → dc_upper_N/lower_N`；`gk_vol → gk_vol_20`

### 风险与后续

- §13-§18 **还未实现**；`python3 cta/feature/run_generate.py` 现在**不会**生成这些列。
  下一步按章节逐批加进 `cta/feature/` 的对应模块（如 §13 -> `volatility.py` 扩展；
  §14 -> `price_action_advanced.py` 扩展；§15-17 可新建 `regime.py` / `composite.py`）。
- §19 不在 bar 生成管道里，对应代码后续散落到 `cta/strategy/common/**`。

---

## 2026-04-20 — 建立 cta_skills 技能树文档体系 + FEATURE.md 聚合

**分支**: `feature`
**任务**: 在 `cta/cta_skills/` 下新建完整的中国商品 CTA 技能树文档，
覆盖方法论 → 市场识别 → 价格行为 → 策略族 → 过滤评分 → 仓位风控 → 数据回测
基础设施 → ML 增强 → 实盘运维的全链路；并把所有 skill 中出现的特征 / 指标
聚合到 `cta/data/feature/FEATURE.md`，作为策略 / ML / SQL 的统一特征索引。

### 新增文件（全部在 `cta/cta_skills/` 内，共 67 个 md）

| 目录 | 内容 |
|------|------|
| `cta/cta_skills/README.md` | 技能树总览、阅读顺序、写作标准 |
| `cta/cta_skills/_TEMPLATE.md` | 10 节通用模板 |
| `00_overview_methodology/` | 1 README + 5 skills（目标 / 边界 / 回测 / 实盘 / 迭代） |
| `01_market_regime/` | 1 README + 5 skills（趋势 / 震荡 / 突破阈值 / 波动率 / 多周期） |
| `02_price_action/` | 1 README + 6 skills（tight range / flag / bpb / H1H2 / 假突破 / channel） |
| `03_trend_strategies/` | 1 README + 5 skills（donchian / atr / ma / xs momentum / 持仓管理） |
| `04_range_strategies/` | 1 README + 4 skills（边界反转 / mean reversion / 假突破反转 / 噪声过滤） |
| `05_regime_switch_strategies/` | 1 README + 4 skills（压缩扩张 / 突破模式评分 / 切换信号 / 过渡风控） |
| `06_filtering_and_scoring/` | 1 README + 5 skills（setup / breakout / context / rr / ML） |
| `07_position_and_portfolio/` | 1 README + 5 skills（单笔 / vol / 板块 / 回撤 / 组合） |
| `08_data_and_backtest_infra/` | 1 README + 5 skills（连续合约 / 展期 / 成本 / 引擎 / 日志评估） |
| `09_ml_augmentation/` | 1 README + 5 skills（filter / regime / MFE-MAE / feature store / walk-forward） |
| `10_live_ops/` | 1 README + 5 skills（signal→order / 执行 / 监控 / 复盘 / 迭代闭环） |

**额外新增**：
| 文件 | 说明 |
|------|------|
| `cta/data/feature/FEATURE.md` | 15 大类特征聚合索引，对齐上述 67 个 skill 第 4 节 |

**未改动任何 `cta/cta_skills/` 之外的代码文件**，仅追加本 change_log 条目。

### 使用方式

- 人读：按 `00 → 01 → ... → 10` 顺序。
- Claude / Codex 读：指向单个 skill 文件（如 `03_trend_strategies/01_donchian_breakout.md`），
  它能按第 6 节代码模块设计直接生成 py 骨架。
- 写策略 / 训练 ML：先查 `cta/data/feature/FEATURE.md` 选特征。

### 风险与后续

- 本次只是文档；具体代码骨架需要后续 skill-by-skill 落地。
- FEATURE.md 中标 `TODO` 的项表示待实现，不是当前已用特征。
- 后续新增 skill 或改动第 4 节，需同步回 FEATURE.md。

---

## 2026-04-19 — Brooks v3:消费 pa_* 特征 + 多周期共振 + XGBoost 门控

**分支**: `feature`
**任务**: 按 `cta/strategy/brooks/brooks_v3.md` 把 v1 规则骨架升级到 v3:
消费预计算 pa_* 特征、HTF→MTF→LTF 共振、ATR 风控 + 组合回撤降仓、
完整成交日志(MFE/MAE/退出原因/特征快照)、XGBoost 训练+评分门控,
并按 `cta/feature/symbols_research_ranking.csv` 顺序选 `top_n` 品种。

### 修改文件

**新增**(全部在 `cta/strategy/brooks/` 内):

| 文件 | 说明 |
|------|------|
| `brooks_v3.md` | v3 实施方案(含 ranking + top_n + 分阶段交付) |
| `config/strategy.yaml` | 统一运行时参数(品种、周期、信号阈值、风控、模型) |
| `config/params.py` | 重写:BrooksV3Params 层级 dataclass,从 yaml 加载 |
| `config/symbols.py` | 重写:`resolve_symbols` 按 ranking CSV + tier + feature 过滤 top_n |
| `core/features/adapter.py` | 离线/在线统一特征接口,offline 路径走整段缓存 + searchsorted |
| `core/signal/{htf_bias,mtf_setup,ltf_entry}.py` | 三个时间级别的信号检测(全部消费 pa_*) |
| `core/risk/{sizing,stops,portfolio}.py` | 0.1% per trade + ATR 止损/移动止损/失败快退 + 2%/5% 降仓 |
| `core/model/{labeler,dataset,train_xgb,score_gate}.py` | XGBoost 完整训练/评分回路 |
| `core/trade_log.py` | `TradeRecord` + parquet writer(含特征 JSON 快照) |
| `core/strategy.py` | `BrooksV3Core`:HTF→MTF→LTF→gate→size→stops→log |
| `backtest/{engine,runner,reporter}.py` | 自研轻量回测引擎(不依赖 vnpy) + 批处理 CLI + 汇总 |
| `online/{runner,live_strategy}.py` | dry-run 回放驱动 + vnpy CtaTemplate 壳 |

**删除**:v1 `indicators/`, `patterns/`, `strategies/`, 旧 `backtest/runner.py`(原地替换)

### 运行命令

```bash
# 1) 规则回测(无模型)
python3 -m cta.strategy.brooks.backtest.runner \
    --top-n 3 --start 2023-01-01 --end 2024-12-31 \
    --capital 1000000 --model none

# 2) 训练 XGBoost(需先 `brew install libomp` 让 xgboost 可加载)
python3 -m cta.strategy.brooks.core.model.train_xgb \
    --top-n 8 --start 2018-01-01 --end 2022-12-31 \
    --target-rr 2.0 --target-bars 20 \
    --out-dir cta/strategy/brooks/models

# 3) 带模型 OOS 回测
python3 -m cta.strategy.brooks.backtest.runner \
    --top-n 8 --start 2023-01-01 --end 2024-12-31 \
    --model cta/strategy/brooks/models/xgb_<ts>.ubj

# 4) 在线 dry-run 冒烟
python3 -m cta.strategy.brooks.online.runner \
    --symbol RB0.SHFE --interval minute5 \
    --warmup-start 2024-01-01 --warmup-end 2024-03-31 \
    --live-start 2024-04-01 --live-end 2024-06-30 --dry-run
```

### 输出位置

- `cta/strategy/brooks/report/<YYYYMMDD_HHMMSS>/summary.csv` + `report.md`
- `.../per_run/<SYMBOL>/trades.parquet` + `daily_equity.csv`
- `cta/strategy/brooks/models/xgb_<ts>.ubj` + `xgb_<ts>.meta.json`

### 主要结论(2026-04-19 16:09 run)

- `top_n=3, 2023-01-01..2024-12-31, --model none`:只有 RB0.SHFE 有
  预计算特征(其它品种的 minute5 feature 尚未落盘),runner 自动过滤为 1 品种。
- 出 133 笔 round-trip,win_rate 27%,PF 0.48,max_dd -1.25%,
  total_return -5.17%(无模型过滤,预期偏负;模型门控是后续改进的关键抓手)。

### 风险 / 待确认项

1. **特征覆盖**:当前只有 `RB0.SHFE` 在 `cta/data/feature/minute5/` 下有文件,
   其它 A 档品种需先跑 `cta/feature/run_all_features.py` 生成特征,再扩充 top_n。
2. **XGBoost 依赖**:macOS 上 `xgboost` 需要 `libomp`,
   `brew install libomp`。未装时训练步骤会报 `libxgboost.dylib could not be loaded`。
3. **多头方向**:`pa_h123` 只编码 bull side,v3 暂只做多;后续 bear 需补 `pa_l123`。
4. **换月跳点**:连续合约拼接处未做特殊处理(与 v1 相同),信号可能在跳点被噪声触发。
5. **在线模式 HTF/MTF 预热**:`online/runner.py` 当前用整段 offline 读的方式预热 HTF/MTF
   (简化处理),真实接入 vnpy 网关时需要改为实时 FeatureGenerator 状态同步。

---

## 2026-04-19 — Brooks 策略阶段1:规则骨架落地

**分支**: `feature`
**任务**: 按 `cta/strategy/brooks/brooks.md` 阶段1 要求,把 tight range breakout、
breakout pullback continuation、High2 in bull trend 三个 Brooks 结构翻译成规则化
`CtaTemplate` 策略,利用现有中国商品日线数据跑通最小可复现回测。

### 修改文件

全部新增,严格限定在 `cta/strategy/brooks/` 内:

| 文件 | 说明 |
|------|------|
| `cta/strategy/brooks/README.md` | 目录说明、运行命令、已知局限 |
| `cta/strategy/brooks/__init__.py` | 包声明 |
| `cta/strategy/brooks/config/params.py` | `BrooksParams`:所有阈值集中(tight range / breakout / pullback / bull trend / High2 / 风控) |
| `cta/strategy/brooks/config/symbols.py` | 8 个目标品种 + 合约 size/rate/slippage/pricetick |
| `cta/strategy/brooks/indicators/price_action.py` | `BarFeatures` + `compute_bar_features`: ATR/EMA/body/wick/close_pos/prev_high_n/ema_slope |
| `cta/strategy/brooks/patterns/tight_range.py` | `detect_tight_range`: 区间宽度/ATR + 相邻 bar 重叠率 + 大实体 bar 占比 |
| `cta/strategy/brooks/patterns/breakout.py` | `detect_breakout_bar`: close 破 prev_high_n + body_ratio + close_pos + 放量 + range 扩张 |
| `cta/strategy/brooks/patterns/pullback.py` | `BreakoutRecord` + `update_breakout_tracker` + `detect_breakout_pullback` |
| `cta/strategy/brooks/patterns/high2.py` | `detect_bull_trend` + `High2Tracker` 状态机(TREND→PULLBACK→HIGH1→H1_FAIL→HIGH2)+ `detect_high2` |
| `cta/strategy/brooks/strategies/base.py` | `BrooksBaseStrategy(CtaTemplate)`: ATR 止损/移动止损/失败快退/最长持仓共用逻辑 |
| `cta/strategy/brooks/strategies/tight_range_breakout.py` | Baseline A |
| `cta/strategy/brooks/strategies/breakout_pullback.py` | Baseline B |
| `cta/strategy/brooks/strategies/high2_bull.py` | Baseline C |
| `cta/strategy/brooks/backtest/runner.py` | 基于 `vnpy_ctastrategy.BacktestingEngine` 的 CLI,输出 per-run trades/daily/stats + 汇总 summary.csv + report.md |

### 运行命令

```bash
# 仓库根目录下
cd /Users/wuyuliang/code/vnpy

# 全部 3 策略 × 8 品种
python3 -m cta.strategy.brooks.backtest.runner

# 子集
python3 -m cta.strategy.brooks.backtest.runner \
    --strategies tight_range_breakout,breakout_pullback \
    --symbols RB0.SHFE,CU0.SHFE \
    --start 2018-01-01 --end 2024-12-31 --capital 1000000
```

默认参数: `--start 2018-01-01 --end 2024-12-31 --capital 1_000_000`,
策略 / 品种都缺省为全量。

### 输出位置

`cta/strategy/brooks/report/<YYYYMMDD_HHMMSS>/`:
- `per_run/<strategy>__<vt_symbol>/` : `trades.csv`, `round_trips.csv`, `daily.csv`, `stats.json`
- `summary.csv` : 所有组合合并
- `report.md` : Markdown 表格(按策略 × 品种明细 + 按策略聚合)
- `run.log`

### 首次运行摘要 (2018-01-01 ~ 2024-12-31, 初始资金 100 万, 8 品种)

按策略聚合平均:
- `tight_range_breakout`: avg total return +31%, win rate 34%, PF 1.67, 70 round trips
- `breakout_pullback`: avg total return +31%, win rate 41%, PF 1.53, 50 round trips
- `high2_bull`: avg total return -4%, win rate 33%, PF 1.20, 211 round trips

代表单组合:I0 tight range +171%,CU0 pullback +199%,AL0 High2 +324%。

### 已知限制 / 阶段2 要做的事

- 固定 1 手仓位,未按 ATR 止损距离动态 sizing
- 主连合约未真实换月对齐,存在跳点偏差
- 所有品种用统一参数,未按品种调优
- 只做多
- 未实现结构特征工程 / MFE·MAE 标签 / 评分模型 / walk-forward / 假突破分析
- 规则参数为"能跑通"的保守值,**不代表实盘可用**

### 风险点

1. `BrooksParams.tr_range_to_atr_max=3.0` 已从最初 1.5 放宽为让 tight range 能出信号;
   后续需在阶段5 做参数稳健性扫描。
2. `_try_open_long` 使用 `close*1.01` 的 LIMIT 单下单,若次日高开跳空仍可能滑点,
   但在日线回测中近似合理。
3. `BreakoutRecord` 只跟踪一条待触发记录;真实情况下可能同时存在多条候选,
   阶段2 用特征工程后再优化。

---

## 2026-04-18 — `load_symbol_feature_at`：按时间点读特征 + 前视保护

**分支**: `feature`
**任务**: 增加按精确时间戳获取特征的 API，默认退 200ms 以避免引用未收盘 bar 造成前视偏差。

### 语义

```
effective_ts = pd.Timestamp(timestamp) - pd.Timedelta(lookback)   # 默认 lookback='200ms'
返回 datetime <= effective_ts 的最新一行特征
```

为什么是 "退 200ms"：
- 分钟 bar 通常按**收盘时间**打 datetime（end-stamp）：14:30 的 bar 意味它覆盖到 14:30
- 查询时刻 T=14:30:00.000，若直接用 `datetime <= T` 会包含刚收盘那一根
- 退 200ms → effective_ts=14:29:59.800，排除 14:30 bar，取 14:29 bar
- 这就保证拿到的特征是"T 时刻完全确定"的历史状态，没有任何未来信息

### 修改文件

| 文件 | 变更 |
|------|------|
| `cta/feature/feature_loader.py` | 新增 `load_symbol_feature_at(symbol, timestamp, interval, lookback, columns, max_days_back)` 返回 `pd.Series` 或 `None`；CLI 增加 `--at <YYYY-MM-DD HH:MM:SS> [--lookback 200ms]` |

### 接口

```python
from cta.feature.feature_loader import load_symbol_feature_at

# 分钟级（默认）
feat = load_symbol_feature_at("RB0", "2025-02-19 14:30:00",
                              interval="minute")
# → 返回 datetime <= 14:29:59.800 的最新一根 bar 的特征 Series

# 日级（建议 lookback='1D' 保证拿前一交易日已完全收盘的日线）
feat = load_symbol_feature_at("RB0", "2024-02-19",
                              interval="day", lookback="1D")

# 只要子集列
feat = load_symbol_feature_at("RB0", "2025-02-19 14:30:00",
                              columns=["close", "rsi_14", "atr_14"])

# CLI
python3 -m cta.feature.feature_loader --symbol RB0 --interval day \
    --at "2024-02-19 14:30:00" --lookback 1D
```

### 关键参数

| 参数 | 说明 |
|------|------|
| `timestamp` | 查询时刻，接受 str / datetime / pd.Timestamp |
| `lookback` | pd.Timedelta 能解析的字符串，默认 '200ms'；minute 级留 200ms 即可，day 级建议 '1D' |
| `max_days_back` | 当 `effective_ts` 之前当日无数据，向前回溯几天（默认 7），用于跨周末/节假日 |

### 跨节假日行为（已验证）

```python
load_symbol_feature_at("RB0", "2024-02-19 08:00", interval="day", lookback="9h")
# → 2024-02-08   （2024-02-09~18 春节休市，自动回退到春节前最后一个交易日）
```

### 实现策略

1. 在品种目录 `{interval}/{SYMBOL}/` 扫描所有按日 parquet 的 stem（日期）
2. 过滤出 `<= effective_ts.date()` 的日期列表
3. 倒序扫描前 `max_days_back` 天，对每天的 parquet 用 `datetime <= effective_ts` 过滤
4. 首个有匹配的日子返回其最后一行；全部回溯仍无匹配 → `None`
5. 每次只读 1 个按日 parquet，minute 级平均 < 50ms

### 风险点

- day 级 `datetime` 通常是 00:00:00，若 `lookback` 很小（< 1 个交易日）会拿到当日 bar（实际未收盘）。
  本函数不会自动处理"盘中用日线"的场景，使用时请显式传 `lookback='1D'`
- minute5/15/30/60 同样遵循"退 lookback 取最新"的规则；如果 bar 是 start-stamp 约定，
  用户应相应调大 lookback 到 1 个 bar 的间隔以确保安全

---

## 2026-04-18 — 特征流水线优化：长→短顺序 + 代码体检

**分支**: `feature`
**任务**: 3 项优化 + 文档一致性扫尾

### 1. 执行顺序：长周期 → 短周期

新的 `INTERVAL_RUN_ORDER = [day, minute60, minute30, minute15, minute5, minute]`。
`minute` 最耗时最耗内存，放到最后；`day` 最快放到最前。

- `--interval all` 自动展开为该顺序
- 手工指定多个频率（如 `--interval minute day minute15`）也会被重新排序
- 新增 `_order_by_run_priority()` 工具函数

### 2. 品种顺序：按 research_rank 升序

已验证 `load_ranking()` L603 `sort_values("research_rank")`，排名 1（RB0）最先。
这次用 RB0 的 2025-02 跑通全频率特征生成作为冒烟测试。

### 3. 代码体检修复

| 文件 | 问题 | 修复 |
|------|------|------|
| `run_all_features.py` | 遗留 `CANON_INTRADAY_INTERVALS` import 不再使用 | 删除 |
| `run_all_features.py` | 顶部 docstring 把 `_all_symbols.parquet` 写在 `{SYMBOL}/` 下，实际在 `{interval}/` 根 | 更正 |
| `run_all_features.py` | `day_threshold` 注释写 "5%" 但实际乘 0.01 (1%)，且 day 分支逻辑不清 | 拆成独立 `if canon == 'day'` 分支 + 注释对齐 |
| `feature_loader.py` | `load_cross_section` 的 end_date 过滤用 `+1 day` offset 会少量误收下一天数据 | 改用 `dt.strftime('%Y-%m-%d')` 串比较 |

### 冒烟测试

```bash
# 第一名品种 RB0 (research_rank=1) 跑 2025-02
python3 -m cta.feature.run_all_features \
    --interval all --symbols RB0 \
    --start-date 2025-02-01 --end-date 2025-02-28 --overwrite
```

- 执行顺序：`['day', 'minute']`（因当前仓库只有这两档原始数据）
- day: 18 个交易日，18 个 parquet，shape=(1, 362) 每文件
- minute: ~4500 bar × 437 cols → 18 个 parquet

验证点：
1. RB0/day/2025-02-05.parquet 至 2025-02-28.parquet 连续 18 天
2. `list_symbol_dates('RB0', 'day')` 返回有序日期列表
3. 按 `research_rank` 第一的 RB0 被 `load_ranking()` 放在首位

---

## 2026-04-18 — 特征落盘按日分片 + `--start-date/--end-date` 增量

**分支**: `feature`
**任务**: 把每品种的单一巨 parquet 拆成"每天一个 parquet"，支持指定
`--start-date / --end-date` 只补/重算某段时间，便于随时间推进做增量更新。

### 输出布局（新）

```
cta/data/feature/{interval}/{SYMBOL}/{YYYY-MM-DD}.parquet     # 按日分片
cta/data/feature/{interval}/_all_symbols.parquet              # 截面合并
cta/data/feature/finished.csv                                 # 新 schema
cta/data/feature/fail.csv
```

- 拆分粒度 = `datetime` 的自然日：day 每文件 1 行；minute 级每文件一个交易日的全部 bar
- 计算时仍喂入**全历史**（滚动特征不受 range 影响），只有 `[start_date, end_date]`
  内的日子会被写盘
- 旧的 `day/CU0.parquet` 格式的文件不会再写入，老文件会保留但不被引用；
  新文件落到 `day/CU0/{YYYY-MM-DD}.parquet`

### 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `cta/feature/run_all_features.py` | 修改 | 重写 `_worker_compute` 按日 groupby 落盘；`run_interval` 新增 `start_date/end_date` 参数；`run_cross_section` 按 `{SYMBOL}/*.parquet` 汇聚；新增 `--start-date/--end-date` CLI；跟踪 CSV 列扩展 (`days_written/date_start/date_end/output_dir`)；schema 漂移时旧文件归档为 `.legacy` |
| `cta/feature/feature_loader.py` | 新增 | `load_symbol_features(symbol, interval, start_date, end_date)`、`list_feature_symbols`、`list_symbol_dates`、`load_cross_section`，并提供 CLI：`python3 -m cta.feature.feature_loader --symbol CU0 --interval day --start-date 2024-01-01` |

### 新跟踪 schema

```
finished.csv:
  symbol, exchange, interval, status,
  days_written, rows, cols,
  date_start, date_end, output_dir,
  completed_at, detail
```

`days_written` 表示本次**实际写盘**的天数；已存在且大小够的按日 parquet 会被跳过
（此时 `days_written=0` 但 `status=success`）。

### CLI 新增用法

```bash
# 增量只补最近（指定 start-date，end-date 可选）
python3 -m cta.feature.run_all_features --interval day --start-date 2026-04-01

# 重算某段时间
python3 -m cta.feature.run_all_features --interval day \
  --start-date 2024-01-01 --end-date 2024-03-31 --overwrite

# 指定多品种 + 日期范围
python3 -m cta.feature.run_all_features --interval minute15 \
  --symbols CU0 RB0 --start-date 2024-01-01 --end-date 2024-06-30

# 截面在指定范围
python3 -m cta.feature.run_all_features --cross-section --interval day \
  --start-date 2024-01-01 --end-date 2024-12-31
```

### 断点续跑策略（更新）

- 无 date range 且非 `--overwrite`：走全局 (symbol, interval) `success` 跳过（老行为）
- 有 date range 或 `--overwrite`：始终进子进程，由子进程**按日**跳过已有文件

### 在线读取

```python
from cta.feature.feature_loader import load_symbol_features
df = load_symbol_features("CU0", interval="day",
                          start_date="2024-01-01", end_date="2024-03-31")
```

自动从 `cta/data/feature/day/CU0/*.parquet` 中筛选并拼接。

### 冒烟测试

```bash
python3 -m cta.feature.run_all_features --interval day --symbols CU0 \
  --start-date 2024-01-01 --end-date 2024-01-31
# -> days=22 rows=22 cols=362

python3 -m cta.feature.run_all_features --interval day --symbols CU0 \
  --start-date 2024-02-01 --end-date 2024-02-29
# -> days=15（增量）；目录累计 37 files

python3 -m cta.feature.run_all_features --cross-section --interval day \
  --start-date 2024-01-01 --end-date 2024-02-29
# -> _all_symbols.parquet: 37 行, 383 列
```

### 风险点

- 旧的 `cta/data/feature/{interval}/{SYMBOL}.parquet` 单文件会遗留，不影响新流程，
  可以安全删除以节省空间
- 跟踪 CSV schema 已变更，老 `finished.csv` 自动归档为 `finished.csv.legacy`，
  需要时手工对照
- `_all_symbols.parquet` 每次 `--cross-section` 都会被整体覆盖（不做增量）

---

## 2026-04-18 — 特征批量生成：全频率 + 多进程 + 新跟踪路径

**分支**: `feature`
**任务**: 让 `cta/feature/` 特征流水线覆盖 6 档频率、多进程并发、断点续跑，
跟踪文件移到 `cta/data/feature/`。

### 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `cta/feature/loader.py` | 修改 | 新增 `normalize_interval()` / `resolve_interval_dir()`，规范名+旧名兼容 |
| `cta/feature/run_all_features.py` | 重写 | ProcessPool 并发、per-interval workers 上限、新跟踪路径 |
| `cta/feature/online.py` | 修改 | `MIN_LOOKBACK` 兼容两套命名；`compute_features` 先归一化 interval |
| `cta/feature/compute.py` | 修改 | `--interval` choices 扩展到全 6 档 + 旧名；内部规范化 |

### 命名规范

**规范名**（对外主推）：`day / minute / minute5 / minute15 / minute30 / minute60`
**旧别名**（兼容输入）：`5min / 15min / 30min / 60min`

`loader.normalize_interval()` 统一归一化；`resolve_interval_dir()` 读数据时
优先规范目录，回退到旧目录。

### 跟踪文件（新位置）

```
cta/data/feature/finished.csv
    symbol, exchange, interval, status, rows, cols,
    output_path, completed_at, detail

cta/data/feature/fail.csv
    symbol, exchange, interval, error_type, error, failed_at
```

- 断点续跑：`finished.csv` 中 `status=='success'` 的 `(symbol, interval)` 下次直接跳过
- 失败不影响其它：每个 `(symbol, interval)` 独立子进程，崩溃捕获到 `fail.csv`
- 子进程级补偿：若目标 parquet 已存在且大于阈值（day=1M, minute=50M, 5min=10M, ...），
  即使 finished.csv 丢失也能跳过

### 并发策略（16 GB / 4 CPU）

| 频率 | worker 上限 | 说明 |
|------|------------|------|
| day | 4 | 特征 ~20 MB/品种，安全 |
| minute | **2** | 特征 ~2-3 GB/品种，限 2 避免 OOM |
| minute5/15/30/60 | 4 | 数据量小 |

`--workers N` 与 per-interval cap 取 `min`。子进程每个任务结束主动 `del + gc.collect()`。

### 输出目录

```
cta/data/feature/
├── day/{SYMBOL}.parquet
├── minute/{SYMBOL}.parquet
├── minute5/{SYMBOL}.parquet
├── minute15/{SYMBOL}.parquet
├── minute30/{SYMBOL}.parquet
├── minute60/{SYMBOL}.parquet
├── {interval}/_all_symbols.parquet   # 截面特征合并
├── finished.csv
└── fail.csv
```

### 运行命令

```bash
# 全部频率（自动识别 cta/data/ 已有目录）
python3 -m cta.feature.run_all_features

# 单频率 / 多频率
python3 -m cta.feature.run_all_features --interval day
python3 -m cta.feature.run_all_features --interval minute minute5
python3 -m cta.feature.run_all_features --interval 5min 15min   # 旧名也接受

# 只跑盘中
python3 -m cta.feature.run_all_features --interval intraday

# 指定品种
python3 -m cta.feature.run_all_features --interval day --symbols CU0 RB0

# 并发
python3 -m cta.feature.run_all_features --workers 4

# 先跑前 10 名品种
python3 -m cta.feature.run_all_features --max-rank 10

# 强制重算
python3 -m cta.feature.run_all_features --overwrite

# 截面特征（等品种跑完后执行）
python3 -m cta.feature.run_all_features --cross-section
```

### 在线复用

`cta.feature.online` 已同步兼容所有规范名+旧名：
```python
from cta.feature.online import compute_features, compute_latest_features, FeatureGenerator
feat = compute_features(df, interval="5min")       # 旧名 OK
feat = compute_features(df, interval="minute5")    # 规范名 OK
gen = FeatureGenerator(interval="minute15")
```

### 验证

- `--interval day --max-rank 4 --workers 4`：4 品种 9 秒跑完（并发加速 ~4×）
- `--interval minute60 --symbols RB0 --workers 1`：32281 行 × 362 列，65 秒
- resume 逻辑：第二次同参数运行 0 新任务、全 skip
- fail.csv 触发路径：`EmptyData` / `FileNotFound` / `WorkerCrash` 三类异常写入

### 主要结论

- 频率全覆盖 + 规范/旧名双支持；数据 code 与 feature code 使用同一套 canonical 逻辑
- 多进程并发把 day 级加速 ~4×；minute 级通过 worker cap 避免 OOM
- 跟踪文件与数据同目录（`cta/data/feature/`），便于迁移 / 压缩存档

### 风险 / 待确认

1. `INTERVAL_WORKER_CAP` 默认 minute=2；若服务器内存更大可直接覆盖 dict 或手动传 `--workers`
   （实际取 min(user, cap)，想更高需要改源码）
2. 大小阈值（`SIZE_THRESHOLDS`）是粗筛，主要用于 finished.csv 丢失时的兜底跳过；
   如果未来加了更多特征列，阈值可能偏低（需要重新评估）
3. 旧的 `cta/feature/symbols_feature_finished.csv` 不再使用，但保留未删除，方便对照历史记录

---

## 2026-04-18 — 下载代码迁移 cta/data_code/ + 空日志区间汇总

**分支**: `feature`
**任务**: 把下载脚本从 `cta/data/` 移到 `cta/data_code/`（数据与代码分离），
跟踪 CSV 也同步迁移；`empty.csv` 由逐日记录改为按时间区间汇总。

### 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `cta/data_code/__init__.py` | 新增 | 包声明 |
| `cta/data_code/futures_downloader.py` | 迁移 | 原 `cta/data/futures_downloader.py`，内容不变（路径常量依然指向 `cta/data/`） |
| `cta/data_code/download_all.py` | 迁移 + 改造 | 原 `cta/data/download_all.py`；导入改到 `cta.data_code.*`；`FINISHED_CSV` / `EMPTY_CSV` 改到同目录；新增 empty 区间聚合 |
| `cta/data/download_all.py` | 删除 | 迁移后清理 |

### empty.csv 聚合规则

原来每个空交易日写一行 → 现在按时间区间汇总。

字段:
```
symbol, exchange, interval, date_start, date_end, count, reason, recorded_at
```

聚合算法（`_aggregate_empty_rows`）：
1. 按 `(symbol, exchange, interval, reason)` 分组
2. 同组内日期排序；相邻日期差 > `EMPTY_GAP_DAYS`（默认 60 天）拆成新区间
3. 日期 > `MAX_EMPTY_DATE` = **2026-04-17** 一律忽略（视为未来/未落地）

示例（ZS0 minute 在 2009 年有连续空日）：
```
symbol  exchange  interval  date_start  date_end    count  reason         recorded_at
ZS0     DCE       minute    2009-03-30  2009-12-31  185    tushare_empty  2026-04-18 10:25:36
```

落盘时机：每处理完一个品种的分钟级下载就聚合 flush 一次（锁保护），
多品种并发安全。

### 目录

```
cta/data_code/                      # ← 代码 + 跟踪 CSV
  ├── __init__.py
  ├── futures_downloader.py
  ├── download_all.py
  ├── finished.csv                  # 新位置
  └── empty.csv                     # 新位置 + 区间汇总格式
cta/data/                           # ← 数据（不变）
  ├── day/{SYMBOL}.csv
  ├── minute/{PREFIX}/YYYY-MM-DD.parquet
  └── minute5|15|30|60/{PREFIX}/...
```

### 运行命令

```bash
export TUSHARE_TOKEN="你的tushare付费token"

# 全量
python3 -m cta.data_code.download_all

# 只下日线（无需 token）
python3 -m cta.data_code.download_all --intervals day

# 只下分钟级
python3 -m cta.data_code.download_all --intervals minute minute5 minute15 minute30 minute60

# 先跑前 10 名
python3 -m cta.data_code.download_all --max-rank 10

# 指定品种
python3 -m cta.data_code.download_all --only-symbols CU0 RB0
```

### 主要结论

- 代码集中在 `cta/data_code/`，不再与数据混放；数据目录 `cta/data/` 继续承担只读/追加。
- 跟踪 CSV 和脚本放一起，便于 git 管理 + 备份（数据本身不跟 git）。
- empty.csv 区间汇总大幅降低条目数量：2000+ 天逐日 → 通常 1-3 条区间/品种频率。
- 原逐日 `empty` 信息未丢失，仍在内存里参与区间合并。

### 风险与注意

1. 如果之前已有 `cta/data/finished.csv` / `cta/data/empty.csv`，需**手动移动**到 `cta/data_code/`
   （当前尚未生成，不存在兼容问题）。
2. `EMPTY_GAP_DAYS` 默认 60 天：若某品种空日很稀疏（例如每 2-3 个月才一次），可能被拆多段；
   真实场景中"整段时间无数据"的空日基本是连续或日频，60 天足够合并。如需更粗粒度，
   在 `cta/data_code/download_all.py` 里调大 `EMPTY_GAP_DAYS`。
3. `MAX_EMPTY_DATE = "2026-04-17"`：晚于此日期的空日被丢弃（视为数据尚未落地），
   可按需更新为当前日期。

---

## 2026-04-18 — 统一商品期货多频率批量下载器

**分支**: `feature`
**任务**: 整合 akshare（日线）+ tushare（分钟）两套下载脚本，做成可配置、可断点续跑、
可复用于在线单次下载的统一大任务。

### 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `cta/data/futures_downloader.py` | 新增 | 可复用核心下载器（离线/在线共用） |
| `cta/data/download_all.py` | 新增 | 批量编排脚本，按 `research_rank` 顺序 |
| `cta/feature/loader.py` | 修改 | `INTRADAY_INTERVALS` 兼容新旧两套目录命名 |

### `cta/data/futures_downloader.py`（新增）

- `RateLimiter(max_per_min)`：线程安全令牌桶，默认 450 req/min（tushare 付费 ~500 留余量）
- `FuturesDownloader`
  - `download_day(symbol, exchange)`：akshare `futures_main_sina` → `cta/data/day/{SYMBOL}.csv`
  - `fetch_fut_mapping(symbol, exchange)`：自动按 `SHF/SHFE`、`CZC/ZCE/CZCE`、`GFE/GFEX` 变体尝试
  - `fetch_1min_day(contract, trade_date)`：tushare `ft_mins` 单日全量 1min
  - `resample_minute(df_1min, freq)`：按自然日分组重采样，避免跨夜盘空 bar
  - `download_day_minute_all(...)`：**在线/离线共用入口**，一次 1min 拉取 + 多频率落盘
- 常量：
  - `MINUTE_INTERVALS = (minute, minute5, minute15, minute30, minute60)`
  - `RESAMPLE_FREQ` 映射
  - `EXCHANGE_VARIANTS` 交易所兜底列表

### `cta/data/download_all.py`（新增）

- 按 `cta/feature/symbols_research_ranking0.csv` 的 `research_rank` 从小到大依次下载
- CLI：`--intervals / --workers / --rate-limit / --max-rank / --only-symbols / --token`
- 断点续跑三级：
  1. `finished.csv` 里 `(symbol, interval)` 状态 `success|empty` → 整个品种-频率跳过
  2. 逐日 parquet 存在 → 跳过该日
  3. 日线 CSV 存在 → 跳过该品种
- 并发：外层品种顺序、内层按日 `ThreadPoolExecutor(workers=4)`，共享 `RateLimiter`
- 全局锁下 `append` 写入 `finished.csv` / `empty.csv`，线程安全

#### 输出目录
```
cta/data/day/{SYMBOL}.csv
cta/data/minute/{PREFIX}/{YYYY-MM-DD}.parquet      # 1min
cta/data/minute5/{PREFIX}/{YYYY-MM-DD}.parquet     # 由 1min 本地重采样
cta/data/minute15/{PREFIX}/{YYYY-MM-DD}.parquet
cta/data/minute30/{PREFIX}/{YYYY-MM-DD}.parquet
cta/data/minute60/{PREFIX}/{YYYY-MM-DD}.parquet
cta/data/finished.csv                              # symbol,exchange,interval,status,rows,...
cta/data/empty.csv                                 # symbol,exchange,interval,trade_date,reason,...
```

### `cta/feature/loader.py`（修改）

```python
INTRADAY_INTERVALS = [
    "minute",
    "minute5", "minute15", "minute30", "minute60",   # 新（download_all.py 约定）
    "5min", "15min", "30min", "60min",               # 旧（向后兼容）
]
```
`load_intraday_data` / `list_intraday_symbols` 已经按字母前缀扫描所有匹配目录，
无需改动其它逻辑；新旧目录并存不冲突。

### 运行命令

```bash
# 前置：tushare 付费 token
export TUSHARE_TOKEN="你的tushare付费token"

# 全量（6 档）
python3 -m cta.data.download_all

# 只下日线（不需要 tushare token）
python3 -m cta.data.download_all --intervals day

# 只下分钟级（1/5/15/30/60min）
python3 -m cta.data.download_all --intervals minute minute5 minute15 minute30 minute60

# 先跑前 10 名试水
python3 -m cta.data.download_all --max-rank 10

# 指定单品种补数据
python3 -m cta.data.download_all --only-symbols CU0 RB0

# 自定义并发 / 速率
python3 -m cta.data.download_all --workers 4 --rate-limit 450
```

### 在线复用示例

```python
from cta.data.futures_downloader import FuturesDownloader

dl = FuturesDownloader()
results = dl.download_day_minute_all(
    symbol="CU0", exchange="SHFE",
    contract_code="CU2501.SHF", trade_date="2025-01-15",
    intervals=["minute", "minute5", "minute15"],
)
# 返回 {interval: DownloadResult}
```

### 主要结论

- 1min 拉一次 → 本地重采样 5/15/30/60min：**API 用量 ~1/5**，各频率时间戳严格一致
- 71 品种 × ~3000 交易日 × 1min ≈ 20 万次 ft_mins 调用 @ 450/min ≈ 8 小时（粗估）
- `finished.csv` 以 `(symbol, interval)` 为键，中断再跑不会重复，空数据（empty）也记录避免反复扫
- 与下游 `cta/feature/run_all_features.py` 的 `INTRADAY_INTERVALS` 兼容，不影响已有特征流水线

### 风险 / 待确认项

1. **`symbols_research_ranking0.csv` 当前仅 1 行（RB0）**，完整 71 行在 `symbols_research_ranking.csv`。
   如需全量，恢复文件或改 `download_all.py` 里的 `RANKING_CSV` 常量。
2. `resample_minute` 使用 `label='left', closed='left'`；tushare 原生 1min 的 `trade_time` 语义是 bar
   **结束时间**。若下游策略严格依赖结束时间，把 `label='right'` 切换即可。
3. 部分小品种 `fut_mapping` 可能返回空，已降级为 `empty`（非 `error`）。
4. tushare 接口偶发限流，`_safe_retry` 配置为 3 次指数等待，极端情况下单日失败会记入 `empty.csv`。

---

## 2026-04-22 — 实现 §13-§18 特征 + loader 反污染 + tod 门控修复

- **分支**: `feature`
- **任务**: 按 `cta/feature/FEATURES.md` §13-§18 在现有代码基础上补齐 bar-derivable 特征，并修复 `cta/feature/code_review.txt` 中的 1/3 号 issue。

### 修改文件

1. `cta/feature/loader.py`
   - `load_intraday_data` / `list_intraday_symbols` 改为**严格按目录名的 `split('.')[0]` 匹配**，不再用字母前缀；
     - `symbol='CU0'` 只纳入 `CU0.SHF` / `CU0.SHFE`，不会吸收 `CU_small`；
     - `list_intraday_symbols` 按 symbol（含后缀）分组，避免跨品种合并。
   - 保留对"symbol 不带末尾 0"的目录回退匹配（如 `minute5/RB` 对应 `RB0`）；仅当精确匹配无命中时才触发。
   - 新增 `_dir_symbol(name)` 工具函数；交易所缩写别名表（SHF/SHFE、CZC/ZCE/CZCE 等）集中在 `exch_aliases`。
2. `cta/feature/compute.py`
   - tod 同比特征门控**显式注释 + 保持为 `interval == 'minute'`**：minute5/15/30/60 的 bar 本身已跨多分钟，内部再按 1/3/5/10/20 分钟粒度聚合没有物理意义。
   - 改为接收 canonical interval（含 minute5/15/30/60），不再在上游归一到 "day"/"minute"。
   - 在 `parts` 中新增 §13-§15 / §18 的调用；新增 §16 / §17 的二阶段聚合（先算上游再喂给 regime / composite）。
3. `cta/feature/run_all_features.py`
   - `_worker_compute` 直接把 `canon` 传给 `compute_single_symbol_features`，让 compute 内部根据频率决定是否算 tod / LTF。
4. `cta/feature/FEATURES.md`
   - 文首入口改指向 `python3 -m cta.feature.run_all_features`（按日分片、支持全频率）；`run_generate.py` 标注为旧入口、不推荐。
   - §11 明确写"仅在 1 分钟 bar 上生成"，与 compute.py 行为对齐。
5. 新增 6 个特征模块（均在 `cta/feature/` 下）：
   - `volatility_regime.py` — §13，10 组列（zscore、regime_3、range_ratio、consecutive_hh/ll、breakout_dist_atr 等）。
   - `price_action_supplement.py` — §14，10 列（tight_range、bull/bear_flag、bpb、swing_idx、trend_channel 4 列）。trend_channel 用移动 buffer 做最近 3 swing 拟合，避免 O(n²)。
   - `multi_timeframe.py` — §15，7 列（HTF/MTF 三档趋势分 + 对齐 flag + conflict + LTF 信号）。用内部 resample（trade_date / dt.floor('60T' / '30T' / '5T')）算各频率 bar，再 `shift(1).map(key)` 防未来信息。
   - `regime.py` — §16，5 列（softmax + argmax 得 regime_label，cumcount 算 regime_age）。
   - `composite.py` — §17，8 个 0-1/−1-1 分数（trend / compression / expansion / breakout_mode / setup_quality / breakout_quality / context / rr）。
   - `entry_stop.py` — §18，9 列（atr_stop/target、chandelier、micro_channel、liquidity_filter）。

### 最小可复现实验

```bash
# 重置 5 个品种的 2024 产物
rm -rf cta/data/feature/day/{RB0,HC0,I0,JM0,J0} cta/data/feature/minute5/{RB0,HC0,I0,JM0,J0}

# day + minute5，2024 全年，4 workers
python3 -m cta.feature.run_all_features \
  --interval day minute5 \
  --symbols RB0 HC0 I0 JM0 J0 \
  --start-date 2024-01-01 --end-date 2024-12-31 \
  --overwrite --workers 4
```

### 结果

- **day（5 品种）**: 全部 OK，每品种 242 个交易日、426 列，合计 ~28s。
- **minute5**: RB0 OK（242 天，17,491 行，426 列，~495s）；HC0/JM0/I0/J0 因本地暂无 minute5 数据而 FileNotFound（非代码问题，数据端未下载）。
- §13-§18 全部 47 列均写入 parquet，RB0 2024-12-31 尾 bar 有合理值：`trend_score=-0.29, regime_label=transition, atr_based_stop_long=3234.75, htf_trend_score_day=-0.27`。
- minute5 RB0 `htf_trend_score_day` 在当日 48 根 bar 内保持常量（预期），`mtf_trend_score_60m` 在 13:35 切负、15:00 回升；`regime_label` 呈 range / transition 交替。

### 输出位置

- `cta/data/feature/day/{SYMBOL}/{YYYY-MM-DD}.parquet`
- `cta/data/feature/minute5/{SYMBOL}/{YYYY-MM-DD}.parquet`
- `cta/data/feature/finished.csv` / `fail.csv`（新增 5 条 day + 1 条 minute5 成功 + 4 条 minute5 FileNotFound）

### 风险 / 待确认项

1. **code_review #2、#4（`run_generate.py` 旧入口的布局与 CLI 问题）**：本轮只在文档中明确弃用并指向 `run_all_features.py`，代码层未强制改动。如需彻底下线，下一轮可把 `run_generate.py` 改成 `sys.argv` 透传给 `run_all_features.main()` 的 shim。
2. **regime.py 阈值**：当前 trend_up/range/compression/expansion 的分段阈值（30/70 分位、0.6 trading_range、-0.5 bb_z、0.5 atr_z）是 FEATURES.md 中建议值；品种/周期差异下可能需按品种再做一次分位标定。
3. **multi_timeframe 的 LTF 信号**：仅在 `interval in (minute, minute5)` 启用；更高频率（15/30/60m）下 `ltf_signal_ready_5m=0`，这是设计使然（避免未来信息）。
4. **minute5 耗时**：RB0 单品种 17k bar 约 495s，其中 trend_channel 与 regime softmax 占比较高；全 71 品种全历史仍建议夜间批处理。

---

## 2026-04-23 · main · run_all_features 诊断日志 + 1min 内存治理

### 背景

服务器上跑 1-minute 全量生成（71 品种）出现 success=1 / fail=70 的系统性失败，但
旧的单层 try/except 把 4 个阶段的异常全部归到同一个 `error_type`，`fail.csv` 里
看不清到底死在哪一步。同时 `ProcessPoolExecutor` 默认不会回收 worker，
1-minute 单品种峰值 RSS 2-3GB，跑多个品种后 worker 内存持续累加很容易 OOM 被 kill。

### 任务

1. 把 `_worker_compute` 拆成 4 个独立阶段，各自 try/except，`error_type` 精确到
   `LoadError:*` / `ComputeError:*` / `FilterError:*` / `WriteError:*`。
2. 每阶段打印 `pid / symbol / 行列数 / 阶段耗时 / RSS` 日志。
3. 1-minute interval 下启用 `max_tasks_per_child=1`（Python 3.11+），每处理完 1
   个品种就回收 worker 进程，彻底释放内存。
4. 主进程每完成 5 个品种触发一次 `gc.collect()` 并打印 `main_rss / 进度`。

### 修改文件

**`cta/feature/run_all_features.py`**

- 新增 `_rss_mb()` 工具函数，兼容 macOS（`ru_maxrss` bytes）与 Linux（KB）。
- `_worker_compute` 结构重写：
    - Stage 1 `load`: `LoadError:FileNotFound / LoadError:<ExcName> / LoadError:Empty`
    - Stage 2 `compute`: `ComputeError:<ExcName>` / `ComputeError:Empty`
    - Stage 3 `filter`: `FilterError:MissingDatetime / FilterError:<ExcName>`
    - Stage 4 `write`: `WriteError:<ExcName>`（附 `written_before_fail` 计数）
    - 每阶段成功后打印 `begin / loaded / features / done` 四行日志，含 `rss`
      和 `total_t / peak_delta`。
    - Stage 2 结束后立即 `del df; gc.collect()` 避免 compute + 原始 df 叠加。
    - `detail` 字段拆分成 `load=…s compute=…s write=…s rss_end=…MB`，
      方便从 `finished.csv` 直接看阶段耗时。
- `run_interval` 增强：
    - `canon == "minute"` 且 Python ≥3.11 时 `max_tasks_per_child=1`，打印启用提示。
    - 每 `GC_EVERY=5` 个完成或最后一个完成后主进程 `gc.collect()` 并打印
      `progress / ok / fail / main_rss`。

### 未改动

- `cta/feature/compute.py`、`cta/feature/minute_tod.py`：本轮仅做 review，未发现
  明确 bug。真正的 1-min 失败类型待加上分阶段日志后重跑一轮即可从 `fail.csv`
  `error_type` 列直接定位。

### 运行命令

```bash
# 服务器上重跑 1-minute（旧 fail 会被覆盖 append）
python3 -m cta.feature.run_all_features --interval minute

# 若要先清空旧 fail.csv 便于观察新 error_type
rm -f cta/data/feature/fail.csv
python3 -m cta.feature.run_all_features --interval minute
```

### 输出位置

- `cta/data/feature/minute/{SYMBOL}/{YYYY-MM-DD}.parquet`
- `cta/data/feature/finished.csv`（`detail` 列新增 load/compute/write 耗时拆分）
- `cta/data/feature/fail.csv`（`error_type` 列新增阶段前缀）

### 风险 / 待确认项

1. `max_tasks_per_child=1` 要求 Python 3.11+。服务器如果是 3.10 及以下会被
   `sys.version_info` 检查跳过（仍按默认行为运行，不报错，但退回内存累积模式）。
2. 若重跑后 `fail.csv` 里仍是 `WorkerCrash`（主进程捕获的异常，子进程已死），
   则 90% 是 OOM / SIGKILL，下一步需要降低 per-worker 内存（如进一步精简
   `compute_minute_tod_features` 的中间拷贝，或显式 `chunksize` 聚合）。
3. Stage 日志级别是 INFO，跑 71 品种 × 4 行 ≈ 284 行额外日志；若嫌噪音可改 DEBUG。
