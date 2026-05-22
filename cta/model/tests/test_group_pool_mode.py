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

    def test_parse_args_enable_oscillation_taper_default_false(self) -> None:
        ns = mp._parse_args(["--group-pool"])
        self.assertFalse(bool(ns.enable_oscillation_taper))

    def test_effective_oot_cfg_enables_oscillation_taper_for_requested_intervals(self) -> None:
        cfg = mp._build_effective_oot_config(
            use_portfolio_logic_runtime=True,
            enable_oscillation_taper=True,
            intervals=("day", "60min"),
        )
        pl = cfg.portfolio_logic
        self.assertTrue(cfg.use_portfolio_logic_runtime)
        self.assertTrue(pl.enable_oscillation_taper)
        self.assertTrue(pl.oscillation_taper.is_enabled("index", "day"))
        self.assertTrue(pl.oscillation_taper.is_enabled("black", "minute60"))
        self.assertFalse(pl.oscillation_taper.is_enabled("index", "30min"))

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
        from cta.model.dataset.pipeline_feature_curation import _safe_name
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

    def test_write_group_pool_runtime_bundle_writes_aggregate_trade_details(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_group_runtime_bundle_") as td:
            root = Path(td)
            run_records: list[dict[str, Any]] = []
            for group_name, pool_name, px in (
                ("cluster_black", "GRP_CLUSTER_BLACK", 100.0),
                ("cluster_metal", "GRP_CLUSTER_METAL", 200.0),
            ):
                out_dir = root / f"20260516_{pool_name}_minute60_both_model_pipeline"
                out_dir.mkdir(parents=True, exist_ok=True)
                oot_trades = out_dir / f"20260516_{pool_name}_minute60_both_oot_trade_details.csv"
                pd.DataFrame(
                    {
                        "datetime": [pd.Timestamp("2020-01-02 09:00:00")],
                        "symbol": ["RB0"],
                        "exchange": ["SHFE"],
                        "interval": ["60min"],
                        "side": ["long"],
                        "entry_price": [px],
                        "open_notional_at_entry": [1_000_000.0],
                        "open_notional_after_exit": [750_000.0],
                    }
                ).to_csv(oot_trades, index=False, encoding="utf-8-sig")

                # 其余路径给占位文件，模拟真实 ModelPipelineResult。
                def _touch(name: str) -> Path:
                    p = out_dir / name
                    p.write_text("x", encoding="utf-8")
                    return p

                res = mp.ModelPipelineResult(
                    output_dir=out_dir,
                    candidate_path=_touch("candidates.csv"),
                    feature_table_path=_touch("feature_table.parquet"),
                    prediction_path=_touch("predictions.csv"),
                    metrics_path=_touch("metrics.csv"),
                    oot_monthly_path=_touch("oot_monthly_returns.csv"),
                    oot_summary_path=_touch("oot_summary.csv"),
                    oot_trades_path=oot_trades,
                    html_report_path=_touch("report.html"),
                    top_feature_importance_path=_touch("top10_feature_importance.csv"),
                    report_path=_touch("model_report.md"),
                )
                run_records.append(
                    {
                        "interval": "60min",
                        "group_name": group_name,
                        "pool_name": pool_name,
                        "members": [("RB0", "SHFE")],
                        "result": res,
                    }
                )

            bundle_dir = mp._write_group_pool_runtime_bundle(
                root=root,
                run_date_tag="20260516",
                group_by="cluster",
                trade_side_mode="both",
                run_records=run_records,
            )
            self.assertIsNotNone(bundle_dir)
            assert bundle_dir is not None
            self.assertTrue(bundle_dir.exists())

            agg_csv = bundle_dir / "20260516_group_pool_cluster_both_all_symbol_group_oot_trade_details.csv"
            self.assertTrue(agg_csv.exists())
            agg_df = pd.read_csv(agg_csv, encoding="utf-8-sig")
            self.assertEqual(len(agg_df), 2)
            self.assertEqual(set(agg_df["group_name"].astype(str)), {"cluster_black", "cluster_metal"})
            self.assertIn("position_notional_after_trade", agg_df.columns)
            self.assertTrue((pd.to_numeric(agg_df["position_notional_after_trade"], errors="coerce") == 750000.0).all())

            manifest_csv = bundle_dir / "20260516_group_pool_cluster_both_symbol_group_run_manifest.csv"
            self.assertTrue(manifest_csv.exists())
            manifest_df = pd.read_csv(manifest_csv, encoding="utf-8-sig")
            self.assertEqual(len(manifest_df), 2)
            self.assertIn("model_dir", manifest_df.columns)
            self.assertIn("group_detail_manifest", manifest_df.columns)

            oot_reports = sorted(root.glob("oot_*_cluster_both"))
            self.assertGreaterEqual(len(oot_reports), 1)
            latest = oot_reports[-1]
            self.assertTrue((latest / "00_overview" / "headline_metrics.csv").exists())
            self.assertTrue((latest / "06_drilldown" / "gate_funnel.csv").exists())

    def test_recompute_oot_with_shared_htf_reference_rewrites_blocked_htf(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_recompute_htf_") as td:
            root = Path(td)

            def _mk_result(tag: str, interval: str, path_id: str) -> mp.ModelPipelineResult:
                out = root / f"{tag}_{interval}"
                out.mkdir(parents=True, exist_ok=True)
                pred_path = out / f"{path_id}_predictions.csv"
                pd.DataFrame(
                    {
                        "datetime": [pd.Timestamp("2020-01-06 09:00:00")],
                        "entry_datetime": [pd.Timestamp("2020-01-06 09:00:00")],
                        "exit_datetime": [pd.Timestamp("2020-01-06 11:00:00")],
                        "symbol": ["RB0"],
                        "exchange": ["SHFE"],
                        "interval": [interval],
                        "signal_type": ["donchian_breakout"],
                        "side": ["long"],
                        "pred_regime_label": ["trend_up"],
                        "pred_split": ["test"],
                        "window_id": [0],
                        "is_executed": [1],
                        "entry_price": [100.0],
                        "future_mfe_atr": [1.0],
                        "future_mae_atr": [0.2],
                    }
                ).to_csv(pred_path, index=False, encoding="utf-8-sig")

                def _touch(name: str) -> Path:
                    p = out / name
                    p.write_text("x", encoding="utf-8")
                    return p

                return mp.ModelPipelineResult(
                    output_dir=out,
                    candidate_path=_touch(f"{path_id}_candidates.csv"),
                    feature_table_path=_touch(f"{path_id}_feature_table.parquet"),
                    prediction_path=pred_path,
                    metrics_path=_touch(f"{path_id}_metrics.csv"),
                    oot_monthly_path=out / f"{path_id}_oot_monthly_returns.csv",
                    oot_summary_path=out / f"{path_id}_oot_summary.csv",
                    oot_trades_path=out / f"{path_id}_oot_trade_details.csv",
                    html_report_path=_touch(f"{path_id}_report.html"),
                    top_feature_importance_path=_touch(f"{path_id}_top10.csv"),
                    report_path=_touch(f"{path_id}_model_report.md"),
                )

            r_day = _mk_result("run", "day", "day")
            r_60 = _mk_result("run", "60min", "m60")

            from cta.config.model_oot_eval_config import OotEvaluationConfig
            from cta.portfolio_logic.config import PortfolioLogicConfig

            cfg = OotEvaluationConfig(
                use_trade_filter_gate=False,
                use_regime_gate=False,
                use_mfe_mae_gate=False,
                use_test_split_only=True,
                require_executed_only=True,
                use_intrabar_stop_tracking=False,
                use_portfolio_constraints=False,
                use_position_sizing=False,
                use_portfolio_logic_runtime=True,
                portfolio_logic=PortfolioLogicConfig(
                    enable_htf_gate=True,
                    enable_ranker=False,
                    enable_risk_throttle=False,
                    enable_pyramid=False,
                ),
            )
            rewritten = mp._recompute_oot_with_shared_htf_reference([r_day, r_60], cfg)
            self.assertEqual(rewritten, 2)

            s_day = pd.read_csv(r_day.oot_summary_path, encoding="utf-8-sig")
            s_60 = pd.read_csv(r_60.oot_summary_path, encoding="utf-8-sig")
            self.assertEqual(int(s_day.iloc[0]["blocked_htf_rows"]), 0)
            self.assertEqual(int(s_60.iloc[0]["blocked_htf_rows"]), 0)

            t_day = pd.read_csv(r_day.oot_trades_path, encoding="utf-8-sig")
            t_60 = pd.read_csv(r_60.oot_trades_path, encoding="utf-8-sig")
            self.assertEqual(str(t_day.iloc[0]["execution_status"]), "executed")
            self.assertEqual(str(t_60.iloc[0]["execution_status"]), "executed")


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
        # W4 重构后 `run_model_pipeline` / `_load_symbol_groups_from_ranking` 的真实定义在
        # `cta.model.orchestration.pipeline_orchestrator`，而 `cta.model.model_pipeline` 只是 shim。
        # `pipeline_orchestrator.main()` 内部按模块本地名字查找这两个符号，因此必须 patch
        # 到 orchestrator 上才能生效；patch shim 是无效操作（会让真模型走真训练）。
        import cta.model.orchestration.pipeline_orchestrator as _impl
        with mock.patch.object(_impl, "_load_symbol_groups_from_ranking", return_value=fake_groups), \
             mock.patch.object(_impl, "run_model_pipeline", side_effect=fake_run), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            mp.main(argv)

        self.assertEqual(len(captured), 4)
        pool_names = {str(c.get("pool_name")) for c in captured}
        self.assertIn("GRP_TIER_A", pool_names)
        self.assertIn("GRP_TIER_B", pool_names)
        self.assertTrue(all(c.get("pool_symbols") for c in captured))


if __name__ == "__main__":
    unittest.main()
