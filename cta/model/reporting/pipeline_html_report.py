"""Bridge model pipeline OOT trades to cta.report.render HTML report."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from cta.report.render.html_report import HtmlReportConfig, write_html_report

logger = logging.getLogger(__name__)


def write_pipeline_oot_html_report(
    *,
    oot_trade_df: pd.DataFrame,
    out_dir: Path,
    title: str,
    periods_per_year: int,
    initial_capital: float,
) -> Path | None:
    """Render unified HTML report from OOT trade details.

    Returns created html path, or None when there are no valid OOT trades.
    """
    if oot_trade_df.empty or "datetime" not in oot_trade_df.columns:
        return None
    trades = oot_trade_df.copy()
    trades["datetime"] = pd.to_datetime(trades["datetime"], errors="coerce")
    trades = trades.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    if trades.empty:
        return None

    n = len(trades)
    _zero = pd.Series(0.0, index=trades.index)
    gross_pnl = pd.to_numeric(trades.get("gross_pnl", _zero), errors="coerce").fillna(0.0)
    net_pnl = pd.to_numeric(trades.get("net_pnl", _zero), errors="coerce").fillna(0.0)
    gross_ret_pct = pd.to_numeric(trades.get("gross_return_pct", _zero), errors="coerce").fillna(0.0)

    entry_price = np.ones(n, dtype=float)
    exit_price = entry_price * (1.0 + gross_ret_pct.to_numpy())
    trade_log = pd.DataFrame(
        {
            "entry_i": np.arange(n, dtype=int),
            "exit_i": np.arange(1, n + 1, dtype=int),
            "side": trades.get("side", pd.Series(["long"] * n)).astype(str).to_numpy(),
            "lots": np.ones(n, dtype=float),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_pnl": gross_pnl.to_numpy(),
            "cost": (gross_pnl - net_pnl).to_numpy(),
            "net_pnl": net_pnl.to_numpy(),
            "symbol": trades.get("symbol", pd.Series([""] * n)).astype(str).to_numpy(),
        }
    )

    equity = [float(initial_capital)]
    for v in net_pnl.to_numpy():
        equity.append(float(equity[-1] + float(v)))
    equity_series = pd.Series(equity, name="equity")

    dt0 = pd.Timestamp(trades["datetime"].iloc[0]) - pd.Timedelta(minutes=1)
    bars_dt = [dt0] + trades["datetime"].tolist()
    bars = pd.DataFrame({"datetime": pd.to_datetime(bars_dt), "volume": np.ones(len(bars_dt), dtype=float)})

    cfg = HtmlReportConfig(
        out_dir=str(out_dir),
        title=title,
        periods_per_year=int(max(1, periods_per_year)),
    )
    try:
        html_path = write_html_report(
            trade_log=trade_log,
            equity=equity_series,
            bars=bars,
            dates=bars["datetime"].tolist(),
            cfg=cfg,
        )
    except Exception:
        logger.exception("failed to render OOT html report")
        return None
    return Path(html_path)


__all__ = ["write_pipeline_oot_html_report"]

