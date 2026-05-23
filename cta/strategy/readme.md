# cta/strategy/

## 主要做什么

**策略实现层**：把 [cta/skills/](../skills/) 的 skill 组件组装成可回测/可上线的具体策略。每个策略一个文件，配套 `*_backtest.py` 或 `*_cli.py`。下半部分是该目录的方法论 / 路线图（保留历史笔记）。

## 目录索引

### 基线候选 / Skill suite
| 文件 | 作用 |
|---|---|
| [baseline_skill_suite.py](baseline_skill_suite.py) | **候选生成主入口**：`generate_candidate_opportunities()` 给 strategy/model 提供统一候选事件流（带 future_mfe_atr/mae 标签） |
| [baseline_candidate_gen.py](baseline_candidate_gen.py) | 单 setup 候选生成的底层细节 |
| [baseline_setup_detection.py](baseline_setup_detection.py) | setup 识别（窄幅整理 / 突破 / 单边）的可量化规则 |
| [baseline_feature_frame.py](baseline_feature_frame.py) | candidate × 特征 join 的 frame builder |
| [baseline_helpers.py](baseline_helpers.py) | 通用工具 |
| [baseline_backtest_cli.py](baseline_backtest_cli.py) | baseline 策略组回测 CLI |
| [baseline_strategies.py](baseline_strategies.py) | baseline 各策略类的注册/调度 |

### 具体策略
| 文件 | 主题 | 文档 |
|---|---|---|
| [skill_tight_range_breakout.py](skill_tight_range_breakout.py) | 窄幅整理 + 突破 | — |
| [cross_sectional_momentum_rotation.py](cross_sectional_momentum_rotation.py) | day 级截面动量轮动候选生成，输出 long/short rebalance candidate | [cross_sectional_momentum_rotation_design.md](../docs/cross_sectional_momentum_rotation_design.md) |
| [spread_arbitrage_strategy.py](spread_arbitrage_strategy.py) + [spread_state.py](spread_state.py) + [calendar_spread_state.py](calendar_spread_state.py) | 跨品种/跨期价差套利核心（z-score 进出场，双腿 order intent，calendar rollover 状态机） | [cross_instrument_calendar_spread_arbitrage_design.md](../docs/cross_instrument_calendar_spread_arbitrage_design.md) |
| [skill_tight_range_backtest.py](skill_tight_range_backtest.py) | 上面策略的回测 entrypoint | — |
| [price_action_breakout_engine.py](price_action_breakout_engine.py) + `_rules.py` / `_indicators.py` / `_report.py` | Brooks 价格行为突破四件套 | [breakout.md](breakout.md) |
| [backtest_price_action_breakout.py](backtest_price_action_breakout.py) | 上面策略的回测 entrypoint | — |
| [cta_baseline.py](cta_baseline.py) / [cta_tight_range.py](cta_tight_range.py) / [cta_adapter.py](cta_adapter.py) | 接入 vnpy CtaTemplate | — |
| [demos.py](demos.py) | 教学 demo | — |

### Brooks 系列
- [brooks/](brooks/) — Brooks 价格行为学完整子项目（详见 [brooks/README.md](brooks/README.md)）。

### 历史产出
- `output_price_action_breakout/per_symbol/<SYM_EX>/` — 旧的逐品种回测结果归档。

## 注意事项（开发新策略前必读）

- **文件 500 行内**：超出按"engine / rules / indicators / report"四件套拆。
- **禁止特征穿越**：candidate 的 `entry_datetime` 之前的 bar 才能用于信号；`future_*` 字段只能用作 label。审计走 [cta/model/tools/leakage_audit.py](../model/tools/leakage_audit.py)。
- **统一 candidate schema**：所有策略产出的 candidate DataFrame 必须含 [cta/model/feature/candidate_schema.py](../model/feature/candidate_schema.py) 定义的核心字段，否则 model pipeline 拼不上。
- **手续费/滑点**：从 [cta/config/futures_meta.py](../config/futures_meta.py) 取，不硬编码；`event_driven_backtest` 已统一处理。
- **vnpy 框架接入**：只在 `cta_*.py` 系列里 import vnpy；纯研究策略不 import vnpy，方便单元测试。
- **测试**：`pytest cta/strategy/tests/ -v`；split-contract 测试不能挂。
- **策略文件顶部必写**（来自 `cta/README.md` 第五节约定）：策略假设 / 适用品种周期 / 信号定义 / 开仓平仓止损规则 / 仓位管理 / 手续费滑点假设 / 可能失效市场环境。
- **新策略上线 checklist**：
  1. `cta/report/backtest/` 有完整产物（含 OOT）
  2. `cta/model/model_pipeline.py` 能用它的 candidate 训练完整三段模型
  3. 通过 `leakage_audit`
  4. 通过 baseline 对比（≥ baseline 的胜率与净 PnL）

