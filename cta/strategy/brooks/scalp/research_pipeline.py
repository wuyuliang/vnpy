"""Causal, metadata-gated RB/CU research pipeline for the frozen scalp rules."""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
import heapq
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import ScalpConfig, config_sha256
from .data import (
    ContractMapping,
    DataQualityError,
    MinuteBar,
    aggregate_completed_bars,
    apply_contract_roll_freeze,
    normalize_source_bars,
    validate_contract_mappings,
)
from .engine import ExitReason, ScalpEngine, TradePlan, plan_target_price
from .features import add_causal_features, confirmed_swings
from .metadata import (
    BlockedMetadataError,
    DailyTradingSpec,
    FeeMarginSpec,
    InstrumentSpec,
    MetadataBundle,
)
from .metadata_importer import sha256_file
from .metrics import (
    RiskScoreInput,
    block_bootstrap_loss_probability,
    build_group_metrics,
    calculate_risk_score,
    compute_trade_metrics,
    equity_metrics,
)
from .portfolio_coordinator import BrooksScalpPortfolioCoordinator
from .regime import RegimeSnapshot, RegimeState, classify_regime
from .risk_policy import PortfolioRiskState, RuleOnlyRiskAdapter
from .session import (
    SessionCalendar,
    SessionError,
    SessionSegment,
    SessionSpec,
    TradingCalendarEntry,
)
from .setups import SecondEntryTracker, SetupCandidate, detect_setups


ROOT_CLUSTERS = {"RB": "black", "CU": "base_metals"}

CANDIDATE_COLUMNS = (
    "candidate_id",
    "strategy_version",
    "symbol",
    "contract_code",
    "direction",
    "rule_id",
    "regime",
    "setup_bar_end",
    "feature_source_max",
    "trigger_price",
    "structural_stop",
    "planned_target",
    "planned_net_payoff",
    "available_space_r",
    "expires_at",
    "regime_range_high",
    "regime_range_low",
    "second_entry_state",
    "accepted",
    "reject_reason",
    "context_json",
)

TRADE_COLUMNS = (
    "trade_id",
    "candidate_id",
    "symbol",
    "contract_code",
    "direction",
    "rule_id",
    "signal_time",
    "entry_time",
    "entry_price",
    "quantity",
    "initial_stop",
    "planned_target",
    "initial_risk_cny",
    "exit_time",
    "exit_price",
    "exit_reason",
    "gross_pnl",
    "total_fee",
    "total_slippage",
    "net_pnl",
    "net_r",
    "mfe_r",
    "mae_r",
    "holding_1m_bars",
    "session_id",
    "session_type",
    "exchange_trade_date",
)


@dataclass
class _ScheduledCandidate:
    candidate: SetupCandidate
    plan: TradePlan
    risk_inputs: dict[str, Any]
    record_index: int


@dataclass
class _PreparedSymbol:
    symbol: str
    root_symbol: str
    start: date
    end: date
    files: tuple[Path, ...]
    source_hashes: dict[str, str]
    metadata: MetadataBundle
    config: ScalpConfig
    instruments: dict[str, InstrumentSpec]
    calendars: dict[str, SessionCalendar]
    contract_rows: dict[str, pd.Series]
    daily_specs: dict[tuple[str, date], DailyTradingSpec]
    fee_specs: dict[tuple[str, date, datetime], FeeMarginSpec]
    mappings: list[ContractMapping]
    boundary_snapshots_dropped: int = 0
    vendor_midnight_timestamps_shifted: int = 0
    vendor_zero_placeholders_dropped: int = 0
    vendor_no_night_rows_dropped: int = 0
    excluded_roll_sessions: set[str] = field(default_factory=set)
    portfolio_excluded_sessions: set[str] = field(default_factory=set)
    bars_5m: pd.DataFrame | None = None
    bars_30m: pd.DataFrame | None = None
    normalized_frames: tuple[pd.DataFrame, ...] | None = None
    scheduled: list[_ScheduledCandidate] | None = None
    candidate_records: list[dict[str, Any]] | None = None
    actual_first_bar_end: datetime | None = None
    actual_last_bar_end: datetime | None = None
    observed_sessions: dict[date, set[str]] = field(default_factory=dict)

    def runtime_metadata(
        self,
        contract_code: str,
        trade_date: date,
        session_open: datetime | None = None,
    ) -> tuple[InstrumentSpec, FeeMarginSpec, SessionCalendar]:
        try:
            if session_open is None:
                matches = [
                    value
                    for (contract, day, _), value in self.fee_specs.items()
                    if contract == contract_code and day == trade_date
                ]
                if len(matches) != 1:
                    raise KeyError((contract_code, trade_date, "ambiguous session"))
                fee = matches[0]
            else:
                fee = self.fee_specs[(contract_code, trade_date, session_open)]
            return (
                self.instruments[contract_code],
                fee,
                self.calendars[contract_code],
            )
        except KeyError as exc:
            raise BlockedMetadataError(
                f"runtime metadata missing for {contract_code} {trade_date}"
            ) from exc


def run_research_pipeline(
    *,
    symbols: tuple[str, ...],
    start: str,
    end: str,
    data_root: Path,
    metadata: MetadataBundle,
    config: ScalpConfig,
    research_only: bool,
):
    """Run the frozen strategy without loading the full minute history at once."""
    from .backtest_runner import BacktestArtifacts

    start_date = pd.Timestamp(start).date()
    end_date = pd.Timestamp(end).date()
    if end_date < start_date:
        raise ValueError("end precedes start")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be non-empty and unique")

    prepared = [
        _prepare_symbol(
            symbol=symbol,
            start=start_date,
            end=end_date,
            data_root=Path(data_root),
            metadata=metadata,
            config=config,
        )
        for symbol in symbols
    ]
    _synchronize_roll_session_exclusions(prepared)
    for item in prepared:
        _prepare_features_and_candidates(item)

    replay = _replay(prepared, config)
    independent_results: dict[str, dict[str, Any]] = {}
    independent_risk: dict[str, dict[str, Any]] = {}
    for item in prepared:
        single_replay = _replay(
            [item],
            config,
            update_candidate_records=False,
            collect_charts=False,
        )
        single_trades = _collect_trades([item], single_replay["coordinator"])
        single_minute = pd.DataFrame(single_replay["minute_equity"])
        single_daily = _daily_equity(
            single_minute,
            metadata,
            start_date,
            end_date,
            config.account.initial_equity,
        )
        single_stress = _run_stress_replays([item], config)
        single_candidates = pd.DataFrame(item.candidate_records or [])
        _, single_risk = _risk_results(
            single_trades,
            single_daily,
            single_minute,
            single_replay["coordinator"],
            config,
            stress_results=single_stress,
            daily_spec_keys=set(item.daily_specs),
            causal_audit_passed=_causal_audit_passes(
                single_candidates,
                single_trades,
            ),
        )
        single_status = _research_status(
            single_trades,
            single_daily,
            single_risk,
            (item.symbol,),
            config,
        )
        independent_risk[item.symbol] = single_risk
        independent_results[item.symbol] = {
            "status": single_status,
            "final_equity": float(single_daily.iloc[-1]["equity"]),
            "trade_metrics": compute_trade_metrics(single_trades),
            "equity_metrics": equity_metrics(
                single_daily,
                initial_equity=config.account.initial_equity,
            ),
            "risk_score": single_risk,
        }
    _verify_source_hashes(prepared)
    contract_mapping = pd.DataFrame(
        [mapping.to_dict() for item in prepared for mapping in item.mappings]
    )
    if not contract_mapping.empty:
        contract_mapping = contract_mapping.sort_values(
            ["session_open", "root_symbol", "effective_session"],
            kind="stable",
        ).reset_index(drop=True)
    daily_specs = pd.DataFrame(
        [
            {**spec.to_dict(), "symbol": item.symbol}
            for item in prepared
            for _, spec in sorted(item.daily_specs.items())
        ]
    )
    candidates = pd.DataFrame(
        [row for item in prepared for row in (item.candidate_records or [])],
        columns=CANDIDATE_COLUMNS,
    )
    if not candidates.empty:
        candidates = candidates.sort_values(
            ["setup_bar_end", "candidate_id"], kind="stable"
        ).reset_index(drop=True)
    rejections = candidates.loc[
        candidates["accepted"].eq(False),
        ["candidate_id", "symbol", "contract_code", "setup_bar_end", "reject_reason"],
    ].reset_index(drop=True)
    trades = _collect_trades(prepared, replay["coordinator"])
    fills = _collect_fills(replay["coordinator"])
    orders = _collect_orders(replay["coordinator"])
    order_events = _collect_order_events(replay["coordinator"])
    risk_decision_columns = [
        "candidate_id",
        "symbol",
        "phase",
        "allowed",
        "quantity",
        "reason",
        "audit",
        "signal_quantity",
        "trigger_fill_price",
        "net_stop_loss_per_contract",
        "risk_budget",
        "planned_open_risk",
        "reduction_submitted",
    ]
    risk_decisions = pd.DataFrame(
        replay["coordinator"].snapshot().risk_decisions
    ).reindex(columns=risk_decision_columns)
    minute_equity = pd.DataFrame(replay["minute_equity"])
    daily_equity = _daily_equity(
        minute_equity,
        metadata,
        start_date,
        end_date,
        config.account.initial_equity,
    )
    replayed_stress = _run_stress_replays(prepared, config)
    causal_audit_passed = _causal_audit_passes(candidates, trades)
    stress_results, risk_score = _risk_results(
        trades,
        daily_equity,
        minute_equity,
        replay["coordinator"],
        config,
        stress_results=replayed_stress,
        daily_spec_keys={
            key for item in prepared for key in item.daily_specs
        },
        causal_audit_passed=causal_audit_passed,
    )
    portfolio_status = _research_status(
        trades, daily_equity, risk_score, symbols, config
    )
    status = _combine_status(
        (portfolio_status, *(value["status"] for value in independent_results.values()))
    )
    risk_score["symbols"] = independent_risk
    source_audit = {
        "requested_start": start_date.isoformat(),
        "requested_end": end_date.isoformat(),
        "actual_start": minute_equity["datetime"].min().isoformat(),
        "actual_end": minute_equity["datetime"].max().isoformat(),
        "actual_symbol_ranges": {
            item.symbol: {
                "first_bar_end": item.actual_first_bar_end.isoformat()
                if item.actual_first_bar_end else None,
                "last_bar_end": item.actual_last_bar_end.isoformat()
                if item.actual_last_bar_end else None,
            }
            for item in prepared
        },
        "symbols": list(symbols),
        "metadata_manifest": metadata.manifest,
        "source_files": {
            item.symbol: item.source_hashes for item in prepared
        },
        "boundary_opening_snapshots_dropped": {
            item.symbol: item.boundary_snapshots_dropped for item in prepared
        },
        "vendor_midnight_timestamps_shifted": {
            item.symbol: item.vendor_midnight_timestamps_shifted for item in prepared
        },
        "vendor_zero_placeholders_dropped": {
            item.symbol: item.vendor_zero_placeholders_dropped for item in prepared
        },
        "vendor_no_night_rows_dropped": {
            item.symbol: item.vendor_no_night_rows_dropped for item in prepared
        },
        "excluded_intrasession_roll_sessions": {
            item.symbol: sorted(item.excluded_roll_sessions) for item in prepared
        },
        "portfolio_excluded_roll_sessions": sorted(
            set().union(*(item.portfolio_excluded_sessions for item in prepared))
        ),
        "bar_timestamp_semantics": "source timestamp is bar_end; exact segment-open snapshots are excluded",
        "causal_audit_passed": causal_audit_passed,
        "rollover_audit_passed": _rollover_audit_passes(trades),
        "ledger_reconciliation_passed": _ledger_reconciles(
            replay["coordinator"], config.account.initial_equity
        ),
        "source_hash_stability_passed": True,
        "chart_source_interval_minutes": 1,
        "base_slippage_ticks": {
            contract: spec.slippage_ticks_base
            for item in prepared
            for contract, spec in item.instruments.items()
        },
    }
    summary = {
        "research_only": bool(research_only),
        "requested_symbols": list(symbols),
        "candidate_count": int(len(candidates)),
        "accepted_candidate_count": int(candidates["accepted"].eq(True).sum()),
        "trade_count": int(len(trades)),
        "portfolio_status": portfolio_status,
        "independent_symbol_results": independent_results,
        "baselines": {
            "no_trade": {"net_pnl": 0.0, "status": "REFERENCE"},
            "brooks_v3_existing": {"status": "NOT_COMPARABLE_OLD_ACCOUNTING"},
            "always_in_only": {"status": "NOT_ENABLED_IN_FROZEN_RULE_RUN"},
            "scalp_rule_full": {"net_pnl": float(trades.get("net_pnl", pd.Series(dtype=float)).sum())},
        },
    }
    return BacktestArtifacts(
        status=status,
        source_audit=source_audit,
        contract_mapping=contract_mapping,
        daily_trading_specs=daily_specs,
        candidates=candidates,
        rejections=rejections,
        risk_decisions=risk_decisions,
        orders=orders,
        order_events=order_events,
        fills=fills,
        trades=trades,
        minute_equity=minute_equity,
        daily_equity=daily_equity,
        stress_results=stress_results,
        risk_score=risk_score,
        summary=summary,
        symbol_bars=replay["symbol_bars"],
    )


