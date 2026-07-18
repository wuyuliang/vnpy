from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyConfig

RANK_COLUMNS: tuple[str, ...] = (
    "return_3",
    "return_5",
    "return_10",
    "return_20",
    "ema5_slope",
    "normalized_atr5",
)


def _is_trading(series: pd.Series) -> pd.Series:
    normalized = series.fillna(False).astype(str).str.lower()
    return normalized.isin({"true", "1", "l", "listed", "上市"})


def is_risk_on(
    benchmark_row: pd.Series,
    previous_ema5: float,
    config: StrategyConfig,
) -> bool:
    """Return whether the benchmark passes the close-time Risk On filter."""
    required = ["open", "close", "ema5", "ema10", "ema20", "trend_adx"]
    if benchmark_row[required].isna().any() or pd.isna(previous_ema5):
        return False
    return bool(
        benchmark_row["ema5"] > benchmark_row["ema10"] > benchmark_row["ema20"]
        and benchmark_row["trend_adx"] > config.adx_threshold
        and benchmark_row["open"] > previous_ema5
        and benchmark_row["close"] > benchmark_row["ema10"]
    )


def _base_candidates(
    candidates: pd.DataFrame,
    signal_date: pd.Timestamp,
    config: StrategyConfig,
) -> pd.DataFrame:
    frame = candidates.loc[pd.to_datetime(candidates["datetime"]) == signal_date].copy()
    frame["list_date"] = pd.to_datetime(frame["list_date"])
    if "delist_date" not in frame:
        frame["delist_date"] = pd.NaT
    frame["delist_date"] = pd.to_datetime(frame["delist_date"])
    listing_cutoff = signal_date - pd.DateOffset(months=config.min_listing_months)
    required = list(RANK_COLUMNS) + [
        "close",
        "ema5",
        "ema10",
        "ema20",
        "trend_adx",
        "risk_atr",
    ]
    finite = pd.DataFrame(np.isfinite(frame[required]), index=frame.index).all(axis=1)
    mask = (
        frame["turnover"].notna()
        & _is_trading(frame["is_trading"])
        & (frame["list_date"] <= listing_cutoff)
        & (frame["delist_date"].isna() | (signal_date <= frame["delist_date"]))
        & (frame["bar_count"] >= config.warmup_bars)
        & frame[required].notna().all(axis=1)
        & finite
    )
    return frame.loc[mask].copy()


