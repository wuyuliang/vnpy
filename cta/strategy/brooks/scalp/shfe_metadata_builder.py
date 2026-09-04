"""Build auditable RB/CU metadata from SHFE daily market and parameter files."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .metadata import BlockedMetadataError
from .metadata_importer import (
    CANONICAL_SCHEMAS,
    StagingSource,
    import_metadata_bundle,
    sha256_file,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SHFE_MARKET_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{date}.dat"
SHFE_PARAMETER_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/js{date}.dat"
SHFE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/",
}


@dataclass(frozen=True)
class ProductRule:
    root_symbol: str
    contract_size: float
    price_tick: float
    session_template_id: str
    slippage_ticks_base: float = 1.0


PRODUCT_RULES = {
    "RB": ProductRule("RB", 10.0, 1.0, "SHFE_RB_NIGHT_2300"),
    "CU": ProductRule("CU", 5.0, 10.0, "SHFE_CU_NIGHT_0100"),
}


@dataclass(frozen=True)
class SettlementParameters:
    contract_code: str
    source_date: date
    settlement: float
    margin_rate_long: float
    margin_rate_short: float
    open_fee_rate: float
    close_today_fee_rate: float
    fee_per_lot_open: float
    fee_per_lot_close_today: float


@dataclass(frozen=True)
class MarketDaily:
    contract_code: str
    trade_date: date
    pre_settlement: float
    settlement: float
    high: float
    low: float


@dataclass(frozen=True)
class CachedSource:
    kind: str
    source_date: date
    url: str
    sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SourceDiscovery:
    required_contracts: dict[tuple[str, date], set[str]]
    night_dates: dict[str, set[date]]
    source_contracts: set[str]


def _finite_float(value: object, field_name: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"{field_name} is missing")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} is not finite")
    return result


def _optional_float(value: object, fallback: float) -> float:
    if value is None or str(value).strip() == "":
        return fallback
    result = float(value)
    return result if math.isfinite(result) else fallback


def _contract_code(value: object) -> str:
    matched = re.fullmatch(r"\s*([A-Za-z]+)(\d+)\s*", str(value))
    if matched is None:
        raise ValueError(f"invalid SHFE contract code: {value!r}")
    return f"{matched.group(1).upper()}{matched.group(2)}.SHF"


def parse_shfe_settlement_rows(
    payload: dict[str, Any],
    *,
    source_date: date,
) -> dict[str, SettlementParameters]:
    """Parse both legacy and current SHFE ``jsYYYYMMDD.dat`` schemas."""
    result: dict[str, SettlementParameters] = {}
    for raw in payload.get("o_cursor", []):
        instrument = raw.get("INSTRUMENTID")
        if instrument is None:
            continue
        try:
            contract = _contract_code(instrument)
        except ValueError:
            continue
        root = re.match(r"[A-Z]+", contract)
        if root is None or root.group() not in PRODUCT_RULES:
            continue
        standard_ratio = _finite_float(raw.get("TRADEFEERATIO", 0.0), "TRADEFEERATIO")
        standard_unit = _finite_float(raw.get("TRADEFEEUNIT", 0.0), "TRADEFEEUNIT")
        close_today_ratio = _optional_float(raw.get("TTRADEFEERATIO"), standard_ratio)
        close_today_unit = _optional_float(raw.get("TTRADEFEEUNIT"), standard_unit)
        long_margin = _finite_float(
            raw.get("SPECLONGMARGINRATIO"), "SPECLONGMARGINRATIO"
        )
        short_margin = _finite_float(
            raw.get("SPECSHORTMARGINRATIO"), "SPECSHORTMARGINRATIO"
        )
        if min(long_margin, short_margin) <= 0.02:
            raise ValueError(f"invalid SHFE margin for {contract} on {source_date}")
        result[contract] = SettlementParameters(
            contract_code=contract,
            source_date=source_date,
            settlement=_finite_float(raw.get("SETTLEMENTPRICE"), "SETTLEMENTPRICE"),
            margin_rate_long=long_margin,
            margin_rate_short=short_margin,
            open_fee_rate=standard_ratio / 1_000.0,
            close_today_fee_rate=close_today_ratio / 1_000.0,
            fee_per_lot_open=standard_unit,
            fee_per_lot_close_today=close_today_unit,
        )
    return result


def parse_shfe_market_rows(
    payload: dict[str, Any],
    *,
    trade_date: date,
) -> dict[str, MarketDaily]:
    """Parse required RB/CU rows from ``kxYYYYMMDD.dat``."""
    result: dict[str, MarketDaily] = {}
    for raw in payload.get("o_curinstrument", []):
        delivery_month = str(raw.get("DELIVERYMONTH", "")).strip()
        product = str(raw.get("PRODUCTGROUPID") or raw.get("PRODUCTID") or "")
        root = product.split("_", maxsplit=1)[0].strip().upper()
        if root not in PRODUCT_RULES or not delivery_month.isdigit():
            continue
        contract = _contract_code(f"{root}{delivery_month}")
        try:
            result[contract] = MarketDaily(
                contract_code=contract,
                trade_date=trade_date,
                pre_settlement=_finite_float(
                    raw.get("PRESETTLEMENTPRICE"), "PRESETTLEMENTPRICE"
                ),
                settlement=_finite_float(raw.get("SETTLEMENTPRICE"), "SETTLEMENTPRICE"),
                high=_finite_float(raw.get("HIGHESTPRICE"), "HIGHESTPRICE"),
                low=_finite_float(raw.get("LOWESTPRICE"), "LOWESTPRICE"),
            )
        except ValueError:
            # Non-trading far-month rows can contain blanks; mapped contracts may not.
            continue
    return result


def _floor_tick(value: float, tick: float) -> float:
    units = math.floor(value / tick + 1e-10)
    result = units * tick
    return float(int(result)) if float(result).is_integer() else float(result)


def build_contract_daily_row(
    *,
    contract_code: str,
    trade_date: date,
    prior_open_date: date,
    session_open: datetime,
    pre_settlement: float,
    settlement: float,
    observed_high: float,
    observed_low: float,
    product_rule: ProductRule,
    settlement_parameters: SettlementParameters,
    market_source_url: str,
    parameter_source_url: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create causal daily and fee rows from prior-close SHFE parameters."""
    if session_open.tzinfo is None:
        raise ValueError("session_open must be timezone-aware")
    if settlement_parameters.source_date != prior_open_date:
        raise ValueError("settlement parameters must come from the prior open date")
    if settlement_parameters.contract_code != contract_code:
        raise ValueError("settlement parameter contract mismatch")
    if abs(settlement_parameters.settlement - pre_settlement) > product_rule.price_tick / 2:
        raise ValueError(
            f"prior SHFE settlement mismatch for {contract_code} {trade_date}: "
            f"js={settlement_parameters.settlement}, kx_pre={pre_settlement}"
        )

    limit_rate = min(
        settlement_parameters.margin_rate_long,
        settlement_parameters.margin_rate_short,
    ) - 0.02
    if not 0 < limit_rate < 0.5:
        raise ValueError(f"invalid derived SHFE limit rate for {contract_code}: {limit_rate}")
    limit_up = _floor_tick(
        pre_settlement * (1.0 + limit_rate), product_rule.price_tick
    )
    limit_down = _floor_tick(
        pre_settlement * (1.0 - limit_rate), product_rule.price_tick
    )
    tolerance = product_rule.price_tick * 1e-7
    if observed_high > limit_up + tolerance or observed_low < limit_down - tolerance:
        raise ValueError(
            f"market prices outside derived SHFE limits for {contract_code} {trade_date}: "
            f"low/high={observed_low}/{observed_high}, limits={limit_down}/{limit_up}"
        )

    known_at = session_open.astimezone(SHANGHAI_TZ).isoformat()
    schedule_id = f"SHFE-{contract_code.removesuffix('.SHF')}-{trade_date:%Y%m%d}"
    daily = {
        "contract_code": contract_code,
        "exchange_trade_date": trade_date.isoformat(),
        "pre_settlement": pre_settlement,
        "settlement": settlement,
        "limit_rate": limit_rate,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "limit_rounding_rule": "SHFE_FLOOR_TICK_SPEC_MARGIN_MINUS_2PP",
        "source": "SHFE_KX_JS_RULE_DERIVED",
        "source_url_or_file": f"{market_source_url}|{parameter_source_url}",
        "known_at": known_at,
        "fee_margin_schedule_id": schedule_id,
        "settlement_known_at": datetime.combine(
            trade_date, time(15, 30), tzinfo=SHANGHAI_TZ
        ).isoformat(),
    }
    fee = {
        "schedule_id": schedule_id,
        "root_symbol": product_rule.root_symbol,
        "contract_code": contract_code,
        "effective_from": known_at,
        "effective_to": "",
        "margin_rate_long": settlement_parameters.margin_rate_long,
        "margin_rate_short": settlement_parameters.margin_rate_short,
        "open_fee_rate": settlement_parameters.open_fee_rate,
        "close_fee_rate": settlement_parameters.open_fee_rate,
        "close_today_fee_rate": settlement_parameters.close_today_fee_rate,
        "fee_per_lot_open": settlement_parameters.fee_per_lot_open,
        "fee_per_lot_close": settlement_parameters.fee_per_lot_open,
        "fee_per_lot_close_today": settlement_parameters.fee_per_lot_close_today,
        "source": "SHFE_JS_OFFICIAL",
        "source_url_or_file": parameter_source_url,
        "known_at": known_at,
    }
    return daily, fee


