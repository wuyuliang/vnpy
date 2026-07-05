"""eval_only：从已有 train run 目录复用 predictions.csv 重跑 OOT 的单测。

历史背景（2026-05-25）：
  - 当前 `cta.model.model_pipeline` 把 train + predict + OOT eval 串成单一流程；
    用户每次改 cfg（Action 1/2/3 任一）都被迫重训 4-5 个模型，浪费 5-10 分钟/cluster。
  - 现有 `_recompute_oot_with_shared_htf_reference` 已是原型，但只在 enable_htf_gate=True
    时自动触发、不暴露 CLI、不写 aggregated bundle。
  - 本测试锁定新模块 `cta.model.eval_only_run` 的 API 契约。

覆盖：
  1) discover_train_runs 从 root 按 pattern 找到所有 train 产物目录并解析路径
  2) rerun_oot_for_train_run 把更新后的 cfg 应用到既有 predictions.csv，输出新的 oot_*.csv
  3) Action 1/2/3 任一 cfg 切换后，重跑 OOT 的 cost_pct / 决策位 / htf 路由真的不同
  4) run_oot_eval_batch 跨多 cluster 聚合产出 oot_<timestamp>_<run_tag> bundle
  5) cfg fingerprint dump 到 meta/ 防止"命名失真"
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.eval_only_cli import _build_cfg_from_args, _parse_args
from cta.risk.config import RiskSystemConfig
from cta.portfolio_logic.config import CapsConfig, PortfolioLogicConfig
from cta.model.eval_only_run import (
    TrainRunMeta,
    discover_train_runs,
    rerun_oot_for_train_run,
    run_oot_eval_batch,
)


def _make_fake_train_run(
    root: Path,
    run_date: str = "20260523",
    pool_name: str = "GRP_CLUSTER_BLACK",
    interval: str = "day",
    side: str = "both",
    n_rows: int = 50,
) -> Path:
    """造一个最小可用的 train 输出目录（含 predictions.csv 与必备列）。"""
    out_dir = root / f"{run_date}_{pool_name}_{interval}_{side}_model_pipeline"
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    n = n_rows
    # 必备列：与 _evaluate_oot_real_execution 实际消费的列保持一致
    base_ts = pd.Timestamp("2024-01-15 09:00:00")
    df = pd.DataFrame({
        "datetime": pd.date_range(base_ts, periods=n, freq="B"),
        "entry_datetime": pd.date_range(base_ts, periods=n, freq="B"),
        "signal_datetime": pd.date_range(base_ts, periods=n, freq="B"),
        "exit_datetime": pd.date_range(base_ts + pd.Timedelta(days=2), periods=n, freq="B"),
        "symbol": ["RB0"] * n,
        "exchange": ["SHFE"] * n,
        "interval": [interval] * n,
        "signal_type": ["bull_pullback_continuation"] * n,
        "side": rng.choice(["long", "short"], size=n).tolist(),
        "entry_action": ["buy"] * n,
        "exit_action": ["sell"] * n,
        "is_executed": [1] * n,
        "entry_price": rng.uniform(3000, 4000, size=n),
        "exit_price_ref": rng.uniform(3000, 4000, size=n),
        "trigger": rng.uniform(3000, 4000, size=n),
        "stop_price": rng.uniform(2900, 3100, size=n),
        "label_class": rng.integers(0, 2, size=n),
        "regime_label": ["trend_up"] * n,
        "ma_alignment": [1] * n,
        "pred_regime_label": ["trend_up"] * n,
        "trade_filter_prob": rng.uniform(0.5, 0.9, size=n),
        "trade_filter_prob_pctl": rng.uniform(60, 95, size=n),
        "pred_mfe_atr": rng.uniform(0.5, 2.0, size=n),
        "pred_mae_atr": rng.uniform(0.3, 1.0, size=n),
        "final_decision_score": rng.uniform(0.4, 0.8, size=n),
        "final_decision_model_kind": ["stack_ensemble"] * n,
        "future_mfe_atr": rng.uniform(0.0, 2.5, size=n),
        "future_mae_atr": rng.uniform(0.0, 1.5, size=n),
        "future_pnl_atr": rng.uniform(-1.0, 2.0, size=n),
        "window_id": [0] * n,
        "pred_split": ["test"] * n,
        "_model_pass": [True] * n,
        "_model_block_reason": [""] * n,
    })
    pred_path = out_dir / f"{run_date}_{pool_name}_{interval}_{side}_predictions.csv"
    df.to_csv(pred_path, index=False, encoding="utf-8-sig")
    # 占位的 model_report 让 discover 不空读
    (out_dir / f"{run_date}_{pool_name}_{interval}_{side}_model_report.md").write_text(
        f"# CTA Model Pipeline Report\n- pool: {pool_name}\n- interval: {interval}\n",
        encoding="utf-8",
    )
    return out_dir


def _make_single_trade_train_run(
    root: Path,
    *,
    run_date: str,
    pool_name: str,
    interval: str,
    side: str = "both",
    symbol: str = "IF0",
    exchange: str = "CFFEX",
    signal_type: str = "bull_pullback_continuation",
    trade_time: str = "2024-01-02 09:30:00",
    exit_time: str = "2024-01-10 15:00:00",
) -> Path:
    out_dir = root / f"{run_date}_{pool_name}_{interval}_{side}_model_pipeline"
    out_dir.mkdir(parents=True, exist_ok=True)
    dt = pd.Timestamp(trade_time)
    ex = pd.Timestamp(exit_time)
    df = pd.DataFrame(
        {
            "datetime": [dt],
            "entry_datetime": [dt],
            "signal_datetime": [dt],
            "exit_datetime": [ex],
            "symbol": [symbol],
            "exchange": [exchange],
            "interval": [interval],
            "signal_type": [signal_type],
            "side": ["long"],
            "pred_split": ["test"],
            "window_id": [0],
            "is_executed": [1],
            "entry_price": [100.0],
            "future_mfe_atr": [1.0],
            "future_mae_atr": [0.2],
        }
    )
    prefix = f"{run_date}_{pool_name}_{interval}_{side}"
    pred_path = out_dir / f"{prefix}_predictions.csv"
    df.to_csv(pred_path, index=False, encoding="utf-8-sig")
    (out_dir / f"{prefix}_model_report.md").write_text(
        f"# report\n- pool: {pool_name}\n- interval: {interval}\n",
        encoding="utf-8",
    )
    return out_dir


class TestDiscoverTrainRuns(unittest.TestCase):
    def test_finds_dirs_matching_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_train_run(root, pool_name="GRP_CLUSTER_BLACK", interval="day")
            _make_fake_train_run(root, pool_name="GRP_CLUSTER_INDEX", interval="day")
            (root / "20260523_other_dir_no_match").mkdir()
            metas = discover_train_runs(root, pattern="*_model_pipeline")
            self.assertEqual(len(metas), 2)
            pools = {m.pool_name for m in metas}
            self.assertEqual(pools, {"GRP_CLUSTER_BLACK", "GRP_CLUSTER_INDEX"})
            for m in metas:
                self.assertTrue(m.prediction_path.exists(), f"missing {m.prediction_path}")
                self.assertEqual(m.trade_side_mode, "both")

    def test_skips_dirs_without_predictions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "20260523_GRP_CLUSTER_X_day_both_model_pipeline"
            d.mkdir()
            metas = discover_train_runs(root, pattern="*_model_pipeline")
            self.assertEqual(len(metas), 0)


class TestRerunOotForTrainRun(unittest.TestCase):
    def test_writes_oot_outputs_to_destination(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            train_dir = _make_fake_train_run(root)
            metas = discover_train_runs(root, pattern="*_model_pipeline")
            self.assertEqual(len(metas), 1)
            out_dir = root / "eval_out"
            res = rerun_oot_for_train_run(metas[0], cfg=OotEvaluationConfig(), output_dir=out_dir)
            self.assertTrue(Path(res.oot_trades_path).exists())
            self.assertTrue(Path(res.oot_summary_path).exists())
            self.assertTrue(Path(res.oot_monthly_path).exists())

    def test_cfg_change_changes_cost_pct_in_trade_table(self) -> None:
        """Action 1：修改 commission_pct_by_cluster_interval 后，cost_pct 必须真的不同。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            train_dir = _make_fake_train_run(root)
            metas = discover_train_runs(root, pattern="*_model_pipeline")
            # cfg A：用真实 manifest 默认
            cfg_a = OotEvaluationConfig(signal_type_blacklist=())
            out_a = root / "eval_a"
            res_a = rerun_oot_for_train_run(metas[0], cfg=cfg_a, output_dir=out_a)
            # cfg B：显式空 dict 回退到全局 3bp
            cfg_b = OotEvaluationConfig(
                commission_pct_by_cluster_interval={},
                slippage_pct_by_cluster_interval={},
                signal_type_blacklist=(),
            )
            out_b = root / "eval_b"
            res_b = rerun_oot_for_train_run(metas[0], cfg=cfg_b, output_dir=out_b)
            df_a = pd.read_csv(res_a.oot_trades_path, encoding="utf-8-sig")
            df_b = pd.read_csv(res_b.oot_trades_path, encoding="utf-8-sig")
            # cfg A 用真实 manifest（black|day 1.5bp + 3bp = 4.5bp），cfg B 用全局 3bp
            cost_a = set(round(float(v), 6) for v in df_a["cost_pct"].dropna().unique())
            cost_b = set(round(float(v), 6) for v in df_b["cost_pct"].dropna().unique())
            self.assertNotEqual(cost_a, cost_b, f"cost should differ: A={cost_a} B={cost_b}")


