# 创业板 ETF 状态预测叠加 EMA 仓位回测设计

## 1. 目标

对 `159915.SZ` 易方达创业板 ETF 在 `2017-08-14` 至 `2026-07-20` 期间进行
日频回测，把已实现的 1 日、3 日状态预测叠加到已验收 EMA 策略之上：

- 原 EMA 策略独立决定可接受的目标仓位上限。
- 3 日预测决定状态仓位上限及是否允许从空仓开仓。
- 1 日预测决定最终仓位的升降节奏。
- EMA 和 3 日状态的风险降低立即执行。
- 最终仓位增加继续遵守 10 个完整交易日的冷却。

本次回测是在第一阶段预测规则未通过持久性基线准入条件后，由用户明确要求开展的
研究性诊断。结果不得重新宣称为未见样本外结果，也不能自动获得实盘准入资格。

## 2. 回测区间与初始状态

回测区间按交易日收盘记录，首尾均包含：

```text
start = 2017-08-14
end   = 2026-07-20
```

初始条件：

- 2017-08-14 开盘前资金为 `1,000,000` 元。
- 初始持仓为 0，最终状态冷却计时器为空。
- 从 2016-01-01 起下载行情，区间前行情只用于 EMA、状态指标和首个执行日预测的预热。
- 预热数据必须在 2017-08-14 前提供至少 252 条有效交易行，否则运行立即失败。
- 不继承 2017-08-14 以前的仓位、盈亏或冷却状态。
- 2026-07-20 收盘按市值结算，不为报告目的强制平仓。

## 3. 数据与因果时序

### 3.1 数据源

使用 Tushare：

```text
fund_daily
fund_adj
trade_cal
```

原始日线和逐日复权因子通过 `regime_data.build_causal_bars` 构造
`point_in_time_adjusted` 行情：

- 每个交易日只能使用同日或更早的复权因子。
- 调整后 OHLC 等于原始 OHLC 乘当时因子。
- 调整后成交量等于原始成交量除以当时因子。
- 成交额保持不变。
- EMA 信号、状态预测、成交价格和收盘估值统一使用该因果连续价格。

### 3.2 信号时序

对交易日 `T`：

- 1 日、3 日状态分数在 `T` 收盘后生成。
- 两个分数最早在标的下一条实际交易行 `T+1` 开盘使用。
- 3 日预测的目标答案日虽然是 `T+3`，但预测本身在 `T+1` 开盘已经可用。
- EMA 开盘条件使用 `T+1` 开盘价及截至 `T` 已完成的 EMA 和确认数据。

每个执行日必须记录：

```text
execution_date
regime_feature_asof_date
score_1d
score_3d
max_feature_source_date
```

并满足：

```text
max_feature_source_date <= regime_feature_asof_date < execution_date
```

状态预测按 `feature_asof_date` 先转为一日一行，再与该标的下一条实际行情行连接。
禁止用 `prediction_for_date` 把 3 日预测推迟到 `T+3` 才使用，也禁止按自然日移动。

## 4. 已验收 EMA 策略

EMA 上限使用现有 `TrendAllocationConfig`：

```text
symbol = 159915.SZ
slow_period = 20
confirmation_days = 1
slope_lookback = 3
risk_increase_cooldown_days = 10
```

交易参数：

```text
initial_capital = 1_000_000
lot_size = 100
commission_rate = 0.0003
min_commission = 5
slippage_rate = 0.0005
```

原 EMA 策略从 2017-08-14 空仓开始独立运行，产生每日
`ema_target_weight`，取值只能是 `0.0/0.5/1.0`。该目标路径保留原策略自己的风险优先、
确认和 10 日冷却行为，不受状态覆盖层历史仓位影响。

本次只使用已验收 EMA 退出条件，不增加没有预先定义阈值的 ATR 止损。

## 5. 3 日状态上限

映射如下：

| `score_3d` | 状态 | 允许从空仓开仓 | `regime_cap` |
|---|---|---:|---:|
| `[-3,-2]` | 趋势向下 | 否 | 0% |
| `(-2,-1)` | 震荡向下 | 否 | 50% |
| `[-1,1]` | 无趋势 | 否 | 50% |
| `(1,2)` | 震荡向上 | 是 | 50% |
| `[2,3]` | 趋势向上 | 是 | 100% |

边界固定为：