def _source_directories(data_root: Path) -> dict[str, Path]:
    candidates = {
        "RB": (data_root / "RB", data_root / "RB0.SHF"),
        "CU": (data_root / "CU0.SHF", data_root / "CU"),
    }
    result: dict[str, Path] = {}
    for root, paths in candidates.items():
        matched = next((path for path in paths if path.is_dir()), None)
        if matched is None:
            raise BlockedMetadataError(f"minute source directory is missing for {root}")
        result[root] = matched
    return result


def _available_dates(directory: Path, start: date, end: date) -> set[date]:
    result: set[date] = set()
    for path in directory.glob("*.parquet"):
        try:
            value = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start <= value <= end:
            result.add(value)
    return result


def _night_leg_is_complete(
    minutes: set[int],
    *,
    has_positive_volume: bool,
    first_minute: int,
    last_minute: int,
) -> bool:
    """Confirm endpoint coverage while allowing at most two missing minute bars."""
    if not has_positive_volume:
        return False
    ordered = sorted(value for value in minutes if first_minute <= value <= last_minute)
    if not ordered or ordered[0] > first_minute + 1 or ordered[-1] < last_minute:
        return False
    return all(
        current - prior <= 3
        for prior, current in zip(ordered, ordered[1:], strict=False)
    )


