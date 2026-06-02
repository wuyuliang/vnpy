from __future__ import annotations

import unittest

import pandas as pd

from cta.config.cost_manifest import impact_cost_pct
from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation_inputs import resolve_per_row_cost_pct


class TestImpactCost(unittest.TestCase):
    def test_impact_cost_increases_with_participation(self) -> None:
        small = impact_cost_pct(order_lots=10, adv_lots=10_000, k=0.10)
        large = impact_cost_pct(order_lots=1_000, adv_lots=10_000, k=0.10)

        self.assertGreater(large, small)
        self.assertAlmostEqual(impact_cost_pct(order_lots=10, adv_lots=0, k=0.10), 0.0)

    def test_resolver_uses_symbol_commission_override_and_impact_cost(self) -> None:
        cfg = OotEvaluationConfig(
            commission_pct_by_cluster_interval={},
            slippage_pct_by_cluster_interval={},
            commission_pct_by_symbol={"EC0": 0.0010},
            use_impact_cost=True,
            impact_cost_k=0.10,
        )
        df = pd.DataFrame(
            {
                "symbol": ["EC0", "RB0"],
                "interval": ["day", "day"],
                "position_qty": [100.0, 100.0],
                "adv_lots": [10_000.0, 10_000.0],
            }
        )

        arr = resolve_per_row_cost_pct(df, cfg)

        base_global = float(cfg.commission_pct_per_trade) + float(cfg.slippage_pct_per_trade)
        self.assertGreater(arr[0], base_global)
        self.assertAlmostEqual(arr[0], 0.0010 + float(cfg.slippage_pct_per_trade) + 0.10 * (0.01 ** 0.5))
        self.assertAlmostEqual(arr[1], base_global + 0.10 * (0.01 ** 0.5))


if __name__ == "__main__":
    unittest.main()
