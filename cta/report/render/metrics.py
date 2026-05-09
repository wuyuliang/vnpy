"""扩展评估指标。

在 ``cta.skills.data_backtest.trade_evaluation.summarize_trades`` 之上补齐：
    - sortino       下行波动调整收益
    - calmar        年化收益 / 最大回撤
    - mdd_duration  最长水下天数（以 bar 为单位）
    - mdd_recovery  最大回撤恢复 bar 数（未恢复返回 None）
    - win_streak / lose_streak  最长连胜 / 连亏交易数
    - avg_holding_bars
    - turnover_per_bar  每根 bar 平均成交手数
    - monthly_pnl   {YYYY-MM: net_pnl} （需要传 dates）

输入 schema 与现有 trade_evaluation 一致：
    trade_log: entry_i, exit_i, side, lots, entry_price, exit_price, gross_pnl, cost, net_pnl
    equity   : pd.Series (与 bars 长度一致；RangeIndex 即可)
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from cta.skills.data_backtest.trade_evaluation import summarize_trades


def _drawdown_series(equity: pd.Series) -> pd.Series:
    eq = equity.astype(float)
    peak = eq.cummax()
    return (peak - eq).clip(lower=0.0)


def _max_dd_recovery(equity: pd.Series) -> tuple[int, int | None]:
    """Return (max water-under-peak duration, bars to recover from MDD).

    duration 用 bar 计；未恢复时 recovery 为 None。
    """
    eq = equity.astype(float).reset_index(drop=True)
    if len(eq) == 0:
        return 0, None

    peak = eq.cummax()
    underwater = eq < peak
    # 最长水下段
    longest = 0
    cur = 0
    for flag in underwater:
        if flag:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0

    # 最大回撤恢复 bar 数
    dd = peak - eq
    if dd.max() <= 0:
        return longest, 0
    trough_i = int(dd.idxmax())
    peak_at_trough = float(peak.iloc[trough_i])
    after = eq.iloc[trough_i:]
    recovered = after[after >= peak_at_trough]
    if recovered.empty:
        return longest, None
    recovery = int(recovered.index[0]) - trough_i
    return longest, recovery


def _streaks(pnl: pd.Series) -> tuple[int, int]:
    win = lose = cur_w = cur_l = 0
    for v in pnl:
        if v > 0:
            cur_w += 1
            cur_l = 0
            win = max(win, cur_w)
        elif v < 0:
            cur_l += 1
            cur_w = 0
            lose = max(lose, cur_l)
        else:
            cur_w = cur_l = 0
    return win, lose


def _sortino(rets: pd.Series, periods_per_year: int) -> float:
    arr = rets.astype(float).to_numpy()
    if arr.size == 0:
        return 0.0
    downside = arr[arr < 0]
    if downside.size == 0:
        return 0.0
    dd_std = float(np.sqrt((downside ** 2).mean()))
    if dd_std == 0:
        return 0.0
    return float(arr.mean() / dd_std * np.sqrt(periods_per_year))


def _monthly_pnl(
    equity: pd.Series, dates: Sequence[pd.Timestamp] | None
) -> dict[str, float]:
    if dates is None:
        return {}
    if len(dates) != len(equity):
        raise ValueError(f"dates 长度 {len(dates)} 与 equity {len(equity)} 不一致")
    rets = equity.astype(float).diff().fillna(0.0).to_numpy()
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    s = pd.Series(rets, index=idx)
    monthly = s.resample("ME").sum()
    return {ts.strftime("%Y-%m"): float(v) for ts, v in monthly.items()}


def extended_metrics(
    trade_log: pd.DataFrame,
    equity: pd.Series,
    *,
    periods_per_year: int = 252,
    dates: Sequence[pd.Timestamp] | None = None,
) -> dict:
    """Return base + extended metrics dictionary."""
    base = summarize_trades(trade_log, equity, periods_per_year=periods_per_year)

    eq = equity.astype(float).reset_index(drop=True)
    rets = eq.diff().fillna(0.0)
    sortino = _sortino(rets, periods_per_year)
    mdd_dur, mdd_rec = _max_dd_recovery(eq)

    pnl = (
        trade_log["net_pnl"].astype(float)
        if "net_pnl" in trade_log.columns
        else trade_log.get("gross_pnl", pd.Series(dtype=float)).astype(float)
    )
    win_streak, lose_streak = _streaks(pnl)

    holding = pd.Series(dtype=float)
    if {"entry_i", "exit_i"}.issubset(trade_log.columns) and len(trade_log):
        holding = (trade_log["exit_i"].astype(float) - trade_log["entry_i"].astype(float)).clip(
            lower=0.0
        )
    avg_holding = float(holding.mean()) if len(holding) else 0.0

    n_bars = max(len(eq) - 1, 1)
    total_lots = float(trade_log["lots"].astype(float).sum()) if "lots" in trade_log.columns else 0.0
    turnover_per_bar = total_lots * 2.0 / n_bars  # 双边（开+平）

    monthly = _monthly_pnl(eq, dates)

    base.update(
        {
            "sortino": sortino,
            "mdd_duration": int(mdd_dur),
            "mdd_recovery": mdd_rec,  # int | None
            "win_streak": int(win_streak),
            "lose_streak": int(lose_streak),
            "avg_holding_bars": avg_holding,
            "turnover_per_bar": turnover_per_bar,
            "monthly_pnl": monthly,
        }
    )
    return base


__all__ = ["extended_metrics"]
