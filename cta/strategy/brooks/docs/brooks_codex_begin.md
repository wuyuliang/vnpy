你是一个具有丰富中国商品期货 CTA、量化交易、vn.py 开发经验，同时深入理解 Al Brooks Price Action 的资深量化研究员和工程师。

## 任务目标

参考 Al Brooks Trading Course 中公开资料：

https://www.brookstradingcourse.com/wp-content/uploads/

重点研究其中与以下内容相关的 PDF：

- Market Cycle
- Trends
- Breakouts
- Channels
- Trading Ranges
- Pullbacks
- Scalping
- Swing Trading
- Entry / Exit
- Risk Management
- Always In Direction

不要简单翻译或复制 Brooks 原文，而是提炼其交易思想，将主观 Price Action 方法转化为可以程序化、可回测、无未来函数、适用于中国期货市场的 CTA 量化规则。

最终设计文档保存到：

vnpy/cta/strategy/brooks/brooks.md

后续该文档将作为 Brooks CTA 策略代码实现的设计规范。

---

# 一、核心设计目标

构建一个：

大周期判断市场环境 → 中周期判断 Market Cycle → 小周期寻找交易 Setup → Entry → Position Sizing → Exit → Risk Management

的多周期 CTA 系统。

重点解决：

1. 当前市场属于什么 Market Cycle？
2. 当前应该做趋势交易、回调交易、突破交易，还是 scalp？
3. 当前应该做多、做空还是不交易？
4. 应该在哪个价格入场？
5. 止损放在哪里？
6. 仓位应该多大？
7. 盈利后应该如何持有、加仓、减仓或退出？
8. 如何避免震荡市场频繁止损？
9. 如何避免未来数据泄露和回测过拟合？

---

# 二、多周期 Market Cycle

至少设计三个级别：

- 大级别 Market Cycle：决定市场 regime 和主要方向
- 中级别 Market Cycle：决定当前交易方式
- 小级别 Market Cycle：决定具体 Entry / Exit

例如可以考虑：

- Daily：大周期
- 60min：中周期
- 5min：小周期

但不要硬编码固定周期。

需要设计成可配置形式，例如：

python large_tf medium_tf small_tf 

并说明不同中国期货品种是否需要不同周期参数。

---

# 三、Market Cycle 分类

需要把 Brooks 的 Market Cycle 尽可能量化为以下状态：

text STRONG_BULL_BREAKOUT BULL_TIGHT_CHANNEL BULL_BROAD_CHANNEL  TRADING_RANGE  BEAR_BROAD_CHANNEL BEAR_TIGHT_CHANNEL STRONG_BEAR_BREAKOUT 

至少重点实现以下四类：

## 1. 上涨突破 Bull Breakout

特征：

- 强趋势上涨
- 连续强阳线或明显突破
- 回调极少
- K线大部分收在高位
- EMA保持明显上升
- 多头控制市场
- 价格持续远离均衡区域

对应交易思想：

> 强突破阶段不能一直等完美回调，否则容易踏空。

研究如何量化 Brooks 所说的：

“Any pullback is likely to be bought”

并定义：

- 什么时候允许追涨
- 什么时候等待第一次回调
- 什么情况下认为突破失败

---

## 2. Bull Tight Channel

特征：

- 整体上涨
- 回调很浅
- 通常只有1～2根K线
- EMA斜率明显向上
- Higher High + Higher Low结构稳定
- 空头反转成功率低

交易方式：

- 主要做多
- 小回调买入
- 不轻易做空
- 可以 Swing，也可以趋势内 Scalp

需要量化：

- channel slope
- pullback bars
- pullback depth / ATR
- EMA distance
- HH/HL结构
- trend bar ratio
- overlap ratio

---

## 3. Bull Broad Channel

特征：

- 长期整体向上
- 但回调明显
- 回调持续时间更长
- 经常跌破EMA
- 可能出现 Lower High / Lower Low 的局部结构
- 但更大周期仍然向上

交易方式：

> Buy Low in Bull Channel

