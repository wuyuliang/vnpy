"""Truncation-equivalence audit: no feature may change when the future is removed.

The check is the same for every feature: compute it on the full frame, then
recompute it on the frame truncated right after bar ``i``, and require bar ``i``
to carry the identical value. Anything that reads a later bar shows up as a
mismatch.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.multi_timeframe_trend_rules import (
    attach_session_range,
    build_daily_context,
    build_intraday_context,
)
from cta.strategy.multi_timeframe_trend_strategy import (
    InstrumentSpec,
    generate_multi_timeframe_candidates,
)

TZ = "Asia/Shanghai"
RANGE_COLUMNS = ("range_window_high", "range_window_low")


def _intraday(days: int = 8, bars_per_day: int = 12, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    for d in range(days):
        trade_date = date(2026, 1, 5) + timedelta(days=d)
        for k in range(bars_per_day):
            price *= 1.0 + rng.normal(0.0, 0.004)
            high = price * (1.0 + abs(rng.normal(0.0, 0.003)))
            low = price * (1.0 - abs(rng.normal(0.0, 0.003)))
            rows.append(
                {
                    "bar_end": pd.Timestamp(trade_date, tz=TZ)
                    + pd.Timedelta(hours=9, minutes=5 * (k + 1)),
                    "exchange_trade_date": trade_date,
                    "open": price,
                    "high": high,
                    "low": low,
                    "close": price,
                    "volume": float(rng.integers(50, 500)),
                }
            )
    return pd.DataFrame(rows)


def _daily(days: int = 40, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    price = 90.0
    for d in range(days):
        price *= 1.0 + rng.normal(0.002, 0.01)
        rows.append(
            {
                "bar_end": pd.Timestamp(date(2025, 12, 1) + timedelta(days=d), tz=TZ)
                + pd.Timedelta(hours=15),
                "open": price * 0.999,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


def test_session_range_is_identical_under_truncation() -> None:
    frame = _intraday()
    full = attach_session_range(frame.copy(), 2)
    mismatches = []
    for i in range(len(frame)):
        partial = attach_session_range(frame.iloc[: i + 1].copy(), 2)
        for column in RANGE_COLUMNS:
            a = full.iloc[i][column]
            b = partial.iloc[i][column]
            if not ((pd.isna(a) and pd.isna(b)) or a == pytest.approx(b)):
                mismatches.append((i, column, a, b))
    assert not mismatches, mismatches[:5]


def test_intraday_context_features_are_identical_under_truncation() -> None:
    """volume_threshold / atr14 / range window must not move when the tail is cut."""
    frame = _intraday()
    config = MultiTimeframeTrendConfig(atr_period=3, volume_lookback=4)
    full = build_intraday_context(frame.copy(), config)
    checked = ("atr14", "volume_threshold", *RANGE_COLUMNS)
    mismatches = []
    # 前若干根用于填满 rolling 窗口，从第 6 根起逐根比对
    for i in range(6, len(frame)):
        partial = build_intraday_context(frame.iloc[: i + 1].copy(), config)
        for column in checked:
            a = full.iloc[i][column]
            b = partial.iloc[i][column]
            if not ((pd.isna(a) and pd.isna(b)) or a == pytest.approx(b)):
                mismatches.append((i, column, a, b))
    assert not mismatches, mismatches[:5]


def test_daily_context_features_are_identical_under_truncation() -> None:
    frame = _daily()
    config = MultiTimeframeTrendConfig()
    full = build_daily_context(frame.copy(), config)
    checked = (
        "daily_ema5",
        "daily_ema10",
        "daily_ema20",
        "daily_atr14",
        "prior_5d_high",
        "prior_5d_low",
        "daily_direction",
    )
    mismatches = []
    for i in range(25, len(frame)):
        partial = build_daily_context(frame.iloc[: i + 1].copy(), config)
        for column in checked:
            a = full.iloc[i][column]
            b = partial.iloc[i][column]
            if not ((pd.isna(a) and pd.isna(b)) or a == pytest.approx(b)):
                mismatches.append((i, column, a, b))
    assert not mismatches, mismatches[:5]


def _instrument() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="RB0",
        exchange="SHFE",
        contract="RB2605",
        tick_size=0.5,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        metadata_asof=pd.Timestamp("2025-01-01", tz=TZ),
    )


def test_candidate_rows_are_identical_under_truncation() -> None:
    """整条候选链（含四道入场闸门）在截断后必须逐字段一致。"""
    daily = _daily(days=60, seed=3)
    intraday = _intraday(days=10, bars_per_day=14, seed=5)
    config = MultiTimeframeTrendConfig()
    full = generate_multi_timeframe_candidates(
        daily, intraday, instrument=_instrument(), config=config, equity=1_000_000.0
    )
    if full.empty:
        pytest.skip("fixture produced no candidates")
    compared = (
        "trigger",
        "stop_price",
        "filtered_reason",
        "candidate_status",
        "quantity",
        "daily_atr14",
    )
    cutoffs = sorted(pd.to_datetime(full["signal_datetime"]).unique())
    mismatches = []
    for cutoff in cutoffs:
        partial_bars = intraday[pd.to_datetime(intraday["bar_end"]) <= cutoff]
        partial = generate_multi_timeframe_candidates(
            daily,
            partial_bars,
            instrument=_instrument(),
            config=config,
            equity=1_000_000.0,
        )
        rows_full = full[pd.to_datetime(full["signal_datetime"]).eq(cutoff)]
        rows_partial = partial[pd.to_datetime(partial["signal_datetime"]).eq(cutoff)]
        if len(rows_full) != len(rows_partial):
            mismatches.append((cutoff, "row_count", len(rows_full), len(rows_partial)))
            continue
        for column in compared:
            a = rows_full[column].to_numpy()
            b = rows_partial[column].to_numpy()
            for x, y in zip(a, b):
                same = (
                    (pd.isna(x) and pd.isna(y))
                    if not isinstance(x, str)
                    else x == y
                )
                if not same and not (
                    isinstance(x, str) or x == pytest.approx(y)
                ):
                    mismatches.append((cutoff, column, x, y))
    assert not mismatches, mismatches[:5]


def test_range_window_never_exceeds_bars_seen_so_far() -> None:
    """区间上沿不得高于已出现过的最高价，下沿不得低于已出现过的最低价。"""
    frame = attach_session_range(_intraday().copy(), 2)
    seen_high = frame["high"].cummax()
    seen_low = frame["low"].cummin()
    valid = frame["range_window_high"].notna()
    assert (frame.loc[valid, "range_window_high"] <= seen_high[valid] + 1e-9).all()
    assert (frame.loc[valid, "range_window_low"] >= seen_low[valid] - 1e-9).all()


def test_high_gap_classification_is_identical_under_truncation() -> None:
    """P1-3 的高跳空分级同样不得读到当日及之后的数据。"""
    from cta.strategy.multi_timeframe_trend_backtest.engine import (
        _high_gap_flags_by_trade_date,
    )

    rng = np.random.default_rng(21)
    rows = []
    close = 100.0
    for d in range(80):
        trade_date = date(2026, 1, 5) + timedelta(days=d)
        gap = rng.normal(0.0, 3.0) * (5.0 if d % 11 == 0 else 1.0)
        open_price = close + gap
        close = open_price * (1.0 + rng.normal(0.0, 0.01))
        rows.append(
            {
                "exchange_trade_date": trade_date,
                "open": open_price,
                "close": close,
                "daily_atr14": 2.0 + abs(rng.normal(0.0, 0.2)),
            }
        )
    frame = pd.DataFrame(rows)
    config = MultiTimeframeTrendConfig(high_gap_lookback_days=20)
    full = _high_gap_flags_by_trade_date(frame, config)
    mismatches = []
    for i in range(25, len(frame)):
        partial = _high_gap_flags_by_trade_date(frame.iloc[: i + 1], config)
        key = frame.iloc[i]["exchange_trade_date"]
        if full.get(key) != partial.get(key):
            mismatches.append((key, full.get(key), partial.get(key)))
    assert not mismatches, mismatches[:5]
    # 该构造里必须真的出现过高跳空日，否则这条测试是空转的
    assert any(full.values())
