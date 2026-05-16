下面是一份"老兵视角"的 review。结构按"上线前必须解决（P0）→ 方法论结构性（P1）→ 工程打磨（P2）"，每条都配文件位置与具体可操作的建议。

---

## P0 — 上实盘前**必须**先处理的问题

### P0.1 数据源不同源是最大的隐患
现状：日线走 akshare，分钟走 tushare（[futures_downloader.py:6-9](cta/data_code/futures_downloader.py:6)）。两个源的"主连规则"、"夜盘归属日"、"集合竞价处理"几乎一定不一样。

**实战后果**：你在 60min 上看到的"突破"，重采样到 day 不一定还突破；模型训练时 60min 的 future_mfe_atr 与 day 的根本不在同一价格序列上。我十几年里见过最隐蔽的"穿越"就是这种数据源不一致——回测看着很美，实盘第一周就被打爆。

**建议**：
1. 短期：写一份 `cta/data_code/cross_source_audit.py`，对每个 symbol 取 5 个抽样日，把 tushare 分钟数据按交易所规则重采样到 day，与 akshare 日线对 OHLCV 四列做精度 1‰ 的对账。差异超阈值的品种**直接禁用**，不进入训练。
2. 中期：选定一个权威源（推荐 tushare/RQData 单源），日线由分钟数据**本地重采样**生成，禁用 akshare 日线。

### P0.2 涨跌停 / 一字板 / 停牌没有显式建模
现状：回测里 [event_driven_backtest.py:24-50](cta/skills/data_backtest/event_driven_backtest.py:24) 只做了"next_bar high==low → 跳过"。但是：
- 一字涨停**当天**理论上"开仓买入不可得，平空可得"
- 国内合约**没有真停牌**但有"夜盘单边停牌"（如商品交易所节前停夜盘）

**实战后果**：回测里你以为开仓成功的"突破信号"，实盘是一字板根本进不去。模型把这些"虚假成交"当正样本学进去，OOT AUC 虚高就来源于此。

**建议**：
1. 在特征层加 `is_limit_up_close` / `is_limit_down_close` / `is_one_way_bar`（high==low 且 volume 异常低）三个布尔特征，喂给模型让它**主动学**这些场景。
2. 在 OOT 评估的 [pipeline_oot_evaluation.py](cta/model/pipeline_oot_evaluation.py) 里，对触及涨跌停的开仓行为强制 `execution_status="blocked_limit_move"`，新增 `blocked_limit_move_rows` 计数。
3. 在 `cta/strategy/baseline_skill_suite.py` 的 `_simulate_intrabar_exit` 里，涨停板只允许卖出方向、跌停板只允许买入方向（**这是中国期货的硬规则**）。

### P0.3 换月滚动 PnL 没有真正进回测
现状：[continuous_contract.py:71-120](cta/skills/data_backtest/continuous_contract.py:71) 有连续合约逻辑，但**模型用主连 RB0 训练 → 实盘下单到 rb2410**之间的"展期成本"没有被建模。

**实战经验**：商品期货主力月换月的 1-2 个交易日，**做多容易吃亏**（远月升水 → 滚动到远月时账面"亏"一段升水）。商品研究里这是 6-8% 年化的真实成本，被你的回测全吞掉了。

**建议**：
1. 在 ContractSpec 上加 `roll_cost_pct_per_year` 字段（不同品种不一样：股指 2%、黑色系 6%、农产品 4-12%）
2. 回测引擎在每月换月日按持仓方向扣一笔 `notional × roll_cost_pct_per_year / 12` 的"换月成本"
3. 实盘里这部分对应 `rollover_rules.py` 的实际平仓+新开仓双边手续费 + 滑点，校验回测扣减与实际开销差距

### P0.4 多周期混用时样本数严重失衡
现状：[run.sh:49](cta/run.sh:49) 让 5min/min 与 day/60min 共训。**5min 样本数是 day 的 240 倍**，pool 训练时小周期会主导。

**实战后果**：你的 POOL 模型学到的本质是"高频信号"，但你下单的最小粒度（手数）和盈亏量级是低频的——这是经典的 "AUC 高但 PnL 难做"。

**建议**：
1. **不要同 pool 训练**。按周期建独立模型：`day_model` / `60min_model` / `5min_model`。每个周期的 trade_filter_threshold、position_scale 可以不同。
2. 如果一定要 pool，加 `sample_weight = 1 / sqrt(samples_per_interval)`，让 day 与 5min 的"边际样本权重"接近。

### P0.5 标签里 `mfe - 0.7×mae` 的"先 mfe 还是先 mae"歧义
现状：[baseline_skill_suite.py:996-1003](cta/strategy/baseline_skill_suite.py:996) 用 horizon 内最大 high - 入场 / 入场 - 最小 low 算 mfe/mae，**完全忽略顺序**。

