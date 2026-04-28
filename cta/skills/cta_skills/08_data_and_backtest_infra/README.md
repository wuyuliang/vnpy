# 08 数据工程与回测基础设施 / Data & Backtest Infra

> 本章目标：把"回测 / 数据管道"的工程化写清楚。已有实现在 `cta/data_code/`、`cta/feature/`、`cta/strategy/brooks/backtest/`；本章给出迁移式的标准答案。

## 子技能

| # | 文件 | 主题 |
|---|------|------|
| 01 | [01_continuous_contract.md](01_continuous_contract.md) | 连续合约构造 |
| 02 | [02_rollover_rules.md](02_rollover_rules.md) | 展期规则（主力切换） |
| 03 | [03_transaction_cost_model.md](03_transaction_cost_model.md) | 交易成本模型（手续费 / 滑点 / 冲击） |
| 04 | [04_event_driven_backtest.md](04_event_driven_backtest.md) | 事件驱动回测引擎 |
| 05 | [05_trade_log_and_evaluation.md](05_trade_log_and_evaluation.md) | 成交日志与评估报告 |

## 推荐阅读顺序

`01 → 02 → 03 → 04 → 05`。数据 → 成本 → 引擎 → 报告。

## 已有资产

| 资产 | 作用 |
|------|------|
| `cta/data_code/futures_downloader.py` | 下载原始行情 |
| `cta/data_code/download_all.py` | 批量下载 + 跟踪 |
| `cta/feature/run_all_features.py` | 特征批量生成 |
| `cta/strategy/brooks/backtest/engine.py` | 事件驱动回测引擎 |
| `cta/strategy/brooks/backtest/reporter.py` | 报告与 summary |
| `cta/strategy/brooks/core/trade_log.py` | 成交日志格式 |
| `cta/config/futures_meta.py` | 合约元数据 |
