from __future__ import annotations

import importlib
from pathlib import Path

import pandas as pd


def _mock_ohlcv(n: int = 80) -> pd.DataFrame:
    dt = pd.date_range("2024-01-01", periods=n, freq="D")
    base = pd.Series(range(n), dtype=float) * 0.2 + 100.0
    close = base + ((pd.Series(range(n)) % 5) - 2) * 0.1
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.4
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.4
    volume = pd.Series(1000 + (pd.Series(range(n)) % 7) * 10, dtype=float)
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_price_action_split_modules_importable() -> None:
    for mod in (
        "cta.feature.price_action_bars",
        "cta.feature.price_action_swings",
        "cta.feature.price_action",
        "cta.feature.price_action_quality",
        "cta.feature.price_action_structure",
        "cta.feature.price_action_context",
        "cta.feature.price_action_legs",
        "cta.feature.price_action_advanced",
    ):
        importlib.import_module(mod)


def test_price_action_family_compute_still_works() -> None:
    from cta.feature.price_action import compute_price_action_features
    from cta.feature.price_action_advanced import compute_price_action_advanced_features
    from cta.feature.price_action_context import compute_price_action_context_features

    df = _mock_ohlcv(100)
    pa = compute_price_action_features(df)
    ctx = compute_price_action_context_features(df)
    adv = compute_price_action_advanced_features(df)
    assert len(pa) == len(df)
    assert len(ctx) == len(df)
    assert len(adv) == len(df)
    assert "pa_bar_range" in pa.columns
    assert "pa_range_over_atr_20" in ctx.columns
    assert "pa_leg_body_cv_10" in adv.columns


def test_price_action_split_targets_under_500_lines() -> None:
    root = Path(__file__).resolve().parents[2]
    targets = [
        root / "feature" / "price_action_context.py",
        root / "feature" / "price_action_advanced.py",
        root / "feature" / "price_action_structure.py",
        root / "feature" / "price_action_legs.py",
    ]
    for path in targets:
        with path.open(encoding="utf-8") as fh:
            lines = sum(1 for _ in fh)
        assert lines < 500, f"{path} has {lines} lines (expected <500)"


def test_swing_confirmation_is_delayed_by_right_bars() -> None:
    from cta.feature.price_action_swings import swing_high, swing_low

    high = pd.Series([1.0, 2.0, 5.0, 2.0, 1.0])
    low = pd.Series([5.0, 4.0, 1.0, 4.0, 5.0])

    sh = swing_high(high, left=2, right=2)
    sl = swing_low(low, left=2, right=2)

    # pivot 在 index=2，必须等 right=2 根确认后，才可在 index=4 可见
    assert sh.isna().iloc[:4].all()
    assert sl.isna().iloc[:4].all()
    assert float(sh.iloc[4]) == 5.0
    assert float(sl.iloc[4]) == 1.0


def test_dist_to_last_swing_uses_confirmed_swings_only() -> None:
    from cta.feature.price_action_swings import dist_to_last_swing_high

    high = pd.Series([1.0, 2.0, 5.0, 2.0, 1.0])
    close = pd.Series([1.0, 2.0, 5.0, 2.0, 1.0])
    dist = dist_to_last_swing_high(high, close, left=2, right=2)

    # 在确认前不应暴露 swing 值
    assert dist.isna().iloc[:4].all()
    # 确认后才出现到最近 swing 的距离
    assert float(dist.iloc[4]) == -80.0
