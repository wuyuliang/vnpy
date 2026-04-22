# 构建适合中国 CTA 的 Brooks 多周期策略：vn.py 落地版 + Claude 执行版

## 文档目标
这份文档不是概念说明，而是**可以直接拿去驱动 Claude 写代码、并最终落到 vn.py 项目中运行**的实施蓝图。

本版相对上一版做了 6 个强化：

1. **改成更适合 vn.py 的项目结构与运行方式**。
2. **增加更适合直接喂给 Claude / Claude Code 的执行指令模板**。
3. **支持多周期共振**：大级别顺势、中级别反趋势回调、小级别顺大级别方向突破入场。
4. **明确分钟级别交易、风险预算、仓位公式、交易落盘日志**。
5. **明确“目前已有特征”，所以模型部分也要直接实现，不只停留在设计**。
6. **明确研发路径：先用品种 RB 在本地跑通，再迁移到服务器批量跑其他品种**。

---

# 一、策略总思路：把 Brooks 翻译成中国 CTA 的可编程系统

Brooks 价格行为学最适合中国 CTA 的，不是纯主观盯盘，而是拆成以下四层：

1. **状态层（Market Regime）**：趋势、震荡、突破临界、加速、衰竭。
2. **结构层（Structure）**：tight range、breakout、breakout pullback、High2、失败突破等。
3. **评分层（Scoring / ML）**：对候选机会打分，而不是机械见形态就交易。
4. **执行层（Execution / Risk）**：分钟级别入场，严格止损，固定账户风险，交易记录落文件。

这份文档采用的核心逻辑是：

> **大级别定方向，中级别找反趋势回调，小级别等顺大级别方向突破再交易。**

初始建议配置：
- **大级别**：日线 + 60分钟
- **中级别**：15分钟或5分钟（做反趋势回调识别）
- **小级别**：1分钟或 5分钟（先用你现有分钟级别数据测试）
- 这些级别必须做成**可配置**，不要写死。

---

# 二、最适合 vn.py 的落地方式

## 2.1 目录设计原则
你当前已经有 vn.py 目录，并且只能修改 `cta/` 目录内容，因此建议按下面方式组织：

```text
vnpy/
└── cta/
    ├── README.md
    ├── AGENTS.md
    ├── config/
    │   ├── strategy.yaml
    │   ├── symbols.yaml
    │   ├── model.yaml
    │   └── paths.yaml
    ├── data/
    │   ├── day/
    │   ├── minute/
    │   │   ├── RB.SHFE/
    │   │   ├── I.DCE/
    │   │   └── ...
    │   ├── processed/
    │   ├── features/
    │   ├── labels/
    │   ├── trades/
    │   └── models/
    ├── research/
    │   ├── build_dataset.py
    │   ├── build_features.py
    │   ├── build_labels.py
    │   ├── train_model.py
    │   ├── predict_scores.py
    │   └── analyze_trades.py
    ├── strategies/
    │   ├── brooks_base.py
    │   ├── brooks_mtf_breakout.py
    │   ├── signals.py
    │   ├── regime.py
    │   ├── patterns.py
    │   ├── risk_manager.py
    │   ├── position_sizer.py
    │   └── trade_logger.py
    ├── backtest/
    │   ├── run_backtest_rb.py
    │   ├── run_backtest_batch.py
    │   ├── metrics.py
    │   ├── walk_forward.py
    │   └── plot_results.py
    ├── utils/
    │   ├── io_utils.py
    │   ├── time_utils.py
    │   ├── contract_utils.py
    │   ├── bar_utils.py
    │   └── config_loader.py
    ├── notebooks/
    └── tests/
        ├── test_features.py
        ├── test_labels.py
        ├── test_signals.py
        └── test_position.py
```

## 2.2 为什么这样拆
### `strategies/`
放策略实时逻辑，要求可直接被 vn.py 回测或实盘调用。

### `research/`
放离线研究脚本。因为**特征生成、标签构造、模型训练**不应该和实盘策略代码耦合在一起。

### `backtest/`
放单品种回测、多品种批跑、walk-forward、指标统计。

### `data/trades/`
专门存交易记录，后续做模型打分、复盘分析、真假突破统计都依赖这里。

### `data/models/`
存训练好的模型、特征列名、标准化器、阈值配置。

