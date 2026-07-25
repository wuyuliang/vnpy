"""Causal daily-bar preparation and optional Tushare input download."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RegimeInputs:
    """Downloaded, normalized source tables and their non-secret audit."""

    daily: pd.DataFrame
    factors: pd.DataFrame
    calendar: pd.DataFrame
    source_audit: dict[str, Any]


def _normalize_daily(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.rename(
        columns={
            "ts_code": "symbol",
            "trade_date": "datetime",
            "vol": "volume",
            "amount": "turnover",
        }
    ).copy()
    required = {"symbol", "datetime", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    columns = ["symbol", "datetime", "open", "high", "low", "close", "volume"]
    if "turnover" in frame.columns:
        columns.append("turnover")
    frame = frame.loc[:, columns]
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    if frame["symbol"].isna().any() or frame["symbol"].eq("").any():
        raise ValueError("daily symbol must be non-empty")
    frame["symbol"] = frame["symbol"].astype(str)
    dates = pd.to_datetime(frame["datetime"], errors="raise")
    if dates.isna().any():
        raise ValueError("daily datetime must not be missing")
    if dates.dt.tz is not None:
        raise ValueError("daily datetime must be timezone-naive")
    frame["datetime"] = dates.dt.normalize()
    if frame.duplicated(["symbol", "datetime"]).any():
        raise ValueError("daily data contains duplicate symbol/date rows")

    numeric = ["open", "high", "low", "close", "volume"]
    if "turnover" in frame.columns:
        numeric.append("turnover")
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if not np.isfinite(frame[numeric].to_numpy(dtype=float)).all():
        raise ValueError("daily numeric values must be finite")
    if frame[["open", "high", "low", "close"]].le(0).any(axis=None):
        raise ValueError("daily OHLC prices must be positive")
    if (
        frame["high"].lt(frame[["open", "close"]].max(axis=1)).any()
        or frame["low"].gt(frame[["open", "close"]].min(axis=1)).any()
        or frame["high"].lt(frame["low"]).any()
    ):
        raise ValueError("daily data contains invalid OHLC relationships")
    if frame["volume"].lt(0).any():
        raise ValueError("daily volume must be non-negative")
    if "turnover" in frame.columns and frame["turnover"].lt(0).any():
        raise ValueError("daily turnover must be non-negative")
    return frame.sort_values(["symbol", "datetime"], ignore_index=True)


def _normalize_factors(factors: pd.DataFrame) -> pd.DataFrame:
    frame = factors.rename(
        columns={
            "ts_code": "symbol",
            "trade_date": "factor_source_date",
            "datetime": "factor_source_date",
        }
    ).copy()
    required = {"symbol", "factor_source_date", "adj_factor"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"adjustment factors missing columns: {sorted(missing)}")
    frame = frame.loc[:, ["symbol", "factor_source_date", "adj_factor"]]
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    if frame["symbol"].isna().any() or frame["symbol"].eq("").any():
        raise ValueError("factor symbol must be non-empty")
    frame["symbol"] = frame["symbol"].astype(str)
    dates = pd.to_datetime(frame["factor_source_date"], errors="raise")
    if dates.isna().any():
        raise ValueError("factor dates must not be missing")
    if dates.dt.tz is not None:
        raise ValueError("factor dates must be timezone-naive")
    frame["factor_source_date"] = dates.dt.normalize()
    frame["adj_factor"] = pd.to_numeric(frame["adj_factor"], errors="coerce")
    if (
        frame["adj_factor"].isna().any()
        or not frame["adj_factor"].map(lambda value: isfinite(float(value))).all()
        or frame["adj_factor"].le(0).any()
    ):
        raise ValueError("adjustment factors must be finite and positive")
    if frame.duplicated(["symbol", "factor_source_date"]).any():
        raise ValueError("adjustment factors contain duplicate symbol/date rows")
    return frame.sort_values(
        ["symbol", "factor_source_date"],
        ignore_index=True,
    )


def build_causal_bars(
    daily: pd.DataFrame,
    factors: pd.DataFrame | None,
    price_adjustment_mode: str,
) -> pd.DataFrame:
    """Build raw or point-in-time adjusted bars without future factor access."""
    if price_adjustment_mode not in {"raw", "point_in_time_adjusted"}:
        raise ValueError(
            "price_adjustment_mode must be raw or point_in_time_adjusted"
        )
    bars = _normalize_daily(daily)
    if price_adjustment_mode == "raw":
        bars["adj_factor"] = 1.0
        bars["factor_source_date"] = pd.NaT
        bars["price_adjustment_mode"] = "raw"
        return bars
    if factors is None or factors.empty:
        raise ValueError("point-in-time adjustment factors are required")

    normalized_factors = _normalize_factors(factors)
    adjusted_groups: list[pd.DataFrame] = []
    for symbol, symbol_bars in bars.groupby("symbol", sort=True):
        symbol_factors = normalized_factors.loc[
            normalized_factors["symbol"].eq(symbol),
            ["factor_source_date", "adj_factor"],
        ]
        if symbol_factors.empty:
            raise ValueError(f"adjustment factors missing symbol: {symbol}")
        merged = pd.merge_asof(
            symbol_bars.sort_values("datetime"),
            symbol_factors.sort_values("factor_source_date"),
            left_on="datetime",
            right_on="factor_source_date",
            direction="backward",
            allow_exact_matches=True,
        )
        if merged["adj_factor"].isna().any():
            raise ValueError(
                f"initial adjustment factor is missing for symbol: {symbol}"
            )
        if (merged["factor_source_date"] > merged["datetime"]).any():
            raise ValueError("future adjustment factor detected")
        for column in ("open", "high", "low", "close"):
            merged[column] = merged[column] * merged["adj_factor"]
        merged["volume"] = merged["volume"] / merged["adj_factor"]
        adjusted_groups.append(merged)

    adjusted = pd.concat(adjusted_groups, ignore_index=True)
    adjusted["price_adjustment_mode"] = "point_in_time_adjusted"
    return adjusted.sort_values(["symbol", "datetime"], ignore_index=True)


def normalize_trade_calendar(calendar: pd.DataFrame) -> pd.DataFrame:
    """Normalize Tushare or local exchange calendar rows without dropping closes."""
    frame = calendar.rename(columns={"cal_date": "datetime"}).copy()
    required = {"datetime", "is_open"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"trade calendar missing columns: {sorted(missing)}")
    columns = ["datetime", "is_open"]
    if "exchange" in frame.columns:
        columns.append("exchange")
    frame = frame.loc[:, columns]
    dates = pd.to_datetime(frame["datetime"], errors="raise")
    if dates.isna().any():
        raise ValueError("trade calendar dates must not be missing")
    if dates.dt.tz is not None:
        raise ValueError("trade calendar dates must be timezone-naive")
    frame["datetime"] = dates.dt.normalize()
    open_values = frame["is_open"].astype("string").str.strip()
    if not open_values.isin(["0", "1", "False", "True", "false", "true"]).all():
        raise ValueError("trade calendar is_open must contain only 0 or 1")
    frame["is_open"] = open_values.isin(["1", "True", "true"]).astype(int)
    if frame["datetime"].duplicated().any():
        raise ValueError("trade calendar contains duplicate dates")
    return frame.sort_values("datetime", ignore_index=True)


def _frame_sha256(frame: pd.DataFrame) -> str:
    serialized = frame.to_csv(index=False, date_format="%Y-%m-%d").encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _date_range(frame: pd.DataFrame, column: str) -> dict[str, str | None]:
    if frame.empty:
        return {"minimum_date": None, "maximum_date": None}
    return {
        "minimum_date": str(pd.Timestamp(frame[column].min()).date()),
        "maximum_date": str(pd.Timestamp(frame[column].max()).date()),
    }


def _exchange_for_symbol(symbol: str) -> str:
    if symbol.endswith(".SZ"):
        return "SZSE"
    if symbol.endswith(".SH"):
        return "SSE"
    if symbol.endswith(".BJ"):
        return "BSE"
    raise ValueError("symbol must end in .SZ, .SH, or .BJ")


def download_regime_inputs(
    symbol: str,
    start_date: str,
    end_date: str,
    token: str | None = None,
) -> RegimeInputs:
    """Download one symbol's daily bars, factors, and exchange calendar."""
    try:
        import tushare as ts  # type: ignore
    except ImportError as exc:
        raise RuntimeError("tushare is required when download is enabled") from exc

    api_token = token or os.environ.get("TUSHARE_TOKEN")
    if not api_token:
        raise RuntimeError("TUSHARE_TOKEN is required when download is enabled")
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    exchange = _exchange_for_symbol(symbol)
    pro = ts.pro_api(api_token)
    query_start = start.strftime("%Y%m%d")
    query_end = end.strftime("%Y%m%d")

    raw_daily = pro.fund_daily(
        ts_code=symbol,
        start_date=query_start,
        end_date=query_end,
    )
    raw_factors = pro.fund_adj(
        ts_code=symbol,
        start_date=query_start,
        end_date=query_end,
    )
    calendar_end = (end + pd.Timedelta(days=30)).strftime("%Y%m%d")
    raw_calendar = pro.trade_cal(
        exchange=exchange,
        start_date=query_start,
        end_date=calendar_end,
    )
    if raw_daily.empty:
        raise RuntimeError(f"Tushare returned no fund_daily rows for {symbol}")
    if raw_factors.empty:
        raise RuntimeError(f"Tushare returned no fund_adj rows for {symbol}")
    if raw_calendar.empty:
        raise RuntimeError(f"Tushare returned no trade_cal rows for {exchange}")

    daily = _normalize_daily(raw_daily)
    daily = daily.loc[
        daily["symbol"].eq(symbol)
        & daily["datetime"].between(start, end, inclusive="both")
    ].reset_index(drop=True)
    factors = _normalize_factors(raw_factors)
    factors = factors.loc[
        factors["symbol"].eq(symbol)
        & factors["factor_source_date"].between(start, end, inclusive="both")
    ].rename(columns={"factor_source_date": "datetime"})
    factors = factors.reset_index(drop=True)
    calendar = normalize_trade_calendar(raw_calendar)
    if daily.empty or factors.empty:
        raise RuntimeError("Tushare data is empty after date and symbol filtering")

    source_audit = {
        "provider": "Tushare",
        "symbol": symbol,
        "requested_start_date": str(start.date()),
        "requested_end_date": str(end.date()),
        "daily": {
            "endpoint": "fund_daily",
            "row_count": len(daily),
            "sha256": _frame_sha256(daily),
            "turnover_unit": "Tushare fund_daily amount unit",
            **_date_range(daily, "datetime"),
        },
        "factors": {
            "endpoint": "fund_adj",
            "row_count": len(factors),
            "sha256": _frame_sha256(factors),
            **_date_range(factors, "datetime"),
        },
        "calendar": {
            "endpoint": "trade_cal",
            "exchange": exchange,
            "requested_end_date": str(pd.Timestamp(calendar_end).date()),
            "row_count": len(calendar),
            "sha256": _frame_sha256(calendar),
            **_date_range(calendar, "datetime"),
        },
    }
    return RegimeInputs(
        daily=daily,
        factors=factors,
        calendar=calendar,
        source_audit=source_audit,
    )