def build_daily_ranking(
    candidates: pd.DataFrame,
    signal_date: pd.Timestamp,
    config: StrategyConfig,
) -> pd.DataFrame:
    """Filter and rank one daily ETF cross-section using the specified RS score."""
    frame = _base_candidates(candidates, pd.Timestamp(signal_date), config)
    if frame.empty:
        return frame.assign(
            rs_score=pd.Series(dtype=float),
            rs_rank=pd.Series(dtype="Int64"),
            entry_rank=pd.Series(dtype="Int64"),
            holding_rank=pd.Series(dtype="Int64"),
            entry_liquidity_eligible=pd.Series(dtype=bool),
            entry_representative=pd.Series(dtype=bool),
            trend_confirmed=pd.Series(dtype=bool),
        )

    for column in RANK_COLUMNS:
        frame[f"rank_{column}"] = frame[column].rank(
            method="average", ascending=True, pct=True
        )
    frame["rs_score"] = (
        0.35 * frame["rank_return_3"]
        + 0.35 * frame["rank_return_5"]
        + 0.20 * frame["rank_return_10"]
        + 0.10 * frame["rank_return_20"]
        + 0.20 * frame["rank_ema5_slope"]
        - 0.20 * frame["rank_normalized_atr5"]
    )
    frame["trend_confirmed"] = (
        (frame["close"] > frame["ema5"])
        & (frame["ema5"] > frame["ema10"])
        & (frame["ema10"] > frame["ema20"])
        & (frame["trend_adx"] > config.adx_threshold)
    )
    frame = frame.sort_values(
        ["rs_score", "return_5", "turnover", "symbol"],
        ascending=[False, False, False, True],
        kind="stable",
    ).reset_index(drop=True)
    frame["entry_liquidity_eligible"] = (
        frame["turnover_median20"].notna()
        & np.isfinite(frame["turnover_median20"])
        & (frame["turnover_median20"] > config.min_turnover)
    )
    if "benchmark_key" not in frame:
        frame["benchmark_key"] = "symbol:" + frame["symbol"].astype(str)
    frame["entry_representative"] = False
    liquid = frame.loc[frame["entry_liquidity_eligible"]].sort_values(
        ["benchmark_key", "turnover_median20", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )
    representative_indexes = liquid.drop_duplicates("benchmark_key", keep="first").index
    frame.loc[representative_indexes, "entry_representative"] = True
    entry_reference = frame["entry_liquidity_eligible"] & frame["entry_representative"]
    frame["entry_rank"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    reference_count = 0
    holding_ranks: list[int] = []
    for index, eligible in enumerate(entry_reference):
        if eligible:
            reference_count += 1
            frame.at[index, "entry_rank"] = reference_count
            holding_ranks.append(reference_count)
        else:
            holding_ranks.append(reference_count + 1)
    frame["holding_rank"] = pd.Series(holding_ranks, dtype="Int64")
    frame["rs_rank"] = frame["entry_rank"]
    return frame


def entry_symbols(ranking: pd.DataFrame, config: StrategyConfig) -> list[str]:
    """Return trend-confirmed symbols inside the configured entry rank."""
    top = ranking.loc[ranking["entry_rank"] <= config.entry_rank]
    return top.loc[top["trend_confirmed"], "symbol"].tolist()


def audit_daily_candidates(
    candidates: pd.DataFrame,
    signal_date: pd.Timestamp,
    config: StrategyConfig,
    *,
    ranking: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return every daily candidate with exclusion reasons and eligible ranks."""
    date = pd.Timestamp(signal_date)
    audit = candidates.loc[pd.to_datetime(candidates["datetime"]) == date].copy()
    if audit.empty:
        return audit.assign(exclusion_reason=pd.Series(dtype=str))
    audit["list_date"] = pd.to_datetime(audit["list_date"])
    if "delist_date" not in audit:
        audit["delist_date"] = pd.NaT
    audit["delist_date"] = pd.to_datetime(audit["delist_date"])
    listing_cutoff = date - pd.DateOffset(months=config.min_listing_months)
    required = list(RANK_COLUMNS) + [
        "close",
        "ema5",
        "ema10",
        "ema20",
        "trend_adx",
        "risk_atr",
    ]

    reasons = pd.Series("", index=audit.index, dtype=str)

    def append_reason(mask: pd.Series, reason: str) -> None:
        selected = mask.fillna(False)
        separator = reasons.loc[selected].ne("").map({True: "|", False: ""})
        reasons.loc[selected] = reasons.loc[selected] + separator + reason

    append_reason(audit["turnover"].isna(), "missing_turnover")
    missing_turnover_history = audit["turnover_median20"].isna()
    append_reason(missing_turnover_history, "missing_turnover_history")
    append_reason(
        ~missing_turnover_history & (audit["turnover_median20"] <= config.min_turnover),
        "turnover_not_above_minimum",
    )
    append_reason(~_is_trading(audit["is_trading"]), "not_trading")
    missing_list_date = audit["list_date"].isna()
    append_reason(missing_list_date, "missing_list_date")
    append_reason(
        ~missing_list_date & (audit["list_date"] > listing_cutoff),
        "listing_age_below_six_months",
    )
    append_reason(
        audit["delist_date"].notna() & (date > audit["delist_date"]),
        "delisted",
    )
    append_reason(audit["bar_count"] < config.warmup_bars, "insufficient_warmup")
    missing_indicator = audit[required].isna().any(axis=1)
    append_reason(missing_indicator, "missing_indicator")
    finite_indicator = pd.Series(
        np.isfinite(audit[required].to_numpy(dtype=float)).all(axis=1),
        index=audit.index,
    )
    append_reason(~missing_indicator & ~finite_indicator, "invalid_indicator")
    audit["exclusion_reason"] = reasons
    if ranking is None:
        ranking = build_daily_ranking(candidates, date, config)
    rank_columns = [
        "symbol",
        *(f"rank_{column}" for column in RANK_COLUMNS),
        "rs_score",
        "rs_rank",
        "entry_rank",
        "holding_rank",
        "entry_liquidity_eligible",
        "entry_representative",
        "trend_confirmed",
    ]
    available = [column for column in rank_columns if column in ranking]
    audit = audit.merge(
        ranking[available], on="symbol", how="left", suffixes=("", "_ranked")
    )
    return audit.sort_values("symbol").reset_index(drop=True)
