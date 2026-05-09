"""CtaTemplate 子类的回测包装器。

提供两条路径：

1. ``run_via_event_driven``：从 ``LegacyCtaAdapter`` 子类抽出 inner v1 策略，
   用 ``cta.skills.data_backtest.event_driven_backtest.run_backtest`` 跑批量回测。
   - 优点：不依赖 vnpy_ctabacktester，速度快，是 M2 主路径
   - 用途：策略研究 + parity_check 一致性校验
2. ``run_via_vnpy_ctabacktester``：直接调用 ``vnpy_ctabacktester.BacktestingEngine``。
   - 优点：与 SimNow / 实盘共享同一 cta_engine 实现，最大化对齐
   - 用途：进入仿真前最终验证

两条路径都会在 ``out_dir`` 下生成 HTML 综合报告（复用 ``cta.report.render``）。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from cta.report.render.html_report import HtmlReportConfig, write_html_report
from cta.report.render.metrics import extended_metrics
from cta.skills.data_backtest.event_driven_backtest import EngineConfig, run_backtest


@dataclass
class BacktesterResult:
    trade_log: pd.DataFrame
    equity_curve: pd.Series
    stats: dict[str, Any]
    report_path: str


class _StubEngine:
    """运行 prepare_frame / make_inner 时给 CtaTemplate 子类一个最小可用的 engine 桩。
    回测在 ``run_backtest`` 内部完成，不真的发单到 engine。"""

    def __init__(self, pricetick: float = 1.0, size: int = 1) -> None:
        self._pricetick = pricetick
        self._size = size

    def send_order(self, *a, **kw): return []
    def cancel_order(self, *a, **kw): return None
    def cancel_all(self, *a, **kw): return None
    def write_log(self, msg, strategy=None): return None
    def get_pricetick(self, strategy): return self._pricetick
    def get_size(self, strategy): return self._size
    def get_engine_type(self):
        from vnpy_ctastrategy.base import EngineType
        return EngineType.BACKTESTING


def _instantiate(strategy_class: type, vt_symbol: str, setting: dict) -> Any:
    """以 stub engine 实例化 CtaTemplate 子类，返回实例。"""
    eng = _StubEngine()
    return strategy_class(eng, "tmp", vt_symbol, dict(setting or {}))


def _extract_trade_log_from_engine(engine: Any) -> pd.DataFrame:
    """从 vnpy_ctabacktester engine 中提取 trades，返回 DataFrame；缺字段自动跳过。"""
    raw = getattr(engine, "trades", None)
    if not raw:
        return pd.DataFrame()
    items = raw.values() if hasattr(raw, "values") else raw
    rows: list[dict] = []
    for t in items:
        rows.append({
            "datetime": getattr(t, "datetime", None),
            "side": "long" if str(getattr(t, "direction", "")).lower().endswith("long") else "short",
            "lots": float(getattr(t, "volume", 0.0) or 0.0),
            "price": float(getattr(t, "price", 0.0) or 0.0),
        })
    return pd.DataFrame(rows)


def run_via_event_driven(
    *,
    strategy_class: type,
    vt_symbol: str,
    setting: dict | None,
    bars: pd.DataFrame,
    engine_cfg: EngineConfig | None = None,
    cost_fn: Any | None = None,
    out_dir: str,
    title: str = "Backtest",
    monte_carlo_iter: int = 1000,
) -> BacktesterResult:
    """用 ``LegacyCtaAdapter`` 子类的 prepare_frame + inner，走 event_driven 引擎回测。

    要求 ``strategy_class`` 是 ``cta.strategy.cta_adapter.LegacyCtaAdapter`` 的子类。
    ``cost_fn=None`` 默认与 v1 ``run_backtest`` 默认一致；
    若需要计入手续费/滑点，显式传入 ``cta.skills.data_backtest.transaction_cost.estimate_cost``。
    """
    setting = dict(setting or {})
    instance = _instantiate(strategy_class, vt_symbol, setting)
    instance.on_init()

    if not hasattr(instance, "prepare_frame") or not hasattr(instance, "make_inner"):
        raise TypeError(
            f"strategy_class={strategy_class.__name__} must be a LegacyCtaAdapter subclass"
        )

    frame = instance.prepare_frame(bars.copy())
    inner = instance.make_inner(frame)

    cfg = engine_cfg if engine_cfg is not None else EngineConfig()
    if cost_fn is not None and cfg.cost_fn is None:
        import dataclasses
        cfg = dataclasses.replace(cfg, cost_fn=cost_fn)

    out = run_backtest(frame, inner, cfg)
    trade_log: pd.DataFrame = out["trade_log"]
    equity: pd.Series = out["equity_curve"]
    dates = pd.to_datetime(frame["datetime"]).tolist() if "datetime" in frame.columns else None
    stats = extended_metrics(trade_log, equity, dates=dates)

    report_cfg = HtmlReportConfig(
        out_dir=out_dir,
        title=title,
        monte_carlo_iter=monte_carlo_iter,
    )
    report_path = write_html_report(
        trade_log=trade_log, equity=equity, bars=frame, dates=dates, cfg=report_cfg
    )
    return BacktesterResult(
        trade_log=trade_log, equity_curve=equity, stats=stats, report_path=report_path
    )


def run_via_vnpy_ctabacktester(
    *,
    strategy_class: type,
    vt_symbol: str,
    setting: dict | None,
    interval: str = "d",
    start: datetime,
    end: datetime,
    rate: float = 0.0001,
    slippage: float = 1.0,
    size: int = 10,
    pricetick: float = 1.0,
    capital: float = 1_000_000.0,
    out_dir: str | None = None,
    title: str = "Backtest",
) -> BacktesterResult:
    """调用真实 ``vnpy_ctabacktester.BacktestingEngine``。

    数据需事先在 vnpy 的数据库里（参见 vnpy 文档配置 SETTINGS["database.*"]）。
    未安装 vnpy_ctabacktester 时直接抛 ``ImportError``。
    """
    if importlib.util.find_spec("vnpy_ctabacktester") is None:
        raise ImportError(
            "vnpy_ctabacktester 未安装；pip install vnpy_ctabacktester 后重试，"
            "或改用 run_via_event_driven。"
        )

    from vnpy.trader.constant import Interval  # type: ignore
    from vnpy_ctabacktester.engine import BacktestingEngine  # type: ignore

    interval_map = {
        "d": Interval.DAILY, "day": Interval.DAILY,
        "1m": Interval.MINUTE, "minute": Interval.MINUTE,
        "1h": Interval.HOUR, "hour": Interval.HOUR,
    }
    itv = interval_map.get(str(interval).lower(), Interval.DAILY)

    engine = BacktestingEngine()
    engine.set_parameters(
        vt_symbol=vt_symbol,
        interval=itv,
        start=start, end=end,
        rate=rate, slippage=slippage, size=size, pricetick=pricetick,
        capital=capital,
    )
    engine.add_strategy(strategy_class, dict(setting or {}))
    engine.load_data()
    engine.run_backtesting()
    daily_df = engine.calculate_result()
    stats = engine.calculate_statistics(output=False) or {}

    # vnpy 的 daily_df 形态与 event_driven 不同：用其中的 net_pnl 累计当 equity
    equity = pd.Series(daily_df.get("balance", []), name="equity")
    trade_log = _extract_trade_log_from_engine(engine)

    report_path = ""
    if out_dir:
        report_cfg = HtmlReportConfig(out_dir=out_dir, title=title)
        report_path = write_html_report(
            trade_log=trade_log, equity=equity,
            bars=pd.DataFrame({"datetime": daily_df.index}) if hasattr(daily_df, "index") else pd.DataFrame(),
            cfg=report_cfg,
        )
    return BacktesterResult(
        trade_log=trade_log, equity_curve=equity, stats=dict(stats), report_path=report_path
    )


__all__ = [
    "BacktesterResult",
    "run_via_event_driven",
    "run_via_vnpy_ctabacktester",
]
