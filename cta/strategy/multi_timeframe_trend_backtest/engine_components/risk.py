"""Daily circuit, sector, and per-symbol cooldown controls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from cta.config.replay_common import BaseReplayConfig

from .models import _Position

@dataclass
class _DailyCircuitState:
    trade_date: date | None = None
    realized_pnl: float = 0.0
    loss_streak: int = 0
    tripped: bool = False
    trip_reason: str = ""


@dataclass
class _SymbolLossCooldownState:
    last_loss_exit_time: pd.Timestamp | None = None
    cooldown_trigger_time: pd.Timestamp | None = None
    cooldown_release_time: pd.Timestamp | None = None


def _market_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        raise ValueError("symbol loss cooldown timestamp must be timezone-aware")
    return timestamp.tz_convert("Asia/Shanghai")


def _next_day_open_after(minimum_time: pd.Timestamp) -> pd.Timestamp:
    minimum_time = _market_timestamp(minimum_time)
    day_open = minimum_time.normalize() + pd.Timedelta(hours=9)
    if minimum_time <= day_open:
        return day_open
    return day_open + pd.Timedelta(days=1)


def _advance_daily_circuit(
    state: _DailyCircuitState,
    *,
    trade_date: Any,
    net_pnl: float,
    equity: float,
    config: BaseReplayConfig,
) -> None:
    """Fold one closed trade into the exchange-trade-date circuit breaker."""
    if not config.daily_circuit_breaker_enabled:
        return
    current = pd.Timestamp(trade_date).date()
    if state.trade_date != current:
        state.trade_date = current
        state.realized_pnl = 0.0
        state.loss_streak = 0
        state.tripped = False
        state.trip_reason = ""
    state.realized_pnl += float(net_pnl)
    if float(net_pnl) < 0:
        state.loss_streak += 1
    else:
        state.loss_streak = 0
    risk_unit = float(equity) * float(config.risk_per_trade)
    reasons = []
    if (
        risk_unit > 0
        and state.realized_pnl
        <= -float(config.daily_circuit_breaker_loss_r) * risk_unit
    ):
        reasons.append("LOSS_R")
    if state.loss_streak >= int(config.daily_circuit_breaker_loss_streak):
        reasons.append("LOSS_STREAK")
    if reasons:
        state.tripped = True
        state.trip_reason = "+".join(reasons)


def _daily_circuit_blocked(
    state: _DailyCircuitState,
    trade_date: Any,
    config: BaseReplayConfig,
) -> str:
    """Return a rejection detail when the circuit breaker blocks new entries."""
    if not config.daily_circuit_breaker_enabled or not state.tripped:
        return ""
    if state.trade_date != pd.Timestamp(trade_date).date():
        return ""
    return (
        f"reason={state.trip_reason};realized_pnl={state.realized_pnl:.12g};"
        f"loss_streak={state.loss_streak}"
    )


def _sector_exposure_blocked(
    root_symbol: str,
    sector_by_root: dict[str, str],
    positions: dict[str, "_Position"],
    pending: dict[str, Any],
    config: BaseReplayConfig,
) -> str:
    """Return a rejection detail when the root's sector is already at its cap."""
    limit = int(config.max_positions_per_sector)
    sector = sector_by_root.get(root_symbol, "")
    if not sector:
        return ""
    active = {
        root
        for root in set(positions) | set(pending)
        if sector_by_root.get(root, "") == sector
    }
    active.discard(root_symbol)
    if len(active) < limit:
        return ""
    return f"sector={sector};active={len(active)};limit={limit}"


def _advance_symbol_loss_cooldown(
    state: _SymbolLossCooldownState,
    *,
    trade: dict[str, Any],
    config: BaseReplayConfig,
) -> _SymbolLossCooldownState:
    if not config.symbol_loss_cooldown_enabled:
        return state

    exit_time = _market_timestamp(trade["exit_time"])
    if float(trade["net_pnl"]) >= 0:
        state.last_loss_exit_time = None
        state.cooldown_trigger_time = None
        state.cooldown_release_time = None
        return state

    previous_loss_exit_time = state.last_loss_exit_time
    if previous_loss_exit_time is not None:
        previous_loss_exit_time = _market_timestamp(previous_loss_exit_time)
    state.last_loss_exit_time = exit_time
    state.cooldown_trigger_time = None
    state.cooldown_release_time = None

    if previous_loss_exit_time is None:
        return state

    loss_interval = exit_time - previous_loss_exit_time
    pair_window = pd.Timedelta(hours=config.symbol_loss_pair_window_hours)
    if pd.Timedelta(0) <= loss_interval <= pair_window:
        state.cooldown_trigger_time = exit_time
        minimum_release_time = exit_time + pd.Timedelta(
            hours=config.symbol_loss_cooldown_hours
        )
        state.cooldown_release_time = _next_day_open_after(minimum_release_time)
    return state


def _symbol_loss_cooldown_detail(
    state: _SymbolLossCooldownState,
    timestamp: pd.Timestamp,
    config: BaseReplayConfig,
) -> str:
    if not config.symbol_loss_cooldown_enabled:
        return ""

    timestamp = _market_timestamp(timestamp)
    if state.cooldown_trigger_time is None or state.cooldown_release_time is None:
        return ""
    trigger_time = _market_timestamp(state.cooldown_trigger_time)
    release_time = _market_timestamp(state.cooldown_release_time)
    if timestamp >= release_time:
        return ""
    return (
        f"second_loss_exit={trigger_time.isoformat()}; "
        f"release_at={release_time.isoformat()}"
    )
