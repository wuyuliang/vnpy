"""Tests for cluster_model_registry online inference routing.

Covers:
- from_registry_json: parse + symbol-to-group reverse index
- resolve_group / resolve_model_dir for trained and untrained symbols
- predict_proba: routes RB0 to cluster_black model, CU0 to cluster_metal model
- predict_proba: graceful skip when model files absent
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from cta.model.cluster_model_registry import ClusterModelRegistry, _cluster_group_key
from cta.model.trade_filter_model import TradeFilterModel
from cta.portfolio_logic.score_calibrator import CalibrationStats, ScoreCalibrator


def _train_toy_trade_filter() -> TradeFilterModel:
    """Train a minimal TradeFilterModel on synthetic 2-feature data."""
    rng = np.random.default_rng(42)
    n = 200
    df = pd.DataFrame(
        {
            "feature_x": rng.normal(size=n),
            "feature_y": rng.normal(size=n),
        }
    )
    df["label_class"] = (df["feature_x"] + 0.5 * df["feature_y"] > 0).astype(int)
    df["is_executed"] = 1
    m = TradeFilterModel(random_state=42).fit(
        df, feature_columns=["feature_x", "feature_y"], label_column="label_class"
    )
    return m


def _populate_model_dir(root: Path, signal_type: str = "donchian_breakout") -> None:
    model_dir = root / "models" / signal_type / "window_00"
    model_dir.mkdir(parents=True, exist_ok=True)
    model = _train_toy_trade_filter()
    model.save(model_dir / "trade_filter.joblib")
    pd.DataFrame({"feature": ["feature_x", "feature_y"], "importance": [1.0, 0.5]}).to_csv(
        model_dir / "trade_filter_features.csv", index=False, encoding="utf-8-sig"
    )


def _populate_trade_filter_calibration(root: Path, signal_type: str = "donchian_breakout") -> None:
    model_dir = root / "models" / signal_type / "window_00"
    model_dir.mkdir(parents=True, exist_ok=True)
    cal = ScoreCalibrator(
        {
            (
                "cluster_black",
                "60min",
                "trade_filter",
            ): CalibrationStats(
                cluster="cluster_black",
                interval="60min",
                model_kind="trade_filter",
                sample_count=101,
                train_window=("2020-01-01", "2020-12-31"),
                percentile_values=np.linspace(0.0, 1.0, 101),
            )
        }
    )
    cal.save(model_dir / "trade_filter_calibration.joblib")


def _make_registry_json(root: Path, group_dirs: dict[str, Path]) -> Path:
    """Write a cluster_registry.json mapping group_name → model_dir."""
    payload = {
        "run_tag": "test_run",
        "group_by": "cluster",
        "trade_side_mode": "both",
        "intervals": ["60min"],
        "entries": [
            {
                "interval": "60min",
                "group_name": grp,
                "pool_name": f"GRP_{grp.upper()}",
                "model_dir": str(d),
                "members": [{"symbol": sym, "exchange": "SHFE"} for sym in members],
            }
            for grp, (d, members) in group_dirs.items()
        ],
    }
    p = root / "cluster_registry.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


class TestClusterModelRegistry(unittest.TestCase):
    def test_cluster_group_key_basic(self) -> None:
        self.assertEqual(_cluster_group_key("RB0"), "cluster_black")
        self.assertEqual(_cluster_group_key("CU0"), "cluster_metal")
        self.assertEqual(_cluster_group_key("UNKNOWNX"), "cluster_other")

    def test_cluster_group_key_index_futures_all_route_to_index(self) -> None:
        """股指期货 4 个 symbol 全部路由到 cluster_index（同一个 group_pool 模型）。"""
        for sym in ("IF0", "IH0", "IC0", "IM0"):
            self.assertEqual(
                _cluster_group_key(sym), "cluster_index",
                f"{sym} should route to cluster_index but got {_cluster_group_key(sym)}",
            )

    def test_cluster_group_key_bond_futures_all_route_to_bond(self) -> None:
        for sym in ("T0", "TF0", "TS0"):
            self.assertEqual(
                _cluster_group_key(sym), "cluster_bond",
                f"{sym} should route to cluster_bond but got {_cluster_group_key(sym)}",
            )

    def test_from_registry_json_builds_reverse_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            black = root / "black_pool"
            metal = root / "metal_pool"
            black.mkdir()
            metal.mkdir()
            rj = _make_registry_json(
                root,
                {
                    "cluster_black": (black, ["RB0", "HC0"]),
                    "cluster_metal": (metal, ["CU0", "AL0"]),
                },
            )
            reg = ClusterModelRegistry.from_registry_json(rj)
            self.assertEqual(reg.resolve_group("RB0"), "cluster_black")
            self.assertEqual(reg.resolve_group("CU0"), "cluster_metal")
            self.assertEqual(reg.resolve_model_dir("RB0", "60min"), black)
            self.assertEqual(reg.resolve_model_dir("CU0", "60min"), metal)
            # 未训练 symbol：fallback 按 cluster 推
            self.assertEqual(reg.resolve_group("JM0"), "cluster_black")

    def test_resolve_model_dir_unknown_interval_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            black = root / "black_pool"
            black.mkdir()
            rj = _make_registry_json(
                root, {"cluster_black": (black, ["RB0"])}
            )
            reg = ClusterModelRegistry.from_registry_json(rj)
            # 60min 有；day 没训过
            self.assertEqual(reg.resolve_model_dir("RB0", "60min"), black)
            self.assertIsNone(reg.resolve_model_dir("RB0", "day"))

    def test_predict_proba_routes_to_correct_cluster_model(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            black = root / "black_pool"
            metal = root / "metal_pool"
            _populate_model_dir(black)
            _populate_model_dir(metal)
            rj = _make_registry_json(
                root,
                {
                    "cluster_black": (black, ["RB0"]),
                    "cluster_metal": (metal, ["CU0"]),
                },
            )
            reg = ClusterModelRegistry.from_registry_json(rj)
            df = pd.DataFrame(
                {
                    "symbol": ["RB0", "CU0", "RB0"],
                    "interval": ["60min", "60min", "60min"],
                    "signal_type": ["donchian_breakout"] * 3,
                    "feature_x": [0.5, -0.3, 1.5],
                    "feature_y": [0.1, 0.2, -0.4],
                }
            )
            out = reg.predict_proba(df, model_kind="trade_filter")
            self.assertIn("trade_filter_prob", out.columns)
            self.assertEqual(out.iloc[0]["cluster_used"], "cluster_black")
            self.assertEqual(out.iloc[1]["cluster_used"], "cluster_metal")
            self.assertEqual(out.iloc[2]["cluster_used"], "cluster_black")
            # 所有 prob 都应为有效概率
            probs = pd.to_numeric(out["trade_filter_prob"], errors="coerce")
            self.assertTrue(((probs >= 0) & (probs <= 1)).all())
            # 两行 RB0 路由到同一个 model_dir
            self.assertEqual(out.iloc[0]["model_dir_used"], out.iloc[2]["model_dir_used"])
            self.assertNotEqual(out.iloc[0]["model_dir_used"], out.iloc[1]["model_dir_used"])

    def test_predict_proba_returns_nan_when_signal_type_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            black = root / "black_pool"
            _populate_model_dir(black, signal_type="donchian_breakout")
            rj = _make_registry_json(
                root, {"cluster_black": (black, ["RB0"])}
            )
            reg = ClusterModelRegistry.from_registry_json(rj)
            # 用未训练的 signal_type，应当 graceful NaN（不抛异常）
            df = pd.DataFrame(
                {
                    "symbol": ["RB0"],
                    "interval": ["60min"],
                    "signal_type": ["never_trained_signal"],
                    "feature_x": [0.5],
                    "feature_y": [0.1],
                }
            )
            out = reg.predict_proba(df, model_kind="trade_filter")
            self.assertTrue(pd.isna(out.iloc[0]["trade_filter_prob"]))

    def test_predict_proba_outputs_percentile_when_calibration_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            black = root / "black_pool"
            _populate_model_dir(black, signal_type="donchian_breakout")
            _populate_trade_filter_calibration(black, signal_type="donchian_breakout")
            rj = _make_registry_json(
                root, {"cluster_black": (black, ["RB0"])}
            )
            reg = ClusterModelRegistry.from_registry_json(rj)
            df = pd.DataFrame(
                {
                    "symbol": ["RB0"],
                    "interval": ["60min"],
                    "signal_type": ["donchian_breakout"],
                    "feature_x": [0.3],
                    "feature_y": [0.1],
                }
            )
            out = reg.predict_proba(df, model_kind="trade_filter")
            self.assertIn("trade_filter_prob_pctl", out.columns)
            val = pd.to_numeric(out["trade_filter_prob_pctl"], errors="coerce").iloc[0]
            self.assertTrue(np.isfinite(float(val)))
            self.assertGreaterEqual(float(val), 0.0)
            self.assertLessEqual(float(val), 100.0)

    def test_predict_proba_uses_default_interval_when_column_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            black = root / "black_pool"
            _populate_model_dir(black)
            rj = _make_registry_json(
                root, {"cluster_black": (black, ["RB0"])}
            )
            reg = ClusterModelRegistry.from_registry_json(rj)
            df = pd.DataFrame(
                {
                    "symbol": ["RB0"],
                    "signal_type": ["donchian_breakout"],
                    "feature_x": [0.5],
                    "feature_y": [0.1],
                }
            )
            out = reg.predict_proba(df, model_kind="trade_filter", default_interval="60min")
            self.assertEqual(out.iloc[0]["cluster_used"], "cluster_black")

    def test_from_registry_json_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = root / "cluster_registry.json"
            bad.write_text("{bad-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                ClusterModelRegistry.from_registry_json(bad)

    def test_from_registry_json_empty_entries_keeps_registry_usable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rj = root / "cluster_registry.json"
            rj.write_text(
                json.dumps(
                    {
                        "run_tag": "x",
                        "group_by": "cluster",
                        "trade_side_mode": "both",
                        "intervals": ["60min"],
                        "entries": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertLogs("cta.model.cluster_model_registry", level="ERROR") as cm:
                reg = ClusterModelRegistry.from_registry_json(rj)
            self.assertEqual(len(reg), 0)
            self.assertFalse(bool(reg))
            self.assertTrue(any("empty entries" in line for line in cm.output))
            df = pd.DataFrame({"symbol": ["RB0"], "interval": ["60min"], "signal_type": ["donchian_breakout"]})
            out = reg.predict_proba(df, model_kind="trade_filter")
            self.assertTrue(pd.isna(out["trade_filter_prob"]).all())

    def test_from_registry_json_missing_entries_field_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rj = root / "cluster_registry.json"
            rj.write_text(
                json.dumps(
                    {
                        "run_tag": "x",
                        "group_by": "cluster",
                        "trade_side_mode": "both",
                        "intervals": ["60min"],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertLogs("cta.model.cluster_model_registry", level="ERROR") as cm:
                reg = ClusterModelRegistry.from_registry_json(rj)
            self.assertEqual(len(reg), 0)
            self.assertTrue(any("empty entries" in line for line in cm.output))

    def test_resolve_group_warns_when_group_by_not_cluster(self) -> None:
        reg = ClusterModelRegistry(
            group_by="tier",
            trade_side_mode="both",
            entries={},
            symbol_to_group={},
        )
        with self.assertLogs("cta.model.cluster_model_registry", level="WARNING") as cm:
            grp = reg.resolve_group("RB0")
        self.assertEqual(grp, "")
        self.assertTrue(any("group_by=tier" in line for line in cm.output))

    def test_predict_proba_missing_model_dir_affects_only_that_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            black = root / "black_pool"
            _populate_model_dir(black)
            missing = root / "missing_pool"
            rj = _make_registry_json(
                root,
                {
                    "cluster_black": (black, ["RB0"]),
                    "cluster_metal": (missing, ["CU0"]),
                },
            )
            reg = ClusterModelRegistry.from_registry_json(rj)
            df = pd.DataFrame(
                {
                    "symbol": ["RB0", "CU0"],
                    "interval": ["60min", "60min"],
                    "signal_type": ["donchian_breakout", "donchian_breakout"],
                    "feature_x": [0.5, 0.1],
                    "feature_y": [0.2, -0.3],
                }
            )
            out = reg.predict_proba(df, model_kind="trade_filter")
            rb_prob = pd.to_numeric(out.loc[out["symbol"] == "RB0", "trade_filter_prob"], errors="coerce").iloc[0]
            cu_prob = pd.to_numeric(out.loc[out["symbol"] == "CU0", "trade_filter_prob"], errors="coerce").iloc[0]
            self.assertTrue(np.isfinite(rb_prob))
            self.assertTrue(np.isnan(cu_prob))


if __name__ == "__main__":
    unittest.main()
