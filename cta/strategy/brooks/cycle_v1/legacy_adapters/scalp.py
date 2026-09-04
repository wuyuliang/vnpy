"""Explicit read-only wrappers; no legacy signature or state is changed."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from cta.strategy.brooks.scalp.research_pipeline import _prepare_symbol
from cta.strategy.brooks.scalp.features import wilder_atr as _legacy_wilder_atr
from cta.strategy.brooks.scalp.metadata import BlockedMetadataError

from ..backtest.data_loader import LoadedSymbol
from ..instruments.rollover import (
    RollAdjustmentReference,
    build_point_in_time_signal_bars,
)
from ..instruments.sessions import SessionSegment, SessionSpec


def legacy_wilder_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    return _legacy_wilder_atr(high, low, close, period=period)


def load_normalized_symbol(
    *,
    symbol: str,
    start: date,
    end: date,
    data_root: Path,
    metadata: Any,
    config: Any,
) -> LoadedSymbol:
    """Expose legacy's audited normalization as an immutable cycle_v1 input."""
    prepared = _prepare_symbol(
        symbol=symbol,
        start=start,
        end=end,
        data_root=Path(data_root),
        metadata=metadata,
        config=config,
    )
    if not prepared.normalized_frames:
        raise ValueError(f"legacy normalization produced no minute bars for {symbol}")
    normalized = pd.concat(prepared.normalized_frames, ignore_index=True)
    excluded_sessions = set(
        getattr(prepared, "excluded_roll_sessions", set())
    ) | set(getattr(prepared, "portfolio_excluded_sessions", set()))
    if excluded_sessions:
        if "session_id" not in normalized:
            raise ValueError("audited roll exclusions require session_id")
        normalized = normalized.loc[
            ~normalized["session_id"].astype(str).isin(excluded_sessions)
        ].reset_index(drop=True)
    if normalized.empty:
        raise ValueError(f"legacy normalization retained no audited bars for {symbol}")
    bars, overlapping_rows_removed = _reconcile_partition_overlaps(normalized)
    bars = bars.sort_values("bar_end", kind="stable").reset_index(drop=True)
    required = {
        "bar_end",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
        "open_interest",
        "contract_code",
        "exchange_trade_date",
        "session_open",
    }
    missing = sorted(required.difference(bars.columns))
    if missing:
        raise ValueError(f"normalized minute bars are missing: {','.join(missing)}")

    instruments = prepared.instruments
    if not instruments:
        raise ValueError(f"legacy normalization produced no instruments for {symbol}")
    roots = {str(item.root_symbol).upper() for item in instruments.values()}
    exchanges = {str(item.exchange).upper() for item in instruments.values()}
    session_sets = {_cycle_sessions(item.sessions) for item in instruments.values()}
    if len(roots) != 1 or len(exchanges) != 1 or len(session_sets) != 1:
        raise ValueError("one loaded root requires one exchange and session template")

    mechanics_rows: list[dict[str, object]] = []
    for (contract, trade_date, session_open), fee in prepared.fee_specs.items():
        instrument = instruments[contract]
        daily = getattr(prepared, "daily_specs", {}).get(
            (contract, trade_date)
        )
        daily_price_tick = getattr(daily, "price_tick", None)
        daily_contract_size = getattr(daily, "contract_size", None)
        mechanics_rows.append(
            {
                "contract_code": contract,
                "exchange_trade_date": trade_date,
                "session_open": session_open,
                "price_tick": float(
                    instrument.price_tick
                    if daily_price_tick is None
                    else daily_price_tick
                ),
                "contract_size": float(
                    instrument.contract_size
                    if daily_contract_size is None
                    else daily_contract_size
                ),
                "slippage_ticks_base": float(instrument.slippage_ticks_base),
                "open_fee_rate": float(fee.open_fee_rate),
                "exit_fee_rate": max(
                    float(fee.close_fee_rate),
                    float(fee.close_today_fee_rate),
                ),
                "fee_per_lot_open": float(fee.fee_per_lot_open),
                "fee_per_lot_exit": max(
                    float(fee.fee_per_lot_close),
                    float(fee.fee_per_lot_close_today),
                ),
            }
        )
    mechanics = pd.DataFrame(mechanics_rows)
    if mechanics.empty:
        raise ValueError(f"legacy normalization produced no fee mechanics for {symbol}")
    bars = bars.merge(
        mechanics,
        on=["contract_code", "exchange_trade_date", "session_open"],
        how="left",
        validate="many_to_one",
    )
    mechanics_columns = [
        "price_tick",
        "contract_size",
        "slippage_ticks_base",
        "open_fee_rate",
        "exit_fee_rate",
        "fee_per_lot_open",
        "fee_per_lot_exit",
    ]
    if bars[mechanics_columns].isna().any().any():
        raise ValueError(
            "point-in-time mechanics do not cover every normalized minute bar"
        )
    bars["base_round_trip_cost_price"] = (
        bars["close"] * (bars["open_fee_rate"] + bars["exit_fee_rate"])
        + (bars["fee_per_lot_open"] + bars["fee_per_lot_exit"]) / bars["contract_size"]
        + 2.0 * bars["slippage_ticks_base"] * bars["price_tick"]
    )
    bars = build_point_in_time_signal_bars(
        bars,
        root_symbol=next(iter(roots)),
        references=_roll_adjustment_references(
            bars,
            root_symbol=next(iter(roots)),
            metadata=metadata,
        ),
    )
    if "feature_sequence" not in bars:
        bars["feature_sequence"] = range(len(bars))
    bars = bars.loc[
        :,
        [
            "bar_end",
            "feature_sequence",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "open_interest",
            "contract_code",
            "exchange_trade_date",
            "price_tick",
            "base_round_trip_cost_price",
            "signal_open",
            "signal_high",
            "signal_low",
            "signal_close",
            "adjustment_scale",
            "adjustment_offset",
            "adjustment_known_at",
            "adjustment_version",
            "adjustment_source",
        ],
    ].copy()
    root = next(iter(roots))
    exchange = next(iter(exchanges))
    return LoadedSymbol(
        root_symbol=root,
        exchange=exchange,
        vt_symbol=f"{root}0.{exchange}",
        minute_bars=bars,
        sessions=next(iter(session_sets)),
        source_files=tuple(prepared.files),
        overlapping_rows_removed=overlapping_rows_removed,
    )


