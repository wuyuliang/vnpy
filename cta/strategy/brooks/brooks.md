# 构建适合中国 CTA 的 Brooks 策略：给 Claude 的逐步执行说明

## 目标
把 AI Brooks 的价格行为学，落地成一个**适合中国商品 CTA 的、可回测、可打分、可迭代**的研究与交易系统。核心不是复刻主观盯盘，而是把 Brooks 的语言拆成：**状态识别、结构标签、机会评分、执行风控**。这与原始思路一致：先做“市场状态识别 + 结构标签 + 机会评分 + 风控执行”，并优先从趋势突破、突破回踩延续、假突破过滤三个方向切入。 

---

## 你要扮演的角色
你现在不是在写一份泛泛而谈的技术文档，而是在做一个**可以直接落地编码的 CTA 研究项目**。你的输出必须满足下面要求：

1. 所有内容都面向**Python 可实现**。
2. 每一步都要明确：
   - 输入数据是什么
   - 要生成什么字段/特征/标签
   - 函数怎么拆
   - 文件怎么组织
   - 回测怎么验证
3. 不要写空话，不要只写概念。
4. 每一步都要输出让工程师“拿去就能继续写代码”的内容。
5. 默认研究对象是**中国商品期货**，优先考虑：螺纹、铁矿、铜、铝、豆粕、棕榈油、PTA、甲醇。
6. 默认周期从 **日线 / 60min / 30min** 开始，不做 tick 高频。
7. 默认先做**做多优先**版本，后续再扩展做空。
8. 必须避免未来函数，必须按时间序列切分训练/验证/测试。

---

## 总体路线
你要按下面 5 个阶段推进，每个阶段都要输出代码设计与文档：

### 阶段1：规则骨架
先把 Brooks 主观语言翻译成规则化 setup：
- tight range breakout
- breakout pullback continuation
- High2 in bull trend

### 阶段2：结构特征工程
把价格行为结构转成特征：
- 压缩程度
- 重叠程度
- 上涨腿斜率
- 回调深度
- 突破强度
- follow-through 强度
- 假突破风险

### 阶段3：标签与打分模型
不要只预测涨跌，要构建：
- MFE
- MAE
- opportunity class（A/B/C/D）
- 好机会二分类标签

### 阶段4：CTA 执行与风控
加入：
- ATR 止损
- 移动止盈
- 周回撤降仓
- 同板块风险预算
- 夜盘处理
- 换月处理

### 阶段5：组合与迭代
建立：
- 单策略评估
- 多品种组合评估
- 假突破率分析
- 参数稳健性分析
- 环境分类模型

---

# 第一部分：请你先输出项目目录设计
第一步，不要直接写业务逻辑代码。先输出一个清晰的项目目录，让后续开发有固定位置。

请输出如下风格的目录，并解释每个文件做什么：

```text
cta_brooks_china/
├── README.md
├── config/
│   ├── symbols.yaml
│   ├── backtest.yaml
│   └── model.yaml
├── data/
│   ├── raw/
│   ├── cleaned/
│   ├── features/
│   └── labels/
├── src/
│   ├── data/
│   │   ├── load_data.py
│   │   ├── clean_futures.py
│   │   └── continuous_contract.py
│   ├── indicators/
│   │   ├── atr.py
│   │   ├── ema.py
│   │   └── price_action_basic.py
│   ├── patterns/
│   │   ├── tight_range.py
│   │   ├── breakout.py
│   │   ├── pullback.py
│   │   └── high2.py
│   ├── features/
│   │   ├── structure_features.py
│   │   ├── breakout_features.py
│   │   └── context_features.py
│   ├── labels/
│   │   ├── mfe_mae.py
│   │   ├── opportunity_label.py
│   │   └── binary_label.py
│   ├── models/
│   │   ├── train_lgb.py
│   │   ├── train_xgb.py
│   │   └── predict_score.py
│   ├── strategy/
│   │   ├── baseline_breakout.py
│   │   ├── baseline_pullback.py
│   │   ├── baseline_high2.py
│   │   └── scored_strategy.py
│   ├── risk/
│   │   ├── position_sizing.py
│   │   ├── stop_rules.py
│   │   └── drawdown_control.py
│   ├── backtest/
│   │   ├── engine.py
│   │   ├── metrics.py
│   │   └── walk_forward.py
│   └── utils/
│       ├── logger.py
│       ├── io.py
│       └── calendar.py
├── notebooks/
├── reports/
└── tests/
```

