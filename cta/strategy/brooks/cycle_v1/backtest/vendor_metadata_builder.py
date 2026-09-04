"""Build audited multi-exchange mechanics without metadata defaults."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache
import gzip
import hashlib
from io import StringIO
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from cta.data_code.futures_downloader_utils import normalize_contract_filename
from cta.data_code.tushare_client import RateLimiter, _safe_retry
from cta.strategy.brooks.scalp.metadata import MetadataBundle
from cta.strategy.brooks.scalp.metadata_importer import (
    CANONICAL_FILENAMES,
    CANONICAL_SCHEMAS,
    StagingSource,
    import_metadata_bundle,
    sha256_file,
    verify_manifest,
)

from .data_loader import DiscoveredSymbol
from .execution_metadata import merge_canonical_frames, point_in_time_daily_mask


_CONTRACT = re.compile(r"^(?P<root>[A-Z]+)(?P<delivery>\d+)$")
_LEADING_NUMBER = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)")
_CASH_FEE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*元$")
_RATE_FEE = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)\s*/\s*万分之"
    r"(?:\s*\([^()]*元\))?$"
)
_PARTITION_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d")
_EXCHANGE_ALIASES = {
    "CFX": "CFFEX",
    "CZC": "CZCE",
    "GFE": "GFEX",
    "SHF": "SHFE",
    "ZCE": "CZCE",
}
_CZCE_NIGHT_2300_ROOTS_2026 = frozenset(
    {
        "CF",
        "CY",
        "FG",
        "MA",
        "OI",
        "PF",
        "PL",
        "PR",
        "PX",
        "RM",
        "SA",
        "SH",
        "SR",
        "TA",
    }
)
_CZCE_DAY_ROOTS_2026 = frozenset({"AP", "CJ", "PK", "RS", "SF", "SM", "UR"})
_CZCE_GENERIC_OTHER_HOURS = "及交易所规定的其他交易时间"
_GTJA_EXCHANGE_NAMES = {
    "CFFEX": "中金所",
    "CZCE": "郑商所",
    "DCE": "大商所",
    "GFEX": "广期所",
    "INE": "能源中心",
    "SHFE": "上期所",
}
_SHFE_PARAMETER_URL = (
    "https://www.shfe.com.cn/data/tradedata/future/dailydata/js{date}.dat"
)
_INE_PARAMETER_URL = (
    "https://www.ine.cn/data/tradedata/future/dailydata/js{date}.dat"
)
_CZCE_PARAMETER_URL = (
    "http://www.czce.com.cn/cn/DFSStaticFiles/Future/{year}/{date}/"
    "FutureDataClearParams.txt"
)
_INE_EC_ORIGINAL_SPEC_URL = (
    "https://www.ine.cn/products/futures/index_f/ec_f/standard_ec_f/"
    "202312/t20231205_802544.html"
)
_INE_EC_REVISED_SPEC_URL = (
    "https://www.ine.cn/publicnotice/notice/202601/t20260116_830126.html"
)
_INE_EC_TICK_CHANGE_DATE = date(2026, 5, 11)
_DCE_P_ORIGINAL_SPEC_SOURCE = "DCE_NOTICE_2007-10-09_P_CONTRACT"
_DCE_P_REVISED_SPEC_URL = (
    "https://www.dce.com.cn/dce/content/2026/ywggytz/18628268.html"
)
_DCE_P_TICK_CHANGE_DATE = date(2026, 4, 10)
_DCE_Y_VALIDATED_MECHANICS_START_DATE = date(2025, 9, 15)
_DCE_Y_TICK_CHANGE_DATE = date(2026, 4, 10)
_DCE_Y_VALIDATED_MECHANICS_SOURCE = (
    "GTJA_TRADING_RULE_20250520_Y2605:"
    "https://www.gtjaqh.com/pc/calendar?date=20250520"
    "|GTJA_TRADING_RULE_20250915_Y2609:"
    "https://www.gtjaqh.com/pc/calendar?date=20250915"
)
_DCE_Y_REVISED_MECHANICS_SOURCE = (
    f"{_DCE_P_REVISED_SPEC_URL}"
    "|GTJA_TRADING_RULE_20260410_Y2609:"
    "https://www.gtjaqh.com/pc/calendar?date=20260410"
)
_DCE_FB_FEE_SNAPSHOT_PATH = (
    Path(__file__).with_name("source_snapshots") / "dce_fb_fee_20260617.csv"
)
_DCE_PG_FEE_SNAPSHOT_PATH = (
    Path(__file__).with_name("source_snapshots") / "dce_pg_fee_20260323.csv"
)
_DCE_HISTORICAL_FEE_SNAPSHOT_PATHS = (
    _DCE_FB_FEE_SNAPSHOT_PATH,
    _DCE_PG_FEE_SNAPSHOT_PATH,
)
_SHFE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/",
}
_JIN10_VENDOR_COLUMNS = (
    "日期",
    "合约品种",
    "合约代码",
    "手续费公布时间",
    "价格公布时间",
    "现价",
    "涨停板",
    "跌停板",
    "保证金/买开",
    "保证金/卖开",
    "开仓",
    "平昨",
    "平今",
)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
BUILDER_SCHEMA_VERSION = 72
_COVERAGE_COLUMNS = {
    "exchange_calendar.csv": "exchange_trade_date",
    "contract_specs.csv": "last_trade_date",
    "contract_daily.csv": "exchange_trade_date",
    "fee_margin_schedule.csv": "effective_from",
}


class MetadataBuildError(ValueError):
    """One source row cannot be converted without guessing mechanics."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ReconciledDailyMechanics:
    """One canonical daily row and its exact fee/margin schedule."""

    daily: dict[str, object]
    fee: dict[str, object]


@dataclass(frozen=True)
class EffectiveContractMechanics:
    contract_size: float
    price_tick: float
    source: str
    known_at: datetime


@dataclass(frozen=True)
class HistoricalFeeSnapshot:
    root_symbol: str
    exchange: str
    contract_code: str
    effective_from: date
    effective_to: date | None
    known_at: datetime
    open_fee_expression: str
    close_fee_expression: str
    close_today_fee_expression: str
    source_url: str
    corroborating_url: str
    visibility_url: str
    validation_reference: str
    source_path: Path


@dataclass(frozen=True, order=True)
class ContractDate:
    """Exact local actual contract used by one exchange trade date."""

    root_symbol: str
    exchange: str
    contract_code: str
    trade_date: date


@dataclass(frozen=True)
class ExtensionBuildResult:
    """Canonical extension plus immutable raw-source audit records."""

    frames: dict[str, pd.DataFrame]
    audit_records: tuple[dict[str, object], ...]
    assumptions: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class MetadataPreparationResult:
    """Compact report-safe description of one verified merged cache."""

    metadata_root: Path
    cache_key: str
    cache_hit: bool
    contract_date_pairs: int
    generated_contract_rows: int
    generated_daily_rows: int
    generated_fee_rows: int
    source_queries: int
    source_audit_sha256: str
    manifest_sha256: str
    assumptions: tuple[dict[str, object], ...] = ()

    def to_audit_dict(self) -> dict[str, object]:
        return {
            "status": (
                "READY_WITH_ASSUMPTIONS" if self.assumptions else "READY"
            ),
            "metadata_root": str(self.metadata_root),
            "cache_key": self.cache_key,
            "cache_hit": self.cache_hit,
            "contract_date_pairs": self.contract_date_pairs,
            "generated_contract_rows": self.generated_contract_rows,
            "generated_daily_rows": self.generated_daily_rows,
            "generated_fee_rows": self.generated_fee_rows,
            "source_queries": self.source_queries,
            "source_audit_sha256": self.source_audit_sha256,
            "manifest_sha256": self.manifest_sha256,
            "assumptions": [dict(item) for item in self.assumptions],
        }


class MetadataSourceClient(Protocol):
    @property
    def audit_records(self) -> tuple[dict[str, object], ...]: ...

    def fetch_calendar(self, exchange: str, start: date, end: date) -> pd.DataFrame: ...

    def fetch_contracts(self, exchange: str) -> pd.DataFrame: ...

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame: ...

    def fetch_daily_quote(
        self,
        contract_code: str,
        trade_date: date,
    ) -> pd.DataFrame: ...

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame: ...

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame: ...

    def fetch_czce_settlement_parameters(self, source_date: date) -> pd.DataFrame: ...

    def fetch_shfe_settlement_parameters(self, source_date: date) -> pd.DataFrame: ...

    def fetch_ine_settlement_parameters(self, source_date: date) -> pd.DataFrame: ...


class AuditedVendorMetadataClient:
    """Thin audited clients for historical Tushare and Jin10 source tables."""

    def __init__(self, *, rate_limit: int = 450) -> None:
        if rate_limit <= 0:
            raise ValueError("rate_limit must be positive")
        self._limiter = RateLimiter(rate_limit)
        self._pro: Any | None = None
        self._records: list[dict[str, object]] = []

    @property
    def audit_records(self) -> tuple[dict[str, object], ...]:
        return tuple(self._records)

    def fetch_calendar(self, exchange: str, start: date, end: date) -> pd.DataFrame:
        try:
            self._limiter.acquire()
            frame = _safe_retry(
                self._tushare_pro().trade_cal,
                exchange=exchange,
                start_date=f"{start:%Y%m%d}",
                end_date=f"{end:%Y%m%d}",
                retries=3,
                wait=2.0,
            )
        except Exception as exc:
            raise MetadataBuildError(
                "SOURCE_REQUEST_FAILED",
                f"tushare trade_cal {exchange}: {exc}",
            ) from exc
        result = _require_frame(frame, f"trade_cal {exchange}")
        self._record(
            "tushare_trade_cal",
            f"{exchange}:{start:%Y%m%d}:{end:%Y%m%d}",
            result,
            "https://tushare.pro/document/2?doc_id=26",
        )
        return result

    def fetch_contracts(self, exchange: str) -> pd.DataFrame:
        fields = (
            "ts_code,symbol,exchange,fut_code,list_date,delist_date,per_unit,"
            "quote_unit_desc,trade_time_desc"
        )
        last_error: Exception | None = None
        result = pd.DataFrame()
        for variant in _exchange_variants(exchange):
            try:
                self._limiter.acquire()
                frame = _safe_retry(
                    self._tushare_pro().fut_basic,
                    exchange=variant,
                    fut_type="1",
                    fields=fields,
                    retries=3,
                    wait=2.0,
                )
            except Exception as exc:  # source failure remains fail-closed
                last_error = exc
                continue
            if frame is not None and not frame.empty:
                result = frame.copy()
                break
        if result.empty:
            raise MetadataBuildError(
                "EMPTY_FUT_BASIC",
                f"{exchange}: {last_error or 'no rows'}",
            )
        self._record(
            "tushare_fut_basic",
            exchange,
            result,
            "https://tushare.pro/document/2?doc_id=135",
        )
        return result

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        fields = (
            "ts_code,trade_date,settle,b_hedging_margin_rate,"
            "s_hedging_margin_rate,long_margin_rate,short_margin_rate"
        )
        try:
            self._limiter.acquire()
            frame = _safe_retry(
                self._tushare_pro().fut_settle,
                trade_date=f"{trade_date:%Y%m%d}",
                fields=fields,
                retries=3,
                wait=2.0,
            )
        except Exception as exc:
            raise MetadataBuildError(
                "SOURCE_REQUEST_FAILED",
                f"tushare fut_settle {trade_date}: {exc}",
            ) from exc
        result = _require_frame(frame, f"fut_settle {trade_date}")
        self._record(
            "tushare_fut_settle",
            f"{trade_date:%Y%m%d}",
            result,
            "https://tushare.pro/document/2?doc_id=141",
        )
        return result

    def fetch_daily_quote(
        self,
        contract_code: str,
        trade_date: date,
    ) -> pd.DataFrame:
        fields = "ts_code,trade_date,pre_settle,settle"
        try:
            self._limiter.acquire()
            frame = _safe_retry(
                self._tushare_pro().fut_daily,
                ts_code=contract_code,
                start_date=f"{trade_date:%Y%m%d}",
                end_date=f"{trade_date:%Y%m%d}",
                fields=fields,
                retries=3,
                wait=2.0,
            )
        except Exception as exc:
            raise MetadataBuildError(
                "SOURCE_REQUEST_FAILED",
                f"tushare fut_daily {contract_code} {trade_date}: {exc}",
            ) from exc
        result = _require_frame(
            frame,
            f"fut_daily {contract_code} {trade_date}",
        ).copy()
        result["_source_table"] = "tushare_fut_daily"
        self._record(
            "tushare_fut_daily",
            f"{contract_code}:{trade_date:%Y%m%d}",
            result,
            "https://tushare.pro/document/2?doc_id=138",
        )
        return result

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover - environment dependency
            raise MetadataBuildError("AKSHARE_UNAVAILABLE", str(exc)) from exc

        def fetch_snapshot() -> pd.DataFrame:
            try:
                return ak.futures_comm_js(date=f"{source_date:%Y%m%d}")
            except KeyError as exc:
                if exc.args == ("日期",):
                    return pd.DataFrame(columns=_JIN10_VENDOR_COLUMNS)
                raise

        try:
            self._limiter.acquire()
            frame = _safe_retry(
                fetch_snapshot,
                retries=3,
                wait=2.0,
            )
        except Exception as exc:
            raise MetadataBuildError(
                "SOURCE_REQUEST_FAILED",
                f"jin10 futures_comm_js {source_date}: {exc}",
            ) from exc
        if not isinstance(frame, pd.DataFrame):
            raise MetadataBuildError(
                "INVALID_SOURCE_RESPONSE",
                f"futures_comm_js {source_date}: {type(frame).__name__}",
            )
        result = frame.copy()
        self._record(
            "jin10_futures_comm_js",
            f"{source_date:%Y%m%d}",
            result,
            "https://datacenter.jin10.com/reportType/dc_qihuo_shouxufei",
        )
        return result

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
        try:
            self._limiter.acquire()
            response = _safe_retry(
                requests.get,
                "https://www.gtjaqh.com/pc/calendar",
                params={"date": f"{source_date:%Y%m%d}"},
                timeout=30,
                retries=3,
                wait=2.0,
            )
            response.raise_for_status()
            frame = _parse_gtja_trading_rules_html(response.text, source_date)
        except Exception as exc:
            if isinstance(exc, MetadataBuildError):
                raise
            raise MetadataBuildError(
                "SOURCE_REQUEST_FAILED",
                f"gtja futures_rule {source_date}: {exc}",
            ) from exc
        result = _require_frame(frame, f"futures_rule {source_date}")
        result["生效日期"] = source_date.isoformat()
        self._record(
            "gtja_futures_rule",
            f"{source_date:%Y%m%d}",
            result,
            "https://www.gtjaqh.com/pc/calendar.html",
        )
        return result

    def fetch_czce_settlement_parameters(self, source_date: date) -> pd.DataFrame:
        url = _CZCE_PARAMETER_URL.format(
            year=f"{source_date:%Y}",
            date=f"{source_date:%Y%m%d}",
        )
        try:
            self._limiter.acquire()
            response = _safe_retry(
                requests.get,
                url,
                timeout=30,
                retries=3,
                wait=2.0,
            )
            response.raise_for_status()
            frame = _parse_czce_settlement_parameters_text(
                response.text,
                source_date,
            )
        except Exception as exc:
            if isinstance(exc, MetadataBuildError):
                raise
            raise MetadataBuildError(
                "SOURCE_REQUEST_FAILED",
                f"czce settlement parameters {source_date}: {exc}",
            ) from exc
        result = _require_frame(
            frame,
            f"czce settlement parameters {source_date}",
        )
        self._record(
            "czce_official_settlement_parameters",
            f"{source_date:%Y%m%d}",
            result,
            url,
        )
        return result

    def fetch_shfe_settlement_parameters(self, source_date: date) -> pd.DataFrame:
        url = _SHFE_PARAMETER_URL.format(date=f"{source_date:%Y%m%d}")
        try:
            self._limiter.acquire()
            response = _safe_retry(
                requests.get,
                url,
                headers=_SHFE_HEADERS,
                timeout=30,
                retries=3,
                wait=2.0,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("o_cursor")
        except Exception as exc:
            raise MetadataBuildError(
                "SOURCE_REQUEST_FAILED",
                f"shfe settlement parameters {source_date}: {exc}",
            ) from exc
        if not isinstance(rows, list):
            raise MetadataBuildError(
                "INVALID_SOURCE_RESPONSE",
                f"shfe settlement parameters {source_date}: o_cursor",
            )
        result = _require_frame(
            pd.DataFrame(rows),
            f"shfe settlement parameters {source_date}",
        )
        result["_source_date"] = source_date.isoformat()
        self._record(
            "shfe_official_settlement_parameters",
            f"{source_date:%Y%m%d}",
            result,
            url,
        )
        return result

    def fetch_ine_settlement_parameters(self, source_date: date) -> pd.DataFrame:
        url = _INE_PARAMETER_URL.format(date=f"{source_date:%Y%m%d}")
        try:
            self._limiter.acquire()
            response = _safe_retry(
                requests.get,
                url,
                headers=_SHFE_HEADERS,
                timeout=30,
                retries=3,
                wait=2.0,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("o_cursor")
        except Exception as exc:
            raise MetadataBuildError(
                "SOURCE_REQUEST_FAILED",
                f"ine settlement parameters {source_date}: {exc}",
            ) from exc
        if not isinstance(rows, list):
            raise MetadataBuildError(
                "INVALID_SOURCE_RESPONSE",
                f"ine settlement parameters {source_date}: o_cursor",
            )
        result = _require_frame(
            pd.DataFrame(rows),
            f"ine settlement parameters {source_date}",
        )
        result["_source_date"] = source_date.isoformat()
        self._record(
            "ine_official_settlement_parameters",
            f"{source_date:%Y%m%d}",
            result,
            url,
        )
        return result

    def _tushare_pro(self) -> Any:
        if self._pro is None:
            import os

            token = os.environ.get("TUSHARE_TOKEN", "").strip()
            if not token:
                raise MetadataBuildError("TUSHARE_TOKEN_MISSING", "TUSHARE_TOKEN")
            import tushare as ts

            self._pro = ts.pro_api(token)
        return self._pro

    def _record(
        self,
        kind: str,
        source_key: str,
        frame: pd.DataFrame,
        source_url: str,
    ) -> None:
        rows = _json_rows(frame)
        raw = json.dumps(rows, ensure_ascii=True, sort_keys=True).encode("utf-8")
        self._records.append(
            {
                "kind": kind,
                "source_key": source_key,
                "source_url": source_url,
                "retrieved_at": datetime.now(tz=SHANGHAI_TZ).isoformat(),
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "rows": rows,
            }
        )


def parse_fee_expression(value: object) -> tuple[float, float]:
    """Return ``(notional_rate, cash_per_lot)`` for one explicit fee value."""
    raw = "" if value is None else str(value).strip()
    cash = _CASH_FEE.fullmatch(raw)
    if cash is not None:
        return 0.0, float(cash.group("value"))
    rate = _RATE_FEE.fullmatch(raw)
    if rate is not None:
        return float(rate.group("value")) / 10_000.0, 0.0
    raise MetadataBuildError("INVALID_FEE_EXPRESSION", repr(value))


@cache
def _historical_fee_snapshots() -> tuple[HistoricalFeeSnapshot, ...]:
    required_base = {
        "root_symbol",
        "exchange",
        "contract_code",
        "effective_from",
        "effective_to",
        "known_at",
        "source_url",
        "corroborating_url",
    }
    rate_columns = {
        "open_fee_rate",
        "close_fee_rate",
        "close_today_fee_rate",
    }
    cash_columns = {
        "open_fee_cash",
        "close_fee_cash",
        "close_today_fee_cash",
    }
    snapshots: list[HistoricalFeeSnapshot] = []
    for source_path in _DCE_HISTORICAL_FEE_SNAPSHOT_PATHS:
        try:
            frame = pd.read_csv(source_path)
        except (OSError, pd.errors.ParserError) as exc:
            raise MetadataBuildError(
                "INVALID_HISTORICAL_FEE_SNAPSHOT",
                f"{source_path}: {exc}",
            ) from exc
        missing = required_base.difference(frame.columns)
        has_rates = rate_columns.issubset(frame.columns)
        has_cash = cash_columns.issubset(frame.columns)
        if missing or frame.empty or has_rates == has_cash:
            raise MetadataBuildError(
                "INVALID_HISTORICAL_FEE_SNAPSHOT",
                f"{source_path}: missing={sorted(missing)} rows={len(frame)} "
                f"has_rates={has_rates} has_cash={has_cash}",
            )

        def fee_expression(row: Mapping[str, object], field: str) -> str:
            if has_rates:
                rate = _rate_float(
                    row[f"{field}_fee_rate"],
                    f"HISTORICAL_{field.upper()}_FEE_RATE",
                )
                return f"{rate * 10_000:g}/万分之"
            cash = _positive_float(
                row[f"{field}_fee_cash"],
                f"HISTORICAL_{field.upper()}_FEE_CASH",
            )
            return f"{cash:g}元"

        for row in frame.to_dict(orient="records"):
            known_at = pd.Timestamp(row["known_at"])
            if known_at.tzinfo is None:
                raise MetadataBuildError(
                    "NAIVE_HISTORICAL_FEE_KNOWN_AT",
                    str(row["contract_code"]),
                )
            effective_to_raw = row["effective_to"]
            effective_to = (
                None
                if pd.isna(effective_to_raw) or not str(effective_to_raw).strip()
                else _parse_date(
                    effective_to_raw,
                    "HISTORICAL_FEE_EFFECTIVE_TO",
                )
            )
            visibility_url = (
                str(row.get("visibility_url", "")).strip()
                if not pd.isna(row.get("visibility_url", ""))
                else ""
            )
            validation_reference = (
                str(row.get("validation_reference", "")).strip()
                if not pd.isna(row.get("validation_reference", ""))
                else ""
            )
            snapshot = HistoricalFeeSnapshot(
                root_symbol=str(row["root_symbol"]).strip().upper(),
                exchange=str(row["exchange"]).strip().upper(),
                contract_code=str(row["contract_code"]).strip().upper(),
                effective_from=_parse_date(
                    row["effective_from"],
                    "HISTORICAL_FEE_EFFECTIVE_FROM",
                ),
                effective_to=effective_to,
                known_at=known_at.tz_convert(SHANGHAI_TZ).to_pydatetime(),
                open_fee_expression=fee_expression(row, "open"),
                close_fee_expression=fee_expression(row, "close"),
                close_today_fee_expression=fee_expression(row, "close_today"),
                source_url=str(row["source_url"]).strip(),
                corroborating_url=str(row["corroborating_url"]).strip(),
                visibility_url=visibility_url,
                validation_reference=validation_reference,
                source_path=source_path,
            )
            if not snapshot.source_url or not snapshot.corroborating_url:
                raise MetadataBuildError(
                    "INVALID_HISTORICAL_FEE_SOURCE",
                    snapshot.contract_code,
                )
            if (
                snapshot.effective_to is not None
                and snapshot.effective_to < snapshot.effective_from
            ):
                raise MetadataBuildError(
                    "INVALID_HISTORICAL_FEE_INTERVAL",
                    snapshot.contract_code,
                )
            snapshots.append(snapshot)
    return tuple(snapshots)


def _dce_historical_fee_vendor_row(
    *,
    local_contract: str,
    root_symbol: str,
    exchange: str,
    trade_date: date,
    vendor_source_date: date,
    session_open: datetime,
    prior_settlement_row: Mapping[str, object],
    current_trade_rule_row: Mapping[str, object] | None,
    price_tick: float,
) -> dict[str, object] | None:
    if (
        _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper()) != "DCE"
        or current_trade_rule_row is None
    ):
        return None
    normalized_contract = local_contract.strip().upper()
    matching = [
        snapshot
        for snapshot in _historical_fee_snapshots()
        if snapshot.root_symbol == root_symbol.upper()
        and snapshot.exchange == "DCE"
        and snapshot.contract_code == normalized_contract
        and snapshot.effective_from <= trade_date
        and (snapshot.effective_to is None or trade_date <= snapshot.effective_to)
        and snapshot.known_at <= session_open
    ]
    if len(matching) != 1:
        return None
    snapshot = matching[0]
    prior_settlement = _positive_float(
        prior_settlement_row.get("settle"),
        "PRIOR_SETTLEMENT",
    )
    limit_rate = _trade_rule_limit_rate(
        current_trade_rule_row,
        local_contract=local_contract,
        root_symbol=root_symbol,
        exchange=exchange,
        effective_on=trade_date,
        price_tick=price_tick,
    )
    amplitude = _floor_tick(prior_settlement * limit_rate, price_tick)
    margin_long = _rate_float(
        prior_settlement_row.get("long_margin_rate"),
        "LONG_MARGIN",
    )
    margin_short = _rate_float(
        prior_settlement_row.get("short_margin_rate"),
        "SHORT_MARGIN",
    )
    source_hash = sha256_file(snapshot.source_path)
    source_urls = "|".join(
        value
        for value in (
            snapshot.source_url,
            snapshot.corroborating_url,
            snapshot.visibility_url,
            snapshot.validation_reference,
        )
        if value
    )
    source_reference = (
        f"{snapshot.source_path.resolve()}#sha256={source_hash}|{source_urls}"
    )

    return {
        "日期": f"{vendor_source_date:%Y%m%d}",
        "合约品种": "纤维板",
        "合约代码": normalized_contract.split(".", maxsplit=1)[0],
        "手续费公布时间": snapshot.known_at.isoformat(),
        "价格公布时间": datetime.combine(
            vendor_source_date,
            time(15, 30),
            tzinfo=SHANGHAI_TZ,
        ).isoformat(),
        "现价": prior_settlement,
        "涨停板": prior_settlement + amplitude,
        "跌停板": prior_settlement - amplitude,
        "保证金/买开": f"{margin_long * 100:g}%",
        "保证金/卖开": f"{margin_short * 100:g}%",
        "开仓": snapshot.open_fee_expression,
        "平昨": snapshot.close_fee_expression,
        "平今": snapshot.close_today_fee_expression,
        "_historical_fee_mirror": True,
        "_historical_fee_source_reference": source_reference,
        "_historical_fee_snapshot_path": str(snapshot.source_path),
    }


def _dce_target_close_roll_fee_vendor_row(
    *,
    local_contract: str,
    root_symbol: str,
    exchange: str,
    trade_date: date,
    vendor_source_date: date,
    session_open: datetime,
    current_settlement_row: Mapping[str, object],
    prior_settlement_row: Mapping[str, object],
    prior_vendor_frame: pd.DataFrame,
    target_vendor_frame: pd.DataFrame,
    current_trade_rule_row: Mapping[str, object] | None,
    price_tick: float,
    allow_runtime_defaults: bool = False,
) -> dict[str, object] | None:
    if (
        _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper()) != "DCE"
        or current_trade_rule_row is None
    ):
        return None
    try:
        late_row = _exact_contract_row(
            local_contract,
            target_vendor_frame,
            code_column="合约代码",
        )
    except MetadataBuildError as exc:
        if exc.code == "MISSING_CONTRACT":
            return None
        raise
    _require_source_identity(
        local_contract,
        trade_date,
        late_row,
        code_key="合约代码",
        date_key="日期",
        source="JIN10_TARGET_CLOSE_ROLL_VALIDATION",
    )
    current_settlement = _positive_float(
        current_settlement_row.get("settle"),
        "CURRENT_SETTLEMENT",
    )
    late_settlement = _positive_float(
        late_row.get("现价"),
        "LATE_VENDOR_SETTLEMENT",
    )
    if abs(current_settlement - late_settlement) > price_tick / 2.0 + 1e-12:
        raise MetadataBuildError(
            "TARGET_CLOSE_ROLL_SETTLEMENT_MISMATCH",
            f"{local_contract} {trade_date}: tushare={current_settlement} "
            f"jin10={late_settlement}",
        )
    assumed_reason: str | None = None
    try:
        fee_known_at, reference = _validated_historical_roll_fee_reference(
            local_contract=local_contract,
            source_date=trade_date,
            current_frame=target_vendor_frame,
            historical_source_date=vendor_source_date,
            historical_frame=prior_vendor_frame,
            session_open=session_open,
        )
    except MetadataBuildError as exc:
        if not allow_runtime_defaults or exc.code != "MISSING_ROLL_FEE_REFERENCE":
            raise
        fee_known_at = datetime.combine(
            vendor_source_date,
            time(15, 30),
            tzinfo=SHANGHAI_TZ,
        )
        reference = (
            f"{local_contract.split('.', maxsplit=1)[0].lower()}"
            f"@{trade_date:%Y%m%d}:runtime_default"
        )
        reference_row = late_row
        assumed_reason = str(exc)
    else:
        reference_contract = reference.split("@", maxsplit=1)[0]
        reference_row = _exact_contract_row(
            reference_contract,
            prior_vendor_frame,
            code_column="合约代码",
        )
    prior_settlement = _positive_float(
        prior_settlement_row.get("settle"),
        "PRIOR_SETTLEMENT",
    )
    limit_rate = _trade_rule_limit_rate(
        current_trade_rule_row,
        local_contract=local_contract,
        root_symbol=root_symbol,
        exchange=exchange,
        effective_on=trade_date,
        price_tick=price_tick,
    )
    amplitude = _floor_tick(prior_settlement * limit_rate, price_tick)
    margin_long = _rate_float(
        prior_settlement_row.get("long_margin_rate"),
        "LONG_MARGIN",
    )
    margin_short = _rate_float(
        prior_settlement_row.get("short_margin_rate"),
        "SHORT_MARGIN",
    )
    source_reference = (
        f"jin10:futures_comm:{trade_date:%Y%m%d}:"
        f"assumed_runtime_default:{local_contract}"
        if assumed_reason is not None
        else (
            f"jin10:futures_comm:{vendor_source_date:%Y%m%d}:"
            f"root_fee_reference:{reference}"
            f"|jin10:futures_comm:{trade_date:%Y%m%d}:"
            f"late_contract_validation:{local_contract}"
        )
    )
    row = {
        "日期": f"{vendor_source_date:%Y%m%d}",
        "合约品种": root_symbol,
        "合约代码": local_contract.split(".", maxsplit=1)[0],
        "手续费公布时间": fee_known_at.isoformat(),
        "价格公布时间": datetime.combine(
            vendor_source_date,
            time(15, 30),
            tzinfo=SHANGHAI_TZ,
        ).isoformat(),
        "现价": prior_settlement,
        "涨停板": prior_settlement + amplitude,
        "跌停板": prior_settlement - amplitude,
        "保证金/买开": f"{margin_long * 100:g}%",
        "保证金/卖开": f"{margin_short * 100:g}%",
        "开仓": reference_row.get("开仓"),
        "平昨": reference_row.get("平昨"),
        "平今": reference_row.get("平今"),
        "_dce_target_close_roll_fee_validation": True,
        "_dce_target_close_roll_source_reference": source_reference,
    }
    if assumed_reason is not None:
        row["_assumed_runtime_default_reason"] = assumed_reason
    return row


