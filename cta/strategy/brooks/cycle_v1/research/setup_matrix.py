"""Level-2 setup x cycle x direction x instrument opportunity matrix."""
from __future__ import annotations

import pandas as pd


GROUP_COLUMNS = ("setup", "cycle", "direction", "symbol", "sector", "timeframe")


def build_setup_matrix(
    candidates: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    plans: pd.DataFrame | None = None,
    fills: pd.DataFrame | None = None,
) -> pd.DataFrame:
    missing = sorted(set(GROUP_COLUMNS + ("candidate_id",)).difference(candidates.columns))
    if missing:
        raise ValueError(f"missing setup matrix columns: {','.join(missing)}")
    base = candidates.groupby(list(GROUP_COLUMNS), dropna=False).agg(
        candidate_count=("candidate_id", "size")
    ).reset_index()
    for output_column, events in (("plan_count", plans), ("fill_count", fills)):
        base = _merge_event_count(base, candidates, events, output_column)
    if trades.empty:
        base["trade_count"] = 0
        base["net_expectancy_R"] = float("nan")
        return base
    metric_columns = [
        name for name in (
            "net_r", "mfe_r", "mae_r", "holding_bars", "fees", "slippage"
        )
        if name in trades
    ]
    trade_columns = ["candidate_id", *metric_columns]
    joined = candidates[list(GROUP_COLUMNS) + ["candidate_id"]].merge(
        trades[trade_columns], on="candidate_id", how="inner", validate="one_to_many"
    )
    aggregations: dict[str, tuple[str, str]] = {"trade_count": ("candidate_id", "size")}
    if "net_r" in joined:
        net_r = pd.to_numeric(joined["net_r"], errors="coerce")
        joined["net_r"] = net_r
        joined["winning_r"] = net_r.where(net_r > 0)
        joined["losing_r"] = net_r.where(net_r < 0)
        joined["win"] = net_r > 0
        aggregations["net_expectancy_R"] = ("net_r", "mean")
        aggregations["win_rate"] = ("win", "mean")
        aggregations["average_win_R"] = ("winning_r", "mean")
        aggregations["average_loss_R"] = ("losing_r", "mean")
        aggregations["gross_win_R"] = ("winning_r", "sum")
        aggregations["gross_loss_R"] = ("losing_r", "sum")
    for source, output in (
        ("mfe_r", "average_mfe_R"),
        ("mae_r", "average_mae_R"),
        ("holding_bars", "average_holding_bars"),
        ("fees", "fees"),
        ("slippage", "slippage"),
    ):
        if source in joined:
            aggregations[output] = (source, "sum" if source in {"fees", "slippage"} else "mean")
    traded = joined.groupby(list(GROUP_COLUMNS), dropna=False).agg(**aggregations).reset_index()
    if {"gross_win_R", "gross_loss_R"}.issubset(traded):
        loss = traded["gross_loss_R"].abs()
        traded["profit_factor"] = traded["gross_win_R"] / loss.where(loss > 0)
        traded = traded.drop(columns=["gross_win_R", "gross_loss_R"])
    result = base.merge(traded, on=list(GROUP_COLUMNS), how="left")
    result["trade_count"] = result["trade_count"].fillna(0).astype(int)
    if "net_expectancy_R" not in result:
        result["net_expectancy_R"] = float("nan")
    return result


def _merge_event_count(
    base: pd.DataFrame,
    candidates: pd.DataFrame,
    events: pd.DataFrame | None,
    output_column: str,
) -> pd.DataFrame:
    if events is None or events.empty:
        result = base.copy()
        result[output_column] = 0
        return result
    if "candidate_id" not in events:
        raise ValueError(f"{output_column} events require candidate_id")
    joined = candidates[list(GROUP_COLUMNS) + ["candidate_id"]].merge(
        events[["candidate_id"]],
        on="candidate_id",
        how="inner",
        validate="one_to_many",
    )
    counts = joined.groupby(list(GROUP_COLUMNS), dropna=False).agg(
        **{output_column: ("candidate_id", "size")}
    ).reset_index()
    result = base.merge(counts, on=list(GROUP_COLUMNS), how="left")
    result[output_column] = result[output_column].fillna(0).astype(int)
    return result


__all__ = ["GROUP_COLUMNS", "build_setup_matrix"]
