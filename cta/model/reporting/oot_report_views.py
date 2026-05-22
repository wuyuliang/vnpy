"""Subview builders for OOT report writer."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from cta.model.oot.block_reasons import BR_LOG_EXECUTED_SENTINEL

from cta.model.reporting.group_pool_aggregate import AggregateConfig, build_aggregate_reports


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

    by_signal = (
        executed.groupby("signal_type", as_index=False)
        .agg(
            trade_count=("net_pnl", "size"),
            win_rate=("net_pnl", lambda s: float((pd.Series(s) > 0).mean())),
            net_pnl=("net_pnl", "sum"),
        )
        .sort_values(["net_pnl", "signal_type"], ascending=[False, True])
        .reset_index(drop=True)
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


__all__ = ["write_cluster_symbol_interval_signal_views", "write_drilldown"]
