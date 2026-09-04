"""Minimal bull pullback continuation evaluator for stock daily bars."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import pandas as pd


SignalDecision = dict[str, Any]


@dataclass(frozen=True)
class BullPullbackConfig:
    """Config for stock signal scanners on daily bars."""

    ema_fast: int = 5
    ema_mid: int = 10
    ema_slow: int = 20
    ma_window: int = 20
    pullback_pct: float = 0.015
    volume_spike_ratio: float = 2.0
    volume_spike_window: int = 5
    volume_short_window: int = 3
    volume_long_window: int = 10
    volume_avg_ratio: float = 1.5
    volume_spike_low_price_lookback_days: int = 548
    volume_spike_low_price_ratio: float = 0.5
    breakout_window: int = 20
    breakout_pullback_lookback: int = 5
    breakout_pullback_pct: float = 0.03
    breakout_reclaim_pct: float = 0.01


OPPORTUNITY_COLUMNS: list[str] = [
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
    "ema5",
    "ema10",
    "ema20",
    "volume",
    "volume_prev",
    "volume_5_avg",
    "volume_3_avg",
    "volume_10_avg",
    "volume_condition",
    "lookback_high_18m",
    "close_to_lookback_high",
    "breakout_level",
    "pullback_low",
    "pullback_high",
    "bars_since_breakout",
]


def _close(bar: Any) -> float:
    return float(getattr(bar, "close_price", 0.0))


def _volume(bar: Any) -> float:
    return float(getattr(bar, "volume", 0.0))


def _ema(values: Sequence[float], window: int) -> float:
    if len(values) < window or window <= 0:
        return float("nan")
    return float(pd.Series(values, dtype="float64").ewm(span=window, adjust=False).mean().iloc[-1])


def _avg(values: Sequence[float], window: int) -> float:
    if len(values) < window or window <= 0:
        return float("nan")
    return float(sum(values[-window:]) / window)


def _high(bar: Any) -> float:
    return float(getattr(bar, "high_price", 0.0))


def _low(bar: Any) -> float:
    return float(getattr(bar, "low_price", 0.0))


def evaluate_bull_pullback_continuation(
    bars: Sequence[Any],
    cfg: BullPullbackConfig,
) -> SignalDecision | None:
    """Return a long signal when price pulls back with EMA stack and volume confirmation."""
    min_bars = max(cfg.ema_fast, cfg.ema_mid, cfg.ema_slow, cfg.volume_long_window)
    if len(bars) < min_bars:
        return None

    closes = [_close(bar) for bar in bars]
    volumes = [_volume(bar) for bar in bars]
    ema5 = _ema(closes, cfg.ema_fast)
    ema10 = _ema(closes, cfg.ema_mid)
    ema20 = _ema(closes, cfg.ema_slow)
    close_now = closes[-1]

    if ema5 <= 0 or ema10 <= 0 or ema20 <= 0:
        return None
    if not (ema5 >= ema10 >= ema20):
        return None

    latest_volume = volumes[-1]
    previous_volume = volumes[-2] if len(volumes) >= 2 else 0.0
    volume_3_avg = _avg(volumes, cfg.volume_short_window)
    volume_10_avg = _avg(volumes, cfg.volume_long_window)

    doubled = previous_volume > 0 and latest_volume >= previous_volume * cfg.volume_spike_ratio
    avg_expanded = volume_10_avg > 0 and volume_3_avg >= volume_10_avg * cfg.volume_avg_ratio
    if not (doubled or avg_expanded):
        return None

    distance_pct = abs(close_now - ema20) / ema20
    if distance_pct > cfg.pullback_pct:
        return None

    return {
        "side": "long_open",
        "price": close_now,
        "trigger": "bull_pullback_long",
        "ema5": ema5,
        "ema10": ema10,
        "ema20": ema20,
        "volume": latest_volume,
        "volume_prev": previous_volume,
        "volume_3_avg": volume_3_avg,
        "volume_10_avg": volume_10_avg,
        "volume_condition": "latest_volume_doubled" if doubled else "three_day_avg_expanded",
    }


def evaluate_breakout_pullback_continuation(
    bars: Sequence[Any],
    cfg: BullPullbackConfig,
) -> SignalDecision | None:
    """Return a long signal after breakout, pullback to breakout level, and reclaim."""
    n = cfg.breakout_window
    lookback = cfg.breakout_pullback_lookback
    if len(bars) < n + lookback + 1:
        return None

    pre_break_window = bars[-(n + lookback + 1):-(lookback + 1)]
    pullback_window = bars[-(lookback + 1):-1]
    breakout_level = max(_high(bar) for bar in pre_break_window)
    pullback_high = max(_high(bar) for bar in pullback_window)
    pullback_low = min(_low(bar) for bar in pullback_window)
    close_now = _close(bars[-1])

    if breakout_level <= 0:
        return None
    breakout_seen = pullback_high > breakout_level
    pullback_near = pullback_low <= breakout_level * (1.0 + cfg.breakout_pullback_pct)
    pullback_not_too_deep = pullback_low >= breakout_level * (1.0 - cfg.breakout_pullback_pct)
    reclaimed = close_now >= breakout_level and close_now >= pullback_high * (1.0 - cfg.breakout_reclaim_pct)
    if not (breakout_seen and pullback_near and pullback_not_too_deep and reclaimed):
        return None

    lookback_high = max(_high(bar) for bar in bars)
    if lookback_high <= 0 or close_now >= lookback_high * cfg.volume_spike_low_price_ratio:
        return None

    return {
        "side": "long_open",
        "price": close_now,
        "trigger": "breakout_pullback_long",
        "lookback_high_18m": lookback_high,
        "close_to_lookback_high": close_now / lookback_high,
        "breakout_level": breakout_level,
        "pullback_low": pullback_low,
        "pullback_high": pullback_high,
        "bars_since_breakout": lookback,
    }


def evaluate_volume_spike_up(
    bars: Sequence[Any],
    cfg: BullPullbackConfig,
    *,
    pct_chg: float | None = None,
) -> SignalDecision | None:
    """Return a long signal when an up day has at least 2x prior five-day average volume."""
    window = cfg.volume_spike_window
    if len(bars) < window + 1 or window <= 0:
        return None

    closes = [_close(bar) for bar in bars]
    volumes = [_volume(bar) for bar in bars]
    volume_5_avg = _avg(volumes[:-1], window)
    if pd.isna(volume_5_avg) or volume_5_avg <= 0:
        return None

    if pct_chg is None or pd.isna(pct_chg):
        is_up_day = closes[-1] > closes[-2]
    else:
        is_up_day = float(pct_chg) > 0
    if not is_up_day:
        return None

    latest_volume = volumes[-1]
    if latest_volume < volume_5_avg * cfg.volume_spike_ratio:
        return None

    lookback_high = max(_high(bar) for bar in bars)
    if lookback_high <= 0 or closes[-1] >= lookback_high * cfg.volume_spike_low_price_ratio:
        return None

    return {
        "side": "long_open",
        "price": closes[-1],
        "trigger": "volume_spike_up_long",
        "volume": latest_volume,
        "volume_prev": volumes[-2],
        "volume_5_avg": volume_5_avg,
        "volume_condition": "latest_volume_vs_five_day_avg",
        "lookback_high_18m": lookback_high,
        "close_to_lookback_high": closes[-1] / lookback_high,
    }


def _empty_opportunities() -> pd.DataFrame:
    return pd.DataFrame(columns=OPPORTUNITY_COLUMNS)


def _prepare_bars_frame(frame: pd.DataFrame, cfg: BullPullbackConfig) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    bars_frame = frame.copy()
    bars_frame["datetime"] = pd.to_datetime(bars_frame["datetime"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        bars_frame[column] = pd.to_numeric(bars_frame[column], errors="coerce")
    if "pct_chg" in bars_frame.columns:
        bars_frame["pct_chg"] = pd.to_numeric(bars_frame["pct_chg"], errors="coerce")
    bars_frame["volume"] = bars_frame["volume"].fillna(0.0)
    bars_frame = bars_frame.dropna(subset=["datetime", "close"]).sort_values("datetime").reset_index(drop=True)

    close = pd.to_numeric(bars_frame["close"], errors="coerce")
    volume = pd.to_numeric(bars_frame["volume"], errors="coerce").fillna(0.0)
    bars_frame["ema5"] = close.ewm(span=cfg.ema_fast, adjust=False).mean()
    bars_frame["ema10"] = close.ewm(span=cfg.ema_mid, adjust=False).mean()
    bars_frame["ema20"] = close.ewm(span=cfg.ema_slow, adjust=False).mean()
    bars_frame["volume_prev"] = volume.shift(1)
    bars_frame["volume_5_avg"] = volume.shift(1).rolling(cfg.volume_spike_window).mean()
    bars_frame["volume_3_avg"] = volume.rolling(cfg.volume_short_window).mean()
    bars_frame["volume_10_avg"] = volume.rolling(cfg.volume_long_window).mean()
    bars_frame["lookback_high_18m"] = (
        bars_frame.set_index("datetime")["high"]
        .rolling(f"{cfg.volume_spike_low_price_lookback_days}D", min_periods=1)
        .max()
        .reset_index(drop=True)
    )
    return bars_frame


def _base_result(signal_row: pd.Series, opportunity_row: pd.Series, signal_type: str, trigger: str) -> dict[str, Any]:
    signal_datetime = pd.Timestamp(signal_row["datetime"]).strftime("%Y-%m-%d %H:%M:%S")
    opportunity_date = pd.Timestamp(opportunity_row["datetime"]).strftime("%Y-%m-%d")
    return {
        "symbol": str(signal_row["symbol"]),
        "exchange": str(signal_row["exchange"]),
        "signal_type": signal_type,
        "signal_datetime": signal_datetime,
        "opportunity_date": opportunity_date,
        "signal_price": float(signal_row["close"]),
        "close_price": float(signal_row["close"]),
        "entry_datetime": signal_datetime,
        "entry_price": float(signal_row["close"]),
        "entry_action": "buy",
        "trigger": trigger,
        "ema5": float(signal_row["ema5"]),
        "ema10": float(signal_row["ema10"]),
        "ema20": float(signal_row["ema20"]),
        "volume": float(signal_row["volume"]),
        "volume_prev": float(signal_row["volume_prev"]) if pd.notna(signal_row["volume_prev"]) else pd.NA,
        "volume_5_avg": float(signal_row["volume_5_avg"]) if pd.notna(signal_row["volume_5_avg"]) else pd.NA,
        "volume_3_avg": float(signal_row["volume_3_avg"]) if pd.notna(signal_row["volume_3_avg"]) else pd.NA,
        "volume_10_avg": float(signal_row["volume_10_avg"]) if pd.notna(signal_row["volume_10_avg"]) else pd.NA,
        "volume_condition": "",
        "lookback_high_18m": pd.NA,
        "close_to_lookback_high": pd.NA,
        "breakout_level": pd.NA,
        "pullback_low": pd.NA,
        "pullback_high": pd.NA,
        "bars_since_breakout": pd.NA,
    }


def _scan_bull_from_prepared(bars_frame: pd.DataFrame, cfg: BullPullbackConfig) -> pd.DataFrame:
    if bars_frame.empty:
        return _empty_opportunities()

    close = pd.to_numeric(bars_frame["close"], errors="coerce")
    volume = pd.to_numeric(bars_frame["volume"], errors="coerce").fillna(0.0)
    min_bars = max(cfg.ema_fast, cfg.ema_mid, cfg.ema_slow, cfg.volume_long_window)
    enough_history = pd.Series(range(len(bars_frame)), index=bars_frame.index) >= min_bars - 1
    ema_stack = (bars_frame["ema5"] >= bars_frame["ema10"]) & (bars_frame["ema10"] >= bars_frame["ema20"])
    volume_doubled = (bars_frame["volume_prev"] > 0) & (volume >= bars_frame["volume_prev"] * cfg.volume_spike_ratio)
    volume_avg_expanded = (bars_frame["volume_10_avg"] > 0) & (bars_frame["volume_3_avg"] >= bars_frame["volume_10_avg"] * cfg.volume_avg_ratio)
    near_ema20 = ((close - bars_frame["ema20"]).abs() / bars_frame["ema20"]) <= cfg.pullback_pct
    signal_mask = enough_history & ema_stack & (volume_doubled | volume_avg_expanded) & near_ema20
    if len(signal_mask) > 0:
        signal_mask.iloc[-1] = False

    results: list[dict[str, Any]] = []
    for signal_index in bars_frame.index[signal_mask]:
        opportunity_index = int(signal_index) + 1
        signal_row = bars_frame.iloc[signal_index]
        opportunity_row = bars_frame.iloc[opportunity_index]
        volume_condition = "latest_volume_doubled" if bool(volume_doubled.iloc[signal_index]) else "three_day_avg_expanded"
        row = _base_result(signal_row, opportunity_row, "bull_pullback_continuation", "bull_pullback_long")
        row["volume_condition"] = volume_condition
        results.append(row)

    if not results:
        return _empty_opportunities()
    return pd.DataFrame(results).loc[:, OPPORTUNITY_COLUMNS]


def _scan_volume_spike_up_from_prepared(bars_frame: pd.DataFrame, cfg: BullPullbackConfig) -> pd.DataFrame:
    if bars_frame.empty or len(bars_frame) < cfg.volume_spike_window + 2:
        return _empty_opportunities()

    close = pd.to_numeric(bars_frame["close"], errors="coerce")
    volume = pd.to_numeric(bars_frame["volume"], errors="coerce").fillna(0.0)
    enough_history = pd.Series(range(len(bars_frame)), index=bars_frame.index) >= cfg.volume_spike_window
    if "pct_chg" in bars_frame.columns:
        pct_chg = pd.to_numeric(bars_frame["pct_chg"], errors="coerce")
        up_day = (pct_chg > 0) | (pct_chg.isna() & (close > close.shift(1)))
    else:
        up_day = close > close.shift(1)
    volume_5_avg = pd.to_numeric(bars_frame["volume_5_avg"], errors="coerce")
    volume_expanded = (volume_5_avg > 0) & (volume >= volume_5_avg * cfg.volume_spike_ratio)
    lookback_high = pd.to_numeric(bars_frame["lookback_high_18m"], errors="coerce")
    low_price = (lookback_high > 0) & (close < lookback_high * cfg.volume_spike_low_price_ratio)
    signal_mask = enough_history & up_day & volume_expanded & low_price
    if len(signal_mask) > 0:
        signal_mask.iloc[-1] = False

    results: list[dict[str, Any]] = []
    for signal_index in bars_frame.index[signal_mask.fillna(False)]:
        opportunity_index = int(signal_index) + 1
        signal_row = bars_frame.iloc[signal_index]
        opportunity_row = bars_frame.iloc[opportunity_index]
        row = _base_result(signal_row, opportunity_row, "volume_spike_up", "volume_spike_up_long")
        row["volume_condition"] = "latest_volume_vs_five_day_avg"
        row["lookback_high_18m"] = float(lookback_high.iloc[signal_index])
        row["close_to_lookback_high"] = float(close.iloc[signal_index] / lookback_high.iloc[signal_index])
        results.append(row)

    if not results:
        return _empty_opportunities()
    return pd.DataFrame(results).loc[:, OPPORTUNITY_COLUMNS]


def _scan_breakout_from_prepared(bars_frame: pd.DataFrame, cfg: BullPullbackConfig) -> pd.DataFrame:
    if bars_frame.empty:
        return _empty_opportunities()

    n = cfg.breakout_window
    lookback = cfg.breakout_pullback_lookback
    if len(bars_frame) < n + lookback + 2:
        return _empty_opportunities()

    high = pd.to_numeric(bars_frame["high"], errors="coerce")
    low = pd.to_numeric(bars_frame["low"], errors="coerce")
    close = pd.to_numeric(bars_frame["close"], errors="coerce")
    breakout_level = high.shift(lookback + 1).rolling(n).max()
    pullback_high = high.shift(1).rolling(lookback).max()
    pullback_low = low.shift(1).rolling(lookback).min()
    breakout_seen = pullback_high > breakout_level
    pullback_near = pullback_low <= breakout_level * (1.0 + cfg.breakout_pullback_pct)
    pullback_not_too_deep = pullback_low >= breakout_level * (1.0 - cfg.breakout_pullback_pct)
    reclaimed = (close >= breakout_level) & (close >= pullback_high * (1.0 - cfg.breakout_reclaim_pct))
    lookback_high = pd.to_numeric(bars_frame["lookback_high_18m"], errors="coerce")
    low_price = (lookback_high > 0) & (close < lookback_high * cfg.volume_spike_low_price_ratio)
    signal_mask = breakout_seen & pullback_near & pullback_not_too_deep & reclaimed & low_price
    if len(signal_mask) > 0:
        signal_mask.iloc[-1] = False

    results: list[dict[str, Any]] = []
    for signal_index in bars_frame.index[signal_mask.fillna(False)]:
        opportunity_index = int(signal_index) + 1
        signal_row = bars_frame.iloc[signal_index]
        opportunity_row = bars_frame.iloc[opportunity_index]
        row = _base_result(signal_row, opportunity_row, "breakout_pullback_continuation", "breakout_pullback_long")
        row["lookback_high_18m"] = float(lookback_high.iloc[signal_index])
        row["close_to_lookback_high"] = float(close.iloc[signal_index] / lookback_high.iloc[signal_index])
        row["breakout_level"] = float(breakout_level.iloc[signal_index])
        row["pullback_low"] = float(pullback_low.iloc[signal_index])
        row["pullback_high"] = float(pullback_high.iloc[signal_index])
        row["bars_since_breakout"] = int(lookback)
        results.append(row)

    if not results:
        return _empty_opportunities()
    return pd.DataFrame(results).loc[:, OPPORTUNITY_COLUMNS]


def scan_bull_pullback_continuation(
    frame: pd.DataFrame,
    cfg: BullPullbackConfig,
) -> pd.DataFrame:
    """Scan the full daily history and emit bull pullback continuation rows."""
    return _scan_bull_from_prepared(_prepare_bars_frame(frame, cfg), cfg)


def scan_breakout_pullback_continuation(
    frame: pd.DataFrame,
    cfg: BullPullbackConfig,
) -> pd.DataFrame:
    """Scan the full daily history and emit breakout pullback continuation rows."""
    return _scan_breakout_from_prepared(_prepare_bars_frame(frame, cfg), cfg)


def scan_volume_spike_up(
    frame: pd.DataFrame,
    cfg: BullPullbackConfig,
) -> pd.DataFrame:
    """Scan the full daily history and emit volume spike up rows."""
    return _scan_volume_spike_up_from_prepared(_prepare_bars_frame(frame, cfg), cfg)


def scan_stock_signal_opportunities(
    frame: pd.DataFrame,
    cfg: BullPullbackConfig,
) -> pd.DataFrame:
    """Scan all stock signal types using one shared prepared bar frame."""
    bars_frame = _prepare_bars_frame(frame, cfg)
    rows = [
        _scan_bull_from_prepared(bars_frame, cfg),
        _scan_breakout_from_prepared(bars_frame, cfg),
        _scan_volume_spike_up_from_prepared(bars_frame, cfg),
    ]
    non_empty = [item for item in rows if not item.empty]
    if not non_empty:
        return _empty_opportunities()
    merged = pd.concat(non_empty, ignore_index=True)
    return merged.sort_values(["opportunity_date", "symbol", "signal_type", "signal_datetime"]).reset_index(drop=True)


__all__ = [
    "BullPullbackConfig",
    "OPPORTUNITY_COLUMNS",
    "SignalDecision",
    "evaluate_breakout_pullback_continuation",
    "evaluate_bull_pullback_continuation",
    "evaluate_volume_spike_up",
    "scan_breakout_pullback_continuation",
    "scan_bull_pullback_continuation",
    "scan_volume_spike_up",
    "scan_stock_signal_opportunities",
]