class TestRunOotEvalBatch(unittest.TestCase):
    def test_batch_aggregates_multi_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_train_run(root, pool_name="GRP_CLUSTER_BLACK", interval="day")
            _make_fake_train_run(root, pool_name="GRP_CLUSTER_INDEX", interval="day")
            out_root = root / "out"
            bundle_dir = run_oot_eval_batch(
                from_root=root,
                pattern="*_model_pipeline",
                output_root=out_root,
                cfg=OotEvaluationConfig(),
                run_tag="cluster_both",
            )
            self.assertTrue(bundle_dir.exists())
            # bundle 应当含 raw/all_trade_details.csv 或等价聚合
            agg_files = list(bundle_dir.rglob("*all_trade_details*.csv"))
            self.assertTrue(len(agg_files) > 0, "expected aggregated trade details csv")

    def test_batch_dumps_cfg_fingerprint_to_meta(self) -> None:
        """防止 v_acb 那样的"命名失真"：meta/ 必须含 cfg fingerprint。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_train_run(root, pool_name="GRP_CLUSTER_BLACK", interval="day")
            _make_fake_train_run(root, pool_name="GRP_CLUSTER_INDEX", interval="day")
            out_root = root / "out"
            bundle_dir = run_oot_eval_batch(
                from_root=root,
                pattern="*_model_pipeline",
                output_root=out_root,
                cfg=OotEvaluationConfig(),
                run_tag="cluster_both",
            )
            cfg_files = list(bundle_dir.rglob("cfg_fingerprint.json"))
            self.assertTrue(len(cfg_files) >= 1, f"meta/cfg_fingerprint.json missing: bundle={bundle_dir}")
            data = json.loads(cfg_files[0].read_text())
            # 关键字段必须 dump 出来，便于 grep
            self.assertIn("commission_pct_by_cluster_interval", data)
            self.assertIn("slippage_pct_by_cluster_interval", data)
            self.assertIn("commission_pct_by_symbol", data)
            self.assertIn("use_impact_cost", data)
            self.assertIn("use_liquidity_floor_guard", data)
            self.assertIn("trade_filter_percentile_threshold_by_cluster_interval", data)
            self.assertIn("trade_filter_percentile_threshold_delta_by_signal_type", data)
            self.assertIn("signal_type_size_multiplier", data)
            self.assertIn("signal_type_max_concurrent_positions", data)
            self.assertIn("max_position_scale", data)
            self.assertIn("portfolio_logic", data)

    def test_batch_surfaces_note_in_fingerprint_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_train_run(root, pool_name="GRP_CLUSTER_BLACK", interval="day")
            out_root = root / "out"
            bundle_dir = run_oot_eval_batch(
                from_root=root,
                pattern="*_model_pipeline",
                output_root=out_root,
                cfg=OotEvaluationConfig(use_impact_cost=True, impact_cost_k=0.10),
                run_tag="cluster_both_quality",
                argv=["--note", "strict quality check"],
                note="strict quality check",
            )
            cfg_files = list(bundle_dir.rglob("cfg_fingerprint.json"))
            self.assertTrue(cfg_files)
            data = json.loads(cfg_files[0].read_text())
            self.assertEqual(data.get("note"), "strict quality check")
            summary = (bundle_dir / "00_overview" / "executive_summary.md").read_text(encoding="utf-8")
            self.assertIn("## 复现信息", summary)
            self.assertIn("strict quality check", summary)
            self.assertIn("use_impact_cost=True", summary)

    def test_batch_writes_cfg_drift_report_when_train_fingerprint_differs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            train_dir = _make_fake_train_run(root, pool_name="GRP_CLUSTER_BLACK", interval="day")
            meta_dir = train_dir / "meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "cfg_fingerprint.json").write_text(
                json.dumps(
                    {
                        "use_portfolio_logic_runtime": False,
                        "use_impact_cost": False,
                        "impact_cost_k": 0.0,
                    }
                ),
                encoding="utf-8",
            )
            out_root = root / "out"
            bundle_dir = run_oot_eval_batch(
                from_root=root,
                pattern="*_model_pipeline",
                output_root=out_root,
                cfg=OotEvaluationConfig(use_portfolio_logic_runtime=True, use_impact_cost=True, impact_cost_k=0.10),
                run_tag="cluster_both_quality",
            )

            drift_path = bundle_dir / "meta" / "cfg_drift_report.json"
            self.assertTrue(drift_path.exists())
            data = json.loads(drift_path.read_text(encoding="utf-8"))
            mismatch_fields = {row["field"] for row in data.get("mismatches", [])}
            self.assertIn("use_portfolio_logic_runtime", mismatch_fields)
            self.assertIn("use_impact_cost", mismatch_fields)

    def test_batch_cfg_fingerprint_contains_risk_system(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_fake_train_run(root, pool_name="GRP_CLUSTER_BLACK", interval="day")
            out_root = root / "out"
            bundle_dir = run_oot_eval_batch(
                from_root=root,
                pattern="*_model_pipeline",
                output_root=out_root,
                cfg=OotEvaluationConfig(
                    risk_system=RiskSystemConfig(
                        enable_quantile_threshold=False,
                        enable_dynamic_bump=False,
                    )
                ),
                run_tag="cluster_both",
            )
            cfg_files = list(bundle_dir.rglob("cfg_fingerprint.json"))
            self.assertTrue(cfg_files)
            data = json.loads(cfg_files[0].read_text())
            self.assertIn("risk_system", data)
            self.assertEqual(
                bool(data["risk_system"]["enable_quantile_threshold"]),
                False,
            )

    def test_batch_shares_symbol_caps_across_intervals_in_same_pool(self) -> None:
        """同一 pool 下多 interval 合并回放，symbol cap 应跨 interval 共用。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_single_trade_train_run(
                root,
                run_date="20260523",
                pool_name="GRP_CLUSTER_INDEX",
                interval="day",
                symbol="IF0",
                signal_type="bull_pullback_continuation",
            )
            _make_single_trade_train_run(
                root,
                run_date="20260523",
                pool_name="GRP_CLUSTER_INDEX",
                interval="minute60",
                symbol="IF0",
                signal_type="bull_pullback_continuation",
            )
            cfg = OotEvaluationConfig(
                use_trade_filter_gate=False,
                use_stacking_gate=False,
                use_regime_gate=False,
                use_mfe_mae_gate=False,
                use_intrabar_stop_tracking=False,
                use_position_sizing=False,
                use_portfolio_constraints=True,
                signal_type_size_multiplier={},
                signal_type_max_concurrent_positions={},
                signal_type_max_notional_pct={},
                trade_filter_percentile_threshold_delta_by_signal_type={},
                trade_filter_raw_threshold_delta_by_signal_type={},
                initial_capital=1_000.0,
                max_position_scale=0.10,
                max_symbol_notional_pct=0.10,
                max_concurrent_positions_per_symbol=10,
                max_concurrent_positions_total=20,
                margin_rate=0.10,
                max_total_leverage=10.0,
                max_daily_new_notional_pct=10.0,
                weekly_max_drawdown_pct=1.0,
                enforce_weekly_dd_budget_on_entry=False,
                block_new_entries_on_weekly_dd_breach=False,
                use_portfolio_logic_runtime=True,
                portfolio_logic=PortfolioLogicConfig(
                    enable_htf_gate=False,
                    enable_ranker=False,
                    enable_risk_throttle=False,
                    enable_pyramid=False,
                    caps=CapsConfig(
                        max_total_positions=20,
                        max_per_symbol=10,
                        max_total_per_cluster=20,
                        max_symbol_notional_pct=0.10,
                        max_cluster_notional_pct=1.50,
                        max_total_notional_pct=2.0,
                    ),
                ),
                signal_type_blacklist=(),
            )
            out_root = root / "out"
            bundle_dir = run_oot_eval_batch(
                from_root=root,
                pattern="*_model_pipeline",
                output_root=out_root,
                cfg=cfg,
                run_tag="cluster_both",
            )
            all_trade_details = bundle_dir / "raw" / "all_trade_details.csv"
            self.assertTrue(all_trade_details.exists())
            trades = pd.read_csv(all_trade_details, encoding="utf-8-sig")
            statuses = trades["execution_status"].astype(str).tolist()
            self.assertIn("executed", statuses)
            self.assertIn("blocked_symbol_cap", statuses)
            # 两个 interval 都参与了同一次回放（而非各自独立资金池）
            self.assertEqual(set(trades["interval"].astype(str)), {"day", "minute60"})

