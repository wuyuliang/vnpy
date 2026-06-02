"""Unit tests for opportunity scoring and allocation."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.config import CapsConfig, OpportunityRankerConfig
from cta.portfolio_logic.opportunity_ranker import OpportunityRanker
from cta.portfolio_logic.portfolio_state import PortfolioState


class TestOpportunityRanker(unittest.TestCase):
    def test_score_prefers_higher_prob_and_alignment(self) -> None:
        ranker = OpportunityRanker(
            OpportunityRankerConfig(w_prob=0.4, w_edge=0.3, w_rank=0.2, w_align=0.1),
            edge_stats={("black", "60min"): (0.0, 1.0)},
        )
        df = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "60min"],
                "direction": ["long", "long"],
                "cluster_name": ["black", "black"],
                "trade_filter_prob_pctl": [80.0, 60.0],
                "pred_mfe_atr": [2.0, 1.2],
                "pred_mae_atr": [0.5, 0.6],
                "htf_alignment": ["aligned", "neutral"],
            }
        )
        out = ranker.score(df, htf_state={})
        self.assertIn("score", out.columns)
        self.assertGreater(float(out.iloc[0]["score"]), float(out.iloc[1]["score"]))

    def test_score_normalizes_minute_alias_for_interval_rank(self) -> None:
        ranker = OpportunityRanker(
            OpportunityRankerConfig(w_prob=0.0, w_edge=0.0, w_rank=1.0, w_align=0.0),
        )
        df = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["minute60"],
                "direction": ["long"],
                "cluster_name": ["black"],
                "trade_filter_prob_pctl": [50.0],
                "pred_mfe_atr": [0.0],
                "pred_mae_atr": [0.0],
                "htf_alignment": ["neutral"],
            }
        )
        out = ranker.score(df, htf_state={})
        components = out.iloc[0]["score_components"]
        self.assertAlmostEqual(float(components["rank_weight"]), 0.85, places=6)
        self.assertAlmostEqual(float(out.iloc[0]["score"]), 0.85, places=6)

    def test_score_uses_injected_interval_rank_weights(self) -> None:
        ranker = OpportunityRanker(
            OpportunityRankerConfig(w_prob=0.0, w_edge=0.0, w_rank=1.0, w_align=0.0),
            interval_rank={"60min": 0.33, "day": 0.99},
        )
        df = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["60min"],
                "direction": ["long"],
                "cluster_name": ["black"],
                "trade_filter_prob_pctl": [50.0],
                "pred_mfe_atr": [0.0],
                "pred_mae_atr": [0.0],
                "htf_alignment": ["neutral"],
            }
        )
        out = ranker.score(df, htf_state={})
        self.assertAlmostEqual(float(out.iloc[0]["score_components"]["rank_weight"]), 0.33, places=6)
        self.assertAlmostEqual(float(out.iloc[0]["score"]), 0.33, places=6)

    def test_score_never_falls_back_to_future_edge_columns(self) -> None:
        ranker = OpportunityRanker(
            OpportunityRankerConfig(w_prob=0.0, w_edge=1.0, w_rank=0.0, w_align=0.0),
            edge_stats={("black", "60min"): (0.0, 1.0)},
        )
        df = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["60min"],
                "direction": ["long"],
                "cluster_name": ["black"],
                "trade_filter_prob_pctl": [50.0],
                "pred_mfe_atr": [float("nan")],
                "pred_mae_atr": [float("nan")],
                "future_mfe_atr": [9.0],
                "future_mae_atr": [0.0],
                "htf_alignment": ["neutral"],
            }
        )
        base = ranker.score(df.drop(columns=["future_mfe_atr", "future_mae_atr"]), htf_state={})
        out = ranker.score(df, htf_state={})
        self.assertAlmostEqual(float(out.iloc[0]["score"]), float(base.iloc[0]["score"]), places=6)

    def test_allocate_respects_cluster_and_total_caps(self) -> None:
        ranker = OpportunityRanker(OpportunityRankerConfig())
        state = PortfolioState(equity=1_000_000.0)
        caps = CapsConfig(
            max_total_positions=2,
            max_per_symbol=1,
            max_total_per_cluster=1,
            max_symbol_notional_pct=0.30,
            max_cluster_notional_pct=0.50,
            max_total_notional_pct=1.50,
        )
        scored = pd.DataFrame(
            {
                "symbol": ["RB0", "HC0", "CU0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["60min", "60min", "60min"],
                "direction": ["long", "long", "long"],
                "cluster_name": ["black", "black", "metal"],
                "score": [0.95, 0.90, 0.85],
                "base_notional": [100_000.0, 100_000.0, 100_000.0],
            }
        )
        picks = ranker.allocate(scored, state=state, caps=caps, score_threshold=0.01)

        self.assertEqual(len(picks), 2)
        self.assertEqual(set(picks["symbol"].tolist()), {"RB0", "CU0"})
        self.assertTrue((picks["allocated_notional"] > 0).all())

    def test_allocate_supports_min_prob_percentile_gate(self) -> None:
        ranker = OpportunityRanker(OpportunityRankerConfig())
        state = PortfolioState(equity=1_000_000.0)
        caps = CapsConfig(max_total_positions=5, max_total_per_cluster=5, max_per_symbol=1)
        scored = pd.DataFrame(
            {
                "symbol": ["RB0", "CU0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "60min"],
                "direction": ["long", "long"],
                "cluster_name": ["black", "metal"],
                "score": [0.80, 0.75],
                "trade_filter_prob_pctl": [82.0, 65.0],
                "base_notional": [100_000.0, 100_000.0],
            }
        )
        picks = ranker.allocate(
            scored,
            state=state,
            caps=caps,
            score_threshold=0.01,
            min_prob_pctl=70.0,
        )
        self.assertEqual(len(picks), 1)
        self.assertEqual(str(picks.iloc[0]["symbol"]), "RB0")

    def test_allocate_empty_returns_allocated_notional_column(self) -> None:
        ranker = OpportunityRanker(OpportunityRankerConfig())
        state = PortfolioState(equity=1_000_000.0)
        caps = CapsConfig()
        out = ranker.allocate(
            pd.DataFrame(columns=["symbol", "exchange", "score"]),
            state=state,
            caps=caps,
            score_threshold=0.01,
        )
        self.assertIn("allocated_notional", out.columns)

    def test_allocate_rejects_non_positive_score_threshold(self) -> None:
        ranker = OpportunityRanker(OpportunityRankerConfig())
        state = PortfolioState(equity=1_000_000.0)
        caps = CapsConfig()
        scored = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["60min"],
                "direction": ["long"],
                "cluster_name": ["black"],
                "score": [0.9],
                "trade_filter_prob_pctl": [80.0],
                "base_notional": [100_000.0],
            }
        )
        with self.assertRaises(ValueError):
            ranker.allocate(scored, state=state, caps=caps, score_threshold=0.0)

    def test_allocate_prefers_signal_type_bonus_under_same_score(self) -> None:
        ranker = OpportunityRanker(
            OpportunityRankerConfig(
                signal_type_rank_bonus={
                    "bull_pullback_continuation": 0.02,
                    "atr_breakout": 0.0,
                }
            )
        )
        state = PortfolioState(equity=1_000_000.0)
        caps = CapsConfig(max_total_positions=1, max_total_per_cluster=1, max_per_symbol=1)
        scored = pd.DataFrame(
            {
                "symbol": ["RB0", "HC0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "60min"],
                "direction": ["long", "long"],
                "cluster_name": ["black", "black"],
                "score": [0.90, 0.90],
                "signal_type": ["bull_pullback_continuation", "atr_breakout"],
                "base_notional": [100_000.0, 100_000.0],
            }
        )
        picks = ranker.allocate(scored, state=state, caps=caps, score_threshold=0.01)
        self.assertEqual(len(picks), 1)
        self.assertEqual(str(picks.iloc[0]["signal_type"]), "bull_pullback_continuation")


if __name__ == "__main__":
    unittest.main()
