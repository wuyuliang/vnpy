# 候选样本、已成交样本、被过滤样本的训练集设计

## 1. 目标

在 CTA 研究中，不能只使用“已成交交易样本”训练模型。  
如果只用已成交样本，模型会产生严重的**选择偏差（selection bias）**：

- 模型只能看到“旧规则放行过的交易”
- 模型看不到“本来是好机会但被规则错杀的交易”
- 模型也看不到“候选但被正确过滤掉的垃圾机会”

因此，正确做法是：

1. 先定义**候选事件宇宙**
2. 对所有候选事件统一打未来标签
3. 再记录这些候选事件是否成交、为何被过滤
4. 训练时优先学习“机会质量”，而不是“旧系统是否执行”

---

## 2. 样本分层设计

建议把样本分成 3 层。

### 2.1 候选事件层 `candidate_events`

这是最核心的一层。  
定义为：

> 所有满足基础 setup 条件的候选交易事件，不管最终有没有成交。

例如对于 `tight range breakout`：

- 出现 tight range
- 出现向上突破触发
- 数据完整
- 满足最基础研究条件

这一层中包含：

- 已成交样本
- 候选但被规则过滤样本
- 候选但因风控未成交样本
- 候选但因容量冲突未成交样本
- 候选但因执行约束未成交样本

### 2.2 已成交交易层 `executed_trades`

这是实际发生的交易事件。  
只包含：

- 已下单
- 已成交
- 有真实入场价、退出价、滑点、手续费、PnL

这一层适合做：

- 执行质量分析
- 滑点分析
- 成交偏差分析
- 实盘归因分析

### 2.3 背景时点层 `background_samples`（可选）

这是不满足 setup 的普通时点。  
这一层一般不用于第一版 trade filter，但可用于：

- 市场状态分类模型
- regime classifier
- 趋势 / 震荡 / breakout mode 分类

---

## 3. 候选但未成交/被过滤的样本如何处理

结论：

> 不能丢，必须保留，而且要分类记录。

建议增加两个字段：

### 3.1 样本状态字段 `sample_status`

```text
executed
filtered_by_rule
blocked_by_risk
blocked_by_capacity
blocked_by_execution
not_triggered_market
```

含义如下：

- `executed`：已成交
- `filtered_by_rule`：被规则过滤
- `blocked_by_risk`：因风控未成交
- `blocked_by_capacity`：因仓位/板块暴露/持仓冲突未成交
- `blocked_by_execution`：因流动性、夜盘、滑点、交易时段等**执行规则**原因未成交
- `not_triggered_market`：候选已生成但下一根 bar **市场未触发**（如 stop 单未被触及），与 `blocked_by_execution` 的"执行规则阻断"严格区分；用于研究"市场是否给了机会"vs"执行端是否真的可下单"

### 3.2 阻断原因字段 `block_reason`

```text
near_major_resistance
breakout_strength_too_weak
range_not_tight_enough
trend_filter_failed
max_sector_exposure_reached
weekly_drawdown_lock
night_session_disabled
slippage_too_high
execution_window_closed
```

这一步很重要，因为后续可以分析：

- 哪些过滤是正确的
- 哪些过滤错杀了好机会
- 哪些风控太严
- 哪些执行规则太保守

---

## 4. 训练时如何使用这些样本

### 4.1 场景 A：训练“机会质量模型”

这是第一优先级。

目标：

> 判断这次候选机会本身好不好，而不是旧系统有没有做。

此时应该使用：

- 已成交样本
- 候选但被过滤样本
- 候选但因风控未成交样本
- 候选但因容量冲突未成交样本

对这些样本统一计算事后标签，例如：

- `future_mfe_atr`
- `future_mae_atr`
- `opportunity_class`
- `is_good_opportunity`

也就是说：

> 不管当时有没有成交，都要按统一规则定义“虚拟入场”，并计算其未来表现。

模型学习的是：

- 市场真实给出的机会质量

而不是：

- 旧规则最后选择了什么

### 4.2 场景 B：训练“执行模仿模型”

目标：

> 预测旧系统在当时会不会放行这笔交易。

这时才会用：

- `executed = 1`
- `filtered / blocked = 0`

但这个模型不是 alpha 模型，而是“规则模仿”或“策略蒸馏”模型。  
第一阶段一般不建议优先做，因为它会强化旧规则，而不是发现增量机会。