def _make_scored_single_trade_run(
    root: Path,
    *,
    pool_name: str,
    symbol: str,
    exchange: str,
    score: float,
    run_date: str = "20260523",
    interval: str = "day",
    side: str = "both",
    signal_type: str = "bull_pullback_continuation",
    trade_time: str = "2024-01-02 09:30:00",
    exit_time: str = "2024-01-10 15:00:00",
) -> Path:
    """单候选 train run，带可控 final_decision_score（用于跨 cluster 竞争测试）。"""
    out_dir = root / f"{run_date}_{pool_name}_{interval}_{side}_model_pipeline"
    out_dir.mkdir(parents=True, exist_ok=True)
    dt = pd.Timestamp(trade_time)
    ex = pd.Timestamp(exit_time)
    df = pd.DataFrame(
        {
            "datetime": [dt],
            "entry_datetime": [dt],
            "signal_datetime": [dt],
            "exit_datetime": [ex],
            "symbol": [symbol],
            "exchange": [exchange],
            "interval": [interval],
            "signal_type": [signal_type],
            "side": ["long"],
            "entry_action": ["buy"],
            "exit_action": ["sell"],
            "pred_split": ["test"],
            "window_id": [0],
            "is_executed": [1],
            "entry_price": [100.0],
            "exit_price_ref": [110.0],
            "trigger": [100.0],
            "stop_price": [95.0],
            # ranker.score 由 trade_filter_prob_pctl + pred_mfe/mae 驱动（不是 final_decision_score），
            # 把这些字段绑定到 score 参数，让高分候选在跨 cluster 竞争中确定性胜出。
            "trade_filter_prob": [float(score)],
            "trade_filter_prob_pctl": [float(score) * 100.0],
            "final_decision_score": [float(score)],
            "final_decision_model_kind": ["stack_ensemble"],
            "pred_mfe_atr": [1.0],
            "pred_mae_atr": [0.2],
            "future_mfe_atr": [1.0],
            "future_mae_atr": [0.2],
            "future_pnl_atr": [1.0],
        }
    )
    prefix = f"{run_date}_{pool_name}_{interval}_{side}"
    df.to_csv(out_dir / f"{prefix}_predictions.csv", index=False, encoding="utf-8-sig")
    (out_dir / f"{prefix}_model_report.md").write_text(
        f"# report\n- pool: {pool_name}\n- interval: {interval}\n", encoding="utf-8"
    )
    return out_dir


