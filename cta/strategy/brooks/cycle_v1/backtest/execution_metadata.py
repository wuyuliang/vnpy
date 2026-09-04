"""Adapt audited SHFE/scalp mechanics to cycle_v1 point-in-time metadata."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from ..instruments.metadata import (
    ContractLifecycleSpec,
    DailyTradingSpec,
    ExecutionCostSpec,
    InstrumentSpec,
    MetadataStore,
    OrderCapabilitySpec,
    TradingStatusSpec,
)
from .data_loader import LoadedSymbol


BACKTEST_GATEWAY = "CYCLE_V1_BAR_BACKTEST"
BACKTEST_CAPABILITY_SOURCE = (
    "cta.strategy.brooks.cycle_v1.backtest.engine_adapter.ConservativeBarMatcher"
)
_PRIMARY_KEYS = {
    "exchange_calendar.csv": ("exchange", "exchange_trade_date"),
    "contract_specs.csv": ("contract_code",),
    "contract_daily.csv": ("contract_code", "exchange_trade_date"),
    "fee_margin_schedule.csv": (
        "schedule_id",
        "contract_code",
        "effective_from",
    ),
}
POINT_IN_TIME_DAILY_COLUMNS = (
    "pre_settlement_known_at",
    "settlement_known_at",
    "contract_size",
    "price_tick",
    "mechanics_source",
    "mechanics_known_at",
)


def point_in_time_daily_mask(frame: pd.DataFrame) -> pd.Series:
    """Identify daily rows carrying complete causal price and mechanics fields."""
    if frame.empty or not set(POINT_IN_TIME_DAILY_COLUMNS).issubset(frame):
        return pd.Series(False, index=frame.index, dtype=bool)
    mask = frame.loc[:, POINT_IN_TIME_DAILY_COLUMNS].notna().all(axis=1)
    source = frame["mechanics_source"].astype(str).str.strip().str.lower()
    return mask & ~source.isin({"", "nan"})


def merge_canonical_frames(
    base: dict[str, pd.DataFrame],
    extension: dict[str, pd.DataFrame],
    *,
    start: date,
    end: date,
) -> dict[str, pd.DataFrame]:
    """Merge one observed extension while blocking changed historical mechanics."""
    if end < start or set(base) != set(_PRIMARY_KEYS) or set(extension) != set(base):
        raise ValueError("canonical metadata merge inputs are invalid")
    selected = {name: frame.copy() for name, frame in extension.items()}
    calendar_dates = pd.to_datetime(
        selected["exchange_calendar.csv"]["exchange_trade_date"], errors="raise"
    ).dt.date
    selected["exchange_calendar.csv"] = selected["exchange_calendar.csv"].loc[
        calendar_dates.map(lambda value: start <= value <= end)
    ].copy()
    daily_dates = pd.to_datetime(
        selected["contract_daily.csv"]["exchange_trade_date"], errors="raise"
    ).dt.date
    selected["contract_daily.csv"] = selected["contract_daily.csv"].loc[
        daily_dates.map(lambda value: start <= value <= end)
    ].copy()
    schedules = set(
        selected["contract_daily.csv"]["fee_margin_schedule_id"].astype(str)
    )
    contracts = set(selected["contract_daily.csv"]["contract_code"].astype(str))
    selected["fee_margin_schedule.csv"] = selected[
        "fee_margin_schedule.csv"
    ].loc[
        selected["fee_margin_schedule.csv"]["schedule_id"].astype(str).isin(schedules)
    ].copy()
    selected["contract_specs.csv"] = selected["contract_specs.csv"].loc[
        selected["contract_specs.csv"]["contract_code"].astype(str).isin(contracts)
    ].copy()

    base_daily = base["contract_daily.csv"]
    legacy_daily_keys = _frame_keys(
        base_daily.loc[~point_in_time_daily_mask(base_daily)],
        _PRIMARY_KEYS["contract_daily.csv"],
    )
    incoming_daily = selected["contract_daily.csv"]
    complete_incoming = incoming_daily.loc[
        point_in_time_daily_mask(incoming_daily)
    ]
    migrated_daily_keys = legacy_daily_keys.intersection(
        _frame_keys(complete_incoming, _PRIMARY_KEYS["contract_daily.csv"])
    )
    migrated_daily = complete_incoming.loc[
        _key_mask(
            complete_incoming,
            _PRIMARY_KEYS["contract_daily.csv"],
            migrated_daily_keys,
        )
    ]
    migrated_schedule_ids = set(
        migrated_daily["fee_margin_schedule_id"].astype(str)
    )
    validation_exclusions = {
        "contract_daily.csv": migrated_daily_keys,
    }

    result: dict[str, pd.DataFrame] = {}
    for name, keys in _PRIMARY_KEYS.items():
        current = base[name].copy()
        incoming = selected[name].copy()
        if name == "fee_margin_schedule.csv" and migrated_schedule_ids:
            current = current.loc[
                ~current["schedule_id"].astype(str).isin(migrated_schedule_ids)
            ].copy()
        excluded_keys = validation_exclusions.get(name, set())
        validation_base = current.loc[
            ~_key_mask(current, keys, excluded_keys)
        ]
        _validate_overlapping_rows(
            name,
            validation_base,
            incoming,
            keys,
            ignored_columns={"night_session_start", "source"}
            if name == "exchange_calendar.csv"
            else set(),
        )
        if name == "exchange_calendar.csv":
            current_source = current.get(
                "source", pd.Series("", index=current.index, dtype=str)
            )
            observed_keys = _frame_keys(
                current.loc[
                    current_source.astype(str).str.contains(
                        "OBSERVED_SESSIONS", regex=False
                    )
                ],
                keys,
            )
            incoming_source = incoming.get(
                "source", pd.Series("", index=incoming.index, dtype=str)
            )
            generic_calendar = incoming_source.astype(str).str.endswith(
                "_TUSHARE_TRADE_CAL"
            )
            incoming = incoming.loc[
                ~(generic_calendar & _key_mask(incoming, keys, observed_keys))
            ].copy()
        incoming_keys = {
            tuple(_key_value(row[column]) for column in keys)
            for _, row in incoming.iterrows()
        }
        keep = current.apply(
            lambda row, keys=keys, incoming_keys=incoming_keys: tuple(
                _key_value(row[column]) for column in keys
            )
            not in incoming_keys,
            axis=1,
        )
        result[name] = (
            pd.concat([current.loc[keep], incoming], ignore_index=True)
            .sort_values(list(keys), kind="stable", na_position="last")
            .reset_index(drop=True)
        )
    return result


def build_execution_metadata_store(
    bundle: Any,
    symbols: tuple[LoadedSymbol, ...],
    *,
    cost_stress_mult: float,
) -> MetadataStore:
    """Build metadata only for actual contract/date pairs present in minute data."""
    if not symbols:
        raise ValueError("execution metadata requires loaded symbols")
    if cost_stress_mult < 1:
        raise ValueError("cost_stress_mult must be at least one")
    frames = bundle.frames
    required = {
        "contract_specs.csv",
        "contract_daily.csv",
        "fee_margin_schedule.csv",
    }
    missing = sorted(required.difference(frames))
    if missing:
        raise ValueError(f"metadata bundle is missing: {','.join(missing)}")

    contract_specs = frames["contract_specs.csv"].copy()
    contract_daily = frames["contract_daily.csv"].copy()
    fee_schedules = frames["fee_margin_schedule.csv"].copy()
    contract_daily["_trade_date"] = pd.to_datetime(
        contract_daily["exchange_trade_date"], errors="coerce"
    ).dt.date

    instruments_by_date: dict[tuple[str, str, date], InstrumentSpec] = {}
    lifecycles: list[ContractLifecycleSpec] = []
    capabilities: list[OrderCapabilitySpec] = []
    daily_specs: list[DailyTradingSpec] = []
    statuses: list[TradingStatusSpec] = []
    costs: list[ExecutionCostSpec] = []

    for loaded in symbols:
        pairs = loaded.minute_bars.loc[
            :, ["contract_code", "exchange_trade_date"]
        ].drop_duplicates()
        contracts = tuple(sorted(pairs["contract_code"].astype(str).unique()))
        selected_specs = contract_specs.loc[
            contract_specs["contract_code"].astype(str).isin(contracts)
        ].copy()
        if len(selected_specs) != len(contracts):
            found = set(selected_specs["contract_code"].astype(str))
            absent = sorted(set(contracts).difference(found))
            raise ValueError(f"contract specs are missing: {','.join(absent)}")
        _require_one_row_per_key(selected_specs, "contract_code", "contract spec")
        known_values = selected_specs["known_at"].map(_aware_datetime)
        capabilities.append(
            OrderCapabilitySpec(
                exchange=loaded.exchange,
                gateway=BACKTEST_GATEWAY,
                root_symbol=loaded.root_symbol,
                contract_code=None,
                order_type="STOP",
                supported=True,
                max_order_size=None,
                effective_from=min(known_values),
                effective_to=None,
                source=BACKTEST_CAPABILITY_SOURCE,
                known_at=min(known_values),
            )
        )
        for row in selected_specs.itertuples(index=False):
            listed_on = _aware_datetime(row.known_at)
            lifecycles.append(
                ContractLifecycleSpec(
                    contract_code=str(row.contract_code),
                    listed_on=listed_on.date(),
                    last_trade_date=pd.Timestamp(row.last_trade_date).date(),
                    delivery_start=None,
                    delivery_end=None,
                    client_delivery_eligible=False,
                    effective_from=listed_on,
                    effective_to=None,
                    source=str(row.source),
                    known_at=listed_on,
                )
            )

        for pair in pairs.itertuples(index=False):
            contract = str(pair.contract_code)
            trade_date = _as_date(pair.exchange_trade_date)
            daily_row = _exact_row(
                contract_daily.loc[
                    contract_daily["contract_code"].astype(str).eq(contract)
                    & contract_daily["_trade_date"].eq(trade_date)
                ],
                f"contract daily {contract} {trade_date}",
            )
            schedule_id = str(daily_row["fee_margin_schedule_id"])
            fee_row = _exact_row(
                fee_schedules.loc[
                    fee_schedules["schedule_id"].astype(str).eq(schedule_id)
                    & fee_schedules["contract_code"].astype(str).eq(contract)
                ],
                f"fee schedule {schedule_id} {contract}",
            )
            known_at = _aware_datetime(daily_row["known_at"])
            fee_known_at = _aware_datetime(fee_row["known_at"])
            session_effective_from = _aware_datetime(fee_row["effective_from"])
            if known_at > session_effective_from:
                raise ValueError(
                    f"contract daily {contract} {trade_date} was not known "
                    "at session open"
                )
            if fee_known_at > session_effective_from:
                raise ValueError(
                    f"fee schedule {schedule_id} was not known at session open"
                )
            effective_to = datetime.combine(
                trade_date + timedelta(days=1),
                datetime.min.time(),
                tzinfo=session_effective_from.tzinfo,
            )
            margin_long = float(fee_row["margin_rate_long"])
            margin_short = float(fee_row["margin_rate_short"])
            daily_source = _source(daily_row)
            spec_row = _exact_row(
                selected_specs.loc[
                    selected_specs["contract_code"].astype(str).eq(contract)
                ],
                f"contract spec {contract}",
            )
            (
                contract_size,
                price_tick,
                mechanics_source,
                mechanics_known_at,
            ) = _effective_daily_mechanics(daily_row, spec_row)
            if mechanics_known_at > known_at:
                raise ValueError(
                    f"contract mechanics were not known at session open for "
                    f"{contract} {trade_date}"
                )
            instrument = InstrumentSpec(
                root_symbol=loaded.root_symbol,
                exchange=loaded.exchange,
                contract_size=contract_size,
                price_tick=price_tick,
                lot_step=int(spec_row["lot_step"]),
                sessions=loaded.sessions,
                effective_from=trade_date,
                effective_to=trade_date + timedelta(days=1),
                source=mechanics_source,
                known_at=mechanics_known_at,
            )
            instrument_key = (loaded.root_symbol, loaded.exchange, trade_date)
            existing_instrument = instruments_by_date.get(instrument_key)
            if existing_instrument is not None:
                latest_known_at = max(
                    existing_instrument.known_at,
                    instrument.known_at,
                )
                existing_instrument = replace(
                    existing_instrument,
                    known_at=latest_known_at,
                )
                instrument = replace(instrument, known_at=latest_known_at)
                if existing_instrument != instrument:
                    raise ValueError(
                        "conflicting contract mechanics for "
                        f"{loaded.root_symbol} {trade_date}"
                    )
            instruments_by_date[instrument_key] = instrument
            daily_specs.append(
                DailyTradingSpec(
                    contract_code=contract,
                    exchange_trade_date=trade_date,
                    pre_settlement=float(daily_row["pre_settlement"]),
                    limit_up=float(daily_row["limit_up"]),
                    limit_down=float(daily_row["limit_down"]),
                    margin_rate_long=margin_long,
                    margin_rate_short=margin_short,
                    fee_schedule_id=schedule_id,
                    effective_from=session_effective_from,
                    effective_to=effective_to,
                    source=daily_source,
                    known_at=known_at,
                )
            )
            statuses.append(
                TradingStatusSpec(
                    contract_code=contract,
                    exchange_trade_date=trade_date,
                    tradable=True,
                    suspended=False,
                    close_only=False,
                    max_position=None,
                    status_reason="OPEN_CALENDAR_MAPPED_CONTRACT_WITH_SHFE_DAILY_SPEC",
                    effective_from=session_effective_from,
                    effective_to=effective_to,
                    source=daily_source,
                    known_at=known_at,
                )
            )
            pre_settlement = float(daily_row["pre_settlement"])
            rate_fee = pre_settlement * contract_size * (
                float(fee_row["open_fee_rate"])
                + max(
                    float(fee_row["close_fee_rate"]),
                    float(fee_row["close_today_fee_rate"]),
                )
            )
            fixed_fee = float(fee_row["fee_per_lot_open"]) + max(
                float(fee_row["fee_per_lot_close"]),
                float(fee_row["fee_per_lot_close_today"]),
            )
            base_slippage = float(spec_row["slippage_ticks_base"])
            costs.append(
                ExecutionCostSpec(
                    fee_schedule_id=schedule_id,
                    contract_code=contract,
                    stressed_round_trip_fee_cash=(rate_fee + fixed_fee)
                    * cost_stress_mult,
                    stressed_entry_slippage_ticks=base_slippage * cost_stress_mult,
                    stressed_round_trip_slippage_ticks=2.0
                    * base_slippage
                    * cost_stress_mult,
                    effective_from=session_effective_from,
                    effective_to=effective_to,
                    source=_source(fee_row),
                    known_at=fee_known_at,
                    open_fee_rate=float(fee_row["open_fee_rate"]),
                    close_fee_rate=float(fee_row["close_fee_rate"]),
                    close_today_fee_rate=float(fee_row["close_today_fee_rate"]),
                    fee_per_lot_open=float(fee_row["fee_per_lot_open"]),
                    fee_per_lot_close=float(fee_row["fee_per_lot_close"]),
                    fee_per_lot_close_today=float(
                        fee_row["fee_per_lot_close_today"]
                    ),
                    fee_stress_multiplier=cost_stress_mult,
                )
            )

    return MetadataStore(
        instruments=instruments_by_date.values(),
        daily_specs=daily_specs,
        lifecycles=lifecycles,
        capabilities=capabilities,
        statuses=statuses,
        costs=costs,
    )


def _require_one_row_per_key(frame: pd.DataFrame, key: str, label: str) -> None:
    duplicated = frame.duplicated(key, keep=False)
    if duplicated.any():
        values = sorted(set(frame.loc[duplicated, key].astype(str)))
        raise ValueError(f"ambiguous {label}: {','.join(values)}")


def _effective_daily_mechanics(
    daily_row: pd.Series,
    spec_row: pd.Series,
) -> tuple[float, float, str, datetime]:
    daily_size = daily_row.get("contract_size")
    daily_tick = daily_row.get("price_tick")
    has_daily_size = not pd.isna(daily_size)
    has_daily_tick = not pd.isna(daily_tick)
    if has_daily_size != has_daily_tick:
        raise ValueError("daily contract_size and price_tick must appear together")
    if not has_daily_size:
        return (
            float(spec_row["contract_size"]),
            float(spec_row["price_tick"]),
            str(spec_row["source"]),
            _aware_datetime(spec_row["known_at"]),
        )
    mechanics_source = str(daily_row.get("mechanics_source", "")).strip()
    mechanics_known = daily_row.get("mechanics_known_at")
    if not mechanics_source or mechanics_source.lower() == "nan" or pd.isna(
        mechanics_known
    ):
        raise ValueError("daily contract mechanics provenance is missing")
    return (
        float(daily_size),
        float(daily_tick),
        mechanics_source,
        _aware_datetime(mechanics_known),
    )


def _validate_overlapping_rows(
    name: str,
    base: pd.DataFrame,
    extension: pd.DataFrame,
    keys: tuple[str, ...],
    *,
    ignored_columns: set[str],
) -> None:
    overlap = base.merge(extension, on=list(keys), how="inner", suffixes=("_base", "_ext"))
    comparison = [
        column
        for column in base.columns
        if column not in keys and column not in ignored_columns
    ]
    for _, row in overlap.iterrows():
        conflicts = [
            column
            for column in comparison
            if _key_value(row[f"{column}_base"])
            != _key_value(row[f"{column}_ext"])
        ]
        if conflicts:
            identity = ",".join(str(row[column]) for column in keys)
            raise ValueError(
                f"conflicting {name} row {identity}: {','.join(conflicts)}"
            )


def _key_value(value: object) -> object:
    return None if pd.isna(value) else value


def _frame_keys(
    frame: pd.DataFrame,
    keys: tuple[str, ...],
) -> set[tuple[object, ...]]:
    return {
        tuple(_key_value(row[column]) for column in keys)
        for _, row in frame.iterrows()
    }


def _key_mask(
    frame: pd.DataFrame,
    keys: tuple[str, ...],
    selected_keys: set[tuple[object, ...]],
) -> pd.Series:
    if not selected_keys:
        return pd.Series(False, index=frame.index, dtype=bool)
    return frame.apply(
        lambda row: tuple(_key_value(row[column]) for column in keys)
        in selected_keys,
        axis=1,
    )


def _exact_row(frame: pd.DataFrame, label: str) -> pd.Series:
    if len(frame) != 1:
        raise ValueError(f"expected one {label} row, got {len(frame)}")
    return frame.iloc[0]


def _aware_datetime(value: object) -> datetime:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        raise ValueError("metadata timestamp must be timezone-aware")
    return parsed.tz_convert("Asia/Shanghai").to_pydatetime()


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _source(row: pd.Series) -> str:
    values = [str(row.get("source", "")).strip()]
    source_file = str(row.get("source_url_or_file", "")).strip()
    if source_file and source_file.lower() != "nan":
        values.append(source_file)
    result = "|".join(value for value in values if value)
    if not result:
        raise ValueError("metadata source is required")
    return result


__all__ = [
    "BACKTEST_GATEWAY",
    "POINT_IN_TIME_DAILY_COLUMNS",
    "build_execution_metadata_store",
    "merge_canonical_frames",
    "point_in_time_daily_mask",
]
