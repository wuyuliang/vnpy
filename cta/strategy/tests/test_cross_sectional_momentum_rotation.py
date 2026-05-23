"""Tests for CrossSectionalMomentumRotation strategy."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.strategy.cross_sectional_momentum_rotation import (
    SIGNAL_TYPE,
    CrossSectionalMomentumRotation,
)


def _bars(start_close: float, end_close: float, n_days: int = 90) -> pd.DataFrame:
    dt = pd.date_range("2024-01-01", periods=n_days, freq="D")
    close = np.linspace(float(start_close), float(end_close), int(n_days))
    return pd.DataFrame({"datetime": dt, "close": close})


# 选用真实的 cluster prefix，保证 infer_symbol_cluster 命中：
#   IF/IH/IC = index, RB/HC/I = black, M/Y/P = agri, CU/AL/NI = metal, AU/AG = precious
_MULTI_CLUSTER_UNIVERSE = {
    # index 3
    "IF0": _bars(100.0, 130.0),  # strongest
    "IH0": _bars(100.0, 115.0),
    "IC0": _bars(100.0,  85.0),  # weakest
    # black 3
    "RB0": _bars(100.0, 125.0),
    "HC0": _bars(100.0, 105.0),
    "I0":  _bars(100.0,  80.0),
    # agri 3
    "M0":  _bars(100.0, 120.0),
    "Y0":  _bars(100.0, 110.0),
    "P0":  _bars(100.0,  90.0),
    # metal 3
    "CU0": _bars(100.0, 130.0),
    "AL0": _bars(100.0, 110.0),
    "NI0": _bars(100.0,  88.0),
}

_ALL_CLUSTERS = ("index", "black", "agri", "metal", "precious", "other")


def _enabled_cfg(**kwargs: object) -> CrossSectionalRotationConfig:
    defaults: dict[str, object] = {
        "use_cross_sectional_momentum_rotation": True,
        "use_vol_target_weighting": False,
        "min_history_days": 30,
        "universe_clusters": _ALL_CLUSTERS,
        "enabled_by_cluster_interval": {"*|day": True},
    }
    defaults.update(kwargs)
    return CrossSectionalRotationConfig(**defaults)  # type: ignore[arg-type]


# 2024-03-25 是周一（weekday=0）；后面的测试都以这一天作为 rebalance 触发日。
_REBALANCE_DATE = pd.Timestamp("2024-03-25")


class TestCrossSectionalMomentumRotation(unittest.TestCase):
    def test_default_disabled(self) -> None:
        strat = CrossSectionalMomentumRotation(CrossSectionalRotationConfig())
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertTrue(out.empty)

    def test_signal_type_emitted(self) -> None:
        cfg = _enabled_cfg(cluster_neutral=False, top_quantile=0.6, bottom_quantile=0.4)
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertFalse(out.empty)
        self.assertTrue((out["signal_type"] == SIGNAL_TYPE).all())

    def test_rebalance_only_on_target_weekday(self) -> None:
        cfg = _enabled_cfg(cluster_neutral=False, top_quantile=0.6, bottom_quantile=0.4)
        strat = CrossSectionalMomentumRotation(cfg)
        # 周二（2024-03-26），且 last_rebalance_dt=None
        out = strat.generate_rebalance_candidates(
            date=pd.Timestamp("2024-03-26"),
            universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertTrue(out.empty)

    def test_max_holding_days_forces_rebalance(self) -> None:
        cfg = _enabled_cfg(cluster_neutral=False, max_holding_days=3,
                           top_quantile=0.6, bottom_quantile=0.4)
        strat = CrossSectionalMomentumRotation(cfg)
        # 周三，但距离上次 rebalance 已 4 天 → 应触发
        out = strat.generate_rebalance_candidates(
            date=pd.Timestamp("2024-03-27"),
            universe_bars=_MULTI_CLUSTER_UNIVERSE,
            last_rebalance_dt=pd.Timestamp("2024-03-23"),
        )
        self.assertFalse(out.empty)

    def test_kill_switch_pauses_rotation(self) -> None:
        cfg = _enabled_cfg(cluster_neutral=False, kill_switch_dd_pct=0.15,
                           top_quantile=0.6, bottom_quantile=0.4)
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE,
            universe_bars=_MULTI_CLUSTER_UNIVERSE,
            current_drawdown_pct=0.20,
        )
        self.assertTrue(out.empty)

    def test_excludes_disabled_symbols(self) -> None:
        cfg = _enabled_cfg(cluster_neutral=False, top_quantile=0.6, bottom_quantile=0.4)
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE,
            universe_bars=_MULTI_CLUSTER_UNIVERSE,
            disabled_symbols={"IF0", "CU0"},
        )
        if not out.empty:
            picked = set(out["symbol"].tolist())
            self.assertNotIn("IF0", picked)
            self.assertNotIn("CU0", picked)

    def test_excludes_rollover_window(self) -> None:
        cfg = _enabled_cfg(cluster_neutral=False, top_quantile=0.6, bottom_quantile=0.4)
        strat = CrossSectionalMomentumRotation(cfg)
        calls: list[str] = []

        def stub(symbol: str, as_of: pd.Timestamp, window: int) -> bool:
            calls.append(symbol)
            return symbol == "IF0"  # 只有 IF0 在 rollover 窗口

        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE,
            universe_bars=_MULTI_CLUSTER_UNIVERSE,
            rollover_check=stub,
        )
        self.assertEqual(len(calls), len(_MULTI_CLUSTER_UNIVERSE))
        if not out.empty:
            self.assertNotIn("IF0", out["symbol"].tolist())

    def test_long_only_mode_skips_short(self) -> None:
        cfg = _enabled_cfg(
            cluster_neutral=False, long_only_mode=True,
            top_quantile=0.6, bottom_quantile=0.4,
        )
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertFalse(out.empty)
        self.assertEqual((out["side"] == "short").sum(), 0)
        self.assertGreater((out["side"] == "long").sum(), 0)

    def test_generate_candidates_emits_long_and_short(self) -> None:
        cfg = _enabled_cfg(cluster_neutral=False, top_quantile=0.6, bottom_quantile=0.4)
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertFalse(out.empty)
        self.assertIn("long", set(out["side"]))
        self.assertIn("short", set(out["side"]))

    def test_planned_exit_datetime_equals_entry_plus_max_holding(self) -> None:
        cfg = _enabled_cfg(
            cluster_neutral=False, max_holding_days=5,
            top_quantile=0.6, bottom_quantile=0.4,
        )
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertFalse(out.empty)
        # signal_dt=2024-03-25, entry_dt=2024-03-26, planned_exit=2024-03-31
        self.assertTrue((out["entry_datetime"] == pd.Timestamp("2024-03-26")).all())
        self.assertTrue((out["planned_exit_datetime"] == pd.Timestamp("2024-03-31")).all())

    def test_cluster_neutral_distributes_across_clusters(self) -> None:
        cfg = _enabled_cfg(
            cluster_neutral=True, min_cluster_size=3,
            top_quantile_in_cluster=0.40, bottom_quantile_in_cluster=0.40,
        )
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertFalse(out.empty)
        # universe 含 4 个 cluster 每个 3 个品种 → 都应有候选
        self.assertGreaterEqual(out["cluster"].nunique(), 3)

    def test_disabled_when_no_enabled_cluster_in_universe(self) -> None:
        # universe 只有 metal 品种，但配置仅启用 index|day
        cfg = _enabled_cfg(
            enabled_by_cluster_interval={"index|day": True},
            cluster_neutral=False, top_quantile=0.6, bottom_quantile=0.4,
        )
        metal_only = {
            "CU0": _bars(100.0, 110.0),
            "AL0": _bars(100.0, 105.0),
            "NI0": _bars(100.0,  90.0),
        }
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=metal_only,
        )
        self.assertTrue(out.empty)

    def test_candidates_carry_momentum_score_and_weight(self) -> None:
        cfg = _enabled_cfg(cluster_neutral=False, top_quantile=0.6, bottom_quantile=0.4)
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertFalse(out.empty)
        for col in (
            "symbol", "side", "signal_type", "signal_datetime", "entry_datetime",
            "planned_exit_datetime", "entry_price_hint", "stop_price",
            "momentum_score", "target_weight",
        ):
            self.assertIn(col, out.columns)
        # long 行 target_weight > 0；short < 0
        long_rows = out.loc[out["side"] == "long"]
        short_rows = out.loc[out["side"] == "short"]
        if not long_rows.empty:
            self.assertTrue((long_rows["target_weight"] > 0).all())
        if not short_rows.empty:
            self.assertTrue((short_rows["target_weight"] < 0).all())

    def test_stop_price_below_for_long_above_for_short(self) -> None:
        cfg = _enabled_cfg(
            cluster_neutral=False, stop_loss_pct=0.05,
            top_quantile=0.6, bottom_quantile=0.4,
        )
        strat = CrossSectionalMomentumRotation(cfg)
        out = strat.generate_rebalance_candidates(
            date=_REBALANCE_DATE, universe_bars=_MULTI_CLUSTER_UNIVERSE,
        )
        self.assertFalse(out.empty)
        for _, row in out.iterrows():
            if row["side"] == "long":
                self.assertLess(float(row["stop_price"]), float(row["entry_price_hint"]))
            else:
                self.assertGreater(float(row["stop_price"]), float(row["entry_price_hint"]))


if __name__ == "__main__":
    unittest.main()