def _unified_competition_cfg(total_notional_pct: float) -> OotEvaluationConfig:
    """两簇同 bar 竞争用 cfg：单仓 ~100% equity，total cap 由参数控制。"""
    return OotEvaluationConfig(
        use_trade_filter_gate=False,
        use_stacking_gate=False,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_intrabar_stop_tracking=False,
        use_position_sizing=False,
        use_portfolio_constraints=True,
        signal_type_size_multiplier={},
        signal_type_max_concurrent_positions={},
        signal_type_max_notional_pct={},
        trade_filter_percentile_threshold_delta_by_signal_type={},
        trade_filter_raw_threshold_delta_by_signal_type={},
        initial_capital=1_000.0,
        max_position_scale=1.0,
        max_symbol_notional_pct=float(total_notional_pct),
        max_concurrent_positions_per_symbol=10,
        max_concurrent_positions_total=20,
        margin_rate=0.10,
        max_total_leverage=10.0,
        max_daily_new_notional_pct=10.0,
        weekly_max_drawdown_pct=1.0,
        enforce_weekly_dd_budget_on_entry=False,
        block_new_entries_on_weekly_dd_breach=False,
        use_portfolio_logic_runtime=True,
        portfolio_logic=PortfolioLogicConfig(
            enable_htf_gate=False,
            enable_ranker=True,
            enable_risk_throttle=False,
            enable_pyramid=False,
            caps=CapsConfig(
                max_total_positions=20,
                max_per_symbol=10,
                max_total_per_cluster=20,
                # symbol/cluster 设成 = total，保证只有 total cap 绑定（且满足 cluster<=total 校验）
                max_symbol_notional_pct=float(total_notional_pct),
                max_cluster_notional_pct=float(total_notional_pct),
                max_total_notional_pct=float(total_notional_pct),
            ),
        ),
        signal_type_blacklist=(),
    )


