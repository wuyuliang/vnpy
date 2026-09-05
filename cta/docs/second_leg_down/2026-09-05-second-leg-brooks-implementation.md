# second_leg_brooks 实现记录与第一轮结果

代码：`cta/strategy/second_leg_brooks/`，设计见
[2026-09-05-true-second-leg-design.md](2026-09-05-true-second-leg-design.md)。

## 1. 通用化了哪些东西

新策略没有 fork 现有代码，共享层是新抽出来的三个模块：

| 模块 | 内容 |
| --- | --- |
| `common/intraday_features.py` | 因果特征帧（ATR / EMA / 成交量基线 / 时段边界 / 结构低点）。参数是普通关键字，不绑定任何一个策略的 config 类 |
| `common/setup_pipeline.py` | `PatternMatch`（带 `extras`，形态各自的附加量不用各写一个类型）、`SetupInstrument`、`execution_timeline`、`order_window`、`safe_ratio` |
| `common/short_setup_runner.py` | 回测编排里与策略无关的部分：解析器、复现命令、执行元数据快照、信号周期聚合与列贴回、回放上下文、资金口径汇总、run context、CLI 收尾 |

于是 `second_leg_brooks/backtest/runner.py` 只有 137 行，里面剩下的都是这个
策略特有的东西。`common/bar_shapes.py` 里新增的
`causal_low_pullback_structure` 把"第一段→回抽"的搜索向量化了，
`iter_matches` 只遍历已命中的根。

`second_leg_down` 暂时**没有**改成走共享层——它当时正在被并行修改，
留给后续合并，两边的实现现在是等价的。

## 2. 与原型的标定

原型（`cta/analysis/second_leg_down_funnel_scan.py` 的扩展）在 5 分钟上给出
21 品种 / 半年 **631** 个结构，即 **≈30 个/品种/半年**。

生产实现，6 品种（L MA PP EG V SA）/ 2026H1 / 5 分钟：

```
candidates 177   trades 113        →  29.5 个/品种/半年
```

**两边对得上**，说明生产实现没有比原型宽也没有更严。

## 3. 第一轮结果（诚实版）

```
6 品种 / 半年 / 初始 100 万
trades 113   净盈亏 -24,843   胜率 43%   中位 net_r -0.08
中位 mfe_r 0.34   中位 mae_r 0.36
exit: NO_PROGRESS_TIME_STOP 55 | PROFIT_FLOOR 37 | STOP 15 | 其他 6
```

比旧的单腿口径好（旧的胜率 25~37%、时间止损占 75%），但**仍然是亏的**。

原型预测的"54% 能摸到 0.5R"没有兑现，中位 mfe_r 只有 0.34R。差在哪：
原型是无成本前瞻统计，生产里多了真实撮合（触发价 close−1tick 之后才成交，
成交价可能更差）、手续费、滑点，以及时间止损提前砍仓。

### 放宽时间止损：负面结果

`no_progress_bars` 8 → 20（信号根，即 40 分钟 → 100 分钟）：

| | 时间止损 | 止盈地板 | 净盈亏 | 胜率 | 中位 net_r |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8（默认） | 55 | 37 | −24,843 | 43% | −0.08 |
| 20 | 12 | 45 | **−30,846** | 38% | −0.15 |

**时间止损不是问题，它在帮忙。**放宽之后地板确实更常启动，但亏损单也多亏了
一会儿，净额更差。这条路走不通，不要再花时间在它上面。

## 4. 顺手修的一个真问题：同一结构重复开单

结构成立之后的连续几根会反复匹配同一个 swing + 同一个 rally_high，
**止损位完全相同**——等于把同一笔交易下三次、风险三倍。
`one_candidate_per_structure`（默认开）只保留第一根。

在当前实现下它只砍掉 3/17 个候选（Codex 重写向量化搜索时已经消掉了大部分），
但作为安全网留着：它能防住的是"三倍风险"这一类错误，代价是零。

## 5. 测试

`cta/strategy/second_leg_brooks/tests/` 现在覆盖：向量化结构原语的因果性、
跨休市不借用高点、特征帧的分段隔离、入场根的四道门（实体、下影、放量、
跌破 swing low，各自带"关掉这道门就应该放行"的对照）、回抽比例的上下界、
结构去重、止损取回抽高点、未来数据篡改不影响既有候选、`for_replay()` 的窗口
换算、runner 默认值与复现命令。

全量：**664 passed**。

## 6. 下一步该往哪走

时间止损那条路已经证伪。按当前证据，值得试的顺序：

1. **扩品种再确认**。6 品种 113 笔的结论不稳。先跑 40 品种全量，
   看 113 → ~750 笔之后中位 net_r 还是不是负的。这是最便宜的一步，
   而且不改任何策略定义。
2. **按审计列分层**。候选表里有 `pullback_ratio`、`first_leg_atr`、
   `entry_body_atr`、`entry_volume_ratio`、`break_of_swing_low`、
   `pullback_bars`。先看有没有哪个分层的中位 net_r 明显为正，
   有的话再收紧对应的门——而不是继续整体放宽或收紧。
3. **止损口径**。现在止损放回抽高点，中位 mae_r 0.36 说明它很少被打到，
   但 R 很小，成本占比就高。试 `stop_buffer_ticks` 加大、
   或改用 swing high + ATR 缓冲，看 R 放大之后成本占比下降能不能翻正。

第 1 步应该先做，其余两步都依赖它给出的样本量。