---

# 三、核心策略逻辑：多周期共振版 Brooks

## 3.1 三层周期框架

### A. 大级别：方向过滤
目标：判断当前只做多、只做空、还是不做。

建议组合：
- 日线
- 60分钟

做多条件示例：
- 日线 `EMA20 > EMA60`
- 日线 `EMA20 slope > 0`
- 60分钟 `EMA20 > EMA60`
- 60分钟最近 `N` 根高点抬高、低点抬高
- 当前价格位于 60分钟 EMA20 上方或附近

做空反之。

输出字段：
- `htf_bias`: `1` 多头，`-1` 空头，`0` 中性
- `htf_strength`: 趋势强度分数，0~1

### B. 中级别：反趋势回调识别
目标：在大级别趋势中，等待一个“逆着大方向的回调”。

例如：
- 大级别多头时，中级别（默认 5 分钟）出现一段向下回调
- 回调深度不能过深
- 回调过程尽量是缩量、实体变小、重叠增加
- 回调后在 EMA20/EMA60、前高突破位、VWAP 附近企稳

输出字段：
- `mtf_pullback_flag`
- `pullback_depth_atr`
- `pullback_bars`
- `pullback_quality_score`

### C. 小级别：顺大级别方向突破入场
目标：不在回调时抄底，而是在小级别重新出现顺大级别方向突破时入场。

例如大级别多头：
- 小级别先形成 tight range / 小平台
- 再向上突破近 N 根高点
- 突破 bar 实体较大
- close 靠近 high
- volume_ratio 放大

输出字段：
- `ltf_breakout_flag`
- `breakout_strength_score`
- `entry_signal`

---

# 四、周期必须可配置

不要把“日线/60分钟/5分钟/1分钟”写死。必须在配置文件里写成：

```yaml
# config/strategy.yaml
strategy_name: brooks_mtf_breakout
symbol: RB.SHFE
capital: 10000000
risk_per_trade: 0.001   # 单笔最大亏损占总资金 0.1%
commission_rate: 0.00003
slippage_ticks: 1
contract_size: 10
price_tick: 1

timeframes:
  higher:
    - 1d
    - 60m
  middle: 5m
  lower: 1m

signal_params:
  ema_fast: 20
  ema_slow: 60
  atr_window: 20
  tight_range_window: 8
  breakout_lookback: 10
  close_pos_threshold: 0.75
  body_ratio_threshold: 0.55
  volume_ratio_threshold: 1.2
  pullback_max_depth_atr: 1.8
  pullback_min_bars: 3
  pullback_max_bars: 20

model_params:
  enable_model_filter: true
  score_threshold: 0.62
  model_path: data/models/rb_xgb_model.pkl
  feature_list_path: data/models/rb_feature_cols.json

logging:
  trade_log_path: data/trades/rb_trades.csv
  signal_log_path: data/trades/rb_signals.csv
```

这样 Claude 写代码时，必须实现：
- 读取 yaml
- 允许更换大、中、小周期
- 允许更换参数
- 回测和实盘用同一套配置结构

---

# 五、分钟级别交易规则

## 5.1 交易发生在分钟级别
即使大级别用日线和 60m 判断方向，**真正下单仍发生在分钟级别**。

建议第一版：
- 大级别：日线 + 60m
- 中级别：5m
- 小级别：1m
- 如果 1m 数据质量不稳定，可先用 5m 代替小级别跑通第一版

## 5.2 单笔风险预算
账户资金：
- `capital = 10,000,000`

单笔最多回撤：
- `0.1%`
- 即：`risk_amount = capital * 0.001 = 10,000`

也就是说，任何一笔交易**碰到初始止损线时，理论最大亏损不得超过 1 万元**（未计极端跳空）。

## 5.3 仓位计算公式
对于期货，建议仓位按下面公式：

```python
risk_amount = capital * risk_per_trade
stop_distance = abs(entry_price - stop_price)
contract_risk = stop_distance * contract_size
lots = floor(risk_amount / contract_risk)
```

考虑滑点和手续费，可改成：

```python
effective_stop_distance = abs(entry_price - stop_price) + slippage_ticks * price_tick
contract_risk = effective_stop_distance * contract_size
lots = floor(risk_amount / contract_risk)
```

