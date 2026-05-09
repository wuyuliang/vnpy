"""HTML/Plotly 综合评估报告子模块。

子模块清单
---------
- ``metrics``           扩展指标（Sortino / 最长水下 / 月度 PnL 等）
- ``plots``             plotly 图表（净值/回撤/月度热力/敞口/交易直方图）
- ``monte_carlo``       block bootstrap 重抽样
- ``capacity``          基于 trade_log 的容量曲线
- ``factor_analysis``   Alphalens 因子分析（可选依赖）
- ``html_report``       综合 HTML 报告入口
"""
from cta.report.render.capacity import capacity_curve
from cta.report.render.factor_analysis import ALPHALENS_AVAILABLE, run_alphalens
from cta.report.render.html_report import HtmlReportConfig, write_html_report
from cta.report.render.metrics import extended_metrics
from cta.report.render.monte_carlo import MonteCarloResult, block_bootstrap

__all__ = [
    "ALPHALENS_AVAILABLE",
    "HtmlReportConfig",
    "MonteCarloResult",
    "block_bootstrap",
    "capacity_curve",
    "extended_metrics",
    "run_alphalens",
    "write_html_report",
]