要求：
- 不只列目录，还要解释“为什么这样拆分”。
- 明确哪些模块是第一版必须写，哪些可以第二版再写。
- 所有文件名都要贴近 Python 代码落地。

---

# 第二部分：请你先定义数据标准
第二步，请你写“数据层设计说明”。

你要告诉工程师：为了让 Brooks 结构识别能跑起来，最少需要哪些字段。

## 原始 K 线最少字段
至少包括：
- symbol
- exchange
- datetime
- open
- high
- low
- close
- volume
- open_interest

## 你要新增的派生字段
请你明确生成以下字段，并解释计算逻辑：
- prev_close
- tr
- atr_n
- ema_20
- ema_60
- body
- body_ratio
- upper_wick_ratio
- lower_wick_ratio
- close_pos
- range_size
- gap_pct
- ret_1
- ret_5
- ret_10
- volume_ratio
- overlap_prev_bar
- inside_bar_flag
- outside_bar_flag

要求：
1. 每个字段都要写出公式或伪代码。
2. 明确 rolling 窗口计算时如何避免未来数据泄露。
3. 明确期货数据清洗要处理什么：
   - 缺失 bar
   - volume 异常
   - 主力合约切换
   - 夜盘时间
   - 假期/非交易时段
4. 明确连续合约处理思路。

---

# 第三部分：请你先把 Brooks 概念翻译成程序化定义
第三步，是最核心的一步。

你要把 Brooks 的术语一个个翻译成**可计算规则**，每个术语都要输出：
- 定义说明
- Python 可计算条件
- 需要哪些中间字段
- 示例伪代码

## 重点先做以下结构

### 1. tight range
建议程序化定义方向：
- 最近 N 根 K 线高低区间较小
- 区间宽度 / ATR 小于阈值
- bar 与 bar 之间重叠度高
- 大阳大阴比例低

### 2. breakout
建议程序化定义方向：
- 当前收盘突破过去 N 根最高价
- close 接近 high
- body_ratio 足够大
- volume_ratio 放大

### 3. breakout pullback
建议程序化定义方向：
- 先发生有效突破
- 后续回调未深度跌回原区间内部
- 回踩突破位、EMA20 或者前高附近企稳
- 再次出现强势 bar

### 4. High1 / High2
建议程序化定义方向：
- bull trend 背景下
- 回调后第一次向上尝试为 High1
- 若失败再来一次为 High2
- High2 优先研究

### 5. bull trend
建议程序化定义方向：
- EMA20 > EMA60
- EMA20 slope > 0
- 最近 N 根高点/低点抬高
- 回调深度受控

### 6. failed breakout
建议程序化定义方向：
- 突破后 1~3 根 bar 内没有 follow-through
- 很快跌回突破区间
- 突破 bar 被反包或 close 弱

要求：
1. 每个结构都要给出**第一版保守定义**，不要追求太复杂。
2. 每个结构都要写出对应函数名建议，比如：
   - `detect_tight_range()`
   - `detect_breakout_bar()`
   - `detect_breakout_pullback()`
3. 解释这些定义为什么适合中国 CTA，而不是纯美股日内场景。

---

# 第四部分：请你定义“状态层”而不是只做形态识别
第四步，请你设计市场状态识别模块。

不要只做单个 setup，还要识别当前市场处于哪种状态。至少定义：
- trend_up
- trend_down
- trading_range
- breakout_mode
- climax_or_exhaustion
- high_false_breakout_risk

对于每个状态，请输出：
1. 定义逻辑
2. 可计算指标
3. 阈值建议
4. Python 伪代码
5. 状态切换逻辑

重点说明：
- Brooks 的核心是上下文，不是单根 K 线。
- 所以状态层必须先于 setup 层。
- 策略要在不同状态下采取不同开仓阈值和仓位。

你还要额外输出一个函数接口设计，例如：

```python
def classify_market_state(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Return columns: state, trend_score, range_score, false_breakout_risk"""
```

---

# 第五部分：请你设计三套 baseline 策略
第五步，请你分别给出三套基线策略的完整规则。

## Baseline A：tight range breakout 做多
你要明确：
- 入场条件
- 过滤条件
- 开仓价格
- 初始止损
- 移动止损
- 止盈/退出
- 不做条件

## Baseline B：breakout pullback continuation 做多
你要明确：
- 怎么判定先有突破
- 怎么判定回踩浅
- 怎么判定二次启动
- 怎么避免假突破后的追高

