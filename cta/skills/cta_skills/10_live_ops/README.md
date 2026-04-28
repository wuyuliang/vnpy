# 10 实盘执行与运维 / Live Ops

> 本章目标：回测通过之后，策略**如何稳定地上线、下单、监控、复盘、迭代**。回测不是终点，实盘才是。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_signal_to_order.md](01_signal_to_order.md) | 信号到订单的转换 |
| 02 | [02_order_execution.md](02_order_execution.md) | 订单执行与撮合对接 |
| 03 | [03_monitoring_and_alerting.md](03_monitoring_and_alerting.md) | 监控与告警 |
| 04 | [04_daily_review.md](04_daily_review.md) | 每日复盘 |
| 05 | [05_strategy_iteration_loop.md](05_strategy_iteration_loop.md) | 策略迭代闭环 |

## 推荐阅读顺序

`01 → 02 → 03 → 04 → 05`。生成订单 → 下单 → 观察 → 复盘 → 迭代。

## 上线前 checklist

1. 回测 ≥ 3 年数据 + ≥ 1 年 OOS
2. 交易成本按上限扣（`08/03`）
3. dry-run ≥ 2 周（纸面下单）
4. 接入监控 + 告警
5. 准备下线开关（kill switch）

## 已有资产

| 资产 | 作用 |
|------|------|
| `cta/strategy/brooks/core/` | 信号 / 风控参考实现 |
| `cta/strategy/brooks/backtest/` | 引擎也可作为 dry-run |
| `cta/config/futures_meta.py` | 品种参数（实盘同源） |