**实战后果**：标签把"先冲 mfe=2 ATR 再回到 mae=1.5 ATR"和"先 mae=1.5 ATR 出局后再 mfe=2 ATR"判为同样的正样本。但实战里这两种**完全不一样**——第二种早就止损了。模型学到的是"上帝视角的胜负"，不是"按规则执行的胜负"。

**建议**：把标签从"horizon 端点的 mfe/mae"改成"按止损规则模拟一次执行的真实 PnL"——其实你 OOT 已经有 `_simulate_intrabar_exit` 了，把这套逻辑也用到 training label 生成上，让 train/test 的 label 口径完全一致。

---

## P1 — 方法论 / 架构层面的结构性问题

### P1.1 训练集时间跨度太大、跨多个市场结构
现状：[run.sh:51-53](cta/run.sh:51) `START=2010 / TRAIN_END=2020 / VALID_END=2023`。

10 年的数据跨了：2015 股灾、2016 商品牛、2018 中美贸易战、2020 疫情、2021 双控、2023 复苏不及预期——**6 次完全不同的市场结构**。一个模型在这种数据上学到的"普适规律"通常是脆弱的。

**建议**：
- 把 walk-forward 窗口从"expanding 3 个 window"改为"3 年 train / 1 年 valid / 1 年 test"的**rolling** 窗口，每年滚一次，得到 8-10 个独立模型。
- 集成方式：实盘用最近 3 个 window 的模型投票或平均概率，单一模型失效时不至于全军覆没。

### P1.2 18 品种 POOL 训练忽略了品种异质性
现状：[run.sh:58](cta/run.sh:58) `POOL_INTERVALS="day 60min"`，把 18 个品种当同质样本喂模型。

**实战经验**：螺纹（RB）跟豆粕（M）跟黄金（AU）的微观结构完全不一样——日内波幅、跳空频率、夜盘活跃度、产业链驱动逻辑都不同。一个 POOL 模型本质是"找几个跨品种共性特征"，但 CTA 真正能赚钱的 alpha 通常在品种特性里。

**建议**：
1. 加 `symbol_cluster` 列（黑色 / 有色 / 化工 / 农产品 / 油脂 / 股指 / 国债 / 贵金属 7 类）作为模型 categorical 特征
2. 训练时用 `sample_weight × √(symbol_count / cluster_count)`，避免品种数多的板块主导
3. 或者更激进：**每个 cluster 一个 POOL 模型**

### P1.3 "trade_filter + regime + mfe_mae" 三段式 gate 是脆弱的串联
现状：[pipeline_oot_evaluation.py:415-433](cta/model/pipeline_oot_evaluation.py:415) 三个 gate 是 AND 关系。

**实战经验**：三段独立训练的模型串联，**最弱的一环决定整体**。如果 trade_filter 漏 40%、regime 再漏 30%、mfe_mae 再过滤 20%，剩下 28% 样本，胜率提升的边际效应几乎被"样本数减半"对统计显著性的伤害抵消。

**建议**：
1. 把三个模型的输出做成**特征**，再训一个 stacking 的最终决策模型——`x = [trade_prob, regime_label_encoded, pred_mfe, pred_mae]` 学到一个综合分。
2. 或更朴素：直接训单一回归模型预测 `expected_edge_atr`（mfe - 0.7×mae），用阈值切。三段式更像是"为了可解释"而非"为了准确率"。

### P1.4 LightGBM / HistGradientBoosting 单一模型架构
现状：[trade_filter_model.py:13](cta/model/trade_filter_model.py:13) 只用 HistGBT。

**建议**：CTA 上 boosting 系列对市场状态突变很脆弱。可以做一个简单的 ensemble：
- HistGBT（捕捉非线性交互）
- ElasticNet 逻辑回归（线性基线，过拟合风险低）
- 等权平均概率

实战里，这种"非线性+线性"的简单 ensemble 在 valid/test gap 上通常比单模型小 30%-50%。

### P1.5 特征工程的"高 IC 特征不一定真"
你的 leakage_audit 在 A0 上找出 `bias_5`、`log_ret_3`、`gap`、`pa_gap_bar_size` |IC|>0.30。

**老兵直觉**：`bias_5 = (close - MA5) / MA5`，跟 `fwd_return_5` 的 |IC|≈0.36 实际是合理的——MA5 是过去 5 根的均值，bias_5 大说明短期偏离，**有均值回归性**，这是真信号不是穿越。

但 `pa_swing_high_idx` |IC|=0.28 就**可疑**了——如果 swing high 是用过去 N 根识别（峰），这是合法的；但如果用了"中心化"窗口（左右各 N 根），就是穿越。**建议**：把 `cta/feature/` 下每个特征的计算函数列一个表格，手工标 `causal=true/false`，落到 `cta/feature/causality_manifest.csv`，CI 强制校验。

