"""Tests for vnpy CtaTemplate adapter (P0-1 验收)."""
from __future__ import annotations

import unittest
from dataclasses import dataclass

from cta.strategy.vnpy_adapters.baseline_strategy import (
    BaselineSetupVnpyStrategy,
    BaselineStrategyConfig,
)


@dataclass
class _FakeBar:
    """模拟 vnpy BarData 的最小字段集。"""
    high_price: float
    low_price: float
    close_price: float
    open_price: float = 0.0
    volume: float = 1000.0


def _make_strategy(**cfg_overrides) -> BaselineSetupVnpyStrategy:
    cfg = BaselineStrategyConfig(**cfg_overrides)
    return BaselineSetupVnpyStrategy(
        cta_engine=None,
        strategy_name="test_strategy",
        vt_symbol="RB2501.SHFE",
        setting={"baseline_cfg": cfg},
    )


class TestBaselineStrategyConfig(unittest.TestCase):
    def test_post_init_validates(self) -> None:
        with self.assertRaises(ValueError):
            BaselineStrategyConfig(donchian_window=1)
        with self.assertRaises(ValueError):
            BaselineStrategyConfig(atr_window=0)
        with self.assertRaises(ValueError):
            BaselineStrategyConfig(fixed_volume=0)
        with self.assertRaises(ValueError):
            BaselineStrategyConfig(stop_loss_pct=0)


class TestBaselineSetupVnpyStrategy(unittest.TestCase):
    def test_init_state(self) -> None:
        s = _make_strategy()
        self.assertEqual(s.pos, 0)
        self.assertEqual(s.signal_type, "donchian_breakout")
        self.assertEqual(s.donchian_window, 20)
        self.assertFalse(s.inited)
        s.on_init()
        self.assertTrue(s.inited)

    def test_disabled_strategy_emits_no_order(self) -> None:
        s = _make_strategy(enable=False, donchian_window=3)
        s.on_init()
        s.on_start()
        # 喂 5 根 bar：4 根低位 + 1 根突破
        for px in [100, 99, 100, 99, 110]:
            s.on_bar(_FakeBar(high_price=px, low_price=px - 1, close_price=px))
        self.assertEqual(s._sent_orders, [])

    def test_long_open_on_upper_break(self) -> None:
        s = _make_strategy(donchian_window=3, fixed_volume=2, stop_loss_pct=0.02)
        s.on_init()
        s.on_start()
        # 3 根低位 + 1 根突破 → 第 4 根触发 long_open
        bars = [
            _FakeBar(high_price=100, low_price=99, close_price=99.5),
            _FakeBar(high_price=101, low_price=100, close_price=100.5),
            _FakeBar(high_price=100, low_price=99, close_price=99.5),
            _FakeBar(high_price=105, low_price=100, close_price=104.0),
        ]
        for b in bars:
            s.on_bar(b)
        self.assertEqual(len(s._sent_orders), 1)
        self.assertEqual(s._sent_orders[0]["side"], "long_open")
        self.assertEqual(s._sent_orders[0]["volume"], 2.0)
        self.assertEqual(s.pos, 2)
        # 止损价 = entry * (1 - 0.02) = 104 * 0.98 = 101.92
        self.assertAlmostEqual(s._stop_price, 101.92, places=4)

    def test_short_open_on_lower_break(self) -> None:
        s = _make_strategy(donchian_window=3, stop_loss_pct=0.02)
        s.on_init()
        s.on_start()
        bars = [
            _FakeBar(high_price=101, low_price=100, close_price=100.5),
            _FakeBar(high_price=101, low_price=100, close_price=100.5),
            _FakeBar(high_price=101, low_price=100, close_price=100.5),
            _FakeBar(high_price=99, low_price=95, close_price=95.0),
        ]
        for b in bars:
            s.on_bar(b)
        self.assertEqual(len(s._sent_orders), 1)
        self.assertEqual(s._sent_orders[0]["side"], "short_open")
        self.assertEqual(s.pos, -1)
        # 止损价 = entry * 1.02 = 95 * 1.02 = 96.9
        self.assertAlmostEqual(s._stop_price, 96.9, places=4)

    def test_no_double_open_when_already_long(self) -> None:
        """已有持仓时不再开仓。"""
        s = _make_strategy(donchian_window=2)
        s.on_init()
        s.on_start()
        bars = [
            _FakeBar(high_price=100, low_price=99, close_price=99.5),
            _FakeBar(high_price=100, low_price=99, close_price=99.5),
            _FakeBar(high_price=105, low_price=100, close_price=104.0),  # 触发 long
            _FakeBar(high_price=110, low_price=105, close_price=108.0),  # 不再开
        ]
        for b in bars:
            s.on_bar(b)
        self.assertEqual(len(s._sent_orders), 1)

    def test_hard_stop_triggers_close(self) -> None:
        s = _make_strategy(donchian_window=2, stop_loss_pct=0.05)
        s.on_init()
        s.on_start()
        # 开 long
        s.on_bar(_FakeBar(high_price=100, low_price=99, close_price=99.5))
        s.on_bar(_FakeBar(high_price=100, low_price=99, close_price=99.5))
        s.on_bar(_FakeBar(high_price=105, low_price=100, close_price=104.0))
        # entry=104, stop=98.8
        self.assertEqual(s.pos, 1)
        # 下跌触发止损
        s.on_bar(_FakeBar(high_price=99, low_price=97, close_price=98.0))
        # 应该已平掉
        self.assertEqual(s.pos, 0)
        self.assertIsNone(s._stop_price)
        # 总下单 2 个：open + close
        self.assertEqual(len(s._sent_orders), 2)
        self.assertEqual(s._sent_orders[1]["side"], "long_close")

    def test_extra_filter_blocks_order(self) -> None:
        """通过 setting 注入的 extra_filter 可拦截开仓。"""
        cfg = BaselineStrategyConfig(donchian_window=2)
        blocked: list[dict] = []
        def reject_all(decision: dict) -> bool:
            blocked.append(decision)
            return False
        s = BaselineSetupVnpyStrategy(
            cta_engine=None, strategy_name="t", vt_symbol="RB2501.SHFE",
            setting={"baseline_cfg": cfg, "extra_filter": reject_all},
        )
        s.on_init()
        s.on_start()
        bars = [
            _FakeBar(high_price=100, low_price=99, close_price=99.5),
            _FakeBar(high_price=100, low_price=99, close_price=99.5),
            _FakeBar(high_price=105, low_price=100, close_price=104.0),
        ]
        for b in bars:
            s.on_bar(b)
        # extra_filter 全拒绝 → 无下单
        self.assertEqual(s._sent_orders, [])
        self.assertEqual(s.pos, 0)
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["side"], "long_open")

    def test_buffer_size_capped(self) -> None:
        """bar buffer 长度上限。"""
        s = _make_strategy(donchian_window=20, max_buffer_bars=50)
        s.on_init()
        s.on_start()
        for i in range(200):
            s.on_bar(_FakeBar(high_price=100 + i, low_price=99 + i, close_price=99.5 + i))
        self.assertEqual(len(s._bar_buffer), 50)

    def test_setting_must_carry_cfg(self) -> None:
        """缺 baseline_cfg 时仍然能构造（用默认 cfg）。"""
        s = BaselineSetupVnpyStrategy(
            cta_engine=None, strategy_name="t", vt_symbol="RB2501.SHFE",
            setting={},
        )
        self.assertIsInstance(s.cfg, BaselineStrategyConfig)
        self.assertEqual(s.cfg.signal_type, "donchian_breakout")

    def test_invalid_cfg_type_raises(self) -> None:
        with self.assertRaises(TypeError):
            BaselineSetupVnpyStrategy(
                cta_engine=None, strategy_name="t", vt_symbol="RB2501.SHFE",
                setting={"baseline_cfg": "not_a_cfg_object"},
            )


