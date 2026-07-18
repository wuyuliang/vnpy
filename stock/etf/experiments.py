from __future__ import annotations

import json
from collections.abc import Mapping
from math import ceil, e, sqrt
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd


def _deflated_sharpe_probability(returns: pd.Series, trial_count: int) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    standard_deviation = clean.std(ddof=0)
    if len(clean) < 3 or standard_deviation <= 0:
        return 0.0
    daily_sharpe = float(clean.mean() / standard_deviation)
    if trial_count <= 1:
        expected_maximum = 0.0
    else:
        normal = NormalDist()
        euler_gamma = 0.5772156649015329
        first_quantile = normal.inv_cdf(1 - 1 / trial_count)
        second_quantile = normal.inv_cdf(1 - 1 / (trial_count * e))
        expected_maximum = (
            (1 - euler_gamma) * first_quantile + euler_gamma * second_quantile
        ) / sqrt(len(clean) - 1)
    skewness = float(clean.skew())
    kurtosis = float(clean.kurt()) + 3.0
    variance = (1 - skewness * daily_sharpe + (kurtosis - 1) * daily_sharpe**2 / 4) / (
        len(clean) - 1
    )
    if not np.isfinite(variance) or variance <= 0:
        return 0.0
    probability = NormalDist().cdf((daily_sharpe - expected_maximum) / sqrt(variance))
    return float(np.clip(probability, 0.0, 1.0))


def _median_holding_days(trades: pd.DataFrame) -> float:
    if trades.empty:
        return float("nan")
    entries: dict[str, pd.Timestamp] = {}
    holding_days: list[int] = []
    ordered = trades.sort_values(["datetime", "side"], kind="stable")
    for row in ordered.itertuples(index=False):
        date = pd.Timestamp(row.datetime)
        if row.side == "buy":
            entries[str(row.symbol)] = date
        elif row.side == "sell" and str(row.symbol) in entries:
            holding_days.append((date - entries.pop(str(row.symbol))).days)
    return float(np.median(holding_days)) if holding_days else float("nan")


def _annual_return_table(
    run_id: str,
    equity: pd.DataFrame,
    trades: pd.DataFrame,
) -> pd.DataFrame:
    frame = equity.copy()
    frame["year"] = frame["datetime"].dt.year
    net_returns = frame.groupby("year")["daily_return"].apply(
        lambda values: (1 + values).prod() - 1
    )
    start_equity = frame.groupby("year")["equity"].first()
    costs = (
        trades.assign(
            year=trades["datetime"].dt.year,
            total_cost=trades["commission"] + trades["slippage_cost"],
        )
        .groupby("year")["total_cost"]
        .sum()
    )
    annual = pd.DataFrame(
        {
            "run_id": run_id,
            "year": net_returns.index.astype(int),
            "net_return": net_returns.to_numpy(),
            "cost": costs.reindex(net_returns.index, fill_value=0.0).to_numpy(),
            "start_equity": start_equity.to_numpy(),
        }
    )
    annual["gross_return_static"] = (
        annual["net_return"] + annual["cost"] / annual["start_equity"]
    )
    return annual


def _rolling_return_table(run_id: str, equity: pd.DataFrame) -> pd.DataFrame:
    rolling = (1 + equity["daily_return"]).rolling(252, min_periods=252).apply(
        np.prod,
        raw=True,
    ) - 1
    frame = pd.DataFrame(
        {
            "run_id": run_id,
            "datetime": equity["datetime"],
            "rolling_12m_return": rolling,
        }
    ).dropna()
    if frame.empty:
        return frame
    frame["month"] = frame["datetime"].dt.to_period("M")
    return frame.groupby("month", as_index=False).tail(1).drop(columns="month")