若 `lots < 1`：
- 保守模式：放弃交易
- 或者允许最小 1 手，但要记录该笔风险超预算

第一版建议：
- **若不足 1 手，直接不交易**，保持严格风险一致性

## 5.4 止损设置
先做最稳妥的版本：
- 多头：`stop_price = min(pullback_low, entry_bar_low - k * ATR)`
- 空头反之
- 推荐 `k = 0.2 ~ 0.5`

第一版建议：
- 多头初始止损：`min(最近回调低点, entry_price - 1.2 * ATR_5m)`
- 空头相反

## 5.5 退出规则
必须先实现 3 种：
1. **初始止损**
2. **移动止损**：例如价格创新高后把止损抬到前一 swing low 或 EMA20 下方
3. **时间止损 / 失败退出**：突破后 `M` 根 bar 没有 follow-through，平仓

第一版参数建议：
- `time_stop_bars = 20`（小级别 bar）
- `breakeven_after_r = 1.0`
- `trail_after_r = 1.5`

---

# 六、交易日志必须落文件

这是本次要求中的重点，必须在代码中实现。

## 6.1 为什么必须落文件
后续你要对交易做模型打分、真假突破分析、A/B/C/D 机会分类，所以**每一笔交易都必须形成结构化记录**。

## 6.2 交易日志字段
建议写到：
- `data/trades/rb_trades.csv`
- 或者每日一个 parquet/csv

最少字段：

```text
trade_id
symbol
direction
entry_datetime
exit_datetime
entry_price
exit_price
stop_price_init
stop_price_final
position_size
contract_size
risk_amount
pnl
pnl_ratio
holding_bars
holding_minutes
entry_reason
exit_reason
htf_bias
htf_strength
pullback_quality_score
breakout_strength_score
model_score
mfe
mae
atr_at_entry
ema_fast_at_entry
ema_slow_at_entry
volume_ratio_at_entry
close_pos_at_entry
body_ratio_at_entry
```

## 6.3 信号日志字段
除了成交日志，还建议单独记录所有候选信号：
- 有些信号未成交
- 有些信号被模型过滤掉
- 有些因为仓位不足而放弃

文件：`data/trades/rb_signals.csv`

字段：

```text
signal_id
symbol
datetime
htf_bias
pullback_flag
breakout_flag
raw_signal
model_score
passed_model_filter
entry_allowed
reject_reason
candidate_entry_price
candidate_stop_price
candidate_lots
```

这样以后才能分析：
- 模型过滤是否有效
- 哪类 setup 最容易假突破
- 风险预算是否太紧

---

# 七、因为“现在已有特征”，所以模型部分必须直接实现

不是只设计模型，而是要让 Claude 直接写出：
- 数据集拼接逻辑
- 训练代码
- 预测代码
- 回测中如何调用模型分

## 7.1 模型任务设计
建议先做两层：

### 任务1：机会二分类
目标：判断一个候选突破 setup 是否值得做。

标签示例：
- 如果未来 `T` 根 bar 内，`MFE >= 2R` 且 `MAE <= 1R`，则 `label_good_trade = 1`
- 否则为 `0`

### 任务2：机会评分回归 / 排序
目标：输出一个连续分数，用于过滤。

可用：
- `future_mfe_r`
- `future_net_expectancy`
- 或者 `rank_score`

第一版建议：
- **先用 XGBoost 二分类 + 预测概率作为 model_score**

## 7.2 模型输入
既然你已经有特征，Claude 写代码时必须支持：
- 从现有特征表读取
- 自动补齐策略事件特征
- 对齐 entry datetime

特征来源建议拆成三类：
1. 现有已有特征
2. 结构事件特征
3. 风控空间特征

### 额外必须补的结构特征
即使已有特征，也建议补上：
- `tight_range_width_atr`
- `tight_range_overlap_mean`
- `breakout_body_ratio`
- `breakout_close_pos`
- `breakout_volume_ratio`
- `pullback_depth_atr`
- `pullback_bars`
- `distance_to_htf_ema`
- `distance_to_prev_high`
- `risk_reward_to_nearest_target`
- `htf_strength`
- `ltf_acceleration_score`

## 7.3 标签构造
建议统一用事件驱动标签：
- 不是每根 bar 一条样本
- 而是**每个候选 signal 一条样本**

