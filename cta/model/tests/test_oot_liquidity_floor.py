from __future__ import annotations

import unittest

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation_inputs import apply_oot_liquidity_floor_guard


class TestOotLiquidityFloor(unittest.TestCase):
    def test_liquidity_floor_blocks_opening_rows_with_bad_volume_ratio(self) -> None:
        cfg = OotEvaluationConfig(use_liquidity_floor_guard=True)
        df = pd.DataFrame(
            {
                "symbol": ["EC0", "RB0", "CU0"],
                "side": ["long", "long", "short"],
                "offset": ["open", "open", "close"],
                "volume_ratio": [0.10, 0.80, 0.01],
                "bid_ask_spread_ticks": [1.0, 1.0, 9.0],
                "turnover_ratio": [0.20, 0.20, 0.001],
            }
        )

        out = apply_oot_liquidity_floor_guard(df, cfg)

        self.assertEqual(str(out.loc[0, "liquidity_block_reason"]).split(":", 1)[0], "liquidity_floor")
        self.assertTrue(bool(out.loc[0, "liquidity_blocked"]))
        self.assertFalse(bool(out.loc[1, "liquidity_blocked"]))
        self.assertFalse(bool(out.loc[2, "liquidity_blocked"]), "close orders must fail-open")

    def test_liquidity_floor_fail_opens_when_indicators_are_missing(self) -> None:
        cfg = OotEvaluationConfig(use_liquidity_floor_guard=True)
        df = pd.DataFrame({"symbol": ["EC0"], "side": ["long"]})

        out = apply_oot_liquidity_floor_guard(df, cfg)

        self.assertFalse(bool(out.loc[0, "liquidity_blocked"]))
        self.assertEqual(str(out.loc[0, "liquidity_block_reason"]), "")


if __name__ == "__main__":
    unittest.main()