def _historical_fee_snapshot_audit_record(
    source_path: Path,
) -> dict[str, object]:
    if source_path not in _DCE_HISTORICAL_FEE_SNAPSHOT_PATHS:
        raise MetadataBuildError(
            "INVALID_HISTORICAL_FEE_SNAPSHOT",
            str(source_path),
        )
    frame = pd.read_csv(source_path)
    rows = _json_rows(frame)
    raw = json.dumps(rows, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return {
        "kind": "checked_in_historical_fee_snapshot",
        "source_key": source_path.name,
        "source_url": "|".join(
            sorted(
                {
                    snapshot.source_url
                    for snapshot in _historical_fee_snapshots()
                    if snapshot.source_path == source_path
                }
                | {
                    snapshot.corroborating_url
                    for snapshot in _historical_fee_snapshots()
                    if snapshot.source_path == source_path
                }
                | {
                    snapshot.visibility_url
                    for snapshot in _historical_fee_snapshots()
                    if snapshot.source_path == source_path
                    and snapshot.visibility_url
                }
            )
        ),
        "retrieved_at": datetime.now(tz=SHANGHAI_TZ).isoformat(),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "file_sha256": sha256_file(source_path),
        "rows": rows,
    }


def parse_price_tick(value: object) -> float:
    """Parse the positive leading number in a published quote-unit description."""
    raw = "" if value is None else str(value)
    match = _LEADING_NUMBER.match(raw)
    if match is None:
        raise MetadataBuildError("INVALID_PRICE_TICK", repr(value))
    parsed = float(match.group("value"))
    if not math.isfinite(parsed) or parsed <= 0:
        raise MetadataBuildError("INVALID_PRICE_TICK", repr(value))
    return parsed


def match_vendor_contract(
    local_contract: str,
    vendor_codes: Sequence[str],
) -> str:
    """Match a local actual contract to a full or CZCE-style short vendor code."""
    local_identity = str(local_contract).strip().upper()
    first_code_by_identity: dict[str, str] = {}
    for candidate in vendor_codes:
        rendered = str(candidate).strip()
        first_code_by_identity.setdefault(rendered.upper(), rendered)
    rendered_codes = list(first_code_by_identity.values())
    exact_matches = [
        candidate
        for candidate in rendered_codes
        if candidate.upper() == local_identity
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise MetadataBuildError(
            "AMBIGUOUS_CONTRACT",
            f"{local_contract}: {exact_matches}",
        )

    local_code = local_identity.split(".", maxsplit=1)[0]
    local_match = _CONTRACT.fullmatch(local_code)
    if local_match is None:
        raise MetadataBuildError("INVALID_LOCAL_CONTRACT", local_contract)

    matches: list[str] = []
    for rendered in rendered_codes:
        candidate_code = rendered.split(".", maxsplit=1)[0].upper()
        candidate_match = _CONTRACT.fullmatch(candidate_code)
        if candidate_match is None:
            continue
        same_root = candidate_match.group("root") == local_match.group("root")
        local_delivery = local_match.group("delivery")
        vendor_delivery = candidate_match.group("delivery")
        same_delivery = (
            vendor_delivery == local_delivery
            or vendor_delivery == local_delivery[-3:]
        )
        if same_root and same_delivery:
            matches.append(rendered)
    if len(matches) != 1:
        code = "AMBIGUOUS_CONTRACT" if len(matches) > 1 else "MISSING_CONTRACT"
        raise MetadataBuildError(code, f"{local_contract}: {matches}")
    return matches[0]


def session_template_from_description(
    exchange: str,
    description: object,
    *,
    root_symbol: str | None = None,
    effective_on: date | None = None,
) -> str:
    """Map only published commodity session descriptions we can model exactly."""
    raw = "" if description is None else str(description).strip()
    normalized = (
        raw.replace("：", ":")
        .replace(" ", "")
        .replace("～", "-")
        .replace("－", "-")
    )
    has_morning_session = "9:00" in normalized and "11:30" in normalized
    has_afternoon_session = re.search(
        r"(?:下午)?(?:13:30|1:30)(?:至|-)(?:下午)?(?:15:00|3:00)",
        normalized,
    )
    if not has_morning_session or has_afternoon_session is None:
        raise MetadataBuildError(
            "UNSUPPORTED_SESSION_TEMPLATE",
            f"{exchange}: {description!r}",
        )
    exchange_token = str(exchange).strip().upper()
    normalized_exchange = _EXCHANGE_ALIASES.get(exchange_token, exchange_token)
    normalized_root = str(root_symbol or "").strip().upper()
    if (
        normalized_exchange == "CZCE"
        and _CZCE_GENERIC_OTHER_HOURS in normalized
    ):
        if effective_on is None or effective_on.year != 2026:
            raise MetadataBuildError(
                "UNSUPPORTED_SESSION_TEMPLATE",
                f"{exchange}.{normalized_root or 'UNKNOWN'} "
                f"effective_on={effective_on}: {description!r}",
            )
        if normalized_root in _CZCE_NIGHT_2300_ROOTS_2026:
            return "CN_COMMODITY_NIGHT_2300"
        if normalized_root in _CZCE_DAY_ROOTS_2026:
            return "CN_COMMODITY_DAY"
        raise MetadataBuildError(
            "UNSUPPORTED_SESSION_TEMPLATE",
            f"{exchange}.{normalized_root or 'UNKNOWN'}: {description!r}",
        )
    if "夜盘" not in normalized:
        return "CN_COMMODITY_DAY"
    night_range = re.search(
        r"21:00(?:至|-)(?:次日)?(?P<end>23:00|0?1:00|0?2:30)",
        normalized,
    )
    if night_range is not None:
        ending = night_range.group("end").lstrip("0")
        return {
            "23:00": "CN_COMMODITY_NIGHT_2300",
            "1:00": "CN_COMMODITY_NIGHT_0100",
            "2:30": "CN_COMMODITY_NIGHT_0230",
        }[ending]
    raise MetadataBuildError(
        "UNSUPPORTED_SESSION_TEMPLATE",
        f"{exchange}: {description!r}",
    )


def reconcile_daily_mechanics(
    *,
    local_contract: str,
    root_symbol: str,
    exchange: str,
    trade_date: date,
    prior_open_date: date,
    price_tick: float,
    current_settlement_row: Mapping[str, object],
    prior_settlement_row: Mapping[str, object],
    vendor_row: Mapping[str, object],
    vendor_source_date: date | None = None,
    prior_trade_rule_row: Mapping[str, object] | None = None,
    current_trade_rule_row: Mapping[str, object] | None = None,
    prior_shfe_parameter_row: Mapping[str, object] | None = None,
    current_shfe_parameter_row: Mapping[str, object] | None = None,
    session_open: datetime,
    price_known_at: datetime | None = None,
    fee_known_at: datetime | None = None,
    roll_entry_validation: bool = False,
    fee_reference_contract: str | None = None,
    shfe_official_vendor_validation: bool = False,
    shfe_late_limit_validation_date: date | None = None,
) -> ReconciledDailyMechanics:
    """Reconcile one session's mechanics without cross-field substitution."""
    if session_open.tzinfo is None:
        raise MetadataBuildError("NAIVE_SESSION_OPEN", session_open.isoformat())
    if not math.isfinite(price_tick) or price_tick <= 0:
        raise MetadataBuildError("INVALID_PRICE_TICK", repr(price_tick))
    price_known = _visible_source_time(
        price_known_at or session_open,
        session_open,
        "PRICE_PARAMETERS",
    )
    fee_known = _visible_source_time(
        fee_known_at or session_open,
        session_open,
        "FEE_PARAMETERS",
    )

    _require_source_identity(
        local_contract,
        trade_date,
        current_settlement_row,
        code_key="ts_code",
        date_key="trade_date",
        source="TUSHARE_CURRENT_SETTLEMENT",
    )
    return _finish_daily_reconciliation(
        local_contract=local_contract,
        root_symbol=root_symbol,
        exchange=exchange,
        trade_date=trade_date,
        prior_open_date=prior_open_date,
        price_tick=price_tick,
        current_settlement_row=current_settlement_row,
        prior_settlement_row=prior_settlement_row,
        vendor_row=vendor_row,
        vendor_source_date=vendor_source_date or prior_open_date,
        prior_trade_rule_row=prior_trade_rule_row,
        current_trade_rule_row=current_trade_rule_row,
        prior_shfe_parameter_row=prior_shfe_parameter_row,
        current_shfe_parameter_row=current_shfe_parameter_row,
        session_open=session_open,
        price_known_at=price_known,
        fee_known_at=fee_known,
        roll_entry_validation=roll_entry_validation,
        fee_reference_contract=fee_reference_contract,
        shfe_official_vendor_validation=shfe_official_vendor_validation,
        shfe_late_limit_validation_date=shfe_late_limit_validation_date,
    )


def collect_contract_dates(
    symbols: Sequence[DiscoveredSymbol],
    *,
    start: date,
    end: date,
) -> tuple[ContractDate, ...]:
    """Read one exact contract identity from every selected local partition."""
    if end < start:
        raise ValueError("end precedes start")
    pairs: set[ContractDate] = set()
    identities: dict[tuple[str, str, date], str] = {}
    partitions: dict[
        tuple[str, str],
        list[tuple[date, str, bool, Path, Path]],
    ] = {}
    for symbol in symbols:
        for path in sorted(symbol.source_directory.glob("*.parquet")):
            partition_date = _partition_date(path)
            if partition_date is None or not start <= partition_date <= end:
                continue
            contract_code = _partition_contract(path)
            root, exchange = _contract_identity(contract_code)
            if root != symbol.root_symbol or exchange != symbol.exchange:
                raise MetadataBuildError(
                    "PARTITION_IDENTITY_MISMATCH",
                    f"{path}: expected={symbol.root_symbol}.{symbol.exchange} "
                    f"observed={root}.{exchange}",
                )
            key = (root, exchange, partition_date)
            existing = identities.get(key)
            if existing is not None and existing != contract_code:
                raise MetadataBuildError(
                    "AMBIGUOUS_PARTITION_CONTRACT",
                    f"{root}.{exchange} {partition_date}: {existing},{contract_code}",
                )
            identities[key] = contract_code
            partitions.setdefault((root, exchange), []).append(
                (
                    partition_date,
                    contract_code,
                    _partition_has_night_bars(path),
                    path,
                    symbol.source_directory,
                )
            )
            pairs.add(
                ContractDate(
                    root_symbol=root,
                    exchange=exchange,
                    contract_code=contract_code,
                    trade_date=partition_date,
                )
            )
    for (root, exchange), records in partitions.items():
        ordered = sorted(records)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            _, old_contract, old_has_night, _, source_directory = previous
            current_date, new_contract, _, current_path, _ = current
            include_old_contract = old_has_night
            if not include_old_contract and old_contract != new_contract:
                include_old_contract = _roll_contract_overlaps_partition(
                    source_directory=source_directory,
                    root_symbol=root,
                    contract_code=old_contract,
                    partition_path=current_path,
                    trade_date=current_date,
                )
            if include_old_contract and old_contract != new_contract:
                pairs.add(
                    ContractDate(
                        root_symbol=root,
                        exchange=exchange,
                        contract_code=old_contract,
                        trade_date=current_date,
                    )
                )
    missing = sorted(
        f"{symbol.root_symbol}.{symbol.exchange}"
        for symbol in symbols
        if not any(
            pair.root_symbol == symbol.root_symbol
            and pair.exchange == symbol.exchange
            for pair in pairs
        )
    )
    if missing:
        raise MetadataBuildError(
            "MISSING_LOCAL_PARTITIONS",
            f"{start}..{end}: {','.join(missing)}",
        )
    return tuple(sorted(pairs))


def prepare_execution_metadata(
    *,
    symbols: Sequence[DiscoveredSymbol],
    start: date,
    end: date,
    base_root: str | Path,
    cache_root: str | Path,
    source_client: MetadataSourceClient | None = None,
    rate_limit: int = 450,
    allow_runtime_defaults: bool = False,
) -> MetadataPreparationResult:
    """Publish or reuse a verified merged bundle for the exact local universe."""
    pairs = collect_contract_dates(symbols, start=start, end=end)
    base_path = Path(base_root)
    base = MetadataBundle.load(base_path)
    key_payload = {
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "base_manifest_sha256": sha256_file(base_path / "manifest.json"),
        "historical_fee_snapshot_sha256": {
            path.name: sha256_file(path)
            for path in _DCE_HISTORICAL_FEE_SNAPSHOT_PATHS
        },
        "start": start.isoformat(),
        "end": end.isoformat(),
        "allow_runtime_defaults": bool(allow_runtime_defaults),
        "pairs": [
            {
                "root_symbol": pair.root_symbol,
                "exchange": pair.exchange,
                "contract_code": pair.contract_code,
                "trade_date": pair.trade_date.isoformat(),
            }
            for pair in pairs
        ],
    }
    cache_key = hashlib.sha256(
        json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    cache = Path(cache_root)
    target = cache / cache_key
    if target.is_dir():
        verify_manifest(target)
        return _cached_result(target, cache_key, cache_hit=True)

    client = source_client or AuditedVendorMetadataClient(rate_limit=rate_limit)
    extension = build_extension_frames(
        pairs=pairs,
        base_frames=base.frames,
        start=start,
        end=end,
        source_client=client,
        allow_runtime_defaults=allow_runtime_defaults,
    )
    merged = merge_canonical_frames(
        base.frames,
        extension.frames,
        start=start,
        end=end,
    )
    cache.mkdir(parents=True, exist_ok=True)
    temporary_parent = Path(
        tempfile.mkdtemp(prefix=".cycle_v1_metadata_", dir=cache)
    )
    staging = temporary_parent / "staging"
    output = temporary_parent / "bundle"
    staging.mkdir()
    downloaded_at = datetime.now(tz=SHANGHAI_TZ)
    try:
        sources: dict[str, StagingSource] = {}
        for filename in CANONICAL_FILENAMES:
            path = staging / filename
            merged[filename].to_csv(path, index=False)
            coverage = pd.to_datetime(
                merged[filename][_COVERAGE_COLUMNS[filename]],
                errors="raise",
            )
            sources[filename] = StagingSource(
                path=path,
                source_url_or_file=(
                    f"{base_path.resolve()}|audited_tushare_jin10_extension"
                ),
                downloaded_at=downloaded_at,
                coverage_start=coverage.min().date().isoformat(),
                coverage_end=coverage.max().date().isoformat(),
                sha256=sha256_file(path),
            )
        manifest = import_metadata_bundle(sources, output)
        audit_path = output / "vendor_source_audit.jsonl.gz"
        _write_source_audit(extension.audit_records, audit_path)
        manifest["source_audit"] = {
            "path": audit_path.name,
            "sha256": sha256_file(audit_path),
            "source_queries": len(extension.audit_records),
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        summary = {
            "cache_key": cache_key,
            "contract_date_pairs": len(pairs),
            "generated_contract_rows": len(extension.frames["contract_specs.csv"]),
            "generated_daily_rows": len(extension.frames["contract_daily.csv"]),
            "generated_fee_rows": len(extension.frames["fee_margin_schedule.csv"]),
            "source_queries": len(extension.audit_records),
            "source_audit_sha256": sha256_file(audit_path),
            "manifest_sha256": sha256_file(manifest_path),
            "assumptions": [dict(item) for item in extension.assumptions],
        }
        (output / "build_summary.json").write_text(
            json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        verify_manifest(output)
        if target.exists():
            verify_manifest(target)
        else:
            output.replace(target)
    finally:
        shutil.rmtree(temporary_parent, ignore_errors=True)
    return _cached_result(target, cache_key, cache_hit=False)


def build_extension_frames(
    *,
    pairs: Sequence[ContractDate],
    base_frames: Mapping[str, pd.DataFrame],
    start: date,
    end: date,
    source_client: MetadataSourceClient,
    allow_runtime_defaults: bool = False,
) -> ExtensionBuildResult:
    """Build source-reconciled rows absent from the immutable base bundle."""
    if not pairs:
        raise MetadataBuildError("EMPTY_CONTRACT_DATE_SET", f"{start}..{end}")
    if end < start:
        raise ValueError("end precedes start")
    frames = {
        filename: pd.DataFrame(columns=columns)
        for filename, columns in CANONICAL_SCHEMAS.items()
        }
    base_contracts = _key_set(base_frames, "contract_specs.csv", ("contract_code",))
    base_daily_frame = base_frames.get("contract_daily.csv", pd.DataFrame())
    complete_base_daily = base_daily_frame.loc[
        point_in_time_daily_mask(base_daily_frame)
    ]
    base_daily = _key_set(
        {"contract_daily.csv": complete_base_daily},
        "contract_daily.csv",
        ("contract_code", "exchange_trade_date"),
        date_columns={"exchange_trade_date"},
    )
    contract_rows: list[dict[str, object]] = []
    templates: dict[str, str] = {}
    contract_sources: dict[str, pd.DataFrame] = {}
    unique_pairs = tuple(sorted(set(pairs)))
    roll_entry_pairs: set[tuple[str, date]] = set()
    post_roll_pairs: set[tuple[str, date]] = set()
    pairs_by_identity: dict[tuple[str, str], list[ContractDate]] = {}
    for pair in unique_pairs:
        identity = (pair.root_symbol, pair.exchange)
        pairs_by_identity.setdefault(identity, []).append(pair)
    for identity_pairs in pairs_by_identity.values():
        ordered_pairs = sorted(identity_pairs, key=lambda item: item.trade_date)
        for index, pair in enumerate(ordered_pairs[1:], start=1):
            prior_pair = ordered_pairs[index - 1]
            if prior_pair.contract_code == pair.contract_code:
                continue
            roll_entry_pairs.add((pair.contract_code, pair.trade_date))
            if index + 1 < len(ordered_pairs):
                next_pair = ordered_pairs[index + 1]
                if next_pair.contract_code == pair.contract_code:
                    post_roll_pairs.add(
                        (next_pair.contract_code, next_pair.trade_date)
                    )
    for pair in unique_pairs:
        if pair.contract_code in templates:
            continue
        if (pair.contract_code,) in base_contracts:
            base = base_frames["contract_specs.csv"]
            selected = base.loc[
                base["contract_code"].astype(str).eq(pair.contract_code)
            ]
            if len(selected) != 1:
                raise MetadataBuildError(
                    "AMBIGUOUS_BASE_CONTRACT",
                    pair.contract_code,
                )
            templates[pair.contract_code] = str(
                selected.iloc[0]["session_template_id"]
            )
            continue
        contracts = contract_sources.get(pair.exchange)
        if contracts is None:
            contracts = source_client.fetch_contracts(pair.exchange)
            contract_sources[pair.exchange] = contracts
        source_row = _exact_contract_row(pair.contract_code, contracts)
        template = session_template_from_description(
            pair.exchange,
            source_row.get("trade_time_desc"),
            root_symbol=pair.root_symbol,
            effective_on=pair.trade_date,
        )
        templates[pair.contract_code] = template
        contract_rows.append(_contract_spec_row(pair, source_row, template))

    generated_specs = {
        str(row["contract_code"]): row for row in contract_rows
    }
    base_specs_frame = base_frames.get("contract_specs.csv", pd.DataFrame())
    for pair in unique_pairs:
        spec_row: Mapping[str, object]
        if pair.contract_code in generated_specs:
            spec_row = generated_specs[pair.contract_code]
        else:
            selected_spec = base_specs_frame.loc[
                base_specs_frame["contract_code"].astype(str).eq(pair.contract_code)
            ]
            if len(selected_spec) != 1:
                raise MetadataBuildError(
                    "AMBIGUOUS_BASE_CONTRACT",
                    pair.contract_code,
                )
            spec_row = selected_spec.iloc[0].to_dict()
        listed_on = pd.Timestamp(spec_row["known_at"]).date()
        last_trade_date = pd.Timestamp(spec_row["last_trade_date"]).date()
        if not listed_on <= pair.trade_date <= last_trade_date:
            raise MetadataBuildError(
                "CONTRACT_LIFECYCLE_MISMATCH",
                f"{pair.contract_code} {listed_on}..{last_trade_date} "
                f"vs {pair.trade_date}",
            )

    calendar_rows: list[dict[str, object]] = []
    calendars: dict[str, pd.DataFrame] = {}
    exchanges = sorted({pair.exchange for pair in unique_pairs})
    for exchange in exchanges:
        raw_calendar = source_client.fetch_calendar(
            exchange,
            start - timedelta(days=14),
            end + timedelta(days=14),
        )
        calendar = _normalize_open_calendar(raw_calendar, exchange)
        calendars[exchange] = calendar
        selected = calendar.loc[
            calendar["trade_date"].map(lambda value: start <= value <= end)
        ]
        if selected.empty:
            raise MetadataBuildError(
                "MISSING_EXCHANGE_CALENDAR",
                f"{exchange} {start}..{end}",
            )
        exchange_has_night = any(
            "NIGHT" in templates[pair.contract_code]
            for pair in unique_pairs
            if pair.exchange == exchange
        )
        for position, row in selected.iterrows():
            trade_date = row["trade_date"]
            next_open = _next_open_date(calendar, int(position))
            prior_open = row["prior_open_date"]
            known_at = datetime.combine(
                prior_open,
                time(15, 30),
                tzinfo=SHANGHAI_TZ,
            )
            calendar_rows.append(
                {
                    "exchange": exchange,
                    "exchange_trade_date": trade_date.isoformat(),
                    "is_open": 1,
                    "prior_open_date": prior_open.isoformat(),
                    "next_open_date": next_open.isoformat(),
                    "night_session_start": _night_session_start_for_trade_date(
                        trade_date=trade_date,
                        prior_open_date=prior_open,
                        exchange_has_night=exchange_has_night,
                    ),
                    "source": f"{exchange}_CALENDAR_VIA_TUSHARE_TRADE_CAL",
                    "known_at": known_at.isoformat(),
                }
            )

    required_daily = [
        pair
        for pair in unique_pairs
        if (pair.contract_code, pair.trade_date) not in base_daily
    ]
    settlement_sources: dict[date, pd.DataFrame] = {}
    vendor_sources: dict[date, pd.DataFrame] = {}
    trading_rule_sources: dict[date, pd.DataFrame] = {}
    czce_parameter_sources: dict[date, pd.DataFrame] = {}
    shfe_parameter_sources: dict[date, pd.DataFrame] = {}
    ine_parameter_sources: dict[date, pd.DataFrame] = {}
    missing_trading_rule_sources: set[date] = set()
    daily_rows: list[dict[str, object]] = []
    fee_rows: list[dict[str, object]] = []
    checked_in_audit_records: dict[str, dict[str, object]] = {}
    assumptions: list[dict[str, object]] = []
    for pair in required_daily:
        calendar = calendars[pair.exchange]
        calendar_row = calendar.loc[calendar["trade_date"].eq(pair.trade_date)]
        if len(calendar_row) != 1:
            raise MetadataBuildError(
                "MISSING_EXCHANGE_CALENDAR",
                f"{pair.exchange} {pair.trade_date}",
            )
        prior_open = calendar_row.iloc[0]["prior_open_date"]
        current_settlements = settlement_sources.get(pair.trade_date)
        if current_settlements is None:
            current_settlements = source_client.fetch_settlements(pair.trade_date)
            settlement_sources[pair.trade_date] = current_settlements
        prior_settlements = settlement_sources.get(prior_open)
        if prior_settlements is None:
            prior_settlements = source_client.fetch_settlements(prior_open)
            settlement_sources[prior_open] = prior_settlements
        current_row = _exact_settlement_row(
            source_client=source_client,
            contract_code=pair.contract_code,
            trade_date=pair.trade_date,
            settlements=current_settlements,
            context="current_settlement",
        )
        prior_row = _exact_settlement_row(
            source_client=source_client,
            contract_code=pair.contract_code,
            trade_date=prior_open,
            settlements=prior_settlements,
            context="prior_settlement",
        )
        session_open = _session_open(
            pair.trade_date,
            prior_open,
            templates[pair.contract_code],
        )
        generated_spec = generated_specs.get(pair.contract_code)
        if generated_spec is None:
            selected_spec = base_specs_frame.loc[
                base_specs_frame["contract_code"].astype(str).eq(
                    pair.contract_code
                )
            ]
            if len(selected_spec) != 1:
                raise MetadataBuildError(
                    "AMBIGUOUS_BASE_CONTRACT",
                    pair.contract_code,
                )
            generated_spec = selected_spec.iloc[0].to_dict()
        source_known_at = pd.Timestamp(generated_spec["known_at"])
        if source_known_at.tzinfo is None:
            raise MetadataBuildError(
                "NAIVE_CONTRACT_MECHANICS_KNOWN_AT",
                pair.contract_code,
            )
        pair_mechanics = _effective_contract_mechanics(
            root_symbol=pair.root_symbol,
            exchange=pair.exchange,
            effective_on=pair.trade_date,
            source_contract_size=_positive_float(
                generated_spec["contract_size"],
                "CONTRACT_SIZE",
            ),
            source_price_tick=_positive_float(
                generated_spec["price_tick"],
                "PRICE_TICK",
            ),
            source=str(generated_spec["source"]),
            source_known_at=source_known_at.tz_convert(
                SHANGHAI_TZ
            ).to_pydatetime(),
        )
        price_tick = pair_mechanics.price_tick
        exchange_token = _EXCHANGE_ALIASES.get(pair.exchange, pair.exchange)
        official_parameter_cache = {
            "INE": ine_parameter_sources,
            "SHFE": shfe_parameter_sources,
        }.get(exchange_token)
        vendor_source_date = prior_open
        late_vendor_validation_row: dict[str, object] | None = None
        roll_entry_validation = False
        fee_reference_contract: str | None = None
        shfe_official_vendor_validation = False
        official_vendor_rule_row: dict[str, object] | None = None
        official_vendor_parameter_row: dict[str, object] | None = None
        shfe_late_limit_validation_date: date | None = None
        preloaded_trade_rule_row: dict[str, object] | None = None
        if exchange_token not in {"INE", "SHFE"}:
            fetch_rules = getattr(source_client, "fetch_trading_rules", None)
            if callable(fetch_rules):
                current_rules = trading_rule_sources.get(pair.trade_date)
                if (
                    current_rules is None
                    and pair.trade_date not in missing_trading_rule_sources
                ):
                    try:
                        current_rules = fetch_rules(pair.trade_date)
                    except MetadataBuildError as exc:
                        if exc.code != "MISSING_TRADE_RULE_SNAPSHOT":
                            raise
                        missing_trading_rule_sources.add(pair.trade_date)
                    else:
                        trading_rule_sources[pair.trade_date] = current_rules
                if current_rules is not None:
                    preloaded_trade_rule_row = _exact_trade_rule_row(
                        pair.root_symbol,
                        pair.exchange,
                        current_rules,
                    )
        while True:
            vendor = vendor_sources.get(vendor_source_date)
            if vendor is None:
                vendor = source_client.fetch_vendor_parameters(vendor_source_date)
                vendor_sources[vendor_source_date] = vendor
            try:
                vendor_row = _exact_contract_row(
                    pair.contract_code,
                    vendor,
                    code_column="合约代码",
                )
            except MetadataBuildError as exc:
                if exc.code != "MISSING_CONTRACT":
                    raise
                if (
                    vendor_source_date == prior_open
                    and exchange_token == "CZCE"
                ):
                    fetch_czce_parameters = getattr(
                        source_client,
                        "fetch_czce_settlement_parameters",
                        None,
                    )
                    if not callable(fetch_czce_parameters):
                        raise MetadataBuildError(
                            "MISSING_CZCE_OFFICIAL_SOURCE_CLIENT",
                            f"{pair.contract_code} {pair.trade_date}",
                        ) from exc
                    parameters = czce_parameter_sources.get(prior_open)
                    if parameters is None:
                        parameters = fetch_czce_parameters(prior_open)
                        czce_parameter_sources[prior_open] = parameters
                    vendor_row = _czce_official_vendor_row(
                        local_contract=pair.contract_code,
                        root_symbol=pair.root_symbol,
                        source_date=prior_open,
                        effective_on=pair.trade_date,
                        price_tick=price_tick,
                        expected_settlement=_positive_float(
                            prior_row.get("settle"),
                            "PRIOR_SETTLEMENT",
                        ),
                        parameters=parameters,
                    )
                    price_known_at = _published_at(
                        vendor_row.get("价格公布时间"),
                        vendor_source_date,
                        "PRICE_PUBLISHED_AT",
                    )
                    fee_known_at = _published_at(
                        vendor_row.get("手续费公布时间"),
                        vendor_source_date,
                        "FEE_PUBLISHED_AT",
                    )
                    break
                if vendor_source_date == prior_open:
                    historical_fee_vendor_row = (
                        _dce_historical_fee_vendor_row(
                            local_contract=pair.contract_code,
                            root_symbol=pair.root_symbol,
                            exchange=pair.exchange,
                            trade_date=pair.trade_date,
                            vendor_source_date=vendor_source_date,
                            session_open=session_open,
                            prior_settlement_row=prior_row,
                            current_trade_rule_row=preloaded_trade_rule_row,
                            price_tick=price_tick,
                        )
                    )
                    if historical_fee_vendor_row is not None:
                        vendor_row = historical_fee_vendor_row
                        price_known_at = _published_at(
                            vendor_row.get("价格公布时间"),
                            vendor_source_date,
                            "PRICE_PUBLISHED_AT",
                        )
                        fee_known_at = _published_at(
                            vendor_row.get("手续费公布时间"),
                            vendor_source_date,
                            "FEE_PUBLISHED_AT",
                        )
                        snapshot_path = Path(
                            str(
                                vendor_row.get(
                                    "_historical_fee_snapshot_path"
                                )
                            )
                        )
                        audit_record = _historical_fee_snapshot_audit_record(
                            snapshot_path
                        )
                        checked_in_audit_records[
                            str(audit_record["source_key"])
                        ] = audit_record
                        break
                    target_close_roll_vendor_row: dict[str, object] | None = None
                    if (
                        (pair.contract_code, pair.trade_date)
                        in roll_entry_pairs
                        and exchange_token == "DCE"
                    ):
                        target_vendor = vendor_sources.get(pair.trade_date)
                        if target_vendor is None:
                            target_vendor = source_client.fetch_vendor_parameters(
                                pair.trade_date
                            )
                            vendor_sources[pair.trade_date] = target_vendor
                        try:
                            target_close_roll_vendor_row = (
                                _dce_target_close_roll_fee_vendor_row(
                                    local_contract=pair.contract_code,
                                    root_symbol=pair.root_symbol,
                                    exchange=pair.exchange,
                                    trade_date=pair.trade_date,
                                    vendor_source_date=vendor_source_date,
                                    session_open=session_open,
                                    current_settlement_row=current_row,
                                    prior_settlement_row=prior_row,
                                    prior_vendor_frame=vendor,
                                    target_vendor_frame=target_vendor,
                                    current_trade_rule_row=preloaded_trade_rule_row,
                                    price_tick=price_tick,
                                )
                            )
                        except MetadataBuildError as target_close_exc:
                            if (
                                target_close_exc.code
                                != "MISSING_ROLL_FEE_REFERENCE"
                            ):
                                raise
                            historical_source_date = vendor_source_date
                            while True:
                                try:
                                    historical_source_date = (
                                        _prior_vendor_source_date(
                                            calendar,
                                            historical_source_date,
                                            pair.contract_code,
                                            session_open,
                                        )
                                    )
                                except MetadataBuildError as history_exc:
                                    if (
                                        not allow_runtime_defaults
                                        or history_exc.code
                                        != "MISSING_VISIBLE_VENDOR_PARAMETERS"
                                    ):
                                        raise target_close_exc
                                    target_close_roll_vendor_row = (
                                        _dce_target_close_roll_fee_vendor_row(
                                            local_contract=pair.contract_code,
                                            root_symbol=pair.root_symbol,
                                            exchange=pair.exchange,
                                            trade_date=pair.trade_date,
                                            vendor_source_date=vendor_source_date,
                                            session_open=session_open,
                                            current_settlement_row=current_row,
                                            prior_settlement_row=prior_row,
                                            prior_vendor_frame=vendor,
                                            target_vendor_frame=target_vendor,
                                            current_trade_rule_row=(
                                                preloaded_trade_rule_row
                                            ),
                                            price_tick=price_tick,
                                            allow_runtime_defaults=True,
                                        )
                                    )
                                    break
                                historical_vendor = vendor_sources.get(
                                    historical_source_date
                                )
                                if historical_vendor is None:
                                    historical_vendor = (
                                        source_client.fetch_vendor_parameters(
                                            historical_source_date
                                        )
                                    )
                                    vendor_sources[historical_source_date] = (
                                        historical_vendor
                                    )
                                try:
                                    target_close_roll_vendor_row = (
                                        _dce_target_close_roll_fee_vendor_row(
                                            local_contract=pair.contract_code,
                                            root_symbol=pair.root_symbol,
                                            exchange=pair.exchange,
                                            trade_date=pair.trade_date,
                                            vendor_source_date=(
                                                historical_source_date
                                            ),
                                            session_open=session_open,
                                            current_settlement_row=current_row,
                                            prior_settlement_row=prior_row,
                                            prior_vendor_frame=historical_vendor,
                                            target_vendor_frame=target_vendor,
                                            current_trade_rule_row=(
                                                preloaded_trade_rule_row
                                            ),
                                            price_tick=price_tick,
                                        )
                                    )
                                except MetadataBuildError as historical_exc:
                                    if (
                                        historical_exc.code
                                        != "MISSING_ROLL_FEE_REFERENCE"
                                    ):
                                        raise
                                    target_close_exc = historical_exc
                                    continue
                                if target_close_roll_vendor_row is not None:
                                    vendor_source_date = historical_source_date
                                break
                    if target_close_roll_vendor_row is not None:
                        vendor_row = target_close_roll_vendor_row
                        price_known_at = _published_at(
                            vendor_row.get("价格公布时间"),
                            vendor_source_date,
                            "PRICE_PUBLISHED_AT",
                        )
                        fee_known_at = _published_at(
                            vendor_row.get("手续费公布时间"),
                            vendor_source_date,
                            "FEE_PUBLISHED_AT",
                        )
                        break
                if (
                    vendor_source_date != prior_open
                    and late_vendor_validation_row is not None
                    and (pair.contract_code, pair.trade_date) in post_roll_pairs
                    and exchange_token == "DCE"
                    and preloaded_trade_rule_row is not None
                ):
                    target_vendor = vendor_sources.get(pair.trade_date)
                    if target_vendor is None:
                        target_vendor = source_client.fetch_vendor_parameters(
                            pair.trade_date
                        )
                        vendor_sources[pair.trade_date] = target_vendor
                    post_roll_vendor_row = (
                        _dce_target_close_roll_fee_vendor_row(
                            local_contract=pair.contract_code,
                            root_symbol=pair.root_symbol,
                            exchange=pair.exchange,
                            trade_date=pair.trade_date,
                            vendor_source_date=vendor_source_date,
                            session_open=session_open,
                            current_settlement_row=current_row,
                            prior_settlement_row=prior_row,
                            prior_vendor_frame=vendor,
                            target_vendor_frame=target_vendor,
                            current_trade_rule_row=preloaded_trade_rule_row,
                            price_tick=price_tick,
                        )
                    )
                    if post_roll_vendor_row is not None:
                        post_roll_vendor_row[
                            "_dce_post_roll_night_fee_validation"
                        ] = True
                        vendor_row = post_roll_vendor_row
                        price_known_at = _published_at(
                            vendor_row.get("价格公布时间"),
                            vendor_source_date,
                            "PRICE_PUBLISHED_AT",
                        )
                        fee_known_at = _published_at(
                            vendor_row.get("手续费公布时间"),
                            vendor_source_date,
                            "FEE_PUBLISHED_AT",
                        )
                        break
                if (
                    vendor_source_date == prior_open
                    and official_parameter_cache is not None
                ):
                    (
                        vendor_row,
                        official_vendor_parameter_row,
                        official_vendor_rule_row,
                        price_known_at,
                        fee_known_at,
                    ) = _load_shfe_official_vendor(
                        source_client=source_client,
                        local_contract=pair.contract_code,
                        root_symbol=pair.root_symbol,
                        exchange=pair.exchange,
                        source_date=prior_open,
                        trade_date=pair.trade_date,
                        price_tick=price_tick,
                        parameter_cache=official_parameter_cache,
                        rule_cache=trading_rule_sources,
                    )
                    shfe_official_vendor_validation = True
                    break
                vendor_source_date = _prior_vendor_source_date(
                    calendar,
                    vendor_source_date,
                    pair.contract_code,
                    session_open,
                )
                continue
            price_known_at = _published_at(
                vendor_row.get("价格公布时间"),
                vendor_source_date,
                "PRICE_PUBLISHED_AT",
            )
            fee_known_at = _published_at(
                vendor_row.get("手续费公布时间"),
                vendor_source_date,
                "FEE_PUBLISHED_AT",
            )
            if max(price_known_at, fee_known_at) <= session_open:
                break
            if vendor_source_date == prior_open and exchange_token == "CZCE":
                fetch_czce_parameters = getattr(
                    source_client,
                    "fetch_czce_settlement_parameters",
                    None,
                )
                if not callable(fetch_czce_parameters):
                    raise MetadataBuildError(
                        "MISSING_CZCE_OFFICIAL_SOURCE_CLIENT",
                        f"{pair.contract_code} {pair.trade_date}",
                    )
                parameters = czce_parameter_sources.get(prior_open)
                if parameters is None:
                    parameters = fetch_czce_parameters(prior_open)
                    czce_parameter_sources[prior_open] = parameters
                vendor_row = _czce_official_vendor_row(
                    local_contract=pair.contract_code,
                    root_symbol=pair.root_symbol,
                    source_date=prior_open,
                    effective_on=pair.trade_date,
                    price_tick=price_tick,
                    expected_settlement=_positive_float(
                        prior_row.get("settle"),
                        "PRIOR_SETTLEMENT",
                    ),
                    parameters=parameters,
                )
                price_known_at = _published_at(
                    vendor_row.get("价格公布时间"),
                    prior_open,
                    "PRICE_PUBLISHED_AT",
                )
                fee_known_at = _published_at(
                    vendor_row.get("手续费公布时间"),
                    prior_open,
                    "FEE_PUBLISHED_AT",
                )
                break
            if vendor_source_date == prior_open:
                historical_fee_vendor_row = (
                    _dce_historical_fee_vendor_row(
                        local_contract=pair.contract_code,
                        root_symbol=pair.root_symbol,
                        exchange=pair.exchange,
                        trade_date=pair.trade_date,
                        vendor_source_date=vendor_source_date,
                        session_open=session_open,
                        prior_settlement_row=prior_row,
                        current_trade_rule_row=preloaded_trade_rule_row,
                        price_tick=price_tick,
                    )
                )
                if historical_fee_vendor_row is not None:
                    vendor_row = historical_fee_vendor_row
                    price_known_at = _published_at(
                        vendor_row.get("价格公布时间"),
                        vendor_source_date,
                        "PRICE_PUBLISHED_AT",
                    )
                    fee_known_at = _published_at(
                        vendor_row.get("手续费公布时间"),
                        vendor_source_date,
                        "FEE_PUBLISHED_AT",
                    )
                    snapshot_path = Path(
                        str(
                            vendor_row.get(
                                "_historical_fee_snapshot_path"
                            )
                        )
                    )
                    audit_record = _historical_fee_snapshot_audit_record(
                        snapshot_path
                    )
                    checked_in_audit_records[
                        str(audit_record["source_key"])
                    ] = audit_record
                    break
            day_session_open = datetime.combine(
                pair.trade_date,
                time(9),
                tzinfo=SHANGHAI_TZ,
            )
            if (
                (pair.contract_code, pair.trade_date) in roll_entry_pairs
                and vendor_source_date == prior_open
                and max(price_known_at, fee_known_at) <= day_session_open
                and (
                    str(prior_row.get("_source_table"))
                    == "tushare_fut_settle"
                    or (
                        preloaded_trade_rule_row is not None
                        and not pd.isna(
                            preloaded_trade_rule_row.get("交易保证金比例")
                        )
                    )
                )
            ):
                try:
                    fee_known_at, fee_reference_contract = (
                        _validated_roll_fee_reference(
                            local_contract=pair.contract_code,
                            source_date=vendor_source_date,
                            vendor_frame=vendor,
                            session_open=session_open,
                        )
                    )
                except MetadataBuildError as exc:
                    if exc.code == "MISSING_ROLL_FEE_REFERENCE":
                        historical_source_date = vendor_source_date
                        while True:
                            try:
                                historical_source_date = (
                                    _prior_vendor_source_date(
                                        calendar,
                                        historical_source_date,
                                        pair.contract_code,
                                        session_open,
                                    )
                                )
                            except MetadataBuildError as history_exc:
                                if (
                                    allow_runtime_defaults
                                    and history_exc.code
                                    == "MISSING_VISIBLE_VENDOR_PARAMETERS"
                                ):
                                    vendor_row = dict(vendor_row)
                                    vendor_row[
                                        "_assumed_runtime_default_reason"
                                    ] = str(exc)
                                    fee_known_at = session_open
                                    fee_reference_contract = (
                                        f"{pair.contract_code.split('.', maxsplit=1)[0].lower()}"
                                        f"@{vendor_source_date:%Y%m%d}:runtime_default"
                                    )
                                    price_known_at = session_open
                                    roll_entry_validation = True
                                    break
                                raise exc
                            historical_vendor = vendor_sources.get(
                                historical_source_date
                            )
                            if historical_vendor is None:
                                historical_vendor = (
                                    source_client.fetch_vendor_parameters(
                                        historical_source_date
                                    )
                                )
                                vendor_sources[historical_source_date] = (
                                    historical_vendor
                                )
                            try:
                                fee_known_at, fee_reference_contract = (
                                    _validated_historical_roll_fee_reference(
                                        local_contract=pair.contract_code,
                                        source_date=vendor_source_date,
                                        current_frame=vendor,
                                        historical_source_date=(
                                            historical_source_date
                                        ),
                                        historical_frame=historical_vendor,
                                        session_open=session_open,
                                    )
                                )
                            except MetadataBuildError as historical_exc:
                                if (
                                    historical_exc.code
                                    != "MISSING_ROLL_FEE_REFERENCE"
                                ):
                                    exc = historical_exc
                                    break
                                exc = historical_exc
                                continue
                            price_known_at = session_open
                            roll_entry_validation = True
                            break
                        if roll_entry_validation:
                            break
                    if (
                        exc.code != "ROLL_FEE_VALIDATION_FAILED"
                        or official_parameter_cache is None
                    ):
                        raise
                    late_limit_up = _positive_float(
                        vendor_row.get("涨停板"),
                        "LIMIT_UP",
                    )
                    late_limit_down = _positive_float(
                        vendor_row.get("跌停板"),
                        "LIMIT_DOWN",
                    )
                    (
                        official_vendor_row,
                        official_vendor_parameter_row,
                        official_vendor_rule_row,
                        price_known_at,
                        fee_known_at,
                    ) = _load_shfe_official_vendor(
                        source_client=source_client,
                        local_contract=pair.contract_code,
                        root_symbol=pair.root_symbol,
                        exchange=pair.exchange,
                        source_date=prior_open,
                        trade_date=pair.trade_date,
                        price_tick=price_tick,
                        parameter_cache=official_parameter_cache,
                        rule_cache=trading_rule_sources,
                    )
                    official_limit_up = _positive_float(
                        official_vendor_row.get("涨停板"),
                        f"{exchange_token}_OFFICIAL_LIMIT_UP",
                    )
                    official_limit_down = _positive_float(
                        official_vendor_row.get("跌停板"),
                        f"{exchange_token}_OFFICIAL_LIMIT_DOWN",
                    )
                    tolerance = price_tick * 1e-7
                    if (
                        abs(official_limit_up - late_limit_up) > tolerance
                        or abs(official_limit_down - late_limit_down) > tolerance
                    ):
                        raise MetadataBuildError(
                            f"{exchange_token}_OFFICIAL_LATE_LIMIT_MISMATCH",
                            f"{pair.contract_code} {pair.trade_date}: "
                            f"official={official_limit_down}/{official_limit_up} "
                            f"jin10={late_limit_down}/{late_limit_up}",
                        ) from exc
                    vendor_row = official_vendor_row
                    shfe_official_vendor_validation = True
                    shfe_late_limit_validation_date = vendor_source_date
                    break
                price_known_at = session_open
                roll_entry_validation = True
                break
            if (
                vendor_source_date == prior_open
                and official_parameter_cache is not None
            ):
                try:
                    (
                        vendor_row,
                        official_vendor_parameter_row,
                        official_vendor_rule_row,
                        price_known_at,
                        fee_known_at,
                    ) = _load_shfe_official_vendor(
                        source_client=source_client,
                        local_contract=pair.contract_code,
                        root_symbol=pair.root_symbol,
                        exchange=pair.exchange,
                        source_date=prior_open,
                        trade_date=pair.trade_date,
                        price_tick=price_tick,
                        parameter_cache=official_parameter_cache,
                        rule_cache=trading_rule_sources,
                    )
                except MetadataBuildError as exc:
                    if exc.code != "MISSING_TRADE_RULE_SNAPSHOT":
                        raise
                    late_vendor_validation_row = vendor_row
                    vendor_source_date = _prior_vendor_source_date(
                        calendar,
                        vendor_source_date,
                        pair.contract_code,
                        session_open,
                    )
                    continue
                shfe_official_vendor_validation = True
                break
            if vendor_source_date == prior_open:
                late_vendor_validation_row = vendor_row
            vendor_source_date = _prior_vendor_source_date(
                calendar,
                vendor_source_date,
                pair.contract_code,
                session_open,
            )
        prior_trade_rule_row: dict[str, object] | None = None
        current_trade_rule_row = (
            official_vendor_rule_row or preloaded_trade_rule_row
        )
        prior_shfe_parameter_row = official_vendor_parameter_row
        current_shfe_parameter_row = official_vendor_parameter_row
        prior_settlement = _positive_float(
            prior_row.get("settle"),
            "PRIOR_SETTLEMENT",
        )
        vendor_settlement = _positive_float(
            vendor_row.get("现价"),
            "VENDOR_SETTLEMENT",
        )
        settlements_match = abs(prior_settlement - vendor_settlement) <= (
            price_tick / 2.0 + 1e-12
        )
        if roll_entry_validation or not settlements_match:
            exchange_token = _EXCHANGE_ALIASES.get(pair.exchange, pair.exchange)
            fetch_official_parameters: object | None = None
            official_parameter_cache: dict[date, pd.DataFrame] | None = None
            if exchange_token == "SHFE":
                fetch_official_parameters = getattr(
                    source_client,
                    "fetch_shfe_settlement_parameters",
                    None,
                )
                official_parameter_cache = shfe_parameter_sources
            elif exchange_token == "INE":
                fetch_official_parameters = getattr(
                    source_client,
                    "fetch_ine_settlement_parameters",
                    None,
                )
                official_parameter_cache = ine_parameter_sources
            if callable(fetch_official_parameters) and official_parameter_cache is not None:
                prior_shfe_parameter_row = _find_shfe_vendor_parameter_row(
                    local_contract=pair.contract_code,
                    source_date=vendor_source_date,
                    expected_settlement=vendor_settlement,
                    price_tick=price_tick,
                    calendar=calendar,
                    fetch_parameters=fetch_official_parameters,
                    cache=official_parameter_cache,
                    source_name=exchange_token,
                )
                current_parameters = official_parameter_cache.get(prior_open)
                if current_parameters is None:
                    current_parameters = fetch_official_parameters(prior_open)
                    official_parameter_cache[prior_open] = current_parameters
                current_shfe_parameter_row = _exact_contract_row(
                    pair.contract_code,
                    current_parameters,
                    code_column="INSTRUMENTID",
                )
            uses_published_shfe_roll = (
                roll_entry_validation
                and settlements_match
                and prior_shfe_parameter_row is not None
                and current_shfe_parameter_row is not None
            )
            fetch_rules = getattr(source_client, "fetch_trading_rules", None)
            if callable(fetch_rules) and not uses_published_shfe_roll:
                rule_rows: list[dict[str, object]] = []
                vendor_rule_effective_date = _next_open_after(
                    calendar,
                    vendor_source_date,
                )
                for rule_role, source_date in (
                    ("vendor_limits", vendor_rule_effective_date),
                    ("current_limits", pair.trade_date),
                ):
                    if (
                        rule_role == "current_limits"
                        and source_date == vendor_rule_effective_date
                        and rule_rows
                    ):
                        rule_rows.append(rule_rows[0])
                        continue
                    rules = trading_rule_sources.get(source_date)
                    if rules is None:
                        try:
                            if source_date in missing_trading_rule_sources:
                                raise MetadataBuildError(
                                    "MISSING_TRADE_RULE_SNAPSHOT",
                                    source_date.isoformat(),
                                )
                            rules = fetch_rules(source_date)
                        except MetadataBuildError as exc:
                            fallback_date: date | None = None
                            validation_row: Mapping[str, object] | None = None
                            validation_settlement: float | None = None
                            if exc.code == "MISSING_TRADE_RULE_SNAPSHOT":
                                missing_trading_rule_sources.add(source_date)
                                fetch_czce_parameters = getattr(
                                    source_client,
                                    "fetch_czce_settlement_parameters",
                                    None,
                                )
                                if (
                                    rule_role == "current_limits"
                                    and exchange_token == "CZCE"
                                    and callable(fetch_czce_parameters)
                                ):
                                    parameters = czce_parameter_sources.get(
                                        prior_open
                                    )
                                    if parameters is None:
                                        parameters = fetch_czce_parameters(
                                            prior_open
                                        )
                                        czce_parameter_sources[prior_open] = (
                                            parameters
                                        )
                                    rule_rows.append(
                                        _czce_official_trade_rule(
                                            local_contract=pair.contract_code,
                                            root_symbol=pair.root_symbol,
                                            effective_on=source_date,
                                            parameter_date=prior_open,
                                            price_tick=price_tick,
                                            expected_settlement=_positive_float(
                                                prior_row.get("settle"),
                                                "PRIOR_SETTLEMENT",
                                            ),
                                            parameters=parameters,
                                        )
                                    )
                                    continue
                                if rule_role == "vendor_limits":
                                    fallback_date = vendor_source_date
                                    validation_row = vendor_row
                                    validation_settlement = vendor_settlement
                                elif late_vendor_validation_row is not None:
                                    fallback_date = prior_open
                                    validation_row = late_vendor_validation_row
                                    validation_settlement = (
                                        _positive_float(
                                            validation_row.get("现价"),
                                            "GFEX_CARRY_VALIDATION_SETTLEMENT",
                                        )
                                        if exchange_token == "GFEX"
                                        else prior_settlement
                                    )
                            if (
                                fallback_date is not None
                                and validation_row is not None
                                and validation_settlement is not None
                            ):
                                fallback_rules = trading_rule_sources.get(
                                    fallback_date
                                )
                                if fallback_rules is None:
                                    fallback_rules = fetch_rules(fallback_date)
                                    trading_rule_sources[fallback_date] = fallback_rules
                                fallback_row = _exact_trade_rule_row(
                                    pair.root_symbol,
                                    pair.exchange,
                                    fallback_rules,
                                )
                                rule_rows.append(
                                    _carry_forward_trade_rule(
                                        rule_row=fallback_row,
                                        local_contract=pair.contract_code,
                                        root_symbol=pair.root_symbol,
                                        exchange=pair.exchange,
                                        source_date=fallback_date,
                                        effective_on=source_date,
                                        price_tick=price_tick,
                                        prior_settlement=validation_settlement,
                                        validation_vendor_row=validation_row,
                                        official_parameter_row=(
                                            current_shfe_parameter_row
                                            if validation_row
                                            is late_vendor_validation_row
                                            else prior_shfe_parameter_row
                                        ),
                                    )
                                )
                                continue
                            raise MetadataBuildError(
                                exc.code,
                                f"{pair.contract_code} {source_date}: {exc.detail}",
                            ) from exc
                        trading_rule_sources[source_date] = rules
                    rule_rows.append(
                        _exact_trade_rule_row(
                            pair.root_symbol,
                            pair.exchange,
                            rules,
                        )
                    )
                prior_trade_rule_row, current_trade_rule_row = rule_rows
        if (
            current_shfe_parameter_row is None
            and official_parameter_cache is not None
        ):
            fetch_method = {
                "INE": "fetch_ine_settlement_parameters",
                "SHFE": "fetch_shfe_settlement_parameters",
            }[exchange_token]
            fetch_official_parameters = getattr(
                source_client,
                fetch_method,
                None,
            )
            if callable(fetch_official_parameters):
                current_parameters = official_parameter_cache.get(prior_open)
                if current_parameters is None:
                    current_parameters = fetch_official_parameters(prior_open)
                    official_parameter_cache[prior_open] = current_parameters
                current_shfe_parameter_row = _exact_contract_row(
                    pair.contract_code,
                    current_parameters,
                    code_column="INSTRUMENTID",
                )
        if current_trade_rule_row is not None:
            czce_known_at = current_trade_rule_row.get(
                "_czce_official_parameter_known_at"
            )
            if czce_known_at is not None:
                parsed_czce_known_at = pd.Timestamp(czce_known_at)
                if parsed_czce_known_at.tzinfo is None:
                    raise MetadataBuildError(
                        "NAIVE_CZCE_OFFICIAL_KNOWN_AT",
                        pair.contract_code,
                    )
                price_known_at = max(
                    price_known_at,
                    parsed_czce_known_at.tz_convert(
                        SHANGHAI_TZ
                    ).to_pydatetime(),
                )
        mechanics = reconcile_daily_mechanics(
            local_contract=pair.contract_code,
            root_symbol=pair.root_symbol,
            exchange=pair.exchange,
            trade_date=pair.trade_date,
            prior_open_date=prior_open,
            price_tick=price_tick,
            current_settlement_row=current_row,
            prior_settlement_row=prior_row,
            vendor_row=vendor_row,
            vendor_source_date=vendor_source_date,
            prior_trade_rule_row=prior_trade_rule_row,
            current_trade_rule_row=current_trade_rule_row,
            prior_shfe_parameter_row=prior_shfe_parameter_row,
            current_shfe_parameter_row=current_shfe_parameter_row,
            session_open=session_open,
            price_known_at=price_known_at,
            fee_known_at=fee_known_at,
            roll_entry_validation=roll_entry_validation,
            fee_reference_contract=fee_reference_contract,
            shfe_official_vendor_validation=shfe_official_vendor_validation,
            shfe_late_limit_validation_date=shfe_late_limit_validation_date,
        )
        mechanics.daily.update(
            {
                "contract_size": pair_mechanics.contract_size,
                "price_tick": pair_mechanics.price_tick,
                "mechanics_source": pair_mechanics.source,
                "mechanics_known_at": pair_mechanics.known_at.isoformat(),
            }
        )
        daily_rows.append(mechanics.daily)
        fee_rows.append(mechanics.fee)
        assumption_reason = vendor_row.get("_assumed_runtime_default_reason")
        if assumption_reason is not None:
            assumptions.append(
                {
                    "root_symbol": pair.root_symbol,
                    "contract_code": pair.contract_code,
                    "exchange_trade_date": pair.trade_date.isoformat(),
                    "field": "roll_fee_reference",
                    "reason_code": str(assumption_reason).split(":", 1)[0],
                    "fallback": "CURRENT_CONTRACT_RUNTIME_PARAMETERS",
                }
            )

    row_groups = {
        "exchange_calendar.csv": calendar_rows,
        "contract_specs.csv": contract_rows,
        "contract_daily.csv": daily_rows,
        "fee_margin_schedule.csv": fee_rows,
    }
    for filename, rows in row_groups.items():
        columns = CANONICAL_SCHEMAS[filename]
        if filename == "contract_daily.csv":
            columns = (
                *columns,
                "pre_settlement_known_at",
                "settlement_known_at",
                "contract_size",
                "price_tick",
                "mechanics_source",
                "mechanics_known_at",
            )
        frames[filename] = pd.DataFrame(
            rows,
            columns=columns,
        )
    return ExtensionBuildResult(
        frames=frames,
        audit_records=(
            *source_client.audit_records,
            *checked_in_audit_records.values(),
        ),
        assumptions=tuple(assumptions),
    )


def _partition_date(path: Path) -> date | None:
    for format_string in _PARTITION_DATE_FORMATS:
        try:
            return datetime.strptime(path.stem, format_string).date()
        except ValueError:
            continue
    return None


def _partition_contract(path: Path) -> str:
    for column in ("contract_code", "ts_code"):
        try:
            frame = pd.read_parquet(path, columns=[column])
        except (KeyError, OSError, ValueError):
            continue
        contracts = {
            value
            for value in frame[column].dropna().astype(str).str.upper().str.strip()
            if value
        }
        if len(contracts) != 1:
            raise MetadataBuildError(
                "AMBIGUOUS_PARTITION_CONTRACT",
                f"{path}: {sorted(contracts)}",
            )
        return next(iter(contracts))
    raise MetadataBuildError("MISSING_PARTITION_CONTRACT", str(path))


def _partition_has_night_bars(path: Path) -> bool:
    timestamps = _partition_timestamps(path)
    return bool(pd.Series(timestamps).dt.hour.ge(18).any())


def _partition_timestamps(path: Path) -> pd.DatetimeIndex:
    for column in ("datetime", "trade_time"):
        try:
            frame = pd.read_parquet(path, columns=[column])
        except (KeyError, OSError, ValueError):
            continue
        timestamps = pd.to_datetime(frame[column], errors="coerce")
        if timestamps.isna().any():
            raise MetadataBuildError("INVALID_PARTITION_TIMESTAMP", str(path))
        if timestamps.dt.tz is None:
            return pd.DatetimeIndex(timestamps.dt.tz_localize(SHANGHAI_TZ))
        return pd.DatetimeIndex(timestamps.dt.tz_convert(SHANGHAI_TZ))
    raise MetadataBuildError("MISSING_PARTITION_TIMESTAMP", str(path))


def _night_session_start_for_trade_date(
    *,
    trade_date: date,
    prior_open_date: date,
    exchange_has_night: bool,
) -> str | None:
    if not exchange_has_night:
        return None
    gap = (trade_date - prior_open_date).days
    is_regular_monday = trade_date.weekday() == 0 and gap == 3
    return "21:00:00" if gap == 1 or is_regular_monday else None


def _contract_identity(contract_code: str) -> tuple[str, str]:
    code, separator, suffix = contract_code.upper().partition(".")
    match = _CONTRACT.fullmatch(code)
    if match is None or not separator or not suffix:
        raise MetadataBuildError("INVALID_LOCAL_CONTRACT", contract_code)
    return match.group("root"), _EXCHANGE_ALIASES.get(suffix, suffix)


def _roll_contract_overlaps_partition(
    *,
    source_directory: Path,
    root_symbol: str,
    contract_code: str,
    partition_path: Path,
    trade_date: date,
) -> bool:
    contract_path = _contract_minute_path(
        source_directory=source_directory,
        root_symbol=root_symbol,
        contract_code=contract_code,
    )
    if not contract_path.is_file():
        return False
    partition_times = set(_partition_timestamps(partition_path))
    if not partition_times:
        return False
    contract_times = _contract_trade_date_timestamps(
        path=contract_path,
        contract_code=contract_code,
        trade_date=trade_date,
    )
    return bool(partition_times.intersection(contract_times))


def _contract_minute_path(
    *,
    source_directory: Path,
    root_symbol: str,
    contract_code: str,
) -> Path:
    minute_root = source_directory.parent
    origin_root = minute_root.parent if minute_root.name == "minute" else minute_root
    return (
        origin_root
        / "contract"
        / root_symbol
        / "minute"
        / f"{normalize_contract_filename(contract_code)}.parquet"
    )


def _contract_trade_date_timestamps(
    *,
    path: Path,
    contract_code: str,
    trade_date: date,
) -> set[pd.Timestamp]:
    for timestamp_column in ("datetime", "trade_time"):
        for contract_column in ("contract_code", "ts_code"):
            try:
                frame = pd.read_parquet(path, columns=[timestamp_column, contract_column])
            except (KeyError, OSError, ValueError):
                continue
            timestamps = pd.to_datetime(frame[timestamp_column], errors="coerce")
            if timestamps.isna().any():
                raise MetadataBuildError("INVALID_PARTITION_TIMESTAMP", str(path))
            if timestamps.dt.tz is None:
                timestamps = timestamps.dt.tz_localize(SHANGHAI_TZ)
            else:
                timestamps = timestamps.dt.tz_convert(SHANGHAI_TZ)
            matched = (
                frame[contract_column].astype(str).str.upper().eq(contract_code)
                & timestamps.dt.date.eq(trade_date)
            )
            return set(pd.DatetimeIndex(timestamps.loc[matched]))
    raise MetadataBuildError("MISSING_PARTITION_CONTRACT", str(path))


def _key_set(
    frames: Mapping[str, pd.DataFrame],
    filename: str,
    columns: tuple[str, ...],
    *,
    date_columns: set[str] = frozenset(),
) -> set[tuple[object, ...]]:
    frame = frames.get(filename, pd.DataFrame())
    if frame.empty or not set(columns).issubset(frame):
        return set()
    result: set[tuple[object, ...]] = set()
    for row in frame.loc[:, columns].itertuples(index=False, name=None):
        values = tuple(
            pd.Timestamp(value).date() if column in date_columns else str(value)
            for column, value in zip(columns, row, strict=True)
        )
        result.add(values)
    return result


def _exact_contract_row(
    local_contract: str,
    frame: pd.DataFrame,
    *,
    code_column: str = "ts_code",
) -> dict[str, object]:
    if code_column not in frame:
        raise MetadataBuildError(
            "MISSING_CONTRACT_COLUMN",
            f"{code_column}: {local_contract}",
        )
    matched_code = match_vendor_contract(
        local_contract,
        tuple(frame[code_column].dropna().astype(str)),
    )
    normalized_codes = frame[code_column].astype(str).str.strip().str.upper()
    selected = frame.loc[
        normalized_codes.eq(str(matched_code).strip().upper())
    ]
    return selected.iloc[0].to_dict()


def _exact_settlement_row(
    *,
    source_client: MetadataSourceClient,
    contract_code: str,
    trade_date: date,
    settlements: pd.DataFrame,
    context: str,
) -> dict[str, object]:
    try:
        row = _exact_contract_row(contract_code, settlements)
        row["_source_table"] = "tushare_fut_settle"
        return row
    except MetadataBuildError as exc:
        if exc.code != "MISSING_CONTRACT":
            raise MetadataBuildError(
                exc.code,
                f"{context} {trade_date}: {exc.detail}",
            ) from exc
        fetch_daily_quote = getattr(source_client, "fetch_daily_quote", None)
        if not callable(fetch_daily_quote):
            raise MetadataBuildError(
                exc.code,
                f"{context} {trade_date}: {exc.detail}",
            ) from exc

    daily_quotes = fetch_daily_quote(contract_code, trade_date)
    try:
        row = _exact_contract_row(contract_code, daily_quotes)
    except MetadataBuildError as exc:
        raise MetadataBuildError(
            exc.code,
            f"{context}_daily_quote {trade_date}: {exc.detail}",
        ) from exc
    row["_source_table"] = "tushare_fut_daily"
    return row


def _parse_czce_settlement_parameters_text(
    payload: str,
    source_date: date,
) -> pd.DataFrame:
    lines = [line for line in payload.splitlines() if line.strip()]
    if len(lines) < 3:
        raise MetadataBuildError(
            "INVALID_CZCE_SETTLEMENT_PARAMETERS",
            f"{source_date}: no data rows",
        )
    columns = (
        "symbol",
        "settle_price",
        "is_single_market",
        "single_market_days",
        "margin_ratio",
        "limit_ratio",
        "trade_fee",
        "fee_type",
        "delivery_fee",
        "close_today_fee",
        "position_limit",
        "trade_limit",
    )
    rows: list[list[str]] = []
    for line in lines[2:]:
        values = [value.strip() for value in line.split("|")]
        if len(values) < len(columns):
            raise MetadataBuildError(
                "INVALID_CZCE_SETTLEMENT_PARAMETERS",
                f"{source_date}: {line!r}",
            )
        values = values[: len(columns)]
        if not values[0] or any(token in values[0] for token in ("小计", "合计")):
            continue
        rows.append(values)
    if not rows:
        raise MetadataBuildError(
            "INVALID_CZCE_SETTLEMENT_PARAMETERS",
            f"{source_date}: no contract rows",
        )
    frame = pd.DataFrame(rows, columns=columns)
    frame.insert(0, "date", source_date.isoformat())
    frame.insert(
        2,
        "variety",
        frame["symbol"].str.extract(r"([A-Za-z]+)", expand=False).str.upper(),
    )
    return frame


def _parse_gtja_trading_rules_html(
    page_html: str,
    source_date: date,
) -> pd.DataFrame:
    script_match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        page_html,
        flags=re.DOTALL,
    )
    if script_match is None:
        raise MetadataBuildError(
            "MISSING_GTJA_NEXT_DATA",
            source_date.isoformat(),
        )
    try:
        page_props = json.loads(script_match.group(1))["props"]["pageProps"]
        month_data = page_props["MONTH_DATA"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise MetadataBuildError(
            "INVALID_GTJA_NEXT_DATA",
            source_date.isoformat(),
        ) from exc
    source_token = f"{source_date:%Y%m%d}"
    selected_days = [
        row
        for row in month_data
        if isinstance(row, Mapping)
        and str(row.get("tradingday", "")).strip() == source_token
    ]
    if len(selected_days) != 1:
        raise MetadataBuildError(
            "AMBIGUOUS_TRADE_RULE_DATE",
            f"{source_date}: {len(selected_days)} rows",
        )
    events = selected_days[0].get("events")
    if not isinstance(events, list) or not events:
        raise MetadataBuildError(
            "MISSING_TRADE_RULE_SNAPSHOT",
            source_date.isoformat(),
        )

    required = {"交易所", "代码", "涨跌停板幅度", "最小变动价位"}
    candidates: list[pd.DataFrame] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("event_date", "")).strip() != source_token:
            continue
        content = event.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            tables = pd.read_html(StringIO(content), header=1)
        except ValueError:
            continue
        for table in tables:
            table.columns = [str(column).strip() for column in table.columns]
            if required.issubset(table.columns):
                candidates.append(table)
    if not candidates:
        raise MetadataBuildError(
            "MISSING_TRADE_RULE_TABLE",
            source_date.isoformat(),
        )

    result = pd.concat(candidates, ignore_index=True)
    for column in ("交易保证金比例", "涨跌停板幅度"):
        if column in result:
            result[column] = pd.to_numeric(
                result[column].astype(str).str.strip().str.rstrip("%"),
                errors="coerce",
            )
    for column in ("合约乘数", "最小变动价位", "限价单每笔最大下单手数"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def _exact_trade_rule_row(
    root_symbol: str,
    exchange: str,
    frame: pd.DataFrame,
) -> dict[str, object]:
    required = {"交易所", "代码", "生效日期"}
    if not required.issubset(frame):
        raise MetadataBuildError(
            "MISSING_TRADE_RULE_COLUMNS",
            ",".join(sorted(required - set(frame.columns))),
        )
    exchange_token = _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper())
    exchange_name = _GTJA_EXCHANGE_NAMES.get(exchange_token)
    selected = frame.loc[
        frame["代码"].astype(str).str.strip().str.upper().eq(root_symbol.upper())
        & frame["交易所"].astype(str).str.strip().eq(exchange_name)
    ].drop_duplicates()
    if len(selected) != 1:
        raise MetadataBuildError(
            "AMBIGUOUS_TRADE_RULE",
            f"{root_symbol}.{exchange}: {len(selected)} rows",
        )
    return selected.iloc[0].to_dict()


def _contract_spec_row(
    pair: ContractDate,
    source_row: Mapping[str, object],
    template: str,
) -> dict[str, object]:
    source_contract_size = _positive_float(
        source_row.get("per_unit"),
        "CONTRACT_SIZE",
    )
    source_price_tick = parse_price_tick(source_row.get("quote_unit_desc"))
    listed_on = _parse_date(source_row.get("list_date"), "LIST_DATE")
    last_trade_date = _parse_date(source_row.get("delist_date"), "DELIST_DATE")
    if not listed_on <= pair.trade_date <= last_trade_date:
        raise MetadataBuildError(
            "CONTRACT_LIFECYCLE_MISMATCH",
            f"{pair.contract_code} {listed_on}..{last_trade_date} vs {pair.trade_date}",
        )
    listed_at = datetime.combine(listed_on, time(0), tzinfo=SHANGHAI_TZ)
    mechanics = _effective_contract_mechanics(
        root_symbol=pair.root_symbol,
        exchange=pair.exchange,
        effective_on=pair.trade_date,
        source_contract_size=source_contract_size,
        source_price_tick=source_price_tick,
        source=(
            f"{pair.exchange}_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC"
        ),
        source_known_at=listed_at,
    )
    return {
        "contract_code": pair.contract_code,
        "root_symbol": pair.root_symbol,
        "exchange": pair.exchange,
        "contract_size": mechanics.contract_size,
        "price_tick": mechanics.price_tick,
        "lot_step": 1,
        "slippage_ticks_base": 1.0,
        "last_trade_date": last_trade_date.isoformat(),
        "session_template_id": template,
        "source": mechanics.source,
        "known_at": max(listed_at, mechanics.known_at).isoformat(),
    }


def _effective_contract_mechanics(
    *,
    root_symbol: str,
    exchange: str,
    effective_on: date,
    source_contract_size: float,
    source_price_tick: float,
    source: str = "TUSHARE_FUT_BASIC",
    source_known_at: datetime | None = None,
) -> EffectiveContractMechanics:
    exchange_token = _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper())
    if root_symbol.upper() == "EC" and exchange_token == "INE":
        if effective_on < _INE_EC_TICK_CHANGE_DATE:
            tick = 0.1
            source_url = _INE_EC_ORIGINAL_SPEC_URL
            known_at = datetime(2023, 12, 5, tzinfo=SHANGHAI_TZ)
        else:
            tick = 0.5
            source_url = _INE_EC_REVISED_SPEC_URL
            known_at = datetime(2026, 1, 16, tzinfo=SHANGHAI_TZ)
        return EffectiveContractMechanics(
            contract_size=50.0,
            price_tick=tick,
            source=f"INE_OFFICIAL_STANDARD_CONTRACT_EC:{source_url}",
            known_at=known_at,
        )
    if root_symbol.upper() == "P" and exchange_token == "DCE":
        if effective_on < _DCE_P_TICK_CHANGE_DATE:
            tick = 2.0
            source_ref = _DCE_P_ORIGINAL_SPEC_SOURCE
            known_at = datetime(2007, 10, 9, tzinfo=SHANGHAI_TZ)
        else:
            tick = 1.0
            source_ref = _DCE_P_REVISED_SPEC_URL
            known_at = datetime(2026, 3, 31, tzinfo=SHANGHAI_TZ)
        return EffectiveContractMechanics(
            contract_size=10.0,
            price_tick=tick,
            source=f"DCE_OFFICIAL_STANDARD_CONTRACT_P:{source_ref}",
            known_at=known_at,
        )
    if (
        root_symbol.upper() == "Y"
        and exchange_token == "DCE"
        and effective_on >= _DCE_Y_VALIDATED_MECHANICS_START_DATE
    ):
        if effective_on < _DCE_Y_TICK_CHANGE_DATE:
            tick = 2.0
            mechanics_source = (
                "DCE_STANDARD_CONTRACT_Y_VALIDATED_BY_"
                f"{_DCE_Y_VALIDATED_MECHANICS_SOURCE}"
            )
            known_at = datetime(
                2025,
                9,
                15,
                15,
                30,
                tzinfo=SHANGHAI_TZ,
            )
        else:
            tick = 1.0
            mechanics_source = (
                "DCE_OFFICIAL_STANDARD_CONTRACT_Y:"
                f"{_DCE_Y_REVISED_MECHANICS_SOURCE}"
            )
            known_at = datetime(2026, 3, 31, tzinfo=SHANGHAI_TZ)
        return EffectiveContractMechanics(
            contract_size=10.0,
            price_tick=tick,
            source=mechanics_source,
            known_at=known_at,
        )
    if source_known_at is None or source_known_at.tzinfo is None:
        raise MetadataBuildError(
            "MISSING_CONTRACT_MECHANICS_KNOWN_AT",
            f"{root_symbol}.{exchange} {effective_on}",
        )
    return EffectiveContractMechanics(
        contract_size=_positive_float(source_contract_size, "CONTRACT_SIZE"),
        price_tick=_positive_float(source_price_tick, "PRICE_TICK"),
        source=source,
        known_at=source_known_at,
    )


def _normalize_open_calendar(frame: pd.DataFrame, exchange: str) -> pd.DataFrame:
    required = {"cal_date", "is_open", "pretrade_date"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise MetadataBuildError(
            "MISSING_CALENDAR_FIELDS",
            f"{exchange}: {','.join(missing)}",
        )
    result = frame.loc[:, ["cal_date", "is_open", "pretrade_date"]].copy()
    result = result.loc[result["is_open"].map(_is_open)].copy()
    result["trade_date"] = result["cal_date"].map(
        lambda value: _parse_date(value, "CAL_DATE")
    )
    result["prior_open_date"] = result["pretrade_date"].map(
        lambda value: _parse_date(value, "PRETRADE_DATE")
    )
    result = result.sort_values("trade_date", kind="stable").reset_index(drop=True)
    if result["trade_date"].duplicated().any():
        raise MetadataBuildError("AMBIGUOUS_EXCHANGE_CALENDAR", exchange)
    return result


def _next_open_date(calendar: pd.DataFrame, position: int) -> date:
    if position + 1 >= len(calendar):
        raise MetadataBuildError(
            "MISSING_NEXT_OPEN_DATE",
            str(calendar.iloc[position]["trade_date"]),
        )
    return calendar.iloc[position + 1]["trade_date"]


def _next_open_after(calendar: pd.DataFrame, source_date: date) -> date:
    selected = calendar.index[calendar["trade_date"].eq(source_date)].tolist()
    if len(selected) != 1:
        raise MetadataBuildError(
            "MISSING_RULE_EFFECTIVE_DATE",
            source_date.isoformat(),
        )
    return _next_open_date(calendar, int(selected[0]))


def _find_shfe_vendor_parameter_row(
    *,
    local_contract: str,
    source_date: date,
    expected_settlement: float,
    price_tick: float,
    calendar: pd.DataFrame,
    fetch_parameters: Callable[[date], pd.DataFrame],
    cache: dict[date, pd.DataFrame],
    source_name: str = "SHFE",
) -> dict[str, object]:
    candidate_date = source_date
    while True:
        parameters = cache.get(candidate_date)
        if parameters is None:
            parameters = fetch_parameters(candidate_date)
            cache[candidate_date] = parameters
        try:
            row = _exact_contract_row(
                local_contract,
                parameters,
                code_column="INSTRUMENTID",
            )
        except MetadataBuildError as exc:
            if exc.code != "MISSING_CONTRACT":
                raise
        else:
            official_settlement = _positive_float(
                row.get("SETTLEMENTPRICE"),
                f"{source_name}_OFFICIAL_SETTLEMENT",
            )
            if abs(official_settlement - expected_settlement) <= (
                price_tick / 2.0 + 1e-12
            ):
                return row

        calendar_row = calendar.loc[calendar["trade_date"].eq(candidate_date)]
        if len(calendar_row) != 1:
            raise MetadataBuildError(
                f"MISSING_{source_name}_VENDOR_PARAMETER_MATCH",
                f"{local_contract} {source_date}: settlement={expected_settlement}",
            )
        prior_date = calendar_row.iloc[0]["prior_open_date"]
        if prior_date >= candidate_date:
            raise MetadataBuildError(
                "INVALID_PRIOR_OPEN_DATE",
                f"{candidate_date}: {prior_date}",
            )
        candidate_date = prior_date


def _prior_vendor_source_date(
    calendar: pd.DataFrame,
    source_date: date,
    contract_code: str,
    session_open: datetime,
) -> date:
    source_calendar_row = calendar.loc[calendar["trade_date"].eq(source_date)]
    if len(source_calendar_row) != 1:
        raise MetadataBuildError(
            "MISSING_VISIBLE_VENDOR_PARAMETERS",
            f"{contract_code} before {session_open.isoformat()}",
        )
    earlier_source_date = source_calendar_row.iloc[0]["prior_open_date"]
    if earlier_source_date >= source_date:
        raise MetadataBuildError(
            "INVALID_PRIOR_OPEN_DATE",
            f"{source_date}: {earlier_source_date}",
        )
    return earlier_source_date


def _session_open(
    trade_date: date,
    prior_open_date: date,
    template: str,
) -> datetime:
    night_start = _night_session_start_for_trade_date(
        trade_date=trade_date,
        prior_open_date=prior_open_date,
        exchange_has_night="NIGHT" in template,
    )
    has_prior_night = night_start is not None
    session_date = prior_open_date if has_prior_night else trade_date
    session_time = time.fromisoformat(night_start) if night_start else time(9)
    return datetime.combine(session_date, session_time, tzinfo=SHANGHAI_TZ)


def _is_open(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _cached_result(
    target: Path,
    cache_key: str,
    *,
    cache_hit: bool,
) -> MetadataPreparationResult:
    try:
        summary = json.loads(
            (target / "build_summary.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise MetadataBuildError("INVALID_METADATA_CACHE", str(target)) from exc
    if str(summary.get("cache_key")) != cache_key:
        raise MetadataBuildError("METADATA_CACHE_KEY_MISMATCH", str(target))
    manifest_path = target / "manifest.json"
    audit_path = target / "vendor_source_audit.jsonl.gz"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        audit_record = manifest["source_audit"]
        expected_audit_hash = str(audit_record["sha256"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MetadataBuildError("INVALID_METADATA_CACHE", str(target)) from exc
    actual_manifest_hash = sha256_file(manifest_path)
    actual_audit_hash = sha256_file(audit_path)
    if (
        str(summary.get("manifest_sha256")) != actual_manifest_hash
        or str(summary.get("source_audit_sha256")) != actual_audit_hash
        or expected_audit_hash != actual_audit_hash
        or audit_record.get("path") != audit_path.name
    ):
        raise MetadataBuildError("INVALID_METADATA_CACHE", str(target))
    return MetadataPreparationResult(
        metadata_root=target,
        cache_key=cache_key,
        cache_hit=cache_hit,
        contract_date_pairs=int(summary["contract_date_pairs"]),
        generated_contract_rows=int(summary["generated_contract_rows"]),
        generated_daily_rows=int(summary["generated_daily_rows"]),
        generated_fee_rows=int(summary["generated_fee_rows"]),
        source_queries=int(summary["source_queries"]),
        source_audit_sha256=str(summary["source_audit_sha256"]),
        manifest_sha256=str(summary["manifest_sha256"]),
        assumptions=tuple(
            dict(item) for item in summary.get("assumptions", ())
        ),
    )


def _write_source_audit(
    records: Sequence[Mapping[str, object]],
    output: Path,
) -> None:
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record, ensure_ascii=True, sort_keys=True, default=str)
                + "\n"
            )


def _require_frame(value: object, label: str) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame) or value.empty:
        raise MetadataBuildError("EMPTY_SOURCE_RESPONSE", label)
    return value.copy()


def _json_rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    return json.loads(
        json.dumps(
            frame.where(frame.notna(), None).to_dict(orient="records"),
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        )
    )


def _exchange_variants(exchange: str) -> tuple[str, ...]:
    aliases = {
        "CFFEX": ("CFFEX", "CFX"),
        "CZCE": ("CZCE", "CZC", "ZCE"),
        "GFEX": ("GFEX", "GFE"),
        "SHFE": ("SHFE", "SHF"),
    }
    return aliases.get(exchange.upper(), (exchange.upper(),))


def _floor_tick(value: float, tick: float) -> float:
    units = math.floor(value / tick + 1e-10)
    result = units * tick
    return float(int(result)) if float(result).is_integer() else round(result, 10)


def _ceil_tick(value: float, tick: float) -> float:
    units = math.ceil(value / tick - 1e-10)
    result = units * tick
    return float(int(result)) if float(result).is_integer() else round(result, 10)


def _special_rule_applies_to_contract(clause: str, contract_token: str) -> bool:
    if re.search(rf"(?<![A-Z0-9]){re.escape(contract_token)}\s*合约", clause):
        return True

    for matched in re.finditer(
        r"(?<![A-Z0-9])(?P<contracts>[A-Z]+\d+(?:\s*/\s*[A-Z]+\d+)+)\s*合约",
        clause,
    ):
        if contract_token in re.findall(r"[A-Z]+\d+", matched.group("contracts")):
            return True

    contract_match = _CONTRACT.fullmatch(contract_token)
    if contract_match is None:
        return False
    root = contract_match.group("root")
    delivery = int(contract_match.group("delivery"))
    for matched in re.finditer(
        r"(?<![A-Z0-9])(?P<start_root>[A-Z]+)(?P<start>\d+)\s*-\s*"
        r"(?P<end_root>[A-Z]+)(?P<end>\d+)\s*合约",
        clause,
    ):
        if matched.group("start_root") != root or matched.group("end_root") != root:
            continue
        if int(matched.group("start")) <= delivery <= int(matched.group("end")):
            return True
    return False


def _trade_rule_limit_rate(
    row: Mapping[str, object],
    *,
    local_contract: str,
    root_symbol: str,
    exchange: str,
    effective_on: date,
    price_tick: float,
) -> float:
    observed_date = pd.Timestamp(row.get("生效日期")).date()
    carried_forward_to = row.get("_carried_forward_to")
    carried_date = (
        None
        if carried_forward_to is None
        else pd.Timestamp(carried_forward_to).date()
    )
    if observed_date != effective_on and carried_date != effective_on:
        raise MetadataBuildError(
            "TRADE_RULE_DATE_MISMATCH",
            f"{local_contract}: expected={effective_on} observed={observed_date}",
        )
    exchange_token = _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper())
    expected_exchange = _GTJA_EXCHANGE_NAMES.get(exchange_token)
    if expected_exchange is None or str(row.get("交易所")).strip() != expected_exchange:
        raise MetadataBuildError(
            "TRADE_RULE_EXCHANGE_MISMATCH",
            f"{local_contract}: {row.get('交易所')!r}",
        )
    if str(row.get("代码")).strip().upper() != root_symbol.upper():
        raise MetadataBuildError(
            "TRADE_RULE_PRODUCT_MISMATCH",
            f"{local_contract}: {row.get('代码')!r}",
        )
    rule_tick = _positive_float(row.get("最小变动价位"), "TRADE_RULE_PRICE_TICK")
    if abs(rule_tick - price_tick) > 1e-12:
        raise MetadataBuildError(
            "TRADE_RULE_TICK_MISMATCH",
            f"{local_contract}: tushare={price_tick} gtja={rule_tick}",
        )

    published_rate = row.get("_validated_published_limit_rate")
    if published_rate is not None:
        expected_provenance = f"{exchange_token}_OFFICIAL_JS_JIN10"
        if (
            row.get("_published_limit_validation") != expected_provenance
            or carried_date != effective_on
        ):
            raise MetadataBuildError(
                "INVALID_PUBLISHED_LIMIT_RATE_PROVENANCE",
                f"{local_contract} {effective_on}",
            )
        rate = _positive_float(
            published_rate,
            "VALIDATED_PUBLISHED_LIMIT_RATE",
        )
        if rate >= 0.5:
            raise MetadataBuildError(
                "INVALID_TRADE_RULE_LIMIT_RATE",
                f"{local_contract} {effective_on}: {rate}",
            )
        return rate

    rate_percent = _positive_float(
        row.get("涨跌停板幅度"),
        "TRADE_RULE_LIMIT_RATE",
    )
    adjustment_value = row.get("特殊合约参数调整")
    adjustment = "" if pd.isna(adjustment_value) else str(adjustment_value)
    contract_token = local_contract.split(".", maxsplit=1)[0].upper()
    for clause in re.split(r"[;；]", adjustment.upper()):
        if "涨跌" not in clause or not _special_rule_applies_to_contract(
            clause,
            contract_token,
        ):
            continue
        matched = re.search(
            r"涨跌(?:停板)?幅度(?:调整)?为(?P<rate>\d+(?:\.\d+)?)%",
            clause,
        )
        if matched is None:
            raise MetadataBuildError(
                "UNSUPPORTED_SPECIAL_LIMIT_RULE",
                f"{local_contract} {effective_on}: {clause}",
            )
        rate_percent = float(matched.group("rate"))
    rate = rate_percent / 100.0
    if not 0 < rate < 0.5:
        raise MetadataBuildError(
            "INVALID_TRADE_RULE_LIMIT_RATE",
            f"{local_contract} {effective_on}: {rate}",
        )
    return rate


def _trade_rule_margin_rate(
    row: Mapping[str, object],
    *,
    local_contract: str,
    root_symbol: str,
    exchange: str,
    effective_on: date,
    price_tick: float,
) -> float:
    _trade_rule_limit_rate(
        row,
        local_contract=local_contract,
        root_symbol=root_symbol,
        exchange=exchange,
        effective_on=effective_on,
        price_tick=price_tick,
    )
    rate_percent = _positive_float(
        row.get("交易保证金比例"),
        "TRADE_RULE_MARGIN_RATE",
    )
    adjustment_value = row.get("特殊合约参数调整")
    adjustment = "" if pd.isna(adjustment_value) else str(adjustment_value)
    contract_token = local_contract.split(".", maxsplit=1)[0].upper()
    for clause in re.split(r"[;；]", adjustment.upper()):
        if "保证金" not in clause or not _special_rule_applies_to_contract(
            clause,
            contract_token,
        ):
            continue
        matched = re.search(
            r"交易保证金比例(?:调整)?为(?P<rate>\d+(?:\.\d+)?)%",
            clause,
        )
        if matched is None:
            raise MetadataBuildError(
                "UNSUPPORTED_SPECIAL_MARGIN_RULE",
                f"{local_contract} {effective_on}: {clause}",
            )
        rate_percent = float(matched.group("rate"))
    rate = rate_percent / 100.0
    if not 0 < rate <= 1:
        raise MetadataBuildError(
            "INVALID_TRADE_RULE_MARGIN_RATE",
            f"{local_contract} {effective_on}: {rate}",
        )
    return rate


def _czce_official_trade_rule(
    *,
    local_contract: str,
    root_symbol: str,
    effective_on: date,
    parameter_date: date,
    price_tick: float,
    expected_settlement: float,
    parameters: pd.DataFrame,
) -> dict[str, object]:
    row = _exact_contract_row(
        local_contract,
        parameters,
        code_column="symbol",
    )
    _require_source_identity(
        local_contract,
        parameter_date,
        row,
        code_key="symbol",
        date_key="date",
        source="CZCE_OFFICIAL_SETTLEMENT_PARAMETERS",
    )
    official_settlement = _positive_float(
        str(row.get("settle_price", "")).replace(",", ""),
        "CZCE_OFFICIAL_SETTLEMENT",
    )
    if abs(official_settlement - expected_settlement) > (
        price_tick / 2.0 + 1e-12
    ):
        raise MetadataBuildError(
            "CZCE_OFFICIAL_SETTLEMENT_MISMATCH",
            f"{local_contract} {parameter_date}: official={official_settlement} "
            f"tushare={expected_settlement}",
        )
    limit_match = re.fullmatch(
        r"[±+\-](?P<rate>\d+(?:\.\d+)?)",
        str(row.get("limit_ratio", "")).strip(),
    )
    if limit_match is None:
        raise MetadataBuildError(
            "INVALID_CZCE_OFFICIAL_LIMIT_RATE",
            f"{local_contract} {parameter_date}: {row.get('limit_ratio')!r}",
        )
    limit_rate_percent = float(limit_match.group("rate"))
    if not 0 < limit_rate_percent < 50:
        raise MetadataBuildError(
            "INVALID_CZCE_OFFICIAL_LIMIT_RATE",
            f"{local_contract} {parameter_date}: {limit_rate_percent}",
        )
    margin_rate_percent = _positive_float(
        row.get("margin_ratio"),
        "CZCE_OFFICIAL_MARGIN_RATE",
    )
    if margin_rate_percent > 100:
        raise MetadataBuildError(
            "INVALID_CZCE_OFFICIAL_MARGIN_RATE",
            f"{local_contract} {parameter_date}: {margin_rate_percent}",
        )
    known_at = datetime.combine(
        parameter_date,
        time(15, 30),
        tzinfo=SHANGHAI_TZ,
    )
    return {
        "生效日期": effective_on.isoformat(),
        "交易所": "郑商所",
        "代码": root_symbol.upper(),
        "涨跌停板幅度": limit_rate_percent,
        "最小变动价位": price_tick,
        "特殊合约参数调整": None,
        "_czce_official_parameter_source_date": parameter_date.isoformat(),
        "_czce_official_parameter_known_at": known_at.isoformat(),
        "_czce_official_margin_rate": margin_rate_percent / 100.0,
    }


def _czce_official_vendor_row(
    *,
    local_contract: str,
    root_symbol: str,
    source_date: date,
    effective_on: date,
    price_tick: float,
    expected_settlement: float,
    parameters: pd.DataFrame,
) -> dict[str, object]:
    row = _exact_contract_row(
        local_contract,
        parameters,
        code_column="symbol",
    )
    rule = _czce_official_trade_rule(
        local_contract=local_contract,
        root_symbol=root_symbol,
        effective_on=effective_on,
        parameter_date=source_date,
        price_tick=price_tick,
        expected_settlement=expected_settlement,
        parameters=parameters,
    )
    settlement = _positive_float(
        str(row.get("settle_price", "")).replace(",", ""),
        "CZCE_OFFICIAL_SETTLEMENT",
    )
    limit_rate = _positive_float(
        rule.get("涨跌停板幅度"),
        "CZCE_OFFICIAL_LIMIT_RATE",
    ) / 100.0
    margin_rate = _positive_float(
        rule.get("_czce_official_margin_rate"),
        "CZCE_OFFICIAL_MARGIN_RATE",
    )
    fee_type = str(row.get("fee_type", "")).strip()

    def fee_expression(value: object, field: str) -> str:
        try:
            amount = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError) as exc:
            raise MetadataBuildError(
                "INVALID_CZCE_OFFICIAL_FEE",
                f"{local_contract} {source_date} {field}: {value!r}",
            ) from exc
        if not math.isfinite(amount) or amount < 0:
            raise MetadataBuildError(
                "INVALID_CZCE_OFFICIAL_FEE",
                f"{local_contract} {source_date} {field}: {value!r}",
            )
        rendered = f"{amount:g}"
        if fee_type == "绝对值":
            return f"{rendered}元"
        if fee_type == "比例值":
            return f"{rendered}/万分之"
        raise MetadataBuildError(
            "UNSUPPORTED_CZCE_OFFICIAL_FEE_TYPE",
            f"{local_contract} {source_date}: {fee_type!r}",
        )

    published_at = datetime.combine(
        source_date,
        time(15, 30),
        tzinfo=SHANGHAI_TZ,
    ).isoformat()
    standard_fee = fee_expression(row.get("trade_fee"), "TRADE_FEE")
    return {
        "日期": source_date.isoformat(),
        "合约品种": root_symbol.upper(),
        "合约代码": str(row.get("symbol", "")).strip(),
        "手续费公布时间": published_at,
        "价格公布时间": published_at,
        "现价": settlement,
        "涨停板": _ceil_tick(
            settlement * (1.0 + limit_rate),
            price_tick,
        ),
        "跌停板": _floor_tick(
            settlement * (1.0 - limit_rate),
            price_tick,
        ),
        "保证金/买开": f"{margin_rate * 100:g}%",
        "保证金/卖开": f"{margin_rate * 100:g}%",
        "开仓": standard_fee,
        "平昨": standard_fee,
        "平今": fee_expression(
            row.get("close_today_fee"),
            "CLOSE_TODAY_FEE",
        ),
        "_czce_official_vendor": True,
    }


def _carry_forward_trade_rule(
    *,
    rule_row: Mapping[str, object],
    local_contract: str,
    root_symbol: str,
    exchange: str,
    source_date: date,
    effective_on: date,
    price_tick: float,
    prior_settlement: float,
    validation_vendor_row: Mapping[str, object],
    official_parameter_row: Mapping[str, object] | None = None,
) -> dict[str, object]:
    exchange_token = _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper())
    if exchange_token not in {"CZCE", "DCE", "GFEX", "INE", "SHFE"}:
        raise MetadataBuildError(
            "UNSUPPORTED_RULE_CARRY_FORWARD_EXCHANGE",
            f"{local_contract} {effective_on}: {exchange}",
        )
    if source_date >= effective_on:
        raise MetadataBuildError(
            "INVALID_RULE_CARRY_FORWARD_RANGE",
            f"{local_contract}: {source_date}..{effective_on}",
        )
    _require_source_identity(
        local_contract,
        source_date,
        validation_vendor_row,
        code_key="合约代码",
        date_key="日期",
        source="JIN10_LATE_LIMIT_VALIDATION",
    )
    validation_settlement = _positive_float(
        validation_vendor_row.get("现价"),
        "VALIDATION_SETTLEMENT",
    )
    if abs(validation_settlement - prior_settlement) > price_tick / 2.0 + 1e-12:
        raise MetadataBuildError(
            "CARRY_FORWARD_SETTLEMENT_MISMATCH",
            f"{local_contract} {effective_on}: validation={validation_settlement} "
            f"prior={prior_settlement}",
        )
    rate = _trade_rule_limit_rate(
        rule_row,
        local_contract=local_contract,
        root_symbol=root_symbol,
        exchange=exchange,
        effective_on=source_date,
        price_tick=price_tick,
    )
    if exchange_token in {"DCE", "GFEX"}:
        amplitude = _floor_tick(prior_settlement * rate, price_tick)
        expected_up = prior_settlement + amplitude
        expected_down = prior_settlement - amplitude
    elif exchange_token == "CZCE":
        expected_up = _ceil_tick(
            prior_settlement * (1.0 + rate),
            price_tick,
        )
        expected_down = _floor_tick(
            prior_settlement * (1.0 - rate),
            price_tick,
        )
    else:
        expected_up = _floor_tick(
            prior_settlement * (1.0 + rate),
            price_tick,
        )
        expected_down = _floor_tick(
            prior_settlement * (1.0 - rate),
            price_tick,
        )
    observed_up = _positive_float(validation_vendor_row.get("涨停板"), "LIMIT_UP")
    observed_down = _positive_float(
        validation_vendor_row.get("跌停板"),
        "LIMIT_DOWN",
    )
    tolerance = price_tick * 1e-7
    if (
        abs(expected_up - observed_up) > tolerance
        or abs(expected_down - observed_down) > tolerance
    ):
        if exchange_token in {"INE", "SHFE"} and official_parameter_row is not None:
            _validate_shfe_official_settlement(
                official_parameter_row,
                local_contract=local_contract,
                source_date=source_date,
                settlement=prior_settlement,
                price_tick=price_tick,
                source_name=exchange_token,
            )
            published_rate = _validate_shfe_published_limit_interval(
                local_contract=local_contract,
                source_date=source_date,
                settlement=prior_settlement,
                limit_up=observed_up,
                limit_down=observed_down,
                price_tick=price_tick,
            )
            result = dict(rule_row)
            result["_carried_forward_to"] = effective_on.isoformat()
            result["_validation_source_date"] = source_date.isoformat()
            result["_validated_published_limit_rate"] = published_rate
            result["_published_limit_validation"] = (
                f"{exchange_token}_OFFICIAL_JS_JIN10"
            )
            return result
        raise MetadataBuildError(
            "CARRY_FORWARD_LIMIT_VALIDATION_FAILED",
            f"{local_contract} {effective_on}: expected={expected_down}/"
            f"{expected_up} observed={observed_down}/{observed_up}",
        )
    result = dict(rule_row)
    result["_carried_forward_to"] = effective_on.isoformat()
    result["_validation_source_date"] = source_date.isoformat()
    return result


def _validate_shfe_official_settlement(
    row: Mapping[str, object],
    *,
    local_contract: str,
    source_date: date,
    settlement: float,
    price_tick: float,
    source_name: str = "SHFE",
) -> None:
    _require_source_identity(
        local_contract,
        source_date,
        row,
        code_key="INSTRUMENTID",
        date_key="_source_date",
        source=f"{source_name}_OFFICIAL_JS",
    )
    official_settlement = _positive_float(
        row.get("SETTLEMENTPRICE"),
        f"{source_name}_OFFICIAL_SETTLEMENT",
    )
    if abs(official_settlement - settlement) > price_tick / 2.0 + 1e-12:
        raise MetadataBuildError(
            f"{source_name}_OFFICIAL_SETTLEMENT_MISMATCH",
            f"{local_contract} {source_date}: official={official_settlement} "
            f"expected={settlement}",
        )


def _shfe_official_fee_expression(
    ratio_value: object,
    unit_value: object,
    field: str,
    source_name: str = "SHFE",
) -> str:
    try:
        ratio = float(ratio_value or 0.0)
        unit = float(unit_value or 0.0)
    except (TypeError, ValueError) as exc:
        raise MetadataBuildError(
            f"INVALID_{source_name}_OFFICIAL_FEE",
            f"{field}: {ratio_value!r}/{unit_value!r}",
        ) from exc
    if not math.isfinite(ratio) or not math.isfinite(unit) or min(ratio, unit) < 0:
        raise MetadataBuildError(
            f"INVALID_{source_name}_OFFICIAL_FEE",
            f"{field}: {ratio}/{unit}",
        )
    if ratio > 0 and unit > 0:
        raise MetadataBuildError(
            f"AMBIGUOUS_{source_name}_OFFICIAL_FEE",
            f"{field}: {ratio}/{unit}",
        )
    if ratio > 0:
        return f"{ratio * 10:g}/万分之"
    return f"{unit:g}元"


def _shfe_official_vendor_row(
    row: Mapping[str, object],
    *,
    local_contract: str,
    source_date: date,
    price_tick: float,
    limit_rate: float,
    source_name: str = "SHFE",
) -> dict[str, object]:
    settlement = _positive_float(
        row.get("SETTLEMENTPRICE"),
        f"{source_name}_OFFICIAL_SETTLEMENT",
    )
    _validate_shfe_official_settlement(
        row,
        local_contract=local_contract,
        source_date=source_date,
        settlement=settlement,
        price_tick=price_tick,
        source_name=source_name,
    )
    if not 0 < limit_rate < 0.5:
        raise MetadataBuildError(
            f"INVALID_{source_name}_OFFICIAL_LIMIT_RATE",
            f"{local_contract} {source_date}: {limit_rate}",
        )
    margin_long = _rate_float(
        row.get("SPECLONGMARGINRATIO"),
        f"{source_name}_OFFICIAL_LONG_MARGIN",
    )
    margin_short = _rate_float(
        row.get("SPECSHORTMARGINRATIO"),
        f"{source_name}_OFFICIAL_SHORT_MARGIN",
    )
    standard_ratio = row.get("TRADEFEERATIO", 0.0)
    standard_unit = row.get("TRADEFEEUNIT", 0.0)
    close_today_ratio = row.get("TTRADEFEERATIO")
    close_today_unit = row.get("TTRADEFEEUNIT")
    if close_today_ratio is None or str(close_today_ratio).strip() == "":
        close_today_ratio = standard_ratio
    if close_today_unit is None or str(close_today_unit).strip() == "":
        close_today_unit = standard_unit
    published_at = datetime.combine(
        source_date,
        time(15, 30),
        tzinfo=SHANGHAI_TZ,
    ).isoformat()
    standard_fee = _shfe_official_fee_expression(
        standard_ratio,
        standard_unit,
        f"{source_name}_STANDARD_FEE",
        source_name=source_name,
    )
    return {
        "日期": source_date.isoformat(),
        "合约代码": str(row.get("INSTRUMENTID", "")).strip(),
        "手续费公布时间": published_at,
        "价格公布时间": published_at,
        "现价": settlement,
        "涨停板": _floor_tick(settlement * (1.0 + limit_rate), price_tick),
        "跌停板": _floor_tick(settlement * (1.0 - limit_rate), price_tick),
        "保证金/买开": f"{margin_long * 100:g}%",
        "保证金/卖开": f"{margin_short * 100:g}%",
        "开仓": standard_fee,
        "平昨": standard_fee,
        "平今": _shfe_official_fee_expression(
            close_today_ratio,
            close_today_unit,
            f"{source_name}_CLOSE_TODAY_FEE",
            source_name=source_name,
        ),
    }


def _load_shfe_official_vendor(
    *,
    source_client: MetadataSourceClient,
    local_contract: str,
    root_symbol: str,
    exchange: str,
    source_date: date,
    trade_date: date,
    price_tick: float,
    parameter_cache: dict[date, pd.DataFrame],
    rule_cache: dict[date, pd.DataFrame],
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    datetime,
    datetime,
]:
    source_name = _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper())
    fetch_method = {
        "INE": "fetch_ine_settlement_parameters",
        "SHFE": "fetch_shfe_settlement_parameters",
    }.get(source_name)
    fetch_parameters = getattr(
        source_client,
        fetch_method or "",
        None,
    )
    fetch_rules = getattr(source_client, "fetch_trading_rules", None)
    if not callable(fetch_parameters) or not callable(fetch_rules):
        raise MetadataBuildError(
            f"MISSING_{source_name}_OFFICIAL_SOURCE_CLIENT",
            f"{local_contract} {trade_date}",
        )
    parameters = parameter_cache.get(source_date)
    if parameters is None:
        parameters = fetch_parameters(source_date)
        parameter_cache[source_date] = parameters
    parameter_row = _exact_contract_row(
        local_contract,
        parameters,
        code_column="INSTRUMENTID",
    )
    rules = rule_cache.get(trade_date)
    if rules is None:
        try:
            rules = fetch_rules(trade_date)
        except MetadataBuildError as exc:
            raise MetadataBuildError(
                exc.code,
                f"{local_contract} {trade_date}: {exc.detail}",
            ) from exc
        rule_cache[trade_date] = rules
    rule_row = _exact_trade_rule_row(root_symbol, exchange, rules)
    limit_rate = _trade_rule_limit_rate(
        rule_row,
        local_contract=local_contract,
        root_symbol=root_symbol,
        exchange=exchange,
        effective_on=trade_date,
        price_tick=price_tick,
    )
    vendor_row = _shfe_official_vendor_row(
        parameter_row,
        local_contract=local_contract,
        source_date=source_date,
        price_tick=price_tick,
        limit_rate=limit_rate,
        source_name=source_name,
    )
    price_known_at = _published_at(
        vendor_row.get("价格公布时间"),
        source_date,
        "PRICE_PUBLISHED_AT",
    )
    fee_known_at = _published_at(
        vendor_row.get("手续费公布时间"),
        source_date,
        "FEE_PUBLISHED_AT",
    )
    return vendor_row, parameter_row, rule_row, price_known_at, fee_known_at


def _validate_shfe_published_limit_interval(
    *,
    local_contract: str,
    source_date: date,
    settlement: float,
    limit_up: float,
    limit_down: float,
    price_tick: float,
) -> float:
    tolerance = price_tick * 1e-7
    if not limit_down < settlement < limit_up:
        raise MetadataBuildError(
            "INVALID_SHFE_PUBLISHED_LIMITS",
            f"{local_contract} {source_date}: {limit_down}/{settlement}/{limit_up}",
        )
    if (
        abs(_floor_tick(limit_up, price_tick) - limit_up) > tolerance
        or abs(_floor_tick(limit_down, price_tick) - limit_down) > tolerance
    ):
        raise MetadataBuildError(
            "SHFE_PUBLISHED_LIMIT_TICK_MISMATCH",
            f"{local_contract} {source_date}: {limit_down}/{limit_up}",
        )
    lower_rate = max(
        limit_up / settlement - 1.0,
        1.0 - (limit_down + price_tick) / settlement,
    )
    upper_rate = min(
        (limit_up + price_tick) / settlement - 1.0,
        1.0 - limit_down / settlement,
    )
    if (
        lower_rate <= 0
        or upper_rate >= 0.5
        or lower_rate > upper_rate + 1e-12
    ):
        raise MetadataBuildError(
            "SHFE_PUBLISHED_LIMIT_INTERVAL_MISMATCH",
            f"{local_contract} {source_date}: {lower_rate}..{upper_rate}",
        )
    witness_rate = (lower_rate + upper_rate) / 2.0
    if (
        abs(
            _floor_tick(settlement * (1.0 + witness_rate), price_tick)
            - limit_up
        )
        > tolerance
        or abs(
            _floor_tick(settlement * (1.0 - witness_rate), price_tick)
            - limit_down
        )
        > tolerance
    ):
        raise MetadataBuildError(
            "SHFE_PUBLISHED_LIMIT_WITNESS_FAILED",
            f"{local_contract} {source_date}: {witness_rate}",
        )
    return witness_rate


def _validate_czce_published_limit_interval(
    *,
    local_contract: str,
    source_date: date,
    settlement: float,
    limit_up: float,
    limit_down: float,
    price_tick: float,
) -> float:
    tolerance = price_tick * 1e-7
    if not limit_down < settlement < limit_up:
        raise MetadataBuildError(
            "INVALID_CZCE_PUBLISHED_LIMITS",
            f"{local_contract} {source_date}: {limit_down}/{settlement}/{limit_up}",
        )
    if (
        abs(_floor_tick(limit_up, price_tick) - limit_up) > tolerance
        or abs(_floor_tick(limit_down, price_tick) - limit_down) > tolerance
    ):
        raise MetadataBuildError(
            "CZCE_PUBLISHED_LIMIT_TICK_MISMATCH",
            f"{local_contract} {source_date}: {limit_down}/{limit_up}",
        )
    lower_rate = max(
        (limit_up - price_tick) / settlement - 1.0,
        1.0 - (limit_down + price_tick) / settlement,
    )
    upper_rate = min(
        limit_up / settlement - 1.0,
        1.0 - limit_down / settlement,
    )
    if lower_rate <= 0 or upper_rate >= 0.5 or lower_rate >= upper_rate:
        raise MetadataBuildError(
            "CZCE_PUBLISHED_LIMIT_INTERVAL_MISMATCH",
            f"{local_contract} {source_date}: {lower_rate}..{upper_rate}",
        )
    witness_rate = (lower_rate + upper_rate) / 2.0
    if (
        abs(
            _ceil_tick(settlement * (1.0 + witness_rate), price_tick)
            - limit_up
        )
        > tolerance
        or abs(
            _floor_tick(settlement * (1.0 - witness_rate), price_tick)
            - limit_down
        )
        > tolerance
    ):
        raise MetadataBuildError(
            "CZCE_PUBLISHED_LIMIT_WITNESS_FAILED",
            f"{local_contract} {source_date}: {witness_rate}",
        )
    return witness_rate


def _validate_dce_published_amplitude_interval(
    *,
    local_contract: str,
    source_date: date,
    settlement: float,
    limit_up: float,
    limit_down: float,
    price_tick: float,
    source_name: str = "DCE",
) -> float:
    tolerance = price_tick * 1e-7
    if not limit_down < settlement < limit_up:
        raise MetadataBuildError(
            f"INVALID_{source_name}_PUBLISHED_LIMITS",
            f"{local_contract} {source_date}: {limit_down}/{settlement}/{limit_up}",
        )
    up_amplitude = limit_up - settlement
    down_amplitude = settlement - limit_down
    if abs(up_amplitude - down_amplitude) > tolerance:
        raise MetadataBuildError(
            f"{source_name}_PUBLISHED_LIMIT_ASYMMETRY",
            f"{local_contract} {source_date}: {down_amplitude}/{up_amplitude}",
        )
    if abs(_floor_tick(up_amplitude, price_tick) - up_amplitude) > tolerance:
        raise MetadataBuildError(
            f"{source_name}_PUBLISHED_AMPLITUDE_TICK_MISMATCH",
            f"{local_contract} {source_date}: {up_amplitude}",
        )
    lower_rate = up_amplitude / settlement
    upper_rate = (up_amplitude + price_tick) / settlement
    if lower_rate <= 0 or upper_rate >= 0.5 or lower_rate >= upper_rate:
        raise MetadataBuildError(
            f"{source_name}_PUBLISHED_AMPLITUDE_INTERVAL_MISMATCH",
            f"{local_contract} {source_date}: {lower_rate}..{upper_rate}",
        )
    witness_rate = (lower_rate + upper_rate) / 2.0
    if (
        abs(
            _floor_tick(settlement * witness_rate, price_tick)
            - up_amplitude
        )
        > tolerance
    ):
        raise MetadataBuildError(
            f"{source_name}_PUBLISHED_AMPLITUDE_WITNESS_FAILED",
            f"{local_contract} {source_date}: {witness_rate}",
        )
    return witness_rate


def _rebase_stale_vendor_limits(
    *,
    local_contract: str,
    root_symbol: str,
    exchange: str,
    prior_open_date: date,
    trade_date: date,
    price_tick: float,
    prior_settlement: float,
    vendor_settlement: float,
    vendor_row: Mapping[str, object],
    prior_trade_rule_row: Mapping[str, object],
    current_trade_rule_row: Mapping[str, object],
) -> tuple[float, float, float, str]:
    exchange_token = _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper())
    if exchange_token not in {"CZCE", "DCE", "GFEX", "INE", "SHFE"}:
        raise MetadataBuildError(
            "UNSUPPORTED_LIMIT_REBASE_EXCHANGE",
            f"{local_contract} {trade_date}: {exchange}",
        )
    current_rate = _trade_rule_limit_rate(
        current_trade_rule_row,
        local_contract=local_contract,
        root_symbol=root_symbol,
        exchange=exchange,
        effective_on=trade_date,
        price_tick=price_tick,
    )
    vendor_limit_up = _positive_float(vendor_row.get("涨停板"), "LIMIT_UP")
    vendor_limit_down = _positive_float(vendor_row.get("跌停板"), "LIMIT_DOWN")
    if exchange_token in {"DCE", "GFEX"}:
        _validate_dce_published_amplitude_interval(
            local_contract=local_contract,
            source_date=prior_open_date,
            settlement=vendor_settlement,
            limit_up=vendor_limit_up,
            limit_down=vendor_limit_down,
            price_tick=price_tick,
            source_name=exchange_token,
        )
        calibrated_up = vendor_limit_up
        calibrated_down = vendor_limit_down
        current_amplitude = _floor_tick(
            prior_settlement * current_rate,
            price_tick,
        )
        current_up = prior_settlement + current_amplitude
        current_down = prior_settlement - current_amplitude
        rounding_name = (
            f"{exchange_token}_PUBLISHED_INTERVAL_FLOOR_AMPLITUDE"
        )
    elif exchange_token == "CZCE":
        _validate_czce_published_limit_interval(
            local_contract=local_contract,
            source_date=prior_open_date,
            settlement=vendor_settlement,
            limit_up=vendor_limit_up,
            limit_down=vendor_limit_down,
            price_tick=price_tick,
        )
        calibrated_up = vendor_limit_up
        calibrated_down = vendor_limit_down
        current_up = _ceil_tick(
            prior_settlement * (1.0 + current_rate),
            price_tick,
        )
        current_down = _floor_tick(
            prior_settlement * (1.0 - current_rate),
            price_tick,
        )
        rounding_name = "CZCE_PUBLISHED_INTERVAL_OUTWARD_ABSOLUTE"
    else:
        _validate_shfe_published_limit_interval(
            local_contract=local_contract,
            source_date=prior_open_date,
            settlement=vendor_settlement,
            limit_up=vendor_limit_up,
            limit_down=vendor_limit_down,
            price_tick=price_tick,
        )
        calibrated_up = vendor_limit_up
        calibrated_down = vendor_limit_down
        current_up = _floor_tick(
            prior_settlement * (1.0 + current_rate),
            price_tick,
        )
        current_down = _floor_tick(
            prior_settlement * (1.0 - current_rate),
            price_tick,
        )
        rounding_name = (
            f"{exchange_token}_PUBLISHED_INTERVAL_FLOOR_ABSOLUTE"
        )
    tolerance = price_tick * 1e-7
    if (
        abs(calibrated_up - vendor_limit_up) > tolerance
        or abs(calibrated_down - vendor_limit_down) > tolerance
    ):
        raise MetadataBuildError(
            "UNSUPPORTED_LIMIT_ROUNDING",
            f"{local_contract} {prior_open_date}: published="
            f"{vendor_limit_down}/{vendor_limit_up} {rounding_name.lower()}="
            f"{calibrated_down}/{calibrated_up}",
        )
    rounding_rule = f"GTJA_EFFECTIVE_RATE_JIN10_{rounding_name}_REBASED"
    if current_trade_rule_row.get("_carried_forward_to") is not None:
        rounding_rule += "_CARRY_FORWARD_VALIDATED"
    return (
        current_rate,
        current_up,
        current_down,
        rounding_rule,
    )


def _finish_daily_reconciliation(
    *,
    local_contract: str,
    root_symbol: str,
    exchange: str,
    trade_date: date,
    prior_open_date: date,
    price_tick: float,
    current_settlement_row: Mapping[str, object],
    prior_settlement_row: Mapping[str, object],
    vendor_row: Mapping[str, object],
    vendor_source_date: date,
    prior_trade_rule_row: Mapping[str, object] | None,
    current_trade_rule_row: Mapping[str, object] | None,
    prior_shfe_parameter_row: Mapping[str, object] | None,
    current_shfe_parameter_row: Mapping[str, object] | None,
    session_open: datetime,
    price_known_at: datetime,
    fee_known_at: datetime,
    roll_entry_validation: bool,
    fee_reference_contract: str | None,
    shfe_official_vendor_validation: bool,
    shfe_late_limit_validation_date: date | None,
) -> ReconciledDailyMechanics:
    exchange_token = _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper())
    uses_historical_fee_mirror = vendor_row.get("_historical_fee_mirror") is True
    uses_target_close_roll_fee = (
        vendor_row.get("_dce_target_close_roll_fee_validation") is True
    )
    uses_post_roll_night_fee = (
        vendor_row.get("_dce_post_roll_night_fee_validation") is True
    )
    assumed_runtime_default = vendor_row.get(
        "_assumed_runtime_default_reason"
    )
    uses_czce_official_vendor = vendor_row.get("_czce_official_vendor") is True
    _require_source_identity(
        local_contract,
        prior_open_date,
        prior_settlement_row,
        code_key="ts_code",
        date_key="trade_date",
        source="TUSHARE_PRIOR_SETTLEMENT",
    )
    _require_source_identity(
        local_contract,
        vendor_source_date,
        vendor_row,
        code_key="合约代码",
        date_key="日期",
        source=(
            f"{exchange_token}_OFFICIAL_PRIOR_PARAMETERS"
            if shfe_official_vendor_validation
            else (
                "CZCE_OFFICIAL_PRIOR_PARAMETERS"
                if uses_czce_official_vendor
                else (
                    "CHECKED_IN_HISTORICAL_FEE_MIRROR"
                    if uses_historical_fee_mirror
                    else (
                        "JIN10_TARGET_CLOSE_ROLL_FEE_VALIDATION"
                        if uses_target_close_roll_fee
                        else "JIN10_PRIOR_OPEN_PARAMETERS"
                    )
                )
            )
        ),
    )

    current_settlement = _positive_float(
        current_settlement_row.get("settle"), "CURRENT_SETTLEMENT"
    )
    prior_settlement = _positive_float(
        prior_settlement_row.get("settle"), "PRIOR_SETTLEMENT"
    )
    current_settlement_source = str(
        current_settlement_row.get("_source_table", "tushare_fut_settle")
    )
    prior_settlement_source = str(
        prior_settlement_row.get("_source_table", "tushare_fut_settle")
    )
    supported_settlement_sources = {
        "tushare_fut_daily",
        "tushare_fut_settle",
    }
    observed_sources = {current_settlement_source, prior_settlement_source}
    if not observed_sources.issubset(supported_settlement_sources):
        raise MetadataBuildError(
            "UNSUPPORTED_SETTLEMENT_SOURCE",
            f"{local_contract} {trade_date}: {sorted(observed_sources)}",
        )
    if current_settlement_source == "tushare_fut_daily":
        daily_pre_settlement = _positive_float(
            current_settlement_row.get("pre_settle"),
            "DAILY_PRE_SETTLEMENT",
        )
        if abs(daily_pre_settlement - prior_settlement) > (
            price_tick / 2.0 + 1e-12
        ):
            raise MetadataBuildError(
                "DAILY_PRE_SETTLEMENT_MISMATCH",
                f"{local_contract} {trade_date}: daily={daily_pre_settlement} "
                f"prior={prior_settlement}",
            )
    vendor_settlement = _positive_float(vendor_row.get("现价"), "VENDOR_SETTLEMENT")
    uses_daily_quote = "tushare_fut_daily" in observed_sources
    if shfe_official_vendor_validation:
        source_family = f"{exchange_token}_OFFICIAL_JS_TUSHARE"
    elif uses_czce_official_vendor:
        source_family = "CZCE_OFFICIAL_TUSHARE"
    elif uses_historical_fee_mirror:
        source_family = "TUSHARE_GTJA"
    elif uses_target_close_roll_fee:
        source_family = "TUSHARE_JIN10_GTJA"
    elif uses_daily_quote:
        source_family = "TUSHARE_FUT_DAILY_JIN10"
    else:
        source_family = "EXCHANGE_PARAMETER_MIRROR_TUSHARE_JIN10"
    source = f"{exchange}_{source_family}_RECONCILED"
    current_source_reference = (
        f"tushare:fut_daily:{local_contract}:{trade_date:%Y%m%d}"
        if current_settlement_source == "tushare_fut_daily"
        else f"tushare:fut_settle:{trade_date:%Y%m%d}"
    )
    prior_source_reference = (
        f"tushare:fut_daily:{local_contract}:{prior_open_date:%Y%m%d}"
        if prior_settlement_source == "tushare_fut_daily"
        else f"tushare:fut_settle:{prior_open_date:%Y%m%d}"
    )
    vendor_source_reference = (
        f"{exchange_token.lower()}:js:{vendor_source_date:%Y%m%d}"
        if shfe_official_vendor_validation
        else (
            f"czce:FutureDataClearParams:{vendor_source_date:%Y%m%d}"
            if uses_czce_official_vendor
            else (
                str(vendor_row.get("_historical_fee_source_reference"))
                if uses_historical_fee_mirror
                else (
                    str(
                        vendor_row.get(
                            "_dce_target_close_roll_source_reference"
                        )
                    )
                    if uses_target_close_roll_fee
                    else f"jin10:futures_comm:{vendor_source_date:%Y%m%d}"
                )
            )
        )
    )
    source_reference = (
        f"{current_source_reference}|{prior_source_reference}|"
        f"{vendor_source_reference}"
    )
    settlements_match = abs(prior_settlement - vendor_settlement) <= (
        price_tick / 2.0 + 1e-12
    )
    uses_published_official_roll = (
        exchange_token in {"INE", "SHFE"}
        and roll_entry_validation
        and settlements_match
        and prior_shfe_parameter_row is not None
        and current_shfe_parameter_row is not None
    )
    if uses_published_official_roll:
        prior_parameter_date = _parse_date(
            prior_shfe_parameter_row.get("_source_date"),
            f"{exchange_token}_PRIOR_PARAMETER_DATE",
        )
        current_parameter_date = _parse_date(
            current_shfe_parameter_row.get("_source_date"),
            f"{exchange_token}_CURRENT_PARAMETER_DATE",
        )
        _validate_shfe_official_settlement(
            prior_shfe_parameter_row,
            local_contract=local_contract,
            source_date=prior_parameter_date,
            settlement=vendor_settlement,
            price_tick=price_tick,
            source_name=exchange_token,
        )
        _validate_shfe_official_settlement(
            current_shfe_parameter_row,
            local_contract=local_contract,
            source_date=current_parameter_date,
            settlement=prior_settlement,
            price_tick=price_tick,
            source_name=exchange_token,
        )
        limit_up = _positive_float(vendor_row.get("涨停板"), "LIMIT_UP")
        limit_down = _positive_float(vendor_row.get("跌停板"), "LIMIT_DOWN")
        limit_rate = max(
            limit_up / prior_settlement - 1.0,
            1.0 - limit_down / prior_settlement,
        )
        limit_rounding_rule = (
            f"PUBLISHED_ABSOLUTE_LIMITS_{exchange_token}_"
            "OFFICIAL_JS_ROLL_VALIDATED"
        )
        source = (
            f"{exchange}_{source_family}_{exchange_token}_"
            "OFFICIAL_JS_RECONCILED"
        )
        source_key = exchange_token.lower()
        source_reference += (
            f"|{source_key}:js:{prior_parameter_date:%Y%m%d}"
            f"|{source_key}:js:{current_parameter_date:%Y%m%d}"
        )
    elif roll_entry_validation or not settlements_match:
        if prior_trade_rule_row is None or current_trade_rule_row is None:
            raise MetadataBuildError(
                "SETTLEMENT_MISMATCH",
                f"{local_contract} {prior_open_date}: "
                f"tushare={prior_settlement} vendor={vendor_settlement}",
            )
        limit_rate, limit_up, limit_down, limit_rounding_rule = (
            _rebase_stale_vendor_limits(
            local_contract=local_contract,
            root_symbol=root_symbol,
            exchange=exchange,
            prior_open_date=vendor_source_date,
            trade_date=trade_date,
            price_tick=price_tick,
            prior_settlement=prior_settlement,
            vendor_settlement=vendor_settlement,
            vendor_row=vendor_row,
            prior_trade_rule_row=prior_trade_rule_row,
            current_trade_rule_row=current_trade_rule_row,
            )
        )
        source = f"{exchange}_{source_family}_GTJA_RECONCILED"
        prior_rule_date = pd.Timestamp(
            prior_trade_rule_row.get("生效日期")
        ).date()
        current_rule_date = pd.Timestamp(
            current_trade_rule_row.get("生效日期")
        ).date()
        prior_czce_parameter_date = prior_trade_rule_row.get(
            "_czce_official_parameter_source_date"
        )
        current_czce_parameter_date = current_trade_rule_row.get(
            "_czce_official_parameter_source_date"
        )
        prior_rule_reference = (
            f"czce:FutureDataClearParams:"
            f"{pd.Timestamp(prior_czce_parameter_date):%Y%m%d}"
            if prior_czce_parameter_date is not None
            else f"gtja:futures_rule:{prior_rule_date:%Y%m%d}"
        )
        current_rule_reference = (
            f"czce:FutureDataClearParams:"
            f"{pd.Timestamp(current_czce_parameter_date):%Y%m%d}"
            if current_czce_parameter_date is not None
            else f"gtja:futures_rule:{current_rule_date:%Y%m%d}"
        )
        source_reference += (
            f"|{prior_rule_reference}|{current_rule_reference}"
        )
        if current_czce_parameter_date is not None:
            source += "_CZCE_OFFICIAL_CLEARING_PARAMETERS"
        if (
            prior_shfe_parameter_row is not None
            and current_shfe_parameter_row is not None
        ):
            prior_parameter_date = _parse_date(
                prior_shfe_parameter_row.get("_source_date"),
                f"{exchange_token}_PRIOR_PARAMETER_DATE",
            )
            current_parameter_date = _parse_date(
                current_shfe_parameter_row.get("_source_date"),
                f"{exchange_token}_CURRENT_PARAMETER_DATE",
            )
            _validate_shfe_official_settlement(
                prior_shfe_parameter_row,
                local_contract=local_contract,
                source_date=prior_parameter_date,
                settlement=vendor_settlement,
                price_tick=price_tick,
                source_name=exchange_token,
            )
            _validate_shfe_official_settlement(
                current_shfe_parameter_row,
                local_contract=local_contract,
                source_date=current_parameter_date,
                settlement=prior_settlement,
                price_tick=price_tick,
                source_name=exchange_token,
            )
            source += f"_{exchange_token}_OFFICIAL_JS_FEE_VALIDATED"
            source_key = exchange_token.lower()
            source_reference += (
                f"|{source_key}:js:{prior_parameter_date:%Y%m%d}"
                f"|{source_key}:js:{current_parameter_date:%Y%m%d}"
            )
        validation_source_date = current_trade_rule_row.get(
            "_validation_source_date"
        )
        if validation_source_date is not None:
            source += "_CARRY_FORWARD_VALIDATED"
            source_reference += (
                f"|jin10:futures_comm:{pd.Timestamp(validation_source_date):%Y%m%d}"
                f":late_limit_validation"
            )
    else:
        limit_up = _positive_float(vendor_row.get("涨停板"), "LIMIT_UP")
        limit_down = _positive_float(vendor_row.get("跌停板"), "LIMIT_DOWN")
        if uses_historical_fee_mirror or uses_target_close_roll_fee:
            if exchange_token != "DCE" or current_trade_rule_row is None:
                raise MetadataBuildError(
                    "INVALID_DCE_DERIVED_FEE_CONTEXT",
                    f"{local_contract} {trade_date}",
                )
            limit_rate = _trade_rule_limit_rate(
                current_trade_rule_row,
                local_contract=local_contract,
                root_symbol=root_symbol,
                exchange=exchange,
                effective_on=trade_date,
                price_tick=price_tick,
            )
            amplitude = _floor_tick(
                prior_settlement * limit_rate,
                price_tick,
            )
            expected_up = prior_settlement + amplitude
            expected_down = prior_settlement - amplitude
            tolerance = price_tick * 1e-7
            if (
                abs(limit_up - expected_up) > tolerance
                or abs(limit_down - expected_down) > tolerance
            ):
                raise MetadataBuildError(
                    "DCE_DERIVED_FEE_LIMIT_MISMATCH",
                    f"{local_contract} {trade_date}: published={limit_down}/"
                    f"{limit_up} expected={expected_down}/{expected_up}",
                )
            rule_date = pd.Timestamp(
                current_trade_rule_row.get("生效日期")
            ).date()
            source += (
                f"_DCE_{root_symbol.upper()}_HISTORICAL_FEE_MIRROR"
                if uses_historical_fee_mirror
                else (
                    "_POST_ROLL_NIGHT_FEE_VALIDATED"
                    if uses_post_roll_night_fee
                    else (
                        "_ROLL_ENTRY_ASSUMED_RUNTIME_DEFAULT"
                        if assumed_runtime_default is not None
                        else "_ROLL_ENTRY_VALIDATED"
                    )
                )
            )
            source_reference += f"|gtja:futures_rule:{rule_date:%Y%m%d}"
            limit_rounding_rule = (
                "GTJA_EFFECTIVE_RATE_DCE_FLOOR_AMPLITUDE_"
                + (
                    "HISTORICAL_FEE_MIRROR"
                    if uses_historical_fee_mirror
                    else (
                        "POST_ROLL_NIGHT_FEE_VALIDATED"
                        if uses_post_roll_night_fee
                        else "TARGET_CLOSE_ROLL_VALIDATED"
                    )
                )
            )
        elif shfe_official_vendor_validation:
            if current_trade_rule_row is None:
                raise MetadataBuildError(
                    f"MISSING_{exchange_token}_OFFICIAL_TARGET_RULE",
                    f"{local_contract} {trade_date}",
                )
            limit_rate = _trade_rule_limit_rate(
                current_trade_rule_row,
                local_contract=local_contract,
                root_symbol=root_symbol,
                exchange=exchange,
                effective_on=trade_date,
                price_tick=price_tick,
            )
            expected_up = _floor_tick(
                prior_settlement * (1.0 + limit_rate),
                price_tick,
            )
            expected_down = _floor_tick(
                prior_settlement * (1.0 - limit_rate),
                price_tick,
            )
            tolerance = price_tick * 1e-7
            if (
                abs(limit_up - expected_up) > tolerance
                or abs(limit_down - expected_down) > tolerance
            ):
                raise MetadataBuildError(
                    f"{exchange_token}_OFFICIAL_TARGET_LIMIT_MISMATCH",
                    f"{local_contract} {trade_date}: published={limit_down}/"
                    f"{limit_up} expected={expected_down}/{expected_up}",
                )
            rule_date = pd.Timestamp(
                current_trade_rule_row.get("生效日期")
            ).date()
            source += "_OFFICIAL_CONTRACT_ENTRY"
            source_reference += f"|gtja:futures_rule:{rule_date:%Y%m%d}"
            if shfe_late_limit_validation_date is not None:
                source += "_JIN10_LATE_LIMIT_VALIDATED"
                source_reference += (
                    f"|jin10:futures_comm:"
                    f"{shfe_late_limit_validation_date:%Y%m%d}:late_limit_validation"
                )
            limit_rounding_rule = (
                f"GTJA_EFFECTIVE_RATE_{exchange_token}_"
                "OFFICIAL_JS_FLOOR_ABSOLUTE"
            )
        else:
            limit_rate = max(
                limit_up / prior_settlement - 1.0,
                1.0 - limit_down / prior_settlement,
            )
            limit_rounding_rule = (
                "CZCE_OFFICIAL_OUTWARD_ABSOLUTE_LIMITS"
                if uses_czce_official_vendor
                else "PUBLISHED_ABSOLUTE_LIMITS"
            )
    if roll_entry_validation:
        if not fee_reference_contract:
            raise MetadataBuildError(
                "MISSING_ROLL_FEE_REFERENCE",
                f"{local_contract} {trade_date}",
            )
        if assumed_runtime_default is None:
            source += "_ROLL_ENTRY_VALIDATED"
            source_reference += (
                f"|jin10:root_fee_reference:{fee_reference_contract}"
                f":validated_against:{local_contract}"
            )
        else:
            source += "_ROLL_ENTRY_ASSUMED_RUNTIME_DEFAULT"
            source_reference += (
                f"|assumption:current_contract_runtime_parameters:"
                f"{local_contract}"
            )
    if not limit_down < prior_settlement < limit_up:
        raise MetadataBuildError(
            "INVALID_PRICE_LIMITS",
            f"{local_contract} {trade_date}: {limit_down}/{prior_settlement}/{limit_up}",
        )

    fee_vendor_row = vendor_row
    uses_exchange_official_fee = False
    if (
        exchange_token in {"INE", "SHFE"}
        and current_shfe_parameter_row is not None
    ):
        current_parameter_date = _parse_date(
            current_shfe_parameter_row.get("_source_date"),
            f"{exchange_token}_CURRENT_PARAMETER_DATE",
        )
        _validate_shfe_official_settlement(
            current_shfe_parameter_row,
            local_contract=local_contract,
            source_date=current_parameter_date,
            settlement=prior_settlement,
            price_tick=price_tick,
            source_name=exchange_token,
        )
        fee_vendor_row = _shfe_official_vendor_row(
            current_shfe_parameter_row,
            local_contract=local_contract,
            source_date=current_parameter_date,
            price_tick=price_tick,
            limit_rate=limit_rate,
            source_name=exchange_token,
        )
        uses_exchange_official_fee = True
        fee_known_at = max(
            fee_known_at,
            _published_at(
                fee_vendor_row.get("手续费公布时间"),
                current_parameter_date,
                "FEE_PUBLISHED_AT",
            ),
        )
        official_fee_marker = f"_{exchange_token}_OFFICIAL_JS_FEE_VALIDATED"
        if official_fee_marker not in source:
            source += official_fee_marker
        official_fee_reference = (
            f"{exchange_token.lower()}:js:{current_parameter_date:%Y%m%d}"
        )
        if official_fee_reference not in source_reference:
            source_reference += f"|{official_fee_reference}"
    vendor_margin_long = _margin_rate(fee_vendor_row.get("保证金/买开"))
    vendor_margin_short = _margin_rate(fee_vendor_row.get("保证金/卖开"))
    margin_known_at = fee_known_at
    uses_exact_gtja_margin = (
        not uses_exchange_official_fee
        and exchange_token not in {"INE", "SHFE"}
        and current_trade_rule_row is not None
        and current_trade_rule_row.get("_carried_forward_to") is None
        and not pd.isna(current_trade_rule_row.get("交易保证金比例"))
    )
    if uses_exact_gtja_margin:
        margin_long = _trade_rule_margin_rate(
            current_trade_rule_row,
            local_contract=local_contract,
            root_symbol=root_symbol,
            exchange=exchange,
            effective_on=trade_date,
            price_tick=price_tick,
        )
        margin_short = margin_long
        margin_known_at = max(margin_known_at, session_open)
        source += "_GTJA_COMPANY_MARGIN"
        gtja_margin_reference = f"gtja:futures_rule:{trade_date:%Y%m%d}"
        if gtja_margin_reference not in source_reference:
            source_reference += f"|{gtja_margin_reference}"
    elif prior_settlement_source == "tushare_fut_settle" and not (
        uses_exchange_official_fee
    ):
        margin_long = _rate_float(
            prior_settlement_row.get("long_margin_rate"), "LONG_MARGIN"
        )
        margin_short = _rate_float(
            prior_settlement_row.get("short_margin_rate"), "SHORT_MARGIN"
        )
        margin_known_at = max(
            margin_known_at,
            datetime.combine(
                prior_open_date,
                time(15, 30),
                tzinfo=session_open.tzinfo,
            ),
        )
        margin_pairs = (
            ("long", vendor_margin_long, margin_long),
            ("short", vendor_margin_short, margin_short),
        )
        if vendor_source_date == prior_open_date and settlements_match:
            for side, vendor_margin, tushare_margin in margin_pairs:
                if abs(vendor_margin - tushare_margin) > 1e-9:
                    raise MetadataBuildError(
                        "MARGIN_MISMATCH",
                        f"{local_contract} {trade_date} {side}: "
                        f"tushare={tushare_margin} vendor={vendor_margin}",
                    )
    else:
        margin_long = vendor_margin_long
        margin_short = vendor_margin_short

    open_rate, open_cash = parse_fee_expression(fee_vendor_row.get("开仓"))
    close_rate, close_cash = parse_fee_expression(fee_vendor_row.get("平昨"))
    close_today_rate, close_today_cash = parse_fee_expression(
        fee_vendor_row.get("平今")
    )
    schedule_id = f"{exchange}-{local_contract.split('.', maxsplit=1)[0]}-{trade_date:%Y%m%d}"
    known_at = price_known_at.isoformat()
    fee_effective_from = session_open.isoformat()
    fee_known = margin_known_at.isoformat()
    settlement_known_at = datetime.combine(
        trade_date,
        time(15, 30),
        tzinfo=session_open.tzinfo,
    ).isoformat()
    pre_settlement_known_at = datetime.combine(
        prior_open_date,
        time(15, 30),
        tzinfo=session_open.tzinfo,
    ).isoformat()
    return ReconciledDailyMechanics(
        daily={
            "contract_code": local_contract,
            "exchange_trade_date": trade_date.isoformat(),
            "pre_settlement": prior_settlement,
            "settlement": current_settlement,
            "limit_rate": limit_rate,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "limit_rounding_rule": limit_rounding_rule,
            "source": source,
            "source_url_or_file": source_reference,
            "known_at": known_at,
            "fee_margin_schedule_id": schedule_id,
            "pre_settlement_known_at": pre_settlement_known_at,
            "settlement_known_at": settlement_known_at,
        },
        fee={
            "schedule_id": schedule_id,
            "root_symbol": root_symbol,
            "contract_code": local_contract,
            "effective_from": fee_effective_from,
            "effective_to": None,
            "margin_rate_long": margin_long,
            "margin_rate_short": margin_short,
            "open_fee_rate": open_rate,
            "close_fee_rate": close_rate,
            "close_today_fee_rate": close_today_rate,
            "fee_per_lot_open": open_cash,
            "fee_per_lot_close": close_cash,
            "fee_per_lot_close_today": close_today_cash,
            "source": source,
            "source_url_or_file": source_reference,
            "known_at": fee_known,
        },
    )