def summarize_run(
    run_id: str,
    run_dir: Path,
    *,
    trial_count: int,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Summarize one immutable backtest output directory."""
    with (run_dir / "summary.json").open(encoding="utf-8") as file:
        summary = json.load(file)
    equity = pd.read_csv(run_dir / "equity_curve.csv", parse_dates=["datetime"])
    trades = pd.read_csv(run_dir / "trades.csv", parse_dates=["datetime"])
    signals = pd.read_csv(run_dir / "daily_signals.csv", parse_dates=["signal_date"])
    positions = pd.read_csv(run_dir / "positions.csv", parse_dates=["datetime"])
    capture_path = run_dir / "trend_episode_capture.csv"
    capture = pd.read_csv(capture_path) if capture_path.exists() else pd.DataFrame()

    total_cost = float(summary["total_commission"] + summary["total_slippage_cost"])
    net_profit = float(summary["final_equity"] - summary["initial_capital"])
    gross_profit = net_profit + total_cost
    years = max(len(equity) / 252, 1 / 252)
    average_equity = float(equity["equity"].mean())
    buys = trades.loc[trades["side"] == "buy"]
    buy_notional = float((buys["raw_price"] * buys["quantity"]).sum())
    risk_actions = signals.loc[
        signals["action"].isin(["risk_on", "risk_off"]), "action"
    ]
    risk_state_flips = int(
        risk_actions.ne(risk_actions.shift()).sum() - (not risk_actions.empty)
    )
    annual = _annual_return_table(run_id, equity, trades)
    rolling = _rolling_return_table(run_id, equity)
    duplicate_groups = 0
    if not positions.empty and "benchmark_key" in positions:
        duplicate_groups = int(
            (positions.groupby(["datetime", "benchmark_key"]).size() > 1).sum()
        )
    positive_pnl = trades.loc[trades["realized_pnl"] > 0, "realized_pnl"]
    largest_winner_share = (
        float(positive_pnl.max() / positive_pnl.sum())
        if not positive_pnl.empty
        else float("nan")
    )
    industry_by_symbol: dict[str, str] = {}
    if "industry" in positions:
        industry_by_symbol.update(
            positions.dropna(subset=["industry"])
            .drop_duplicates("symbol", keep="last")
            .set_index("symbol")["industry"]
            .astype(str)
            .to_dict()
        )
    if "industry" in signals:
        industry_by_symbol.update(
            signals.dropna(subset=["industry"])
            .drop_duplicates("symbol", keep="last")
            .set_index("symbol")["industry"]
            .astype(str)
            .to_dict()
        )
    sells = trades.loc[trades["side"] == "sell"].copy()
    sells["industry"] = sells["symbol"].map(industry_by_symbol).fillna("unknown")
    industry_contribution = sells.groupby("industry")["realized_pnl"].sum()
    positive_industry_contribution = industry_contribution.loc[
        industry_contribution > 0
    ]
    if positive_industry_contribution.empty:
        largest_positive_industry = None
        largest_industry_share = float("nan")
    else:
        largest_positive_industry = str(positive_industry_contribution.idxmax())
        largest_industry_share = float(
            positive_industry_contribution.max() / positive_industry_contribution.sum()
        )
    parameters = {
        f"param_{key}": value for key, value in summary.get("parameters", {}).items()
    }
    if capture.empty or "qualifies" not in capture:
        qualified_capture = capture.iloc[0:0]
    else:
        qualifies = capture["qualifies"]
        if qualifies.dtype != bool:
            qualifies = qualifies.astype(str).str.lower().eq("true")
        qualified_capture = capture.loc[qualifies]
    metrics: dict[str, Any] = {
        "run_id": run_id,
        **{key: value for key, value in summary.items() if key != "parameters"},
        **parameters,
        "average_equity": average_equity,
        "gross_profit_static": gross_profit,
        "total_cost": total_cost,
        "cost_to_gross_profit": total_cost / gross_profit
        if gross_profit > 0
        else float("nan"),
        "annual_one_way_turnover": buy_notional / average_equity / years,
        "median_holding_days": _median_holding_days(trades),
        "risk_state_flips": risk_state_flips,
        "min_cash": float(equity["cash"].min()),
        "average_exposure": float((equity["market_value"] / equity["equity"]).mean()),
        "max_exposure": float((equity["market_value"] / equity["equity"]).max()),
        "max_positions": int(positions.groupby("datetime").size().max())
        if not positions.empty
        else 0,
        "max_industry_weight": float(positions["industry_weight"].max())
        if "industry_weight" in positions
        else float("nan"),
        "duplicate_benchmark_groups": duplicate_groups,
        "largest_winning_trade_share": largest_winner_share,
        "largest_positive_industry": largest_positive_industry,
        "largest_positive_industry_contribution_share": largest_industry_share,
        "worst_year_return": float(annual["net_return"].min()),
        "positive_year_ratio": float((annual["net_return"] > 0).mean()),
        "deflated_sharpe_probability": _deflated_sharpe_probability(
            equity["daily_return"],
            trial_count,
        ),
        "deflated_sharpe_status": "diagnostic_only"
        if len(equity) >= 756 and trial_count >= 2
        else "insufficient_sample",
        "bull_episode_count": int(len(qualified_capture)),
        "median_bull_capture_ratio": float(
            pd.to_numeric(qualified_capture["capture_ratio"], errors="coerce").median()
        )
        if not qualified_capture.empty
        else float("nan"),
    }
    filled = signals.loc[signals["action"] == "buy_filled"]
    metrics["max_correlation_entry_weight"] = (
        float(filled["correlation_weight_after_entry"].max())
        if "correlation_weight_after_entry" in filled
        else float("nan")
    )
    skipped = signals.loc[signals["action"] == "buy_skipped", "reason"].value_counts()
    for reason in (
        "gap_filter",
        "industry_cap",
        "correlation_cap",
        "benchmark_duplicate",
    ):
        metrics[f"skip_{reason}"] = int(skipped.get(reason, 0))
    return metrics, annual, rolling


def _benchmark_metrics(
    benchmark: pd.DataFrame,
    start_date: object,
    end_date: object,
    target_volatility: float,
) -> dict[str, float]:
    frame = benchmark.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    frame = frame.loc[
        (frame["datetime"] >= pd.Timestamp(start_date))
        & (frame["datetime"] <= pd.Timestamp(end_date))
    ].sort_values("datetime")
    returns = frame["close"].pct_change(fill_method=None).fillna(0.0)
    benchmark_volatility = float(returns.std(ddof=0) * sqrt(252))
    exposure = (
        min(max(target_volatility / benchmark_volatility, 0.0), 1.0)
        if benchmark_volatility > 0
        else 0.0
    )
    matched_returns = returns * exposure
    matched_curve = (1 + matched_returns).cumprod()
    matched_drawdown = matched_curve / matched_curve.cummax() - 1
    return {
        "benchmark_total_return": float(
            frame["close"].iloc[-1] / frame["close"].iloc[0] - 1
        ),
        "benchmark_annual_volatility": benchmark_volatility,
        "risk_matched_benchmark_exposure": exposure,
        "risk_matched_benchmark_total_return": float(matched_curve.iloc[-1] - 1),
        "risk_matched_benchmark_max_drawdown": float(matched_drawdown.min()),
    }


def _block_overfit_diagnostic(
    run_dirs: Mapping[str, Path],
    summary: pd.DataFrame,
) -> dict[str, Any]:
    quarterly: dict[str, pd.Series] = {}
    for run_id, run_dir in run_dirs.items():
        equity = pd.read_csv(run_dir / "equity_curve.csv", parse_dates=["datetime"])
        periods = equity["datetime"].dt.to_period("Q")
        quarterly[run_id] = equity.groupby(periods)["daily_return"].apply(
            lambda values: (1 + values).prod() - 1
        )
    matrix = pd.DataFrame(quarterly).dropna(how="all")
    if len(matrix) < 8 or len(matrix.columns) < 3:
        return {
            "status": "insufficient_blocks",
            "quarter_count": int(len(matrix)),
            "run_count": int(len(matrix.columns)),
        }
    winner = str(summary.sort_values("sharpe", ascending=False).iloc[0]["run_id"])
    ranks = matrix.rank(axis=1, ascending=False, method="average")
    top_half = ranks[winner] <= ceil(len(matrix.columns) / 2)
    return {
        "status": "diagnostic_only",
        "quarter_count": int(len(matrix)),
        "run_count": int(len(matrix.columns)),
        "full_sample_sharpe_winner": winner,
        "winner_positive_quarter_ratio": float((matrix[winner] > 0).mean()),
        "winner_top_half_quarter_ratio": float(top_half.mean()),
        "block_overfit_risk": float(1 - top_half.mean()),
    }


def _selection_audit(
    summary: pd.DataFrame,
    annual: pd.DataFrame,
    baseline_run_id: str,
    candidate_run_id: str,
) -> dict[str, Any]:
    indexed = summary.set_index("run_id")
    baseline = indexed.loc[baseline_run_id]
    candidate = indexed.loc[candidate_run_id]
    annual_pivot = annual.pivot(
        index="year", columns="run_id", values="net_return"
    ).dropna()
    excess = annual_pivot[candidate_run_id] - annual_pivot[baseline_run_id]
    positive_excess = excess.loc[excess > 0]
    dominant_year_share = (
        float(positive_excess.max() / positive_excess.sum())
        if not positive_excess.empty
        else float("nan")
    )
    gates = {
        "net_return_and_sharpe_above_baseline": bool(
            candidate["total_return"] > baseline["total_return"]
            and candidate["sharpe"] > baseline["sharpe"]
        ),
        "drawdown_not_worse_than_risk_matched_benchmark": bool(
            candidate["max_drawdown"]
            >= candidate["risk_matched_benchmark_max_drawdown"]
        ),
        "annual_one_way_turnover_at_most_10": bool(
            candidate["annual_one_way_turnover"] <= 10
        ),
        "cost_at_most_40_percent_of_positive_gross_profit": bool(
            candidate["gross_profit_static"] > 0
            and candidate["cost_to_gross_profit"] <= 0.40
        ),
        "improvement_spans_multiple_years": bool(
            len(positive_excess) >= 2 and dominant_year_share <= 0.75
        ),
    }
    return {
        **gates,
        "all_numeric_gates_pass": bool(all(gates.values())),
        "positive_excess_years": int(len(positive_excess)),
        "dominant_positive_excess_year_share": dominant_year_share,
        "largest_positive_industry": candidate["largest_positive_industry"],
        "largest_positive_industry_contribution_share": float(
            candidate["largest_positive_industry_contribution_share"]
        ),
        "largest_winning_trade_share": float(candidate["largest_winning_trade_share"]),
        "concentration_review": "manual_review_required",
    }


def write_experiment_report(
    run_dirs: Mapping[str, Path],
    benchmark: pd.DataFrame,
    output_dir: Path,
    *,
    baseline_run_id: str,
    candidate_run_id: str,
) -> dict[str, Any]:
    """Write comparable metrics and an explicit strategy-selection audit."""
    if baseline_run_id not in run_dirs or candidate_run_id not in run_dirs:
        raise ValueError("baseline and candidate run ids must be present")
    trial_count = len(run_dirs)
    metric_rows: list[dict[str, Any]] = []
    annual_frames: list[pd.DataFrame] = []
    rolling_frames: list[pd.DataFrame] = []
    for run_id, run_dir in run_dirs.items():
        metrics, annual, rolling = summarize_run(
            run_id,
            run_dir,
            trial_count=trial_count,
        )
        metrics.update(
            _benchmark_metrics(
                benchmark,
                metrics["start_date"],
                metrics["end_date"],
                float(metrics["annual_volatility"]),
            )
        )
        metric_rows.append(metrics)
        annual_frames.append(annual)
        rolling_frames.append(rolling)
    summary = pd.DataFrame(metric_rows)
    annual_returns = pd.concat(annual_frames, ignore_index=True)
    rolling_returns = pd.concat(rolling_frames, ignore_index=True)
    selection = _selection_audit(
        summary,
        annual_returns,
        baseline_run_id,
        candidate_run_id,
    )
    report = {
        "trial_count": trial_count,
        "selection": selection,
        "block_overfit": _block_overfit_diagnostic(run_dirs, summary),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_dir / "experiment_summary.csv", index=False)
    annual_returns.to_csv(output_dir / "annual_returns.csv", index=False)
    rolling_returns.to_csv(output_dir / "rolling_12m_returns.csv", index=False)
    with (output_dir / "selection_audit.json").open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=True, indent=2, sort_keys=True)
    return report