## Baseline C：High2 in bull trend 做多
你要明确：
- 趋势背景
- 回调定义
- High1 / High2 的程序化区分
- 入场与止损

要求：
1. 三个策略都要给出逐条规则，不要写成随意描述。
2. 给出 Python 类设计，例如：
   - `class TightRangeBreakoutStrategy`
   - `class BreakoutPullbackStrategy`
3. 每个策略都要说明“适合什么市场环境”“最容易死在哪”。
4. 每个策略都要有 `generate_signals(df)` 风格接口。

---

# 第六部分：请你设计价格行为特征工程
第六步，请你围绕“不是堆技术指标，而是提炼价格行为特征”来设计特征工程。

请按下面 5 类特征来组织：

## A. 结构压缩特征
例如：
- range_to_atr
- rolling_std_to_atr
- inside_bar_ratio_n
- overlap_ratio_n
- narrow_range_count_n

## B. 趋势背景特征
例如：
- ema20_slope
- ema60_slope
- distance_to_ema20
- distance_to_recent_high
- hh_hl_score

## C. 回调质量特征
例如：
- pullback_depth_atr
- pullback_bars
- pullback_overlap
- bearish_bar_ratio_in_pullback

## D. 突破质量特征
例如：
- breakout_body_ratio
- breakout_close_pos
- breakout_volume_ratio
- breakout_range_expansion
- breakout_vs_resistance_space

## E. 失败风险特征
例如：
- recent_failed_breakout_count
- upper_wick_cluster
- breakout_followthrough_1bar
- breakout_followthrough_2bar
- reversal_risk_score

要求：
1. 每个特征都解释业务意义。
2. 每个特征都写出计算思路。
3. 每类特征最后给一个 `build_xxx_features(df)` 的函数设计。
4. 明确哪些特征是 candidate 触发时刻生成，哪些可以全样本预计算。

---

# 第七部分：请你设计标签，不要只做涨跌预测
第七步，请你设计更适合 CTA 的标签体系。

请先输出一个原则：
**我们不直接把任务定义成“未来涨跌预测”，而是定义成“这次 setup 值不值得做”**。

请你设计以下标签：

## 1. MFE 标签
- 未来 H 根 bar 的最大有利波动
- 用 ATR 标准化
- 输出回归标签

## 2. MAE 标签
- 未来 H 根 bar 的最大不利波动
- 用 ATR 标准化
- 输出回归标签

## 3. 好机会二分类标签
例如：
- 若 MFE >= 2.0 ATR 且 MAE <= 1.0 ATR，则 label=1
- 否则 label=0

## 4. opportunity_class 多分类标签
例如：
- A：MFE 大且 MAE 小
- B：MFE 中等且 MAE 可接受
- C：MFE 小但不是大亏
- D：假突破/不值得做

要求：
1. 给出标签构造代码伪代码。
2. 解释窗口 H 如何选，比如 10、20、30 根 bar 的取舍。
3. 明确做日线与 60min 时标签窗口如何区分。
4. 说明为什么这比单纯预测 next bar return 更适合 CTA。

---

# 第八部分：请你设计模型训练方案
第八步，请你围绕“规则 + 评分模型”设计训练方案。

默认第一版优先：
- LightGBM
- XGBoost

请输出：

## 1. 样本生成方式
- 不是所有 bar 都做样本
- 先由 baseline 规则找 candidate setup
- 对 candidate 提取特征、打标签
- 形成训练样本表

## 2. 切分方式
必须是时间序列切分，例如：
- train: 2018-2022
- valid: 2023
- test: 2024-2025

禁止随机切分。

## 3. 目标任务
至少包括：
- binary classifier
- multiclass classifier
- MFE/MAE regressor

## 4. 评估指标
除了 AUC，还要评估：
- top decile hit rate
- 分层后平均 MFE
- 分层后平均 MAE
- 分层后收益/回撤
- 假突破率
- score bucket 的胜率与盈亏比

## 5. 特征重要性解释
输出：
- gain importance
- permutation importance
- SHAP（可第二版）

要求：
1. 给出训练数据表字段设计。
2. 给出 `train_model(train_df, valid_df, feature_cols, target_col)` 风格接口。
3. 给出如何把模型分接回策略引擎：
   - 只交易 top X% setup
   - 分数越高，允许仓位越高
4. 明确第一版不要上深度学习。

---