样本主键：
- `symbol`
- `entry_datetime`
- `direction`

标签字段：
- `future_mfe`
- `future_mae`
- `future_mfe_r`
- `future_mae_r`
- `label_good_trade`
- `opportunity_class`

机会等级示例：
- `A`: `mfe_r >= 3 and mae_r <= 1`
- `B`: `mfe_r >= 2 and mae_r <= 1.2`
- `C`: `mfe_r >= 1`
- `D`: 其他

## 7.4 训练切分
必须时间切分，不能随机。

RB 第一版建议：
- Train: 2017-01-01 ~ 2022-12-31
- Valid: 2023-01-01 ~ 2023-12-31
- Test: 2024-01-01 ~ 2025-12-31

如果分钟数据不足，就按你现有分钟数据范围顺延。

## 7.5 模型在策略中的调用方式
流程：
1. 规则先找候选 setup
2. 生成当前时点特征向量
3. 加载模型 `predict_proba`
4. 得到 `model_score`
5. 若 `model_score >= threshold` 才允许开仓

这一步要明确要求 Claude 写成：
- `ModelScorer` 类
- 支持加载 `pkl/json`
- 支持缺失值保护
- 支持特征列顺序对齐

---

# 八、RB 本地先跑通，再扩到服务器

这是执行路线，不是建议，是必须遵循的开发顺序。

## 8.1 本地第一阶段：只跑 RB
目标：先把整条链路打通。

只做：
- 品种：`RB`
- 周期：日线 + 60m + 5m + 1m（如果 1m 数据暂时不好，就先用 5m 兼任小级别）
- 范围：先取 1~2 年数据跑通

本地必须完成：
1. 数据读取
2. 多周期对齐
3. 候选 setup 检测
4. 仓位计算
5. 回测交易撮合
6. 交易日志落盘
7. 标签构造
8. 模型训练
9. 模型过滤回测

## 8.2 服务器第二阶段：扩到多品种
本地 RB 跑通后，再迁移到服务器上跑：
- I
- HC
- CU
- AL
- M
- P
- TA
- MA

服务器阶段新增：
- 批量任务
- 多品种汇总报表
- walk-forward
- 多参数组合
- 模型版本管理

## 8.3 为什么必须先 RB
因为 RB：
- 流动性较好
- 趋势与震荡切换明显
- 中国 CTA 中足够典型
- 便于你先验证 Brooks 结构的可编程性

---

# 九、Claude 应该如何一步一步写代码

下面这部分就是**更适合喂给 Claude 的版本**。你可以把下面内容单独复制给 Claude，让它严格按顺序产出代码。

---

# 给 Claude 的执行指令（可直接复制）

你现在要在 `vnpy/cta/` 目录内，构建一个适合中国商品 CTA 的 Brooks 多周期策略研究与回测项目。

你的目标不是泛泛解释，而是**输出可以直接运行和继续开发的 Python 代码**。

## 总目标
实现一个多周期策略：
- 大级别：判断方向（上升 / 下降 / 中性）
- 中级别：识别逆大级别方向的回调
- 小级别：等待重新顺大级别方向的突破后入场
- 交易发生在分钟级别
- 单笔风险 = 总资金的 0.1%
- 总资金 = 1000 万
- 每笔交易必须记录到文件
- 目前已经有一部分特征，模型训练与打分也必须实现
- 先用品种 RB 在本地跑通，再迁移到服务器跑其他品种

## 强制要求
1. 只能修改 `cta/` 目录内容。
2. 代码必须按模块拆分，不能把所有逻辑写在一个文件里。
3. 必须避免未来函数。
4. 必须支持配置化周期，不允许把日线/60m/5m/1m 写死。
5. 第一版先保证可跑通，再逐步优化，不要一开始过度复杂化。
6. 所有输出代码都要可直接保存成文件。
7. 每个步骤都先给出文件清单，再给出完整代码。
8. 写代码时默认 Python 3.10+。
9. 若依赖第三方库，必须显式说明。
10. 优先使用 pandas / numpy / xgboost / lightgbm / pyyaml。

## 第一步：先输出项目目录
请先输出 `cta/` 下的完整项目目录，并说明每个文件职责，包括：
- config/
- strategies/
- research/
- backtest/
- utils/
- tests/
- data/ 子目录用途