class TestP1CSendOrderFailureNoPosDrift(unittest.TestCase):
    """codex P1-C 回归：vnpy 下单异常时 self.pos 不应被更新。"""

    def test_gateway_exception_keeps_pos_zero(self) -> None:
        class _FailingEngine:
            def write_log(self, msg, strat): pass
            def send_order(self, *a, **kw):
                raise RuntimeError("gateway down")

        cfg = BaselineStrategyConfig(donchian_window=3, fixed_volume=2, stop_loss_pct=0.02)
        s = BaselineSetupVnpyStrategy(
            cta_engine=_FailingEngine(), strategy_name="t", vt_symbol="RB2501.SHFE",
            setting={"baseline_cfg": cfg},
        )
        s.on_init()
        s.on_start()
        # 喂触发 long_open 的 bar 序列
        bars = [
            _FakeBar(high_price=100, low_price=99, close_price=99.5),
            _FakeBar(high_price=101, low_price=100, close_price=100.5),
            _FakeBar(high_price=100, low_price=99, close_price=99.5),
            _FakeBar(high_price=105, low_price=100, close_price=104.0),
        ]
        for b in bars:
            s.on_bar(b)
        # 真实 gateway 抛异常 → pos 必须保持 0
        self.assertEqual(s.pos, 0)
        # 但 audit trail 仍然记录了发单尝试（用于复盘）
        self.assertEqual(len(s._sent_orders), 1)
        self.assertEqual(s._sent_orders[0]["side"], "long_open")
        # stop_price 也不应被设
        self.assertIsNone(s._stop_price)


if __name__ == "__main__":
    unittest.main()
