"""Failure-first funnel and official-performance reporting."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


TRADING_SUMMARY_FIELDS = (
    "trade_count",
    "total_net_pnl",
    "total_return",
    "annualized_return",
    "win_rate",
    "max_drawdown",
    "profit_factor",
    "expectancy_R",
    "fees",
    "slippage",
    "final_equity",
)
_CASH_SUMMARY_FIELDS = {"total_net_pnl", "fees", "slippage", "final_equity"}
_PERCENT_SUMMARY_FIELDS = {
    "total_return",
    "annualized_return",
    "win_rate",
    "max_drawdown",
}
_RATIO_SUMMARY_FIELDS = {"profit_factor", "expectancy_R"}


def build_funnel(
    *,
    candidates: pd.DataFrame,
    plans: pd.DataFrame,
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    trades: pd.DataFrame,
) -> dict[str, int]:
    order_count = (
        int(orders["order_id"].nunique()) if "order_id" in orders else len(orders)
    )
    entry_fills = (
        fills.loc[fills["fill_kind"].eq("ENTRY")]
        if "fill_kind" in fills
        else fills
    )
    fill_count = (
        int(entry_fills["order_id"].nunique())
        if "order_id" in entry_fills
        else len(entry_fills)
    )
    return {
        "candidates": len(candidates),
        "eligible_plans": len(plans),
        "orders": order_count,
        "fills": fill_count,
        "round_trips": len(trades),
    }


def build_official_summary(
    *,
    funnel: Mapping[str, int],
    metadata_gaps: list[Mapping[str, Any]],
    performance: Mapping[str, Any] | None,
    primary_curve: list[Mapping[str, Any]] | None = None,
    promotion_blockers: Sequence[str] = (),
    assumed_mechanics: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if metadata_gaps:
        return {
            "requested_interval_status": "BLOCKED_METADATA",
            "official_performance": None,
            "primary_curve_for_requested_interval": None,
            "signal_only_label": "SIGNAL_ONLY_DIAGNOSTIC",
            "covered_subset_status": "COVERED_SUBPERIOD_DIAGNOSTIC",
            "assumed_mechanics_appendix": "NON_CAUSAL_SCENARIO",
            "metadata_gaps": [dict(item) for item in metadata_gaps],
            "funnel": dict(funnel),
        }
    assumptions = [dict(item) for item in assumed_mechanics]
    if assumptions:
        return {
            "requested_interval_status": "NON_CAUSAL_SCENARIO",
            "official_performance": None,
            "primary_curve_for_requested_interval": None,
            "scenario_performance": _json_safe(dict(performance or {})),
            "scenario_curve": _json_safe(list(primary_curve or [])),
            "signal_only_label": None,
            "covered_subset_status": None,
            "assumed_mechanics_appendix": "NON_CAUSAL_SCENARIO",
            "assumed_mechanics": assumptions,
            "metadata_gaps": [],
            "promotion_blockers": ["assumed_execution_metadata"],
            "funnel": dict(funnel),
        }
    blockers = list(promotion_blockers)
    if not performance or not primary_curve or blockers:
        if not performance and "performance_missing" not in blockers:
            blockers.append("performance_missing")
        if not primary_curve and "primary_curve_missing" not in blockers:
            blockers.append("primary_curve_missing")
        return {
            "requested_interval_status": "BLOCKED_RESEARCH",
            "official_performance": None,
            "primary_curve_for_requested_interval": None,
            "signal_only_label": "SIGNAL_ONLY_DIAGNOSTIC",
            "covered_subset_status": None,
            "assumed_mechanics_appendix": None,
            "metadata_gaps": [],
            "promotion_blockers": blockers,
            "funnel": dict(funnel),
        }
    return {
        "requested_interval_status": "COMPLETE",
        "official_performance": _json_safe(dict(performance or {})),
        "primary_curve_for_requested_interval": _json_safe(
            list(primary_curve or [])
        ),
        "signal_only_label": None,
        "covered_subset_status": None,
        "assumed_mechanics_appendix": None,
        "metadata_gaps": [],
        "promotion_blockers": [],
        "funnel": dict(funnel),
    }


def compute_performance_metrics(
    trades: pd.DataFrame,
    daily_equity: pd.DataFrame,
    *,
    initial_equity: float,
) -> dict[str, float | int]:
    """Compute net Level-3 metrics with every requested trading day in the denominator."""
    if initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    if not {"date", "equity"}.issubset(daily_equity):
        raise ValueError("daily equity requires date and equity")
    daily = daily_equity.copy().sort_values("date").reset_index(drop=True)
    equity = pd.to_numeric(daily["equity"], errors="coerce")
    if equity.empty or not np.isfinite(equity.to_numpy(float)).all() or (equity <= 0).any():
        raise ValueError("daily equity must be finite and positive")
    returns = equity.pct_change()
    returns.iloc[0] = equity.iloc[0] / initial_equity - 1.0
    peak = equity.cummax().clip(lower=initial_equity)
    drawdown = equity / peak - 1.0
    total_return = float(equity.iloc[-1] / initial_equity - 1.0)
    years = max(len(returns) / 252.0, 1.0 / 252.0)
    annualized = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 else -1.0
    return_std = float(returns.std(ddof=1))
    sharpe = (
        float(returns.mean()) / return_std * math.sqrt(252.0)
        if return_std > 0
        else float("nan")
    )
    downside = returns.loc[returns < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else float("nan")
    sortino = (
        float(returns.mean()) / downside_std * math.sqrt(252.0)
        if math.isfinite(downside_std) and downside_std > 0
        else float("nan")
    )
    max_drawdown = abs(float(drawdown.min()))
    calmar = annualized / max_drawdown if max_drawdown > 0 else float("nan")

    net = _numeric(trades, "net_pnl")
    net_r = _numeric(trades, "net_r")
    wins = net.loc[net > 0]
    losses = net.loc[net < 0]
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    fees = float(_numeric(trades, "fees").sum())
    slippage = float(_numeric(trades, "slippage").sum())
    gross_positive = float(_numeric(trades, "gross_pnl").clip(lower=0).sum())
    return {
        "trade_count": len(trades),
        "total_net_pnl": float(net.sum()),
        "total_return": total_return,
        "final_equity": float(equity.iloc[-1]),
        "win_rate": float((net > 0).mean()) if len(net) else float("nan"),
        "average_win": float(wins.mean()) if len(wins) else 0.0,
        "average_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("nan"),
        "expectancy_R": float(net_r.mean()) if len(net_r) else float("nan"),
        "annualized_return": annualized,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "average_mfe_R": float(_numeric(trades, "mfe_r").mean()),
        "average_mae_R": float(_numeric(trades, "mae_r").mean()),
        "average_holding_bars": float(_numeric(trades, "holding_bars").mean()),
        "turnover": float(_numeric(trades, "turnover").sum()),
        "fees": fees,
        "slippage": slippage,
        "cost_to_gross_profit": (fees + slippage) / gross_positive
        if gross_positive > 0 else float("nan"),
        "max_margin_utilization": float(_numeric(daily, "margin_utilization").max()),
        "max_open_risk": float(_numeric(daily, "open_risk").max()),
        "tail_daily_return": float(returns.min()),
        "zero_return_days": int(np.isclose(returns.to_numpy(float), 0.0).sum()),
    }


def build_group_report(trades: pd.DataFrame) -> pd.DataFrame:
    group_columns = ["symbol", "sector", "setup", "cycle", "direction"]
    missing = sorted(set(group_columns + ["net_pnl"]).difference(trades.columns))
    if missing:
        raise ValueError(f"missing group report columns: {','.join(missing)}")
    frame = trades.copy()
    frame["net_pnl"] = pd.to_numeric(frame["net_pnl"], errors="coerce")
    frame["win"] = frame["net_pnl"] > 0
    aggregations: dict[str, tuple[str, str]] = {
        "trade_count": ("net_pnl", "size"),
        "win_rate": ("win", "mean"),
        "net_pnl": ("net_pnl", "sum"),
        "average_win": ("net_pnl", lambda values: values.loc[values > 0].mean()),
        "average_loss": ("net_pnl", lambda values: values.loc[values < 0].mean()),
        "gross_profit": ("net_pnl", lambda values: values.loc[values > 0].sum()),
        "gross_loss": ("net_pnl", lambda values: values.loc[values < 0].sum()),
    }
    if "net_r" in frame:
        aggregations["expectancy_R"] = ("net_r", "mean")
    if "fees" in frame:
        aggregations["fees"] = ("fees", "sum")
    if "slippage" in frame:
        aggregations["slippage"] = ("slippage", "sum")
    for source, output in (
        ("mfe_r", "average_mfe_R"),
        ("mae_r", "average_mae_R"),
        ("holding_bars", "average_holding_bars"),
    ):
        if source in frame:
            aggregations[output] = (source, "mean")
    result = frame.groupby(group_columns, dropna=False).agg(**aggregations).reset_index()
    gross_loss = result.pop("gross_loss").abs()
    gross_profit = result.pop("gross_profit")
    result["profit_factor"] = gross_profit / gross_loss.where(gross_loss > 0)
    return result


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").dropna()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        return None
    return value


def _format_trading_summary_value(name: str, value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(number):
        return "N/A"
    if name == "trade_count":
        return str(int(number))
    if name in _CASH_SUMMARY_FIELDS:
        return f"{number:,.2f}"
    if name in _PERCENT_SUMMARY_FIELDS:
        return f"{number:.2%}"
    if name in _RATIO_SUMMARY_FIELDS:
        return f"{number:.4f}"
    return str(value)


def write_report_bundle(
    output_dir: str | Path,
    *,
    tables: Mapping[str, pd.DataFrame],
    summary: Mapping[str, Any],
) -> Path:
    """Write an auditable non-destructive report directory."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    for name, frame in tables.items():
        if Path(name).name != name or not name.endswith(".csv"):
            raise ValueError("report table names must be plain CSV filenames")
        frame.to_csv(target / name, index=False)
    (target / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# Brooks Cycle V1 Report",
        "",
        f"Official status: {summary.get('requested_interval_status', 'UNKNOWN')}",
    ]
    reproduction_command = summary.get("reproduction_command")
    if isinstance(reproduction_command, Mapping):
        lines.extend(["", "## Reproduction Command"])
        working_directory = reproduction_command.get("working_directory")
        if working_directory:
            lines.append(f"- working_directory: {working_directory}")
        shell_command = reproduction_command.get("shell_command")
        if shell_command:
            lines.extend(["", "```bash", str(shell_command), "```"])
    performance = summary.get("official_performance")
    if isinstance(performance, Mapping):
        lines.extend(["", "## Trading Summary"])
        lines.extend(
            f"- {name}: {_format_trading_summary_value(name, performance.get(name))}"
            for name in TRADING_SUMMARY_FIELDS
        )
    metadata_update = summary.get("execution_metadata_update")
    if isinstance(metadata_update, Mapping) and metadata_update.get("enabled"):
        fields = (
            "status",
            "cache_hit",
            "cache_key",
            "contract_date_pairs",
            "generated_contract_rows",
            "generated_daily_rows",
            "generated_fee_rows",
            "source_queries",
            "source_audit_sha256",
            "manifest_sha256",
            "reason_code",
            "reason",
        )
        lines.extend(["", "## Execution Metadata Update"])
        lines.extend(
            f"- {name}: {metadata_update[name]}"
            for name in fields
            if name in metadata_update
        )
    lines.extend(["", "## Funnel"])
    lines.extend(
        f"- {name}: {value}" for name, value in summary.get("funnel", {}).items()
    )
    if isinstance(performance, Mapping):
        lines.extend(["", "## Official Performance"])
        lines.extend(f"- {name}: {value}" for name, value in performance.items())
    rejections = tables.get("rejections.csv")
    if (
        isinstance(rejections, pd.DataFrame)
        and not rejections.empty
        and "reason_code" in rejections
    ):
        counts = rejections["reason_code"].astype(str).value_counts()
        lines.extend(["", "## Rejection Summary"])
        lines.extend(f"- {reason}: {count}" for reason, count in counts.items())
        if "candidate_id" in rejections:
            candidate_rows = rejections.loc[
                rejections["candidate_id"].fillna("").astype(str).ne("")
            ]
            if not candidate_rows.empty:
                lines.extend(["", "## Candidate Rejections"])
                for row in candidate_rows.to_dict("records"):
                    values = [
                        str(row["candidate_id"]),
                        str(row["reason_code"]),
                    ]
                    for name in ("risk_budget", "loss_per_lot"):
                        value = row.get(name)
                        if value is not None and not pd.isna(value):
                            values.append(f"{name}={round(float(value), 10)}")
                    detail = row.get("detail")
                    if detail is not None and not pd.isna(detail) and str(detail):
                        values.append(str(detail))
                    lines.append(f"- {' '.join(values)}")
    gaps = summary.get("metadata_gaps")
    if gaps:
        lines.extend(["", "## Metadata Gaps"])
        lines.extend(
            f"- {item.get('root_symbol', item.get('contract', 'UNKNOWN'))} "
            f"{item.get('field', 'unknown')}: {item.get('reason_code', '')} "
            f"{item.get('reason', item.get('detail', ''))}".rstrip()
            for item in gaps
        )
    (target / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


__all__ = [
    "build_funnel", "build_group_report", "build_official_summary",
    "compute_performance_metrics", "write_report_bundle",
]
