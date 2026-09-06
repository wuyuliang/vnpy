# 第二段下跌策略（Second Leg Down）

基于 Al Brooks 的做空策略：日线空头趋势中，在 5 分钟信号周期识别第二段下跌，
并在下一根 1 分钟 K 线上激活订单。

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

## 当前 `leg_5min` 过滤口径

- 日线方向为强制条件：只使用信号时刻前最近一根已完成日线，并要求
  `daily_ema5 < daily_ema10 < daily_ema20`。
- 5 分钟形态原有的 `ema_fast < ema_mid < ema_slow` 继续保留，因此默认是日线与
  5 分钟双重空头确认。
- 成交量过滤默认开启，保持原有行为。关闭时仅跳过形态首尾大阴线的放量门槛，
  成交量基线、样本数、比率及 `feature_volume_expanded` 审计字段仍照常计算。

关闭成交量过滤：

```bash
python3 -m cta.strategy.second_leg_down.backtest.runner \
  --start 2026-01-01 --end 2026-07-01 \
  --config-override volume_filter_enabled=false
```

**状态**：已实现并跑通 2026H1。当前待办按优先级：
1. `no_progress_bars` 5 → 15（唯一又大又稳的杠杆，见 leg_n4 复盘 §2）
2. 入场前的成本门槛（小仓位过路费 0.12R > 毛边际收益 0.06R，见 §3）
3. **把回测区间拉到 2 年以上**——半年 64 笔的信噪比撑不起任何参数精调
