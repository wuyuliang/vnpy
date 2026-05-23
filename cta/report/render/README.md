# cta/report/render/

## 主要做什么

**报告渲染层**：把 [cta/backtest/<run_tag>/](../backtest/) 下的 csv / json 数据渲染成 markdown / HTML / 图表的离线工具集。被 [cta/model/reporting/pipeline_html_report.py](../../model/reporting/pipeline_html_report.py) / [cta/skills/live_ops/daily_review.py](../../skills/live_ops/daily_review.py) 等调用。

## 关键文件

| 文件 | 作用 |
|---|---|
| [html_report.py](html_report.py) | HTML 报告渲染：Jinja2 模板 + plot 嵌入 |
| [metrics.py](metrics.py) | 指标计算 / 格式化：sharpe / calmar / win_rate / 月度表 |
| [plots.py](plots.py) | matplotlib / plotly 通用画图 helper（资金曲线、回撤、月度热图等） |
| [capacity.py](capacity.py) | 容量分析：估算策略可承载的最大资金量 |
| [factor_analysis.py](factor_analysis.py) | 因子分析报表（IC / 分组累计收益 / 多空对比） |
| [monte_carlo.py](monte_carlo.py) | Monte Carlo 模拟：基于交易序列重采样估期望与置信区间 |

## 详细过程

```
[输入] cta/backtest/<run_tag>/*_oot_*.csv + predictions.csv + summary.csv
    │
    ▼
metrics.aggregate(...)            → 标量指标
plots.equity_curve(...)            → png / svg
plots.drawdown(...)
factor_analysis.run(...)           → 分组累计收益、IC 表
monte_carlo.simulate(...)          → 置信区间
capacity.estimate(...)             → 资金容量
    │
    ▼
html_report.render(template, data) → cta/backtest/<run_tag>/<run_tag>_model_report.md / .html
```

## 注意事项

- **离线工具**：本目录代码不上 OOT / sim / live 主路径，只渲染结果。
- **不修改输入**：渲染只读，禁止改写 `cta/backtest/<run_tag>/*.csv`。
- **plot 依赖**：matplotlib / plotly 不在最小依赖里；CI 跑前需 `pip install matplotlib plotly`。
- **大数据 sample**：渲染 30+ 万笔交易的 png 会卡，超过 10k 行先 sample。
- **测试**：跑 `pytest cta/report/render/tests/ -v`。
