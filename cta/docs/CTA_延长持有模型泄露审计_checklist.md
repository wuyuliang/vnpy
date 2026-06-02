# CTA 延长持有模型泄露审计 Checklist

## 1. 文档目标

本清单用于审计 CTA 中“延长持有（hold extension / persistence extension）”相关模型是否存在以下问题：

- 未来函数
- 标签泄露
- 时点穿越
- 样本选择偏差
- 训练/验证重叠污染
- 标签与退出逻辑自循环耦合
- 验证集调参污染
- 线上/回测特征口径不一致

该清单特别适用于如下流程：

1. 先构造未来标签，例如 `future_mfe_atr`、`future_mae_atr`
2. 用这些标签训练 `hold_extend_score`
3. 再将 `hold_extend_score` 喂给退出模拟器，决定是否延长 horizon

---

## 2. 适用对象

适用于以下模型与模块：

- `hold_extend_score`
- `trend_persistence_model`
- `recommended_horizon_extension_bars`
- `future_mfe_atr`
- `future_mae_atr`
- `future edge` 类标签
- `dynamic exit horizon`
- `trailing exit extension`
- `regime-aware hold extension`

---

## 3. 风险总览

延长持有模型最常见的风险，不是“把 future 字段直接放进特征”，而是以下隐性问题：

1. 特征在决策时点并不可见，但在离线回测中被算出来了
2. 标签未来窗口依赖最终退出路径，而最终退出又受模型影响
3. 训练样本只保留 `is_executed == 1`，导致强选择偏差
4. train/valid/test 在标签窗口层面互相重叠
5. 特征过滤只靠列名，不做时点审计
6. 延长条数映射规则是在测试集上调出来的
7. 回测时模型分数使用了事后生成的状态变量
8. 线上与离线生成同名特征，但含义并不一致

---

## 4. 审计原则

### 4.1 核心原则一：决策时点可见性
任何用于“是否延长持有”的输入特征，都必须满足：

> 在延长决策发生的那个时间点，该特征值已经可以被真实系统观测到。

### 4.2 核心原则二：标签独立性
训练标签应是外生定义的，不能依赖模型自身后续会不会延长持有。

### 4.3 核心原则三：严格时间切分
训练集、验证集、测试集不仅不能时间穿越，还要避免标签窗口重叠。

### 4.4 核心原则四：训练样本代表性
不能只训练“旧系统已经执行过”的样本，而忽略大量候选但未执行样本。

### 4.5 核心原则五：线上离线一致
同一个特征、同一个标签、同一个决策逻辑，线上/OOT/回测定义必须一致。

---

## 5. 审计模块 A：标签定义审计

### 5.1 检查 `future_mfe_atr` 是否仅作为标签
确认：

- `future_mfe_atr` 只出现在 label 列中
- 不出现在特征列
- 不参与线上推理输入
- 不在任何衍生特征里被间接引用

### 5.2 检查 `future_mae_atr` 是否仅作为标签
确认：

- `future_mae_atr` 只出现在监督目标中
- 不参与当前时点的特征构造
- 不被重命名后混入特征

### 5.3 检查标签窗口是否固定且外生
必须明确：

- 标签未来窗口的起点是什么
- 标签未来窗口的终点是什么
- 标签窗口是否固定 bars 数
- 标签窗口是否与策略最终退出解耦

#### 审计问题
- `future_mfe_atr/future_mae_atr` 是基于固定未来 N bars 计算的吗？
- 还是基于“最终退出前整段路径”计算？
- 如果是基于最终退出路径，而退出又会被 hold extension 影响，则存在目标定义闭环风险。

### 5.4 检查标签是否与模型决策耦合
确认：

- 训练标签的未来路径，不应依赖模型本身是否选择延长
- 模型不能用一个被自身改变过的未来结果当监督目标

#### 高风险信号
- 用“最终策略退出前路径”定义 `future_mfe_atr`
- 而最终策略退出又包含 `hold_extend_score` 逻辑

---

## 6. 审计模块 B：特征可见性审计

### 6.1 对每个输入特征做 as-of 审计
每个特征都必须回答：

- 该特征使用了哪些原始数据列
- 最晚使用到了哪个 bar
- 决策时点是何时
- 特征是否在决策时点已经冻结

### 6.2 必须输出以下字段进行日志核查
建议为每个特征增加审计记录：

```text
feature_name
decision_datetime
feature_asof_datetime
max_source_bar_datetime
is_leak_suspected
```

### 6.3 审计规则
要求：

```text
max_source_bar_datetime <= decision_datetime
```

只要某个特征满足：

```text
max_source_bar_datetime > decision_datetime
```

就高度疑似穿越。

### 6.4 高风险伪装特征示例
以下特征即使不带 `future` 关键词，也可能泄露：