def sessions_for_template(template_id: str) -> tuple[SessionSpec, ...]:
    """Resolve only explicit Chinese commodity templates; unknown history blocks."""
    day = SessionSpec(
        session_id="day",
        is_night=False,
        segments=(
            SessionSegment("day_1", time(9, 0), time(10, 15), time(9, 0)),
            SessionSegment("day_2", time(10, 30), time(11, 30), time(10, 30)),
            SessionSegment("day_3", time(13, 30), time(15, 0), time(13, 30)),
        ),
    )
    key = str(template_id).strip().upper()
    if key in {"SHFE_DAY", "DAY", "CN_COMMODITY_DAY"}:
        return (day,)
    endings = {
        "SHFE_RB_NIGHT": time(23, 0),
        "SHFE_RB_NIGHT_2300": time(23, 0),
        "SHFE_CU_NIGHT": time(1, 0),
        "SHFE_CU_NIGHT_0100": time(1, 0),
        "CN_COMMODITY_NIGHT_2300": time(23, 0),
        "CN_COMMODITY_NIGHT_0100": time(1, 0),
        "CN_COMMODITY_NIGHT_0230": time(2, 30),
    }
    if key not in endings:
        raise BlockedMetadataError(f"unsupported session_template_id: {template_id}")
    night = SessionSpec(
        session_id="night",
        is_night=True,
        segments=(
            SessionSegment("night_continuous", time(21, 0), endings[key], time(21, 0)),
        ),
    )
    return (night, day)


def _prepare_symbol(
    *,
    symbol: str,
    start: date,
    end: date,
    data_root: Path,
    metadata: MetadataBundle,
    config: ScalpConfig,
) -> _PreparedSymbol:
    root = _root_symbol(symbol)
    directory = _source_directory(data_root, symbol, root)
    files = _source_files(directory, start, end)
    if not files:
        raise BlockedMetadataError(f"no source parquet files for {symbol} {start}..{end}")
    contract_frame = metadata.frames["contract_specs.csv"]
    rows = contract_frame.loc[
        contract_frame["root_symbol"].astype(str).str.upper().eq(root)
    ]
    if rows.empty:
        raise BlockedMetadataError(f"contract specs missing root {root}")
    contract_rows = {
        str(row["contract_code"]).upper(): row for _, row in rows.iterrows()
    }
    prepared = _PreparedSymbol(
        symbol=symbol,
        root_symbol=root,
        start=start,
        end=end,
        files=files,
        source_hashes={str(path): sha256_file(path) for path in files},
        metadata=metadata,
        config=config,
        instruments={},
        calendars={},
        contract_rows=contract_rows,
        daily_specs={},
        fee_specs={},
        mappings=[],
    )
    five, thirty = _aggregate_stream(prepared)
    _validate_trade_date_coverage(prepared)
    if five.empty or thirty.empty:
        raise BlockedMetadataError(f"insufficient complete minute buckets for {symbol}")
    prepared.bars_5m = apply_contract_roll_freeze(five.sort_values("bar_end").reset_index(drop=True))
    prepared.bars_30m = apply_contract_roll_freeze(
        thirty.sort_values("bar_end").reset_index(drop=True)
    )
    validate_contract_mappings(tuple(prepared.mappings))
    return prepared


def _aggregate_stream(prepared: _PreparedSymbol) -> tuple[pd.DataFrame, pd.DataFrame]:
    carry = pd.DataFrame()
    five_parts: list[pd.DataFrame] = []
    thirty_parts: list[pd.DataFrame] = []
    normalized_frames: list[pd.DataFrame] = []
    for frame in _normalized_frames(prepared, collect_audit=True):
        normalized_frames.append(frame)
        combined = pd.concat([carry, frame], ignore_index=True) if not carry.empty else frame
        session_order = list(dict.fromkeys(combined["session_id"].astype(str)))
        completed_ids = session_order[:-1]
        if completed_ids:
            completed = combined.loc[combined["session_id"].isin(completed_ids)].copy()
            completed = _register_and_exclude_mixed_sessions(prepared, completed)
            if not completed.empty:
                _validate_observed_sessions(prepared, completed)
                five_parts.append(
                    aggregate_completed_bars(
                        completed, 5, _calendar_for_frame(prepared, completed)
                    )
                )
                thirty_parts.append(
                    aggregate_completed_bars(
                        completed, 30, _calendar_for_frame(prepared, completed)
                    )
                )
        carry = combined.loc[combined["session_id"].eq(session_order[-1])].copy()
    if not carry.empty:
        carry = _register_and_exclude_mixed_sessions(prepared, carry)
        if not carry.empty:
            _validate_observed_sessions(prepared, carry)
            calendar = _calendar_for_frame(prepared, carry)
            five_parts.append(aggregate_completed_bars(carry, 5, calendar))
            thirty_parts.append(aggregate_completed_bars(carry, 30, calendar))
    five = pd.concat(five_parts, ignore_index=True) if five_parts else pd.DataFrame()
    thirty = pd.concat(thirty_parts, ignore_index=True) if thirty_parts else pd.DataFrame()
    prepared.normalized_frames = tuple(normalized_frames)
    return five, thirty


def _exclude_mixed_contract_sessions(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, set[str]]:
    """Exclude sessions that a continuous vendor series switches mid-session."""
    if frame.empty:
        return frame.copy(), set()
    contract_counts = frame.groupby("session_id", sort=False)["contract_code"].nunique()
    excluded = set(contract_counts.loc[contract_counts.gt(1)].index.astype(str))
    clean = frame.loc[~frame["session_id"].astype(str).isin(excluded)].copy()
    return clean, excluded


def _synchronize_roll_session_exclusions(prepared: list[_PreparedSymbol]) -> None:
    """Keep portfolio and independent replays on the same continuous-contract sessions."""
    excluded = set().union(*(item.excluded_roll_sessions for item in prepared))
    if not excluded:
        return
    for item in prepared:
        item.portfolio_excluded_sessions = set(excluded)
        for attribute in ("bars_5m", "bars_30m"):
            frame = getattr(item, attribute)
            if frame is None or frame.empty:
                continue
            clean = frame.loc[~frame["session_id"].astype(str).isin(excluded)]
            setattr(item, attribute, clean.reset_index(drop=True))
        cached_frames = getattr(item, "normalized_frames", None)
        if cached_frames is not None:
            item.normalized_frames = tuple(
                frame.loc[
                    ~frame["session_id"].astype(str).isin(excluded)
                ].reset_index(drop=True)
                for frame in cached_frames
            )


def _register_and_exclude_mixed_sessions(
    prepared: _PreparedSymbol,
    frame: pd.DataFrame,
) -> pd.DataFrame:
    clean, excluded = _exclude_mixed_contract_sessions(frame)
    for session_id in sorted(excluded):
        session = frame.loc[frame["session_id"].astype(str).eq(session_id)]
        trade_date = pd.Timestamp(session.iloc[0]["exchange_trade_date"]).date()
        session_kind = str(session.iloc[0]["session_kind"])
        prepared.observed_sessions.setdefault(trade_date, set()).add(session_kind)
        prepared.excluded_roll_sessions.add(session_id)
    return clean


def _validate_observed_sessions(
    prepared: _PreparedSymbol,
    frame: pd.DataFrame,
) -> None:
    for (_, session_id), session in frame.groupby(
        ["exchange_trade_date", "session_id"],
        sort=False,
    ):
        trade_date = pd.Timestamp(session.iloc[0]["exchange_trade_date"]).date()
        session_kind = str(session.iloc[0]["session_kind"])
        prepared.observed_sessions.setdefault(trade_date, set()).add(session_kind)
        contract = str(session.iloc[0]["contract_code"])
        instrument = _instrument_for(prepared, contract)
        spec = next(
            (value for value in instrument.sessions if value.session_id == session_kind),
            None,
        )
        if spec is None:
            raise DataQualityError(
                f"unknown session kind {session_kind} in {session_id}",
                code="UNKNOWN_SESSION_KIND",
            )
        observed_segments = set(session["segment_id"].astype(str))
        expected_segments = {segment.segment_id for segment in spec.segments}
        missing_segments = sorted(expected_segments - observed_segments)
        if missing_segments:
            raise DataQualityError(
                f"session {session_id} missing segments {missing_segments}",
                code="MISSING_SESSION_SEGMENT",
            )
        for segment_id, segment in session.groupby("segment_id", sort=False):
            first = segment.sort_values("bar_end").iloc[0]
            expected_first_end = pd.Timestamp(first["segment_start"]) + pd.Timedelta(minutes=1)
            if pd.Timestamp(first["bar_end"]) != expected_first_end:
                raise DataQualityError(
                    f"session {session_id} segment {segment_id} starts at "
                    f"{first['bar_end']} instead of {expected_first_end}",
                    code="SESSION_HEAD_GAP",
                )


