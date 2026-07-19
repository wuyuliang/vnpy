from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import calculate_adx, calculate_atr

SHARE_SIZE_COLUMNS = [
    "symbol",
    "datetime",
    "fund_units",
    "market_close",
    "reported_total_size",
]
INDUSTRY_NAMES = {
    "semiconductor": "半导体",
    "electronics": "电子",
    "computer_ai": "计算机与人工智能",
    "communication": "通信",
    "machinery": "机械设备",
    "automobile": "汽车",
    "new_energy": "新能源",
    "finance": "金融",
    "healthcare": "医药医疗",
    "consumer": "消费",
    "materials": "材料",
    "defense": "国防军工",
    "utilities": "公用事业",
    "energy": "能源",
    "agriculture": "农业",
    "real_estate": "房地产",
    "construction": "建筑基建",
    "transportation": "交通运输",
    "media": "传媒",
    "environmental": "环保",
    "broad_or_other": "宽基及其他",
}
INDICATOR_PERIODS = (1, 3, 5, 10, 20, 60, 180)
ADX_PERIODS = (3, 5, 10, 20, 60, 180)
INDUSTRY_BASE_COLUMNS = [
    "datetime",
    "industry",
    "industry_name",
    "etf_count",
    "open",
    "high",
    "low",
    "close",
]
INDUSTRY_OUTPUT_COLUMNS = (
    INDUSTRY_BASE_COLUMNS
    + [f"turnover_sum_{period}" for period in INDICATOR_PERIODS]
    + [f"volume_sum_{period}" for period in INDICATOR_PERIODS]
    + [f"ema_{period}" for period in INDICATOR_PERIODS]
    + [f"atr_{period}" for period in INDICATOR_PERIODS]
    + [f"adx_{period}" for period in ADX_PERIODS]
)