```text
score_3d <= -2       -> cap 0.0, entry forbidden
-2 < score_3d <= 1  -> cap 0.5, entry forbidden
1 < score_3d < 2    -> cap 0.5, entry allowed
score_3d >= 2       -> cap 1.0, entry allowed
```

每日风险上限为：

```text
risk_ceiling = min(ema_target_weight, regime_cap)
```

若 `risk_ceiling < current_target_weight`，先立即降到 `risk_ceiling`，不受 1 日信号或
冷却限制。

## 6. 1 日执行节奏

在应用 EMA 与 3 日上限的风险降低后，令：

```text
risk_reduced_weight = min(current_target_weight, risk_ceiling)
```

再执行 1 日规则：

```text
if score_1d <= -2:
    proposed_weight = max(0, risk_reduced_weight - 0.5)

elif score_1d > 1:
    if risk_reduced_weight == 0 and entry_allowed is false:
        proposed_weight = 0
    else:
        proposed_weight = min(risk_reduced_weight + 0.5, risk_ceiling)

else:
    proposed_weight = risk_reduced_weight
```

因此：

- `score_1d <= -2` 会在 EMA/3 日已要求的降仓基础上再降低一个档位。
- `-2 < score_1d < -1` 禁止开仓和加仓，已有仓位只受 EMA/3 日上限降低。
- `[-1,1]` 保持，不主动增加风险。
- `score_1d > 1` 最多增加一个档位。
- 方向冲突时 3 日上限天然优先，因为任何结果都不得超过 `risk_ceiling`。
- 从 0% 到 100% 在任何单日都不可能发生。

若当天先发生风险上限下降，不允许同一天再反向加仓。

## 7. 最终仓位冷却

最终状态覆盖层独立记录最近一次成功的 `target_weight` 变化：

```text
days_since_transition =
    current_execution_ordinal - last_transition_ordinal - 1
```

若：

```text
proposed_weight > current_target_weight
and days_since_transition < 10
```

则本次增加风险被阻止，最终目标保持当前仓位。规则包括：

- 第一次开仓前没有历史转换，可正常开仓。
- 必须经过 10 个完整交易日，下一次增加风险才允许执行。
- 降仓和退出永远不受冷却限制。
- 每次成功改变最终目标仓位后重新开始计时，包括降仓。
- 因资金不足未完成的升级不更新目标状态或冷却日期。

EMA 独立目标自身已有原策略冷却；最终状态覆盖层冷却用于保证状态导致的实际风险恢复
同样满足 10 个完整交易日要求。

## 8. 成交与估值

复用现有 `Portfolio` 和三档仓位执行口径：

- 目标数量按开盘价、开盘前权益和 100 股整手向下取整。
- 买入价加入 `0.05%` 滑点，卖出价扣除 `0.05%` 滑点。
- 佣金为成交额 `0.03%`，每笔最低 5 元。
- 买入数量受现金约束，不能融资。
- 每个交易日收盘按因果连续收盘价估值。
- 收益、波动、Sharpe 和回撤从区间首日初始资金开始计算。
- 年化单边换手使用成交额绝对值合计的一半除以平均权益和区间年数。

## 9. 对照组

使用完全相同的回测区间和数据口径输出：

1. `regime_overlay`：本设计的 EMA + 状态覆盖策略。
2. `ema_only`：已验收 EMA 策略，从区间首日空仓开始。
3. `buy_hold`：区间首个收盘到末个收盘的无成本买入持有基线。

每组至少报告：

```text
total_return
annual_return
annual_volatility
sharpe
max_drawdown
annual_one_way_turnover
trade_count
```

组合策略额外报告：

- 0%/50%/100% 目标仓位天数和占比。
- 3 日上限压降次数。
- 1 日主动降档次数。
- 加仓冷却阻止次数。
- 总佣金和总滑点成本。
- 期末是否持仓及目标仓位。
- 最大回撤起止日期。

原设计风险约束仅作为报告检查：

```text
max_drawdown >= -35%
annual_one_way_turnover <= 8
```

不因检查失败删除或重跑结果，也不据此调整规则。

### 9.1 半年度统计

三组结果均按自然半年输出：

```text
H1 = 1 月 1 日至 6 月 30 日
H2 = 7 月 1 日至 12 月 31 日
```

首尾不完整区间也保留：

```text
2017-H2 = 2017-08-14 至 2017-12-31 的实际交易日
2026-H2 = 2026-07-01 至 2026-07-20 的实际交易日
```