## 第二步：实现配置加载
请先写以下文件：
- `config/strategy.yaml`
- `utils/config_loader.py`

要求：
- 支持资金、单笔风险、周期配置、品种、手续费、滑点、模型路径、日志路径
- 写出 dataclass 或结构化配置读取代码

## 第三步：实现数据读取与多周期对齐
请写以下文件：
- `utils/io_utils.py`
- `utils/time_utils.py`
- `research/build_dataset.py`

要求：
- 支持读取 day 和 minute 数据
- 支持 RB 本地测试
- 支持把日线、60m、5m、1m 对齐到小级别时间轴
- 对齐时只能使用当前时刻之前已经完成的 higher timeframe bar
- 不能发生未来泄露
- 输出一个用于研究与回测的统一 dataframe

## 第四步：实现基础特征和结构识别
请写以下文件：
- `strategies/regime.py`
- `strategies/patterns.py`
- `strategies/signals.py`

要求：
- 实现 EMA, ATR, body_ratio, close_pos, volume_ratio
- 实现大级别趋势判断 `htf_bias`
- 实现中级别回调识别
- 实现小级别 tight range breakout
- 所有函数都返回 dataframe 新列，不要写成只适合单次调用的硬编码逻辑

## 第五步：实现仓位与风控
请写以下文件：
- `strategies/position_sizer.py`
- `strategies/risk_manager.py`

要求：
- 单笔风险 = 1000万 * 0.1%
- 按 entry_price, stop_price, contract_size 计算手数
- 若不足 1 手，则放弃交易
- 实现初始止损、保本、移动止损、时间止损

## 第六步：实现交易日志
请写以下文件：
- `strategies/trade_logger.py`

要求：
- 每笔交易必须落到 csv
- 记录 entry/exit 时间、价格、方向、手数、PnL、模型分、结构分
- 另外单独记录所有候选信号到 signal csv

## 第七步：实现 RB 单品种回测
请写以下文件：
- `strategies/brooks_base.py`
- `strategies/brooks_mtf_breakout.py`
- `backtest/run_backtest_rb.py`
- `backtest/metrics.py`

要求：
- 先实现离线回测版本，不强依赖 vn.py GUI
- 但代码风格要便于后续迁移到 vn.py 策略类
- 支持本地直接运行：`python backtest/run_backtest_rb.py`
- 输出收益指标、胜率、盈亏比、最大回撤、交易数、平均持仓时间

## 第八步：实现标签构造与模型训练
请写以下文件：
- `research/build_labels.py`
- `research/build_features.py`
- `research/train_model.py`
- `research/predict_scores.py`

要求：
- 样本单位是候选 signal，而不是每根 bar
- 构造 future MFE / MAE / MFE_R / MAE_R
- 生成二分类标签 `label_good_trade`
- 训练 XGBoost 分类模型
- 输出 model_score 概率
- 保存模型、特征列名、阈值文件

## 第九步：实现“规则 + 模型”融合回测
请修改或新增：
- `strategies/brooks_mtf_breakout.py`
- `backtest/run_backtest_rb.py`

要求：
- 先跑纯规则版本
- 再跑加模型过滤版本
- 对比两者：交易数、胜率、盈亏比、回撤、假突破率

## 第十步：实现批量多品种回测
请写以下文件：
- `backtest/run_backtest_batch.py`
- `backtest/walk_forward.py`

要求：
- 在 RB 跑通后，扩展到多个品种
- 输出每个品种结果 + 汇总表
- 支持 walk-forward

## 编码风格要求
1. 所有关键函数都写 docstring。
2. 所有文件给出完整代码，不要只给片段。
3. 所有路径使用相对路径或可配置路径。
4. 所有 dataframe 列名统一英文。
5. 所有策略参数统一从 yaml 读取。
6. 先保证无 bug、可运行，再做性能优化。

## 你输出时的格式要求
每一步都按以下格式输出：
1. 本步要新增/修改的文件列表
2. 每个文件的完整代码
3. 如何运行
4. 预期输出文件是什么
5. 下一步依赖什么

---

# 十、程序化定义：Brooks 术语如何落地

## 10.1 大级别趋势 `bull trend / bear trend`
多头定义示例：

