"""event_driven_backtest.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.data_backtest.event_driven_backtest import (
    EngineConfig,
    run_backtest,
    simulate_fill,
)


class _DummyStrategy:
    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict]:
        if i == 0 and position == 0:
            return [{"side": "long", "lots": 1, "order_type": "market"}]
        if i == 3 and position > 0:
            return [{"side": "flat", "lots": 1, "order_type": "market"}]
        return []


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=8, freq="D"),
            "open": [100, 101, 102, 103, 104, 105, 106, 107],
            "high": [101, 102, 103, 104, 105, 106, 107, 108],
            "low": [99, 100, 101, 102, 103, 104, 105, 106],
            "close": [100.5, 101.5, 102.2, 103.1, 104.4, 105.2, 106.3, 107.4],
        }
    )


class TestEventDrivenBacktest(unittest.TestCase):
    def test_simulate_fill(self) -> None:
        bars = _bars()
        order = {"side": "long", "order_type": "market", "lots": 1}
        fill = simulate_fill(order, bars.iloc[0], bars.iloc[1], EngineConfig())
        self.assertIsNotNone(fill)
        self.assertEqual(fill["price"], bars.iloc[1]["open"])

    def test_run_backtest(self) -> None:
        bars = _bars()
        out = run_backtest(bars, _DummyStrategy(), EngineConfig())
        self.assertIn("trade_log", out)
        self.assertIn("equity_curve", out)
        self.assertGreaterEqual(len(out["trade_log"]), 1)


def _one_sided_up_bar(prev_close: float, jump_pct: float = 0.08) -> pd.Series:
    px = prev_close * (1.0 + jump_pct)
    return pd.Series({"open": px, "high": px, "low": px, "close": px, "volume": 0.0})


def _one_sided_down_bar(prev_close: float, jump_pct: float = 0.08) -> pd.Series:
    px = prev_close * (1.0 - jump_pct)
    return pd.Series({"open": px, "high": px, "low": px, "close": px, "volume": 0.0})


class TestPriceLimitFilter(unittest.TestCase):
    """涨跌停过滤：一字板时禁止该方向开仓，平仓不受限。"""

    def test_long_open_blocked_on_limit_up(self) -> None:
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})
        nxt = _one_sided_up_bar(prev_close=100.0, jump_pct=0.08)
        cfg = EngineConfig(limit_move_pct=0.07)
        fill = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 1}, cur, nxt, cfg
        )
        self.assertIsNone(fill)

    def test_short_open_blocked_on_limit_down(self) -> None:
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})
        nxt = _one_sided_down_bar(prev_close=100.0, jump_pct=0.08)
        cfg = EngineConfig(limit_move_pct=0.07)
        fill = simulate_fill(
            {"side": "short", "order_type": "market", "lots": 1}, cur, nxt, cfg
        )
        self.assertIsNone(fill)

    def test_flat_close_not_blocked(self) -> None:
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})
        nxt = _one_sided_up_bar(prev_close=100.0, jump_pct=0.08)
        cfg = EngineConfig(limit_move_pct=0.07)
        fill = simulate_fill(
            {"side": "flat", "order_type": "market", "lots": 1}, cur, nxt, cfg
        )
        self.assertIsNotNone(fill)

    def test_default_config_no_filter(self) -> None:
        """limit_move_pct 默认 None：一字板涨停时仍可成交（向后兼容）。"""
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})
        nxt = _one_sided_up_bar(prev_close=100.0, jump_pct=0.08)
        fill = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 1}, cur, nxt, EngineConfig()
        )
        self.assertIsNotNone(fill)

    def test_below_threshold_passes(self) -> None:
        """跳空但未触及涨跌停阈值时不过滤（涨幅 5% < 7%）。"""
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})
        nxt = _one_sided_up_bar(prev_close=100.0, jump_pct=0.05)
        cfg = EngineConfig(limit_move_pct=0.07)
        fill = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 1}, cur, nxt, cfg
        )
        self.assertIsNotNone(fill)


class TestLiquidityCap(unittest.TestCase):
    """流动性约束：成交手数被截断到 volume * ratio。"""

    def test_lots_truncated_to_cap(self) -> None:
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0})
        nxt = pd.Series({"open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 100.0})
        cfg = EngineConfig(liquidity_ratio=0.1)  # cap = 10
        fill = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 50}, cur, nxt, cfg
        )
        self.assertIsNotNone(fill)
        self.assertEqual(fill["lots"], 10)

    def test_zero_volume_rejected(self) -> None:
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0})
        nxt = pd.Series({"open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 0.0})
        cfg = EngineConfig(liquidity_ratio=0.1)
        fill = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 1}, cur, nxt, cfg
        )
        self.assertIsNone(fill)

    def test_default_no_truncation(self) -> None:
        """liquidity_ratio 默认 None：不截断（向后兼容）。"""
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0})
        nxt = pd.Series({"open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 1.0})
        fill = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 999}, cur, nxt, EngineConfig()
        )
        self.assertIsNotNone(fill)
        self.assertEqual(fill["lots"], 999)

    def test_engine_config_default_cost_fn_is_realistic_cost_model(self) -> None:
        from cta.skills.data_backtest.transaction_cost import estimate_cost
        cfg = EngineConfig()
        self.assertIs(cfg.cost_fn, estimate_cost)

    def test_below_cap_no_truncation(self) -> None:
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0})
        nxt = pd.Series({"open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 100.0})
        cfg = EngineConfig(liquidity_ratio=0.1)  # cap = 10
        fill = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 5}, cur, nxt, cfg
        )
        self.assertIsNotNone(fill)
        self.assertEqual(fill["lots"], 5)

    def test_liquidity_cap_aggregates_within_same_symbol_bar(self) -> None:
        cur = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0})
        nxt = pd.Series({"open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 100.0})
        cfg = EngineConfig(liquidity_ratio=0.1)  # cap = 10
        state = {}
        f1 = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 8},
            cur,
            nxt,
            cfg,
            liquidity_state=state,
            liquidity_key=("RB0", "2024-01-01T09:00:00"),
        )
        f2 = simulate_fill(
            {"side": "long", "order_type": "market", "lots": 8},
            cur,
            nxt,
            cfg,
            liquidity_state=state,
            liquidity_key=("RB0", "2024-01-01T09:00:00"),
        )
        self.assertIsNotNone(f1)
        self.assertEqual(int(f1["lots"]), 8)
        self.assertIsNotNone(f2)
        self.assertEqual(int(f2["lots"]), 2)


if __name__ == "__main__":
    unittest.main()