def _validate_trade_date_coverage(prepared: _PreparedSymbol) -> None:
    exchanges = {
        str(row["exchange"]).upper() for row in prepared.contract_rows.values()
    }
    if len(exchanges) != 1:
        raise BlockedMetadataError(
            f"ambiguous exchanges for {prepared.root_symbol}: {sorted(exchanges)}"
        )
    exchange = next(iter(exchanges))
    calendar = prepared.metadata.frames["exchange_calendar.csv"].copy()
    calendar["_date"] = pd.to_datetime(
        calendar["exchange_trade_date"], errors="coerce"
    ).dt.date
    calendar = calendar.loc[
        calendar["exchange"].astype(str).str.upper().eq(exchange)
    ].copy()
    expected_dates = set(
        calendar.loc[
            calendar["is_open"].map(_as_bool)
            & calendar["_date"].between(prepared.start, prepared.end),
            "_date",
        ]
    )
    missing_dates = sorted(expected_dates - set(prepared.observed_sessions))
    if missing_dates:
        raise DataQualityError(
            f"{prepared.symbol} missing complete open dates: "
            f"{[value.isoformat() for value in missing_dates[:10]]}",
            code="MISSING_TRADE_DATE_BARS",
        )
    configured_session_kinds = {
        session.session_id
        for instrument in prepared.instruments.values()
        for session in instrument.sessions
    }
    calendar_by_date = calendar.set_index("_date", drop=False)
    for trade_date in sorted(expected_dates):
        expected_session_kinds = set(configured_session_kinds)
        calendar_row = calendar_by_date.loc[trade_date]
        if isinstance(calendar_row, pd.DataFrame):
            raise BlockedMetadataError(
                f"ambiguous exchange calendar row for {exchange} {trade_date}"
            )
        if _parse_time(calendar_row["night_session_start"]) is None:
            expected_session_kinds.discard("night")
        missing_sessions = sorted(
            expected_session_kinds - prepared.observed_sessions.get(trade_date, set())
        )
        if missing_sessions:
            raise DataQualityError(
                f"{prepared.symbol} {trade_date} missing sessions {missing_sessions}",
                code="MISSING_TRADE_DATE_SESSION",
            )


def _normalized_frames(
    prepared: _PreparedSymbol,
    *,
    collect_audit: bool,
) -> Iterator[pd.DataFrame]:
    if prepared.normalized_frames is not None:
        yield from prepared.normalized_frames
        return
    seen_sessions = {mapping.effective_session for mapping in prepared.mappings}
    exchange = str(next(iter(prepared.contract_rows.values()))["exchange"]).upper()
    for path in prepared.files:
        raw = pd.read_parquet(path)
        if raw.empty:
            continue
        timestamp_column = "datetime" if "datetime" in raw else "trade_time"
        contract_column = "contract_code" if "contract_code" in raw else "ts_code"
        raw = _trim_pre_start_partition(
            raw,
            timestamp_column=timestamp_column,
            source_date=pd.Timestamp(path.stem).date(),
            start=prepared.start,
        )
        if raw.empty:
            continue
        raw, no_night_rows = _drop_vendor_no_night_rows(
            raw,
            timestamp_column,
            prepared.metadata.frames["exchange_calendar.csv"],
            exchange=exchange,
        )
        if collect_audit:
            prepared.vendor_no_night_rows_dropped += no_night_rows
        if raw.empty:
            continue
        raw, shifted, placeholders = _restore_cu_vendor_timestamps(
            raw,
            timestamp_column,
            prepared.metadata.frames["exchange_calendar.csv"],
        )
        if collect_audit:
            prepared.vendor_midnight_timestamps_shifted += shifted
            prepared.vendor_zero_placeholders_dropped += placeholders
        raw, dropped = _drop_opening_snapshots(raw, timestamp_column, prepared)
        if collect_audit:
            prepared.boundary_snapshots_dropped += dropped
        if raw.empty:
            continue
        contracts = raw[contract_column].astype(str).str.upper().str.strip()
        unknown = sorted(set(contracts).difference(prepared.contract_rows))
        if unknown:
            raise BlockedMetadataError(f"unknown contracts in {path}: {unknown}")
        templates = {
            str(prepared.contract_rows[contract]["session_template_id"]).upper()
            for contract in set(contracts)
        }
        if len(templates) != 1:
            raise DataQualityError(
                f"multiple session templates inside one source file: {path}",
                code="INTRAFILE_SESSION_TEMPLATE_SWITCH",
            )
        primary_contract = contracts.iloc[0]
        instrument = _instrument_for(prepared, primary_contract)
        calendar = _calendar_for(prepared, primary_contract)
        raw_times = _localized_timestamps(raw[timestamp_column])
        assignments = []
        keep = []
        for timestamp in raw_times:
            try:
                assignment = calendar.assign_bar(
                    timestamp.to_pydatetime() - timedelta(minutes=1),
                    timestamp.to_pydatetime(),
                )
            except SessionError as exc:
                if (
                    exc.code == "OUTSIDE_SESSION"
                    and timestamp.date() == prepared.end
                    and timestamp.hour >= 18
                ):
                    assignments.append(None)
                    keep.append(False)
                    continue
                raise
            assignments.append(assignment)
            keep.append(prepared.start <= assignment.exchange_trade_date <= prepared.end)
        raw = raw.loc[keep].reset_index(drop=True)
        assignments = [value for value, retain in zip(assignments, keep, strict=True) if retain]
        contracts = contracts.loc[keep].reset_index(drop=True)
        if raw.empty:
            continue
        daily: dict[tuple[str, date], DailyTradingSpec] = {}
        for contract, assignment in zip(contracts, assignments, strict=True):
            _validate_contract_known_at(prepared, contract, assignment.session_open)
            key = (contract, assignment.exchange_trade_date)
            spec = prepared.daily_specs.get(key)
            if spec is None:
                spec = prepared.metadata.daily_spec(*key)
                if spec.known_at > assignment.session_open:
                    raise BlockedMetadataError(
                        f"daily spec known late for {contract} {assignment.exchange_trade_date}"
                    )
                prepared.daily_specs[key] = spec
            fee_key = (contract, assignment.exchange_trade_date, assignment.session_open)
            fee = prepared.fee_specs.get(fee_key)
            if fee is None:
                fee = prepared.metadata.fee_margin_spec(
                    spec.fee_margin_schedule_id,
                    contract,
                    assignment.session_open,
                )
                if fee.known_at > assignment.session_open:
                    raise BlockedMetadataError(
                        f"fee schedule known late for {contract} {assignment.exchange_trade_date}"
                    )
                _require_authoritative_sources(prepared, contract, spec, fee)
                prepared.fee_specs[fee_key] = fee
            daily[key] = spec
        normalized = normalize_source_bars(
            raw,
            instrument,
            calendar,
            daily_specs=daily,
            source_path=str(path),
        )
        normalized["vt_symbol"] = prepared.symbol
        excluded_sessions = (
            prepared.excluded_roll_sessions | prepared.portfolio_excluded_sessions
        )
        if excluded_sessions:
            normalized = normalized.loc[
                ~normalized["session_id"].astype(str).isin(
                    excluded_sessions
                )
            ].reset_index(drop=True)
            if normalized.empty:
                continue
        if collect_audit:
            first_end = normalized["bar_end"].min().to_pydatetime()
            last_end = normalized["bar_end"].max().to_pydatetime()
            prepared.actual_first_bar_end = min(
                value for value in (prepared.actual_first_bar_end, first_end) if value is not None
            )
            prepared.actual_last_bar_end = max(
                value for value in (prepared.actual_last_bar_end, last_end) if value is not None
            )
        for session_id, group in normalized.groupby("session_id", sort=False):
            contract = str(group.iloc[0]["contract_code"])
            if session_id not in seen_sessions:
                first = group.iloc[0]
                prepared.mappings.append(
                    ContractMapping(
                        root_symbol=prepared.root_symbol,
                        contract_code=contract,
                        effective_session=str(session_id),
                        session_open=first["session_open"],
                        decision_asof=first["bar_end"],
                        source="local_session_first_bar",
                    )
                )
                seen_sessions.add(str(session_id))
        yield normalized


