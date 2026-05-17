"""Tests for split baseline modules."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.strategy.baseline_candidate_gen import _resolve_candidate_entry
from cta.strategy.baseline_feature_frame import prepare_master_feature_frame
from cta.strategy.baseline_helpers import _normalize_intervals, _safe_bool, _side_allowed
from cta.strategy.baseline_strategies import create_baseline_strategy
from cta.strategy.skill_tight_range_breakout import ContractSpec


def _mk_bars(n: int = 180, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.03, 0.6, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.1, 0.7, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 0.7, size=n)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2020-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(100, 1000, size=n),
            "open_interest": rng.integers(1000, 2000, size=n),
            "turnover": rng.uniform(1e6, 2e6, size=n),
        }
    )


class TestBaselineSplitModules(unittest.TestCase):
    def test_normalize_intervals_mixed(self) -> None:
        out = _normalize_intervals(["day,60min", "30min", "day"])
        self.assertEqual(out, ("day", "minute60", "minute30"))

    def test_safe_bool(self) -> None:
        self.assertFalse(_safe_bool(None))
        self.assertFalse(_safe_bool(np.nan))
        self.assertTrue(_safe_bool(1))

    def test_side_allowed(self) -> None:
        self.assertTrue(_side_allowed("both", "long"))
        self.assertTrue(_side_allowed("both", "short"))
        self.assertFalse(_side_allowed("long", "short"))

    def test_create_strategy_and_scan(self) -> None:
        frame = prepare_master_feature_frame(_mk_bars(), interval="day")
        contract = ContractSpec(
            symbol="RB0",
            exchange="SHFE",
            multiplier=10.0,
            tick_size=1.0,
            commission_rate=0.0001,
            slippage_ticks=1.0,
        )
        strategy = create_baseline_strategy("donchian_breakout", frame, contract, "both")
        for i in range(10, 30):
            orders = strategy.on_bar(i, frame.iloc[i], position=0)
            self.assertIsInstance(orders, list)

    def test_resolve_candidate_entry_stop_long(self) -> None:
        order = {"side": "long", "order_type": "stop", "price": 101.0}
        next_bar = pd.Series({"open": 101.2, "high": 102.0, "low": 100.9})
        ok, entry, trigger = _resolve_candidate_entry(order, next_bar)
        self.assertTrue(ok)
        self.assertAlmostEqual(entry, 101.2, places=8)
        self.assertAlmostEqual(trigger, 101.0, places=8)


if __name__ == "__main__":
    unittest.main()