```python
bull_trend = (
    (ema_fast > ema_slow)
    & (ema_fast.diff(lookback_slope) > 0)
    & (rolling_high_n.diff() > 0)
    & (rolling_low_n.diff() > 0)
)
```

空头反之。

## 10.2 tight range
建议定义：
- 最近 `N` 根 bar 区间宽度较小
- `range_width / ATR < threshold`
- bar 之间重叠度高
- 大实体 bar 比例低

可计算字段：
- `tight_range_width_atr`
- `tight_range_overlap_mean`
- `tight_range_big_body_ratio`

## 10.3 breakout
建议定义：
- 当前收盘突破过去 `N` 根最高价（或最低价）
- `close_pos > threshold`
- `body_ratio > threshold`
- `volume_ratio > threshold`

## 10.4 pullback
在大级别多头时：
- 中级别最近出现若干根向下 bar
- 回调深度 `<= max_depth_atr`
- 回调结束时重叠增加、波动收缩

## 10.5 小级别顺势突破入场
多头示例：
- `htf_bias == 1`
- `mtf_pullback_flag == 1`
- `tight_range == 1`
- `close > rolling_high_n.shift(1)`
- `body_ratio >= threshold`
- `close_pos >= threshold`

---

# 十一、数据与时间对齐要求

## 11.1 数据最少字段
所有周期最少字段：
- `symbol`
- `datetime`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `open_interest`

## 11.2 基础派生字段
必须生成：
- `prev_close`
- `tr`
- `atr_n`
- `ema_20`
- `ema_60`
- `body`
- `body_ratio`
- `upper_wick_ratio`
- `lower_wick_ratio`
- `close_pos`
- `range_size`
- `gap_pct`
- `ret_1`
- `ret_5`
- `ret_10`
- `volume_ratio`
- `overlap_prev_bar`
- `inside_bar_flag`
- `outside_bar_flag`

## 11.3 对齐原则
当小级别 bar 时间是 `t` 时：
- 日线只能使用 `<= t` 且已收完的日线 bar
- 60m 只能使用 `<= t` 且已收完的 60m bar
- 5m 同理

绝不能把尚未结束的 higher timeframe bar 信息提前喂给小级别。

---

# 十二、第一版必须实现的文件清单

## 必须优先写
- `config/strategy.yaml`
- `utils/config_loader.py`
- `utils/io_utils.py`
- `research/build_dataset.py`
- `strategies/regime.py`
- `strategies/patterns.py`
- `strategies/signals.py`
- `strategies/position_sizer.py`
- `strategies/risk_manager.py`
- `strategies/trade_logger.py`
- `strategies/brooks_mtf_breakout.py`
- `backtest/run_backtest_rb.py`
- `backtest/metrics.py`
- `research/build_labels.py`
- `research/train_model.py`

## 第二版再补
- `backtest/run_backtest_batch.py`
- `backtest/walk_forward.py`
- `research/predict_scores.py`
- `tests/`
- 多品种批量模型
- 模型版本管理

---

# 十三、研发顺序建议

## 阶段A：本地最小闭环
1. RB 数据读取
2. 多周期对齐
3. 规则信号
4. 仓位与止损
5. 离线回测
6. 交易日志

## 阶段B：研究闭环
7. 候选信号样本集
8. 标签构造
9. 模型训练
10. 模型打分
11. 规则+模型回测对比

## 阶段C：服务器扩展
12. 多品种批跑
13. walk-forward
14. 参数稳健性
15. 组合层控制

---

# 十四、给你自己的执行建议

你现在最应该做的不是继续抽象讨论，而是：

1. 先把这份文档喂给 Claude。
2. 让 Claude 严格按“第 1 步到第 10 步”逐文件输出代码。
3. 先只跑 RB，本地验证：
   - 能不能生成交易
   - 交易日志是否完整
   - 单笔风险是否真控制在 0.1%
   - 模型能否成功训练并参与过滤
4. RB 跑通后，再迁移到服务器批量跑其他品种。

---

# 十五、一句话总结

这套方案的核心不是“让模型像人一样看盘”，而是把 Brooks 的价格行为翻译成：

> **大级别定方向，中级别找回调，小级别等突破，分钟级别执行，严格按 0.1% 风险做仓位，所有交易落盘，再用模型去筛掉低质量突破。**

这才是最适合中国 CTA、也最适合你当前 vn.py 研发环境的落地路线。
