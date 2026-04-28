"""TDD tests for skill-based tight range breakout strategy."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.config.skill_tight_range_breakout_config import StrategyConfig
from cta.strategy.skill_tight_range_breakout import (
    ContractSpec,
    SkillTightRangeBreakoutStrategy,
    prepare_strategy_frame,
)


def _make_breakout_df(n: int = 90, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.2, size=n))

    # 先造一个窄幅区间，然后单边向上突破
    close[45:56] = close[44] + rng.normal(0.0, 0.03, size=11)
    close[56] = close[55] + 2.0

    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    volume = rng.integers(100, 400, size=n)
    volume[56] = int(volume.mean() * 2.0)

    return pd.DataFrame(
        {
            "datetime": pd.date_range("2020-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def _make_short_breakout_df(n: int = 90, seed: int = 17) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.2, size=n))

    close[45:56] = close[44] + rng.normal(0.0, 0.03, size=11)
    close[56] = close[55] - 2.0

    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    volume = rng.integers(100, 400, size=n)
    volume[56] = int(volume.mean() * 2.0)

    return pd.DataFrame(
        {
            "datetime": pd.date_range("2020-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


class TestSkillTightRangeStrategy(unittest.TestCase):
    def setUp(self) -> None:
        self.df = _make_breakout_df()
        self.cfg = StrategyConfig(
            lookback=10,
            alpha=1.8,
            min_count=4,
            min_breakout_score=0.25,
            lots=1,
            risk_per_trade_pct=0.005,
            initial_stop_atr_mult=1.2,
            trailing_stop_atr_mult=2.0,
            max_holding_bars=20,
            align_trend_direction=False,
        )
        self.contract = ContractSpec(
            symbol="RB0",
            exchange="SHFE",
            multiplier=10.0,
            tick_size=1.0,
            commission_rate=0.0001,
            slippage_ticks=1.5,
        )

    def test_prepare_strategy_frame_adds_columns(self) -> None:
        out = prepare_strategy_frame(self.df, self.cfg, interval="day")
        for col in (
            "atr14",
            "tr_valid",
            "tr_upper",
            "tr_lower",
            "trend_dir",
            "breakout_score",
            "breakout_pass",
        ):
            self.assertIn(col, out.columns)
        self.assertEqual(len(out), len(self.df))

    def test_prepare_strategy_frame_interval_aliases(self) -> None:
        for interval in ("day", "60min", "30min", "15min", "5min", "min"):
            out = prepare_strategy_frame(self.df, self.cfg, interval=interval)
            self.assertEqual(len(out), len(self.df))

    def test_strategy_emits_entry_signal(self) -> None:
        prepared = prepare_strategy_frame(self.df, self.cfg, interval="day")
        strategy = SkillTightRangeBreakoutStrategy(prepared, self.cfg, self.contract)

        found = None
        for i in range(len(prepared) - 2):
            orders = strategy.on_bar(i, prepared.iloc[i], position=0)
            if orders:
                found = orders[0]
                break

        self.assertIsNotNone(found, "应至少出现一次入场信号")
        self.assertIn(found["side"], {"long", "short"})
        self.assertEqual(found["order_type"], "stop")
        self.assertGreater(float(found["price"]), 0.0)
        self.assertGreaterEqual(int(found["lots"]), 1)

    def test_compute_lots_has_floor(self) -> None:
        prepared = prepare_strategy_frame(self.df, self.cfg, interval="day")
        strategy = SkillTightRangeBreakoutStrategy(prepared, self.cfg, self.contract)
        lots = strategy.compute_lots(atr_value=1e9)
        self.assertEqual(lots, 1)

    def test_trade_side_mode_short_only_can_emit_short(self) -> None:
        cfg = StrategyConfig(
            lookback=10,
            alpha=1.8,
            min_count=4,
            min_breakout_score=0.25,
            lots=1,
            risk_per_trade_pct=0.005,
            initial_stop_atr_mult=1.2,
            trailing_stop_atr_mult=2.0,
            max_holding_bars=20,
            align_trend_direction=False,
            trade_side_mode="short",
        )
        short_df = _make_short_breakout_df()
        prepared = prepare_strategy_frame(short_df, cfg, interval="day")
        strategy = SkillTightRangeBreakoutStrategy(prepared, cfg, self.contract)

        seen_short = False
        for i in range(len(prepared) - 2):
            orders = strategy.on_bar(i, prepared.iloc[i], position=0)
            if orders and orders[0]["side"] == "short":
                seen_short = True
                break
        self.assertTrue(seen_short, "short-only 模式应允许空头开仓")

    def test_trade_side_mode_long_blocks_short(self) -> None:
        cfg = StrategyConfig(
            lookback=10,
            alpha=1.8,
            min_count=4,
            min_breakout_score=0.25,
            lots=1,
            risk_per_trade_pct=0.005,
            initial_stop_atr_mult=1.2,
            trailing_stop_atr_mult=2.0,
            max_holding_bars=20,
            align_trend_direction=False,
            trade_side_mode="long",
        )
        short_df = _make_short_breakout_df()
        prepared = prepare_strategy_frame(short_df, cfg, interval="day")
        strategy = SkillTightRangeBreakoutStrategy(prepared, cfg, self.contract)

        for i in range(len(prepared) - 2):
            orders = strategy.on_bar(i, prepared.iloc[i], position=0)
            if orders:
                self.assertNotEqual(
                    orders[0]["side"],
                    "short",
                    "long-only 模式不应发出 short 入场单",
                )


if __name__ == "__main__":
    unittest.main()
