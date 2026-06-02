"""Walk-forward diagnostics for OOT trade tables."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def _executed(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    if "execution_status" in trades.columns:
        mask = trades["execution_status"].astype(str).str.lower().eq("executed")
        if bool(mask.any()):
            return trades.loc[mask].copy()
    return trades.copy()


def _annualized(ret: float, start: pd.Timestamp, end: pd.Timestamp) -> float:
    days = max(1, int((end.normalize() - start.normalize()).days))
    base = 1.0 + float(ret)
    if base <= 0.0:
        return float("nan")
    return float(base ** (365.0 / days) - 1.0)


def _window_row(
    sub: pd.DataFrame,
    *,
    window_id: int,
    left: pd.Timestamp,
    right_inclusive: pd.Timestamp,
    initial_capital: float,
    row_type: str = "window",
) -> tuple[dict[str, Any], float]:
    """Build one window summary row + its return (helper, shared by in-sample / forward windows)."""
    pnl = pd.to_numeric(sub.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    ret = float(pnl.sum() / float(initial_capital)) if initial_capital > 0 else float("nan")
    row = {
        "row_type": row_type,
        "window_id": window_id,
        "start_date": str(pd.Timestamp(left).date()),
        "end_date": str(pd.Timestamp(right_inclusive).date()),
        "trade_count": int(len(sub)),
        "net_pnl": float(pnl.sum()),
        "total_return_pct": ret,
        "annualized_return_pct": _annualized(ret, pd.Timestamp(left), pd.Timestamp(right_inclusive) + pd.Timedelta(days=1)),
        "win_window_ratio": float("nan"),
        "sharpe_std_over_mean": float("nan"),
        "single_segment_overfit_warning": False,
    }
    return row, ret


def build_walk_forward_summary(
    trades: pd.DataFrame,
    *,
    n_windows: int = 4,
    initial_capital: float = 10_000_000.0,
    forward_window_start: str | None = "2026-01-01",
    forward_window_end: str | None = "2026-05-31",
) -> pd.DataFrame:
    """Split the OOT period into time windows and summarize stability.

    in-sample（``forward_window_start`` 之前）按 ``n_windows`` 自动等分；额外显式追加一个
    ``[forward_window_start, forward_window_end]`` 前瞻窗（OOS）。前瞻窗即使当前无成交也会输出占位行
    （trade_count=0），待 2026 数据补入后自动填充。过拟合稳定性统计（win_window_ratio / std_over_mean /
    warning）只基于 in-sample 窗，前瞻窗不参与，避免空窗污染 in-sample 评估。
    传 ``forward_window_start=None`` 可回退到纯自动等分（旧行为）。
    """
    n_windows = max(1, int(n_windows))
    ex = _executed(trades)
    if ex.empty:
        return pd.DataFrame(
            [
                {
                    "row_type": "aggregate",
                    "window_id": -1,
                    "trade_count": 0,
                    "total_return_pct": 0.0,
                    "annualized_return_pct": float("nan"),
                    "win_window_ratio": float("nan"),
                    "sharpe_std_over_mean": float("nan"),
                    "single_segment_overfit_warning": False,
                }
            ]
        )
    dt_col = "exit_datetime" if "exit_datetime" in ex.columns else "datetime"
    ex = ex.copy()
    ex["_dt"] = pd.to_datetime(ex.get(dt_col, pd.NaT), errors="coerce")
    ex = ex.dropna(subset=["_dt"]).sort_values("_dt")
    if ex.empty:
        return build_walk_forward_summary(pd.DataFrame(), n_windows=n_windows, initial_capital=initial_capital)

    fwd_start = pd.Timestamp(forward_window_start) if forward_window_start else None
    fwd_end = pd.Timestamp(forward_window_end) if forward_window_end else None
    # in-sample = 前瞻窗起点之前的成交；前瞻窗 = [fwd_start, fwd_end]
    insample = ex.loc[ex["_dt"] < fwd_start] if fwd_start is not None else ex

    rows: list[dict[str, Any]] = []
    returns: list[float] = []
    if not insample.empty:
        start = pd.Timestamp(insample["_dt"].min())
        end = pd.Timestamp(insample["_dt"].max())
        if start == end:
            edges = [start, end + pd.Timedelta(days=1)]
            n_eff = 1
        else:
            n_eff = n_windows
            edges = list(pd.date_range(start=start, end=end, periods=n_eff + 1))
            edges[-1] = end + pd.Timedelta(nanoseconds=1)
        for i in range(n_eff):
            left = pd.Timestamp(edges[i])
            right = pd.Timestamp(edges[i + 1])
            sub = insample.loc[(insample["_dt"] >= left) & (insample["_dt"] < right)]
            row, ret = _window_row(
                sub, window_id=i, left=left,
                right_inclusive=right - pd.Timedelta(nanoseconds=1),
                initial_capital=initial_capital,
            )
            rows.append(row)
            returns.append(ret)

    # 显式前瞻窗（OOS）：[fwd_start, fwd_end]，闭区间；不计入 in-sample 稳定性统计
    if fwd_start is not None and fwd_end is not None:
        fwd_sub = ex.loc[(ex["_dt"] >= fwd_start) & (ex["_dt"] <= fwd_end + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1))]
        fwd_row, _ = _window_row(
            fwd_sub, window_id=len(rows), left=fwd_start, right_inclusive=fwd_end,
            initial_capital=initial_capital, row_type="forward_window",
        )
        rows.append(fwd_row)

    start = pd.Timestamp(ex["_dt"].min())
    end = pd.Timestamp(ex["_dt"].max())
    ret_s = pd.Series(returns, dtype=float)
    mean_ret = float(ret_s.mean()) if len(ret_s) else float("nan")
    std_ret = float(ret_s.std(ddof=1)) if len(ret_s) >= 2 else float("nan")
    ratio = float((ret_s > 0).mean()) if len(ret_s) else float("nan")
    std_over_mean = abs(std_ret / mean_ret) if math.isfinite(std_ret) and abs(mean_ret) > 1e-12 else float("nan")
    warning = bool((math.isfinite(ratio) and ratio < 0.75) or (math.isfinite(std_over_mean) and std_over_mean > 1.0))
    rows.append(
        {
            "row_type": "aggregate",
            "window_id": -1,
            "start_date": str(start.date()),
            "end_date": str(end.date()),
            "trade_count": int(len(ex)),
            "net_pnl": float(pd.to_numeric(ex.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()),
            "total_return_pct": float(ret_s.sum()) if len(ret_s) else 0.0,
            "annualized_return_pct": float("nan"),
            "win_window_ratio": ratio,
            "sharpe_std_over_mean": std_over_mean,
            "single_segment_overfit_warning": warning,
        }
    )
    return pd.DataFrame(rows)


__all__ = ["build_walk_forward_summary"]