def _discover_source_requirements(
    directories: dict[str, Path],
    *,
    trade_dates: tuple[date, ...],
) -> SourceDiscovery:
    requested = set(trade_dates)
    next_open = {
        current: following
        for current, following in zip(trade_dates, trade_dates[1:], strict=False)
    }
    required: dict[tuple[str, date], set[str]] = defaultdict(set)
    leg_minutes: dict[tuple[str, date, str], set[int]] = defaultdict(set)
    positive_legs: set[tuple[str, date, str]] = set()
    source_contracts: set[str] = set()
    lower = trade_dates[0] - timedelta(days=7)
    upper = trade_dates[-1]

    for root, directory in directories.items():
        timestamp_column = "datetime" if root == "RB" else "trade_time"
        contract_column = "ts_code" if root == "RB" else "contract_code"
        volume_column = "volume" if root == "RB" else "vol"
        for path in sorted(directory.glob("*.parquet")):
            try:
                source_date = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if not lower <= source_date <= upper:
                continue
            frame = pd.read_parquet(
                path,
                columns=[timestamp_column, contract_column, volume_column],
            )
            timestamps = pd.to_datetime(frame[timestamp_column], errors="raise")
            contracts = frame[contract_column].astype(str).str.upper().str.strip()
            volumes = pd.to_numeric(frame[volume_column], errors="coerce").fillna(0.0)
            source_contracts.update(contracts)
            for timestamp, contract, volume in zip(
                timestamps, contracts, volumes, strict=True
            ):
                natural_date = timestamp.date()
                is_evening = timestamp.time() >= time(20, 0)
                if is_evening:
                    trade_date = next_open.get(natural_date)
                else:
                    trade_date = natural_date
                if trade_date not in requested:
                    continue
                required[(root, trade_date)].add(contract)
                is_after_midnight = timestamp.time() <= time(1, 0)
                leg = "evening" if is_evening else "after_midnight" if is_after_midnight else None
                if leg is not None:
                    key = (root, trade_date, leg)
                    leg_minutes[key].add(timestamp.hour * 60 + timestamp.minute)
                    if volume > 0:
                        positive_legs.add(key)

    for root in directories:
        missing = [value for value in trade_dates if not required[(root, value)]]
        if missing:
            raise BlockedMetadataError(
                f"cannot identify exact {root} contract for dates: "
                f"{[value.isoformat() for value in missing[:10]]}"
            )
    rb_nights: set[date] = set()
    cu_nights: set[date] = set()
    for trade_date in trade_dates:
        rb_evening = ("RB", trade_date, "evening")
        cu_evening = ("CU", trade_date, "evening")
        cu_midnight = ("CU", trade_date, "after_midnight")
        if _night_leg_is_complete(
            leg_minutes[rb_evening],
            has_positive_volume=rb_evening in positive_legs,
            first_minute=21 * 60,
            last_minute=23 * 60,
        ):
            rb_nights.add(trade_date)
        if _night_leg_is_complete(
            leg_minutes[cu_evening],
            has_positive_volume=cu_evening in positive_legs,
            first_minute=21 * 60,
            last_minute=24 * 60 - 1,
        ) and _night_leg_is_complete(
            leg_minutes[cu_midnight],
            has_positive_volume=cu_midnight in positive_legs,
            first_minute=0,
            last_minute=60,
        ):
            cu_nights.add(trade_date)
    night_dates = {"RB": rb_nights, "CU": cu_nights}
    return SourceDiscovery(dict(required), night_dates, source_contracts)