### 4.3 场景 C：训练“风控 / 容量决策模型”

目标：

> 某些机会虽然本身不错，但组合层是否应该让路。

这时 `blocked_by_risk` 和 `blocked_by_capacity` 非常有价值。

你可以分析：

- 被挡掉的机会后来其实很好吗
- 如果很好，组合约束是不是太严格
- 被挡掉的机会后来其实很差吗
- 如果很差，说明当前风控起到了作用

---

## 5. 推荐的数据表结构

建议至少设计两张核心表。

## 5.1 候选事件表 `candidate_events`

一行代表一个候选交易事件，不管是否成交。

### 推荐字段

```text
candidate_id
symbol
datetime
timeframe
setup_type
direction
candidate_flag
sample_status
block_reason
entry_price_virtual
stop_price_virtual
target_price_virtual
trigger

feature_*
atr_warmed
future_mfe_atr
future_mae_atr
future_return_atr
opportunity_score
opportunity_class
is_good_opportunity

executed_flag
linked_trade_id
```

### 字段说明

- `candidate_id`：候选事件唯一主键，`(symbol, interval, datetime, setup_type, direction)` 五元组的稳定 hash；一旦生成不再随 merge_asof / 二次 standardize 重排而变化（保证 `candidate_events.parquet` ↔ `training_samples.parquet` 主键一致）。
- `setup_type`：例如 `tight_range_breakout`
- `direction`：long / short
- `trigger`：突破/触发价（baseline 决策时点的目标入场价）。`entry_price_virtual` 在 `entry_price` 缺失时按 `entry_price → trigger → feature_close → close` 顺序兜底，**不再用 `stop_price` 兜底**（在 ATR breakout 等场景中 stop ≠ trigger）。
- `entry_price_virtual`：统一定义的虚拟入场价，即使未成交也要有
- `feature_*`：你提取的结构、趋势、波动率、风险收益等特征
- `atr_warmed`：0 / 1，0 表示 entry bar 的 14 周期 ATR 还在 warmup（前 ~14 根），相关 mfe/mae/score 标签不可信。模型 pipeline 在 `_ensure_training_columns` 之后会显式 drop `atr_warmed=0` 的行。
- `future_mfe_atr` / `future_mae_atr`：事后标签（按 horizon_bars 内的 high/low 相对入场价归一化为 ATR 倍数）。`atr_warmed=0` 或 mfe/mae 缺失时显式置 NaN，**不再用 `fillna(0)` 把"未知"伪装成"差机会"**。
- `opportunity_score`：`mfe_atr - LABEL_MAE_PENALTY * mae_atr`（默认 `LABEL_MAE_PENALTY=0.7`）。机会质量分；NaN 表示标签未知。
- `future_return_atr`：以虚拟入场价为锚点的**真实 horizon 收益**（即 baseline `future_pnl_atr`，已用 ATR 归一化）。与 `opportunity_score` 严格区分：前者描述"这次方向是否赚钱"，后者描述"风险调整后机会有多好"。
- `opportunity_class`：`A / B / C / D / U`。阈值同源 `LABEL_THRESHOLD / OPPORTUNITY_CLASS_A_BREAK / OPPORTUNITY_CLASS_B_BREAK`（见 `cta/config/baseline_skill_suite_config.py`）；`U`（unknown）表示标签未知（warmup 或 mfe/mae 缺）。
- `is_good_opportunity`：0/1；`opportunity_score > LABEL_THRESHOLD` 且标签已知时为 1。
- `executed_flag`：是否真实成交
- `linked_trade_id`：若成交，则关联到真实成交表

### 为什么一定要有 `entry_price_virtual`

因为你需要对未成交样本也统一打标签。  
常见定义方式：

- 当前 bar 收盘价
- 下一根 bar 开盘价
- 规则触发价
- 突破价 + 固定滑点

第一版建议统一简单处理，避免标签口径混乱。

## 5.2 已成交交易表 `executed_trades`

一行代表一笔真实成交交易。

### 推荐字段

```text
trade_id
candidate_id
symbol
datetime_entry
datetime_exit
entry_price_real
exit_price_real
volume
direction
commission
slippage
holding_bars
pnl_abs
pnl_atr
max_favorable_excursion_real
max_adverse_excursion_real
execution_notes
```

### 这张表主要用途

- 执行质量分析
- 实盘和回测偏差分析
- 手续费与滑点归因
- 出场逻辑分析