- `current_trade_best_price`
- `trade_runup_score`
- `post_entry_trend_strength`
- `bars_since_future_peak`
- `holding_efficiency`
- `current_trade_edge`
- `regime_confirmed_after_breakout`
- `remaining_profit_potential`
- `trailing_exit_pressure`

#### 审计要求
不要只看列名，要追溯计算逻辑。

---

## 7. 审计模块 C：决策时点审计

### 7.1 明确 hold extension 的决策时点
必须明确：

- 模型是在入场时打分，还是在某个持仓中间时点打分
- 延长持有判断发生在原始退出前哪个 bar
- 使用的是 close 决策，还是 next open 执行

### 7.2 检查特征快照是否与决策时点一致
如果模型是在 `t` 时刻决定是否延长持有，那么输入特征只能使用 `<= t` 的信息。

### 7.3 典型风险
#### 风险 A
在原本要退出的时刻才打分，但输入里已经包含整个持仓路径统计。

#### 风险 B
使用了 trailing stop 更新后的内部变量，但这些变量本质上需要未来路径才能形成。

#### 风险 C
用“当前 trade 到最终 exit 的收益特征”回填到“当前决策点”。

---

## 8. 审计模块 D：训练样本选择偏差审计

### 8.1 检查是否只使用 `is_executed == 1`
如果训练样本只来自已成交交易，则模型会存在强选择偏差。

#### 风险表现
模型学到的是：

- 旧系统放行过的交易中，哪些更值得延长

而不是：

- 所有候选交易中，哪些真的值得延长

### 8.2 必须检查的字段
建议至少存在以下字段：

```text
candidate_flag
executed_flag
sample_status
block_reason
```

### 8.3 审计问题
- 是否存在大量候选但未执行样本？
- 这些样本是否也被打了未来标签？
- 训练时是否排除了它们？

### 8.4 对照实验要求
必须做两组实验：

#### 实验 A
只用 `is_executed == 1`

#### 实验 B
使用 candidate-level 全样本（只要满足可评估条件都纳入）

#### 审计结论参考
- 如果 A 显著强于 B 很多，说明执行样本偏差较重
- 如果 B 仍然有效，则模型更可信

---

## 9. 审计模块 E：训练/验证/测试切分审计

### 9.1 不能只看 `exit_datetime`
即使做了按时间切分，也不能只按 `exit_datetime` 判断是否重叠。

必须同时考虑：

- 特征观察窗口
- 决策时点
- 标签未来窗口

### 9.2 必须构造样本时间区间
每个样本都建议记录：

```text
feature_start_datetime
feature_end_datetime
decision_datetime
label_start_datetime
label_end_datetime
```

### 9.3 purge / embargo 检查
train/valid/test 之间必须满足：

- 不共享特征窗口
- 不共享标签窗口
- 不共享同一段未来路径

### 9.4 高风险情形
如果：

- 训练样本在 `t0` 决策，标签用 `t0 -> t0+20`
- 验证样本在 `t0+5` 决策，标签用 `t0+5 -> t0+25`

则两者共享大量未来路径，验证集会偏乐观。

### 9.5 审计要求
做一次全量 pairwise overlap 检查：

- 任意 train 样本和 valid 样本
- 任意 valid 样本和 test 样本

确认它们的标签时间区间不重叠。

---

## 10. 审计模块 F：特征过滤机制审计

### 10.1 检查是否只靠列名过滤
如果只是按照列名包含如下关键词过滤：

- `future`
- `label`
- `pred`

这不够安全。

### 10.2 必须增加计算图审计
建议对每个特征保留 lineage：

- 来源列
- 来源模块
- 计算函数
- 依赖窗口
- 最大时间偏移

### 10.3 必须做白名单而不是黑名单
推荐：

- 不要只删“坏特征”
- 而是只允许“已审计通过的特征”进入模型

#### 推荐做法
建立：

```text
approved_feature_list.yaml
```

每一列特征都有：

- `feature_name`
- `allowed_for_training`
- `allowed_for_online`
- `asof_safe`
- `reviewed_by`
- `review_date`

---

## 11. 审计模块 G：`recommended_horizon_extension_bars` 映射审计

### 11.1 检查它是否纯来自训练期规则
必须确认：

- `recommended_horizon_extension_bars` 是基于训练集或验证集定义的吗？
- 是否在测试集上反复调过映射阈值？
- 是否依据测试集表现选择 extension bars 长度？

### 11.2 高风险情形
如果你在测试集上观察：

- 分数 > 0.7 时延长 5 bars 好
- 分数 > 0.85 时延长 8 bars 更好

然后把这个规则写回模型逻辑，这就属于测试集调参污染。

### 11.3 审计要求
记录：

```text
mapping_version
mapping_source_dataset
mapping_tuning_period
mapping_last_updated_at
```

