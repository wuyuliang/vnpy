"""Unit tests for HTF interval gate."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.config import IntervalGateConfig
from cta.portfolio_logic.interval_gate import HtfGate


class TestHtfGate(unittest.TestCase):
    def test_compute_htf_state_consensus_and_conflict(self) -> None:
        day = pd.DataFrame(
            {
                "symbol": ["RB0", "HC0", "CU0", "AG0"],
                "exchange": ["SHFE"] * 4,
                "datetime": pd.to_datetime(["2024-01-02"] * 4),
                "pred_regime_label": ["trend_up", "trend_down", "range", "trend_up"],
            }
        )
        m60 = pd.DataFrame(
            {
                "symbol": ["RB0", "HC0", "CU0", "AG0"],
                "exchange": ["SHFE"] * 4,
                "datetime": pd.to_datetime(["2024-01-02 15:00:00"] * 4),
                "pred_regime_label": ["trend_up", "trend_down", "range", "trend_down"],
            }
        )
        gate = HtfGate(IntervalGateConfig())
        states = gate.compute_htf_state({"day": day, "60min": m60}, as_of=pd.Timestamp("2024-01-03"))

        self.assertEqual(states[("RB0", "SHFE")]["state"], "long_only")
        self.assertEqual(states[("HC0", "SHFE")]["state"], "short_only")
        self.assertEqual(states[("CU0", "SHFE")]["state"], "both")
        self.assertEqual(states[("AG0", "SHFE")]["state"], "none")

    def test_compute_htf_state_accepts_minute_alias_key(self) -> None:
        day = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "datetime": pd.to_datetime(["2024-01-02"]),
                "pred_regime_label": ["trend_up"],
            }
        )
        m60 = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "datetime": pd.to_datetime(["2024-01-02 15:00:00"]),
                "pred_regime_label": ["trend_up"],
            }
        )
        gate = HtfGate(IntervalGateConfig())
        states = gate.compute_htf_state({"day": day, "minute60": m60}, as_of=pd.Timestamp("2024-01-03"))
        self.assertEqual(states[("RB0", "SHFE")]["state"], "long_only")

    def test_is_state_fresh_checks_interval_ttl(self) -> None:
        gate = HtfGate(IntervalGateConfig())
        now = pd.Timestamp("2024-01-03 15:00:00")
        fresh_day = {"state": "long_only", "computed_at": pd.Timestamp("2024-01-03 09:00:00")}
        stale_day = {"state": "long_only", "computed_at": pd.Timestamp("2024-01-01 09:00:00")}

        self.assertTrue(gate.is_state_fresh(fresh_day, current_time=now, interval="day"))
        self.assertFalse(gate.is_state_fresh(stale_day, current_time=now, interval="day"))

    def test_filter_marks_allowed_and_blocked_rows(self) -> None:
        # 2026-05-24：cfg 默认 dict 已含 minute60 路径 "both"；本测试覆盖原始 strict-skip 语义
        # （所有 cell 都走全局 skip），所以显式传空 dict。
        gate = HtfGate(IntervalGateConfig(
            fallback_when_htf_missing="skip",
            fallback_when_htf_missing_by_cluster_interval={},
        ))
        now = pd.Timestamp("2024-01-03 15:00:00")
        htf_state = {
            ("RB0", "SHFE"): {"state": "long_only", "computed_at": now},
            ("HC0", "SHFE"): {"state": "short_only", "computed_at": now},
            ("CU0", "SHFE"): {"state": "both", "computed_at": now},
            ("AG0", "SHFE"): {"state": "none", "computed_at": now},
            ("ZN0", "SHFE"): {"state": "long_only", "computed_at": pd.Timestamp("2024-01-01 09:00:00")},
        }
        opp = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "HC0", "HC0", "CU0", "AG0", "ZN0", "NI0"],
                "exchange": ["SHFE"] * 8,
                "direction": ["long", "short", "short", "long", "long", "long", "long", "long"],
                "interval": ["60min"] * 8,
            }
        )
        out = gate.filter(opp, htf_state=htf_state, current_time=now)

        self.assertEqual(bool(out.iloc[0]["htf_allowed"]), True)
        self.assertEqual(str(out.iloc[0]["htf_alignment"]), "aligned")
        self.assertEqual(bool(out.iloc[1]["htf_allowed"]), False)
        self.assertEqual(str(out.iloc[1]["htf_alignment"]), "opposite")
        self.assertEqual(bool(out.iloc[2]["htf_allowed"]), True)
        self.assertEqual(bool(out.iloc[3]["htf_allowed"]), False)
        self.assertEqual(str(out.iloc[4]["htf_alignment"]), "neutral")
        self.assertEqual(bool(out.iloc[4]["htf_allowed"]), True)
        self.assertEqual(bool(out.iloc[5]["htf_allowed"]), False)
        self.assertEqual(str(out.iloc[5]["htf_block_reason"]), "htf_conflict")
        self.assertEqual(bool(out.iloc[6]["htf_allowed"]), False)
        self.assertEqual(str(out.iloc[6]["htf_block_reason"]), "htf_missing")
        self.assertEqual(bool(out.iloc[7]["htf_allowed"]), False)
        self.assertEqual(str(out.iloc[7]["htf_block_reason"]), "htf_missing")

    def test_filter_allows_missing_when_fallback_both(self) -> None:
        gate = HtfGate(IntervalGateConfig(fallback_when_htf_missing="both"))
        now = pd.Timestamp("2024-01-03 15:00:00")
        opp = pd.DataFrame(
            {
                "symbol": ["NI0"],
                "exchange": ["SHFE"],
                "direction": ["long"],
                "interval": ["60min"],
            }
        )
        out = gate.filter(opp, htf_state={}, current_time=now)
        self.assertEqual(bool(out.iloc[0]["htf_allowed"]), True)
        self.assertEqual(str(out.iloc[0]["htf_alignment"]), "neutral")
        self.assertEqual(str(out.iloc[0]["htf_block_reason"]), "")

    def test_filter_uses_candidate_interval_rank_semantics(self) -> None:
        gate = HtfGate(
            IntervalGateConfig(
                htf_intervals=("day", "60min", "30min"),
                fallback_when_htf_missing="skip",
                fallback_when_htf_missing_by_cluster_interval={},
                state_ttl_seconds={"day": 86400, "60min": 3600, "30min": 1800},
            )
        )
        now = pd.Timestamp("2024-01-03 15:00:00")
        htf_state = {
            ("RB0", "SHFE"): {
                "state": "long_only",
                "computed_at": now,
                "computed_at_by_interval": {
                    "day": pd.Timestamp("2024-01-03 09:00:00"),
                    "60min": pd.Timestamp("2024-01-03 12:00:00"),  # stale at now
                    "30min": pd.Timestamp("2024-01-03 14:45:00"),
                },
                "regime_by_interval": {
                    "day": "trend_up",
                    "60min": "trend_up",
                    "30min": "trend_up",
                },
            }
        }
        opp = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "direction": ["long", "long", "long"],
                "interval": ["day", "60min", "30min"],
            }
        )
        out = gate.filter(opp, htf_state=htf_state, current_time=now)
        # day -> 只看 day，自身新鲜，放行
        self.assertTrue(bool(out.iloc[0]["htf_allowed"]))
        # 60min -> 看 60min+day，60min 过期，拦截
        self.assertFalse(bool(out.iloc[1]["htf_allowed"]))
        self.assertEqual(str(out.iloc[1]["htf_block_reason"]), "htf_missing")
        # 30min -> 看 30min+60min+day，60min 过期，同样拦截
        self.assertFalse(bool(out.iloc[2]["htf_allowed"]))
        self.assertEqual(str(out.iloc[2]["htf_block_reason"]), "htf_missing")


if __name__ == "__main__":
    unittest.main()