def _tushare_reference_data(
    *,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise BlockedMetadataError("TUSHARE_TOKEN is required for calendar/contract archives")
    import tushare as ts

    pro = ts.pro_api(token)
    calendar = pro.trade_cal(
        exchange="SHFE",
        start_date=(start - timedelta(days=40)).strftime("%Y%m%d"),
        end_date=(end + timedelta(days=45)).strftime("%Y%m%d"),
        fields="exchange,cal_date,is_open,pretrade_date",
    )
    contracts = pro.fut_basic(
        exchange="SHFE",
        fut_type="1",
        fields="ts_code,symbol,exchange,fut_code,list_date,delist_date",
    )
    if calendar.empty or contracts.empty:
        raise BlockedMetadataError("Tushare SHFE calendar/contract reference is empty")
    return calendar, contracts


def _fetch_one(
    *,
    kind: str,
    source_date: date,
    cache_root: Path,
    timeout: float,
) -> CachedSource:
    template = SHFE_MARKET_URL if kind == "kx" else SHFE_PARAMETER_URL
    url = template.format(date=source_date.strftime("%Y%m%d"))
    path = cache_root / kind / f"{source_date:%Y%m%d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data: bytes | None = None
    if path.is_file():
        data = path.read_bytes()
    else:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = requests.get(url, headers=SHFE_HEADERS, timeout=timeout)
                response.raise_for_status()
                data = response.content
                json.loads(data)
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
                break
            except (OSError, requests.RequestException, json.JSONDecodeError) as exc:
                last_error = exc
                time_module.sleep(0.5 * (2**attempt))
        if data is None:
            raise BlockedMetadataError(f"cannot download {url}: {last_error}")
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise BlockedMetadataError(f"cached SHFE response is invalid JSON: {path}") from exc
    required_key = "o_curinstrument" if kind == "kx" else "o_cursor"
    if not payload.get(required_key):
        raise BlockedMetadataError(f"SHFE {kind} response has no rows for {source_date}")
    return CachedSource(
        kind=kind,
        source_date=source_date,
        url=url,
        sha256=hashlib.sha256(data).hexdigest(),
        payload=payload,
    )


def _download_sources(
    *,
    trade_dates: tuple[date, ...],
    prior_open: dict[date, date],
    cache_root: Path,
    workers: int,
    timeout: float,
) -> dict[tuple[str, date], CachedSource]:
    requests_to_make = [*(('kx', value) for value in trade_dates)]
    requests_to_make.extend(
        ("js", value) for value in sorted({prior_open[day] for day in trade_dates})
    )
    result: dict[tuple[str, date], CachedSource] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _fetch_one,
                kind=kind,
                source_date=source_date,
                cache_root=cache_root,
                timeout=timeout,
            ): (kind, source_date)
            for kind, source_date in requests_to_make
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result[key] = future.result()
            except Exception as exc:  # noqa: BLE001 - aggregate all missing dates
                failures.append(f"{key[0]} {key[1]}: {exc}")
    if failures:
        raise BlockedMetadataError(
            f"SHFE source download failed for {len(failures)} files: {failures[:10]}"
        )
    return result