def _published_at(
    value: object,
    source_date: date,
    field: str,
) -> datetime:
    raw = "" if value is None else str(value).strip()
    if not raw:
        raise MetadataBuildError("MISSING_PUBLISHED_AT", field)
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", raw):
        parsed_time = time.fromisoformat(raw)
        return datetime.combine(source_date, parsed_time, tzinfo=SHANGHAI_TZ)
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise MetadataBuildError("INVALID_PUBLISHED_AT", f"{field}: {value!r}") from exc
    if pd.isna(parsed):
        raise MetadataBuildError("INVALID_PUBLISHED_AT", f"{field}: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize(SHANGHAI_TZ)
    else:
        parsed = parsed.tz_convert(SHANGHAI_TZ)
    result = parsed.to_pydatetime()
    if result.date() > source_date:
        raise MetadataBuildError(
            "PUBLISHED_AT_AFTER_SOURCE_DATE",
            f"{field}: source={source_date} observed={result.date()}",
        )
    return result


def _validated_roll_fee_reference(
    *,
    local_contract: str,
    source_date: date,
    vendor_frame: pd.DataFrame,
    session_open: datetime,
) -> tuple[datetime, str]:
    late_row = _exact_contract_row(
        local_contract,
        vendor_frame,
        code_column="合约代码",
    )
    local_code = local_contract.split(".", maxsplit=1)[0].upper()
    local_match = _CONTRACT.fullmatch(local_code)
    if local_match is None:
        raise MetadataBuildError("INVALID_LOCAL_CONTRACT", local_contract)
    root_symbol = local_match.group("root")
    late_fees = tuple(
        parse_fee_expression(late_row.get(column))
        for column in ("开仓", "平昨", "平今")
    )
    visible_same_root: list[tuple[datetime, str, tuple[tuple[float, float], ...]]] = []
    for row in vendor_frame.to_dict(orient="records"):
        rendered_code = str(row.get("合约代码", "")).strip()
        candidate_match = _CONTRACT.fullmatch(rendered_code.upper())
        if (
            candidate_match is None
            or candidate_match.group("root") != root_symbol
            or rendered_code.upper() == local_code
        ):
            continue
        fee_known_at = _published_at(
            row.get("手续费公布时间"),
            source_date,
            "FEE_PUBLISHED_AT",
        )
        if fee_known_at > session_open:
            continue
        fees = tuple(
            parse_fee_expression(row.get(column))
            for column in ("开仓", "平昨", "平今")
        )
        visible_same_root.append((fee_known_at, rendered_code, fees))
    if not visible_same_root:
        raise MetadataBuildError(
            "MISSING_ROLL_FEE_REFERENCE",
            f"{local_contract} before {session_open.isoformat()}",
        )
    matching = [item for item in visible_same_root if item[2] == late_fees]
    if not matching:
        raise MetadataBuildError(
            "ROLL_FEE_VALIDATION_FAILED",
            f"{local_contract} {source_date}: late={late_fees}",
        )
    fee_known_at, reference_contract, _ = max(
        matching,
        key=lambda item: (item[0], item[1]),
    )
    return fee_known_at, reference_contract


def _validated_historical_roll_fee_reference(
    *,
    local_contract: str,
    source_date: date,
    current_frame: pd.DataFrame,
    historical_source_date: date,
    historical_frame: pd.DataFrame,
    session_open: datetime,
) -> tuple[datetime, str]:
    local_code = local_contract.split(".", maxsplit=1)[0].strip().upper()
    current_row = _exact_contract_row(
        local_contract,
        current_frame,
        code_column="合约代码",
    )
    current_fees = tuple(
        parse_fee_expression(current_row.get(column))
        for column in ("开仓", "平昨", "平今")
    )
    historical_codes = historical_frame["合约代码"].astype(str).map(
        lambda value: value.split(".", maxsplit=1)[0].strip().upper()
    )
    historical_exact = historical_frame.loc[historical_codes.eq(local_code)]
    if len(historical_exact) == 1:
        historical_row = historical_exact.iloc[0].to_dict()
        historical_known_at = _published_at(
            historical_row.get("手续费公布时间"),
            historical_source_date,
            "FEE_PUBLISHED_AT",
        )
        if historical_known_at <= session_open:
            historical_fees = tuple(
                parse_fee_expression(historical_row.get(column))
                for column in ("开仓", "平昨", "平今")
            )
            if historical_fees == current_fees:
                rendered_contract = str(historical_row.get("合约代码", "")).strip()
                return (
                    historical_known_at,
                    f"{rendered_contract}@{historical_source_date:%Y%m%d}",
                )
    references = historical_frame.loc[historical_codes.ne(local_code)]
    combined = pd.concat([current_frame, references], ignore_index=True)
    known_at, reference_contract = _validated_roll_fee_reference(
        local_contract=local_contract,
        source_date=source_date,
        vendor_frame=combined,
        session_open=session_open,
    )
    return (
        known_at,
        f"{reference_contract}@{historical_source_date:%Y%m%d}",
    )


def _visible_source_time(
    known_at: datetime,
    session_open: datetime,
    field: str,
) -> datetime:
    if known_at.tzinfo is None or known_at.utcoffset() is None:
        raise MetadataBuildError("NAIVE_SOURCE_KNOWN_AT", field)
    visible = known_at.astimezone(SHANGHAI_TZ)
    if visible > session_open.astimezone(SHANGHAI_TZ):
        raise MetadataBuildError(
            "SOURCE_KNOWN_LATE",
            f"{field}: {visible.isoformat()} > {session_open.isoformat()}",
        )
    return visible


def _require_source_identity(
    local_contract: str,
    expected_date: date,
    row: Mapping[str, object],
    *,
    code_key: str,
    date_key: str,
    source: str,
) -> None:
    matched = match_vendor_contract(local_contract, [str(row.get(code_key, ""))])
    if not matched:
        raise MetadataBuildError("MISSING_CONTRACT", f"{source}: {local_contract}")
    observed_date = _parse_date(row.get(date_key), f"{source}_DATE")
    if observed_date != expected_date:
        raise MetadataBuildError(
            "SOURCE_DATE_MISMATCH",
            f"{source}: expected={expected_date} observed={observed_date}",
        )


def _parse_date(value: object, field: str) -> date:
    raw = str(value).strip()
    for format_string in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, format_string).date()
        except ValueError:
            continue
    raise MetadataBuildError("INVALID_SOURCE_DATE", f"{field}: {value!r}")


