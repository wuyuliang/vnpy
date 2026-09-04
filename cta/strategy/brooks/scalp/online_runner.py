"""Metadata-gated launcher preflight for Brooks scalp paper/dry-run sessions."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path

import pandas as pd

from .config import DEFAULT_CONFIG_PATH, ScalpConfig, config_sha256, load_config
from .engine import ScalpEngine
from .metadata import (
    BlockedMetadataError,
    DEFAULT_META_ROOT,
    FeeMarginSpec,
    InstrumentSpec,
    MetadataBundle,
)
from .portfolio_coordinator import BrooksScalpPortfolioCoordinator
from .research_pipeline import sessions_for_template
from .risk_policy import RuleOnlyRiskAdapter
from .session import (
    SHANGHAI_TZ,
    SessionCalendar,
    SessionSpec,
    TradingCalendarEntry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["RB0.SHFE", "CU0.SHFE"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--meta-root", default=str(DEFAULT_META_ROOT))
    parser.add_argument("--contracts", nargs="+")
    parser.add_argument("--trade-date")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        bundle = MetadataBundle.load(Path(args.meta_root))
    except (BlockedMetadataError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED_METADATA", "reason": str(exc)}, ensure_ascii=False))
        return 2
    if not args.contracts or len(args.contracts) != len(args.symbols):
        print(
            json.dumps(
                {
                    "status": "BLOCKED_CONTRACT_MAPPING",
                    "reason": "--contracts must provide one frozen real contract per symbol",
                },
                ensure_ascii=False,
            )
        )
        return 3
    if not args.trade_date:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_TRADE_DATE",
                    "reason": "--trade-date is required for causal runtime metadata",
                },
                ensure_ascii=False,
            )
        )
        return 3
    try:
        runtime = build_dry_run_runtime(
            symbols=tuple(args.symbols),
            contracts=tuple(args.contracts),
            trade_date=pd.Timestamp(args.trade_date).date(),
            bundle=bundle,
            config=config,
        )
    except (BlockedMetadataError, ValueError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED_RUNTIME", "reason": str(exc)}, ensure_ascii=False))
        return 3
    if not args.dry_run:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_GATEWAY_ADAPTER",
                    "reason": "paper mode requires an injected vn.py gateway and broker reconciliation adapter",
                },
                ensure_ascii=False,
            )
        )
        return 4
    snapshot = runtime.snapshot()
    payload = {
        "status": "READY_DRY_RUN",
        "symbols": list(args.symbols),
        "contracts": list(args.contracts),
        "trade_date": args.trade_date,
        "initial_equity": config.account.initial_equity,
        "config_sha256": config_sha256(config),
        "metadata_manifest": bundle.manifest,
        "reconciliation": {
            "marked_equity": snapshot.marked_equity,
            "margin_used_and_reserved": snapshot.margin_used_and_reserved,
            "open_position_count": sum(
                int(engine.position is not None) for engine in snapshot.engines.values()
            ),
            "active_order_count": sum(
                len(engine.orders) for engine in snapshot.engines.values()
            ),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def build_dry_run_runtime(
    *,
    symbols: tuple[str, ...],
    contracts: tuple[str, ...],
    trade_date: date,
    bundle: MetadataBundle,
    config: ScalpConfig,
) -> BrooksScalpPortfolioCoordinator:
    if not symbols or len(symbols) != len(contracts) or len(set(symbols)) != len(symbols):
        raise ValueError("symbols/contracts must be non-empty, unique, and aligned")
    coordinator = BrooksScalpPortfolioCoordinator(
        initial_equity=config.account.initial_equity,
        mode="live",
        stale_tolerance_seconds=config.coordination.active_bar_stale_tolerance_seconds,
        risk_adapter=RuleOnlyRiskAdapter(config, mode="live"),
    )
    for symbol, contract in zip(symbols, contracts, strict=True):
        instrument, fee, calendar = _runtime_metadata(
            bundle,
            contract.upper(),
            trade_date,
        )
        coordinator.register_symbol(
            symbol,
            ScalpEngine(
                instrument,
                fee,
                initial_cash=config.account.initial_equity,
                auto_match=False,
                entry_valid_bars=config.intervals.entry_valid_1m_bars,
                no_follow_through_bars=config.intervals.no_follow_through_bars,
                max_holding_bars=config.intervals.max_holding_1m_bars,
                force_flat_minutes=config.risk.force_flat_minutes,
                allow_cross_session=config.trade_plan.allow_cross_session,
            ),
            calendar,
        )
    return coordinator


def _runtime_metadata(
    bundle: MetadataBundle,
    contract: str,
    trade_date: date,
) -> tuple[InstrumentSpec, FeeMarginSpec, SessionCalendar]:
    rows = bundle.frames["contract_specs.csv"].loc[
        bundle.frames["contract_specs.csv"]["contract_code"]
        .astype(str)
        .str.upper()
        .eq(contract)
    ]
    if len(rows) != 1:
        raise BlockedMetadataError(
            f"expected one contract spec for {contract}, got {len(rows)}"
        )
    row = rows.iloc[0]
    sessions = sessions_for_template(str(row["session_template_id"]))
    instrument = InstrumentSpec(
        root_symbol=str(row["root_symbol"]),
        exchange=str(row["exchange"]),
        contract_size=float(row["contract_size"]),
        price_tick=float(row["price_tick"]),
        lot_step=int(row["lot_step"]),
        slippage_ticks_base=float(row["slippage_ticks_base"]),
        sessions=sessions,
        effective_from=date(1900, 1, 1),
        effective_to=pd.Timestamp(row["last_trade_date"]).date(),
    )
    calendar_frame = bundle.frames["exchange_calendar.csv"].loc[
        bundle.frames["exchange_calendar.csv"]["exchange"]
        .astype(str)
        .str.upper()
        .eq(instrument.exchange)
    ]
    entries = tuple(
        sorted(
            (_calendar_entry(value, instrument.exchange) for _, value in calendar_frame.iterrows()),
            key=lambda value: value.exchange_trade_date,
        )
    )
    calendar_hash = str(
        bundle.manifest.get("files", {})
        .get("exchange_calendar.csv", {})
        .get("sha256", "")
    )
    if not calendar_hash:
        raise BlockedMetadataError("exchange calendar manifest hash is missing")
    calendar = SessionCalendar(
        exchange=instrument.exchange,
        entries=entries,
        sessions=sessions,
        calendar_sha256=calendar_hash,
    )
    session_open = _first_session_open(entries, sessions, trade_date)
    bundle.require_coverage(
        contract_codes=(contract,),
        trade_dates=(trade_date,),
        session_opens={(contract, trade_date): session_open},
    )
    daily = bundle.daily_spec(contract, trade_date)
    fee = bundle.fee_margin_spec(
        daily.fee_margin_schedule_id,
        contract,
        session_open,
    )
    return instrument, fee, calendar


def _calendar_entry(row: pd.Series, exchange: str) -> TradingCalendarEntry:
    return TradingCalendarEntry(
        exchange=exchange,
        exchange_trade_date=pd.Timestamp(row["exchange_trade_date"]).date(),
        is_open=_as_bool(row["is_open"]),
        prior_open_date=pd.Timestamp(row["prior_open_date"]).date(),
        next_open_date=pd.Timestamp(row["next_open_date"]).date(),
        night_session_start=_parse_time(row["night_session_start"]),
        source=str(row["source"]),
        known_at=_aware_datetime(row["known_at"]),
    )


def _first_session_open(
    entries: tuple[TradingCalendarEntry, ...],
    sessions: tuple[SessionSpec, ...],
    trade_date: date,
) -> datetime:
    entry = next(
        (
            value
            for value in entries
            if value.exchange_trade_date == trade_date and value.is_open
        ),
        None,
    )
    if entry is None:
        raise BlockedMetadataError(f"open calendar entry missing for {trade_date}")
    session = sessions[0]
    segment = session.segments[0]
    start_day = entry.exchange_trade_date
    if session.is_night:
        start_day = entry.prior_open_date
        if segment.start < entry.night_session_start:
            start_day += timedelta(days=1)
    return datetime.combine(start_day, segment.start, tzinfo=SHANGHAI_TZ)


def _aware_datetime(value: object) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise BlockedMetadataError("calendar known_at must be timezone-aware")
    return timestamp.tz_convert("Asia/Shanghai").to_pydatetime()


def _parse_time(value: object) -> time:
    return pd.Timestamp(f"2000-01-01 {value}").time()


def _as_bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes"}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_dry_run_runtime", "build_parser", "main"]
