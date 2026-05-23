"""Unit tests for the cross-sectional rotation portfolio executor."""
from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.portfolio_logic.cross_sectional_rotation_executor import (
    CrossSectionalRotationExecutor,
)
from cta.portfolio_logic.portfolio_state import PortfolioState


def _bars(start_close: float, end_close: float) -> pd.DataFrame:
    dt = pd.date_range("2024-01-01", periods=90, freq="D")
    return pd.DataFrame(
        {
            "datetime": dt,
            "close": np.linspace(float(start_close), float(end_close), len(dt)),
        }
    )


def _rotation_cfg() -> CrossSectionalRotationConfig:
    return CrossSectionalRotationConfig(
        use_cross_sectional_momentum_rotation=True,
        enabled_by_cluster_interval={"index|day": True},
        cluster_neutral=True,
        min_cluster_size=3,
        use_vol_target_weighting=False,
        max_symbol_notional_pct=0.25,
        universe_clusters=("index",),
    )


class TestCrossSectionalRotationExecutor(unittest.TestCase):
    def test_step_emits_target_notional_intents_on_rebalance_day(self) -> None:
        universe = {
            "IF0": _bars(100.0, 130.0),
            "IH0": _bars(100.0, 110.0),
            "IC0": _bars(100.0, 80.0),
        }
        executor = CrossSectionalRotationExecutor(
            cfg=_rotation_cfg(),
            universe_provider=lambda _ts: universe,
            exchange_by_symbol={"IF0": "CFFEX", "IH0": "CFFEX", "IC0": "CFFEX"},
        )

        intents = executor.step(
            pd.Timestamp("2024-03-25"),
            PortfolioState(equity=1_000_000.0),
        )

        self.assertGreaterEqual(len(intents), 2)
        self.assertEqual({intent.signal_type for intent in intents}, {"cross_sectional_momentum"})
        self.assertEqual({intent.exchange for intent in intents}, {"CFFEX"})
        self.assertTrue(all(intent.target_notional > 0.0 for intent in intents))
        self.assertIn("long", {intent.side for intent in intents})
        self.assertIn("short", {intent.side for intent in intents})

    def test_step_skips_intent_when_target_weight_is_nan(self) -> None:
        """H3 回归：candidate 中 target_weight=NaN 时应被跳过，不产生 NaN intent。"""
        from unittest import mock

        executor = CrossSectionalRotationExecutor(
            cfg=_rotation_cfg(),
            universe_provider=lambda _ts: {"IF0": _bars(100.0, 110.0)},
        )
        # 构造一份强制带 NaN target_weight 的 candidate
        fake_cand = pd.DataFrame([
            {
                "symbol": "IF0", "cluster": "index", "side": "long",
                "signal_type": "cross_sectional_momentum",
                "signal_datetime": pd.Timestamp("2024-03-25"),
                "entry_datetime": pd.Timestamp("2024-03-26"),
                "planned_exit_datetime": pd.Timestamp("2024-03-31"),
                "entry_price_hint": 110.0, "stop_price": 104.5,
                "momentum_score": 0.10, "target_weight": float("nan"),
            },
            {
                "symbol": "IH0", "cluster": "index", "side": "long",
                "signal_type": "cross_sectional_momentum",
                "signal_datetime": pd.Timestamp("2024-03-25"),
                "entry_datetime": pd.Timestamp("2024-03-26"),
                "planned_exit_datetime": pd.Timestamp("2024-03-31"),
                "entry_price_hint": 110.0, "stop_price": 104.5,
                "momentum_score": 0.05, "target_weight": 0.05,  # 正常
            },
        ])
        with mock.patch.object(executor.rotation, "generate_rebalance_candidates", return_value=fake_cand):
            intents = executor.step(
                pd.Timestamp("2024-03-25"),
                PortfolioState(equity=1_000_000.0),
            )
        # 只剩有效 weight 的那一条
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].symbol, "IH0")
        self.assertTrue(math.isfinite(intents[0].target_notional))
        self.assertGreater(intents[0].target_notional, 0.0)

    def test_step_remembers_last_rebalance_for_forced_cadence(self) -> None:
        universe = {
            "IF0": _bars(100.0, 130.0),
            "IH0": _bars(100.0, 110.0),
            "IC0": _bars(100.0, 80.0),
        }
        executor = CrossSectionalRotationExecutor(
            cfg=_rotation_cfg(),
            universe_provider=lambda _ts: universe,
        )
        state = PortfolioState(equity=500_000.0)

        first = executor.step(pd.Timestamp("2024-03-25"), state)
        second = executor.step(pd.Timestamp("2024-03-26"), state)
        forced = executor.step(pd.Timestamp("2024-03-30"), state)

        self.assertFalse(not first)
        self.assertEqual(second, [])
        self.assertFalse(not forced)


if __name__ == "__main__":
    unittest.main()
