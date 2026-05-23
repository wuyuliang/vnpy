"""Tests for cross_sectional_rank module."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.feature.cross_sectional_rank import (
    attach_target_weights,
    compute_cross_sectional_momentum,
    rank_within_cluster,
    rank_within_universe,
)


def _bars(start_close: float, end_close: float, n_days: int = 90) -> pd.DataFrame:
    """构造从 2024-01-01 起 n_days 天的 day-level 数据，close 线性插值。"""
    dt = pd.date_range("2024-01-01", periods=n_days, freq="D")
    close = np.linspace(float(start_close), float(end_close), int(n_days))
    return pd.DataFrame({"datetime": dt, "close": close})


def _make_momentum_df(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    """构造 momentum_df：(symbol, cluster, momentum_score, realized_vol)。"""
    return pd.DataFrame(
        [
            {
                "symbol": sym, "cluster": cluster,
                "momentum_score": float(score),
                "realized_vol": float(rvol),
                "valid_for_ranking": bool(np.isfinite(score)),
            }
            for sym, cluster, score, rvol in rows
        ]
    )


def _wildcard_cfg(**kwargs: object) -> CrossSectionalRotationConfig:
    """启用 + 通配 day cluster 的便捷 cfg 构造。"""
    defaults: dict[str, object] = {
        "use_cross_sectional_momentum_rotation": True,
        "use_vol_target_weighting": False,
        "enabled_by_cluster_interval": {"*|day": True},
    }
    defaults.update(kwargs)
    return CrossSectionalRotationConfig(**defaults)  # type: ignore[arg-type]


class TestComputeMomentum(unittest.TestCase):
    def test_momentum_score_positive_when_uptrend(self) -> None:
        bars = {"IF0": _bars(100.0, 110.0, 90)}
        out = compute_cross_sectional_momentum(
            bars, lookback_days=60, skip_recent_days=5,
            as_of=pd.Timestamp("2024-03-30"),
        )
        self.assertEqual(len(out), 1)
        score = float(out["momentum_score"].iloc[0])
        self.assertTrue(np.isfinite(score))
        self.assertGreater(score, 0.0)
        self.assertTrue(bool(out["valid_for_ranking"].iloc[0]))

    def test_momentum_score_negative_when_downtrend(self) -> None:
        bars = {"IF0": _bars(110.0, 100.0, 90)}
        out = compute_cross_sectional_momentum(
            bars, lookback_days=60, skip_recent_days=5,
            as_of=pd.Timestamp("2024-03-30"),
        )
        self.assertLess(float(out["momentum_score"].iloc[0]), 0.0)

    def test_skip_recent_days_changes_score(self) -> None:
        bars = {"IF0": _bars(100.0, 110.0, 90)}
        no_skip = compute_cross_sectional_momentum(
            bars, lookback_days=60, skip_recent_days=0,
            as_of=pd.Timestamp("2024-03-30"),
        )
        with_skip = compute_cross_sectional_momentum(
            bars, lookback_days=60, skip_recent_days=5,
            as_of=pd.Timestamp("2024-03-30"),
        )
        self.assertNotAlmostEqual(
            float(no_skip["momentum_score"].iloc[0]),
            float(with_skip["momentum_score"].iloc[0]),
            places=4,
        )

    def test_warmup_returns_nan_when_history_too_short(self) -> None:
        bars = {"IF0": _bars(100.0, 110.0, 20)}
        out = compute_cross_sectional_momentum(
            bars, lookback_days=60, skip_recent_days=5,
            as_of=pd.Timestamp("2024-01-20"),
        )
        self.assertTrue(pd.isna(out["momentum_score"].iloc[0]))
        self.assertFalse(bool(out["valid_for_ranking"].iloc[0]))

    def test_cluster_inferred_from_symbol(self) -> None:
        bars = {
            "IF0": _bars(100.0, 110.0),
            "RB0": _bars(4000.0, 4200.0),
            "M0": _bars(3000.0, 3100.0),
        }
        out = compute_cross_sectional_momentum(
            bars, lookback_days=60, skip_recent_days=5,
            as_of=pd.Timestamp("2024-03-30"),
        )
        clusters = dict(zip(out["symbol"], out["cluster"]))
        self.assertEqual(clusters["IF0"], "index")
        self.assertEqual(clusters["RB0"], "black")
        self.assertEqual(clusters["M0"], "agri")

    def test_realized_vol_computed_when_requested(self) -> None:
        bars = {"IF0": _bars(100.0, 110.0, 90)}
        out = compute_cross_sectional_momentum(
            bars, lookback_days=60, skip_recent_days=5,
            as_of=pd.Timestamp("2024-03-30"),
            realized_vol_window_days=20,
        )
        rvol = float(out["realized_vol"].iloc[0])
        self.assertTrue(np.isfinite(rvol))
        self.assertGreater(rvol, 0.0)


class TestRanking(unittest.TestCase):
    def test_rank_within_universe_picks_top_and_bottom(self) -> None:
        # 5 个样本，top_quantile=0.80 → percentile >= 0.80 命中 rank 1 / 2 两位
        # （percentile=1.0 / 0.8）；bottom_quantile=0.20 命中 rank 5（percentile=0.2）。
        cfg = _wildcard_cfg(top_quantile=0.80, bottom_quantile=0.20)
        rows = [
            ("A0", "metal", 0.10, 0.02),
            ("B0", "metal", 0.05, 0.02),
            ("C0", "metal", 0.00, 0.02),
            ("D0", "metal", -0.05, 0.02),
            ("E0", "metal", -0.10, 0.02),
        ]
        out = rank_within_universe(_make_momentum_df(rows), cfg=cfg)
        sides = dict(zip(out["symbol"], out["selected_side"]))
        self.assertEqual(sides["A0"], "long")
        self.assertEqual(sides["B0"], "long")
        self.assertEqual(sides["C0"], "")
        self.assertEqual(sides["D0"], "")
        self.assertEqual(sides["E0"], "short")

    def test_rank_within_universe_long_only_skips_short(self) -> None:
        cfg = _wildcard_cfg(
            top_quantile=0.80, bottom_quantile=0.20, long_only_mode=True,
        )
        rows = [
            ("A0", "metal", 0.10, 0.02),
            ("B0", "metal", -0.10, 0.02),
            ("C0", "metal", 0.05, 0.02),
            ("D0", "metal", -0.05, 0.02),
            ("E0", "metal", 0.00, 0.02),
        ]
        out = rank_within_universe(_make_momentum_df(rows), cfg=cfg)
        self.assertGreater((out["selected_side"] == "long").sum(), 0)
        self.assertEqual((out["selected_side"] == "short").sum(), 0)

    def test_rank_within_cluster_distributes_across_clusters(self) -> None:
        cfg = _wildcard_cfg(
            cluster_neutral=True, min_cluster_size=3,
            top_quantile_in_cluster=0.30, bottom_quantile_in_cluster=0.30,
        )
        rows = [
            ("A0", "metal", 0.20, 0.02), ("B0", "metal", 0.10, 0.02),
            ("C0", "metal", 0.00, 0.02), ("D0", "metal", -0.10, 0.02),
            ("E0", "metal", -0.20, 0.02),
            ("F0", "black", 0.30, 0.02), ("G0", "black", 0.10, 0.02),
            ("H0", "black", 0.00, 0.02), ("I0", "black", -0.10, 0.02),
        ]
        out = rank_within_cluster(_make_momentum_df(rows), cfg=cfg)
        longs = set(out.loc[out["selected_side"] == "long", "symbol"])
        shorts = set(out.loc[out["selected_side"] == "short", "symbol"])
        self.assertIn("A0", longs)
        self.assertIn("E0", shorts)
        self.assertIn("F0", longs)
        self.assertIn("I0", shorts)

    def test_rank_within_cluster_skips_small_cluster(self) -> None:
        cfg = _wildcard_cfg(cluster_neutral=True, min_cluster_size=3)
        rows = [
            ("A0", "metal", 0.20, 0.02), ("B0", "metal", -0.20, 0.02),  # 2 个
            ("F0", "black", 0.30, 0.02), ("G0", "black", 0.10, 0.02),
            ("H0", "black", 0.00, 0.02), ("I0", "black", -0.10, 0.02),
        ]
        out = rank_within_cluster(_make_momentum_df(rows), cfg=cfg)
        metal_sel = out.loc[out["cluster"] == "metal", "selected_side"]
        self.assertTrue((metal_sel == "").all())
        black_sel = out.loc[out["cluster"] == "black", "selected_side"]
        self.assertGreater((black_sel != "").sum(), 0)

    def test_rank_within_cluster_long_only_skips_short(self) -> None:
        cfg = _wildcard_cfg(
            cluster_neutral=True, min_cluster_size=3, long_only_mode=True,
        )
        rows = [
            ("A0", "metal", 0.20, 0.02), ("B0", "metal", 0.10, 0.02),
            ("C0", "metal", 0.00, 0.02), ("D0", "metal", -0.20, 0.02),
        ]
        out = rank_within_cluster(_make_momentum_df(rows), cfg=cfg)
        self.assertEqual((out["selected_side"] == "short").sum(), 0)

    def test_rank_ignores_nan_scores(self) -> None:
        cfg = _wildcard_cfg(top_quantile=0.80, bottom_quantile=0.20)
        rows = [
            ("A0", "metal", 0.10, 0.02),
            ("B0", "metal", float("nan"), 0.02),  # warmup
            ("C0", "metal", -0.10, 0.02),
        ]
        out = rank_within_universe(_make_momentum_df(rows), cfg=cfg)
        sides = dict(zip(out["symbol"], out["selected_side"]))
        self.assertEqual(sides["B0"], "")  # 不参与排名


class TestAttachTargetWeights(unittest.TestCase):
    def test_target_weight_sign_matches_side(self) -> None:
        cfg = _wildcard_cfg(gross_exposure_target=0.50)
        df = pd.DataFrame(
            [
                {"symbol": "A0", "cluster": "metal", "momentum_score": 0.1,
                 "realized_vol": 0.02, "valid_for_ranking": True, "selected_side": "long"},
                {"symbol": "B0", "cluster": "metal", "momentum_score": -0.1,
                 "realized_vol": 0.02, "valid_for_ranking": True, "selected_side": "short"},
            ]
        )
        out = attach_target_weights(df, cfg=cfg)
        long_w = float(out.loc[out["symbol"] == "A0", "target_weight"].iloc[0])
        short_w = float(out.loc[out["symbol"] == "B0", "target_weight"].iloc[0])
        self.assertGreater(long_w, 0.0)
        self.assertLess(short_w, 0.0)

    def test_vol_target_caps_high_vol_symbols(self) -> None:
        cfg = _wildcard_cfg(
            gross_exposure_target=0.50,
            use_vol_target_weighting=True,
            target_vol_pct_per_symbol=0.02,
            max_symbol_notional_pct=0.10,
        )
        df = pd.DataFrame(
            [
                {"symbol": "A0", "cluster": "metal", "momentum_score": 0.10,
                 "realized_vol": 0.04, "valid_for_ranking": True, "selected_side": "long"},
                {"symbol": "B0", "cluster": "metal", "momentum_score": 0.05,
                 "realized_vol": 0.01, "valid_for_ranking": True, "selected_side": "long"},
            ]
        )
        out = attach_target_weights(df, cfg=cfg)
        a_scale = float(out.loc[out["symbol"] == "A0", "vol_target_scale"].iloc[0])
        b_scale = float(out.loc[out["symbol"] == "B0", "vol_target_scale"].iloc[0])
        # 高 vol → scale 小；低 vol → scale 大
        self.assertLess(a_scale, b_scale)
        # 单品种 cap 生效
        for sym in ("A0", "B0"):
            w = float(out.loc[out["symbol"] == sym, "target_weight"].iloc[0])
            self.assertLessEqual(abs(w), 0.10 + 1e-9)

    def test_no_vol_target_when_disabled(self) -> None:
        cfg = _wildcard_cfg(
            gross_exposure_target=0.50, use_vol_target_weighting=False,
        )
        df = pd.DataFrame(
            [
                {"symbol": "A0", "cluster": "metal", "momentum_score": 0.10,
                 "realized_vol": 0.04, "valid_for_ranking": True, "selected_side": "long"},
                {"symbol": "B0", "cluster": "metal", "momentum_score": 0.05,
                 "realized_vol": 0.01, "valid_for_ranking": True, "selected_side": "long"},
            ]
        )
        out = attach_target_weights(df, cfg=cfg)
        a_scale = float(out.loc[out["symbol"] == "A0", "vol_target_scale"].iloc[0])
        b_scale = float(out.loc[out["symbol"] == "B0", "vol_target_scale"].iloc[0])
        self.assertEqual(a_scale, 1.0)
        self.assertEqual(b_scale, 1.0)

    def test_empty_selection_returns_zero_weights(self) -> None:
        cfg = _wildcard_cfg()
        df = pd.DataFrame(
            [
                {"symbol": "A0", "cluster": "metal", "momentum_score": 0.05,
                 "realized_vol": 0.02, "valid_for_ranking": True, "selected_side": ""},
            ]
        )
        out = attach_target_weights(df, cfg=cfg)
        self.assertEqual(float(out["target_weight"].iloc[0]), 0.0)


if __name__ == "__main__":
    unittest.main()
