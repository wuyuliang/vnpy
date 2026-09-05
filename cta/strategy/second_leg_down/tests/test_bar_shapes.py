from __future__ import annotations

import pandas as pd
import pytest

from cta.strategy.common.bar_shapes import (
    bearish_ema_alignment,
    breaks_structure_low,
    causal_volume_baseline,
    has_small_lower_wick,
    has_small_upper_wick,
    is_big_bear_body,
    is_small_body,
    is_volume_surge,
)


@pytest.mark.parametrize(
    ("close", "expected"),
    [(97.0, True), (97.000001, False)],
)
def test_big_bear_body_boundary(close: float, expected: bool) -> None:
    assert is_big_bear_body(100.0, close, 1.0, 3.0) is expected


@pytest.mark.parametrize(
    ("close", "expected"),
    [(100.5, True), (100.500001, False)],
)
def test_small_body_boundary(close: float, expected: bool) -> None:
    assert is_small_body(100.0, close, 1.0, 0.5) is expected


@pytest.mark.parametrize(
    ("high", "expected"),
    [(100.45, True), (100.450001, False)],
)
def test_upper_wick_ratio_boundary(high: float, expected: bool) -> None:
    assert has_small_upper_wick(100.0, high, 97.0, 0.15) is expected


@pytest.mark.parametrize(
    ("low", "expected"),
    [(96.55, True), (96.549999, False)],
)
def test_lower_wick_ratio_boundary(low: float, expected: bool) -> None:
    assert has_small_lower_wick(100.0, 97.0, low, 0.15) is expected


@pytest.mark.parametrize(
    ("volume", "samples", "expected"),
    [(200.0, 15, True), (199.999, 15, False), (200.0, 14, False)],
)
def test_volume_surge_and_minimum_samples(
    volume: float,
    samples: int,
    expected: bool,
) -> None:
    assert is_volume_surge(volume, 100.0, samples, 2.0, 15) is expected


def test_volume_baseline_excludes_current_and_segment_first_bars() -> None:
    baseline, samples = causal_volume_baseline(
        pd.Series([100.0, 10.0, 10.0, 20.0]),
        pd.Series([True, False, False, False]),
        window=3,
    )

    assert baseline.iloc[3] == pytest.approx(10.0)
    assert samples.iloc[3] == 2


@pytest.mark.parametrize(
    ("fast", "mid", "slow", "expected"),
    [(1.0, 2.0, 3.0, True), (1.0, 1.0, 3.0, False)],
)
def test_bearish_ema_alignment_is_strict(
    fast: float,
    mid: float,
    slow: float,
    expected: bool,
) -> None:
    assert bearish_ema_alignment(fast, mid, slow) is expected


@pytest.mark.parametrize(
    ("low", "expected"),
    [(99.999, True), (100.0, False)],
)
def test_structure_break_is_strict(low: float, expected: bool) -> None:
    assert breaks_structure_low(low, 100.0) is expected