---

## P2 — 工程层面的打磨项

### P2.1 配置层用 dataclass 而非 pydantic
现状：[model_oot_eval_config.py](cta/config/model_oot_eval_config.py) 全是 dataclass(frozen=True)，无类型/值域校验。

**风险**：`risk_per_trade_pct=0.02` 写成 `0.2` 不会报错，单笔风险敞口直接 10 倍。

**建议**：迁到 pydantic v2 + `Field(ge=0, le=0.05)`，启动时校验所有金融参数的合理范围。这个改动 1 天工作量，回报极高。

### P2.2 随机种子不全局统一
摸底确认全局没有 `np.random.seed()`，只在模型层 `random_state=2026`。

**实战影响**：特征生成里若有任何 sampling / 哈希分桶用了 numpy 默认随机，**两次跑出来的特征 parquet 不完全一致**——你的"可复现实验"实际不可复现。

**建议**：在 `cta/cli.py` 和 `cta/run.sh` 入口处统一 `seed_all(int(RUN_TAG))`，包裹 numpy/random/sklearn/torch（若有）。

### P2.3 缺乏"实验产物 lineage"
现状：模型 joblib 旁边没有 git commit hash、数据 hash、特征版本号。

**建议**：每个 model_pipeline 输出目录加 `provenance.json`：
```json
{"git_commit": "3d00af96", "data_snapshot_hash": "sha256:...",
 "feature_manifest_hash": "...", "run_tag": "20260512", "python_version": "..."}
```
这是上线后排查"为什么这个模型跟我两个月前训的不一样"的唯一抓手。

### P2.4 仿真↔回测一致性校验缺位
摸底发现：`sim/tests/test_parity_check.py` 只是单测，**不是日运维工具**。

**建议**：写一个 `cta/sim/daily_parity_report.py`，每个交易日收盘后：
1. 取当日所有仿真订单 / 成交记录
2. 用同一份策略代码 + 当日 K 线跑 `replay_backtest_on_day`
3. 对比订单数、成交价、PnL，差异 > 5% 钉钉/企微告警

这是 P0.5 之后**判断策略真的可上实盘的核心闸门**。

### P2.5 风控规则过于理想化
现状：[model_oot_eval_config.py](cta/config/model_oot_eval_config.py) `weekly_max_drawdown_pct=0.02`、`max_total_leverage=2.0`。

**实战经验**：2% 周回撤上限在 CTA 里**几乎一定会触发**——单日 -1% 在波动大的品种很正常。一旦触发后 `block_new_entries_on_weekly_dd_breach=True` 直接停掉一周，年化机会成本巨大。

**建议**：
- 周回撤改 3%
- 触发后不"完全停"，而是 `max_position_scale *= 0.5`（缩仓而非停手）
- 加 `monthly_max_drawdown_pct=0.08` 作为更高优先级的硬熔断

### P2.6 测试覆盖的盲区
93 个测试看似很多，但缺核心场景：

| 盲区 | 应有的测试 |
|---|---|
| 涨跌停期间下单 | 一字板日不允许同方向开仓 |
| 换月当日 PnL | 主连价格跳变不应被回测当成盈亏 |
| 夜盘缺数据 | 节前停夜盘的合约不报错 |
| 多周期信号冲突 | 60min 多 + 5min 空时的优先级规则 |
| OOT 评估的 cap 优先级 | 5 个 cap 同时触发时的 block_reason 顺序 |

每个补一个 e2e 测试，1 周工作量。

### P2.7 `keyword.md` (18KB) / `run.md` (28KB) 与代码会脱节
两个 markdown 文件加起来 46KB，但缺 CI 检查"markdown 里提到的 CLI 选项是否仍存在"。

**建议**：写一个 `tests/test_docs_sync.py`，正则提取 markdown 里的 `--xxx` 参数和 `import` 路径，断言它们都还能在源码里 grep 到。

---

## 老兵的"如果只能改 3 件事"清单

按 ROI 排序，**如果时间紧迫只做这三件**：

1. **P0.5 — 把训练标签从"端点 mfe/mae"改成"模拟执行 PnL"**（1-2 周工作量，是 train/test gap 大的最主要原因）
2. **P0.1 — 数据源对账与单源化**（3-5 天，否则上面所有研究都是沙滩上的城堡）
3. **P2.4 — 仿真↔回测日终一致性报告**（3-5 天，决定你能不能放心从仿真切实盘）

其他都可以后置。Sharpe 提升不会来自更多特征或更花哨的模型——会来自**让回测信任度真正达到 80% 以上**。