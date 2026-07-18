from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .data import prepare_a_share_index_metadata
from .indicators import add_indicators
from .portfolio import Portfolio, Position, calculate_order_quantity
from .ranking import (
    audit_daily_candidates,
    build_daily_ranking,
    entry_symbols,
    is_risk_on,
)
from .risk import (
    MarketState,
    MarketStateTracker,
    calculate_breadth,
    classify_market_candidate,
)

TRADE_COLUMNS = [
    "datetime",
    "symbol",
    "side",
    "quantity",
    "raw_price",
    "fill_price",
    "commission",
    "slippage_cost",
    "primary_reason",
    "all_reasons",
    "realized_pnl",
]
POSITION_COLUMNS = [
    "datetime",
    "symbol",
    "quantity",
    "average_price",
    "stop_price",
    "market_value",
    "weight",
    "industry",
    "industry_weight",
    "benchmark_key",
]
CANDIDATE_COLUMNS = [
    "symbol",
    "datetime",
    "signal_date",
    "exclusion_reason",
    "name",
    "list_date",
    "delist_date",
    "status",
    "fund_type",
    "benchmark",
    "industry",
    "benchmark_key",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "turnover_median20",
    "bar_count",
    "ema5",
    "ema10",
    "ema20",
    "return_3",
    "return_5",
    "return_10",
    "return_20",
    "ema5_slope",
    "atr5",
    "normalized_atr5",
    "risk_atr",
    "trend_adx",
    "rank_return_3",
    "rank_return_5",
    "rank_return_10",
    "rank_return_20",
    "rank_ema5_slope",
    "rank_normalized_atr5",
    "rs_score",
    "rs_rank",
    "entry_rank",
    "holding_rank",
    "entry_liquidity_eligible",
    "entry_representative",
    "trend_confirmed",
]


@dataclass
class BacktestResult:
    """Audit tables produced by a completed backtest."""

    candidates: pd.DataFrame
    signals: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: dict[str, Any]


@dataclass(frozen=True)
class PendingEntry:
    """Next-open order details fixed at the signal close."""

    symbol: str
    risk_atr: float
    atr5: float
    signal_close: float
    signal_date: pd.Timestamp
    industry: str
    benchmark_key: str
    risk_fraction: float
    entry_rank_limit: int


def _prepare_etfs(
    etfs: pd.DataFrame, metadata: pd.DataFrame, config: StrategyConfig
) -> pd.DataFrame:
    meta = prepare_a_share_index_metadata(metadata)
    meta["list_date"] = pd.to_datetime(meta["list_date"])
    if "delist_date" in meta:
        meta["delist_date"] = pd.to_datetime(meta["delist_date"], errors="coerce")
    selected_symbols = set(meta["symbol"])
    frames: list[pd.DataFrame] = []
    selected_etfs = etfs.loc[etfs["symbol"].isin(selected_symbols)].copy()
    selected_etfs["datetime"] = pd.to_datetime(selected_etfs["datetime"])
    lifecycle = meta.drop_duplicates("symbol", keep="last").set_index("symbol")
    list_dates = selected_etfs["symbol"].map(lifecycle["list_date"])
    within_lifecycle = list_dates.notna() & (selected_etfs["datetime"] >= list_dates)
    if "delist_date" in lifecycle:
        delist_dates = selected_etfs["symbol"].map(lifecycle["delist_date"])
        within_lifecycle &= delist_dates.isna() | (
            selected_etfs["datetime"] <= delist_dates
        )
    selected_etfs = selected_etfs.loc[within_lifecycle]
    for symbol, bars in selected_etfs.groupby("symbol", sort=True):
        prepared = add_indicators(bars, config.atr_period, config.adx_period)
        prepared["symbol"] = symbol
        frames.append(prepared)
    if not frames:
        raise ValueError("no eligible A-share index ETF has daily bars")
    combined = pd.concat(frames, ignore_index=True)
    extra_meta = [
        column
        for column in (
            "symbol",
            "name",
            "list_date",
            "delist_date",
            "status",
            "fund_type",
            "benchmark",
            "benchmark_key",
            "asset_scope",
            "industry",
        )
        if column in meta
    ]
    combined = combined.merge(meta[extra_meta], on="symbol", how="left")
    if "is_trading" not in combined:
        combined["is_trading"] = True
    return combined


