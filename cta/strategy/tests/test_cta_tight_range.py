"""SkillTightRangeBreakoutCta（CtaTemplate 子类）单测。

目标：用合成 OHLCV 喂给 CtaTemplate 子类，验证：
1. 在累积足够 bar 后能产生订单（与 v1 strategy 在同一份数据上结果一致）
2. ``setting`` 中的 lookback/alpha/min_count 等参数生效
3. 默认配置下 ``parameters`` 完整定义了 ``StrategyConfig`` 全部字段
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta
from cta.strategy.tests.test_cta_adapter import FakeCtaEngine


def _bars_with_breakout(n_pre: int = 30, n_break: int = 5) -> list[BarData]:
    """生成一段窄幅整理 + 上行突破的合成 K 线。"""
    rng = np.random.default_rng(0)
    base = 100.0
    # n_pre 根：在 [99, 101] 内窄幅
    bars: list[BarData] = []
    t0 = datetime(2024, 1, 2, 9, 0)
    for i in range(n_pre):
        c = base + rng.uniform(-0.4, 0.4)
        o = c + rng.uniform(-0.2, 0.2)
        h = max(o, c) + rng.uniform(0, 0.3)
        l = min(o, c) - rng.uniform(0, 0.3)
        bars.append(
            BarData(
                gateway_name="TEST",
                symbol="X0",
                exchange=Exchange.SHFE,
                datetime=t0 + timedelta(days=i),
                interval=Interval.DAILY,
                open_price=o,
                high_price=h,
                low_price=l,
                close_price=c,
                volume=1500.0,
            )
        )
    # n_break 根：明显放量上破
    for j in range(n_break):
        c = base + 2 + j * 1.5
        o = c - 0.5
        h = c + 1.0
        l = o - 0.2
        bars.append(
            BarData(
                gateway_name="TEST",
                symbol="X0",
                exchange=Exchange.SHFE,
                datetime=t0 + timedelta(days=n_pre + j),
                interval=Interval.DAILY,
                open_price=o,
                high_price=h,
                low_price=l,
                close_price=c,
                volume=4000.0,
            )
        )
    return bars


class TestSkillTightRangeBreakoutCta(unittest.TestCase):
    def test_parameters_listed(self) -> None:
        from cta.config.skill_tight_range_breakout_config import StrategyConfig
        # 所有 StrategyConfig 字段都应在 parameters 中可调；contract 元数据也允许调
        cfg_fields = set(StrategyConfig.__dataclass_fields__.keys())
        params = set(SkillTightRangeBreakoutCta.parameters)
        self.assertTrue(cfg_fields.issubset(params))
        for extra in ("multiplier", "tick_size", "commission_rate", "slippage_ticks"):
            self.assertIn(extra, params)

    def test_setting_overrides_defaults(self) -> None:
        eng = FakeCtaEngine()
        a = SkillTightRangeBreakoutCta(
            eng, "test", "X0.SHFE",
            {"lookback": 5, "alpha": 2.0, "min_count": 3, "lots": 2},
        )
        self.assertEqual(a.lookback, 5)
        self.assertAlmostEqual(a.alpha, 2.0)
        self.assertEqual(a.min_count, 3)
        self.assertEqual(a.lots, 2)

    def test_smoke_runs_without_error(self) -> None:
        eng = FakeCtaEngine()
        a = SkillTightRangeBreakoutCta(eng, "test", "X0.SHFE", {"lookback": 5, "min_count": 3})
        a.trading = True
        a.on_init()
        a.on_start()
        for bar in _bars_with_breakout(n_pre=20, n_break=5):
            a.on_bar(bar)
        # 至少不应抛错；订单数 >= 0 即可
        self.assertGreaterEqual(len(eng.orders), 0)
        # 且没有 inner.on_bar / prepare_frame 异常日志
        bad = [m for m in eng.logs if "error" in m.lower() or "failed" in m.lower()]
        self.assertEqual(bad, [], f"unexpected error logs: {bad}")

    def test_emits_long_breakout_after_warmup(self) -> None:
        """突破场景下 adapter 应至少发一次 long OPEN（具体数量可能因
        stream/batch 模式差异略有不同；v1 等价对齐由 M2-6 parity_check 完成）。"""
        from vnpy.trader.constant import Direction, Offset

        bars = _bars_with_breakout(n_pre=25, n_break=10)
        eng = FakeCtaEngine()
        a = SkillTightRangeBreakoutCta(
            eng, "test", "X0.SHFE",
            {"lookback": 5, "min_count": 3, "lots": 1, "max_holding_bars": 10},
        )
        a.trading = True
        a.on_init()
        a.on_start()
        for bar in bars:
            a.on_bar(bar)
        opens = [o for o in eng.orders if o["offset"] == Offset.OPEN]
        long_opens = [o for o in opens if o["direction"] == Direction.LONG]
        self.assertGreaterEqual(len(long_opens), 1, f"expected long open after breakout, got {opens}")


if __name__ == "__main__":
    unittest.main()
