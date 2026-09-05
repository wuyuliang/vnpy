"""Per-bar position tracing for auditing a single trade through the replay.

Reading an exit out of ``trades.csv`` tells you where a position closed but not
why the bars before it did not close it. This records one row per minute bar an
open position sees — the confirmed profit floor, the peak it was derived from,
which contract's bar was actually used, and the pending state carried into the
next bar — so a "why did this fill 14 minutes late" question is answered by
reading the bars instead of reasoning about the code.

Tracing is opt-in and off by default: with no candidate selected, ``record`` is
a single set-membership test.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

TRACE_COLUMNS = (
    "candidate_id",
    "root_symbol",
    "bar_end",
    "contract_code",
    "exchange_trade_date",
    "bar_contract_code",
    "open",
    "high",
    "low",
    "close",
    "quantity",
    "initial_quantity",
    "entry_price",
    "stop_price",
    "target_price",
    "maximum_favorable_price",
    "peak_r",
    "bars_since_entry",
    "profit_floor_price",
    "follow_through_seen",
    "no_follow_through_target_active",
    "floor_touched",
    "protective_touched",
    "pending_reason_in",
    "pending_reason_out",
    "decision_reason",
    "decision_price",
    "decision_reference",
    "limit_locked",
)


class PositionTracer:
    """Collect per-bar rows for the selected candidates."""

    def __init__(self) -> None:
        self._candidates: frozenset[str] = frozenset()
        self._rows: list[dict[str, Any]] = []

    def select(self, candidate_ids: object) -> None:
        """Trace these candidate ids; an empty selection disables tracing."""
        self._candidates = frozenset(
            str(item).strip() for item in (candidate_ids or ()) if str(item).strip()
        )
        self._rows = []

    @property
    def enabled(self) -> bool:
        return bool(self._candidates)

    def tracks(self, candidate_id: str) -> bool:
        return bool(self._candidates) and str(candidate_id) in self._candidates

    def record(self, **row: Any) -> None:
        self._rows.append(row)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self._rows, columns=list(TRACE_COLUMNS))


POSITION_TRACER = PositionTracer()


def wrap_manage_open_position(
    impl: Any,
    *,
    floor_touched: Any,
    protective_touched: Any,
    peak_r: Any,
    tracer: PositionTracer | None = None,
) -> Any:
    """Wrap the exit resolver so traced candidates emit one row per bar.

    The three predicates are injected rather than imported to keep this module
    free of a cycle back into ``engine``. They are read *before* ``impl`` runs,
    because ``impl`` folds the bar into the peak and re-arms the floor: the
    interesting values are the ones the bar was actually judged against.
    """
    active = tracer if tracer is not None else POSITION_TRACER

    def managed(position, bar, **kwargs):
        candidate_id = str(position.pending.candidate.get("candidate_id", ""))
        if not active.tracks(candidate_id):
            return impl(position, bar, **kwargs)
        pending_reason = str(kwargs.get("pending_reason", "") or "")
        config = kwargs.get("config")
        before = {
            "maximum_favorable_price": float(position.maximum_favorable_price),
            "peak_r": peak_r(position),
            "profit_floor_price": float(position.profit_floor_price),
        }
        touched = bool(
            getattr(config, "profit_floor_enabled", False)
            and not pending_reason
            and floor_touched(position, bar)
        )
        protective = bool(
            pending_reason != "ROLL_MAPPING_CHANGED"
            and protective_touched(position, bar)
        )
        decision = impl(position, bar, **kwargs)
        bars_since_entry = (
            int(bar.get("_bar_index", position.entry_bar_index))
            - position.entry_bar_index
        )
        active.record(
            candidate_id=candidate_id,
            root_symbol=str(kwargs.get("root_symbol", "")),
            bar_end=bar.get("bar_end"),
            contract_code=str(position.pending.candidate.get("contract_code", "")),
            exchange_trade_date=bar.get("exchange_trade_date"),
            bar_contract_code=bar.get("contract_code"),
            open=float(bar["open"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            close=float(bar["close"]),
            quantity=float(position.quantity),
            initial_quantity=float(position.initial_quantity),
            entry_price=float(position.entry_price),
            stop_price=float(position.stop),
            target_price=float(position.target),
            bars_since_entry=bars_since_entry,
            follow_through_seen=position.follow_through_seen,
            no_follow_through_target_active=(
                position.no_follow_through_target_active
            ),
            floor_touched=touched,
            protective_touched=protective,
            pending_reason_in=pending_reason,
            pending_reason_out=decision.pending_reason,
            decision_reason=decision.reason,
            decision_price=decision.price,
            decision_reference=decision.reference,
            limit_locked=decision.limit_locked,
            **before,
        )
        return decision

    return managed
