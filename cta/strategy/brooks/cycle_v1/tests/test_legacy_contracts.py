from __future__ import annotations

import pandas as pd

from cta.strategy.brooks.cycle_v1.legacy_adapters.scalp import legacy_wilder_atr
from cta.strategy.brooks.scalp.features import wilder_atr


def test_legacy_adapter_is_read_only_and_preserves_output() -> None:
    high = pd.Series([10.0, 11.0, 12.0, 13.0])
    low = pd.Series([8.0, 9.0, 10.0, 11.0])
    close = pd.Series([9.0, 10.0, 11.0, 12.0])

    expected = wilder_atr(high, low, close, period=3)
    actual = legacy_wilder_atr(high, low, close, period=3)

    pd.testing.assert_series_equal(actual, expected)