def _calendar_rows(
    calendar: pd.DataFrame,
    *,
    start: date,
    end: date,
    night_dates: set[date],
) -> tuple[list[dict[str, Any]], dict[date, date]]:
    frame = calendar.copy()
    frame["_date"] = pd.to_datetime(frame["cal_date"], errors="raise").dt.date
    frame = frame.loc[frame["is_open"].astype(int).eq(1)].sort_values("_date")
    open_dates = list(frame["_date"])
    if start not in open_dates or end not in open_dates:
        raise BlockedMetadataError("requested endpoints are not SHFE open dates")
    prior = {
        current: previous
        for previous, current in zip(open_dates, open_dates[1:], strict=False)
    }
    following = {
        current: later
        for current, later in zip(open_dates, open_dates[1:], strict=False)
    }
    rows: list[dict[str, Any]] = []
    for value in open_dates:
        if value not in prior or value not in following:
            continue
        rows.append(
            {
                "exchange": "SHFE",
                "exchange_trade_date": value.isoformat(),
                "is_open": 1,
                "prior_open_date": prior[value].isoformat(),
                "next_open_date": following[value].isoformat(),
                "night_session_start": (
                    "21:00:00"
                    if value < start or value > end or value in night_dates
                    else ""
                ),
                "source": "SHFE_CALENDAR_VIA_TUSHARE_AND_OBSERVED_SESSIONS",
                "known_at": datetime.combine(
                    prior[value], time(15, 30), tzinfo=SHANGHAI_TZ
                ).isoformat(),
            }
        )
    return rows, prior


def _contract_rows(
    contracts: pd.DataFrame,
    *,
    used_contracts: Iterable[str],
) -> list[dict[str, Any]]:
    frame = contracts.copy()
    frame["ts_code"] = frame["ts_code"].astype(str).str.upper()
    indexed = frame.set_index("ts_code", drop=False)
    rows: list[dict[str, Any]] = []
    for contract in sorted(set(used_contracts)):
        if contract not in indexed.index:
            raise BlockedMetadataError(f"Tushare contract archive missing {contract}")
        raw = indexed.loc[contract]
        if isinstance(raw, pd.DataFrame):
            if len(raw) != 1:
                raise BlockedMetadataError(f"ambiguous contract archive row for {contract}")
            raw = raw.iloc[0]
        root = str(raw["fut_code"]).upper()
        rule = PRODUCT_RULES[root]
        list_date = pd.Timestamp(raw["list_date"]).date()
        rows.append(
            {
                "contract_code": contract,
                "root_symbol": root,
                "exchange": "SHFE",
                "contract_size": rule.contract_size,
                "price_tick": rule.price_tick,
                "lot_step": 1,
                "slippage_ticks_base": rule.slippage_ticks_base,
                "last_trade_date": pd.Timestamp(raw["delist_date"]).date().isoformat(),
                "session_template_id": rule.session_template_id,
                "source": "SHFE_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC",
                "known_at": datetime.combine(
                    list_date, time(0), tzinfo=SHANGHAI_TZ
                ).isoformat(),
            }
        )
    return rows


