"""cta/strategy/demos.py 单测：CLI 5.2 引用的 ``make_double_ma`` 工厂。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.strategy.demos import DoubleMaStrategy, make_double_ma


def _bars(n: int = 60) -> pd.DataFrame:
    half = n // 2
    closes = [100 - i * 0.2 if i < half else 100 - (half - 1) * 0.2 + (i - half + 1) * 0.4 for i in range(n)]
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-02", periods=n, freq="D"),
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


class TestMakeDoubleMa(unittest.TestCase):
    def test_factory_returns_strategy(self) -> None:
        s = make_double_ma()
        self.assertIsInstance(s, DoubleMaStrategy)

    def test_signal_long_on_golden_cross(self) -> None:
        s = make_double_ma(fast=3, slow=10)
        bars = _bars(40)
        seen_long = False
        pos = 0
        for i in range(len(bars) - 1):
            orders = s.on_bar(i, bars.iloc[i], pos)
            for o in orders:
                if o["side"] == "long":
                    seen_long = True
                if o["side"] == "flat":
                    pos = 0
            if any(o["side"] == "long" for o in orders):
                pos = int(orders[0]["lots"])
        self.assertTrue(seen_long)


class TestDemoRunsViaCli(unittest.TestCase):
    """烟雾测：CLI 5.2 命令的 import 路径在文档中可解析。"""

    def test_import_path_resolves(self) -> None:
        import importlib
        mod = importlib.import_module("cta.strategy.demos")
        self.assertTrue(callable(getattr(mod, "make_double_ma")))


if __name__ == "__main__":
    unittest.main()