class TestUnifiedPortfolioEval(unittest.TestCase):
    """统一组合（2026-06-01）：跨 cluster/symbol 在同一 bar 竞争同一 150% 总额。"""

    def _two_cluster_root(self, root: Path) -> None:
        # 同一根 bar：INDEX/IF0 高分(0.9) vs BLACK/RB0 低分(0.5)，各自单仓 ~100% equity。
        _make_scored_single_trade_run(
            root, pool_name="GRP_CLUSTER_INDEX", symbol="IF0", exchange="CFFEX", score=0.9
        )
        _make_scored_single_trade_run(
            root, pool_name="GRP_CLUSTER_BLACK", symbol="RB0", exchange="SHFE", score=0.5
        )

    def test_unified_total_notional_cap_blocks_lower_score_cross_cluster(self) -> None:
        """统一模式：total cap=1.0 equity 只容得下 1 仓 → 高分 IF0 成交、低分 RB0 被全局总额挡。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._two_cluster_root(root)
            bundle_dir = run_oot_eval_batch(
                from_root=root,
                pattern="*_model_pipeline",
                output_root=root / "out",
                cfg=_unified_competition_cfg(total_notional_pct=1.0),
                run_tag="unified_test",
                unified_portfolio=True,
            )
            trades = pd.read_csv(bundle_dir / "raw" / "all_trade_details.csv", encoding="utf-8-sig")
            # 两簇都在统一回放里出现（group_name 按 symbol cluster 重切）
            self.assertEqual(
                set(trades["group_name"].astype(str)), {"cluster_index", "cluster_black"}
            )
            executed = trades.loc[trades["execution_status"].astype(str) == "executed"]
            # 全局 total notional cap 只容得下高分那一仓
            self.assertEqual(len(executed), 1)
            self.assertEqual(executed.iloc[0]["symbol"], "IF0")
            # 低分 RB0 没成交（被跨 cluster 全局竞争挤掉）
            rb = trades.loc[trades["symbol"].astype(str) == "RB0"]
            self.assertTrue((rb["execution_status"].astype(str) != "executed").all())

    def test_per_cluster_mode_lets_both_execute(self) -> None:
        """对照：per-cluster 模式下两簇各自独占 1.0 总额 → 都成交（证明差异确实来自统一池）。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._two_cluster_root(root)
            bundle_dir = run_oot_eval_batch(
                from_root=root,
                pattern="*_model_pipeline",
                output_root=root / "out",
                cfg=_unified_competition_cfg(total_notional_pct=1.0),
                run_tag="per_cluster_test",
                unified_portfolio=False,
            )
            trades = pd.read_csv(bundle_dir / "raw" / "all_trade_details.csv", encoding="utf-8-sig")
            executed = trades.loc[trades["execution_status"].astype(str) == "executed"]
            self.assertEqual(
                set(executed["symbol"].astype(str)), {"IF0", "RB0"},
                "per-cluster 模式两簇应各自成交（互不竞争）",
            )

    def test_cli_defaults_to_unified(self) -> None:
        """CLI 默认统一组合（--per-cluster-portfolio 不传 → unified）。"""
        args = _parse_args(["--from-root", "x", "--output-root", "y"])
        self.assertFalse(bool(args.per_cluster_portfolio))


