"""MA5/MA10 big-bull candidate scanner and cross-sectional selector."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from stock.data_code.stock_downloader import stock_price_limit_threshold_pct


BIG_BULL_SIGNAL_TYPE = "ma5_ma10_big_bull"
BIG_BULL_TRIGGER = "ma5_cross_ma10_big_bull_long"

BIG_BULL_COLUMNS: list[str] = [
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma120",
    "ma20_slope_5d",
    "ma60_slope_20d",
    "return_20d",
    "return_60d",
    "return_120d",
    "rs_60d_pct",
    "volume_20_avg",
    "volume_ratio_20",
    "volume_3_to_20",
    "limit_up_count_120d",
    "big_volume_up_count_60d",
    "close_to_ma20",
    "close_to_120d_high",
    "base_compression_60d",
    "big_bull_score",
    "candidate_reason",
]

_BASE_COLUMNS: list[str] = [
    "symbol",
    "exchange",
    "signal_type",
    "signal_datetime",
    "opportunity_date",
    "signal_price",
    "close_price",
    "entry_datetime",
    "entry_price",
    "entry_action",
    "trigger",
]


@dataclass(frozen=True)
class BigBullConfig:
    """Selection thresholds for the MA5/MA10 big-bull research signal."""

    min_history_bars: int = 120
    ma_fast: int = 5
    ma_mid: int = 10
    ma_slow: int = 20
    ma_trend: int = 60
    ma_long: int = 120
    cooldown_days: int = 20
    min_close: float = 3.0
    min_turnover_20_avg: float = 100_000.0
    min_volume_ratio_20: float = 1.3
    min_volume_3_to_20: float = 1.2
    max_close_to_ma20: float = 1.25
    max_return_20d: float = 0.80
    min_rs_60d_pct: float = 0.70
    min_limit_up_count_120d: int = 1
    min_big_volume_up_count_60d: int = 2
    min_score: float = 70.0
    max_candidates_per_day: int = 30


def _empty_big_bull_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_BASE_COLUMNS + BIG_BULL_COLUMNS)


def _numeric_column(frame: pd.DataFrame, column: str, *, default: float | None = None) -> pd.Series:
    if column in frame.columns:
        values = pd.to_numeric(frame[column], errors="coerce")
    else:
        values = pd.Series(index=frame.index, dtype="float64")
    if default is not None:
        values = values.fillna(default)
    return values


def prepare_big_bull_features(frame: pd.DataFrame, cfg: BigBullConfig) -> pd.DataFrame:
    """Normalize one symbol's bars and calculate all big-bull features once."""
    if frame.empty or "datetime" not in frame.columns:
        return pd.DataFrame()

    bars = frame.copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
    close = _numeric_column(bars, "close")
    high = _numeric_column(bars, "high")
    low = _numeric_column(bars, "low")
    volume = _numeric_column(bars, "volume", default=0.0)
    turnover = _numeric_column(bars, "turnover", default=0.0)
    pct_chg = _numeric_column(bars, "pct_chg")
    bars = bars.assign(
        close=close,
        high=high,
        low=low,
        volume=volume,
        turnover=turnover,
        pct_chg=pct_chg,
    )
    bars = (
        bars.dropna(subset=["datetime", "close", "high", "low"])
        .sort_values("datetime")
        .drop_duplicates("datetime", keep="last")
        .reset_index(drop=True)
    )
    if bars.empty:
        return bars

    close = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    volume = pd.to_numeric(bars["volume"], errors="coerce").fillna(0.0)
    turnover = pd.to_numeric(bars["turnover"], errors="coerce").fillna(0.0)
    pct_chg = pd.to_numeric(bars["pct_chg"], errors="coerce").fillna(
        close.pct_change() * 100.0
    )

    for window, column in [
        (cfg.ma_fast, "ma5"),
        (cfg.ma_mid, "ma10"),
        (cfg.ma_slow, "ma20"),
        (cfg.ma_trend, "ma60"),
        (cfg.ma_long, "ma120"),
    ]:
        bars[column] = close.rolling(window).mean()

    bars["ma20_slope_5d"] = bars["ma20"] / bars["ma20"].shift(5) - 1.0
    bars["ma60_slope_20d"] = bars["ma60"] / bars["ma60"].shift(20) - 1.0
    bars["return_20d"] = close / close.shift(20) - 1.0
    bars["return_60d"] = close / close.shift(60) - 1.0
    bars["return_120d"] = close / close.shift(120) - 1.0
    bars["volume_20_avg"] = volume.shift(1).rolling(20).mean()
    bars["volume_3_avg"] = volume.rolling(3).mean()
    valid_volume_avg = bars["volume_20_avg"].where(bars["volume_20_avg"] > 0)
    bars["volume_ratio_20"] = volume / valid_volume_avg
    bars["volume_3_to_20"] = bars["volume_3_avg"] / valid_volume_avg
    bars["turnover_20_avg"] = turnover.shift(1).rolling(20).mean()
    symbol = str(bars["symbol"].iloc[0]) if "symbol" in bars.columns else ""
    limit_threshold = stock_price_limit_threshold_pct(symbol)
    bars["limit_up_count_120d"] = (pct_chg >= limit_threshold).rolling(
        120,
        min_periods=1,
    ).sum()
    big_volume_up = (pct_chg > 3.0) & (bars["volume_ratio_20"] >= 1.8)
    bars["big_volume_up_count_60d"] = big_volume_up.rolling(60, min_periods=1).sum()
    bars["high_120d"] = high.rolling(120, min_periods=1).max()
    bars["low_60d"] = low.rolling(60, min_periods=1).min()
    bars["high_60d"] = high.rolling(60, min_periods=1).max()
    bars["close_to_ma20"] = close / bars["ma20"].where(bars["ma20"] > 0)
    bars["close_to_120d_high"] = close / bars["high_120d"].where(bars["high_120d"] > 0)
    bars["base_compression_60d"] = (
        bars["high_60d"] / bars["low_60d"].where(bars["low_60d"] > 0) - 1.0
    )
    return bars