def _industry_market_values(
    portfolio: Portfolio,
    industry_by_symbol: dict[str, str],
    daily: pd.DataFrame,
    last_close: dict[str, float],
) -> dict[str, float]:
    values: dict[str, float] = {}
    for symbol, position in portfolio.positions.items():
        industry = industry_by_symbol.get(symbol, "broad_or_other")
        if industry == "broad_or_other":
            continue
        if symbol in daily.index and pd.notna(daily.loc[symbol, "open"]):
            price = float(daily.loc[symbol, "open"])
        else:
            price = last_close.get(symbol, position.average_price)
        values[industry] = values.get(industry, 0.0) + position.quantity * price
    return values


def _position_market_values(
    portfolio: Portfolio,
    daily: pd.DataFrame,
    last_close: dict[str, float],
) -> dict[str, float]:
    values: dict[str, float] = {}
    for symbol, position in portfolio.positions.items():
        if symbol in daily.index and pd.notna(daily.loc[symbol, "open"]):
            price = float(daily.loc[symbol, "open"])
        else:
            price = last_close.get(symbol, position.average_price)
        values[symbol] = position.quantity * price
    return values


def _correlated_cluster_symbols(
    candidate: str,
    held_symbols: set[str],
    returns: pd.DataFrame,
    signal_date: pd.Timestamp,
    config: StrategyConfig,
) -> set[str]:
    if config.correlation_threshold is None or not held_symbols:
        return set()
    nodes = [candidate, *sorted(held_symbols)]
    available = [symbol for symbol in nodes if symbol in returns.columns]
    adjacency = {symbol: set() for symbol in available}
    history = returns.loc[returns.index <= signal_date]
    for left_index, left in enumerate(available):
        for right in available[left_index + 1 :]:
            pair = history[[left, right]].dropna().tail(config.correlation_lookback)
            if len(pair) < config.min_correlation_observations:
                continue
            correlation = pair[left].corr(pair[right])
            if pd.notna(correlation) and correlation >= config.correlation_threshold:
                adjacency[left].add(right)
                adjacency[right].add(left)
    if candidate not in adjacency:
        return set()
    cluster = {candidate}
    frontier = [candidate]
    while frontier:
        current = frontier.pop()
        for neighbour in adjacency[current] - cluster:
            cluster.add(neighbour)
            frontier.append(neighbour)
    return cluster & held_symbols


def _winner_reasons_for_exit(
    position: Position,
    ranking_by_symbol: pd.DataFrame,
    etf_row: pd.Series,
    config: StrategyConfig,
) -> list[str]:
    """Return slow exit reasons while maintaining consecutive rank weakness."""
    if position.symbol in ranking_by_symbol.index:
        holding_rank = ranking_by_symbol.loc[position.symbol, "holding_rank"]
        rank_is_weak = pd.notna(holding_rank) and int(holding_rank) > config.exit_rank
    else:
        rank_is_weak = False
    position.weak_rank_days = position.weak_rank_days + 1 if rank_is_weak else 0

    close = float(etf_row["close"])
    reasons: list[str] = []
    if close < float(etf_row["ema20"]):
        reasons.append("etf_below_ema20")
    if position.weak_rank_days >= config.exit_rank_confirmation_days and close < float(
        etf_row["ema10"]
    ):
        reasons.append("confirmed_rank_and_ema10_weakness")
    return reasons


def _reasons_for_exit(
    position: Position,
    benchmark_row: pd.Series,
    ranking_by_symbol: pd.DataFrame,
    etf_row: pd.Series | None,
    config: StrategyConfig,
) -> list[str]:
    symbol = position.symbol
    if config.winner_holding_enabled:
        if etf_row is None:
            position.weak_rank_days = 0
            return []
        return _winner_reasons_for_exit(
            position,
            ranking_by_symbol,
            etf_row,
            config,
        )
    reasons: list[str] = []
    if (
        not config.market_state_enabled
        and benchmark_row["close"] < benchmark_row["ema10"]
    ):
        reasons.append("market_below_ema10")
    if etf_row is not None and etf_row["close"] < etf_row["ema10"]:
        reasons.append("etf_below_ema10")
    if symbol not in ranking_by_symbol.index:
        reasons.append("rs_unavailable")
    elif int(ranking_by_symbol.loc[symbol, "holding_rank"]) > config.exit_rank:
        reasons.append("rs_out_top10")
    return reasons