class TestEvalOnlyCliRiskGuardFlags(unittest.TestCase):
    def test_cfg_json_accepts_portfolio_logic_trailing_alias(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            patch_path = Path(td) / "cfg.json"
            patch_path.write_text(
                json.dumps(
                    {
                        "portfolio_logic": {
                            "trailing": {
                                "interval_params": {
                                    "day": {
                                        "atr_multiplier": 3.2,
                                        "activation_profit_atr": 0.8,
                                        "fallback_hard_stop_pct": 0.04,
                                        "breakeven_profit_atr": 0.8,
                                        "breakeven_lock_atr": 0.05,
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = _parse_args(
                [
                    "--from-root",
                    "cta/backtest",
                    "--output-root",
                    "cta/backtest/out",
                    "--cfg-json",
                    str(patch_path),
                ]
            )
            cfg = _build_cfg_from_args(args)
        params = cfg.portfolio_logic.trailing.interval_params["day"]
        self.assertAlmostEqual(float(params.activation_profit_atr), 0.8, places=12)
        self.assertAlmostEqual(float(params.breakeven_lock_atr), 0.05, places=12)

    def test_parse_oot_guard_chain_flags_into_cfg(self) -> None:
        args = _parse_args(
            [
                "--from-root",
                "cta/backtest",
                "--pattern",
                "*_model_pipeline",
                "--output-root",
                "cta/backtest",
                "--enable-oot-guard-chain",
                "--enable-oot-score-drift-guard",
                "--score-drift-train-path",
                "cta/model/manifests/score_distribution_train_latest.json",
            ]
        )
        cfg = _build_cfg_from_args(args)
        self.assertTrue(bool(cfg.use_oot_guard_chain))
        self.assertTrue(bool(cfg.use_oot_score_distribution_guard))
        self.assertEqual(
            str(cfg.oot_score_distribution_guard.train_distribution_path),
            "cta/model/manifests/score_distribution_train_latest.json",
        )


if __name__ == "__main__":
    unittest.main()