# 第九部分：请你设计 CTA 风控与执行模块
第九步，请你输出一个专门的“执行与风控设计说明”。

必须包含：

## 1. 仓位 sizing
例如：
- 单笔风险占权益 0.5% ~ 1%
- 基于 ATR 止损距离计算手数

## 2. 初始止损
例如：
- 突破策略放在 breakout bar low 下方
- 或 recent swing low 下方
- 加 buffer * ATR

## 3. 移动止损
例如：
- 2 ATR trailing
- 或跌破 EMA20 退出
- 或跌破前一 swing low 退出

## 4. 失败快速退出
例如：
- 突破后 1~3 根 bar 无 follow-through 则减仓/平仓
- close 跌回区间内部则退出

## 5. 组合层风险控制
例如：
- 黑色系总风险上限
- 化工总风险上限
- 单日新增风险上限
- 周回撤超过阈值自动降仓

## 6. 国内商品期货实务处理
必须补充：
- 夜盘信号与执行处理
- 涨跌停导致无法成交的情况
- 主力换月与连续合约回测偏差
- 手续费、滑点、跳空

要求：
1. 给出执行模块类设计。
2. 给出风控优先级。
3. 解释为什么 Brooks 最大价值之一是“错了要快走”。

---

# 第十部分：请你设计回测评估框架
第十步，请你输出回测框架设计，不要只看最终收益。

请至少输出以下指标：
- annual return
- max drawdown
- Calmar
- Sharpe
- win rate
- payoff ratio
- avg holding bars
- trade frequency
- long-only exposure
- false breakout loss ratio
- setup 类型分组表现
- 品种分组表现
- 年度表现
- rolling 3M / 6M 回撤

还要额外输出以下分析：

## 1. 假突破分析
统计：
- 哪类 breakout 最容易失败
- 哪些状态下假突破率高
- 假突破发生后损失多大

## 2. 分数分桶分析
例如：
- score 分 10 桶
- 每桶统计胜率、MFE、MAE、收益率
- 看模型分是否真的能筛出好机会

## 3. walk-forward 分析
例如：
- 滚动训练
- 滚动验证
- 滚动测试

## 4. 参数稳健性分析
例如：
- tight range 窗口 8/10/12/15
- breakout 阈值变化
- ATR 止损倍数变化

要求：
1. 写出 `BacktestResult` 数据结构建议。
2. 写出 `evaluate_strategy()`、`run_walk_forward()` 等函数接口。
3. 明确不能只展示一张资金曲线。

---

# 第十一部分：请你明确“Claude 每一步要产出什么”
第十一步，请你把整个任务拆成一个**可执行交付清单**。

你要明确告诉工程师，Claude 在每一步应该实际输出什么。

## 交付清单模板
请按这种格式输出：

### Step 1：项目骨架
输出：
- 项目目录树
- 每个模块职责说明
- 第一版/第二版开发优先级

### Step 2：数据标准与清洗规范
输出：
- 原始字段定义表
- 派生字段定义表
- 连续合约处理方案
- Python 数据清洗函数草图

### Step 3：Brooks 结构规则化定义
输出：
- tight range / breakout / pullback / High2 / failed breakout 的定义文档
- 对应函数签名
- 伪代码

### Step 4：三套 baseline 策略
输出：
- 三个策略的规则文档
- Python 类设计
- generate_signals 伪代码

### Step 5：特征工程
输出：
- 特征清单表
- 各特征计算逻辑
- build_features 模块设计

### Step 6：标签体系
输出：
- MFE/MAE 标签定义
- 二分类与多分类标签定义
- label 构造伪代码

### Step 7：模型训练
输出：
- 样本表结构
- 时间切分方案
- LightGBM/XGBoost 训练流程
- 模型评估方案

### Step 8：执行与风控
输出：
- 仓位与止损规则
- 失败退出规则
- 组合风控规则

### Step 9：回测与评估
输出：
- 回测引擎模块划分
- 指标计算逻辑
- walk-forward 方案
- 分桶分析方案

### Step 10：最终 README
输出：
- 一份完整 README.md
- 说明如何从 0 到 1 跑通 baseline
- 说明如何继续接模型分

要求：
- 这一部分要写得像“开发任务书”。
- 让 Claude 后续每一步都能顺着输出代码和文档。

---

# 第十二部分：请你输出代码编写原则
第十二步，请你强制约束 Claude 的代码风格，避免写出无法维护的研究脚本。