重点寻找：

- H1 / H2
- Wedge Bull Flag
- Two-legged Pullback
- EMA Pullback
- Previous Swing Low
- Failed Bear Breakout

必须设计如何识别：

text 第一段回调 反弹 第二段回调 H2触发 

特别注意：

H1/H2必须使用实时可确认结构，不能使用未来K线确认当前信号。

---

## 4. Trading Range

特征：

- 多空力量接近平衡
- EMA走平
- K线高度重叠
- Breakout经常失败
- 趋势信号胜率下降
- Mean Reversion特征增强

Brooks核心思想：

> Buy Low, Sell High, Scalp.

量化设计：

text 底部区域 → 做多 中间区域 → 不交易 顶部区域 → 做空/平多 

需要定义：

- range_high
- range_low
- equilibrium / midpoint
- range percentile
- failed breakout
- overlap ratio
- EMA slope
- ADX
- ATR compression

特别避免：

Trading Range中间区域频繁交易。

---

# 四、趋势方向识别

不能只使用：

python close > EMA20 

需要综合：

- EMA斜率
- Price vs EMA
- HH / HL / LH / LL
- breakout
- trend bar数量
- strong close
- overlap
- pullback depth
- ATR
- ADX
- channel slope
- recent swing structure

最终输出类似：

python trend_direction = {     "direction": "bull",     "strength": 0.82,     "cycle": "BULL_TIGHT_CHANNEL",     "confidence": 0.76, } 

其中：

text direction: bull / bear / neutral  strength: 0~1  confidence: 0~1 

---

# 五、Always-In方向

设计 Brooks 的 Always In Long / Always In Short 概念。

例如：

text ALWAYS_IN_LONG NEUTRAL ALWAYS_IN_SHORT 

研究：

- 什么条件从 Neutral → Always In Long
- 什么条件从 Long → Neutral
- 什么条件从 Long → Short

不要因为单根K线直接反转方向。

最好设计：

text state machine 

避免市场状态频繁跳变。

---

# 六、Setup体系

不要只设计一个 Pullback Signal。

设计一个可扩展 Setup Engine。

例如：

python SetupType:     BREAKOUT     FIRST_PULLBACK     H1     H2     WEDGE_PULLBACK     EMA_PULLBACK     FAILED_BREAKOUT     RANGE_REVERSAL     TREND_RESUMPTION 

每个 Setup 必须说明：

### Market Context

什么 Market Cycle 下允许？

### Direction

Long还是Short？

### Trigger

什么时候触发？

### Entry Price

具体如何下单？

### Stop

止损在哪里？

### Target

目标在哪里？

### Expected Holding Period

Scalp还是Swing？

---

# 七、Scalp vs Swing

这是本系统的重要部分。

需要根据 Market Cycle 动态决定：

text SCALP SWING NO_TRADE 

例如：

### Strong Breakout

倾向：

text SWING 

允许让利润奔跑。

### Tight Channel

倾向：

text SWING + Pullback Entry 

### Broad Channel

倾向：

text Swing Entry 但减少追涨 

### Trading Range

倾向：

text SCALP 

目标缩短。

明确量化：

python trade_mode = classify_trade_mode(...) 

---

# 八、Entry设计

Entry不要只考虑 Market Order。

至少讨论：

text Stop Entry Limit Entry Market Entry 

例如：

H2：

text Buy Stop = signal_bar_high + tick 

Trading Range Low：

可以考虑：

text Limit Buy 

Strong Breakout：

可以考虑：

text Market / Stop Entry 

并考虑中国期货：

- 最小跳动价位
- 涨跌停
- 夜盘
- 滑点
- 手续费
- 流动性

---

# 九、Stop Loss

不要固定使用：

text 2 ATR 

应该结合 Price Action Stop：

例如：

- signal bar low
- swing low
- pullback low
- opposite breakout point
- ATR fallback stop

可以设计：

python stop_price = max(     structural_stop,     volatility_stop ) 

但注意 Long / Short 方向对应关系。

