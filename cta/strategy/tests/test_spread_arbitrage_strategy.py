"""Tests for spread arbitrage strategy (cross-instrument baseline)."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.config.spread_arbitrage_config import SpreadArbitrageConfig
from cta.config.spread_pair_registry import SpreadPair
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.strategy.spread_arbitrage_strategy import SIGNAL_TYPE, SpreadArbitrageStrategy


def _mk_pair(
    pair_key: str = "rb_hc",
    *,
    leg1: str = "RB",
    leg2: str = "HC",
    cluster: str = "black",
) -> SpreadPair:
    return SpreadPair(
        pair_key=pair_key,
        pair_type="cross_instrument",
        leg1_symbol=leg1,
        leg2_symbol=leg2,
        leg1_exchange="SHFE",
        leg2_exchange="SHFE",
        hedge_ratio=1.0,
        cluster=cluster,
    )


def _mk_bars(close_values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=len(close_values), freq="D"),
            "close": close_values,
        }
    )


def _run_all_steps(
    strategy: SpreadArbitrageStrategy,
    bars_by_symbol: dict[str, pd.DataFrame],
) -> list[list[object]]:
    timeline = pd.to_datetime(bars_by_symbol[next(iter(bars_by_symbol))]["datetime"])
    out: list[list[object]] = []
    state = PortfolioState(equity=1_000_000.0)
    for ts in timeline:
        out.append(strategy.step(ts, bars_by_symbol, state))
    return out


class TestSpreadArbitrageStrategy(unittest.TestCase):
    def test_default_disabled(self) -> None:
        pair = _mk_pair()
        strategy = SpreadArbitrageStrategy(cfg=SpreadArbitrageConfig(), pairs=(pair,), interval="day")
        bars = {
            "RB": _mk_bars([100, 101, 99, 100, 101, 120]),
            "HC": _mk_bars([100, 100, 100, 100, 100, 100]),
        }
        emitted = _run_all_steps(strategy, bars)
        self.assertTrue(all(len(items) == 0 for items in emitted))

    def test_entry_short_spread_when_zscore_high(self) -> None:
        pair = _mk_pair()
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            enabled_by_pair_interval={"rb_hc|day": True},
        )
        strategy = SpreadArbitrageStrategy(cfg=cfg, pairs=(pair,), interval="day")
        bars = {
            "RB": _mk_bars([100, 101, 99, 100, 101, 120]),
            "HC": _mk_bars([100, 100, 100, 100, 100, 100]),
        }
        emitted = _run_all_steps(strategy, bars)
        last = emitted[-1]
        self.assertEqual(len(last), 2)
        self.assertTrue(all(intent.signal_type == SIGNAL_TYPE for intent in last))
        sides = {(intent.leg_id, intent.order_side) for intent in last}
        self.assertEqual(sides, {("leg1", "short"), ("leg2", "long")})

    def test_entry_long_spread_when_zscore_low(self) -> None:
        pair = _mk_pair()
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            enabled_by_pair_interval={"rb_hc|day": True},
        )
        strategy = SpreadArbitrageStrategy(cfg=cfg, pairs=(pair,), interval="day")
        bars = {
            "RB": _mk_bars([100, 101, 99, 100, 101, 80]),
            "HC": _mk_bars([100, 100, 100, 100, 100, 100]),
        }
        emitted = _run_all_steps(strategy, bars)
        last = emitted[-1]
        self.assertEqual(len(last), 2)
        sides = {(intent.leg_id, intent.order_side) for intent in last}
        self.assertEqual(sides, {("leg1", "long"), ("leg2", "short")})

    def test_no_entry_in_mid_zone(self) -> None:
        pair = _mk_pair()
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            enabled_by_pair_interval={"rb_hc|day": True},
        )
        strategy = SpreadArbitrageStrategy(cfg=cfg, pairs=(pair,), interval="day")
        bars = {
            "RB": _mk_bars([100, 101, 99, 100, 101, 100]),
            "HC": _mk_bars([100, 100, 100, 100, 100, 100]),
        }
        emitted = _run_all_steps(strategy, bars)
        self.assertEqual(len(emitted[-1]), 0)

    def test_exit_mean_revert(self) -> None:
        pair = _mk_pair()
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            z_exit=1.2,
            enabled_by_pair_interval={"rb_hc|day": True},
        )
        strategy = SpreadArbitrageStrategy(cfg=cfg, pairs=(pair,), interval="day")
        bars = {
            "RB": _mk_bars([100, 101, 99, 100, 101, 120, 100]),
            "HC": _mk_bars([100, 100, 100, 100, 100, 100, 100]),
        }
        emitted = _run_all_steps(strategy, bars)
        self.assertEqual(len(emitted[-2]), 2)  # entry
        self.assertEqual(len(emitted[-1]), 2)  # exit
        self.assertTrue(all(intent.exit_reason == "spread_mean_revert" for intent in emitted[-1]))

    def test_max_concurrent_spreads_cap(self) -> None:
        pair1 = _mk_pair("rb_hc", leg1="RB", leg2="HC", cluster="black")
        pair2 = _mk_pair("m_rm", leg1="M", leg2="RM", cluster="agri")
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            max_concurrent_spreads=1,
            enabled_by_pair_interval={"rb_hc|day": True, "m_rm|day": True},
        )
        strategy = SpreadArbitrageStrategy(cfg=cfg, pairs=(pair1, pair2), interval="day")
        bars = {
            "RB": _mk_bars([100, 101, 99, 100, 101, 120]),
            "HC": _mk_bars([100, 100, 100, 100, 100, 100]),
            "M": _mk_bars([100, 101, 99, 100, 101, 120]),
            "RM": _mk_bars([100, 100, 100, 100, 100, 100]),
        }
        emitted = _run_all_steps(strategy, bars)
        self.assertEqual(len(emitted[-1]), 2)

    def test_max_concurrent_per_cluster_cap(self) -> None:
        pair1 = _mk_pair("rb_hc", leg1="RB", leg2="HC", cluster="black")
        pair2 = _mk_pair("j_jm", leg1="J", leg2="JM", cluster="black")
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            max_concurrent_spreads=5,
            max_concurrent_per_cluster=1,
            enabled_by_pair_interval={"rb_hc|day": True, "j_jm|day": True},
        )
        strategy = SpreadArbitrageStrategy(cfg=cfg, pairs=(pair1, pair2), interval="day")
        bars = {
            "RB": _mk_bars([100, 101, 99, 100, 101, 120]),
            "HC": _mk_bars([100, 100, 100, 100, 100, 100]),
            "J": _mk_bars([100, 101, 99, 100, 101, 120]),
            "JM": _mk_bars([100, 100, 100, 100, 100, 100]),
        }
        emitted = _run_all_steps(strategy, bars)
        self.assertEqual(len(emitted[-1]), 2)

    def test_pair_disabled_by_pair_interval(self) -> None:
        pair = _mk_pair()
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            enabled_by_pair_interval={"rb_hc|60min": True},
        )
        strategy = SpreadArbitrageStrategy(cfg=cfg, pairs=(pair,), interval="day")
        bars = {
            "RB": _mk_bars([100, 101, 99, 100, 101, 120]),
            "HC": _mk_bars([100, 100, 100, 100, 100, 100]),
        }
        emitted = _run_all_steps(strategy, bars)
        self.assertTrue(all(len(items) == 0 for items in emitted))

    def test_calendar_rollover_forced_exit(self) -> None:
        pair = SpreadPair(
            pair_key="rb_cal_1_3",
            pair_type="calendar",
            leg1_symbol="RB2401.SHF",
            leg2_symbol="RB2403.SHF",
            leg1_exchange="SHFE",
            leg2_exchange="SHFE",
            hedge_ratio=1.0,
            cluster="black",
        )
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            z_exit=0.5,
            rollover_days_before_expiry=10,
            enabled_by_pair_interval={"rb_cal_1_3|day": True},
        )
        strategy = SpreadArbitrageStrategy(cfg=cfg, pairs=(pair,), interval="day")
        near = _mk_bars([100, 101, 99, 100, 101, 120, 121]).copy()
        near["near_days_to_expiry"] = [20, 18, 15, 12, 11, 10, 8]
        far = _mk_bars([100, 100, 100, 100, 100, 100, 100]).copy()
        bars = {
            "RB2401.SHF": near,
            "RB2403.SHF": far,
        }
        emitted = _run_all_steps(strategy, bars)
        self.assertEqual(len(emitted[-2]), 2)  # day 6 entry
        self.assertEqual(len(emitted[-1]), 2)  # day 7 forced rollover exit
        self.assertTrue(
            all(intent.exit_reason == "calendar_rollover_forced" for intent in emitted[-1])
        )


if __name__ == "__main__":
    unittest.main()
