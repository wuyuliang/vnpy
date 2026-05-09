"""验证 CtaTemplate 子类的 _build_contract 优先从 cta_engine 读 pricetick/size。"""
from __future__ import annotations

import unittest

from cta.strategy.cta_baseline import DonchianCta
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta
from cta.strategy.tests.test_cta_adapter import FakeCtaEngine


class TestAutoContractFromEngine(unittest.TestCase):
    def test_baseline_prefers_engine_pricetick_and_size(self) -> None:
        eng = FakeCtaEngine(pricetick=2.5, size=20)
        a = DonchianCta(eng, "test", "RB888.SHFE", {})
        contract = a._build_contract()
        # cta_engine 提供的真实合约元数据应被采用
        self.assertEqual(contract.tick_size, 2.5)
        self.assertEqual(contract.multiplier, 20.0)

    def test_tight_range_prefers_engine_pricetick_and_size(self) -> None:
        eng = FakeCtaEngine(pricetick=0.5, size=5)
        a = SkillTightRangeBreakoutCta(eng, "test", "CU888.SHFE", {})
        contract = a._build_contract()
        self.assertEqual(contract.tick_size, 0.5)
        self.assertEqual(contract.multiplier, 5.0)

    def test_user_override_wins_when_engine_returns_zero(self) -> None:
        """cta_engine.get_pricetick 返回 <=0 时回退到 setting 字段值。"""
        eng = FakeCtaEngine(pricetick=0, size=0)
        a = DonchianCta(eng, "test", "RB888.SHFE",
                        {"tick_size": 4.0, "multiplier": 25.0})
        contract = a._build_contract()
        self.assertEqual(contract.tick_size, 4.0)
        self.assertEqual(contract.multiplier, 25.0)

    def test_engine_exception_falls_back(self) -> None:
        class _Throwing:
            gateway_name = "X"
            def get_pricetick(self, s): raise RuntimeError("no contract")
            def get_size(self, s): raise RuntimeError("no contract")
            def write_log(self, m, s=None): pass
            def send_order(self, *a, **kw): return []
            def cancel_order(self, *a, **kw): pass
            def cancel_all(self, *a, **kw): pass
            def get_engine_type(self):
                from vnpy_ctastrategy.base import EngineType
                return EngineType.BACKTESTING

        a = DonchianCta(_Throwing(), "t", "RB888.SHFE",
                        {"tick_size": 1.0, "multiplier": 10.0})
        contract = a._build_contract()
        self.assertEqual(contract.tick_size, 1.0)
        self.assertEqual(contract.multiplier, 10.0)


if __name__ == "__main__":
    unittest.main()
