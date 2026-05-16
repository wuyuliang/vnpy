"""Group-pool training tests for model_pipeline.

目标：
1) 支持从 ranking CSV 按 group 分类 symbol；
2) 支持 CLI 进入 group-pool 模式（组 × interval 批量训练）；
3) 输出模型命名包含组信息，便于上线识别。
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

import pandas as pd

import cta.model.model_pipeline as mp


class TestGroupPoolHelpers(unittest.TestCase):
    def test_parse_args_supports_group_pool_flags(self) -> None:
        ns = mp._parse_args(
            [
                "--group-pool",
                "--group-by",
                "tier",
                "--group-min-size",
                "3",
                "--include-disabled-symbols",
            ]
        )
        self.assertTrue(bool(ns.group_pool))
        self.assertEqual(str(ns.group_by), "tier")
        self.assertEqual(int(ns.group_min_size), 3)
        self.assertTrue(bool(ns.include_disabled_symbols))

    def test_load_symbol_groups_from_ranking_groups_by_tier(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_group_rank_") as td:
            p = Path(td) / "ranking.csv"
            pd.DataFrame(
                {
                    "symbol": ["AU0", "RB0", "HC0", "AG0"],
                    "exchange": ["SHFE", "SHFE", "SHFE", "SHFE"],
                    "research_rank": [4, 1, 2, 3],
                    "tier": ["B", "A", "A", "B"],
                }
            ).to_csv(p, index=False, encoding="utf-8-sig")
            groups = mp._load_symbol_groups_from_ranking(
                p,
                top_n=0,
                group_by="tier",
                min_symbols_per_group=1,
                respect_disabled_manifest=False,
            )
        self.assertEqual([g[0] for g in groups], ["tier_a", "tier_b"])
        self.assertEqual(groups[0][1], [("RB0", "SHFE"), ("HC0", "SHFE")])
        self.assertEqual(groups[1][1], [("AG0", "SHFE"), ("AU0", "SHFE")])

    def test_load_symbol_groups_index_cluster_collects_all_four(self) -> None:
        """IF0/IH0/IC0/IM0 全部映射到 cluster_index，且独立成组。"""
        with tempfile.TemporaryDirectory(prefix="cta_group_rank_") as td:
            p = Path(td) / "ranking.csv"
            pd.DataFrame(
                {
                    "symbol": ["IF0", "IH0", "IC0", "IM0", "RB0"],
                    "exchange": ["CFFEX", "CFFEX", "CFFEX", "CFFEX", "SHFE"],
                    "research_rank": [72, 73, 74, 75, 1],
                }
            ).to_csv(p, index=False, encoding="utf-8-sig")
            groups = mp._load_symbol_groups_from_ranking(
                p, top_n=0, group_by="cluster",
                min_symbols_per_group=2, respect_disabled_manifest=False,
            )
        gd = dict(groups)
        self.assertIn("cluster_index", gd)
        self.assertEqual(
            sorted(s for s, _ in gd["cluster_index"]),
            ["IC0", "IF0", "IH0", "IM0"],
        )

    def test_parse_args_supports_only_clusters(self) -> None:
        ns = mp._parse_args(
            ["--group-pool", "--group-by", "cluster", "--only-clusters", "index", "bond"]
        )
        self.assertEqual(list(ns.only_clusters), ["index", "bond"])

    def test_parse_args_use_portfolio_logic_runtime_default_false(self) -> None:
        ns = mp._parse_args(["--group-pool"])
        self.assertFalse(bool(ns.use_portfolio_logic_runtime))

    def test_parse_args_use_portfolio_logic_runtime_explicit_true(self) -> None:
        ns = mp._parse_args(["--group-pool", "--use-portfolio-logic-runtime"])
        self.assertTrue(bool(ns.use_portfolio_logic_runtime))

    def test_effective_oot_cfg_replaced_when_flag_on(self) -> None:
        """OotEvaluationConfig 的 use_portfolio_logic_runtime 被正确设为 True。"""
        from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG
        from dataclasses import replace as dc_replace
        eff = dc_replace(DEFAULT_OOT_EVAL_CONFIG, use_portfolio_logic_runtime=True)
        self.assertTrue(eff.use_portfolio_logic_runtime)
        # 子配置的所有 enable_* 默认都是 True（design v4）
        pl = eff.portfolio_logic
        self.assertTrue(pl.enable_htf_gate)
        self.assertTrue(pl.enable_ranker)
        self.assertTrue(pl.enable_trailing)
        self.assertTrue(pl.enable_pyramid)
        self.assertTrue(pl.enable_score_calibration)
        self.assertTrue(pl.enable_risk_throttle)

    def test_only_clusters_filter_keeps_index_drops_others(self) -> None:
        """--only-clusters index 仅保留 cluster_index 组，drop cluster_black 等。"""
        from cta.model.model_pipeline import _safe_name
        all_groups: list[tuple[str, list[tuple[str, str | None]]]] = [
            ("cluster_index", [("IF0", "CFFEX"), ("IH0", "CFFEX"), ("IC0", "CFFEX"), ("IM0", "CFFEX")]),
            ("cluster_black", [("RB0", "SHFE"), ("HC0", "SHFE")]),
            ("cluster_metal", [("CU0", "SHFE"), ("AL0", "SHFE")]),
        ]
        only = ["index"]
        group_by_key = _safe_name("cluster")
        wanted = {f"{group_by_key}_{_safe_name(c)}" for c in only}
        kept = [(g, m) for g, m in all_groups if g in wanted]
        self.assertEqual([g for g, _ in kept], ["cluster_index"])
        self.assertEqual(len(kept[0][1]), 4)


class TestGroupPoolCliDispatch(unittest.TestCase):
    def test_group_pool_dispatches_each_group_x_interval(self) -> None:
        captured: list[dict[str, Any]] = []

        fake_groups = [
            ("tier_a", [("RB0", "SHFE"), ("HC0", "SHFE")]),
            ("tier_b", [("AU0", "SHFE"), ("AG0", "SHFE")]),
        ]

        def fake_run(*, symbol, exchange, interval, **kwargs):
            captured.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "pool_symbols": kwargs.get("pool_symbols"),
                    "pool_name": kwargs.get("pool_name"),
                }
            )
            return mock.MagicMock(
                report_path="/tmp/x",
                prediction_path="/tmp/y",
                metrics_path="/tmp/z",
                top_feature_importance_path="/tmp/f",
            )

        argv = [
            "--group-pool",
            "--group-by",
            "tier",
            "--symbols-ranking-path",
            "cta/feature/symbols_research_ranking.csv",
            "--interval",
            "day",
            "60min",
        ]
        with mock.patch.object(mp, "_load_symbol_groups_from_ranking", return_value=fake_groups), \
             mock.patch.object(mp, "run_model_pipeline", side_effect=fake_run), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            mp.main(argv)

        self.assertEqual(len(captured), 4)
        pool_names = {str(c.get("pool_name")) for c in captured}
        self.assertIn("GRP_TIER_A", pool_names)
        self.assertIn("GRP_TIER_B", pool_names)
        self.assertTrue(all(c.get("pool_symbols") for c in captured))


if __name__ == "__main__":
    unittest.main()