半年度统计规则：

- 策略在整个回测区间只运行一次，跨半年持仓、成本、冷却和目标状态连续继承。
- 半年边界不平仓、不重新投入 100 万元、不重启状态机。
- 每段 `initial_equity` 使用该段首个交易日前一交易日的收盘权益；首段使用 100 万元
  初始资金。`final_equity` 使用该段最后交易日收盘权益。
- 每段 `total_return = final_equity / initial_equity - 1`，因此不会遗漏半年首个交易日
  的开盘成交成本和当日涨跌。
- 最大回撤峰值在该段内重新计算，但初始峰值包含 `initial_equity`。
- Sharpe 和年化波动率使用该段实际日收益；首个交易日收益相对 `initial_equity`
  计算，不伪造为 0。
- 成交次数、成本和换手只统计成交日期落在该半年内的交易。
- 不足完整半年的 2017-H2、2026-H2 明确标记 `is_partial_period=true`。

每个半年、每个对照组至少输出：

```text
period
period_start
period_end
is_partial_period
strategy
trading_days
initial_equity
final_equity
total_return
annual_return
annual_volatility
sharpe
max_drawdown
annual_one_way_turnover
trade_count
```

## 10. 代码边界

新增独立文件，不修改现有 EMA 与第一阶段预测行为：

```text
stock/etf/regime_overlay_strategy.py
stock/etf/run_regime_overlay_backtest.py
stock/etf/tests/test_regime_overlay_strategy.py
stock/etf/tests/test_run_regime_overlay_backtest.py
```

主要纯函数接口：

```text
map_regime_cap(score_3d) -> (cap, entry_allowed)
transition_overlay_weight(...) -> OverlayTransition
align_regime_predictions(predictions, bars) -> DataFrame
run_regime_overlay_backtest(...) -> RegimeOverlayResult
```

运行器负责：

- 下载并审计真实 Tushare 数据。
- 计算因果连续行情与 1 日/3 日预测。
- 构造已验收 EMA 独立目标路径。
- 截取回测区间并从空仓执行状态覆盖策略。
- 原子发布全部产物，失败时保留上一轮成功目录。

## 11. 输出

默认目录：

```text
stock/etf/output/20260727_chuangyeban_regime_overlay/
```

产物：

```text
signals.csv
trades.csv
positions.csv
equity_curve.csv
comparison.csv
semiannual_metrics.csv
summary.json
source_audit.json
```

另生成：

```text
stock/etf/20260727_chuangyeban_regime_overlay_results.md
```

结果文档必须列出组合、EMA 和买入持有对比，逐年收益、每半年完整指标、最大回撤区间、
换手、成本、仓位分布、信号诊断及是否满足回撤/换手约束。

## 12. 测试与验收

必须覆盖：

- 3 日分数 `-3/-2/-1/0/1/2/3` 的上限与开仓边界。
- 1 日分数 `-2/-1/1` 的闭开边界。
- EMA 或 3 日上限下降立即执行且不受冷却限制。
- 1 日趋势向下在风险压降后再降低一个档位。
- 3 日禁止开仓时，1 日上涨信号不能从 0% 开仓。
- 1 日上涨最多增加一个档位，禁止 0% 直接到 100%。
- 必须经过 10 个完整交易日才能再次增加风险。
- 每次成功降仓后冷却重新计时。
- 2017-08-14 使用上一实际交易日收盘预测，不使用 8 月 14 日收盘数据。
- 修改执行日及未来预测不改变该日开盘目标。
- 状态覆盖策略目标始终不超过 EMA 目标和 3 日上限。
- 成交数量为 100 股整数倍，资金、佣金和滑点可复算。
- 回测首日空仓、期末不强制平仓。
- 输出文件完整、JSON 无 `NaN/Infinity`、失败覆盖可回滚。
- 真实运行日期严格为 2017-08-14 至 2026-07-20。
- 半年度结果从 2017-H2 至 2026-H2 连续覆盖，不遗漏任何有权益记录的交易日。
- 半年边界不改变持仓、最终仓位状态或冷却状态。
- 2017-H2 和 2026-H2 标为部分区间，其余半年覆盖完整自然半年内的可用交易日。

完成后运行：

```text
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
```

所有设计、计划、代码和结果文档只提交到 `feature` 分支，不合并主分支。真实输出目录
作为本机可复现产物保留。