说明什么时候采用：

- Tight Stop
- Wide Stop

特别是：

text Broad Channel 

通常需要允许更宽止损并降低仓位。

---

# 十、Position Sizing

仓位不能由 Signal Confidence 单独决定。

采用 Risk-Based Position Sizing：

text risk_amount = account_equity × risk_per_trade 

然后：

text position = risk_amount / (stop_distance × contract_multiplier) 

再加入：

### Volatility Adjustment

ATR越大：

text 仓位越小 

### Market Cycle Adjustment

例如：

text Strong Breakout       1.0 Tight Channel         0.8 Broad Channel         0.6 Trading Range         0.4 Low Confidence        0 

### Portfolio Risk

必须考虑：

- 单品种风险
- 板块风险
- 总账户风险
- 相关性风险

例如：

text RB / HC / I / J / JM 

不能每个品种都满风险，因为它们属于同一产业链。

---

# 十一、Trade Management

设计：

### Break-even

什么情况下移动到成本？

不要过早移动止损导致趋势交易被洗掉。

### Trailing Stop

比较：

text EMA trailing Swing trailing ATR trailing 

### Partial Exit

研究：

text 1R减仓 2R减仓 runner继续持有 

但必须通过回测决定，而不是主观假设一定有效。

### Hold Extension

当趋势增强：

text 延长持有 

当趋势衰减：

text 提前退出 

---

# 十二、风控体系

至少包括：

## 单笔风险

text risk_per_trade 

## 单品种最大风险

text max_symbol_risk 

## 板块风险

例如：

text 黑色 有色 化工 农产品 能源 贵金属 

## Portfolio Risk

text max_total_open_risk 

## Daily Loss Limit

text max_daily_loss 

## Drawdown Control

例如：

text DD > X% → position_multiplier下降 

## Extreme Market

处理：

- 涨停
- 跌停
- 无法平仓
- 夜盘跳空
- 原油极端行情
- 合约临近交割
- 主力换月

---

# 十三、最重要：禁止未来数据泄露

重点审查所有：

text Swing High Swing Low H1 H2 Wedge Breakout Market Cycle Trend 

算法。

绝对不能使用：

python future_high future_low future_return future_mfe future_mae 

作为实时特征。

例如：

不能因为未来5根K线跌了，才把当前K线标记为：

text Swing High 

如果必须等待2根K线确认：

必须明确：

text 真正可以使用该信息的时间是 t+2 

而不是回填到：

text t 

要求在文档中专门增加：

# Lookahead Bias / Label Leakage Audit

逐项审计所有核心特征。

---

# 十四、中国期货市场适配

Brooks主要研究美股指数期货。

不能直接照搬。

必须考虑中国商品期货特点：

- 夜盘
- 午间休市
- 不同品种不同交易时间
- 交易所涨跌停
- 主力合约切换
- 手续费差异
- 日内平今费用
- 商品产业链相关性
- 政策冲击
- 高频跳空
- 流动性差异
- 最小价格变动
- 合约乘数
- 保证金
- 换月
- 连续合约价格处理

分析哪些 Brooks 方法：

text 可以直接使用 需要调整 不适合中国期货 

---

# 十五、Architecture设计

最终给出清晰代码架构建议，例如：

text brooks/ ├── market_cycle.py ├── trend_detector.py ├── swing_detector.py ├── setup_detector.py ├── trade_mode.py ├── entry_engine.py ├── stop_engine.py ├── position_sizing.py ├── risk_manager.py ├── trade_manager.py ├── features.py ├── config.py └── tests/ 

但请先检查当前：

vnpy/cta/strategy/

已有代码结构。

优先复用已有组件，不要无意义重复造轮子。

---

# 十六、brooks.md必须包含

最终 brooks.md 至少包含：