---

# 方法论与路线图（历史笔记，保留备查）

后面不是立刻上实盘，而是走这条主线：

## 先把"特征"变成"标签"

你现在只有 X，还没有可训练的 y。先定义训练目标：

方向一：行情分类标签

给每个时点打标签：
	•	单边上涨
	•	单边下跌
	•	震荡
	•	breakout mode

这是状态模型。

方向二：交易机会标签

对每个候选信号打标签：
	•	好机会 / 坏机会
	•	A/B/C/D 级机会
	•	future_mfe_atr
	•	future_mae_atr

这是机会评分模型。

先做第二种更实用。因为你最终不是做“预测市场”，而是做“筛选交易”。

⸻

## 先做 baseline 规则策略

不要一上来直接上 ML。
先在 cta/strategy 里写几套纯规则 baseline：
	•	Donchian breakout
	•	ATR breakout
	•	tight range breakout
	•	breakout pullback continuation
	•	mean reversion range（震荡区间反向 setup，默认关闭）

目的不是赚钱，而是：
	•	生成候选交易样本
	•	知道哪些场景容易亏
	•	给后面的 ML 提供母策略

你要先回答：
不用模型时，系统最差亏在哪。

要求：
可以支持各个interval，参数支持数组，比如interval ['day','60min','30min','15min']  本地测试60min
生成特征和模型参数品种symbol支持加载cta/feature/symbols_research_ranking.csv文件中，topN的品种，N作为参数
	•	模型命令示例：`python3 -m cta.model.model_pipeline --top-n-symbols 10 --symbols-ranking-path cta/feature/symbols_research_ranking.csv --interval 60min ...`

⸻

## 构造训练样本表

在cta/model/feature 把“特征 + 信号 + 未来结果”落成一张标准表parquet格式存到cta/data/model_feature目录下面。
原始行情输入统一来自 `cta/data/origin/`（`day/minute/minute5/minute15/minute30/minute60`）。

建议一行代表一个候选交易时点（实际列见 `cta/strategy/breakout.md` § 16.3 与
`cta/model/feature/candidate_vs_executed_samples.md` § 4.5）：
	•	symbol / exchange / interval / datetime / side / signal_type
	•	trigger（突破/触发价；下游 `entry_price_virtual` 优先用 trigger 兜底，**不再用 stop_price**）
	•	feature_1 … feature_n
	•	candidate_status（filled / filtered / not_triggered）
	•	is_executed / is_filtered / filtered_reason
	•	future_mfe_atr / future_mae_atr / future_pnl_atr
	•	atr_warmed   ← 0/1 标志，0 表示 entry_bar 的 ATR 还在 warmup（前 ~14 根）
	•	label_class（仅 `is_executed==1 & atr_warmed==1` 时，按**止损跟踪后的真实执行收益**判正）
	•	regime_label

> **口径细节（2026-04-26 二修）**：
> 1. `future_mfe_atr / future_mae_atr` 用 **entry_bar** 的 `atr14` 做归一化；entry_bar 缺失时回退到 signal_bar，再失败用 high-low 兜底；
> 2. `atr_warmed=0` 的样本必须在训练前 drop（pipeline 已在 `run_model_pipeline` 内自动处理）；
> 3. `label_class` **只有在** `is_executed==1 & atr_warmed==1` 时才可判正，口径为“入场后逐 bar 跟踪止损的真实执行收益 > 0”；not_triggered/filtered/warmup 行的 label 必须保持 0。
> 4. `generate_candidate_opportunities(..., label_stop_loss_pct=...)` 可控制标签模拟止损线（默认 0.1%）。

这张表才是后面所有模型训练、分析、评分的核心资产。
其中训练样本的特征(包括两大类，第一类是候选机会相关的特征，第二类是cta/data/feature里面的通用特征)拼接起来。
一定要注意训练样本和特征都不能穿越。
训练样本包含满足条件未成交的样本(未成交样本的设置虚假成交价格以及卖出价格)，OOT成交样本分十档评估效果。

