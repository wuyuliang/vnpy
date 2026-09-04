"""Net performance, group gates, stress metrics, and the frozen RiskScore."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_RULE_DIRECTIONS = (
    ("second_entry_continuation", "LONG"),
    ("second_entry_continuation", "SHORT"),
    ("strong_breakout_follow_through", "LONG"),
    ("strong_breakout_follow_through", "SHORT"),
    ("failed_breakout_range_fade", "LONG"),
    ("failed_breakout_range_fade", "SHORT"),
)


def compute_trade_metrics(trades: pd.DataFrame) -> dict[str, float | int]:
    """Compute metrics exclusively from net PnL; zero-PnL trades are non-wins."""
    if trades.empty:
        return _empty_metrics()
    if "net_pnl" not in trades:
        raise ValueError("trades must contain net_pnl")
    pnl = pd.to_numeric(trades["net_pnl"], errors="coerce")
    if pnl.isna().any() or not np.isfinite(pnl.to_numpy()).all():
        raise ValueError("net_pnl must be finite")
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    sample_count = int(len(pnl))
    win_rate = float(len(wins) / sample_count)
    payoff = (
        float(wins.mean() / abs(losses.mean()))
        if len(wins) and len(losses)
        else float("inf") if len(wins) else 0.0
    )
    gross_win = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0.0
    )
    wilson_low, wilson_high = wilson_interval(len(wins), sample_count)
    result: dict[str, float | int] = {
        "sample_count": sample_count,
        "win_count": int(len(wins)),
        "loss_count": int(len(losses)),
        "scratch_count": int((pnl == 0).sum()),
        "net_win_rate": win_rate,
        "wilson_low": wilson_low,
        "wilson_high": wilson_high,
        "net_average_payoff": payoff,
        "profit_factor": profit_factor,
        "net_pnl": float(pnl.sum()),
        "expected_pnl": float(pnl.mean()),
    }
    for source, output in (
        ("net_r", "expected_r"),
        ("mfe_r", "average_mfe_r"),
        ("mae_r", "average_mae_r"),
        ("holding_1m_bars", "average_holding_1m_bars"),
        ("total_fee", "total_fee"),
        ("total_slippage", "total_slippage"),
    ):
        result[output] = _finite_stat(trades, source, "sum" if output.startswith("total_") else "mean")
    return result


def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = wins / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return (centre - margin) / denominator, (centre + margin) / denominator


def build_group_metrics(
    trades: pd.DataFrame,
    *,
    minimum_trades: int = 100,
    minimum_win_rate: float = 0.80,
    payoff_min: float = 1.02,
    payoff_max: float = 1.20,
    required_symbols: tuple[str, ...] = (),
    initial_equity: float = 200_000.0,
    daily_dates: pd.Series | None = None,
) -> pd.DataFrame:
    """Build all required views, including explicit zero-sample frozen groups."""
    frame = trades.copy()
    if "direction" in frame:
        frame["direction"] = frame["direction"].map(_direction_label)
    if "exchange_trade_date" in frame:
        frame["year"] = pd.to_datetime(frame["exchange_trade_date"]).dt.year
    elif "exit_time" in frame:
        frame["year"] = pd.to_datetime(frame["exit_time"]).dt.year
    else:
        frame["year"] = pd.Series(dtype="Int64")
    rows: list[dict[str, Any]] = []
    _append_group(
        rows,
        "portfolio",
        {},
        frame,
        minimum_trades,
        minimum_win_rate,
        payoff_min,
        payoff_max,
        initial_equity,
        daily_dates,
    )
    group_specs = (
        ("symbol", ["symbol"]),
        ("symbol_direction", ["symbol", "direction"]),
        ("symbol_setup", ["symbol", "rule_id"]),
        ("symbol_setup_direction", ["symbol", "rule_id", "direction"]),
        ("symbol_session", ["symbol", "session_type"]),
        ("symbol_year", ["symbol", "year"]),
    )
    for group_type, columns in group_specs:
        if frame.empty or any(column not in frame for column in columns):
            continue
        grouper: str | list[str] = columns[0] if len(columns) == 1 else columns
        for keys, group in frame.groupby(grouper, dropna=False, sort=True):
            values = keys if isinstance(keys, tuple) else (keys,)
            labels = dict(zip(columns, values, strict=True))
            _append_group(
                rows,
                group_type,
                labels,
                group,
                minimum_trades,
                minimum_win_rate,
                payoff_min,
                payoff_max,
                initial_equity,
                daily_dates,
            )
    for rule_id, direction in REQUIRED_RULE_DIRECTIONS:
        if frame.empty:
            group = frame
        else:
            group = frame.loc[
                frame["rule_id"].eq(rule_id) & frame["direction"].eq(direction)
            ]
        _append_group(
            rows,
            "required_setup_direction",
            {"rule_id": rule_id, "direction": direction},
            group,
            minimum_trades,
            minimum_win_rate,
            payoff_min,
            payoff_max,
            initial_equity,
            daily_dates,
        )
    for symbol in required_symbols:
        symbol_group = (
            frame.loc[frame["symbol"].eq(symbol)]
            if not frame.empty and "symbol" in frame
            else frame.iloc[0:0]
        )
        _append_group(
            rows,
            "required_symbol",
            {"symbol": symbol},
            symbol_group,
            minimum_trades,
            minimum_win_rate,
            payoff_min,
            payoff_max,
            initial_equity,
            daily_dates,
        )
        for rule_id, direction in REQUIRED_RULE_DIRECTIONS:
            group = (
                symbol_group.loc[
                    symbol_group["rule_id"].eq(rule_id)
                    & symbol_group["direction"].eq(direction)
                ]
                if not symbol_group.empty
                else symbol_group
            )
            _append_group(
                rows,
                "required_symbol_setup_direction",
                {"symbol": symbol, "rule_id": rule_id, "direction": direction},
                group,
                minimum_trades,
                minimum_win_rate,
                payoff_min,
                payoff_max,
                initial_equity,
                daily_dates,
            )
    return pd.DataFrame(rows)


def equity_metrics(
    daily_equity: pd.DataFrame,
    *,
    initial_equity: float | None = None,
) -> dict[str, float | int]:
    """Calculate daily/monthly returns without dropping zero-trade dates."""
    if daily_equity.empty:
        return {
            "daily_count": 0,
            "average_daily_return": 0.0,
            "average_monthly_return": 0.0,
            "max_drawdown": 0.0,
            "worst_daily_return": 0.0,
            "var99": 0.0,
            "es99": 0.0,
        }
    required = {"date", "equity"}
    if not required.issubset(daily_equity):
        raise ValueError("daily_equity must contain date and equity")
    frame = daily_equity.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date")
    equity = pd.to_numeric(frame["equity"], errors="coerce")
    if equity.isna().any() or (equity <= 0).any():
        raise ValueError("daily equity must be finite and positive")
    if initial_equity is not None and (
        not np.isfinite(initial_equity) or initial_equity <= 0
    ):
        raise ValueError("initial_equity must be finite and positive")
    daily_returns = _returns_from_baseline(equity, initial_equity)
    monthly = frame.assign(equity=equity).set_index("date")["equity"].resample("ME").last()
    monthly_returns = _returns_from_baseline(monthly, initial_equity)
    peaks = equity.cummax()
    if initial_equity is not None:
        peaks = peaks.clip(lower=float(initial_equity))
    drawdown = equity / peaks - 1.0
    var99, es99 = historical_var_es(daily_returns)
    return {
        "daily_count": int(len(frame)),
        "average_daily_return": float(daily_returns.mean()),
        "average_monthly_return": float(monthly_returns.mean()),
        "max_drawdown": float(abs(drawdown.min())),
        "worst_daily_return": float(daily_returns.min()),
        "var99": var99,
        "es99": es99,
    }


def _returns_from_baseline(
    values: pd.Series,
    initial_equity: float | None,
) -> pd.Series:
    returns = values.pct_change()
    returns.iloc[0] = (
        float(values.iloc[0]) / float(initial_equity) - 1.0
        if initial_equity is not None
        else 0.0
    )
    return returns


def historical_var_es(returns: pd.Series) -> tuple[float, float]:
    numeric = pd.to_numeric(returns, errors="coerce").dropna()
    if numeric.empty:
        return 0.0, 0.0
    losses = -numeric.to_numpy(dtype=float)
    var99 = max(0.0, float(np.quantile(losses, 0.99)))
    tail = losses[losses >= var99]
    return var99, float(tail.mean()) if len(tail) else var99


def block_bootstrap_loss_probability(
    returns: pd.Series,
    *,
    block_days: int = 5,
    path_days: int = 252,
    simulations: int = 10_000,
    seed: int = 20260809,
) -> float | None:
    numeric = pd.to_numeric(returns, errors="coerce").dropna().to_numpy(dtype=float)
    if len(numeric) < 500:
        return None
    if block_days <= 0 or path_days <= 0 or simulations <= 0:
        raise ValueError("bootstrap dimensions must be positive")
    starts = np.arange(0, len(numeric) - block_days + 1)
    if not len(starts):
        return None
    rng = np.random.default_rng(seed)
    losses = 0
    blocks_needed = math.ceil(path_days / block_days)
    for _ in range(simulations):
        selected = rng.choice(starts, size=blocks_needed, replace=True)
        path = np.concatenate([numeric[start : start + block_days] for start in selected])[:path_days]
        ending_return = float(np.prod(1.0 + path) - 1.0)
        losses += ending_return <= -0.20
    return losses / simulations


@dataclass(frozen=True)
class RiskScoreInput:
    max_drawdown: float
    max_margin_usage: float
    margin_breach_count: int
    bootstrap_loss_probability: float | None
    worst_daily_return: float
    var99: float
    es99: float
    fee_stress_net_pnl: float
    fee_stress_profit_factor: float
    slippage_stress_net_pnl: float
    slippage_stress_profit_factor: float
    gap_limit_margin_breaches: int
    risk_budget_violations: int
    circuit_breaker_violations: int
    session_close_residuals: int
    all_years_positive: bool
    positive_rolling_quarter_ratio: float
    rb_cu_force_failures: int
    causal_audit_passed: bool
    rollover_audit_passed: bool
    ledger_reconciliation_passed: bool
    future_leakage: bool
    continuous_contract_execution: bool
    costs_missing: bool
    rejected_open_fills: int
    unresolved_entry_risk_breaches: int
    opens_without_daily_limits: int


@dataclass(frozen=True)
class RiskScoreResult:
    score: int
    status: str
    items: tuple[dict[str, Any], ...]
    vetoes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_risk_score(value: RiskScoreInput, *, minimum_score: int = 90) -> RiskScoreResult:
    checks = (
        ("max_drawdown", value.max_drawdown <= 0.08, 10),
        ("margin_survival", value.margin_breach_count == 0 and value.max_margin_usage <= 0.35, 8),
        (
            "bootstrap_survival",
            value.bootstrap_loss_probability is not None
            and value.bootstrap_loss_probability < 0.01,
            7,
        ),
        ("worst_day", value.worst_daily_return >= -0.015, 8),
        ("var99", value.var99 <= 0.010, 6),
        ("es99", value.es99 <= 0.015, 6),
        ("fee_stress", value.fee_stress_net_pnl > 0 and value.fee_stress_profit_factor > 1, 8),
        (
            "slippage_stress",
            value.slippage_stress_net_pnl > 0 and value.slippage_stress_profit_factor > 1,
            8,
        ),
        ("gap_limit_stress", value.gap_limit_margin_breaches == 0, 4),
        ("risk_budgets", value.risk_budget_violations == 0, 5),
        ("circuit_breakers", value.circuit_breaker_violations == 0, 5),
        ("session_flat", value.session_close_residuals == 0, 5),
        ("positive_years", value.all_years_positive, 4),
        ("positive_quarters", value.positive_rolling_quarter_ratio >= 0.70, 3),
        ("rb_cu_survival", value.rb_cu_force_failures == 0, 3),
        ("causal_audit", value.causal_audit_passed, 4),
        ("rollover_audit", value.rollover_audit_passed, 3),
        ("ledger_audit", value.ledger_reconciliation_passed, 3),
    )
    items = tuple(
        {"name": name, "passed": bool(passed), "points": points if passed else 0, "maximum": points}
        for name, passed, points in checks
    )
    score = int(sum(int(item["points"]) for item in items))
    vetoes: list[str] = []
    veto_if(vetoes, value.max_drawdown > 0.08, "max_drawdown")
    veto_if(vetoes, value.worst_daily_return < -0.02, "single_day_loss")
    veto_if(
        vetoes,
        value.margin_breach_count > 0 or value.max_margin_usage > 0.35,
        "margin_breach",
    )
    veto_if(
        vetoes,
        value.bootstrap_loss_probability is not None
        and value.bootstrap_loss_probability >= 0.01,
        "bootstrap_loss_probability",
    )
    veto_if(vetoes, value.future_leakage, "future_leakage")
    veto_if(vetoes, value.session_close_residuals > 0, "session_close_residual")
    veto_if(vetoes, value.continuous_contract_execution, "continuous_contract_execution")
    veto_if(vetoes, value.costs_missing, "costs_missing")
    veto_if(vetoes, value.rejected_open_fills > 0, "rejected_open_fill")
    veto_if(vetoes, value.unresolved_entry_risk_breaches > 0, "entry_risk_breach")
    veto_if(vetoes, value.opens_without_daily_limits > 0, "open_without_daily_limits")
    status = "REJECTED" if vetoes else "PASSED" if score >= minimum_score else "FAILED"
    return RiskScoreResult(score, status, items, tuple(vetoes))


def stress_replay(
    trades: pd.DataFrame,
    *,
    fee_multiplier: float = 1.0,
    slippage_multiplier: float = 1.0,
) -> dict[str, float]:
    required = {"gross_pnl", "total_fee", "total_slippage"}
    if not required.issubset(trades):
        raise ValueError(f"stress trades missing columns: {sorted(required - set(trades))}")
    stressed = (
        pd.to_numeric(trades["gross_pnl"])
        - pd.to_numeric(trades["total_fee"]) * fee_multiplier
        - pd.to_numeric(trades["total_slippage"]) * (slippage_multiplier - 1.0)
    )
    wins = stressed[stressed > 0].sum()
    losses = abs(stressed[stressed < 0].sum())
    return {
        "net_pnl": float(stressed.sum()),
        "profit_factor": float(wins / losses) if losses > 0 else float("inf"),
    }


def veto_if(vetoes: list[str], condition: bool, name: str) -> None:
    if condition:
        vetoes.append(name)


def _append_group(
    rows: list[dict[str, Any]],
    group_type: str,
    labels: dict[str, Any],
    group: pd.DataFrame,
    minimum_trades: int,
    minimum_win_rate: float,
    payoff_min: float,
    payoff_max: float,
    initial_equity: float,
    daily_dates: pd.Series | None,
) -> None:
    metrics = compute_trade_metrics(group)
    metrics.update(
        _group_equity_metrics(
            group,
            initial_equity=initial_equity,
            daily_dates=daily_dates,
        )
    )
    count = int(metrics["sample_count"])
    if count < minimum_trades:
        status = "INCONCLUSIVE"
    else:
        payoff = float(metrics["net_average_payoff"])
        status = (
            "PASSED"
            if float(metrics["net_win_rate"]) >= minimum_win_rate
            and payoff_min <= payoff <= payoff_max
            else "FAILED"
        )
    rows.append({"group_type": group_type, **labels, **metrics, "status": status})


def _group_equity_metrics(
    group: pd.DataFrame,
    *,
    initial_equity: float,
    daily_dates: pd.Series | None,
) -> dict[str, float]:
    if not np.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial_equity must be finite and positive")
    if daily_dates is not None:
        dates = pd.DatetimeIndex(pd.to_datetime(daily_dates, errors="coerce")).normalize()
        if dates.isna().any():
            raise ValueError("daily_dates contains invalid values")
        dates = dates.drop_duplicates().sort_values()
    elif not group.empty and "exchange_trade_date" in group:
        dates = pd.DatetimeIndex(
            pd.to_datetime(group["exchange_trade_date"], errors="coerce")
        ).normalize().drop_duplicates().sort_values()
    elif not group.empty and "exit_time" in group:
        dates = pd.DatetimeIndex(
            pd.to_datetime(group["exit_time"], errors="coerce", utc=True)
            .tz_convert(None)
        ).normalize().drop_duplicates().sort_values()
    else:
        dates = pd.DatetimeIndex([])
    if dates.empty:
        return {
            "average_daily_return": 0.0,
            "average_monthly_return": 0.0,
            "max_drawdown": 0.0,
        }
    pnl_by_date = pd.Series(0.0, index=dates)
    if not group.empty:
        if "exchange_trade_date" in group:
            trade_dates = pd.to_datetime(
                group["exchange_trade_date"], errors="coerce"
            ).dt.normalize()
        else:
            trade_dates = pd.to_datetime(
                group["exit_time"], errors="coerce", utc=True
            ).dt.tz_convert(None).dt.normalize()
        pnl = pd.to_numeric(group["net_pnl"], errors="coerce")
        if trade_dates.isna().any() or pnl.isna().any():
            raise ValueError("group trade dates/net_pnl contain invalid values")
        grouped = pnl.groupby(trade_dates).sum()
        pnl_by_date = pnl_by_date.add(grouped, fill_value=0.0)
    equity = initial_equity + pnl_by_date.cumsum()
    prior = equity.shift(1)
    prior.iloc[0] = initial_equity
    daily_returns = equity / prior - 1.0
    monthly = equity.resample("ME").last()
    monthly_prior = monthly.shift(1)
    monthly_prior.iloc[0] = initial_equity
    monthly_returns = monthly / monthly_prior - 1.0
    drawdown = equity / equity.cummax().clip(lower=initial_equity) - 1.0
    return {
        "average_daily_return": float(daily_returns.mean()),
        "average_monthly_return": float(monthly_returns.mean()),
        "max_drawdown": float(abs(drawdown.min())),
    }


def _empty_metrics() -> dict[str, float | int]:
    return {
        "sample_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "scratch_count": 0,
        "net_win_rate": 0.0,
        "wilson_low": 0.0,
        "wilson_high": 0.0,
        "net_average_payoff": 0.0,
        "profit_factor": 0.0,
        "net_pnl": 0.0,
        "expected_pnl": 0.0,
        "expected_r": 0.0,
        "average_mfe_r": 0.0,
        "average_mae_r": 0.0,
        "average_holding_1m_bars": 0.0,
        "total_fee": 0.0,
        "total_slippage": 0.0,
    }


def _finite_stat(frame: pd.DataFrame, column: str, operation: str) -> float:
    if column not in frame or frame.empty:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return 0.0
    return float(values.sum() if operation == "sum" else values.mean())


def _direction_label(value: object) -> str:
    text = str(value).upper()
    if text in {"1", "1.0", "LONG"}:
        return "LONG"
    if text in {"-1", "-1.0", "SHORT"}:
        return "SHORT"
    return text


__all__ = [
    "REQUIRED_RULE_DIRECTIONS",
    "RiskScoreInput",
    "RiskScoreResult",
    "block_bootstrap_loss_probability",
    "build_group_metrics",
    "calculate_risk_score",
    "compute_trade_metrics",
    "equity_metrics",
    "historical_var_es",
    "stress_replay",
    "wilson_interval",
]