def _roll_adjustment_references(
    bars: pd.DataFrame,
    *,
    root_symbol: str,
    metadata: Any,
) -> tuple[RollAdjustmentReference, ...]:
    references: list[RollAdjustmentReference] = []
    ordered = bars.sort_values("bar_end", kind="stable").reset_index(drop=True)
    for position in range(1, len(ordered)):
        previous = ordered.iloc[position - 1]
        current = ordered.iloc[position]
        old_contract = str(previous["contract_code"])
        new_contract = str(current["contract_code"])
        if old_contract == new_contract:
            continue
        old_trade_date = pd.Timestamp(previous["exchange_trade_date"]).date()
        trade_date = pd.Timestamp(current["exchange_trade_date"]).date()
        if old_trade_date == trade_date:
            old_reference = metadata.pre_settlement_reference(
                old_contract,
                old_trade_date,
            )
        else:
            old_reference = metadata.settlement_reference(
                old_contract,
                old_trade_date,
            )
        new_reference = metadata.pre_settlement_reference(
            new_contract,
            trade_date,
        )
        known_at = max(old_reference.known_at, new_reference.known_at)
        session_open = pd.Timestamp(current["session_open"]).to_pydatetime()
        if known_at > session_open:
            raise BlockedMetadataError(
                "roll pre-settlement reference known late for "
                f"{old_contract}->{new_contract} {trade_date}"
            )
        effective_from = pd.Timestamp(current["bar_end"]).to_pydatetime()
        sources = sorted({str(old_reference.source), str(new_reference.source)})
        references.append(
            RollAdjustmentReference(
                root_symbol=root_symbol,
                old_contract=old_contract,
                new_contract=new_contract,
                effective_from=effective_from,
                old_reference_price=float(old_reference.price),
                new_reference_price=float(new_reference.price),
                known_at=known_at,
                source="SHFE_PRE_SETTLEMENT:" + "+".join(sources),
                version=(
                    f"PIT_PRE_SETTLEMENT_ADDITIVE_V1:{root_symbol}:"
                    f"{trade_date}:{old_contract}->{new_contract}"
                ),
            )
        )
    return tuple(references)


def _cycle_sessions(sessions: tuple[Any, ...]) -> tuple[SessionSpec, ...]:
    return tuple(
        SessionSpec(
            session_id=str(session.session_id),
            is_night=bool(session.is_night),
            segments=tuple(
                SessionSegment(
                    segment_id=str(segment.segment_id),
                    start=segment.start,
                    end=segment.end,
                    bucket_anchor=segment.bucket_anchor,
                )
                for segment in session.segments
            ),
        )
        for session in sessions
    )


def _reconcile_partition_overlaps(bars: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    keys = ["contract_code", "bar_end"]
    if not set(keys).issubset(bars):
        return bars, 0
    duplicated = bars.duplicated(keys, keep=False)
    if not duplicated.any():
        return bars, 0

    provenance = {"source_calendar_date", "source_path", "source_row"}
    comparison_columns = [column for column in bars.columns if column not in provenance]
    duplicate_rows = bars.loc[duplicated]
    for (contract, bar_end), group in duplicate_rows.groupby(keys, sort=True):
        conflicts = [
            column
            for column in comparison_columns
            if group[column].nunique(dropna=False) != 1
        ]
        if conflicts:
            sources = sorted(
                set(group.get("source_path", pd.Series(dtype=str)).astype(str))
            )
            raise ValueError(
                "CONFLICTING_DUPLICATE_BAR "
                f"{contract} {pd.Timestamp(bar_end)} "
                f"columns={','.join(conflicts)} sources={sources}"
            )
    result = bars.drop_duplicates(keys, keep="first").reset_index(drop=True)
    return result, len(bars) - len(result)


__all__ = ["legacy_wilder_atr", "load_normalized_symbol"]