def normalize_share_size(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize Tushare ETF share-size rows to units and yuan."""
    frame = raw.rename(
        columns={
            "ts_code": "symbol",
            "trade_date": "datetime",
            "total_share": "fund_units",
            "close": "market_close",
            "total_size": "reported_total_size",
        }
    ).copy()
    missing = {
        "symbol",
        "datetime",
        "fund_units",
        "market_close",
        "reported_total_size",
    } - set(frame.columns)
    if missing:
        raise ValueError(f"ETF share-size data missing columns: {sorted(missing)}")

    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    for column in ("fund_units", "market_close", "reported_total_size"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["fund_units"] *= 10_000.0
    frame["reported_total_size"] *= 10_000.0
    frame["market_close"] = frame["market_close"].where(
        frame["market_close"] > 0,
        np.nan,
    )
    frame = frame.loc[
        frame["symbol"].notna() & frame["datetime"].notna() & (frame["fund_units"] > 0)
    ]
    frame = frame.drop_duplicates(["symbol", "datetime"], keep="last")
    return frame[SHARE_SIZE_COLUMNS].sort_values(
        ["symbol", "datetime"], ignore_index=True
    )


def align_share_size(daily: pd.DataFrame, share_size: pd.DataFrame) -> pd.DataFrame:
    """Attach the latest known units and exact-date unadjusted market price."""
    frame = daily.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    if frame.duplicated(["symbol", "datetime"]).any():
        raise ValueError("ETF daily data contains duplicate symbol/date keys")

    shares = share_size[SHARE_SIZE_COLUMNS].copy()
    shares["datetime"] = pd.to_datetime(shares["datetime"], errors="raise")
    if shares.duplicated(["symbol", "datetime"]).any():
        raise ValueError("ETF share-size data contains duplicate symbol/date keys")

    frame = frame.sort_values(["datetime", "symbol"])
    units = shares[["symbol", "datetime", "fund_units"]].sort_values(
        ["datetime", "symbol"]
    )
    frame = pd.merge_asof(
        frame,
        units,
        on="datetime",
        by="symbol",
        direction="backward",
    )
    exact = shares[["symbol", "datetime", "market_close", "reported_total_size"]]
    frame = frame.merge(exact, on=["symbol", "datetime"], how="left")
    return frame.sort_values(["symbol", "datetime"], ignore_index=True)


def _attach_industry(daily: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
    required_metadata = {"symbol", "industry"}
    missing_metadata_columns = required_metadata - set(metadata.columns)
    if missing_metadata_columns:
        raise ValueError(
            f"ETF metadata missing columns: {sorted(missing_metadata_columns)}"
        )
    classifications = metadata[["symbol", "industry"]].copy()
    if classifications["symbol"].duplicated().any():
        raise ValueError("ETF metadata contains duplicate symbols")
    missing_symbols = sorted(set(daily["symbol"]) - set(classifications["symbol"]))
    if missing_symbols:
        raise ValueError(
            "missing industry metadata for symbols: " + ", ".join(missing_symbols)
        )
    if classifications["industry"].isna().any():
        symbols = classifications.loc[
            classifications["industry"].isna(), "symbol"
        ].tolist()
        raise ValueError("missing industry metadata for symbols: " + ", ".join(symbols))
    return daily.merge(
        classifications,
        on="symbol",
        how="left",
        validate="many_to_one",
    )


def _prepare_industry_components(
    daily: pd.DataFrame,
    metadata: pd.DataFrame,
) -> pd.DataFrame:
    required = {
        "symbol",
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    }
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"ETF daily data missing columns: {sorted(missing)}")
    frame = daily.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    if frame.duplicated(["symbol", "datetime"]).any():
        raise ValueError("ETF daily data contains duplicate symbol/date keys")
    for column in required - {"symbol", "datetime"}:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = _attach_industry(frame, metadata)
    frame["volume_in_units"] = frame["volume"] * 100.0
    return frame.sort_values(["symbol", "datetime"], ignore_index=True)


def aggregate_industry_daily(
    daily: pd.DataFrame,
    metadata: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate daily ETF count and liquidity by industry."""
    components = _prepare_industry_components(daily, metadata)
    return _aggregate_prepared_components(components)


def _aggregate_prepared_components(components: pd.DataFrame) -> pd.DataFrame:
    result = (
        components.groupby(["datetime", "industry"], sort=True)
        .agg(
            etf_count=("symbol", "nunique"),
            daily_turnover=("turnover", lambda values: values.sum(min_count=1)),
            daily_volume=("volume_in_units", lambda values: values.sum(min_count=1)),
        )
        .reset_index()
    )
    result.insert(
        2,
        "industry_name",
        result["industry"].map(INDUSTRY_NAMES).fillna(result["industry"]),
    )
    return result.sort_values(["datetime", "industry"], ignore_index=True)


def build_industry_price_index(
    daily: pd.DataFrame,
    metadata: pd.DataFrame,
) -> pd.DataFrame:
    """Build prior-day-turnover-weighted industry OHLC indexes."""
    components = _prepare_industry_components(daily, metadata)
    return _build_industry_price_index_from_components(components)


def _build_industry_price_index_from_components(
    components: pd.DataFrame,
) -> pd.DataFrame:
    components = components.copy()
    components["previous_turnover"] = components.groupby("symbol", sort=False)[
        "turnover"
    ].shift(1)
    components["previous_bar_datetime"] = components.groupby("symbol", sort=False)[
        "datetime"
    ].shift(1)
    components["previous_close"] = components.groupby("symbol", sort=False)[
        "close"
    ].shift(1)
    industry_calendar = components[["industry", "datetime"]].drop_duplicates()
    industry_calendar = industry_calendar.sort_values(["industry", "datetime"])
    industry_calendar["previous_industry_datetime"] = industry_calendar.groupby(
        "industry", sort=False
    )["datetime"].shift(1)
    components = components.merge(
        industry_calendar,
        on=["industry", "datetime"],
        how="left",
        validate="many_to_one",
    )

    price_columns = ["open", "high", "low", "close"]
    eligible = (
        components[price_columns].notna().all(axis=1)
        & np.isfinite(components[price_columns]).all(axis=1)
        & (components[price_columns] > 0).all(axis=1)
        & np.isfinite(components["previous_close"])
        & (components["previous_close"] > 0)
        & np.isfinite(components["previous_turnover"])
        & (components["previous_turnover"] > 0)
        & (
            components["previous_bar_datetime"]
            == components["previous_industry_datetime"]
        )
    )
    valid = components.loc[eligible].copy()
    relative_rows: list[dict[str, object]] = []
    for (date, industry), group in valid.groupby(["datetime", "industry"], sort=True):
        weight = group["previous_turnover"]
        weight = weight / weight.sum()
        relative_rows.append(
            {
                "datetime": date,
                "industry": industry,
                **{
                    f"{field}_relative": (
                        weight * group[field] / group["previous_close"]
                    ).sum()
                    for field in ("open", "high", "low", "close")
                },
            }
        )

    keys = components[["datetime", "industry"]].drop_duplicates()
    relatives = pd.DataFrame(relative_rows)
    if relatives.empty:
        relatives = pd.DataFrame(
            columns=[
                "datetime",
                "industry",
                "open_relative",
                "high_relative",
                "low_relative",
                "close_relative",
            ]
        )
    relative_frame = keys.merge(
        relatives,
        on=["datetime", "industry"],
        how="left",
    ).sort_values(["industry", "datetime"])

    index_rows: list[dict[str, object]] = []
    for industry, group in relative_frame.groupby("industry", sort=True):
        last_close = 100.0
        for row in group.itertuples(index=False):
            relatives_for_day = [
                row.open_relative,
                row.high_relative,
                row.low_relative,
                row.close_relative,
            ]
            if any(pd.isna(value) for value in relatives_for_day):
                open_value = high_value = low_value = close_value = np.nan
            else:
                open_value = last_close * row.open_relative
                high_value = last_close * row.high_relative
                low_value = last_close * row.low_relative
                close_value = last_close * row.close_relative
                high_value = max(high_value, open_value, close_value)
                low_value = min(low_value, open_value, close_value)
                last_close = close_value
            index_rows.append(
                {
                    "datetime": row.datetime,
                    "industry": industry,
                    "open": open_value,
                    "high": high_value,
                    "low": low_value,
                    "close": close_value,
                }
            )
    return pd.DataFrame(index_rows).sort_values(
        ["datetime", "industry"], ignore_index=True
    )


def build_industry_daily(
    daily: pd.DataFrame,
    metadata: pd.DataFrame,
) -> pd.DataFrame:
    """Combine industry liquidity totals and turnover-weighted OHLC index."""
    components = _prepare_industry_components(daily, metadata)
    totals = _aggregate_prepared_components(components)
    index = _build_industry_price_index_from_components(components)
    result = totals.merge(
        index,
        on=["datetime", "industry"],
        how="left",
        validate="one_to_one",
    )
    if result.duplicated(["datetime", "industry"]).any():
        raise RuntimeError("industry daily data contains duplicate keys")
    return result.sort_values(["datetime", "industry"], ignore_index=True)


def add_industry_indicators(industry_daily: pd.DataFrame) -> pd.DataFrame:
    """Add full-window liquidity and industry-price technical indicators."""
    required = set(INDUSTRY_BASE_COLUMNS) | {"daily_turnover", "daily_volume"}
    missing = required - set(industry_daily.columns)
    if missing:
        raise ValueError(f"industry daily data missing columns: {sorted(missing)}")
    if industry_daily.empty:
        return pd.DataFrame(columns=INDUSTRY_OUTPUT_COLUMNS)

    groups: list[pd.DataFrame] = []
    for _, group in industry_daily.groupby("industry", sort=True):
        result = group.sort_values("datetime").copy()
        for period in INDICATOR_PERIODS:
            result[f"turnover_sum_{period}"] = (
                result["daily_turnover"]
                .rolling(
                    period,
                    min_periods=period,
                )
                .sum()
            )
            result[f"volume_sum_{period}"] = (
                result["daily_volume"]
                .rolling(
                    period,
                    min_periods=period,
                )
                .sum()
            )
            result[f"ema_{period}"] = np.nan
            result[f"atr_{period}"] = np.nan
        for period in ADX_PERIODS:
            result[f"adx_{period}"] = np.nan

        price_columns = ["open", "high", "low", "close"]
        valid_ohlc = result[price_columns].notna().all(axis=1) & (
            result[price_columns] > 0
        ).all(axis=1)
        segment_ids = (~valid_ohlc).cumsum()
        for _, segment in result.loc[valid_ohlc].groupby(segment_ids[valid_ohlc]):
            for period in INDICATOR_PERIODS:
                result.loc[segment.index, f"ema_{period}"] = (
                    segment["close"]
                    .ewm(
                        span=period,
                        adjust=False,
                        min_periods=period,
                    )
                    .mean()
                )
                result.loc[segment.index, f"atr_{period}"] = calculate_atr(
                    segment, period
                )
            for period in ADX_PERIODS:
                result.loc[segment.index, f"adx_{period}"] = calculate_adx(
                    segment, period
                )
        groups.append(result)

    combined = pd.concat(groups, ignore_index=True)
    combined = combined.sort_values(["datetime", "industry"], ignore_index=True)
    return combined[INDUSTRY_OUTPUT_COLUMNS]
