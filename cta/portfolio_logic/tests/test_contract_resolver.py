"""Tests for ContractResolver (P0-5 验收)."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.contract_resolver import (
    ActiveContractInfo,
    ContractResolver,
    build_vnpy_contract_query_fn,
    load_default_resolver,
)


def _sample_calendar() -> pd.DataFrame:
    """构造一段 RB 主力日历：2024-09~12 → RB2412；2024-12-16 切到 RB2501。"""
    rows = []
    for d in pd.date_range("2024-09-02", "2024-12-13", freq="B"):
        rows.append({"trade_date": d, "main_contract_code": "RB2412.SHFE"})
    for d in pd.date_range("2024-12-16", "2025-04-15", freq="B"):
        rows.append({"trade_date": d, "main_contract_code": "RB2505.SHFE"})
    # 加一条 AU 让多品种共存
    for d in pd.date_range("2024-09-02", "2024-12-13", freq="B"):
        rows.append({"trade_date": d, "main_contract_code": "AU2412.SHFE"})
    return pd.DataFrame(rows)


class TestContractResolver(unittest.TestCase):
    def test_resolve_active_contract_basic(self) -> None:
        r = ContractResolver(calendar_df=_sample_calendar())
        info = r.resolve_active_contract("RB0", "2024-11-20")
        self.assertEqual(info.active_contract, "RB2412.SHFE")
        self.assertEqual(info.exchange, "SHFE")
        self.assertEqual(info.expiry_yyyymm, 202412)
        self.assertEqual(info.continuous_symbol, "RB0")

    def test_resolve_after_rollover_returns_new_main(self) -> None:
        r = ContractResolver(calendar_df=_sample_calendar())
        info = r.resolve_active_contract("RB0", "2025-01-10")
        self.assertEqual(info.active_contract, "RB2505.SHFE")
        self.assertEqual(info.expiry_yyyymm, 202505)

    def test_resolve_multi_symbol(self) -> None:
        r = ContractResolver(calendar_df=_sample_calendar())
        rb_info = r.resolve_active_contract("RB0", "2024-10-15")
        au_info = r.resolve_active_contract("AU0", "2024-10-15")
        self.assertEqual(rb_info.active_contract, "RB2412.SHFE")
        self.assertEqual(au_info.active_contract, "AU2412.SHFE")

    def test_resolve_raises_when_no_match(self) -> None:
        r = ContractResolver(calendar_df=_sample_calendar())
        # universe 里没 CU
        with self.assertRaises(LookupError):
            r.resolve_active_contract("CU0", "2024-11-20")

    def test_resolve_raises_when_empty_continuous(self) -> None:
        r = ContractResolver(calendar_df=_sample_calendar())
        with self.assertRaises(Exception):
            r.resolve_active_contract("", "2024-11-20")

    def test_is_rollover_day_inside_window(self) -> None:
        r = ContractResolver(calendar_df=_sample_calendar())
        # 2024-12-16 是切换日；window=3 时 12-13 ~ 12-19 应都返 True
        self.assertTrue(r.is_rollover_day("RB0", "2024-12-13", window_days=3))
        self.assertTrue(r.is_rollover_day("RB0", "2024-12-16", window_days=3))
        self.assertTrue(r.is_rollover_day("RB0", "2024-12-19", window_days=3))

    def test_is_rollover_day_outside_window(self) -> None:
        r = ContractResolver(calendar_df=_sample_calendar())
        # 11-01 远离 12-16 切换日
        self.assertFalse(r.is_rollover_day("RB0", "2024-11-01", window_days=3))
        self.assertFalse(r.is_rollover_day("RB0", "2025-02-01", window_days=3))

    def test_is_rollover_day_window_zero(self) -> None:
        r = ContractResolver(calendar_df=_sample_calendar())
        # window_days=0 时只有切换当天本身返 True
        self.assertTrue(r.is_rollover_day("RB0", "2024-12-16", window_days=0))
        self.assertFalse(r.is_rollover_day("RB0", "2024-12-13", window_days=0))

    def test_vnpy_fallback_when_no_calendar(self) -> None:
        """无日历时 vnpy_query_fn 提供候选合约。"""
        candidates_by_prefix = {
            "RB": ["RB2412.SHFE", "RB2505.SHFE", "RB2510.SHFE"],
        }
        def fake_vnpy_query(prefix: str) -> list[str]:
            return candidates_by_prefix.get(prefix, [])

        r = ContractResolver(calendar_df=None, vnpy_query_fn=fake_vnpy_query)
        # 2024-11-20 时 RB2412 还在；取第一个 >= 当前 yyyymm 的
        info = r.resolve_active_contract("RB0", "2024-11-20")
        self.assertEqual(info.active_contract, "RB2412.SHFE")
        # 2024-12-25 时 RB2412 到期了，应取 RB2505
        info2 = r.resolve_active_contract("RB0", "2024-12-25")
        self.assertEqual(info2.active_contract, "RB2505.SHFE")

    def test_vnpy_fallback_fails_when_empty(self) -> None:
        r = ContractResolver(
            calendar_df=None,
            vnpy_query_fn=lambda _prefix: [],
        )
        with self.assertRaises(LookupError):
            r.resolve_active_contract("RB0", "2024-11-20")

    def test_load_default_resolver_handles_missing_path(self) -> None:
        """日历路径不存在时不 raise，构造空 resolver（fall back to vnpy）。"""
        r = load_default_resolver("nonexistent/path/calendar.csv")
        # 没注入 vnpy → 必 LookupError
        with self.assertRaises(LookupError):
            r.resolve_active_contract("RB0", "2024-11-20")

    def test_load_default_resolver_explicit_path(self) -> None:
        """显式路径存在时从该 path 加载日历。"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            df = _sample_calendar()
            df.to_csv(f.name, index=False)
            tmp_path = f.name
        try:
            r = load_default_resolver(tmp_path)
            info = r.resolve_active_contract("RB0", "2024-11-20")
            self.assertEqual(info.active_contract, "RB2412.SHFE")
        finally:
            import os
            os.unlink(tmp_path)

    def test_from_fut_mapping_builds_resolver(self) -> None:
        """fut_mapping DataFrame 直接桥接到 resolver。"""
        from cta.portfolio_logic.contract_resolver import from_fut_mapping
        # 模拟 fut_mapping 输出：trade_date + mapping_ts_code
        mapping_df = pd.DataFrame([
            {"trade_date": d, "mapping_ts_code": "RB2412.SHF"}
            for d in pd.date_range("2024-09-02", "2024-12-13", freq="B")
        ])
        r = from_fut_mapping(mapping_df, months_ahead=1)
        info = r.resolve_active_contract("RB0", "2024-11-20")
        self.assertEqual(info.active_contract, "RB2412.SHF")

    def test_build_vnpy_contract_query_fn_filters_prefix(self) -> None:
        class _Exchange:
            def __init__(self, value: str) -> None:
                self.value = value

        class _Contract:
            def __init__(self, symbol: str, exchange: str) -> None:
                self.symbol = symbol
                self.exchange = _Exchange(exchange)

        class _MainEngine:
            def get_all_contracts(self):
                return [
                    _Contract("RB2412", "SHFE"),
                    _Contract("RB2501", "SHFE"),
                    _Contract("AU2412", "SHFE"),
                    _Contract("", "SHFE"),
                ]

        query = build_vnpy_contract_query_fn(_MainEngine())
        self.assertEqual(query("RB"), ["RB2412.SHFE", "RB2501.SHFE"])

    def test_resolver_vnpy_hook_prefers_nearest_future_month(self) -> None:
        class _Exchange:
            def __init__(self, value: str) -> None:
                self.value = value

        class _Contract:
            def __init__(self, symbol: str, exchange: str) -> None:
                self.symbol = symbol
                self.exchange = _Exchange(exchange)

        class _MainEngine:
            def get_all_contracts(self):
                return [
                    _Contract("RB2412", "SHFE"),
                    _Contract("RB2505", "SHFE"),
                    _Contract("RB2510", "SHFE"),
                ]

        query = build_vnpy_contract_query_fn(_MainEngine())
        resolver = ContractResolver(calendar_df=None, vnpy_query_fn=query)
        info = resolver.resolve_active_contract("RB0", "2024-12-25")
        self.assertEqual(info.active_contract, "RB2505.SHFE")


if __name__ == "__main__":
    unittest.main()
