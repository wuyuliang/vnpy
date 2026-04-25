"""transaction_cost.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.data_backtest.transaction_cost import apply_cost_to_pnl, estimate_cost


class TestTransactionCost(unittest.TestCase):
    def test_estimate_cost(self) -> None:
        c = estimate_cost(
            symbol="rb888.SHFE",
            price=3500.0,
            lots=2,
            side="long",
            multiplier=10.0,
            commission_rate=0.0001,
            tick_size=1.0,
            slippage_ticks=1.5,
            adv=1_000_000.0,
        )
        self.assertGreater(c.total, 0.0)
        self.assertGreaterEqual(c.impact, 0.0)

    def test_apply_cost_to_pnl(self) -> None:
        trades = pd.DataFrame(
            {
                "symbol": ["rb888.SHFE", "rb888.SHFE"],
                "price": [3500.0, 3510.0],
                "lots": [1, 1],
                "side": ["long", "short"],
                "gross_pnl": [100.0, -50.0],
                "multiplier": [10.0, 10.0],
                "commission_rate": [0.0001, 0.0001],
                "tick_size": [1.0, 1.0],
            }
        )
        out = apply_cost_to_pnl(trades, estimate_cost)
        self.assertIn("cost", out.columns)
        self.assertIn("net_pnl", out.columns)

    def test_apply_cost_both_legs(self) -> None:
        """一笔完整交易应扣两腿成本 (entry + exit)。"""
        trades = pd.DataFrame(
            {
                "symbol": ["rb888.SHFE"],
                "entry_price": [3500.0],
                "exit_price": [3520.0],
                "lots": [1],
                "side": ["long"],
                "gross_pnl": [200.0],  # (3520-3500)*1*10 = 200
                "multiplier": [10.0],
                "commission_rate": [0.0001],
                "tick_size": [1.0],
                "slippage_ticks": [1.5],
            }
        )
        out = apply_cost_to_pnl(trades, estimate_cost)
        # 单腿成本估算：commission + slippage
        # commission = 3500 * 1 * 10 * 0.0001 = 3.5
        # slippage  = 1 * 10 * 1.0 * 1.5 = 15
        # 单腿 ≈ 18.5；两腿 (另一腿 price=3520): commission=3.52, slippage=15 → 18.52
        # 总 cost ≈ 37
        cost = float(out["cost"].iloc[0])
        self.assertGreater(cost, 30.0,
                           f"双腿成本应 >30，got {cost:.2f}（只算单腿会 <20）")
        self.assertLess(cost, 50.0, f"双腿成本应合理 <50，got {cost:.2f}")

    def test_event_driven_charges_entry_and_exit(self) -> None:
        """Event-driven engine：平仓时的 cost 应覆盖 entry + exit 两腿。"""
        from cta.skills.data_backtest.event_driven_backtest import (
            EngineConfig,
            run_backtest,
        )

        class Strat:
            def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict]:
                base = {
                    "symbol": "rb888.SHFE",
                    "multiplier": 10.0,
                    "commission_rate": 0.0001,
                    "tick_size": 1.0,
                }
                if i == 0 and position == 0:
                    return [{**base, "side": "long", "lots": 1, "order_type": "market"}]
                if i == 3 and position > 0:
                    return [{**base, "side": "flat", "lots": 1, "order_type": "market"}]
                return []

        bars = pd.DataFrame(
            {
                "datetime": pd.date_range("2024-01-01", periods=8, freq="D"),
                "open": [3500.0] * 8,
                "high": [3510.0] * 8,
                "low": [3490.0] * 8,
                "close": [3500.0] * 8,
            }
        )
        cfg = EngineConfig(cost_fn=estimate_cost, slippage_ticks=1.5)
        out = run_backtest(bars, Strat(), cfg)
        tl = out["trade_log"]
        self.assertEqual(len(tl), 1)
        cost = float(tl["cost"].iloc[0])
        # commission(entry) + slippage(entry) + commission(exit) + slippage(exit)
        # ≈ 3.5 + 15 + 3.5 + 15 = 37
        self.assertGreater(cost, 30.0,
                           f"双腿成本应 >30，got {cost:.2f}")


if __name__ == "__main__":
    unittest.main()