def _write_source_audit(
    path: Path,
    sources: dict[tuple[str, date], CachedSource],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for key in sorted(sources):
            source = sources[key]
            rows_key = "o_curinstrument" if source.kind == "kx" else "o_cursor"
            relevant = []
            for row in source.payload.get(rows_key, []):
                text = json.dumps(row, ensure_ascii=True).upper()
                if "RB" in text or "CU" in text:
                    relevant.append(row)
            handle.write(
                json.dumps(
                    {
                        "kind": source.kind,
                        "source_date": source.source_date.isoformat(),
                        "url": source.url,
                        "response_sha256": source.sha256,
                        "report_date": source.payload.get("report_date"),
                        "update_date": source.payload.get("update_date"),
                        "rows": relevant,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
                + "\n"
            )


def _coverage(frame: pd.DataFrame, filename: str) -> tuple[str, str]:
    column = {
        "exchange_calendar.csv": "exchange_trade_date",
        "contract_specs.csv": "last_trade_date",
        "contract_daily.csv": "exchange_trade_date",
        "fee_margin_schedule.csv": "effective_from",
    }[filename]
    values = pd.to_datetime(frame[column], errors="raise")
    return values.min().date().isoformat(), values.max().date().isoformat()


def _canonical_frame(rows: list[dict[str, Any]], filename: str) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    required = list(CANONICAL_SCHEMAS[filename])
    extras = [column for column in frame.columns if column not in required]
    return frame.loc[:, [*required, *extras]]


def build_metadata_bundle(
    *,
    start: date,
    end: date,
    data_root: Path,
    output_root: Path,
    cache_root: Path,
    workers: int = 8,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Download, validate, and publish a complete canonical RB/CU bundle."""
    if end < start:
        raise ValueError("end precedes start")
    directories = _source_directories(data_root)
    date_sets = {
        root: _available_dates(directory, start, end)
        for root, directory in directories.items()
    }
    if date_sets["RB"] != date_sets["CU"]:
        differences = sorted(date_sets["RB"] ^ date_sets["CU"])
        raise BlockedMetadataError(
            f"RB/CU source calendars differ: {[value.isoformat() for value in differences[:10]]}"
        )
    trade_dates = tuple(sorted(date_sets["RB"]))
    if not trade_dates or trade_dates[0] != start or trade_dates[-1] != end:
        raise BlockedMetadataError("minute files do not span requested endpoint dates")

    discovery = _discover_source_requirements(directories, trade_dates=trade_dates)
    calendar_ref, contract_ref = _tushare_reference_data(start=start, end=end)
    combined_night_dates = set.intersection(*discovery.night_dates.values())
    calendar_rows, prior_open = _calendar_rows(
        calendar_ref,
        start=start,
        end=end,
        night_dates=combined_night_dates,
    )
    missing_prior = [value for value in trade_dates if value not in prior_open]
    if missing_prior:
        raise BlockedMetadataError(f"calendar missing prior open dates: {missing_prior[:10]}")
    sources = _download_sources(
        trade_dates=trade_dates,
        prior_open=prior_open,
        cache_root=cache_root,
        workers=workers,
        timeout=timeout,
    )

    daily_rows: list[dict[str, Any]] = []
    fee_rows: list[dict[str, Any]] = []
    used_contracts: set[str] = set()
    for trade_date in trade_dates:
        prior_date = prior_open[trade_date]
        market_source = sources[("kx", trade_date)]
        parameter_source = sources[("js", prior_date)]
        market = parse_shfe_market_rows(market_source.payload, trade_date=trade_date)
        parameters = parse_shfe_settlement_rows(
            parameter_source.payload, source_date=prior_date
        )
        has_night = trade_date in combined_night_dates
        session_open = datetime.combine(
            prior_date if has_night else trade_date,
            time(21) if has_night else time(9),
            tzinfo=SHANGHAI_TZ,
        )
        for root in PRODUCT_RULES:
            contracts = discovery.required_contracts[(root, trade_date)]
            for contract in sorted(contracts):
                if contract not in market:
                    raise BlockedMetadataError(
                        f"SHFE kx missing mapped contract {contract} {trade_date}"
                    )
                if contract not in parameters:
                    raise BlockedMetadataError(
                        f"SHFE prior js missing mapped contract {contract} {prior_date}"
                    )
                value = market[contract]
                daily, fee = build_contract_daily_row(
                    contract_code=contract,
                    trade_date=trade_date,
                    prior_open_date=prior_date,
                    session_open=session_open,
                    pre_settlement=value.pre_settlement,
                    settlement=value.settlement,
                    observed_high=value.high,
                    observed_low=value.low,
                    product_rule=PRODUCT_RULES[root],
                    settlement_parameters=parameters[contract],
                    market_source_url=market_source.url,
                    parameter_source_url=parameter_source.url,
                )
                daily_rows.append(daily)
                fee_rows.append(fee)
                used_contracts.add(contract)

    frames = {
        "exchange_calendar.csv": _canonical_frame(
            calendar_rows, "exchange_calendar.csv"
        ),
        "contract_specs.csv": _canonical_frame(
            _contract_rows(
                contract_ref,
                used_contracts=used_contracts | discovery.source_contracts,
            ),
            "contract_specs.csv",
        ),
        "contract_daily.csv": _canonical_frame(daily_rows, "contract_daily.csv"),
        "fee_margin_schedule.csv": _canonical_frame(
            fee_rows, "fee_margin_schedule.csv"
        ),
    }

    staging = cache_root / "canonical_staging"
    staging.mkdir(parents=True, exist_ok=True)
    downloaded_at = datetime.now(tz=SHANGHAI_TZ)
    staging_sources: dict[str, StagingSource] = {}
    for filename, frame in frames.items():
        path = staging / filename
        frame.to_csv(path, index=False)
        coverage_start, coverage_end = _coverage(frame, filename)
        staging_sources[filename] = StagingSource(
            path=path,
            source_url_or_file="SHFE official kx/js + Tushare SHFE calendar/basic mirror",
            downloaded_at=downloaded_at,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            sha256=sha256_file(path),
        )
    manifest = import_metadata_bundle(staging_sources, output_root)
    source_audit_path = output_root / "shfe_source_audit.jsonl.gz"
    _write_source_audit(source_audit_path, sources)
    manifest["source_audit"] = {
        "path": source_audit_path.name,
        "sha256": sha256_file(source_audit_path),
        "source_files": len(sources),
        "limit_derivation": "spec_margin_minus_2pp_then_floor_both_prices_to_tick",
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "status": "READY",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "trade_dates": len(trade_dates),
        "contracts": len(used_contracts | discovery.source_contracts),
        "daily_rows": len(daily_rows),
        "fee_rows": len(fee_rows),
        "nightless_dates": [
            value.isoformat() for value in trade_dates if value not in combined_night_dates
        ],
        "manifest_sha256": sha256_file(output_root / "manifest.json"),
    }
    (output_root / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2018-01-03")
    parser.add_argument("--end", default="2026-04-16")
    parser.add_argument("--data-root", default="cta/data/origin/minute")
    parser.add_argument("--output-root", default="cta/strategy/brooks/scalp/meta")
    parser.add_argument("--cache-root", default="/private/tmp/vnpy_shfe_scalp_metadata")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = build_metadata_bundle(
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            data_root=Path(args.data_root),
            output_root=Path(args.output_root),
            cache_root=Path(args.cache_root),
            workers=max(1, args.workers),
            timeout=args.timeout,
        )
    except (BlockedMetadataError, ValueError, OSError, requests.RequestException) as exc:
        print(json.dumps({"status": "BLOCKED_METADATA", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ProductRule",
    "SettlementParameters",
    "build_contract_daily_row",
    "build_metadata_bundle",
    "parse_shfe_market_rows",
    "parse_shfe_settlement_rows",
]