def _positive_float(value: object, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MetadataBuildError("INVALID_NUMERIC_FIELD", f"{field}: {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise MetadataBuildError("INVALID_NUMERIC_FIELD", f"{field}: {value!r}")
    return parsed


def _rate_float(value: object, field: str) -> float:
    parsed = _positive_float(value, field)
    if parsed > 1:
        raise MetadataBuildError("INVALID_RATE", f"{field}: {value!r}")
    return parsed


def _margin_rate(value: object) -> float:
    raw = "" if value is None else str(value).strip()
    if not raw.endswith("%"):
        raise MetadataBuildError("INVALID_MARGIN_RATE", repr(value))
    try:
        parsed = float(raw[:-1]) / 100.0
    except ValueError as exc:
        raise MetadataBuildError("INVALID_MARGIN_RATE", repr(value)) from exc
    if not math.isfinite(parsed) or not 0 < parsed <= 1:
        raise MetadataBuildError("INVALID_MARGIN_RATE", repr(value))
    return parsed


__all__ = [
    "ContractDate",
    "AuditedVendorMetadataClient",
    "BUILDER_SCHEMA_VERSION",
    "ExtensionBuildResult",
    "MetadataBuildError",
    "MetadataSourceClient",
    "MetadataPreparationResult",
    "ReconciledDailyMechanics",
    "build_extension_frames",
    "collect_contract_dates",
    "match_vendor_contract",
    "parse_fee_expression",
    "parse_price_tick",
    "prepare_execution_metadata",
    "reconcile_daily_mechanics",
    "session_template_from_description",
]