### 11.4 正确做法
- 在训练/验证集上确定映射规则
- 测试集只做一次评估
- OOT 阶段固定映射，不再反调

---

## 12. 审计模块 H：退出模拟器耦合审计

### 12.1 检查退出模拟器是否使用了事后状态
退出模拟器中所有触发延长的条件，都必须在当时可见。

重点检查：

- regime 条件
- trailing stop 状态
- 当前持仓表现摘要
- 扩展条数映射输入

### 12.2 典型高风险点
- 用最终 trade 的完整统计特征作为 exit 输入
- regime 条件由未来 bars 才能确认
- 当前状态字段其实是离线回放后一次性补上的

### 12.3 审计问题
- `hold_extend_score` 在模拟器调用时，是实时重新算，还是使用离线结果列？
- 若使用离线结果列，该列生成时是否严格冻结在决策时点？
- regime 条件是否包含未来确认信息？

---

## 13. 审计模块 I：线上 / 离线一致性审计

### 13.1 检查同名特征是否同义
同名特征在线上和离线中，计算口径必须一致。

### 13.2 检查模型输入是否完全一致
线上推理使用的列、顺序、缺失值填充方式必须与训练时一致。

### 13.3 审计要求
保存：

- 训练时 feature schema
- OOT 时 feature schema
- 线上实时推理 feature schema

并做哈希比对。

### 13.4 检查线上是否有“离线专用列”
例如：

- `future_*`
- `pred_*` from validation
- 后处理统计列
- trade summary columns that require future exit

---

## 14. 审计模块 J：回测与 OOT 可信度审计

### 14.1 必做对照实验
必须至少做以下 4 组：

#### 实验 1：不延长持有 baseline
只使用原始退出逻辑。

#### 实验 2：规则型延长持有
不用模型，只用固定条件延长。

#### 实验 3：模型延长持有
用 `hold_extend_score`。

#### 实验 4：随机延长对照
在同样触发频率下随机延长若干 bars。

### 14.2 审计结论解释
- 如果模型延长远强于随机延长和规则延长，才有一定可信度
- 如果模型只比随机好一点点，edge 很薄
- 如果测试集很强但 OOT 急剧衰减，要怀疑过拟合或泄露

---

## 15. 建议的审计日志表

建议增加一张审计表：

```text
hold_extension_audit_log
```

推荐字段：

```text
audit_id
model_version
feature_name
decision_datetime
feature_asof_datetime
max_source_bar_datetime
is_time_safe
label_definition_version
label_window_start
label_window_end
is_label_independent
sample_status
executed_flag
split_name
is_overlap_purged
mapping_version
online_offline_schema_hash_match
audit_comment
created_at
```

---

## 16. 红线判断标准

如果出现以下任意一条，应直接判定为高风险：

1. 任意模型输入特征在决策时点后才可知
2. 标签窗口依赖最终退出，而最终退出又受模型控制
3. train/valid/test 标签窗口存在明显重叠
4. `recommended_horizon_extension_bars` 在测试集上反复调参
5. 模型仅在 `is_executed==1` 样本上训练且未做 candidate-level 对照
6. 线上和离线 feature schema 不一致
7. 特征过滤只靠列名，不做 lineage 审计

---

## 17. 建议的最小修复方案

如果当前系统存在不确定性，建议按以下顺序修复：

### 第一步
将 `future_mfe_atr/future_mae_atr` 标签窗口改成固定 horizon，与最终退出解耦。

### 第二步
为所有输入特征增加 `asof_datetime` 审计。

### 第三步
使用 candidate-level 样本，而不是只用 executed 样本。

### 第四步
做严格的 purged time split。

### 第五步
把 `recommended_horizon_extension_bars` 映射冻结在训练/验证集，不再用测试集调。

### 第六步
建立 feature 白名单，而不是只靠黑名单过滤。

---

## 18. 最终结论模板

完成审计后，建议输出如下结论模板：

### 审计结论
- 是否发现显式 future 列进入特征：是 / 否
- 是否发现时点穿越：是 / 否
- 是否发现标签与退出逻辑耦合：是 / 否
- 是否存在样本选择偏差：低 / 中 / 高
- 是否存在 train/valid/test 窗口重叠：是 / 否
- 是否存在测试集调参污染：是 / 否
- 当前模型可信度评级：A / B / C / D

### 修复建议
- 立即修复项
- 中期修复项
- 可选优化项

---

## 19. 一句话总结

> 延长持有模型最危险的泄露，不是显式的 `future_*` 列，而是“决策时点不可见特征”、“标签与退出逻辑闭环耦合”、“执行样本选择偏差”和“标签窗口重叠污染”。

审计重点不应该只看列名，而要看：

1. 特征在何时可见
2. 标签如何定义
3. 训练样本怎么来的
4. 切分是否严格
5. 模型输出如何接回退出系统