## 测试目录约定（更新）

- 策略相关测试放在：`cta/strategy/tests/`
- 模型相关测试放在：`cta/model/tests/`
- 特征构建相关测试放在：`cta/model/feature/tests/`

统一原则：测试文件与对应实现代码同层维护，不再集中放在 `cta/tests/`。

⸻

## 做样本切分和验证

绝对不要随机切分。
必须按时间切：
	•	train: 2018-2022
	•	valid: 2023
	•	test: 2024
	•	paper/live simulation: 2025+

或者 rolling walk-forward。`run_model_pipeline` 提供两种 walk-forward：

	•	`window_mode="expanding"`（默认）：train 起点固定为最早样本，每个窗口的 train_end 单调右移；
	•	`window_mode="sliding"`：train 长度恒定（首个窗口的 train 长度），每窗按 step=valid_end-train_end 整体滑动。
	•	`window_mode="rolling"`：固定 train/valid/test 年数窗口（默认 3y/1y/1y），按 `rolling_step_years` 年滚动。

CLI: `python3 -m cta.model.model_pipeline --window-mode sliding ...`

如果你现在这步做错，后面全白干。

⸻

## 先训练 3 类最关键模型

在cta/model里面写模型代码，按优先级：

模型1：Trade Filter

输入：候选信号 + 特征
输出：这笔值不值得做

模型可先用：
	•	LightGBM

这是最优先。

模型2：Regime Classifier

输入：多周期特征
输出：趋势 / 震荡 / 临界突破

这是策略路由器。

模型3：MFE / MAE 预测

输入：候选信号 + 特征
输出：未来最大有利/不利波动

这是仓位和机会大小模型。

> **三模型公共字段**：每个模型对象暴露 `model_kind` 字段（`uninitialized / dummy / hist_gradient_boosting / random_forest`），训练样本量不足时自动退化为 `dummy` 并 round-trip 写入 joblib。
> 评估表 `metrics.csv` 增加 `model_kind` 列，便于在多窗口/多 signal_type 下快速识别哪些位置训练样本不足。
> `MfeMaeModel` 通过 `min_samples` 字段（默认 10）控制 dummy 退化阈值。
> 每个模型训练完成后，打印top10重要特征，包括特征名称，特征含义，重要度
> top10 文件输出为 `*_top10_feature_importance.csv`，列包含：`feature / feature_meaning / importance`。

⸻

## 把模型接回 vn.py，不是直接替代策略

正确接法是：

错误接法

模型直接输出买卖点。

正确接法

规则策略先出候选信号，模型做：
	•	过滤
	•	评分
	•	调仓位
	•	决定是否跳过

即市场数据
-> 特征
-> baseline信号
-> 模型评分
-> 风控
-> 下单

## 建组合层，不要停留在单策略

你做完单个品种、单个策略后，下一步必须上组合：
	•	多品种
	•	多板块
	•	多策略
	•	风险预算
	•	波动率目标

重点控制：
	•	单品种风险
	•	同板块暴露
	•	周回撤
	•	月回撤

否则你做出来只是“单模型回测”，不是 CTA 系统。

⸻

## 做仿真，不要急着真钱

顺序一定是：
	1.	离线回测
	2.	滚动样本外
	3.	vn.py 仿真盘
	4.	小资金实盘
	5.	逐步放大

仿真阶段重点看：
	•	信号时点和下单时点偏差
	•	夜盘是否稳定
	•	持仓是否漂移
	•	滑点是否严重
	•	模型评分是否实时可用

⸻

## 建复盘体系

每笔交易都要记录：
	•	哪个策略
	•	哪个 setup
	•	当时 regime
	•	模型分
	•	入场原因
	•	止损原因
	•	实际 MFE / MAE
	•	是否本可不做

你后面真正的进步，不在模型代码，而在这套复盘。

⸻

## 你现在最合理的下一阶段顺序

我建议你按这个推进：

第一阶段
	•	1个品种：RB 或 M
	•	1个周期：60min
	•	1个 baseline：tight range breakout
	•	1个模型：XGBoost trade filter

第二阶段
	•	扩到 5~10 个品种
	•	增加 regime classifier
	•	做 score 分层仓位

第三阶段
	•	接 vn.py 仿真
	•	跑 1~2 个月连续仿真
	•	再上小资金