def _primary_reason(reasons: list[str]) -> str:
    priority = [
        "portfolio_risk_off",
        "etf_below_ema20",
        "confirmed_rank_and_ema10_weakness",
        "market_below_ema10",
        "etf_below_ema10",
        "rs_out_top10",
        "rs_unavailable",
    ]
    return next(reason for reason in priority if reason in reasons)


def run_backtest(
    benchmark_daily: pd.DataFrame,
    etf_daily: pd.DataFrame,
    metadata: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    start_date: object | None = None,
    end_date: object | None = None,
) -> BacktestResult:
    """Run the daily ETF strategy without using data beyond each signal close."""
    cfg = config or StrategyConfig()
    benchmark = add_indicators(benchmark_daily, cfg.atr_period, cfg.adx_period)
    benchmark = benchmark.sort_values("datetime").reset_index(drop=True)
    benchmark["datetime"] = pd.to_datetime(benchmark["datetime"])
    benchmark["previous_ema5"] = benchmark["ema5"].shift(1)
    if start_date is not None:
        benchmark = benchmark.loc[benchmark["datetime"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        benchmark = benchmark.loc[benchmark["datetime"] <= pd.Timestamp(end_date)]
    benchmark = benchmark.reset_index(drop=True)
    if benchmark.empty:
        raise ValueError("benchmark has no rows in the requested report range")
    etfs = _prepare_etfs(etf_daily, metadata, cfg)
    etfs["datetime"] = pd.to_datetime(etfs["datetime"])
    etfs_by_date = {
        date: rows.set_index("symbol", drop=False)
        for date, rows in etfs.groupby("datetime")
    }
    empty_daily = etfs.iloc[0:0].set_index("symbol", drop=False)
    close_history = etfs.pivot_table(
        index="datetime",
        columns="symbol",
        values="close",
        aggfunc="last",
    ).sort_index()
    etf_returns = close_history.pct_change(fill_method=None)
    industry_by_symbol = (
        etfs[["symbol", "industry"]]
        .drop_duplicates("symbol", keep="last")
        .set_index("symbol")["industry"]
        .astype(str)
        .to_dict()
    )
    benchmark_key_by_symbol = (
        etfs[["symbol", "benchmark_key"]]
        .drop_duplicates("symbol", keep="last")
        .set_index("symbol")["benchmark_key"]
        .astype(str)
        .to_dict()
    )
    delist_date_by_symbol = (
        etfs[["symbol", "delist_date"]]
        .drop_duplicates("symbol", keep="last")
        .set_index("symbol")["delist_date"]
        .to_dict()
        if "delist_date" in etfs
        else {}
    )

    portfolio = Portfolio(cfg.initial_capital, cfg)
    pending_exits: dict[str, list[str]] = {}
    pending_entries: list[PendingEntry] = []
    entry_streaks: dict[str, int] = {}
    candidate_frames: list[pd.DataFrame] = []
    signal_records: list[dict[str, Any]] = []
    position_records: list[dict[str, Any]] = []
    equity_records: list[dict[str, Any]] = []
    last_close: dict[str, float] = {}
    previous_equity = cfg.initial_capital
    market_tracker = MarketStateTracker(
        state=MarketState.CAUTION,
        confirmation_days=cfg.market_state_confirmation_days,
    )

    for index, benchmark_row in benchmark.iterrows():
        date = pd.Timestamp(benchmark_row["datetime"])
        daily = etfs_by_date.get(date, empty_daily)

        carried_exits: dict[str, list[str]] = {}
        for symbol, reasons in pending_exits.items():
            if symbol not in portfolio.positions:
                continue
            if symbol not in daily.index or pd.isna(daily.loc[symbol, "open"]):
                carried_exits[symbol] = reasons
                continue
            portfolio.sell(
                symbol,
                date,
                float(daily.loc[symbol, "open"]),
                _primary_reason(reasons),
                "|".join(reasons),
            )
        pending_exits = carried_exits

        for symbol in list(portfolio.positions):
            delist_date = delist_date_by_symbol.get(symbol)
            has_open = symbol in daily.index and pd.notna(daily.loc[symbol, "open"])
            if (
                pd.notna(delist_date)
                and date >= pd.Timestamp(delist_date)
                and not has_open
            ):
                portfolio.sell(symbol, date, 0.0, "delisted_writeoff")
                pending_exits.pop(symbol, None)

        industry_values = _industry_market_values(
            portfolio,
            industry_by_symbol,
            daily,
            last_close,
        )
        execution_position_values = _position_market_values(
            portfolio, daily, last_close
        )
        held_benchmark_keys = {
            benchmark_key_by_symbol.get(symbol, f"symbol:{symbol}")
            for symbol in portfolio.positions
        }
        for pending_entry in pending_entries:
            symbol = pending_entry.symbol
            risk_atr = pending_entry.risk_atr
            signal_date = pending_entry.signal_date
            industry = pending_entry.industry
            benchmark_key = pending_entry.benchmark_key
            if symbol in portfolio.positions or symbol in pending_exits:
                continue
            if benchmark_key in held_benchmark_keys:
                signal_records.append(
                    {
                        "signal_date": signal_date,
                        "execution_date": date,
                        "symbol": symbol,
                        "action": "buy_skipped",
                        "reason": "benchmark_duplicate",
                        "industry": industry,
                        "benchmark_key": benchmark_key,
                    }
                )
                continue
            if len(portfolio.positions) >= cfg.entry_rank:
                break
            if symbol not in daily.index or pd.isna(daily.loc[symbol, "open"]):
                continue
            raw_open = float(daily.loc[symbol, "open"])
            if (
                cfg.max_entry_gap_atr is not None
                and raw_open
                > pending_entry.signal_close
                + cfg.max_entry_gap_atr * pending_entry.atr5
            ):
                signal_records.append(
                    {
                        "signal_date": signal_date,
                        "execution_date": date,
                        "symbol": symbol,
                        "action": "buy_skipped",
                        "reason": "gap_filter",
                        "industry": industry,
                        "benchmark_key": benchmark_key,
                        "signal_close": pending_entry.signal_close,
                        "signal_atr5": pending_entry.atr5,
                        "execution_open": raw_open,
                    }
                )
                continue
            estimated_fill = raw_open * (1 + cfg.slippage_rate)
            uncapped_quantity = calculate_order_quantity(
                previous_equity,
                portfolio.cash,
                estimated_fill,
                risk_atr,
                cfg,
                risk_fraction=pending_entry.risk_fraction,
            )
            constraint_caps: dict[str, float] = {}
            if industry != "broad_or_other":
                constraint_caps["industry_cap"] = max(
                    previous_equity * cfg.max_industry_weight
                    - industry_values.get(industry, 0.0),
                    0.0,
                )
            correlated_symbols = _correlated_cluster_symbols(
                symbol,
                set(portfolio.positions),
                etf_returns,
                signal_date,
                cfg,
            )
            correlated_exposure = sum(
                execution_position_values.get(held_symbol, 0.0)
                for held_symbol in correlated_symbols
            )
            if cfg.max_correlation_weight is not None:
                constraint_caps["correlation_cap"] = max(
                    previous_equity * cfg.max_correlation_weight - correlated_exposure,
                    0.0,
                )
            binding_constraint = (
                min(constraint_caps, key=constraint_caps.get)
                if constraint_caps
                else None
            )
            max_additional_notional = (
                constraint_caps[binding_constraint]
                if binding_constraint is not None
                else None
            )
            quantity = calculate_order_quantity(
                previous_equity,
                portfolio.cash,
                estimated_fill,
                risk_atr,
                cfg,
                max_additional_notional=max_additional_notional,
                risk_fraction=pending_entry.risk_fraction,
            )
            if quantity:
                trade = portfolio.buy(
                    symbol,
                    date,
                    raw_open,
                    quantity,
                    risk_atr,
                    f"rs_top{pending_entry.entry_rank_limit}",
                )
                industry_weight_after_entry: float | None = None
                if industry != "broad_or_other":
                    industry_values[industry] = (
                        industry_values.get(industry, 0.0)
                        + trade.quantity * trade.fill_price
                    )
                    industry_weight_after_entry = (
                        industry_values[industry] / previous_equity
                    )
                execution_position_values[symbol] = trade.quantity * trade.fill_price
                correlation_weight_after_entry: float | None = None
                if cfg.max_correlation_weight is not None:
                    correlation_weight_after_entry = (
                        correlated_exposure + trade.quantity * trade.fill_price
                    ) / previous_equity
                held_benchmark_keys.add(benchmark_key)
                entry_streaks.pop(symbol, None)
                signal_records.append(
                    {
                        "signal_date": signal_date,
                        "execution_date": date,
                        "symbol": symbol,
                        "action": "buy_filled",
                        "reason": f"rs_top{pending_entry.entry_rank_limit}",
                        "industry": industry,
                        "benchmark_key": benchmark_key,
                        "industry_weight_after_entry": industry_weight_after_entry,
                        "correlation_weight_after_entry": correlation_weight_after_entry,
                        "correlated_symbols": "|".join(sorted(correlated_symbols)),
                        "risk_fraction": pending_entry.risk_fraction,
                    }
                )
            elif uncapped_quantity and binding_constraint is not None:
                signal_records.append(
                    {
                        "signal_date": signal_date,
                        "execution_date": date,
                        "symbol": symbol,
                        "action": "buy_skipped",
                        "reason": binding_constraint,
                        "industry": industry,
                        "benchmark_key": benchmark_key,
                        "industry_weight_after_entry": industry_values.get(
                            industry, 0.0
                        )
                        / previous_equity,
                        "correlation_weight_after_entry": correlated_exposure
                        / previous_equity,
                        "correlated_symbols": "|".join(sorted(correlated_symbols)),
                    }
                )
        pending_entries = []

        for symbol in list(portfolio.positions):
            if symbol in daily.index:
                portfolio.check_stop(
                    symbol,
                    date,
                    float(daily.loc[symbol, "open"]),
                    float(daily.loc[symbol, "low"]),
                )

        if not daily.empty:
            last_close.update(daily["close"].astype(float).to_dict())
        equity = portfolio.equity(last_close)
        daily_return = equity / previous_equity - 1 if previous_equity else 0.0
        equity_records.append(
            {
                "datetime": date,
                "cash": portfolio.cash,
                "market_value": equity - portfolio.cash,
                "equity": equity,
                "daily_return": daily_return,
            }
        )
        position_values = {
            symbol: position.quantity * last_close.get(symbol, position.average_price)
            for symbol, position in portfolio.positions.items()
        }
        close_industry_values: dict[str, float] = {}
        for symbol, market_value in position_values.items():
            industry = industry_by_symbol.get(symbol, "broad_or_other")
            if industry != "broad_or_other":
                close_industry_values[industry] = (
                    close_industry_values.get(industry, 0.0) + market_value
                )
        for symbol, position in sorted(portfolio.positions.items()):
            market_value = position_values[symbol]
            industry = industry_by_symbol.get(symbol, "broad_or_other")
            position_records.append(
                {
                    "datetime": date,
                    "symbol": symbol,
                    "quantity": position.quantity,
                    "average_price": position.average_price,
                    "stop_price": position.stop_price,
                    "market_value": market_value,
                    "weight": market_value / equity if equity else 0.0,
                    "industry": industry,
                    "benchmark_key": benchmark_key_by_symbol.get(
                        symbol,
                        f"symbol:{symbol}",
                    ),
                    "industry_weight": close_industry_values[industry] / equity
                    if equity and industry != "broad_or_other"
                    else np.nan,
                }
            )
        previous_equity = equity

        daily_cross_section = daily.reset_index(drop=True)
        ranking = build_daily_ranking(daily_cross_section, date, cfg)
        candidate_audit = audit_daily_candidates(
            daily_cross_section,
            date,
            cfg,
            ranking=ranking,
        )
        if not candidate_audit.empty:
            candidate_audit["signal_date"] = date
            candidate_frames.append(candidate_audit.reindex(columns=CANDIDATE_COLUMNS))
        ranking_by_symbol = (
            ranking.set_index("symbol", drop=False)
            if not ranking.empty
            else pd.DataFrame()
        )
        current_rows = daily
        previous_ema5 = benchmark_row["previous_ema5"]
        broad_risk_on = is_risk_on(benchmark_row, previous_ema5, cfg)
        if cfg.market_state_enabled:
            breadth = calculate_breadth(daily)
            candidate_state = classify_market_candidate(
                benchmark_row,
                broad_risk_on,
                breadth,
                cfg,
            )
            market_state = market_tracker.advance(candidate_state)
            entry_allowed = market_state != MarketState.RISK_OFF
            entry_rank_limit = (
                cfg.entry_rank
                if market_state == MarketState.RISK_ON
                else cfg.caution_entry_rank
            )
            entry_risk_fraction = (
                1.0
                if market_state == MarketState.RISK_ON
                else cfg.caution_risk_fraction
            )
            signal_records.append(
                {
                    "signal_date": date,
                    "execution_date": benchmark.iloc[index + 1]["datetime"]
                    if index + 1 < len(benchmark)
                    else pd.NaT,
                    "symbol": cfg.benchmark_symbol,
                    "action": f"market_{market_state.value}",
                    "reason": "confirmed_market_state",
                    "breadth": breadth,
                    "candidate_state": candidate_state.value,
                    "state_confirmation_days": market_tracker.candidate_days,
                }
            )
        else:
            market_state = (
                MarketState.RISK_ON if broad_risk_on else MarketState.RISK_OFF
            )
            entry_allowed = broad_risk_on
            entry_rank_limit = cfg.entry_rank
            entry_risk_fraction = 1.0
            signal_records.append(
                {
                    "signal_date": date,
                    "execution_date": benchmark.iloc[index + 1]["datetime"]
                    if index + 1 < len(benchmark)
                    else pd.NaT,
                    "symbol": cfg.benchmark_symbol,
                    "action": "risk_on" if broad_risk_on else "risk_off",
                    "reason": "market_filter",
                }
            )
        planned_exits: dict[str, list[str]] = dict(pending_exits)
        for symbol, position in portfolio.positions.items():
            row = current_rows.loc[symbol] if symbol in current_rows.index else None
            if (
                cfg.winner_holding_enabled
                and row is not None
                and pd.notna(row.get("close"))
                and pd.notna(row.get("ema10"))
                and pd.notna(row.get("ema20"))
                and pd.notna(row.get("atr5"))
            ):
                portfolio.update_after_close(
                    symbol,
                    float(row["close"]),
                    float(row["ema10"]),
                    float(row["ema20"]),
                    float(row["atr5"]),
                )
            reasons = _reasons_for_exit(
                position, benchmark_row, ranking_by_symbol, row, cfg
            )
            if cfg.market_state_enabled and market_state == MarketState.RISK_OFF:
                reasons.insert(0, "portfolio_risk_off")
            if reasons:
                planned_exits[symbol] = reasons
                signal_records.append(
                    {
                        "signal_date": date,
                        "execution_date": benchmark.iloc[index + 1]["datetime"]
                        if index + 1 < len(benchmark)
                        else pd.NaT,
                        "symbol": symbol,
                        "action": "sell_planned",
                        "reason": "|".join(reasons),
                    }
                )
            elif symbol not in pending_exits:
                signal_records.append(
                    {
                        "signal_date": date,
                        "execution_date": pd.NaT,
                        "symbol": symbol,
                        "action": "hold",
                        "reason": "exit_conditions_not_met",
                    }
                )
        pending_exits = planned_exits

        ranked_entries = entry_symbols(ranking, cfg) if entry_allowed else []
        if cfg.market_state_enabled and ranked_entries:
            ranked_entries = [
                symbol
                for symbol in ranked_entries
                if int(ranking_by_symbol.loc[symbol, "entry_rank"]) <= entry_rank_limit
            ]
        qualified_entries = {
            symbol
            for symbol in ranked_entries
            if symbol not in portfolio.positions and symbol not in pending_exits
        }
        if entry_allowed:
            for symbol in set(entry_streaks) - qualified_entries:
                entry_streaks.pop(symbol, None)
            for symbol in qualified_entries:
                entry_streaks[symbol] = entry_streaks.get(symbol, 0) + 1
        else:
            entry_streaks.clear()
        if entry_allowed and index + 1 < len(benchmark):
            for symbol in ranked_entries:
                if symbol in portfolio.positions or symbol in pending_exits:
                    continue
                if entry_streaks.get(symbol, 0) < cfg.entry_confirmation_days:
                    continue
                risk_atr = float(ranking_by_symbol.loc[symbol, "risk_atr"])
                atr5 = float(ranking_by_symbol.loc[symbol, "atr5"])
                signal_close = float(ranking_by_symbol.loc[symbol, "close"])
                industry = str(ranking_by_symbol.loc[symbol, "industry"])
                benchmark_key = str(ranking_by_symbol.loc[symbol, "benchmark_key"])
                pending_entries.append(
                    PendingEntry(
                        symbol=symbol,
                        risk_atr=risk_atr,
                        atr5=atr5,
                        signal_close=signal_close,
                        signal_date=date,
                        industry=industry,
                        benchmark_key=benchmark_key,
                        risk_fraction=entry_risk_fraction,
                        entry_rank_limit=entry_rank_limit,
                    )
                )
                signal_records.append(
                    {
                        "signal_date": date,
                        "execution_date": benchmark.iloc[index + 1]["datetime"],
                        "symbol": symbol,
                        "action": "buy_planned",
                        "reason": f"rs_top{entry_rank_limit}",
                        "industry": industry,
                        "benchmark_key": benchmark_key,
                        "confirmation_days": entry_streaks[symbol],
                        "risk_fraction": entry_risk_fraction,
                    }
                )

    equity_curve = pd.DataFrame(equity_records)
    equity_curve["drawdown"] = (
        equity_curve["equity"] / equity_curve["equity"].cummax() - 1
    )
    trades = pd.DataFrame(
        [trade.to_dict() for trade in portfolio.trades], columns=TRADE_COLUMNS
    )
    positions = pd.DataFrame(position_records, columns=POSITION_COLUMNS)
    candidates = (
        pd.concat(candidate_frames, ignore_index=True)
        if candidate_frames
        else pd.DataFrame(columns=CANDIDATE_COLUMNS)
    )
    signals = pd.DataFrame(signal_records)
    summary = _build_summary(equity_curve, trades, cfg.initial_capital)
    return BacktestResult(candidates, signals, trades, positions, equity_curve, summary)


def _build_summary(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    initial_capital: float,
) -> dict[str, Any]:
    final_equity = (
        float(equity_curve.iloc[-1]["equity"])
        if not equity_curve.empty
        else initial_capital
    )
    total_return = final_equity / initial_capital - 1
    years = max(len(equity_curve) / 252, 1 / 252)
    annual_return = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0
    returns = (
        equity_curve["daily_return"]
        if not equity_curve.empty
        else pd.Series(dtype=float)
    )
    volatility = float(returns.std(ddof=0) * np.sqrt(252)) if len(returns) else 0.0
    sharpe = (
        float(returns.mean() / returns.std(ddof=0) * np.sqrt(252))
        if returns.std(ddof=0) > 0
        else 0.0
    )
    sells = (
        trades.loc[trades.get("side", pd.Series(dtype=str)) == "sell"]
        if not trades.empty
        else trades
    )
    return {
        "start_date": str(pd.Timestamp(equity_curve.iloc[0]["datetime"]).date())
        if not equity_curve.empty
        else None,
        "end_date": str(pd.Timestamp(equity_curve.iloc[-1]["datetime"]).date())
        if not equity_curve.empty
        else None,
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": float(equity_curve["drawdown"].min())
        if not equity_curve.empty
        else 0.0,
        "annual_volatility": volatility,
        "sharpe": sharpe,
        "trade_count": int(len(trades)),
        "win_rate": float((sells["realized_pnl"] > 0).mean())
        if not sells.empty
        else 0.0,
        "total_commission": float(trades["commission"].sum())
        if not trades.empty
        else 0.0,
        "total_slippage_cost": float(trades["slippage_cost"].sum())
        if not trades.empty
        else 0.0,
    }


def write_backtest_outputs(
    result: BacktestResult,
    output_dir: Path,
    config: StrategyConfig,
    *,
    run_metadata: dict[str, Any] | None = None,
) -> None:
    """Write deterministic audit CSVs and a JSON summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result.candidates.to_csv(output_dir / "daily_candidates.csv", index=False)
    result.signals.to_csv(output_dir / "daily_signals.csv", index=False)
    result.trades.to_csv(output_dir / "trades.csv", index=False)
    result.positions.to_csv(output_dir / "positions.csv", index=False)
    result.equity_curve.to_csv(output_dir / "equity_curve.csv", index=False)
    payload = dict(result.summary)
    payload["parameters"] = asdict(config)
    payload.update(run_metadata or {})
    with (output_dir / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=True, indent=2, sort_keys=True)
