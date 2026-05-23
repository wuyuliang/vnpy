"""Cross-instrument/calendar spread arbitrage strategy core."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from cta.config.spread_arbitrage_config import SpreadArbitrageConfig
from cta.config.spread_pair_registry import SpreadPair
from cta.feature.spread_features import compute_log_spread
from cta.portfolio_logic.config import normalize_portfolio_interval
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.strategy.calendar_spread_state import CalendarSpreadState
from cta.strategy.spread_state import SpreadState

logger = logging.getLogger(__name__)

SIGNAL_TYPE = "spread_arbitrage"
CalendarContractResolver = Callable[[SpreadPair, pd.Timestamp], tuple[str, str] | None]


@dataclass(frozen=True)
class SpreadOrderIntent:
    """One spread-leg order intent emitted by strategy."""

    pair_key: str
    pair_type: str
    cluster: str
    signal_type: str
    spread_side: str
    leg_id: str
    symbol: str
    exchange: str
    order_side: str
    signal_datetime: pd.Timestamp
    entry_datetime: pd.Timestamp
    planned_exit_datetime: pd.Timestamp
    notional: float
    zscore_at_signal: float
    exit_reason: str = ""


@dataclass
class OpenSpreadPosition:
    """Open spread state for one pair."""

    pair_key: str
    pair_type: str
    cluster: str
    spread_side: str
    signal_datetime: pd.Timestamp
    entry_datetime: pd.Timestamp
    planned_exit_datetime: pd.Timestamp
    zscore_at_entry: float
    leg1_notional: float
    leg2_notional: float
    leg1_symbol: str
    leg2_symbol: str

    def holding_days(self, as_of: pd.Timestamp) -> int:
        return int((pd.Timestamp(as_of) - pd.Timestamp(self.entry_datetime)).days)


def _normalize_price(value: object) -> float:
    out = float(pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0])
    return out


def _extract_datetimes(frame: pd.DataFrame) -> pd.Series:
    if "datetime" in frame.columns:
        return pd.to_datetime(frame["datetime"], errors="coerce")
    return pd.to_datetime(frame.index, errors="coerce").to_series(index=frame.index)


def _bar_at(frame: pd.DataFrame, ts: pd.Timestamp) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    dt = _extract_datetimes(frame)
    mask = dt <= pd.Timestamp(ts)
    if not bool(mask.any()):
        return None
    idx = dt[mask].index[-1]
    row = frame.loc[idx]
    return row if isinstance(row, pd.Series) else row.iloc[-1]


def _next_bar_datetime(frames: list[pd.DataFrame], ts: pd.Timestamp, interval: str) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    candidates: list[pd.Timestamp] = []
    for frame in frames:
        if frame is None or frame.empty:
            continue
        dt = _extract_datetimes(frame).dropna()
        nxt = dt.loc[dt > t]
        if not nxt.empty:
            candidates.append(pd.Timestamp(nxt.iloc[0]))
    if candidates:
        return min(candidates)
    norm = normalize_portfolio_interval(interval)
    if norm == "day":
        return t + pd.Timedelta(days=1)
    if norm == "min":
        return t + pd.Timedelta(minutes=1)
    if norm.endswith("min"):
        try:
            mins = int(norm[:-3])
        except ValueError:
            mins = 1
        return t + pd.Timedelta(minutes=max(1, mins))
    return t + pd.Timedelta(days=1)


def _entry_legs(spread_side: str) -> tuple[str, str]:
    if spread_side == "short_spread":
        return "short", "long"
    return "long", "short"


def _exit_legs(spread_side: str) -> tuple[str, str]:
    leg1, leg2 = _entry_legs(spread_side)
    return ("long" if leg1 == "short" else "short"), ("long" if leg2 == "short" else "short")


class SpreadArbitrageStrategy:
    """Spread arbitrage decision engine."""

    def __init__(
        self,
        *,
        cfg: SpreadArbitrageConfig,
        pairs: tuple[SpreadPair, ...],
        interval: str = "day",
        calendar_contract_resolver: CalendarContractResolver | None = None,
    ) -> None:
        self.cfg = cfg
        self.pairs = tuple(pairs)
        self.interval = normalize_portfolio_interval(interval)
        self.calendar_contract_resolver = calendar_contract_resolver
        self._states: dict[str, SpreadState] = {
            pair.pair_key: SpreadState(pair=pair, rolling_window_days=int(cfg.rolling_window_days))
            for pair in self.pairs
        }
        self._calendar_states: dict[str, CalendarSpreadState] = {
            pair.pair_key: CalendarSpreadState(
                pair_key=pair.pair_key,
                months_ahead=2,
                rollover_days_before_expiry=int(cfg.rollover_days_before_expiry),
            )
            for pair in self.pairs
            if pair.pair_type == "calendar"
        }
        self._open_positions: dict[str, OpenSpreadPosition] = {}

    @property
    def open_positions(self) -> Mapping[str, OpenSpreadPosition]:
        return self._open_positions

    def _compute_spread(self, pair: SpreadPair, price1: float, price2: float) -> float:
        if self.cfg.use_log_spread:
            p1 = pd.Series([float(price1)])
            p2 = pd.Series([float(price2) * float(pair.hedge_ratio)])
            return float(compute_log_spread(p1, p2).iloc[0])
        return float(price1 - float(pair.hedge_ratio) * float(price2))

    def _cluster_open_count(self, cluster: str) -> int:
        key = str(cluster).strip().lower()
        return sum(1 for pos in self._open_positions.values() if pos.cluster == key)

    def _can_open_new_position(self, pair: SpreadPair) -> bool:
        if len(self._open_positions) >= int(self.cfg.max_concurrent_spreads):
            return False
        if self._cluster_open_count(pair.cluster) >= int(self.cfg.max_concurrent_per_cluster):
            return False
        return True

    def _build_entry_intents(
        self,
        *,
        pair: SpreadPair,
        leg1_symbol: str,
        leg2_symbol: str,
        spread_side: str,
        signal_dt: pd.Timestamp,
        entry_dt: pd.Timestamp,
        planned_exit_dt: pd.Timestamp,
        zscore: float,
        equity: float,
    ) -> list[SpreadOrderIntent]:
        spread_notional = float(equity) * float(self.cfg.notional_pct_per_spread)
        leg1_notional = spread_notional
        leg2_notional = spread_notional * float(pair.hedge_ratio)
        leg1_side, leg2_side = _entry_legs(spread_side)
        intents = [
            SpreadOrderIntent(
                pair_key=pair.pair_key,
                pair_type=pair.pair_type,
                cluster=pair.cluster,
                signal_type=SIGNAL_TYPE,
                spread_side=spread_side,
                leg_id="leg1",
                symbol=leg1_symbol,
                exchange=pair.leg1_exchange,
                order_side=leg1_side,
                signal_datetime=signal_dt,
                entry_datetime=entry_dt,
                planned_exit_datetime=planned_exit_dt,
                notional=float(leg1_notional),
                zscore_at_signal=float(zscore),
            ),
            SpreadOrderIntent(
                pair_key=pair.pair_key,
                pair_type=pair.pair_type,
                cluster=pair.cluster,
                signal_type=SIGNAL_TYPE,
                spread_side=spread_side,
                leg_id="leg2",
                symbol=leg2_symbol,
                exchange=pair.leg2_exchange,
                order_side=leg2_side,
                signal_datetime=signal_dt,
                entry_datetime=entry_dt,
                planned_exit_datetime=planned_exit_dt,
                notional=float(leg2_notional),
                zscore_at_signal=float(zscore),
            ),
        ]
        self._open_positions[pair.pair_key] = OpenSpreadPosition(
            pair_key=pair.pair_key,
            pair_type=pair.pair_type,
            cluster=pair.cluster,
            spread_side=spread_side,
            signal_datetime=signal_dt,
            entry_datetime=entry_dt,
            planned_exit_datetime=planned_exit_dt,
            zscore_at_entry=float(zscore),
            leg1_notional=float(leg1_notional),
            leg2_notional=float(leg2_notional),
            leg1_symbol=str(leg1_symbol).strip().upper(),
            leg2_symbol=str(leg2_symbol).strip().upper(),
        )
        return intents

    def _build_exit_intents(
        self,
        *,
        pair: SpreadPair,
        open_pos: OpenSpreadPosition,
        signal_dt: pd.Timestamp,
        entry_dt: pd.Timestamp,
        zscore: float,
        reason: str,
    ) -> list[SpreadOrderIntent]:
        leg1_side, leg2_side = _exit_legs(open_pos.spread_side)
        return [
            SpreadOrderIntent(
                pair_key=pair.pair_key,
                pair_type=pair.pair_type,
                cluster=pair.cluster,
                signal_type=SIGNAL_TYPE,
                spread_side=open_pos.spread_side,
                leg_id="leg1",
                symbol=open_pos.leg1_symbol,
                exchange=pair.leg1_exchange,
                order_side=leg1_side,
                signal_datetime=signal_dt,
                entry_datetime=entry_dt,
                planned_exit_datetime=entry_dt,
                notional=float(open_pos.leg1_notional),
                zscore_at_signal=float(zscore),
                exit_reason=reason,
            ),
            SpreadOrderIntent(
                pair_key=pair.pair_key,
                pair_type=pair.pair_type,
                cluster=pair.cluster,
                signal_type=SIGNAL_TYPE,
                spread_side=open_pos.spread_side,
                leg_id="leg2",
                symbol=open_pos.leg2_symbol,
                exchange=pair.leg2_exchange,
                order_side=leg2_side,
                signal_datetime=signal_dt,
                entry_datetime=entry_dt,
                planned_exit_datetime=entry_dt,
                notional=float(open_pos.leg2_notional),
                zscore_at_signal=float(zscore),
                exit_reason=reason,
            ),
        ]

    def _decide_exit_reason(
        self,
        *,
        pair: SpreadPair,
        open_pos: OpenSpreadPosition,
        zscore: float,
        as_of: pd.Timestamp,
        leg1_bar: pd.Series,
    ) -> str | None:
        if np.isfinite(zscore):
            if abs(float(zscore)) <= float(self.cfg.z_exit):
                return "spread_mean_revert"
            if abs(float(zscore)) >= float(self.cfg.z_stop):
                return "spread_zscore_stop"
        if open_pos.holding_days(as_of) >= int(self.cfg.max_holding_days):
            return "spread_time_stop"
        if pair.pair_type == "calendar":
            near_days = pd.to_numeric(pd.Series([leg1_bar.get("near_days_to_expiry", np.nan)]), errors="coerce")
            if near_days.notna().any():
                if float(near_days.iloc[0]) < float(self.cfg.rollover_days_before_expiry):
                    return "calendar_rollover_forced"
        return None

    def _resolve_pair_legs(
        self,
        *,
        pair: SpreadPair,
        as_of: pd.Timestamp,
        open_pos: OpenSpreadPosition | None,
    ) -> tuple[str, str]:
        if open_pos is not None:
            return str(open_pos.leg1_symbol).upper(), str(open_pos.leg2_symbol).upper()
        if pair.pair_type != "calendar":
            return str(pair.leg1_symbol).upper(), str(pair.leg2_symbol).upper()
        st = self._calendar_states.get(pair.pair_key)
        if self.calendar_contract_resolver is not None:
            resolved = self.calendar_contract_resolver(pair, pd.Timestamp(as_of))
            if resolved is not None:
                near, far = str(resolved[0]).upper(), str(resolved[1]).upper()
                if st is not None:
                    st.update_current_pair(
                        as_of=pd.Timestamp(as_of),
                        near_contract_code=near,
                        far_contract_code=far,
                    )
                return near, far
        if st is not None and st.current_near_contract_code and st.current_far_contract_code:
            return st.current_near_contract_code, st.current_far_contract_code
        return str(pair.leg1_symbol).upper(), str(pair.leg2_symbol).upper()

    def step(
        self,
        t: pd.Timestamp,
        bars_by_symbol: Mapping[str, pd.DataFrame],
        portfolio_state: PortfolioState,
        *,
        current_drawdown_pct: float = 0.0,
    ) -> list[SpreadOrderIntent]:
        """Generate spread entry/exit intents for timestamp ``t``."""
        if not bool(self.cfg.use_spread_arbitrage):
            return []

        ts = pd.Timestamp(t)
        intents: list[SpreadOrderIntent] = []
        kill_switch_active = float(current_drawdown_pct) >= float(self.cfg.kill_switch_dd_pct)

        for pair in self.pairs:
            if not self.cfg.is_enabled(pair.pair_key, self.interval):
                continue
            open_pos = self._open_positions.get(pair.pair_key)
            leg1_symbol, leg2_symbol = self._resolve_pair_legs(
                pair=pair,
                as_of=ts,
                open_pos=open_pos,
            )
            leg1_frame = bars_by_symbol.get(leg1_symbol)
            leg2_frame = bars_by_symbol.get(leg2_symbol)
            if leg1_frame is None or leg2_frame is None:
                continue
            leg1_bar = _bar_at(leg1_frame, ts)
            leg2_bar = _bar_at(leg2_frame, ts)
            if leg1_bar is None or leg2_bar is None:
                continue
            price1 = _normalize_price(leg1_bar.get("close", np.nan))
            price2 = _normalize_price(leg2_bar.get("close", np.nan))
            if not np.isfinite(price1) or not np.isfinite(price2) or price1 <= 0.0 or price2 <= 0.0:
                continue
            spread_now = self._compute_spread(pair, price1, price2)
            st = self._states[pair.pair_key]
            next_dt = _next_bar_datetime([leg1_frame, leg2_frame], ts, self.interval)
            if open_pos is not None:
                z = st.zscore(spread_now)
                exit_reason = self._decide_exit_reason(
                    pair=pair,
                    open_pos=open_pos,
                    zscore=float(z),
                    as_of=ts,
                    leg1_bar=leg1_bar,
                )
                if exit_reason:
                    if pair.pair_type == "calendar":
                        cal_st = self._calendar_states.get(pair.pair_key)
                        if cal_st is not None and exit_reason == "calendar_rollover_forced":
                            cal_st.handle_rollover(
                                as_of=ts,
                                current_main_contract_code=open_pos.leg2_symbol,
                                near_days_to_expiry=float(
                                    pd.to_numeric(
                                        pd.Series([leg1_bar.get("near_days_to_expiry", np.nan)]),
                                        errors="coerce",
                                    ).iloc[0]
                                ),
                            )
                    intents.extend(
                        self._build_exit_intents(
                            pair=pair,
                            open_pos=open_pos,
                            signal_dt=ts,
                            entry_dt=next_dt,
                            zscore=float(z),
                            reason=exit_reason,
                        )
                    )
                    self._open_positions.pop(pair.pair_key, None)
                st.update(spread_now)
                continue

            if kill_switch_active:
                st.update(spread_now)
                continue
            if not st.is_warmup_done():
                st.update(spread_now)
                continue
            if not self._can_open_new_position(pair):
                st.update(spread_now)
                continue

            zscore = float(st.zscore(spread_now))
            spread_side = ""
            if np.isfinite(zscore) and zscore >= float(self.cfg.z_entry):
                spread_side = "short_spread"
            elif np.isfinite(zscore) and zscore <= -float(self.cfg.z_entry):
                spread_side = "long_spread"
            if spread_side:
                planned_exit_dt = next_dt + pd.Timedelta(days=int(self.cfg.max_holding_days))
                intents.extend(
                    self._build_entry_intents(
                        pair=pair,
                        leg1_symbol=leg1_symbol,
                        leg2_symbol=leg2_symbol,
                        spread_side=spread_side,
                        signal_dt=ts,
                        entry_dt=next_dt,
                        planned_exit_dt=planned_exit_dt,
                        zscore=float(zscore),
                        equity=float(portfolio_state.equity),
                    )
                )
            st.update(spread_now)

        if intents:
            logger.info("spread_arbitrage emitted %d intents at %s", len(intents), ts)
        return intents


__all__ = [
    "SIGNAL_TYPE",
    "SpreadArbitrageStrategy",
    "SpreadOrderIntent",
    "OpenSpreadPosition",
]