def _prepare_features_and_candidates(prepared: _PreparedSymbol) -> None:
    assert prepared.bars_5m is not None and prepared.bars_30m is not None
    five = _features_by_contract_epoch(prepared.bars_5m)
    thirty = _features_by_contract_epoch(prepared.bars_30m)
    regimes = _regimes_by_epoch(thirty, prepared.config)
    mapping_switch_dates = _mapping_switch_dates(prepared.mappings)
    records: list[dict[str, Any]] = []
    scheduled: list[_ScheduledCandidate] = []
    strategy_version = f"scalp-{config_sha256(prepared.config)[:12]}"

    for _epoch, epoch_five in five.groupby("_contract_epoch", sort=False):
        epoch_five = epoch_five.reset_index(drop=True)
        contract = str(epoch_five.iloc[0]["contract_code"])
        start_at, end_at = epoch_five["bar_end"].min(), epoch_five["bar_end"].max()
        epoch_regimes = regimes.loc[
            regimes["source_contract"].eq(contract)
            & regimes["feature_asof"].between(start_at, end_at)
        ].sort_values("feature_asof").reset_index(drop=True)
        epoch_thirty = thirty.loc[
            thirty["contract_code"].eq(contract)
            & thirty["bar_end"].between(start_at - pd.Timedelta(days=30), end_at)
        ].reset_index(drop=True)
        swings_5m = confirmed_swings(epoch_five)
        swings_30m = confirmed_swings(epoch_thirty) if not epoch_thirty.empty else pd.DataFrame()
        trackers = {
            1: SecondEntryTracker(prepared.symbol, 1, _instrument_for(prepared, contract).price_tick),
            -1: SecondEntryTracker(prepared.symbol, -1, _instrument_for(prepared, contract).price_tick),
        }
        regime_position = -1
        latest: RegimeSnapshot | None = None
        for position, row in epoch_five.iterrows():
            while (
                regime_position + 1 < len(epoch_regimes)
                and epoch_regimes.iloc[regime_position + 1]["feature_asof"] <= row["bar_end"]
            ):
                regime_position += 1
                latest = _regime_snapshot(epoch_regimes.iloc[regime_position])
            if latest is None:
                latest = RegimeSnapshot(
                    RegimeState.UNAVAILABLE,
                    0,
                    pd.Timestamp(row["bar_end"]).to_pydatetime(),
                    float("nan"),
                    float("nan"),
                    float("nan"),
                    contract,
                )
            if position + 1 < prepared.config.intervals.warmup_5m:
                continue
            second_entries = [
                update.candidate
                for update in (
                    trackers[1].update(row, latest),
                    trackers[-1].update(row, latest),
                )
                if update.candidate is not None
            ]
            if second_entries:
                found = [sorted(second_entries, key=lambda value: value.candidate_id)[0]]
            else:
                found = detect_setups(
                    epoch_five.iloc[: position + 1],
                    latest,
                    _instrument_for(prepared, contract),
                    prepared.config,
                    symbol=prepared.symbol,
                )
            for candidate in found[:1]:
                obstacle = _obstacle_price(
                    candidate,
                    latest,
                    row,
                    epoch_five.iloc[: position + 1],
                    epoch_thirty.loc[epoch_thirty["bar_end"] <= row["bar_end"]],
                    swings_5m,
                    swings_30m,
                )
                context = {
                    **candidate.context,
                    "obstacle_price": obstacle,
                    "session_id": str(row["session_id"]),
                    "session_type": str(row["session_kind"]),
                }
                candidate = replace(
                    candidate,
                    available_space_price=abs(obstacle - candidate.trigger_price),
                    context=context,
                )
                trade_date = pd.Timestamp(row["exchange_trade_date"]).date()
                instrument, fee, _ = prepared.runtime_metadata(
                    contract,
                    trade_date,
                    pd.Timestamp(row["session_open"]).to_pydatetime(),
                )
                plan = plan_target_price(
                    candidate,
                    obstacle,
                    instrument,
                    fee,
                    planned_net_payoff=prepared.config.trade_plan.planned_net_payoff,
                    allowed_min=prepared.config.trade_plan.allowed_net_payoff_min,
                    allowed_max=prepared.config.trade_plan.allowed_net_payoff_max,
                    minimum_space_r=prepared.config.trade_plan.minimum_space_r,
                    pressure_fee_multiplier=prepared.config.trade_plan.pressure_fee_multiplier,
                    pressure_slippage_multiplier=prepared.config.trade_plan.pressure_slippage_multiplier,
                )
                record = _candidate_record(candidate, latest, plan, strategy_version)
                records.append(record)
                if plan is None:
                    records[-1]["accepted"] = False
                    records[-1]["reject_reason"] = "trade_plan_unavailable"
                    continue
                latest_regime_row = epoch_regimes.iloc[regime_position]
                last_trade_date = pd.Timestamp(
                    prepared.contract_rows[contract]["last_trade_date"]
                ).date()
                switch_date = mapping_switch_dates[str(row["session_id"])]
                exchange = prepared.instruments[contract].exchange
                scheduled.append(
                    _ScheduledCandidate(
                        candidate=candidate,
                        plan=plan,
                        risk_inputs={
                            "exchange_trade_date": trade_date,
                            "cluster": ROOT_CLUSTERS.get(prepared.root_symbol, "other"),
                            "volume_ratio": row.get("volume_ratio"),
                            "turnover_activity_ratio": row.get("turnover_activity_ratio"),
                            "realized_vol_pctl_252_30m": latest_regime_row.get(
                                "realized_vol_pctl_252_30m"
                            ),
                            "volatility_sample_count": int(regime_position + 1),
                            "last_trade_date": last_trade_date,
                            "main_switch_date": switch_date,
                            "trading_days_to_expiry": _exchange_trading_day_count(
                                prepared.metadata,
                                exchange,
                                trade_date,
                                last_trade_date,
                                minimum_horizon=(
                                    prepared.config.risk_components.rollover.freeze_window_days
                                ),
                            ),
                            "trading_days_since_switch": _exchange_trading_day_count(
                                prepared.metadata,
                                exchange,
                                switch_date,
                                trade_date,
                            ),
                            "roll_freeze": bool(row.get("roll_freeze", False)),
                            "daily_limit_known": True,
                            "market_data_stale": False,
                        },
                        record_index=len(records) - 1,
                    )
                )
    prepared.bars_5m = five
    prepared.bars_30m = thirty
    prepared.scheduled = sorted(
        scheduled,
        key=lambda item: (
            item.candidate.setup_bar_end,
            item.candidate.symbol,
            item.candidate.candidate_id,
        ),
    )
    prepared.candidate_records = records


def _replay(
    prepared: list[_PreparedSymbol],
    config: ScalpConfig,
    *,
    update_candidate_records: bool = True,
    fee_multiplier: float = 1.0,
    slippage_multiplier: float = 1.0,
    limit_stress: bool = False,
    collect_charts: bool = True,
) -> dict[str, Any]:
    if fee_multiplier <= 0 or slippage_multiplier <= 0:
        raise ValueError("stress multipliers must be positive")
    iterators = {item.symbol: iter(_minute_bars(item)) for item in prepared}
    first_bars: dict[str, MinuteBar] = {}
    for item in prepared:
        try:
            first_bars[item.symbol] = next(iterators[item.symbol])
        except StopIteration as exc:
            raise BlockedMetadataError(f"no normalized bars for {item.symbol}") from exc
    by_symbol = {item.symbol: item for item in prepared}
    coordinator = BrooksScalpPortfolioCoordinator(
        initial_equity=config.account.initial_equity,
        mode="historical",
        stale_tolerance_seconds=config.coordination.active_bar_stale_tolerance_seconds,
        risk_adapter=RuleOnlyRiskAdapter(config, mode="backtest"),
    )
    active_metadata: dict[str, tuple[str, date, str, datetime]] = {}
    for symbol, bar in first_bars.items():
        item = by_symbol[symbol]
        instrument, fee, calendar = item.runtime_metadata(
            bar.contract_code,
            bar.exchange_trade_date,
            bar.session_open,
        )
        instrument, fee = _stressed_execution_metadata(
            instrument,
            fee,
            fee_multiplier=fee_multiplier,
            slippage_multiplier=slippage_multiplier,
            limit_stress=limit_stress,
        )
        coordinator.register_symbol(
            symbol,
            ScalpEngine(
                instrument,
                fee,
                initial_cash=config.account.initial_equity,
                auto_match=True,
                entry_valid_bars=config.intervals.entry_valid_1m_bars,
                no_follow_through_bars=config.intervals.no_follow_through_bars,
                max_holding_bars=config.intervals.max_holding_1m_bars,
                force_flat_minutes=config.risk.force_flat_minutes,
            ),
            calendar,
        )
        active_metadata[symbol] = (
            bar.contract_code,
            bar.exchange_trade_date,
            fee.schedule_id,
            fee.effective_from,
        )

    heap: list[tuple[datetime, str, int, MinuteBar]] = []
    sequence = 0
    for symbol, bar in first_bars.items():
        heapq.heappush(heap, (bar.bar_end, symbol, sequence, bar))
        sequence += 1
    schedules: dict[datetime, list[_ScheduledCandidate]] = defaultdict(list)
    for item in prepared:
        for candidate in item.scheduled or []:
            schedules[candidate.candidate.setup_bar_end].append(candidate)
    risk_state = PortfolioRiskState(config, symbols=tuple(sorted(by_symbol)))
    closed_counts = {symbol: 0 for symbol in by_symbol}
    minute_equity: list[dict[str, Any]] = []
    chart_rows: dict[str, list[dict[str, Any]]] = {item.root_symbol: [] for item in prepared}
    execution_fills: deque[dict[str, float]] = deque(
        maxlen=config.risk_components.execution_quality.rolling_window_days * 20
    )
    exhausted: set[str] = set()
    terminal_bars: dict[str, MinuteBar] = {}
    current_trade_date: date | None = None
    day_start_engine_pnl = {symbol: 0.0 for symbol in by_symbol}

    while heap:
        watermark = heap[0][0]
        group: dict[str, MinuteBar] = {}
        while heap and heap[0][0] == watermark:
            _, symbol, _, bar = heapq.heappop(heap)
            if symbol in group:
                raise DataQualityError(
                    f"duplicate symbol at watermark {symbol} {watermark}",
                    code="DUPLICATE_WATERMARK_BAR",
                )
            group[symbol] = bar
            try:
                following = next(iterators[symbol])
            except StopIteration:
                exhausted.add(symbol)
                terminal_bars[symbol] = bar
            else:
                heapq.heappush(heap, (following.bar_end, symbol, sequence, following))
                sequence += 1

        for symbol in sorted(exhausted.difference(group)):
            terminal = terminal_bars[symbol]
            if terminal.bar_end != terminal.session_close:
                raise DataQualityError(
                    f"{symbol} source ends before session close at {terminal.bar_end}",
                    code="INCOMPLETE_TERMINAL_SESSION",
                )
            coordinator.deactivate_symbol(symbol)
            exhausted.remove(symbol)

        for symbol, bar in group.items():
            item = by_symbol[symbol]
            instrument, fee, calendar = item.runtime_metadata(
                bar.contract_code,
                bar.exchange_trade_date,
                bar.session_open,
            )
            instrument, fee = _stressed_execution_metadata(
                instrument,
                fee,
                fee_multiplier=fee_multiplier,
                slippage_multiplier=slippage_multiplier,
                limit_stress=limit_stress,
            )
            metadata_key = (
                bar.contract_code,
                bar.exchange_trade_date,
                fee.schedule_id,
                fee.effective_from,
            )
            if active_metadata[symbol] != metadata_key:
                coordinator.update_symbol_metadata(symbol, instrument, fee, calendar)
                active_metadata[symbol] = metadata_key
        any_bar = next(iter(group.values()))
        expected = set(coordinator.expected_active_symbols(any_bar.bar_start, any_bar.bar_end))
        if set(group) != expected:
            raise DataQualityError(
                f"historical watermark {watermark} expected {sorted(expected)} got {sorted(group)}",
                code="HISTORICAL_WATERMARK_GAP",
            )
        state_before = coordinator.account_state()
        fills_before = {
            symbol: state_before["engines"][symbol]["fill_count"] for symbol in group
        }
        for symbol in sorted(group):
            coordinator.on_symbol_bar(symbol, group[symbol])
        state = coordinator.account_state()
        if (
            state["margin_usage"] > config.account.hard_margin_usage_pct
            or state["available_funds"] < 0
        ):
            coordinator.cancel_entries_and_flatten(ExitReason.MARGIN_REDUCTION)
        trade_dates = {bar.exchange_trade_date for bar in group.values()}
        if len(trade_dates) != 1:
            raise DataQualityError(
                f"symbols disagree on exchange trade date at {watermark}",
                code="TRADE_DATE_WATERMARK_MISMATCH",
            )
        trade_date = next(iter(trade_dates))
        if current_trade_date != trade_date:
            current_trade_date = trade_date
            day_start_engine_pnl = {
                symbol: engine["marked_equity"] - config.account.initial_equity
                for symbol, engine in state["engines"].items()
            }
        session_ids = {symbol: bar.session_id for symbol, bar in group.items()}
        for symbol, engine_state in state["engines"].items():
            new_trades = coordinator.closed_trades_since(symbol, closed_counts[symbol])
            for trade in new_trades:
                risk_state.record_closed_trade(symbol, trade.net_pnl, trade.exit_time)
            closed_counts[symbol] = engine_state["closed_trade_count"]
            new_fills = coordinator.fills_since(
                symbol,
                fills_before.get(symbol, engine_state["fill_count"]),
            )
            for fill in new_fills:
                base = max(
                    by_symbol[symbol].instruments[fill.contract_code].price_tick
                    * by_symbol[symbol].instruments[fill.contract_code].contract_size
                    * fill.quantity,
                    1e-12,
                )
                execution_fills.append(
                    {
                        "slippage_ratio": fill.slippage / base,
                        "deviation": abs(fill.fill_price - fill.reference_price)
                        / fill.reference_price,
                    }
                )
        flatten = risk_state.update_mark(
            now=watermark,
            exchange_trade_date=trade_date,
            marked_equity=state["marked_equity"],
            session_ids=session_ids,
        )
        for symbol in by_symbol:
            coordinator.update_risk_snapshot(symbol, risk_state.snapshot(symbol))
        if flatten:
            coordinator.cancel_entries_and_flatten(ExitReason.EXCHANGE_KILL_SWITCH)

        for scheduled in sorted(
            schedules.get(watermark, []),
            key=lambda item: (item.candidate.symbol, item.candidate.candidate_id),
        ):
            item = by_symbol[scheduled.candidate.symbol]
            record = (
                item.candidate_records[scheduled.record_index]  # type: ignore[index]
                if update_candidate_records
                else {}
            )
            risk_inputs = {
                **scheduled.risk_inputs,
                **_dynamic_risk_inputs(
                    coordinator,
                    risk_state,
                    scheduled.candidate.symbol,
                    execution_fills,
                    config,
                    day_start_engine_pnl,
                ),
            }
            try:
                _, decision = coordinator.submit_candidate(
                    scheduled.candidate,
                    scheduled.plan,
                    risk_state.snapshot(scheduled.candidate.symbol),
                    risk_inputs,
                )
            except ValueError as exc:
                record["accepted"] = False
                record["reject_reason"] = f"symbol_busy:{exc}"
            else:
                record["accepted"] = bool(decision.allowed and decision.quantity > 0)
                record["reject_reason"] = "" if record["accepted"] else decision.reason

        state = coordinator.account_state()
        minute_equity.append(
            {
                "datetime": watermark,
                "exchange_trade_date": trade_date,
                "marked_equity": state["marked_equity"],
                "margin_used_and_reserved": state["margin_used_and_reserved"],
                "margin_usage": state["margin_usage"],
                "available_funds": state["available_funds"],
                "portfolio_open_risk": state["portfolio_open_risk"],
                "symbol_state_json": json.dumps(
                    {
                        symbol: {
                            "position_margin": value["position_margin"],
                            "working_order_margin_reserve": value[
                                "working_order_margin_reserve"
                            ],
                            "margin_mark_price": value["margin_mark_price"],
                        }
                        for symbol, value in state["engines"].items()
                    },
                    sort_keys=True,
                ),
            }
        )
        for _symbol, bar in group.items():
            if collect_charts:
                chart_rows[bar.root_symbol].append(
                    {
                        "bar_end": bar.bar_end,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "session_id": bar.session_id,
                        "segment_id": bar.segment_id,
                        "segment_start": bar.segment_start,
                    }
                )
    if coordinator.snapshot().pending_watermarks:
        raise DataQualityError(
            "historical replay ended with pending watermarks",
            code="PENDING_HISTORICAL_WATERMARK",
        )
    return {
        "coordinator": coordinator,
        "minute_equity": minute_equity,
        "symbol_bars": {
            root: pd.DataFrame(rows) for root, rows in chart_rows.items()
        },
    }


