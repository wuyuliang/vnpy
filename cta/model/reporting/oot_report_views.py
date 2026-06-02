"""Subview builders for OOT report writer."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from cta.model.oot.block_reasons import BR_LOG_EXECUTED_SENTINEL

from cta.model.reporting.group_pool_aggregate import AggregateConfig, build_aggregate_reports

_DAILY_DIST_COLUMNS = (
    "date",
    "trade_count",
    "position_notional_mean_pct",
    "position_notional_p50_pct",
    "position_notional_p90_pct",
    "position_notional_max_pct",
    "margin_used_after_trade_mean_pct",
    "margin_used_after_trade_p50_pct",
    "margin_used_after_trade_p90_pct",
    "margin_used_after_trade_max_pct",
    "eod_position_notional_pct",
    "eod_margin_used_after_trade_pct",
    "day_net_pnl",
)
_MONTHLY_DIST_COLUMNS = (
    "month",
    "trading_days",
    "total_trade_count",
    "avg_trade_count_per_day",
    "p50_trade_count_per_day",
    "p90_trade_count_per_day",
    "avg_eod_position_notional_pct",
    "p90_eod_position_notional_pct",
    "max_eod_position_notional_pct",
    "avg_eod_margin_used_pct",
    "max_eod_margin_used_pct",
    "month_net_pnl",
)
_WEEKDAY_DIST_COLUMNS = (
    "weekday_num",
    "weekday",
    "days",
    "avg_trade_count",
    "p90_trade_count",
    "avg_eod_position_notional_pct",
    "p90_eod_position_notional_pct",
    "avg_eod_margin_used_pct",
    "avg_day_net_pnl",
)


def _annualized_return(total_return_pct: float, start_ts: pd.Timestamp | None, end_ts: pd.Timestamp | None) -> float:
    if start_ts is None or end_ts is None or not np.isfinite(float(total_return_pct)):
        return float("nan")
    days = max(1, int((end_ts.normalize() - start_ts.normalize()).days))
    base = 1.0 + float(total_return_pct)
    if base <= 0.0:
        return float("nan")
    return float(base ** (365.0 / float(days)) - 1.0)


def _signal_exit_datetime(df: pd.DataFrame) -> pd.Series:
    idx = df.index
    for col in ("final_exit_datetime", "exit_datetime", "entry_fill_datetime", "datetime"):
        ts = pd.to_datetime(df.get(col, pd.Series(pd.NaT, index=idx)), errors="coerce")
        if ts.notna().any():
            return ts
    return pd.Series(pd.NaT, index=idx)


def _signal_max_drawdown_pct(pnl: pd.Series, capital: float) -> float:
    p = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    if p.empty or capital <= 0:
        return float("nan")
    equity = float(capital) + p.cumsum()
    peak = equity.cummax().replace(0, np.nan)
    dd = (equity - peak) / peak
    return float(dd.min()) if len(dd) else float("nan")


def _signal_monthly_sharpe(pnl: pd.Series, exit_dt: pd.Series, capital: float, rf_annual: float) -> float:
    p = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    dt = pd.to_datetime(exit_dt, errors="coerce")
    if p.empty or capital <= 0:
        return float("nan")
    work = pd.DataFrame({"pnl": p, "dt": dt}).dropna(subset=["dt"])
    if work.empty:
        return float("nan")
    work["month"] = work["dt"].dt.to_period("M").dt.to_timestamp()
    monthly = work.groupby("month", as_index=False)["pnl"].sum()
    ret = pd.to_numeric(monthly["pnl"], errors="coerce").fillna(0.0) / float(capital)
    if len(ret) < 2:
        return float("nan")
    excess = ret - float(rf_annual) / 12.0
    std = float(excess.std(ddof=1))
    if not np.isfinite(std) or std <= 1e-9:
        return float("nan")
    return float(excess.mean() / std * np.sqrt(12.0))


def _top_positive_share(values: pd.Series, top_n: int) -> float:
    v = pd.to_numeric(values, errors="coerce").fillna(0.0)
    pos = v[v > 0.0]
    total = float(pos.sum())
    if total <= 0.0:
        return float("nan")
    return float(pos.nlargest(int(top_n)).sum() / total)


def _executed_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=bool)
    if "execution_status" in df.columns:
        s = df["execution_status"].astype(str).str.lower()
        mask = s.eq("executed")
        if bool(mask.any()):
            return mask
    if "net_pnl" in df.columns:
        return pd.to_numeric(df["net_pnl"], errors="coerce").notna()
    return pd.Series([False] * len(df), index=df.index)


def _safe_pct(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    num = pd.to_numeric(numerator, errors="coerce")
    den = pd.to_numeric(denominator, errors="coerce")
    out = pd.Series(np.nan, index=num.index, dtype=float)
    mask = den > 0
    out.loc[mask] = (num.loc[mask] / den.loc[mask]) * 100.0
    return out


def _trade_datetime_series(df: pd.DataFrame) -> pd.Series:
    idx = df.index
    ts = pd.to_datetime(df.get("entry_fill_datetime", pd.Series(pd.NaT, index=idx)), errors="coerce")
    for col in ("datetime", "entry_datetime", "signal_datetime"):
        ts = ts.fillna(pd.to_datetime(df.get(col, pd.Series(pd.NaT, index=idx)), errors="coerce"))
    return ts


def _write_empty_time_distributions(report_dir: Path) -> None:
    ddir = report_dir / "09_diagnostics"
    pd.DataFrame(columns=_DAILY_DIST_COLUMNS).to_csv(
        ddir / "daily_trade_position_distribution.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(columns=_MONTHLY_DIST_COLUMNS).to_csv(
        ddir / "monthly_trade_position_distribution.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(columns=_WEEKDAY_DIST_COLUMNS).to_csv(
        ddir / "weekday_trade_position_distribution.csv",
        index=False,
        encoding="utf-8-sig",
    )


def write_trade_position_time_distributions(report_dir: Path, trades: pd.DataFrame) -> None:
    """Write daily/monthly/weekday time distributions for executed trades.

    统计口径：
    - 仅统计 execution_status=executed 的记录；
    - trade time 使用 entry_fill_datetime，缺失时回退 datetime/entry_datetime/signal_datetime；
    - 仓位字段统一输出为占当时权益百分比（*_pct）。
    """
    if trades.empty:
        _write_empty_time_distributions(report_dir)
        return

    ex = trades.loc[_executed_mask(trades)].copy()
    if ex.empty:
        _write_empty_time_distributions(report_dir)
        return

    ex["_trade_dt"] = _trade_datetime_series(ex)
    ex = ex.loc[ex["_trade_dt"].notna()].copy()
    if ex.empty:
        _write_empty_time_distributions(report_dir)
        return

    ex["_trade_dt"] = pd.to_datetime(ex["_trade_dt"], errors="coerce")
    ex["_date"] = ex["_trade_dt"].dt.normalize()
    ex["_equity_before"] = pd.to_numeric(ex.get("equity_before", pd.Series(np.nan, index=ex.index)), errors="coerce")
    ex["_equity_after"] = pd.to_numeric(ex.get("equity_after", pd.Series(np.nan, index=ex.index)), errors="coerce")
    ex["_net_pnl"] = pd.to_numeric(ex.get("net_pnl", pd.Series(0.0, index=ex.index)), errors="coerce").fillna(0.0)
    pos_after = pd.to_numeric(
        ex.get("position_notional_after_trade", pd.Series(np.nan, index=ex.index)),
        errors="coerce",
    )
    if pos_after.notna().sum() == 0:
        pos_after = pd.to_numeric(ex.get("position_notional", pd.Series(np.nan, index=ex.index)), errors="coerce")
    ex["_position_notional_after"] = pos_after

    margin_before = pd.to_numeric(
        ex.get("margin_used_before_entry", pd.Series(np.nan, index=ex.index)),
        errors="coerce",
    )
    entry_margin = pd.to_numeric(ex.get("entry_margin", pd.Series(np.nan, index=ex.index)), errors="coerce")
    margin_after = margin_before.fillna(0.0) + entry_margin.fillna(0.0)
    margin_after.loc[~(margin_before.notna() | entry_margin.notna())] = np.nan
    ex["_margin_used_after_trade"] = margin_after

    ex["_position_notional_pct"] = _safe_pct(ex["_position_notional_after"], ex["_equity_before"])
    ex["_margin_used_after_trade_pct"] = _safe_pct(ex["_margin_used_after_trade"], ex["_equity_before"])
    ex = ex.sort_values("_trade_dt").reset_index(drop=True)

    daily_rows: list[dict[str, Any]] = []
    for date, group in ex.groupby("_date", sort=True):
        g = group.sort_values("_trade_dt")
        pos = pd.to_numeric(g["_position_notional_pct"], errors="coerce").dropna()
        mar = pd.to_numeric(g["_margin_used_after_trade_pct"], errors="coerce").dropna()
        eod = g.iloc[-1]
        eq_after = pd.to_numeric(pd.Series([eod.get("_equity_after", np.nan)]), errors="coerce").iloc[0]
        eq_before = pd.to_numeric(pd.Series([eod.get("_equity_before", np.nan)]), errors="coerce").iloc[0]
        eod_equity = eq_after if np.isfinite(eq_after) and eq_after > 0 else eq_before
        eod_pos = pd.to_numeric(pd.Series([eod.get("_position_notional_after", np.nan)]), errors="coerce").iloc[0]
        eod_mar = pd.to_numeric(pd.Series([eod.get("_margin_used_after_trade", np.nan)]), errors="coerce").iloc[0]
        eod_pos_pct = float(eod_pos / eod_equity * 100.0) if np.isfinite(eod_pos) and np.isfinite(eod_equity) and eod_equity > 0 else float("nan")
        eod_mar_pct = float(eod_mar / eod_equity * 100.0) if np.isfinite(eod_mar) and np.isfinite(eod_equity) and eod_equity > 0 else float("nan")
        daily_rows.append(
            {
                "date": date,
                "trade_count": int(len(g)),
                "position_notional_mean_pct": float(pos.mean()) if len(pos) else float("nan"),
                "position_notional_p50_pct": float(pos.quantile(0.5)) if len(pos) else float("nan"),
                "position_notional_p90_pct": float(pos.quantile(0.9)) if len(pos) else float("nan"),
                "position_notional_max_pct": float(pos.max()) if len(pos) else float("nan"),
                "margin_used_after_trade_mean_pct": float(mar.mean()) if len(mar) else float("nan"),
                "margin_used_after_trade_p50_pct": float(mar.quantile(0.5)) if len(mar) else float("nan"),
                "margin_used_after_trade_p90_pct": float(mar.quantile(0.9)) if len(mar) else float("nan"),
                "margin_used_after_trade_max_pct": float(mar.max()) if len(mar) else float("nan"),
                "eod_position_notional_pct": eod_pos_pct,
                "eod_margin_used_after_trade_pct": eod_mar_pct,
                "day_net_pnl": float(pd.to_numeric(g["_net_pnl"], errors="coerce").fillna(0.0).sum()),
            }
        )

    daily = pd.DataFrame(daily_rows, columns=_DAILY_DIST_COLUMNS).sort_values("date").reset_index(drop=True)
    ddir = report_dir / "09_diagnostics"
    daily.to_csv(ddir / "daily_trade_position_distribution.csv", index=False, encoding="utf-8-sig")

    if daily.empty:
        _write_empty_time_distributions(report_dir)
        return

    daily["_month"] = pd.to_datetime(daily["date"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    monthly = (
        daily.groupby("_month", as_index=False)
        .agg(
            trading_days=("date", "count"),
            total_trade_count=("trade_count", "sum"),
            avg_trade_count_per_day=("trade_count", "mean"),
            p50_trade_count_per_day=("trade_count", lambda s: float(pd.Series(s).quantile(0.5))),
            p90_trade_count_per_day=("trade_count", lambda s: float(pd.Series(s).quantile(0.9))),
            avg_eod_position_notional_pct=("eod_position_notional_pct", "mean"),
            p90_eod_position_notional_pct=("eod_position_notional_pct", lambda s: float(pd.Series(s).quantile(0.9))),
            max_eod_position_notional_pct=("eod_position_notional_pct", "max"),
            avg_eod_margin_used_pct=("eod_margin_used_after_trade_pct", "mean"),
            max_eod_margin_used_pct=("eod_margin_used_after_trade_pct", "max"),
            month_net_pnl=("day_net_pnl", "sum"),
        )
        .rename(columns={"_month": "month"})
        .sort_values("month")
        .reset_index(drop=True)
    )
    monthly = monthly.loc[:, list(_MONTHLY_DIST_COLUMNS)]
    monthly.to_csv(ddir / "monthly_trade_position_distribution.csv", index=False, encoding="utf-8-sig")

    weekday = daily.copy()
    weekday["weekday"] = pd.to_datetime(weekday["date"], errors="coerce").dt.day_name()
    weekday["weekday_num"] = pd.to_datetime(weekday["date"], errors="coerce").dt.weekday
    weekday_out = (
        weekday.groupby(["weekday_num", "weekday"], as_index=False)
        .agg(
            days=("date", "count"),
            avg_trade_count=("trade_count", "mean"),
            p90_trade_count=("trade_count", lambda s: float(pd.Series(s).quantile(0.9))),
            avg_eod_position_notional_pct=("eod_position_notional_pct", "mean"),
            p90_eod_position_notional_pct=("eod_position_notional_pct", lambda s: float(pd.Series(s).quantile(0.9))),
            avg_eod_margin_used_pct=("eod_margin_used_after_trade_pct", "mean"),
            avg_day_net_pnl=("day_net_pnl", "mean"),
        )
        .sort_values("weekday_num")
        .reset_index(drop=True)
    )
    weekday_out = weekday_out.loc[:, list(_WEEKDAY_DIST_COLUMNS)]
    weekday_out.to_csv(ddir / "weekday_trade_position_distribution.csv", index=False, encoding="utf-8-sig")


def write_cluster_symbol_interval_signal_views(report_dir: Path, trades: pd.DataFrame, cfg: AggregateConfig) -> None:
    if trades.empty:
        for rel in (
            "02_by_cluster/_comparison.csv",
            "03_by_symbol/_ranking.csv",
            "04_by_interval/_comparison.csv",
            "05_by_signal_type/_comparison.csv",
        ):
            pd.DataFrame().to_csv(report_dir / rel, index=False, encoding="utf-8-sig")
        return

    ex_mask = _executed_mask(trades)
    executed = trades.loc[ex_mask].copy()
    executed["net_pnl"] = pd.to_numeric(
        executed.get("net_pnl", pd.Series(0.0, index=executed.index)),
        errors="coerce",
    ).fillna(0.0)
    executed["group_name"] = executed.get("group_name", pd.Series(["unknown"] * len(executed))).astype(str)
    executed["symbol"] = executed.get("symbol", pd.Series([""] * len(executed))).astype(str)
    executed["interval"] = executed.get("interval", pd.Series([""] * len(executed))).astype(str)
    executed["signal_type"] = executed.get("signal_type", pd.Series([""] * len(executed))).astype(str)

    by_cluster_dir = report_dir / "02_by_cluster"
    cmp_rows: list[dict[str, Any]] = []
    for cluster in sorted(set(trades.get("group_name", pd.Series([], dtype=object)).astype(str))):
        sub_all = trades.loc[trades.get("group_name", pd.Series([""] * len(trades))).astype(str) == cluster].copy()
        sub_exec = executed.loc[executed["group_name"] == cluster].copy()
        rep = build_aggregate_reports(sub_all, cfg=cfg)
        cdir = by_cluster_dir / str(cluster).strip().lower()
        cdir.mkdir(parents=True, exist_ok=True)
        sub_all.to_csv(cdir / "trade_details.csv", index=False, encoding="utf-8-sig")
        rep["monthly"].to_csv(cdir / "monthly_metrics.csv", index=False, encoding="utf-8-sig")
        rep["weekly"].to_csv(cdir / "weekly_metrics.csv", index=False, encoding="utf-8-sig")
        rep["summary"].to_csv(cdir / "summary.csv", index=False, encoding="utf-8-sig")
        s0 = rep["summary"].iloc[0] if not rep["summary"].empty else {}
        cmp_rows.append(
            {
                "cluster_name": cluster,
                "trade_count": int(len(sub_exec)),
                "win_rate": float((sub_exec["net_pnl"] > 0).mean()) if len(sub_exec) else float("nan"),
                "net_pnl": float(sub_exec["net_pnl"].sum()) if len(sub_exec) else 0.0,
                "total_return_pct": float(s0.get("total_return_pct", float("nan"))) if isinstance(s0, pd.Series) else float("nan"),
                "monthly_sharpe": float(s0.get("monthly_sharpe", float("nan"))) if isinstance(s0, pd.Series) else float("nan"),
                "max_drawdown_pct": float(s0.get("max_dd_pct_monthly", float("nan"))) if isinstance(s0, pd.Series) else float("nan"),
            }
        )
    pd.DataFrame(cmp_rows).to_csv(by_cluster_dir / "_comparison.csv", index=False, encoding="utf-8-sig")

    by_symbol = (
        executed.groupby("symbol", as_index=False)
        .agg(
            trade_count=("net_pnl", "size"),
            win_rate=("net_pnl", lambda s: float((pd.Series(s) > 0).mean())),
            net_pnl=("net_pnl", "sum"),
        )
        .sort_values(["net_pnl", "symbol"], ascending=[False, True])
        .reset_index(drop=True)
    )
    by_symbol.insert(0, "rank", np.arange(1, len(by_symbol) + 1, dtype=int))
    by_symbol.to_csv(report_dir / "03_by_symbol" / "_ranking.csv", index=False, encoding="utf-8-sig")

    by_interval = (
        executed.groupby("interval", as_index=False)
        .agg(
            trade_count=("net_pnl", "size"),
            win_rate=("net_pnl", lambda s: float((pd.Series(s) > 0).mean())),
            total_return_pct=("net_pnl", lambda s: float(pd.Series(s).sum() / float(cfg.initial_capital))),
        )
        .sort_values(["interval"], ascending=[True])
        .reset_index(drop=True)
    )
    by_interval.to_csv(report_dir / "04_by_interval" / "_comparison.csv", index=False, encoding="utf-8-sig")

    signal_rows: list[dict[str, Any]] = []
    for signal_type, sub in executed.groupby("signal_type", dropna=False):
        s = sub.copy()
        s["net_pnl"] = pd.to_numeric(s.get("net_pnl", pd.Series(0.0, index=s.index)), errors="coerce").fillna(0.0)
        s["symbol"] = s.get("symbol", pd.Series([""] * len(s), index=s.index)).astype(str)
        exit_dt = _signal_exit_datetime(s)
        s = s.assign(_exit_dt=exit_dt).sort_values("_exit_dt")
        pnl = pd.to_numeric(s["net_pnl"], errors="coerce").fillna(0.0)
        trade_count = int(len(s))
        net_pnl = float(pnl.sum()) if trade_count else 0.0
        avg_pnl = float(pnl.mean()) if trade_count else float("nan")
        win_rate = float((pnl > 0).mean()) if trade_count else float("nan")
        max_dd = _signal_max_drawdown_pct(pnl, float(cfg.initial_capital))
        start_ts = pd.to_datetime(s["_exit_dt"], errors="coerce").min()
        end_ts = pd.to_datetime(s["_exit_dt"], errors="coerce").max()
        total_return_pct = net_pnl / float(cfg.initial_capital) if float(cfg.initial_capital) > 0 else float("nan")
        ann_ret = _annualized_return(
            total_return_pct,
            None if pd.isna(start_ts) else pd.Timestamp(start_ts),
            None if pd.isna(end_ts) else pd.Timestamp(end_ts),
        )
        calmar = float(ann_ret / abs(max_dd)) if np.isfinite(ann_ret) and np.isfinite(max_dd) and max_dd < 0 else float("nan")
        monthly_sharpe = _signal_monthly_sharpe(
            pnl,
            s["_exit_dt"],
            float(cfg.initial_capital),
            float(cfg.risk_free_annual_return),
        )
        top5_trade_pct = _top_positive_share(pnl, top_n=5)
        by_symbol_pnl = pnl.groupby(s["symbol"], dropna=False).sum()
        top5_symbol_pct = _top_positive_share(by_symbol_pnl, top_n=5)
        signal_rows.append(
            {
                "signal_type": str(signal_type),
                "trade_count": trade_count,
                "win_rate": win_rate,
                "net_pnl": net_pnl,
                "avg_net_pnl_per_trade": avg_pnl,
                "max_drawdown_pct": max_dd,
                "monthly_sharpe": monthly_sharpe,
                "annualized_return_pct": ann_ret,
                "calmar_like": calmar,
                "top5_trade_pnl_pct": top5_trade_pct,
                "top5_symbol_pnl_pct": top5_symbol_pct,
            }
        )
    if signal_rows:
        by_signal = (
            pd.DataFrame(signal_rows)
            .sort_values(["net_pnl", "signal_type"], ascending=[False, True])
            .reset_index(drop=True)
        )
    else:
        by_signal = pd.DataFrame(
            columns=[
                "signal_type",
                "trade_count",
                "win_rate",
                "net_pnl",
                "avg_net_pnl_per_trade",
                "max_drawdown_pct",
                "monthly_sharpe",
                "annualized_return_pct",
                "calmar_like",
                "top5_trade_pnl_pct",
                "top5_symbol_pnl_pct",
            ]
        )
    by_signal.to_csv(report_dir / "05_by_signal_type" / "_comparison.csv", index=False, encoding="utf-8-sig")


def write_drilldown(report_dir: Path, trades: pd.DataFrame) -> None:
    ddir = report_dir / "06_drilldown"
    if trades.empty:
        for name in ("gate_funnel.csv", "block_reason_breakdown.csv", "drawdown_episodes.csv", "outlier_trades.csv"):
            pd.DataFrame().to_csv(ddir / name, index=False, encoding="utf-8-sig")
        return

    n_total = int(len(trades))
    status = trades.get("execution_status", pd.Series([""] * len(trades))).astype(str)
    invalid = int((status == "blocked_invalid_time").sum() + (status == "invalid_time").sum())
    blocked_throttle = int((status == "blocked_throttle_halt").sum())
    blocked_htf = int((status == "blocked_htf_gate").sum())
    blocked_ranker = int((status == "blocked_ranker").sum())
    blocked_other = int(((status.str.startswith("blocked_")) & ~status.isin(
        ["blocked_invalid_time", "blocked_throttle_halt", "blocked_htf_gate", "blocked_ranker"]
    )).sum())
    executed = int((status == "executed").sum())

    c0 = n_total
    c1 = max(0, c0 - invalid)
    c2 = max(0, c1 - blocked_throttle)
    c3 = max(0, c2 - blocked_htf)
    c4 = max(0, c3 - blocked_ranker)
    c5 = max(0, c4 - blocked_other)
    c6 = min(c5, executed)
    funnel = pd.DataFrame(
        [
            {"stage": "candidates_total", "count": c0},
            {"stage": "after_time_sanity", "count": c1},
            {"stage": "after_throttle", "count": c2},
            {"stage": "after_htf_gate", "count": c3},
            {"stage": "after_ranker", "count": c4},
            {"stage": "after_portfolio_constraints", "count": c5},
            {"stage": "executed", "count": c6},
        ]
    )
    funnel["pct_of_candidates"] = np.where(c0 > 0, funnel["count"].astype(float) / float(c0), np.nan)
    prev = funnel["count"].shift(1).replace(0, np.nan)
    funnel["pct_of_prev"] = np.where(funnel.index == 0, 1.0, funnel["count"].astype(float) / prev)
    funnel.to_csv(ddir / "gate_funnel.csv", index=False, encoding="utf-8-sig")

    br = trades.copy()
    br["month"] = pd.to_datetime(br.get("datetime", pd.NaT), errors="coerce").dt.to_period("M").dt.to_timestamp()
    br["group_name"] = br.get("group_name", pd.Series(["unknown"] * len(br))).astype(str)
    br["block_reason"] = (
        br.get("block_reason", pd.Series([""] * len(br))).fillna("").replace("", BR_LOG_EXECUTED_SENTINEL)
    )
    g = br.groupby(["month", "group_name", "block_reason"], dropna=False, as_index=False).size().rename(columns={"size": "count"})
    tot = g.groupby(["month", "group_name"], as_index=False)["count"].sum().rename(columns={"count": "month_group_total"})
    out = g.merge(tot, on=["month", "group_name"], how="left")
    out["pct_of_month_group"] = np.where(out["month_group_total"] > 0, out["count"] / out["month_group_total"], np.nan)
    out.to_csv(ddir / "block_reason_breakdown.csv", index=False, encoding="utf-8-sig")

    ex = trades.loc[_executed_mask(trades)].copy()
    ex["net_pnl"] = pd.to_numeric(
        ex.get("net_pnl", pd.Series(0.0, index=ex.index)),
        errors="coerce",
    ).fillna(0.0)
    outlier = pd.concat(
        [ex.sort_values("net_pnl", ascending=False).head(20), ex.sort_values("net_pnl", ascending=True).head(20)],
        axis=0,
    ).drop_duplicates()
    outlier.to_csv(ddir / "outlier_trades.csv", index=False, encoding="utf-8-sig")

    if ex.empty:
        pd.DataFrame(columns=["start", "trough", "end", "drawdown_pct"]).to_csv(
            ddir / "drawdown_episodes.csv", index=False, encoding="utf-8-sig"
        )
    else:
        ex["exit_datetime"] = pd.to_datetime(ex.get("exit_datetime", pd.NaT), errors="coerce")
        ex = ex.sort_values("exit_datetime")
        eq = ex["net_pnl"].cumsum()
        peak = eq.cummax().replace(0, np.nan)
        dd = (eq - peak) / peak
        episodes = pd.DataFrame({"timestamp": ex["exit_datetime"], "drawdown_pct": dd})
        episodes.to_csv(ddir / "drawdown_episodes.csv", index=False, encoding="utf-8-sig")


__all__ = ["write_cluster_symbol_interval_signal_views", "write_drilldown", "write_trade_position_time_distributions"]