def extract_return_60d_by_opportunity_date(
    frame: pd.DataFrame,
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[tuple[str, float]]:
    """Return point-in-time 60-day returns for market-wide RS ranking."""
    if frame.empty or not {"datetime", "close"}.issubset(frame.columns):
        return []
    bars = frame.loc[:, ["datetime", "close"]].copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = (
        bars.dropna(subset=["datetime", "close"])
        .sort_values("datetime")
        .drop_duplicates("datetime", keep="last")
        .reset_index(drop=True)
    )
    if len(bars) < 62:
        return []
    return_60d = bars["close"] / bars["close"].shift(60) - 1.0
    opportunity_dates = bars["datetime"].shift(-1)
    mask = return_60d.notna() & opportunity_dates.notna()
    if start is not None:
        mask &= opportunity_dates >= pd.Timestamp(start)
    if end is not None:
        mask &= opportunity_dates <= pd.Timestamp(end)
    return [
        (pd.Timestamp(date).strftime("%Y-%m-%d"), float(value))
        for date, value in zip(
            opportunity_dates[mask],
            return_60d[mask],
            strict=True,
        )
    ]


def _format_reason(row: pd.Series) -> str:
    return "; ".join(
        [
            f"ma5>=ma10 {float(row.get('ma5', 0.0)):.2f}>={float(row.get('ma10', 0.0)):.2f}",
            f"rs60={float(row.get('rs_60d_pct', 0.0)):.2f}",
            f"vol20={float(row.get('volume_ratio_20', 0.0)):.2f}",
            f"limit120={int(float(row.get('limit_up_count_120d', 0.0)))}",
            f"score={float(row.get('big_bull_score', 0.0)):.2f}",
        ]
    )


def scan_ma5_ma10_big_bull(
    frame: pd.DataFrame,
    cfg: BigBullConfig | None = None,
) -> pd.DataFrame:
    """Emit unranked MA5/MA10 candidates using the next bar as opportunity date."""
    cfg = cfg or BigBullConfig()
    bars = prepare_big_bull_features(frame, cfg)
    minimum_rows = max(cfg.min_history_bars, cfg.ma_mid) + 1
    if bars.empty or len(bars) < minimum_rows:
        return _empty_big_bull_frame()

    close = pd.to_numeric(bars["close"], errors="coerce")
    ma5 = pd.to_numeric(bars["ma5"], errors="coerce")
    ma10 = pd.to_numeric(bars["ma10"], errors="coerce")
    ma60 = pd.to_numeric(bars["ma60"], errors="coerce")
    volume_ratio_20 = pd.to_numeric(bars["volume_ratio_20"], errors="coerce")
    volume_3_to_20 = pd.to_numeric(bars["volume_3_to_20"], errors="coerce")
    turnover_20_avg = pd.to_numeric(bars["turnover_20_avg"], errors="coerce")

    ma_cross = (ma5 >= ma10) & (ma5.shift(1) < ma10.shift(1))
    enough_history = pd.Series(range(len(bars)), index=bars.index) >= cfg.min_history_bars - 1
    trend_ok = (pd.to_numeric(bars["ma20_slope_5d"], errors="coerce") > 0) & (
        (close >= ma60)
        | (pd.to_numeric(bars["ma60_slope_20d"], errors="coerce") > 0)
    )
    volume_ok = (volume_ratio_20 >= cfg.min_volume_ratio_20) | (
        volume_3_to_20 >= cfg.min_volume_3_to_20
    )
    active_ok = (
        pd.to_numeric(bars["limit_up_count_120d"], errors="coerce").fillna(0)
        >= cfg.min_limit_up_count_120d
    ) | (
        pd.to_numeric(bars["big_volume_up_count_60d"], errors="coerce").fillna(0)
        >= cfg.min_big_volume_up_count_60d
    )
    liquidity_ok = turnover_20_avg.fillna(0.0) >= cfg.min_turnover_20_avg
    not_overheated = (
        pd.to_numeric(bars["close_to_ma20"], errors="coerce") <= cfg.max_close_to_ma20
    ) & (pd.to_numeric(bars["return_20d"], errors="coerce") <= cfg.max_return_20d)

    signal_mask = (
        enough_history
        & ma_cross
        & (close >= ma10)
        & (close >= cfg.min_close)
        & trend_ok
        & volume_ok
        & active_ok
        & liquidity_ok
        & not_overheated
    ).fillna(False)
    signal_mask.iloc[-1] = False

    rows: list[dict[str, Any]] = []
    for signal_index in bars.index[signal_mask]:
        signal_row = bars.iloc[int(signal_index)]
        opportunity_row = bars.iloc[int(signal_index) + 1]
        signal_datetime = pd.Timestamp(signal_row["datetime"]).strftime("%Y-%m-%d %H:%M:%S")
        opportunity_datetime = pd.Timestamp(opportunity_row["datetime"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        opportunity_open = pd.to_numeric(
            pd.Series([opportunity_row.get("open")]), errors="coerce"
        ).iloc[0]
        entry_price = (
            float(opportunity_open)
            if pd.notna(opportunity_open)
            else float(opportunity_row["close"])
        )
        row: dict[str, Any] = {
            "symbol": str(signal_row.get("symbol", "")),
            "exchange": str(signal_row.get("exchange", "")),
            "signal_type": BIG_BULL_SIGNAL_TYPE,
            "signal_datetime": signal_datetime,
            "opportunity_date": opportunity_datetime[:10],
            "signal_price": float(signal_row["close"]),
            "close_price": float(signal_row["close"]),
            "entry_datetime": opportunity_datetime,
            "entry_price": entry_price,
            "entry_action": "buy",
            "trigger": BIG_BULL_TRIGGER,
        }
        for column in BIG_BULL_COLUMNS:
            row[column] = signal_row.get(column, pd.NA)
        row["rs_60d_pct"] = pd.NA
        row["big_bull_score"] = pd.NA
        row["candidate_reason"] = ""
        rows.append(row)

    if not rows:
        return _empty_big_bull_frame()
    return pd.DataFrame(rows).loc[:, _BASE_COLUMNS + BIG_BULL_COLUMNS]


def _calculate_big_bull_score(frame: pd.DataFrame, cfg: BigBullConfig) -> pd.Series:
    trend = (
        ((frame["ma5"] >= frame["ma10"]) & (frame["ma10"] >= frame["ma20"]))
        .astype(float)
        .mul(10.0)
        + (frame["ma20_slope_5d"] > 0).astype(float).mul(7.5)
        + (frame["close_price"] >= frame["ma60"]).astype(float).mul(7.5)
    )
    relative_strength = (
        frame["rs_60d_pct"].fillna(0.0).clip(0.0, 1.0).mul(20.0)
        + frame["return_120d"].fillna(0.0).clip(0.0, 1.0).mul(5.0)
    )
    volume = (
        frame["volume_ratio_20"].fillna(0.0).clip(0.0, 3.0).div(3.0).mul(8.0)
        + frame["volume_3_to_20"].fillna(0.0).clip(0.0, 2.5).div(2.5).mul(7.0)
    )
    structure = (
        frame["close_to_120d_high"].fillna(0.0).clip(0.0, 1.0).mul(8.0)
        + (1.0 - frame["base_compression_60d"].fillna(1.0).clip(0.0, 1.0)).mul(7.0)
    )
    active = (
        frame["limit_up_count_120d"].fillna(0.0).clip(0.0, 3.0).div(3.0).mul(5.0)
        + frame["big_volume_up_count_60d"].fillna(0.0).clip(0.0, 5.0).div(5.0).mul(5.0)
    )
    not_overheated = (
        (frame["close_to_ma20"] <= cfg.max_close_to_ma20).astype(float).mul(5.0)
        + (frame["return_20d"] <= cfg.max_return_20d).astype(float).mul(5.0)
    )
    return (trend + relative_strength + volume + structure + active + not_overheated).round(2)


def _select_daily_topn_with_cooldown(
    frame: pd.DataFrame,
    *,
    max_candidates_per_day: int,
    cooldown_days: int,
) -> pd.DataFrame:
    if frame.empty or max_candidates_per_day <= 0:
        return frame.iloc[0:0].copy()
    selected_indices: list[int] = []
    last_date_by_symbol: dict[str, pd.Timestamp] = {}
    for opportunity_date in sorted(frame["opportunity_date"].astype(str).unique()):
        current_date = pd.Timestamp(opportunity_date)
        day = frame[frame["opportunity_date"].astype(str) == opportunity_date].sort_values(
            ["big_bull_score", "rs_60d_pct", "volume_ratio_20", "symbol"],
            ascending=[False, False, False, True],
        )
        eligible_indices: list[int] = []
        for index, row in day.iterrows():
            symbol = str(row["symbol"])
            last_date = last_date_by_symbol.get(symbol)
            if (
                cooldown_days > 0
                and last_date is not None
                and (current_date - last_date).days < cooldown_days
            ):
                continue
            eligible_indices.append(int(index))
        chosen_indices = eligible_indices[:max_candidates_per_day]
        selected_indices.extend(chosen_indices)
        for index in chosen_indices:
            last_date_by_symbol[str(frame.at[index, "symbol"])] = current_date
    return frame.loc[selected_indices].reset_index(drop=True)


def _percentile_in_distribution(value: object, distribution: Sequence[float]) -> float:
    numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric_value) or not distribution:
        return float("nan")
    left = bisect_left(distribution, float(numeric_value))
    right = bisect_right(distribution, float(numeric_value))
    return ((left + 1) + right) / 2.0 / len(distribution)


def score_and_filter_big_bull_opportunities(
    merged: pd.DataFrame,
    cfg: BigBullConfig | None = None,
    *,
    market_returns_by_date: Mapping[str, Sequence[float]] | None = None,
) -> pd.DataFrame:
    """Apply market-wide RS, score, daily TopN, and symbol cooldown."""
    cfg = cfg or BigBullConfig()
    if merged.empty or "signal_type" not in merged.columns:
        return merged.copy()

    signal_types = merged["signal_type"].astype(str)
    other = merged[signal_types != BIG_BULL_SIGNAL_TYPE].copy()
    big = merged[signal_types == BIG_BULL_SIGNAL_TYPE].copy()
    if big.empty:
        return merged.reset_index(drop=True)
    if "opportunity_date" not in big.columns:
        raise ValueError("big-bull rows require a valid opportunity_date column")
    parsed_opportunity_dates = pd.to_datetime(big["opportunity_date"], errors="coerce")
    if parsed_opportunity_dates.isna().any():
        raise ValueError("big-bull rows require valid opportunity_date values")
    big["opportunity_date"] = parsed_opportunity_dates.dt.strftime("%Y-%m-%d")
    if market_returns_by_date is None:
        raise ValueError(
            "market_returns_by_date is required for market-wide big-bull relative strength"
        )

    numeric_columns = [
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "return_20d",
        "return_60d",
        "return_120d",
        "volume_ratio_20",
        "volume_3_to_20",
        "ma20_slope_5d",
        "close_price",
        "close_to_ma20",
        "close_to_120d_high",
        "base_compression_60d",
        "limit_up_count_120d",
        "big_volume_up_count_60d",
    ]
    for column in numeric_columns:
        if column not in big.columns:
            big[column] = pd.NA
        big[column] = pd.to_numeric(big[column], errors="coerce")

    sorted_distributions = {
        str(date): sorted(float(value) for value in values if pd.notna(value))
        for date, values in market_returns_by_date.items()
    }
    big["rs_60d_pct"] = [
        _percentile_in_distribution(
            return_60d,
            sorted_distributions.get(str(opportunity_date), []),
        )
        for opportunity_date, return_60d in zip(
            big["opportunity_date"],
            big["return_60d"],
            strict=True,
        )
    ]
    big["big_bull_score"] = _calculate_big_bull_score(big, cfg)
    big = big[
        (big["rs_60d_pct"] >= cfg.min_rs_60d_pct)
        & (big["big_bull_score"] >= cfg.min_score)
    ].copy()
    if not big.empty:
        big = _select_daily_topn_with_cooldown(
            big,
            max_candidates_per_day=cfg.max_candidates_per_day,
            cooldown_days=cfg.cooldown_days,
        )
        big["candidate_reason"] = big.apply(_format_reason, axis=1)

    combined = pd.concat([other, big], ignore_index=True, sort=False)
    if "opportunity_date" not in combined.columns:
        return combined.reset_index(drop=True)
    sort_columns = [
        column
        for column in ["opportunity_date", "big_bull_score", "rs_60d_pct", "symbol", "signal_type"]
        if column in combined.columns
    ]
    ascending = [True, False, False, True, True][: len(sort_columns)]
    return combined.sort_values(sort_columns, ascending=ascending, na_position="last").reset_index(drop=True)


__all__ = [
    "BIG_BULL_COLUMNS",
    "BIG_BULL_SIGNAL_TYPE",
    "BIG_BULL_TRIGGER",
    "BigBullConfig",
    "extract_return_60d_by_opportunity_date",
    "prepare_big_bull_features",
    "scan_ma5_ma10_big_bull",
    "score_and_filter_big_bull_opportunities",
]
