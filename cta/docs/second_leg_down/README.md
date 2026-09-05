# 第二段下跌策略（Second Leg Down）

基于 Al Brooks 的 1 分钟做空策略：下跌趋势中，连续放量大阴线突破结构，做空第二段下跌。

与 `multi_timeframe_trend`（日线定方向 / 5 分钟定入场 / 做多）**并行独立**运行，
共用同一套数据加载、元数据、回放引擎、报表与图表。

| 文档 | 内容 |
| --- | --- |
| [design.md](design.md) | 完整设计与实现规格 |
| [2026-09-05-candidate-scarcity-analysis.md](2026-09-05-candidate-scarcity-analysis.md) | 第一版半年只有 9 笔：漏斗诊断与放宽方向 |
| [2026-09-05-true-second-leg-design.md](2026-09-05-true-second-leg-design.md) | **补回"第一段+回抽"**：机会 ×13，且是唯一中位 MFE>MAE 的口径 |
| [2026-09-05-second-leg-brooks-implementation.md](2026-09-05-second-leg-brooks-implementation.md) | `second_leg_brooks` 实现记录、与原型的标定、第一轮结果 |
| [2026-09-05-leg-n4-exit-policy-analysis.md](2026-09-05-leg-n4-exit-policy-analysis.md) | leg_n4 复盘：5 根时间止损在放血，成本吃掉小仓位 |
| [2026-09-05-selling-climax-filter-negative-result.md](2026-09-05-selling-climax-filter-negative-result.md) | 抛售高潮能否事前过滤：**负面结果**，七个判别量全部交叉检验失败 |

扫描工具：`cta/analysis/second_leg_down_funnel_scan.py`（几秒钟跑完一轮参数扫描，
不用为了试一个参数跑一次全量回测）。

**状态**：已实现并跑通 2026H1。当前待办按优先级：
1. `no_progress_bars` 5 → 15（唯一又大又稳的杠杆，见 leg_n4 复盘 §2）
2. 入场前的成本门槛（小仓位过路费 0.12R > 毛边际收益 0.06R，见 §3）
3. **把回测区间拉到 2 年以上**——半年 64 笔的信噪比撑不起任何参数精调
