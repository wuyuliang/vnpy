"""Causal daily EMA universe used before cycle_v1 intraday decisions."""

from __future__ import annotations

import numpy as np
import pandas as pd


REQUIRED_DAILY_COLUMNS = frozenset(
    {"root_symbol", "exchange_trade_date", "contract_code", "bar_end", "close"}
)


def build_dynamic_ema_universe(daily_bars: pd.DataFrame) -> pd.DataFrame:
    """Make date D eligible only from the same contract's completed D-1 EMA state."""
    missing = sorted(REQUIRED_DAILY_COLUMNS.difference(daily_bars.columns))
    if missing:
        raise ValueError(f"daily EMA input is missing columns: {','.join(missing)}")
    if daily_bars.empty:
        return pd.DataFrame(
            columns=(
                "root_symbol",
                "exchange_trade_date",
                "contract_code",
                "ema_asof_trade_date",
                "ema_asof_bar_end",
                "ema1",
                "ema3",
                "ema5",
                "eligible",
                "reason_code",
            )
        )

    frame = daily_bars.loc[:, sorted(REQUIRED_DAILY_COLUMNS)].copy()
    frame["root_symbol"] = frame["root_symbol"].astype(str).str.upper().str.strip()
    frame["contract_code"] = frame["contract_code"].astype(str).str.upper().str.strip()
    frame["exchange_trade_date"] = pd.to_datetime(
        frame["exchange_trade_date"], errors="coerce"
    ).dt.date
    frame["bar_end"] = pd.to_datetime(frame["bar_end"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame["exchange_trade_date"].isna().any() or frame["bar_end"].isna().any():
        raise ValueError("daily EMA input contains invalid dates")
    if frame["bar_end"].dt.tz is None:
        raise ValueError("daily EMA bar_end must be timezone-aware")
    if (
        frame["root_symbol"].eq("").any()
        or frame["contract_code"].eq("").any()
        or not np.isfinite(frame["close"].to_numpy(float)).all()
        or frame["close"].le(0).any()
    ):
        raise ValueError("daily EMA identity and close values must be valid")

    frame = frame.sort_values(
        ["root_symbol", "exchange_trade_date", "bar_end"], kind="stable"
    ).reset_index(drop=True)
    if frame.duplicated(["root_symbol", "exchange_trade_date"]).any():
        raise ValueError(
            "daily EMA requires one actual contract bar per root and trade date"
        )
    frame["_contract_epoch"] = frame.groupby("root_symbol", sort=False)[
        "contract_code"
    ].transform(lambda values: values.ne(values.shift()).cumsum())
    group_keys = ["root_symbol", "_contract_epoch"]
    grouped = frame.groupby(group_keys, sort=False, group_keys=False)
    for period in (1, 3, 5):
        frame[f"_ema{period}"] = grouped["close"].transform(
            lambda values, period=period: values.ewm(
                span=period,
                adjust=False,
                min_periods=period,
            ).mean()
        )

    epoch = frame.groupby(group_keys, sort=False)
    frame["ema_asof_trade_date"] = epoch["exchange_trade_date"].shift(1)
    frame["ema_asof_bar_end"] = epoch["bar_end"].shift(1)
    for period in (1, 3, 5):
        frame[f"ema{period}"] = epoch[f"_ema{period}"].shift(1)
    ready = frame[["ema1", "ema3", "ema5"]].notna().all(axis=1)
    frame["eligible"] = (
        ready & frame["ema1"].gt(frame["ema3"]) & frame["ema3"].gt(frame["ema5"])
    )
    frame["reason_code"] = np.select(
        [~ready, frame["eligible"]],
        ["EMA_WARMUP_OR_ROLL", ""],
        default="EMA_ORDER_BLOCKED",
    )
    return frame.loc[
        :,
        [
            "root_symbol",
            "exchange_trade_date",
            "contract_code",
            "ema_asof_trade_date",
            "ema_asof_bar_end",
            "ema1",
            "ema3",
            "ema5",
            "eligible",
            "reason_code",
        ],
    ].reset_index(drop=True)


__all__ = ["REQUIRED_DAILY_COLUMNS", "build_dynamic_ema_universe"]
