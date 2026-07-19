from __future__ import annotations

import numpy as np
import pandas as pd

BAR_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume"]
SIGNAL_COLUMNS = BAR_COLUMNS + [
    "ema5",
    "previous_ema5",
    "target_invested",
    "action",
]


def prepare_symbol_bars(daily: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Validate and return one ETF's date-ordered daily OHLCV rows."""
    missing = set(BAR_COLUMNS) - set(daily.columns)
    if missing:
        raise ValueError(f"ETF daily data missing columns: {sorted(missing)}")
    frame = daily.loc[daily["symbol"].astype(str).eq(symbol), BAR_COLUMNS].copy()
    if frame.empty:
        raise ValueError(f"ETF daily data missing symbol: {symbol}")
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    for column in BAR_COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["datetime"].duplicated().any():
        raise ValueError(f"ETF daily data contains duplicate dates for {symbol}")
    prices = frame[["open", "high", "low", "close"]]
    valid = (
        np.isfinite(prices).all(axis=1)
        & (prices > 0).all(axis=1)
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
        & np.isfinite(frame["volume"])
        & (frame["volume"] >= 0)
    )
    if not valid.all():
        raise ValueError(f"ETF daily data contains invalid OHLCV rows for {symbol}")
    frame = frame.sort_values("datetime", ignore_index=True)
    if len(frame) < 6:
        raise ValueError("ETF daily data requires at least 6 rows for previous EMA5")
    return frame


def build_ema5_open_signals(bars: pd.DataFrame) -> pd.DataFrame:
    """Compare each open with the previous completed close EMA5."""
    result = bars.copy()
    result["ema5"] = (
        result["close"].ewm(span=5, adjust=False, min_periods=5).mean()
    )
    result["previous_ema5"] = result["ema5"].shift(1)
    result["target_invested"] = result["previous_ema5"].notna() & (
        result["open"] > result["previous_ema5"]
    )
    result["action"] = "flat"
    return result[SIGNAL_COLUMNS]