请明确要求：
1. Python 代码必须模块化。
2. 每个函数必须有 type hints。
3. 每个函数必须有 docstring。
4. DataFrame 列名要统一。
5. 不允许到处复制粘贴计算逻辑。
6. 规则阈值要集中放在 config 中。
7. 所有策略信号必须显式避免未来函数。
8. 回测引擎与特征工程要分离。
9. 训练集/验证集/测试集必须按时间切分。
10. 输出代码时优先给：
   - 可运行版本
   - 清晰注释
   - 小函数拆分
   - 配置化参数

你还要明确告诉 Claude：
- 第一版追求**正确、可解释、可跑通**。
- 不追求一开始就极致复杂。
- 不要上来就深度学习。
- 不要把 Brooks 简化成一两根 K 线模式。
- 一定要重视**状态层**与**失败退出**。

---

# 第十三部分：请你直接给出最终输出格式要求
最后，请你直接按下面格式输出最终结果，方便后续持续对 Claude 下指令。

## 最终输出格式要求
你最终输出的文档必须是 Markdown，结构如下：

```md
# 中国商品CTA的 Brooks 策略开发任务书

## 1. 项目目标
## 2. 系统整体架构
## 3. 项目目录设计
## 4. 数据标准与清洗规范
## 5. Brooks 结构规则化定义
## 6. 市场状态识别设计
## 7. 三套 baseline 策略定义
## 8. 特征工程设计
## 9. 标签体系设计
## 10. 模型训练方案
## 11. 执行与风控设计
## 12. 回测与评估框架
## 13. 分阶段开发计划
## 14. Claude 每一步交付清单
## 15. 代码编写规范
```

要求：
- 每个章节都要详细、工程化、能指导代码开发。
- 多用表格、清单、伪代码、函数签名。
- 少写空泛理论。
- 所有结论都要围绕中国商品 CTA 场景。

---

# 可直接复制给 Claude 的总指令
下面这段请你原样整理到文档最后，作为“给 Claude 的主提示词”：

```md
你现在是一名资深量化研究员 + Python CTA 系统架构师。你的任务是：围绕 AI Brooks 价格行为学，构建一套适合中国商品 CTA 的策略研究与开发任务书。目标不是写主观交易心得，而是把 Brooks 语言翻译成可程序化、可回测、可打分、可风控执行的系统。

请严格按以下要求输出：
1. 以中国商品期货为背景，优先研究螺纹、铁矿、铜、铝、豆粕、棕榈油、PTA、甲醇。
2. 周期先从日线、60min、30min 开始。
3. 第一版先做做多策略，重点研究：tight range breakout、breakout pullback continuation、High2 in bull trend。
4. 必须先做状态层，再做结构层，再做评分层，再做执行层。
5. 不允许把 Brooks 简化成单根K线形态；必须体现上下文。
6. 不允许直接端到端深度学习；第一版优先规则 + LightGBM/XGBoost。
7. 标签不要只做未来涨跌，要设计 MFE、MAE、opportunity class、好机会二分类。
8. 必须显式考虑假突破过滤、失败快速退出、ATR止损、移动止损、周回撤降仓、同板块风险上限、夜盘、换月、连续合约偏差。
9. 所有规则都要给出程序化定义、函数签名、伪代码、字段说明。
10. 所有开发内容都要以 Python 可落地为目标，目录结构、模块拆分、函数设计都要清晰。
11. 训练/验证/测试必须按时间切分，禁止随机切分。
12. 输出必须是 Markdown，章节包括：项目目标、系统架构、项目目录、数据标准、Brooks结构定义、市场状态识别、baseline策略、特征工程、标签体系、模型训练、执行与风控、回测评估、分阶段开发计划、逐步交付清单、代码规范。

请输出成一份可以直接指导工程师编码的完整任务书。
```

---

## 补充说明：本任务的设计依据
这份开发任务书遵循的核心依据是：不要把 Brooks 照搬成主观盯盘，而是拆成“状态层、结构层、评分层、执行层”；优先从 tight range breakout、breakout pullback、High2 三类 baseline 切入；模型上优先使用规则 + XGBoost/LightGBM；标签上优先使用 MFE、MAE 和机会分级；训练上必须做时间序列切分；执行上必须加入 ATR 止损、trailing stop、周回撤降仓、同板块风险上限、夜盘与换月处理。这些原则都来自你前面的策略方向说明。fileciteturn1file0 fileciteturn1file2 fileciteturn1file4