---

## 6. 推荐的双标签体系

为了后续分析清楚，建议每个候选样本都有两套标签。

### 6.1 市场机会标签

用于回答：

> 这次候选机会本身是不是好机会？

典型字段：

```text
future_mfe_atr
future_mae_atr
future_return_atr
is_good_opportunity
opportunity_class
```

这些标签**独立于是否成交**。

### 6.2 系统决策标签

用于回答：

> 系统当时是否放行了这笔机会？

典型字段：

```text
executed_flag
sample_status
block_reason
risk_block_flag
capacity_block_flag
execution_block_flag
```

这些标签反映的是系统行为，而不是市场本身。

---

## 7. 最重要的四类样本分析框架

你后续一定要把样本拆成这四类：

### 7.1 放行且事后很好

```text
executed_flag = 1
is_good_opportunity = 1
```

这类样本说明：

- 当前规则抓住了有效机会
- 要分析共性，强化它们的特征

### 7.2 放行但事后很差

```text
executed_flag = 1
is_good_opportunity = 0
```

这类样本是模型和规则最需要过滤掉的对象。  
你要重点分析：

- 为什么被放进来
- 哪些特征当时已经显示它风险很大
- 是否可被 trade filter 降分

### 7.3 没放行但事后很好

```text
executed_flag = 0
is_good_opportunity = 1
```

这是最有价值的一类。  
它说明：

- 你当前系统漏掉了 alpha
- 某些规则可能过于保守
- 某些风控约束可能太紧

如果你只看已成交样本，就永远发现不了这些机会。

### 7.4 没放行且事后也很差

```text
executed_flag = 0
is_good_opportunity = 0
```

这类样本说明：

- 当前过滤器或风控在发挥作用
- 这些过滤规则可能是有价值的

---

## 8. 正确的训练顺序

建议按下面顺序推进。

### 第一步：先做候选事件表

不要从 `executed_trades` 开始。  
而是先构造：

> 全量候选 setup 事件表 `candidate_events`

### 第二步：统一打未来标签

对所有候选事件，不管是否成交，都按统一规则定义：

- `entry_price_virtual`
- `future_mfe_atr`
- `future_mae_atr`
- `opportunity_class`

### 第三步：先训练“机会质量模型”

模型输入：

- 候选事件特征
- setup / breakout / context / risk 特征

模型输出：

- `is_good_opportunity`
- `opportunity_class`
- `future_mfe_atr`
- `future_mae_atr`

注意：

> 第一版模型优先学“机会质量”，而不是“执行结果”。

### 第四步：再分析系统过滤是否合理

利用：

- `sample_status`
- `block_reason`

做归因分析：

- 哪些过滤规则挡掉了好机会
- 哪些风控挡掉了坏机会
- 哪些容量冲突决策是合理的

---

## 9. 如果只用已成交样本，会出现什么问题

### 9.1 选择偏差

模型只能看到旧系统已经做过的交易，视野太窄。

### 9.2 学不到漏掉的 alpha

很多最有价值的改进都来自：

- 候选但没做
- 结果其实很好

这部分如果不保留，就丢失了最大增量。

### 9.3 容易变成旧规则复读机

如果只用 executed trades，模型最后学到的是：

- 旧规则喜欢什么

而不是：

- 市场里真正值得做的机会是什么

---

## 10. 推荐的最终原则

### 原则一

> 所有满足基础 setup 的候选事件都要入库。

### 原则二

> 所有候选事件都要统一定义“虚拟入场”和未来标签。

### 原则三

> “是否成交”只是附加标签，不是第一版模型的训练目标。

### 原则四

> 第一版模型先学“机会质量”，后面再学“执行决策”。

---

## 11. 一句话总结

> “候选但未成交 / 被过滤”的样本不是噪音，而是你训练机会模型、优化规则、发现漏掉 alpha 的最重要数据来源。

正确做法是：

1. 先把所有候选事件统一存下来  
2. 对所有候选事件统一打未来标签  
3. 再附加成交/未成交/阻断原因  
4. 训练时优先学习机会质量，而不是旧系统执行结果

---

## 12. 推荐后续文件

建议接下来继续补下面两个 Markdown 文件：

- `candidate_events_schema.md`
- `candidate_events_generation_pipeline.md`

它们分别负责：

1. 样本表结构定义
2. 从 vn.py 特征生成候选训练样本的流程设计
