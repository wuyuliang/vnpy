"""EMA-capped portfolio overlay driven by causal regime forecasts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Any, Literal

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .ema_trend_allocation_strategy import (
    EQUITY_COLUMNS,
    POSITION_COLUMNS,
    TRADE_COLUMNS,
    TrendAllocationConfig,
    TrendAllocationResult,
    build_trend_signals,
    calculate_target_quantity,
    execute_target_weights,
)
from .portfolio import Portfolio


WEIGHT_LEVELS = {0.0, 0.5, 1.0}


@dataclass(frozen=True)
class OverlayTransition:
    """Auditable result of one daily target-weight decision."""

    regime_cap: float
    entry_allowed: bool
    risk_ceiling: float
    risk_reduced_weight: float
    proposed_weight: float
    target_weight: float
    risk_increase_blocked: bool
    primary_reason: str
    ema_reduction_applied: bool
    regime_reduction_applied: bool
    score_1d_reduction_applied: bool


@dataclass
class RegimeOverlayResult:
    """Signals, executions, holdings, performance, and independent EMA result."""

    signals: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: dict[str, Any]
    ema_result: TrendAllocationResult | None = None


@dataclass(frozen=True)
class PerformancePeriod:
    """One clipped calendar year or half-year reporting period."""

    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    is_partial_period: bool


def _validate_score(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    score = float(value)
    if not isfinite(score) or not -3.0 <= score <= 3.0:
        raise ValueError(f"{name} must be finite and within [-3, 3]")
    return score


def _validate_weight(value: object, name: str) -> float:
    if isinstance(value, bool) or value not in WEIGHT_LEVELS:
        raise ValueError(f"{name} must be 0.0, 0.5, or 1.0")
    return float(value)


def map_regime_cap(score_3d: object) -> tuple[float, bool]:
    """Return the three-day position cap and flat-entry permission."""
    score = _validate_score(score_3d, "score_3d")
    if score <= -2.0:
        return 0.0, False
    if score <= 1.0:
        return 0.5, False
    if score < 2.0:
        return 0.5, True
    return 1.0, True


def transition_overlay_weight(
    *,
    current_weight: object,
    ema_target_weight: object,
    score_1d: object,
    score_3d: object,
    days_since_transition: int | None,
    cooldown_days: int,
) -> OverlayTransition:
    """Apply risk ceilings, one-day pacing, and final-position cooldown."""
    current = _validate_weight(current_weight, "current_weight")
    ema_target = _validate_weight(ema_target_weight, "ema_target_weight")
    one_day_score = _validate_score(score_1d, "score_1d")
    regime_cap, entry_allowed = map_regime_cap(score_3d)
    if days_since_transition is not None and (
        type(days_since_transition) is not int or days_since_transition < 0
    ):
        raise ValueError("days_since_transition must be a non-negative int or None")
    if type(cooldown_days) is not int or cooldown_days <= 0:
        raise ValueError("cooldown_days must be a positive int")

    risk_ceiling = min(ema_target, regime_cap)
    risk_reduced = min(current, risk_ceiling)
    ema_reduction = ema_target < current
    regime_reduction = regime_cap < current
    score_reduction = False

    if one_day_score <= -2.0:
        proposed = max(0.0, risk_reduced - 0.5)
        score_reduction = proposed < risk_reduced
        reason = "score_1d_reduce" if score_reduction else "hold"
    elif one_day_score > 1.0 and risk_reduced == current:
        can_enter = current > 0.0 or entry_allowed
        proposed = min(current + 0.5, risk_ceiling) if can_enter else current
        reason = "score_1d_increase" if proposed > current else "hold"
    else:
        proposed = risk_reduced
        reason = "hold"

    if proposed < current and not score_reduction:
        if ema_reduction and regime_reduction:
            reason = "ema_and_regime_reduce"
        elif ema_reduction:
            reason = "ema_target_reduce"
        else:
            reason = "regime_cap_reduce"

    blocked = bool(
        proposed > current
        and days_since_transition is not None
        and days_since_transition < cooldown_days
    )
    target = current if blocked else proposed
    if blocked:
        reason = "cooldown_block"

    return OverlayTransition(
        regime_cap=regime_cap,
        entry_allowed=entry_allowed,
        risk_ceiling=risk_ceiling,
        risk_reduced_weight=risk_reduced,
        proposed_weight=proposed,
        target_weight=target,
        risk_increase_blocked=blocked,
        primary_reason=reason,
        ema_reduction_applied=ema_reduction and risk_reduced < current,
        regime_reduction_applied=regime_reduction and risk_reduced < current,
        score_1d_reduction_applied=score_reduction,
    )


def _normalize_naive_dates(values: pd.Series, name: str) -> pd.Series:
    dates = pd.to_datetime(values, errors="raise")
    if dates.isna().any():
        raise ValueError(f"{name} contains missing dates")
    if dates.dt.tz is not None:
        raise ValueError(f"{name} must be timezone-naive")
    return dates.dt.normalize()


def align_regime_predictions(
    predictions: pd.DataFrame,
    bars: pd.DataFrame,
) -> pd.DataFrame:
    """Align close-time 1d/3d forecasts to the next actual symbol bar."""
    prediction_columns = {
        "symbol",
        "feature_asof_date",
        "max_feature_source_date",
        "prediction_horizon",
        "score",
        "state",
    }
    bar_columns = {"symbol", "datetime"}
    missing_predictions = prediction_columns - set(predictions.columns)
    missing_bars = bar_columns - set(bars.columns)
    if missing_predictions:
        raise ValueError(f"predictions missing columns: {sorted(missing_predictions)}")
    if missing_bars:
        raise ValueError(f"bars missing columns: {sorted(missing_bars)}")

    forecast = predictions.loc[:, sorted(prediction_columns)].copy()
    forecast["feature_asof_date"] = _normalize_naive_dates(
        forecast["feature_asof_date"],
        "feature_asof_date",
    )
    forecast["max_feature_source_date"] = _normalize_naive_dates(
        forecast["max_feature_source_date"],
        "max_feature_source_date",
    )
    if not forecast["prediction_horizon"].isin(["1d", "3d"]).all():
        raise ValueError("prediction_horizon must be 1d or 3d")
    keys = ["symbol", "feature_asof_date"]
    if forecast.duplicated([*keys, "prediction_horizon"]).any():
        raise ValueError("predictions contain duplicate horizon rows")
    horizon_sets = forecast.groupby(keys, sort=False)["prediction_horizon"].agg(set)
    if not horizon_sets.map(lambda values: values == {"1d", "3d"}).all():
        raise ValueError("each feature date requires both 1d and 3d predictions")
    if (forecast["max_feature_source_date"] > forecast["feature_asof_date"]).any():
        raise ValueError("predictions contain future feature source dates")

    base = forecast.groupby(keys, as_index=False, sort=False).agg(
        max_feature_source_date=("max_feature_source_date", "max")
    )
    for horizon in ("1d", "3d"):
        horizon_rows = forecast.loc[
            forecast["prediction_horizon"].eq(horizon),
            [*keys, "score", "state"],
        ].rename(
            columns={
                "score": f"score_{horizon}",
                "state": f"state_{horizon}",
            }
        )
        base = base.merge(
            horizon_rows,
            on=keys,
            how="inner",
            validate="one_to_one",
        )

    actual_bars = bars.loc[:, ["symbol", "datetime"]].copy()
    actual_bars["datetime"] = _normalize_naive_dates(
        actual_bars["datetime"],
        "datetime",
    )
    if actual_bars.duplicated(["symbol", "datetime"]).any():
        raise ValueError("bars contain duplicate symbol/date rows")
    actual_bars = actual_bars.sort_values(
        ["symbol", "datetime"],
        ignore_index=True,
    )
    actual_bars["regime_feature_asof_date"] = actual_bars.groupby(
        "symbol",
        sort=False,
    )["datetime"].shift(1)
    aligned = actual_bars.merge(
        base.rename(columns={"feature_asof_date": "regime_feature_asof_date"}),
        on=["symbol", "regime_feature_asof_date"],
        how="inner",
        validate="one_to_one",
    ).rename(columns={"datetime": "execution_date"})
    if (
        aligned["max_feature_source_date"] > aligned["regime_feature_asof_date"]
    ).any() or (aligned["regime_feature_asof_date"] >= aligned["execution_date"]).any():
        raise ValueError("aligned predictions violate causal date ordering")
    return aligned.sort_values(
        ["symbol", "execution_date"],
        ignore_index=True,
    )


def _portfolio_config(config: TrendAllocationConfig) -> StrategyConfig:
    return StrategyConfig(
        initial_capital=config.initial_capital,
        lot_size=config.lot_size,
        commission_rate=config.commission_rate,
        min_commission=config.min_commission,
        slippage_rate=config.slippage_rate,
        max_position_weight=1.0,
    )


def _affordable_buy_quantity(
    cash: float,
    raw_price: float,
    desired_quantity: int,
    config: TrendAllocationConfig,
) -> int:
    fill_price = raw_price * (1.0 + config.slippage_rate)
    quantity = desired_quantity
    while quantity > 0:
        notional = fill_price * quantity
        commission = max(notional * config.commission_rate, config.min_commission)
        if notional + commission <= cash:
            return quantity
        quantity -= config.lot_size
    return 0


def _prepare_overlay_signals(
    signals: pd.DataFrame,
    config: TrendAllocationConfig,
) -> pd.DataFrame:
    required = {
        "symbol",
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "regime_feature_asof_date",
        "max_feature_source_date",
        "score_1d",
        "state_1d",
        "score_3d",
        "state_3d",
        "ema_target_weight",
    }
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"overlay signals missing columns: {sorted(missing)}")
    frame = signals.copy()
    for column in (
        "datetime",
        "regime_feature_asof_date",
        "max_feature_source_date",
    ):
        frame[column] = _normalize_naive_dates(frame[column], column)
    if frame.duplicated(["symbol", "datetime"]).any():
        raise ValueError("overlay signals contain duplicate symbol/date rows")
    if (
        frame["symbol"].astype(str).nunique() != 1
        or not frame["symbol"].astype(str).eq(config.symbol).all()
    ):
        raise ValueError("overlay signals must contain only config.symbol")
    if (frame["max_feature_source_date"] > frame["regime_feature_asof_date"]).any() or (
        frame["regime_feature_asof_date"] >= frame["datetime"]
    ).any():
        raise ValueError("overlay signals violate causal date ordering")
    if not frame["ema_target_weight"].isin(WEIGHT_LEVELS).all():
        raise ValueError("ema_target_weight must use three-level weights")
    for column in ("open", "high", "low", "close", "volume", "score_1d", "score_3d"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    numeric = frame[
        ["open", "high", "low", "close", "volume", "score_1d", "score_3d"]
    ].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise ValueError("overlay signal numeric values must be finite")
    return frame.sort_values("datetime", ignore_index=True)


def execute_overlay_signals(
    signals: pd.DataFrame,
    config: TrendAllocationConfig,
) -> RegimeOverlayResult:
    """Execute already-aligned EMA ceilings and regime forecasts at each open."""
    audited = _prepare_overlay_signals(signals, config)
    portfolio = Portfolio(config.initial_capital, _portfolio_config(config))
    position_rows: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []
    current_weight = 0.0
    last_transition_ordinal: int | None = None
    previous_equity = config.initial_capital
    peak_equity = config.initial_capital

    audit_defaults: dict[str, object] = {
        "regime_cap": 0.0,
        "entry_allowed": False,
        "risk_ceiling": 0.0,
        "risk_reduced_weight": 0.0,
        "proposed_weight": 0.0,
        "risk_increase_blocked": False,
        "days_since_transition": pd.NA,
        "target_weight": 0.0,
        "action": "hold",
        "primary_reason": "hold",
        "ema_reduction_applied": False,
        "regime_reduction_applied": False,
        "score_1d_reduction_applied": False,
    }
    for column, default in audit_defaults.items():
        audited[column] = default
    audited["days_since_transition"] = pd.Series(
        pd.array([pd.NA] * len(audited), dtype="Int64"),
        index=audited.index,
    )

    for ordinal, (index, row) in enumerate(audited.iterrows()):
        days_since_transition = (
            None
            if last_transition_ordinal is None
            else ordinal - last_transition_ordinal - 1
        )
        transition = transition_overlay_weight(
            current_weight=current_weight,
            ema_target_weight=float(row["ema_target_weight"]),
            score_1d=float(row["score_1d"]),
            score_3d=float(row["score_3d"]),
            days_since_transition=days_since_transition,
            cooldown_days=config.risk_increase_cooldown_days,
        )
        target_weight = transition.target_weight
        action = "hold"
        reason = transition.primary_reason
        position = portfolio.positions.get(config.symbol)
        held_quantity = position.quantity if position is not None else 0
        raw_open = float(row["open"])

        if target_weight != current_weight:
            pre_trade_equity = portfolio.cash + held_quantity * raw_open
            target_quantity = calculate_target_quantity(
                pre_trade_equity,
                raw_open,
                target_weight,
                config.lot_size,
            )
            upgrade = target_weight > current_weight
            transition_succeeded = not upgrade or held_quantity >= target_quantity
            if target_quantity > held_quantity:
                buy_quantity = _affordable_buy_quantity(
                    portfolio.cash,
                    raw_open,
                    target_quantity - held_quantity,
                    config,
                )
                if buy_quantity > 0:
                    if position is None:
                        portfolio.buy(
                            config.symbol,
                            row["datetime"],
                            raw_open,
                            buy_quantity,
                            0.0,
                            reason,
                        )
                    else:
                        portfolio.increase(
                            config.symbol,
                            row["datetime"],
                            raw_open,
                            buy_quantity,
                            reason,
                        )
                    action = "buy_to_half" if target_weight == 0.5 else "buy_to_full"
                    transition_succeeded = True
            elif target_quantity < held_quantity:
                portfolio.sell_quantity(
                    config.symbol,
                    row["datetime"],
                    raw_open,
                    held_quantity - target_quantity,
                    reason,
                )
                action = "sell_to_flat" if target_weight == 0.0 else "sell_to_half"

            if upgrade and not transition_succeeded:
                target_weight = current_weight
                reason = "insufficient_cash"
            if target_weight != current_weight:
                last_transition_ordinal = ordinal
                current_weight = target_weight

        for field in (
            "regime_cap",
            "entry_allowed",
            "risk_ceiling",
            "risk_reduced_weight",
            "proposed_weight",
            "risk_increase_blocked",
            "ema_reduction_applied",
            "regime_reduction_applied",
            "score_1d_reduction_applied",
        ):
            audited.at[index, field] = getattr(transition, field)
        audited.at[index, "days_since_transition"] = days_since_transition
        audited.at[index, "target_weight"] = target_weight
        audited.at[index, "action"] = action
        audited.at[index, "primary_reason"] = reason

        position = portfolio.positions.get(config.symbol)
        market_value = (
            position.quantity * float(row["close"]) if position is not None else 0.0
        )
        equity = portfolio.cash + market_value
        daily_return = equity / previous_equity - 1.0
        peak_equity = max(peak_equity, equity)
        equity_rows.append(
            {
                "datetime": row["datetime"],
                "cash": portfolio.cash,
                "market_value": market_value,
                "equity": equity,
                "daily_return": daily_return,
                "drawdown": equity / peak_equity - 1.0,
            }
        )
        if position is not None:
            position_rows.append(
                {
                    "datetime": row["datetime"],
                    "symbol": config.symbol,
                    "quantity": position.quantity,
                    "average_price": position.average_price,
                    "market_value": market_value,
                    "equity": equity,
                    "weight": market_value / equity,
                    "target_weight": target_weight,
                }
            )
        previous_equity = equity

    trades = pd.DataFrame(
        [trade.to_dict() for trade in portfolio.trades],
        columns=TRADE_COLUMNS,
    )
    positions = pd.DataFrame(position_rows, columns=POSITION_COLUMNS)
    equity_curve = pd.DataFrame(equity_rows, columns=EQUITY_COLUMNS)
    summary = calculate_continuous_metrics(
        equity_curve,
        trades,
        initial_equity=config.initial_capital,
        start=equity_curve.iloc[0]["datetime"],
        end=equity_curve.iloc[-1]["datetime"],
    )
    summary.update(
        {
            "symbol": config.symbol,
            "initial_capital": config.initial_capital,
            "target_weight": current_weight,
            "is_open": config.symbol in portfolio.positions,
        }
    )
    return RegimeOverlayResult(
        signals=audited,
        trades=trades,
        positions=positions,
        equity_curve=equity_curve,
        summary=summary,
    )


def calculate_continuous_metrics(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    initial_equity: float,
    start: object,
    end: object,
) -> dict[str, Any]:
    """Calculate inclusive metrics while retaining the first day's return."""
    if not isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial_equity must be finite and positive")
    required = {"datetime", "equity"}
    missing = required - set(equity_curve.columns)
    if missing:
        raise ValueError(f"equity_curve missing columns: {sorted(missing)}")
    frame = equity_curve.copy()
    frame["datetime"] = _normalize_naive_dates(frame["datetime"], "datetime")
    frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    if (
        frame["equity"].isna().any()
        or not np.isfinite(frame["equity"]).all()
        or frame["equity"].le(0).any()
    ):
        raise ValueError("equity must be finite and positive")
    start_date = pd.Timestamp(start).normalize()
    end_date = pd.Timestamp(end).normalize()
    period = frame.loc[
        frame["datetime"].between(start_date, end_date, inclusive="both")
    ].sort_values("datetime", ignore_index=True)
    if period.empty:
        raise ValueError("no equity rows in requested period")

    returns = period["equity"].pct_change(fill_method=None)
    returns.iloc[0] = float(period.iloc[0]["equity"]) / initial_equity - 1.0
    values = np.concatenate([[initial_equity], period["equity"].to_numpy(dtype=float)])
    peaks = np.maximum.accumulate(values)
    drawdowns = values / peaks - 1.0
    trough_position = int(np.argmin(drawdowns))
    peak_position = int(np.argmax(values[: trough_position + 1]))
    drawdown_start = (
        pd.Timestamp(period.iloc[0]["datetime"])
        if peak_position == 0
        else pd.Timestamp(period.iloc[peak_position - 1]["datetime"])
    )
    drawdown_end = (
        pd.Timestamp(period.iloc[0]["datetime"])
        if trough_position == 0
        else pd.Timestamp(period.iloc[trough_position - 1]["datetime"])
    )

    period_trades = trades.copy()
    if period_trades.empty:
        traded_notional = 0.0
        commission = 0.0
        slippage_cost = 0.0
    else:
        trade_required = {
            "datetime",
            "fill_price",
            "quantity",
            "commission",
            "slippage_cost",
        }
        missing_trades = trade_required - set(period_trades.columns)
        if missing_trades:
            raise ValueError(f"trades missing columns: {sorted(missing_trades)}")
        period_trades["datetime"] = _normalize_naive_dates(
            period_trades["datetime"],
            "trade datetime",
        )
        period_trades = period_trades.loc[
            period_trades["datetime"].between(
                start_date,
                end_date,
                inclusive="both",
            )
        ]
        traded_notional = float(
            (
                period_trades["fill_price"].astype(float)
                * period_trades["quantity"].astype(float)
            )
            .abs()
            .sum()
        )
        commission = float(period_trades["commission"].sum())
        slippage_cost = float(period_trades["slippage_cost"].sum())

    days = len(period)
    years = max(days / 252.0, 1.0 / 252.0)
    final_equity = float(period.iloc[-1]["equity"])
    total_return = final_equity / initial_equity - 1.0
    annual_return = (
        (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1.0 else -1.0
    )
    daily_std = float(returns.std(ddof=0))
    mean_equity = float(period["equity"].mean())
    return {
        "start_date": str(pd.Timestamp(period.iloc[0]["datetime"]).date()),
        "end_date": str(pd.Timestamp(period.iloc[-1]["datetime"]).date()),
        "trading_days": days,
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": daily_std * sqrt(252.0),
        "sharpe": (
            float(returns.mean()) / daily_std * sqrt(252.0) if daily_std > 0 else 0.0
        ),
        "max_drawdown": float(drawdowns.min()),
        "max_drawdown_start": str(drawdown_start.date()),
        "max_drawdown_end": str(drawdown_end.date()),
        "annual_one_way_turnover": (
            0.5 * traded_notional / mean_equity / years if mean_equity > 0 else 0.0
        ),
        "trade_count": int(len(period_trades)),
        "total_commission": commission,
        "total_slippage_cost": slippage_cost,
    }


def run_regime_overlay_backtest(
    bars: pd.DataFrame,
    predictions: pd.DataFrame,
    *,
    start: object,
    end: object,
    config: TrendAllocationConfig,
) -> RegimeOverlayResult:
    """Run independent EMA targets and the final regime overlay from flat."""
    start_date = pd.Timestamp(start).normalize()
    end_date = pd.Timestamp(end).normalize()
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    ema_signals = build_trend_signals(bars, config)
    execution_signals = ema_signals.loc[
        ema_signals["datetime"].between(
            start_date,
            end_date,
            inclusive="both",
        )
    ].copy()
    if execution_signals.empty:
        raise ValueError("no bars in requested backtest period")
    ema_result = execute_target_weights(execution_signals, config)
    ema_audit = ema_result.signals.rename(
        columns={
            "target_weight": "ema_target_weight",
            "action": "ema_action",
            "risk_increase_blocked": "ema_risk_increase_blocked",
            "days_since_transition": "ema_days_since_transition",
        }
    )
    aligned = align_regime_predictions(predictions, bars).rename(
        columns={"execution_date": "datetime"}
    )
    aligned = aligned.loc[
        aligned["datetime"].between(start_date, end_date, inclusive="both")
    ]
    combined = ema_audit.merge(
        aligned,
        on=["symbol", "datetime"],
        how="left",
        validate="one_to_one",
    )
    if (
        combined[
            [
                "regime_feature_asof_date",
                "score_1d",
                "score_3d",
            ]
        ]
        .isna()
        .any(axis=None)
    ):
        raise ValueError("every execution date requires aligned regime predictions")
    result = execute_overlay_signals(combined, config)
    result.ema_result = ema_result
    return result


def calendar_periods(
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    frequency: Literal["year", "half_year"],
) -> list[PerformancePeriod]:
    """Create clipped natural calendar periods covering the full date range."""
    start_date = pd.Timestamp(start).normalize()
    end_date = pd.Timestamp(end).normalize()
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    if frequency not in {"year", "half_year"}:
        raise ValueError("frequency must be year or half_year")

    if frequency == "year":
        natural_start = pd.Timestamp(start_date.year, 1, 1)
    else:
        half_start_month = 1 if start_date.month <= 6 else 7
        natural_start = pd.Timestamp(start_date.year, half_start_month, 1)

    periods: list[PerformancePeriod] = []
    while natural_start <= end_date:
        if frequency == "year":
            natural_end = pd.Timestamp(natural_start.year, 12, 31)
            label = str(natural_start.year)
            next_start = pd.Timestamp(natural_start.year + 1, 1, 1)
        elif natural_start.month == 1:
            natural_end = pd.Timestamp(natural_start.year, 6, 30)
            label = f"{natural_start.year}-H1"
            next_start = pd.Timestamp(natural_start.year, 7, 1)
        else:
            natural_end = pd.Timestamp(natural_start.year, 12, 31)
            label = f"{natural_start.year}-H2"
            next_start = pd.Timestamp(natural_start.year + 1, 1, 1)
        clipped_start = max(start_date, natural_start)
        clipped_end = min(end_date, natural_end)
        periods.append(
            PerformancePeriod(
                label=label,
                start=clipped_start,
                end=clipped_end,
                is_partial_period=(
                    clipped_start > natural_start or clipped_end < natural_end
                ),
            )
        )
        natural_start = next_start
    return periods


def calculate_period_table(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    initial_capital: float,
    periods: Sequence[PerformancePeriod],
    strategy: str,
) -> pd.DataFrame:
    """Calculate period metrics without resetting the underlying strategy."""
    if not strategy.strip():
        raise ValueError("strategy must not be empty")
    equity = equity_curve.copy()
    if not {"datetime", "equity"} <= set(equity.columns):
        raise ValueError("equity_curve requires datetime and equity")
    equity["datetime"] = _normalize_naive_dates(equity["datetime"], "datetime")
    if equity["datetime"].duplicated().any():
        raise ValueError("equity_curve contains duplicate dates")
    equity = equity.sort_values("datetime", ignore_index=True)

    rows: list[dict[str, object]] = []
    for period in periods:
        period_rows = equity.loc[
            equity["datetime"].between(
                period.start,
                period.end,
                inclusive="both",
            )
        ]
        if period_rows.empty:
            continue
        actual_start = pd.Timestamp(period_rows.iloc[0]["datetime"])
        actual_end = pd.Timestamp(period_rows.iloc[-1]["datetime"])
        prior_rows = equity.loc[equity["datetime"] < actual_start]
        opening_equity = (
            initial_capital
            if prior_rows.empty
            else float(prior_rows.iloc[-1]["equity"])
        )
        metrics = calculate_continuous_metrics(
            equity,
            trades,
            initial_equity=opening_equity,
            start=actual_start,
            end=actual_end,
        )
        rows.append(
            {
                "period": period.label,
                "period_start": str(actual_start.date()),
                "period_end": str(actual_end.date()),
                "is_partial_period": period.is_partial_period,
                "strategy": strategy,
                **metrics,
            }
        )
    return pd.DataFrame(rows)


def build_buy_hold_equity(
    bars: pd.DataFrame,
    *,
    initial_capital: float,
) -> pd.DataFrame:
    """Build a close-to-close, zero-cost buy-and-hold benchmark."""
    if not isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial_capital must be finite and positive")
    required = {"datetime", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars missing columns: {sorted(missing)}")
    frame = bars.loc[:, ["datetime", "close"]].copy()
    frame["datetime"] = _normalize_naive_dates(frame["datetime"], "datetime")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if (
        frame["close"].isna().any()
        or not np.isfinite(frame["close"]).all()
        or frame["close"].le(0).any()
    ):
        raise ValueError("buy-and-hold close must be finite and positive")
    frame = frame.sort_values("datetime", ignore_index=True)
    first_close = float(frame.iloc[0]["close"])
    equity = initial_capital * frame["close"] / first_close
    daily_return = equity.pct_change(fill_method=None).fillna(0.0)
    drawdown = equity / equity.cummax() - 1.0
    return pd.DataFrame(
        {
            "datetime": frame["datetime"],
            "cash": 0.0,
            "market_value": equity,
            "equity": equity,
            "daily_return": daily_return,
            "drawdown": drawdown,
        }
    )
