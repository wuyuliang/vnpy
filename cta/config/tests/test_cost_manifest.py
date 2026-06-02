"""按 (cluster, interval) 真实 commission + slippage 成本表测试。

历史背景（2026-05-24 OOT 诊断）：
  - `oot_20260523_034107` / `oot_20260523_154428` 中 `cost_pct` 在所有 5,200+ 已执行行里
    mean/median/p99/max **全是 3.00 bps**——commission + slippage 被硬编码为常数。
  - 真实 Chinese commodity day-level round-trip 应该按品种分层：
      * bond (T/TF/TS)  ≈ 0.5-1 bp（保证金大、价差小）
      * index (IF/IC/IH/IM) ≈ 0.5-1 bp（费率万分点级）
      * precious (AU/AG) ≈ 1-2 bp
      * metal (CU/AL/...) ≈ 2-3 bp
      * black (RB/HC/I/...) ≈ 4-6 bp（tick 跨度大）
      * chemical / agri ≈ 2-4 bp
      * other ≈ 3 bp（默认）
  - cluster_bond 单簇 cost/|gross| = 152%（before）/ 120%（after），被低估的成本是亏损主因。

本测试覆盖：
  1) 默认 manifest 含 8 个 cluster 且 commission + slippage 数值落在合理 bp 区间
  2) bond 的 total < other 的 total（保证金大 → 名义口径成本低）
  3) build_cluster_interval_cost_dict() 生成的 dict key 形如 "cluster|interval"
  4) 未在 manifest 中的 cluster 走 fallback default
"""
from __future__ import annotations

import unittest

from cta.config.cost_manifest import (
    CLUSTER_DAY_COMMISSION_PCT,
    CLUSTER_DAY_SLIPPAGE_PCT,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_SLIPPAGE_PCT,
    build_cluster_interval_cost_dict,
    infer_commission_pct,
    infer_slippage_pct,
)


class TestCostManifest(unittest.TestCase):
    def test_manifest_covers_all_known_clusters(self) -> None:
        expected = {"bond", "index", "precious", "metal", "black", "chemical", "agri", "other"}
        self.assertEqual(set(CLUSTER_DAY_COMMISSION_PCT.keys()), expected)
        self.assertEqual(set(CLUSTER_DAY_SLIPPAGE_PCT.keys()), expected)

    def test_manifest_values_are_in_reasonable_bp_range(self) -> None:
        # 单边 commission 应在 0-10bp（0.001%-0.10%）之间
        for cluster, v in CLUSTER_DAY_COMMISSION_PCT.items():
            self.assertGreaterEqual(v, 0.0, f"{cluster} commission negative")
            self.assertLessEqual(v, 0.0010, f"{cluster} commission > 10bp 不真实")
        # 单边 slippage 在 0-15bp 之间
        for cluster, v in CLUSTER_DAY_SLIPPAGE_PCT.items():
            self.assertGreaterEqual(v, 0.0, f"{cluster} slippage negative")
            self.assertLessEqual(v, 0.0015, f"{cluster} slippage > 15bp 不真实")

    def test_bond_cheaper_than_black(self) -> None:
        """bond 票面 1000k+ / 黑色 RB 单手仅几万，bond cost 单笔占比应低于 black。"""
        bond_total = CLUSTER_DAY_COMMISSION_PCT["bond"] + CLUSTER_DAY_SLIPPAGE_PCT["bond"]
        black_total = CLUSTER_DAY_COMMISSION_PCT["black"] + CLUSTER_DAY_SLIPPAGE_PCT["black"]
        self.assertLess(bond_total, black_total)

    def test_default_constants_present(self) -> None:
        # 默认值用于 manifest 未覆盖的 cluster
        self.assertGreater(DEFAULT_COMMISSION_PCT, 0.0)
        self.assertGreater(DEFAULT_SLIPPAGE_PCT, 0.0)

    def test_infer_commission_pct_known_cluster(self) -> None:
        self.assertEqual(infer_commission_pct("bond"), CLUSTER_DAY_COMMISSION_PCT["bond"])
        self.assertEqual(infer_commission_pct("BOND"), CLUSTER_DAY_COMMISSION_PCT["bond"])

    def test_infer_commission_pct_unknown_falls_back(self) -> None:
        self.assertEqual(infer_commission_pct("unknown_cluster"), DEFAULT_COMMISSION_PCT)

    def test_infer_slippage_pct_known_and_fallback(self) -> None:
        self.assertEqual(infer_slippage_pct("precious"), CLUSTER_DAY_SLIPPAGE_PCT["precious"])
        self.assertEqual(infer_slippage_pct("xyz"), DEFAULT_SLIPPAGE_PCT)

    def test_build_cluster_interval_cost_dict_default_intervals(self) -> None:
        comm = build_cluster_interval_cost_dict(kind="commission")
        slip = build_cluster_interval_cost_dict(kind="slippage")
        # 默认含 day / 60min / 30min × 8 cluster = 24 条
        self.assertGreaterEqual(len(comm), 8 * 3)
        self.assertGreaterEqual(len(slip), 8 * 3)
        # key 形如 "cluster|interval"
        for k in comm:
            parts = k.split("|")
            self.assertEqual(len(parts), 2)

    def test_build_dict_minute_has_higher_slippage_than_day(self) -> None:
        """分钟级深度浅，滑点应该 ≥ day。"""
        slip = build_cluster_interval_cost_dict(kind="slippage")
        for cluster in ("black", "metal", "agri", "chemical"):
            day_v = slip.get(f"{cluster}|day", 0.0)
            min60_v = slip.get(f"{cluster}|60min", 0.0)
            self.assertGreaterEqual(
                min60_v, day_v,
                f"{cluster}: 60min slippage {min60_v} should >= day {day_v}"
            )

    def test_build_dict_raises_on_invalid_kind(self) -> None:
        with self.assertRaises(ValueError):
            build_cluster_interval_cost_dict(kind="foo")


if __name__ == "__main__":
    unittest.main()