1. Brooks Price Action核心思想
2. 中国期货CTA适配原则
3. 多周期架构
4. Market Cycle定义
5. Market Cycle量化算法
6. Trend / Always-In判断
7. Setup体系
8. Scalp vs Swing决策
9. Entry
10. Stop Loss
11. Position Sizing
12. Trade Management
13. Exit
14. Portfolio Risk Management
15. 中国期货特殊风险
16. Lookahead Bias审计
17. 推荐代码架构
18. 参数表
19. 回测方案
20. Ablation Test方案
21. Walk-forward方案
22. 后续ML扩展方案

---

# 十七、每一个规则必须做到可编码

禁止只写：

text 趋势很强时做多 

必须进一步定义，例如：

text EMA20 slope > threshold AND close > EMA20 AND HH/HL score > threshold AND trend_bar_ratio > threshold AND overlap_ratio < threshold 

但阈值不要主观写死。

区分：

text 定义 

与：

text 待回测参数 

所有阈值进入配置文件。

---

# 十八、参数设计原则

避免几十个参数导致过拟合。

核心原则：

text 优先结构 其次归一化指标 最后固定阈值 

尽量使用：

text ATR normalized percentage rolling percentile relative rank 

而不是：

text 固定10点 固定20点 

从而提高：

text RB CU AU SC M RM TA MA ... 

跨品种泛化能力。

---

# 十九、回测设计

设计至少三层回测：

### Level 1

单独验证：

text Market Cycle Detection 

### Level 2

验证不同：

text Setup × Market Cycle 

例如：

text H2 × Broad Channel  H2 × Trading Range  H2 × Strong Breakout 

看看真正的Edge来自哪里。

### Level 3

完整Portfolio回测。

至少统计：

text Trade Count Win Rate Average Win Average Loss Profit Factor Expectancy Sharpe Sortino Max Drawdown Calmar MFE MAE Holding Period Turnover Slippage Cost 

---

# 二十、重点做Ablation Test

必须回答：

到底是什么产生收益？

例如：

text Baseline: H2  + Trend Filter  + Market Cycle  + Multi-Timeframe  + ADX  + ATR Position Sizing  + Trade Management 

逐个增加。

输出：

text 模块 收益变化 Sharpe变化 最大回撤变化 交易数变化 

防止系统越来越复杂却没有增加真实Edge。

---

# 二十一、Walk-Forward

禁止只优化整个历史数据。

例如：

text Train: 2015-2020  Validate: 2021  Train: 2016-2021  Validate: 2022  ... 

最终关注：

text Out-of-Sample Performance 

而不是 In-Sample 最优参数。

---

# 二十二、ML扩展

第一阶段：

Brooks规则系统

第二阶段再考虑：

text Rule-based Candidate Generator         ↓ ML Trade Quality Filter 

ML可以预测：

text P(success) Expected R MFE MAE Trade Quality Score 

但是：

训练标签可以使用未来数据；

实时Feature绝对不能使用未来数据。

明确区分：

text Feature Label 

并再次做Leakage Audit。

---

# 二十三、实施顺序

不要直接一次性写完整策略代码。

按照：

### Phase 1

阅读现有vn.py项目代码。

### Phase 2

研究Brooks资料。

### Phase 3

编写：

vnpy/cta/strategy/brooks/brooks.md

### Phase 4

检查：

- 是否可编码
- 是否存在未来数据
- 参数是否过多
- 是否适合中国期货

### Phase 5

根据brooks.md给出：

text Implementation Plan 

### Phase 6

再开始实现核心模块。

---

# 最终要求

这个系统的目标不是：

> 把Brooks所有Price Action概念全部程序化。

而是：

> 找出Brooks体系中最鲁棒、最容易量化、最可能跨品种泛化的交易Edge，构建一个简洁、低过拟合、可解释、可回测的中国商品期货CTA系统。

始终遵守：

text Robustness > Complexity Out-of-Sample > In-Sample Risk Management > Entry Accuracy Market Context > Individual Signal 

完成 brooks.md 后，最后额外输出一份：

text TOP 10最值得优先实现的规则 

并按照：

text 预期Edge 实现难度 过拟合风险 未来函数风险 

四个维度进行评级。