def _stressed_execution_metadata(
    instrument: InstrumentSpec,
    fee: FeeMarginSpec,
    *,
    fee_multiplier: float,
    slippage_multiplier: float,
    limit_stress: bool,
) -> tuple[InstrumentSpec, FeeMarginSpec]:
    stressed_ticks = instrument.slippage_ticks_base * slippage_multiplier
    if limit_stress:
        stressed_ticks = max(stressed_ticks, 1_000_000.0)
    stressed_instrument = replace(
        instrument,
        slippage_ticks_base=stressed_ticks,
    )
    stressed_fee = replace(
        fee,
        open_fee_rate=fee.open_fee_rate * fee_multiplier,
        close_fee_rate=fee.close_fee_rate * fee_multiplier,
        close_today_fee_rate=fee.close_today_fee_rate * fee_multiplier,
        fee_per_lot_open=fee.fee_per_lot_open * fee_multiplier,
        fee_per_lot_close=fee.fee_per_lot_close * fee_multiplier,
        fee_per_lot_close_today=fee.fee_per_lot_close_today * fee_multiplier,
    )
    return stressed_instrument, stressed_fee


def _run_stress_replays(
    prepared: list[_PreparedSymbol],
    config: ScalpConfig,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    scenarios = (
        ("2x_fee", {"fee_multiplier": 2.0}),
        ("3x_slippage", {"slippage_multiplier": 3.0}),
        ("gap_limit", {"limit_stress": True}),
    )
    for scenario, overrides in scenarios:
        replay = _replay(
            prepared,
            config,
            update_candidate_records=False,
            collect_charts=False,
            **overrides,
        )
        trades = _collect_trades(prepared, replay["coordinator"])
        metrics = compute_trade_metrics(trades)
        minute = pd.DataFrame(replay["minute_equity"])
        max_margin = (
            float(minute["margin_usage"].max()) if not minute.empty else 0.0
        )
        rows.append(
            {
                "scenario": scenario,
                "net_pnl": float(
                    trades.get("net_pnl", pd.Series(dtype=float)).sum()
                ),
                "profit_factor": _finite_or_zero(metrics["profit_factor"]),
                "trade_count": int(len(trades)),
                "max_margin_usage": max_margin,
                "margin_breaches": int(
                    (
                        minute.get("margin_usage", pd.Series(dtype=float))
                        > config.account.hard_margin_usage_pct
                    ).sum()
                ),
                "forced_flat_failures": sum(
                    int(engine.forced_flat_failed)
                    for engine in replay["coordinator"].snapshot().engines.values()
                ),
            }
        )
    return pd.DataFrame(rows)


def _minute_bars(prepared: _PreparedSymbol) -> Iterator[MinuteBar]:
    last_end: datetime | None = None
    for frame in _normalized_frames(prepared, collect_audit=False):
        for row in frame.itertuples(index=False):
            bar = MinuteBar.from_mapping(row._asdict())
            if last_end is not None and bar.bar_end <= last_end:
                raise DataQualityError(
                    f"stream bars are not strictly ordered for {prepared.symbol}",
                    code="OUT_OF_ORDER_STREAM",
                )
            last_end = bar.bar_end
            yield bar


def _instrument_for(prepared: _PreparedSymbol, contract: str) -> InstrumentSpec:
    contract = contract.upper()
    cached = prepared.instruments.get(contract)
    if cached is not None:
        return cached
    try:
        row = prepared.contract_rows[contract]
    except KeyError as exc:
        raise BlockedMetadataError(f"contract spec missing for {contract}") from exc
    try:
        spec = InstrumentSpec(
            root_symbol=str(row["root_symbol"]),
            exchange=str(row["exchange"]),
            contract_size=float(row["contract_size"]),
            price_tick=float(row["price_tick"]),
            lot_step=int(row["lot_step"]),
            slippage_ticks_base=float(row["slippage_ticks_base"]),
            sessions=sessions_for_template(str(row["session_template_id"])),
            effective_from=date(1900, 1, 1),
            effective_to=pd.Timestamp(row["last_trade_date"]).date(),
        )
    except (TypeError, ValueError) as exc:
        raise BlockedMetadataError(f"invalid contract spec for {contract}: {exc}") from exc
    prepared.instruments[contract] = spec
    return spec


def _calendar_for(prepared: _PreparedSymbol, contract: str) -> SessionCalendar:
    contract = contract.upper()
    cached = prepared.calendars.get(contract)
    if cached is not None:
        return cached
    instrument = _instrument_for(prepared, contract)
    frame = prepared.metadata.frames["exchange_calendar.csv"].copy()
    frame = frame.loc[frame["exchange"].astype(str).str.upper().eq(instrument.exchange)]
    if frame.empty:
        raise BlockedMetadataError(f"calendar missing exchange {instrument.exchange}")
    entries: list[TradingCalendarEntry] = []
    for _, row in frame.iterrows():
        try:
            entries.append(
                TradingCalendarEntry(
                    exchange=instrument.exchange,
                    exchange_trade_date=pd.Timestamp(row["exchange_trade_date"]).date(),
                    is_open=_as_bool(row["is_open"]),
                    prior_open_date=pd.Timestamp(row["prior_open_date"]).date(),
                    next_open_date=pd.Timestamp(row["next_open_date"]).date(),
                    night_session_start=_parse_time(row["night_session_start"]),
                    source=str(row["source"]),
                    known_at=_aware_datetime(row["known_at"], "exchange_calendar.known_at"),
                )
            )
        except (TypeError, ValueError, SessionError) as exc:
            raise BlockedMetadataError(f"invalid exchange calendar row: {exc}") from exc
    entries.sort(key=lambda value: value.exchange_trade_date)
    calendar_hash = str(
        prepared.metadata.manifest.get("files", {})
        .get("exchange_calendar.csv", {})
        .get("sha256", "")
    )
    if not calendar_hash:
        raise BlockedMetadataError("exchange calendar manifest hash is missing")
    calendar = SessionCalendar(
        exchange=instrument.exchange,
        entries=tuple(entries),
        sessions=instrument.sessions,
        calendar_sha256=calendar_hash,
    )
    prepared.calendars[contract] = calendar
    return calendar


def _calendar_for_frame(
    prepared: _PreparedSymbol,
    frame: pd.DataFrame,
) -> SessionCalendar:
    contracts = frame["contract_code"].astype(str).unique()
    calendars = {_calendar_for(prepared, contract) for contract in contracts}
    if len(calendars) != 1:
        templates = {tuple(calendar.sessions) for calendar in calendars}
        if len(templates) != 1:
            raise DataQualityError(
                "aggregation frame spans incompatible session templates",
                code="AGGREGATION_TEMPLATE_SWITCH",
            )
    return next(iter(calendars))


def _features_by_contract_epoch(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.sort_values("bar_end").reset_index(drop=True).copy()
    ordered["_contract_epoch"] = ordered["contract_code"].ne(
        ordered["contract_code"].shift()
    ).cumsum()
    parts = [
        add_causal_features(group).assign(_contract_epoch=epoch)
        for epoch, group in ordered.groupby("_contract_epoch", sort=False)
    ]
    return pd.concat(parts, ignore_index=True)


def _regimes_by_epoch(frame: pd.DataFrame, config: ScalpConfig) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for epoch, group in frame.groupby("_contract_epoch", sort=False):
        group = group.reset_index(drop=True)
        classified = classify_regime(
            group,
            config,
            source_contract=str(group.iloc[0]["contract_code"]),
        ).reset_index(drop=True)
        classified["feature_asof"] = pd.to_datetime(classified["feature_asof"])
        classified["_contract_epoch"] = epoch
        classified["realized_vol_pctl_252_30m"] = group[
            "realized_vol_pctl_252_30m"
        ].to_numpy()
        parts.append(classified)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _regime_snapshot(row: pd.Series) -> RegimeSnapshot:
    return RegimeSnapshot(
        state=RegimeState(str(row["state"])),
        direction=int(row["direction"]),
        feature_asof=pd.Timestamp(row["feature_asof"]).to_pydatetime(),
        range_high=float(row["range_high"]),
        range_low=float(row["range_low"]),
        range_mid=float(row["range_mid"]),
        source_contract=str(row["source_contract"]),
    )


def _obstacle_price(
    candidate: SetupCandidate,
    regime: RegimeSnapshot,
    row: pd.Series,
    five_prefix: pd.DataFrame,
    thirty_prefix: pd.DataFrame,
    swings_5m: pd.DataFrame,
    swings_30m: pd.DataFrame,
) -> float:
    if candidate.rule_id == "failed_breakout_range_fade":
        return float(regime.range_mid)
    setup_end = pd.Timestamp(candidate.setup_bar_end)
    five_starts = set(five_prefix.tail(60)["bar_end"])
    thirty_starts = set(thirty_prefix.tail(20)["bar_end"])
    swing_sets = (
        swings_5m.loc[
            (pd.to_datetime(swings_5m["known_at"]) <= setup_end)
            & swings_5m["pivot_bar_end"].isin(five_starts)
        ],
        swings_30m.loc[
            (pd.to_datetime(swings_30m.get("known_at", pd.Series(dtype="datetime64[ns]"))) <= setup_end)
            & swings_30m.get("pivot_bar_end", pd.Series(dtype=object)).isin(thirty_starts)
        ] if not swings_30m.empty else pd.DataFrame(),
    )
    prices: list[float] = []
    kind = "HIGH" if candidate.direction > 0 else "LOW"
    for swings in swing_sets:
        if swings.empty:
            continue
        values = pd.to_numeric(swings.loc[swings["kind"].eq(kind), "price"], errors="coerce")
        if candidate.direction > 0:
            values = values.loc[values > candidate.trigger_price]
        else:
            values = values.loc[values < candidate.trigger_price]
        prices.extend(float(value) for value in values.dropna())
    prices.append(float(row["limit_up"] if candidate.direction > 0 else row["limit_down"]))
    return min(prices) if candidate.direction > 0 else max(prices)


def _candidate_record(
    candidate: SetupCandidate,
    regime: RegimeSnapshot,
    plan: TradePlan | None,
    strategy_version: str,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "strategy_version": strategy_version,
        "symbol": candidate.symbol,
        "contract_code": candidate.contract_code,
        "direction": "LONG" if candidate.direction > 0 else "SHORT",
        "rule_id": candidate.rule_id,
        "regime": candidate.regime.value,
        "setup_bar_end": candidate.setup_bar_end,
        "feature_source_max": candidate.feature_source_max,
        "trigger_price": candidate.trigger_price,
        "structural_stop": candidate.structural_stop,
        "planned_target": plan.target_price if plan else np.nan,
        "planned_net_payoff": plan.planned_net_payoff if plan else np.nan,
        "available_space_r": plan.available_space_r if plan else np.nan,
        "expires_at": candidate.expires_after_bar_end,
        "regime_range_high": regime.range_high,
        "regime_range_low": regime.range_low,
        "second_entry_state": candidate.context.get("second_entry_state", ""),
        "accepted": None,
        "reject_reason": "",
        "context_json": json.dumps(candidate.context, ensure_ascii=False, sort_keys=True),
    }


def _dynamic_risk_inputs(
    coordinator: BrooksScalpPortfolioCoordinator,
    risk_state: PortfolioRiskState,
    symbol: str,
    execution_fills: deque[dict[str, float]],
    config: ScalpConfig,
    day_start_engine_pnl: dict[str, float],
) -> dict[str, Any]:
    snapshot = coordinator.account_state()
    daily_pnl = snapshot["marked_equity"] - risk_state.trade_date_start_equity
    by_cluster: dict[str, float] = defaultdict(float)
    for vt_symbol, engine in snapshot["engines"].items():
        root = _root_symbol(vt_symbol)
        by_cluster[ROOT_CLUSTERS.get(root, "other")] += (
            engine["marked_equity"] - config.account.initial_equity
            - day_start_engine_pnl[vt_symbol]
        )
    minimum = config.risk_components.execution_quality.min_trades_for_assessment
    payload: dict[str, Any] = {
        "marked_equity_daily_pnl": daily_pnl,
        "marked_equity_daily_pnl_by_cluster": dict(by_cluster),
        "execution_quality_sample_count": len(execution_fills),
        "execution_quality_state": (
            "ACTIVE" if len(execution_fills) >= minimum else "INACTIVE_WARMUP"
        ),
    }
    if len(execution_fills) >= minimum:
        payload.update(
            {
                "execution_slippage_ratio": float(
                    np.mean([item["slippage_ratio"] for item in execution_fills])
                ),
                "execution_reject_rate": 0.0,
                "execution_avg_price_deviation_pct": float(
                    np.mean([item["deviation"] for item in execution_fills])
                ),
            }
        )
    del symbol
    return payload


def _collect_orders(coordinator: BrooksScalpPortfolioCoordinator) -> pd.DataFrame:
    rows = [
        order.to_dict()
        for snapshot in coordinator.snapshot().engines.values()
        for order in snapshot.orders
    ]
    if rows:
        return pd.DataFrame(rows).sort_values(
            ["created_at", "order_id"], kind="stable"
        ).reset_index(drop=True)
    return pd.DataFrame(
        columns=[
            "order_id", "candidate_id", "symbol", "contract_code", "direction",
            "offset", "quantity", "price", "order_type", "status", "created_at",
            "eligible_after", "expires_after_bars", "reduce_only", "traded_quantity",
        ]
    )


def _collect_fills(coordinator: BrooksScalpPortfolioCoordinator) -> pd.DataFrame:
    rows = [
        fill.to_dict()
        for snapshot in coordinator.snapshot().engines.values()
        for fill in snapshot.fills
    ]
    if rows:
        return pd.DataFrame(rows).sort_values(
            ["datetime", "fill_id"], kind="stable"
        ).reset_index(drop=True)
    return pd.DataFrame(
        columns=[
            "fill_id", "order_id", "candidate_id", "symbol", "contract_code",
            "datetime", "direction", "offset", "quantity", "reference_price",
            "fill_price", "slippage", "base_fee", "close_today_surcharge",
            "total_fee", "margin_after", "marked_equity_after", "source_bar_end",
            "exchange_trade_date",
        ]
    )


def _collect_order_events(
    coordinator: BrooksScalpPortfolioCoordinator,
) -> pd.DataFrame:
    rows = [
        event.to_dict()
        for snapshot in coordinator.snapshot().engines.values()
        for event in snapshot.order_events
    ]
    if rows:
        return pd.DataFrame(rows).sort_values(["datetime", "event_id"]).reset_index(drop=True)
    return pd.DataFrame(
        columns=[
            "event_id",
            "order_id",
            "status",
            "datetime",
            "traded_quantity",
            "broker_reported_traded_quantity",
            "source",
        ]
    )


def _collect_trades(
    prepared: list[_PreparedSymbol],
    coordinator: BrooksScalpPortfolioCoordinator,
) -> pd.DataFrame:
    candidates = {
        scheduled.candidate.candidate_id: scheduled
        for item in prepared
        for scheduled in (item.scheduled or [])
    }
    rows: list[dict[str, Any]] = []
    for snapshot in coordinator.snapshot().engines.values():
        for trade in snapshot.closed_trades:
            scheduled = candidates[trade.candidate_id]
            context = scheduled.candidate.context
            rows.append(
                {
                    "trade_id": trade.trade_id,
                    "candidate_id": trade.candidate_id,
                    "symbol": trade.symbol,
                    "contract_code": trade.contract_code,
                    "direction": "LONG" if trade.direction > 0 else "SHORT",
                    "rule_id": scheduled.candidate.rule_id,
                    "signal_time": scheduled.candidate.setup_bar_end,
                    "entry_time": trade.entry_time,
                    "entry_price": trade.entry_price,
                    "quantity": trade.quantity,
                    "initial_stop": trade.initial_stop,
                    "planned_target": trade.planned_target,
                    "initial_risk_cny": trade.initial_risk_cny,
                    "exit_time": trade.exit_time,
                    "exit_price": trade.exit_price,
                    "exit_reason": trade.exit_reason.value,
                    "gross_pnl": trade.gross_pnl,
                    "total_fee": trade.total_fee,
                    "total_slippage": trade.total_slippage,
                    "net_pnl": trade.net_pnl,
                    "net_r": trade.net_r,
                    "mfe_r": trade.mfe_r,
                    "mae_r": trade.mae_r,
                    "holding_1m_bars": trade.holding_1m_bars,
                    "session_id": context.get("session_id", ""),
                    "session_type": context.get("session_type", ""),
                    "exchange_trade_date": trade.exchange_trade_date,
                }
            )
    frame = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values(["entry_time", "trade_id"], kind="stable").reset_index(
        drop=True
    )


def _daily_equity(
    minute_equity: pd.DataFrame,
    metadata: MetadataBundle,
    start: date,
    end: date,
    initial_equity: float,
) -> pd.DataFrame:
    calendar = metadata.frames["exchange_calendar.csv"].copy()
    calendar["_date"] = pd.to_datetime(calendar["exchange_trade_date"]).dt.date
    dates = sorted(
        set(
            calendar.loc[
                calendar["is_open"].map(_as_bool)
                & calendar["_date"].between(start, end),
                "_date",
            ]
        )
    )
    marks = {}
    if not minute_equity.empty:
        marks = (
            minute_equity.sort_values("datetime")
            .groupby("exchange_trade_date")["marked_equity"]
            .last()
            .to_dict()
        )
    rows = []
    equity = float(initial_equity)
    for trade_date in dates:
        equity = float(marks.get(trade_date, equity))
        rows.append({"date": trade_date, "equity": equity})
    return pd.DataFrame(rows, columns=["date", "equity"])


def _risk_results(
    trades: pd.DataFrame,
    daily_equity: pd.DataFrame,
    minute_equity: pd.DataFrame,
    coordinator: BrooksScalpPortfolioCoordinator,
    config: ScalpConfig,
    *,
    stress_results: pd.DataFrame,
    daily_spec_keys: set[tuple[str, date]],
    causal_audit_passed: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    required_scenarios = {"2x_fee", "3x_slippage", "gap_limit"}
    if set(stress_results.get("scenario", pd.Series(dtype=str))) != required_scenarios:
        raise ValueError("stress replay results are incomplete")
    stress = stress_results.copy()
    equity = equity_metrics(
        daily_equity,
        initial_equity=config.account.initial_equity,
    )
    daily_returns = pd.to_numeric(daily_equity["equity"]).pct_change()
    if not daily_returns.empty:
        daily_returns.iloc[0] = (
            float(daily_equity.iloc[0]["equity"]) / config.account.initial_equity
            - 1.0
        )
    bootstrap = block_bootstrap_loss_probability(daily_returns)
    snapshot = coordinator.snapshot()
    risk_decisions = list(snapshot.risk_decisions)
    rejected_candidate_ids = {
        str(row.get("candidate_id"))
        for row in risk_decisions
        if row.get("phase") in {"signal", "trigger"}
        and not bool(row.get("allowed", False))
    }
    open_fills = [
        fill
        for engine in snapshot.engines.values()
        for fill in engine.fills
        if fill.offset.value == "OPEN"
    ]
    risk_budget_violations = sum(
        int(
            (
                row.get("phase") in {"signal", "trigger"}
                and bool(row.get("allowed", False))
                and float(row.get("planned_open_risk", 0.0))
                > float(row.get("risk_budget", 0.0)) + 1e-8
            )
            or (
                row.get("phase") == "post_fill"
                and row.get("reason") == "entry_risk_breach"
            )
        )
        for row in risk_decisions
    )
    post_fill_breach_ids = {
        str(row.get("candidate_id"))
        for row in risk_decisions
        if row.get("phase") == "post_fill"
        and row.get("reason") in {"entry_risk_breach", "missing_entry_approval"}
    }
    unresolved_entry_risk_breaches = sum(
        int(
            (
                engine.position is not None
                and engine.position.candidate_id in post_fill_breach_ids
            )
            or any(
                order.candidate_id in post_fill_breach_ids
                and order.order_type.value == "ENTRY_STOP"
                and order.status.value
                in {
                    "CREATED",
                    "RISK_APPROVED",
                    "WORKING",
                    "SUBMITTED",
                    "PARTIALLY_FILLED",
                    "CANCEL_PENDING",
                }
                for order in engine.orders
            )
        )
        for engine in snapshot.engines.values()
    )
    rejected_open_fills = sum(
        int(fill.candidate_id in rejected_candidate_ids) for fill in open_fills
    )
    opens_without_daily_limits = sum(
        int((fill.contract_code, fill.exchange_trade_date) not in daily_spec_keys)
        for fill in open_fills
    )
    max_margin = float(minute_equity["margin_usage"].max()) if not minute_equity.empty else 0.0
    residuals = sum(
        int(engine.position is not None or engine.forced_flat_failed)
        for engine in snapshot.engines.values()
    )
    years = daily_equity.copy()
    years["year"] = pd.to_datetime(years["date"]).dt.year
    yearly = years.groupby("year")["equity"].agg(["first", "last"])
    all_years_positive = bool(not yearly.empty and (yearly["last"] > yearly["first"]).all())
    monthly = daily_equity.copy()
    monthly["date"] = pd.to_datetime(monthly["date"])
    monthly = monthly.set_index("date")["equity"].resample("ME").last()
    quarter = monthly.pct_change(3).dropna()
    positive_quarters = float((quarter > 0).mean()) if not quarter.empty else 0.0
    fee_stress = stress.loc[stress["scenario"].eq("2x_fee")].iloc[0]
    slip_stress = stress.loc[stress["scenario"].eq("3x_slippage")].iloc[0]
    gap_stress = stress.loc[stress["scenario"].eq("gap_limit")].iloc[0]
    score = calculate_risk_score(
        RiskScoreInput(
            max_drawdown=float(equity["max_drawdown"]),
            max_margin_usage=max_margin,
            margin_breach_count=int(
                (minute_equity.get("margin_usage", pd.Series(dtype=float))
                 > config.account.hard_margin_usage_pct).sum()
            ),
            bootstrap_loss_probability=bootstrap,
            worst_daily_return=float(equity["worst_daily_return"]),
            var99=float(equity["var99"]),
            es99=float(equity["es99"]),
            fee_stress_net_pnl=float(fee_stress["net_pnl"]),
            fee_stress_profit_factor=_finite_or_zero(fee_stress["profit_factor"]),
            slippage_stress_net_pnl=float(slip_stress["net_pnl"]),
            slippage_stress_profit_factor=_finite_or_zero(slip_stress["profit_factor"]),
            gap_limit_margin_breaches=int(gap_stress["margin_breaches"]),
            risk_budget_violations=risk_budget_violations,
            circuit_breaker_violations=rejected_open_fills,
            session_close_residuals=residuals,
            all_years_positive=all_years_positive,
            positive_rolling_quarter_ratio=positive_quarters,
            rb_cu_force_failures=sum(
                int(engine.forced_flat_failed) for engine in snapshot.engines.values()
            ),
            causal_audit_passed=causal_audit_passed,
            rollover_audit_passed=_rollover_audit_passes(trades),
            ledger_reconciliation_passed=_ledger_reconciles(
                coordinator, config.account.initial_equity
            ),
            future_leakage=not causal_audit_passed,
            continuous_contract_execution=not _rollover_audit_passes(trades),
            costs_missing=bool(
                not trades.empty
                and (trades["total_fee"].isna().any() or trades["total_slippage"].isna().any())
            ),
            rejected_open_fills=rejected_open_fills,
            unresolved_entry_risk_breaches=unresolved_entry_risk_breaches,
            opens_without_daily_limits=opens_without_daily_limits,
        ),
        minimum_score=config.validation.risk_score_minimum,
    )
    payload = score.to_dict()
    payload["bootstrap_loss_probability"] = bootstrap
    return stress, payload


def _causal_audit_passes(
    candidates: pd.DataFrame,
    trades: pd.DataFrame,
) -> bool:
    if not candidates.empty:
        required = {"feature_source_max", "setup_bar_end"}
        if not required.issubset(candidates):
            return False
        source = pd.to_datetime(candidates["feature_source_max"], errors="coerce", utc=True)
        setup = pd.to_datetime(candidates["setup_bar_end"], errors="coerce", utc=True)
        if source.isna().any() or setup.isna().any() or not bool((source <= setup).all()):
            return False
    if not trades.empty:
        required = {"signal_time", "entry_time", "exit_time"}
        if not required.issubset(trades):
            return False
        signal = pd.to_datetime(trades["signal_time"], errors="coerce", utc=True)
        entry = pd.to_datetime(trades["entry_time"], errors="coerce", utc=True)
        exit_time = pd.to_datetime(trades["exit_time"], errors="coerce", utc=True)
        if (
            signal.isna().any()
            or entry.isna().any()
            or exit_time.isna().any()
            or not bool(((signal < entry) & (entry <= exit_time)).all())
        ):
            return False
    return True


def _rollover_audit_passes(trades: pd.DataFrame) -> bool:
    if trades.empty:
        return True
    contracts = trades["contract_code"].astype(str).str.upper().str.strip()
    continuous_alias = contracts.str.fullmatch(r"[A-Z]+0\.(?:SHF|SHFE)")
    return bool(not continuous_alias.any())


def _research_status(
    trades: pd.DataFrame,
    daily_equity: pd.DataFrame,
    risk_score: dict[str, Any],
    symbols: tuple[str, ...],
    config: ScalpConfig,
) -> str:
    if risk_score.get("vetoes"):
        return "REJECTED"
    groups = build_group_metrics(
        trades,
        minimum_trades=config.validation.minimum_group_trades,
        minimum_win_rate=config.validation.minimum_net_win_rate,
        payoff_min=config.trade_plan.allowed_net_payoff_min,
        payoff_max=config.trade_plan.allowed_net_payoff_max,
        required_symbols=symbols,
        initial_equity=config.account.initial_equity,
        daily_dates=daily_equity.get("date"),
    )
    required = groups.loc[
        groups["group_type"].isin(
            (
                "required_setup_direction",
                "required_symbol",
                "required_symbol_setup_direction",
            )
        )
    ]
    if (required["status"] == "FAILED").any():
        return "REJECTED"
    if (required["status"] == "INCONCLUSIVE").any():
        return "INCONCLUSIVE"
    if risk_score.get("bootstrap_loss_probability") is None:
        return "INCONCLUSIVE"
    if risk_score.get("status") != "PASSED":
        return "REJECTED"
    metrics = equity_metrics(
        daily_equity,
        initial_equity=config.account.initial_equity,
    )
    if (
        metrics["average_daily_return"] < config.validation.average_daily_return_target
        or metrics["average_monthly_return"] < config.validation.average_monthly_return_target
    ):
        return "TARGET_MISSED"
    return "RESEARCH_ACCEPTED"


def _combine_status(statuses: tuple[str, ...]) -> str:
    for status in ("REJECTED", "INCONCLUSIVE", "TARGET_MISSED"):
        if status in statuses:
            return status
    return "RESEARCH_ACCEPTED"


def _mapping_switch_dates(mappings: list[ContractMapping]) -> dict[str, date]:
    result: dict[str, date] = {}
    prior_contract = ""
    switch_date: date | None = None
    for mapping in sorted(mappings, key=lambda value: value.session_open):
        if mapping.contract_code != prior_contract:
            try:
                switch_date = datetime.strptime(
                    mapping.effective_session.split(":", maxsplit=1)[0],
                    "%Y%m%d",
                ).date()
            except ValueError as exc:
                raise BlockedMetadataError(
                    f"invalid effective_session: {mapping.effective_session}"
                ) from exc
            prior_contract = mapping.contract_code
        assert switch_date is not None
        result[mapping.effective_session] = switch_date
    return result


def _exchange_trading_day_count(
    metadata: MetadataBundle,
    exchange: str,
    start: date,
    end: date,
    *,
    minimum_horizon: int | None = None,
) -> int:
    """Count open exchange dates in ``(start, end]`` without calendar guessing."""
    if end < start:
        return 0
    calendar = metadata.frames["exchange_calendar.csv"].copy()
    calendar_dates = pd.to_datetime(
        calendar["exchange_trade_date"],
        errors="coerce",
    ).dt.date
    open_dates = sorted(
        set(
            calendar_dates.loc[
                calendar["exchange"].astype(str).str.upper().eq(exchange.upper())
                & calendar["is_open"].map(_as_bool)
            ]
        )
    )
    if not open_dates or start not in open_dates:
        raise BlockedMetadataError(
            f"open exchange calendar row missing for {exchange} {start}"
        )
    count = sum(start < value <= end for value in open_dates)
    if end > open_dates[-1] and (
        minimum_horizon is None or count < minimum_horizon
    ):
        raise BlockedMetadataError(
            f"exchange calendar horizon is insufficient for {exchange} {start}..{end}"
        )
    return count


def _drop_opening_snapshots(
    raw: pd.DataFrame,
    timestamp_column: str,
    prepared: _PreparedSymbol,
) -> tuple[pd.DataFrame, int]:
    timestamps = _localized_timestamps(raw[timestamp_column])
    starts: set[time] = set()
    for row in prepared.contract_rows.values():
        for session in sessions_for_template(str(row["session_template_id"])):
            starts.update(segment.start for segment in session.segments)
    mask = ~timestamps.dt.time.isin(starts)
    dropped = int((~mask).sum())
    return raw.loc[mask.to_numpy()].reset_index(drop=True), dropped


def _restore_cu_vendor_timestamps(
    raw: pd.DataFrame,
    timestamp_column: str,
    exchange_calendar: pd.DataFrame,
) -> tuple[pd.DataFrame, int, int]:
    """Restore CU after-midnight bars that the vendor stamps with trade date."""
    if timestamp_column != "trade_time" or raw.empty:
        return raw, 0, 0
    timestamps = pd.to_datetime(raw[timestamp_column], errors="coerce")
    if timestamps.isna().any():
        raise DataQualityError("source contains invalid timestamps", code="INVALID_TIMESTAMP")
    after_midnight = timestamps.dt.time.le(time(1, 0))
    if not after_midnight.any():
        return raw, 0, 0

    calendar = exchange_calendar.copy()
    calendar["_date"] = pd.to_datetime(
        calendar["exchange_trade_date"], errors="coerce"
    ).dt.date
    calendar = calendar.loc[
        calendar["exchange"].astype(str).str.upper().eq("SHFE")
    ].set_index("_date", drop=False)
    restored = timestamps.copy()
    keep = pd.Series(True, index=raw.index)
    shifted = 0
    placeholders = 0
    for index in raw.index[after_midnight]:
        trade_date = timestamps.loc[index].date()
        if trade_date not in calendar.index:
            raise BlockedMetadataError(f"calendar missing SHFE trade date {trade_date}")
        row = calendar.loc[trade_date]
        if isinstance(row, pd.DataFrame):
            raise BlockedMetadataError(f"ambiguous SHFE calendar row {trade_date}")
        if _parse_time(row["night_session_start"]) is None:
            volume = _finite_or_zero(raw.loc[index, "vol"])
            turnover = _finite_or_zero(raw.loc[index, "amount"])
            if volume != 0.0 or turnover != 0.0:
                raise DataQualityError(
                    f"CU has activity during a no-night date: {trade_date}",
                    code="NO_NIGHT_SESSION_ACTIVITY",
                )
            keep.loc[index] = False
            placeholders += 1
            continue
        prior_open = pd.Timestamp(row["prior_open_date"]).date()
        restored_date = prior_open + timedelta(days=1)
        value = datetime.combine(restored_date, timestamps.loc[index].time())
        if value != timestamps.loc[index].to_pydatetime():
            shifted += 1
        restored.loc[index] = value
    result = raw.loc[keep].copy()
    result[timestamp_column] = restored.loc[keep].to_numpy()
    return result.reset_index(drop=True), shifted, placeholders


def _drop_vendor_no_night_rows(
    raw: pd.DataFrame,
    timestamp_column: str,
    exchange_calendar: pd.DataFrame,
    *,
    exchange: str,
) -> tuple[pd.DataFrame, int]:
    """Drop copied/synthetic vendor night rows on exchange no-night dates."""
    if raw.empty or exchange.upper() != "SHFE":
        return raw, 0
    timestamps = pd.to_datetime(raw[timestamp_column], errors="coerce")
    if timestamps.isna().any():
        raise DataQualityError("source contains invalid timestamps", code="INVALID_TIMESTAMP")
    calendar = exchange_calendar.copy()
    calendar["_date"] = pd.to_datetime(
        calendar["exchange_trade_date"], errors="coerce"
    ).dt.date
    calendar["_prior"] = pd.to_datetime(
        calendar["prior_open_date"], errors="coerce"
    ).dt.date
    calendar = calendar.loc[
        calendar["exchange"].astype(str).str.upper().eq("SHFE")
    ]
    by_date = calendar.set_index("_date", drop=False)
    by_prior = calendar.set_index("_prior", drop=False)
    keep = pd.Series(True, index=raw.index)
    for index, timestamp in timestamps.items():
        trade_date: date | None = None
        if timestamp.time() >= time(20, 0):
            if timestamp.date() in by_prior.index:
                row = by_prior.loc[timestamp.date()]
                if isinstance(row, pd.DataFrame):
                    raise BlockedMetadataError(
                        f"ambiguous next SHFE open date after {timestamp.date()}"
                    )
                trade_date = row["_date"]
        elif timestamp.time() < time(3, 0):
            trade_date = timestamp.date()
        if trade_date is None or trade_date not in by_date.index:
            continue
        row = by_date.loc[trade_date]
        if isinstance(row, pd.DataFrame):
            raise BlockedMetadataError(f"ambiguous SHFE calendar row {trade_date}")
        if _parse_time(row["night_session_start"]) is None:
            keep.loc[index] = False
    dropped = int((~keep).sum())
    return raw.loc[keep].reset_index(drop=True), dropped


def _localized_timestamps(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.isna().any():
        raise DataQualityError("source contains invalid timestamps", code="INVALID_TIMESTAMP")
    if parsed.dt.tz is None:
        return parsed.dt.tz_localize("Asia/Shanghai")
    return parsed.dt.tz_convert("Asia/Shanghai")


def _validate_contract_known_at(
    prepared: _PreparedSymbol,
    contract: str,
    session_open: datetime,
) -> None:
    known = _aware_datetime(
        prepared.contract_rows[contract]["known_at"],
        "contract_specs.known_at",
    )
    if known > session_open:
        raise BlockedMetadataError(f"contract spec known late for {contract}")


def _require_authoritative_sources(
    prepared: _PreparedSymbol,
    contract: str,
    daily: DailyTradingSpec,
    fee: FeeMarginSpec,
) -> None:
    if not prepared.config.metadata.require_authoritative_exchange_source:
        return
    values = (
        str(prepared.contract_rows[contract]["source"]),
        daily.source,
        fee.source,
    )
    exchange = str(prepared.contract_rows[contract]["exchange"]).strip().upper()
    authoritative_prefixes = (f"{exchange}_", f"{exchange}-", f"{exchange}:")
    if any(
        not (
            value.upper().startswith(authoritative_prefixes)
            or "BROKER" in value.upper()
        )
        for value in values
    ):
        raise BlockedMetadataError(
            f"non-authoritative metadata source for {contract}: {values}"
        )


def _source_directory(data_root: Path, symbol: str, root: str) -> Path:
    candidates = (
        data_root / symbol,
        data_root / symbol.replace(".SHFE", ".SHF"),
        data_root / f"{root}0.SHF",
        data_root / root,
    )
    existing = [path for path in candidates if path.is_dir()]
    if not existing:
        raise BlockedMetadataError(f"source directory missing for {symbol} under {data_root}")
    return existing[0]


def _source_files(directory: Path, start: date, end: date) -> tuple[Path, ...]:
    dated_paths: list[tuple[date, Path]] = []
    for path in directory.glob("*.parquet"):
        try:
            source_date = pd.Timestamp(path.stem).date()
        except ValueError:
            continue
        if source_date <= end:
            dated_paths.append((source_date, path))
    prior = [item for item in dated_paths if item[0] < start]
    selected = [item for item in dated_paths if start <= item[0] <= end]
    if prior:
        selected.append(max(prior, key=lambda item: item[0]))
    return tuple(path for _, path in sorted(selected))


def _trim_pre_start_partition(
    raw: pd.DataFrame,
    *,
    timestamp_column: str,
    source_date: date,
    start: date,
) -> pd.DataFrame:
    if source_date >= start:
        return raw
    timestamps = _localized_timestamps(raw[timestamp_column])
    return raw.loc[timestamps.dt.time >= time(18, 0)].reset_index(drop=True)


def _verify_source_hashes(prepared: list[_PreparedSymbol]) -> None:
    for item in prepared:
        for path_text, expected in item.source_hashes.items():
            actual = sha256_file(path_text)
            if actual != expected:
                raise DataQualityError(
                    f"source file changed during run: {path_text}",
                    code="SOURCE_HASH_CHANGED",
                )


def _ledger_reconciles(
    coordinator: BrooksScalpPortfolioCoordinator,
    initial_equity: float,
) -> bool:
    snapshot = coordinator.snapshot()
    if any(engine.position is not None for engine in snapshot.engines.values()):
        return False
    gross = 0.0
    fees = 0.0
    for symbol, engine in snapshot.engines.items():
        contract_size = coordinator.instrument_spec(symbol).contract_size
        fills = pd.DataFrame([fill.to_dict() for fill in engine.fills])
        if fills.empty:
            continue
        fees += float(pd.to_numeric(fills["total_fee"]).sum())
        for _, candidate_fills in fills.groupby("candidate_id", sort=False):
            opened = candidate_fills.loc[
                candidate_fills["offset"].astype(str).str.upper().eq("OPEN")
            ]
            closed = candidate_fills.loc[~candidate_fills.index.isin(opened.index)]
            open_quantity = int(pd.to_numeric(opened["quantity"]).sum())
            close_quantity = int(pd.to_numeric(closed["quantity"]).sum())
            if open_quantity != close_quantity:
                return False
            if open_quantity == 0:
                continue
            direction = int(opened.iloc[0]["direction"])
            open_notional = float(
                (pd.to_numeric(opened["fill_price"]) * pd.to_numeric(opened["quantity"])).sum()
            )
            close_notional = float(
                (pd.to_numeric(closed["fill_price"]) * pd.to_numeric(closed["quantity"])).sum()
            )
            gross += direction * (close_notional - open_notional) * contract_size
    recomputed = initial_equity + gross - fees
    return math.isclose(snapshot.marked_equity, recomputed, rel_tol=0.0, abs_tol=1e-6)


def _root_symbol(symbol: str) -> str:
    head = str(symbol).split(".", 1)[0].upper()
    root = "".join(value for value in head if value.isalpha())
    if not root:
        raise ValueError(f"cannot infer root symbol: {symbol}")
    return root


def _aware_datetime(value: object, field_name: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise BlockedMetadataError(f"{field_name} must be timezone-aware")
    return timestamp.tz_convert("Asia/Shanghai").to_pydatetime()


def _parse_time(value: object) -> time | None:
    if pd.isna(value) or str(value).strip() == "":
        return None
    parsed = pd.Timestamp(f"2000-01-01 {value}")
    return parsed.time()


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _finite_or_zero(value: object) -> float:
    parsed = float(value)
    return parsed if math.isfinite(parsed) else 0.0


__all__ = ["run_research_pipeline", "sessions_for_template"]
