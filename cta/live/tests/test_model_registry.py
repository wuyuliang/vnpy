"""Tests for cta.live.model_registry."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.live.model_registry import LiveModelRegistry


class _FakeClusterRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def predict_proba(self, df: pd.DataFrame, *, model_kind: str, **_: object) -> pd.DataFrame:
        self.calls.append((model_kind, len(df)))
        out = df.copy()
        out[f"{model_kind}_prob"] = 0.77
        return out

    def resolve_model_dir(self, symbol: str, interval: str):  # noqa: ANN001
        if symbol.upper() == "RB0" and str(interval) == "day":
            return Path("/tmp/fake_model_dir")
        return None

    @property
    def entries(self):  # noqa: ANN201
        return {}


class _FakeHotRegistry:
    def __init__(self, current: _FakeClusterRegistry) -> None:
        self.current = current
        self.version = 1
        self.reload_calls = 0

    def reload(self):  # noqa: ANN201
        self.reload_calls += 1
        self.version += 1
        return True, object()


class TestLiveModelRegistry(unittest.TestCase):
    def test_from_registry_path_loads_and_resolves_model_dir(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_live_registry_") as td:
            root = Path(td)
            model_dir = root / "model_black_day"
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "provenance.json").write_text("{}", encoding="utf-8")
            reg_path = root / "cluster_registry.json"
            payload = {
                "run_tag": "20260530",
                "group_by": "cluster",
                "trade_side_mode": "both",
                "intervals": ["day"],
                "entries": [
                    {
                        "interval": "day",
                        "group_name": "cluster_black",
                        "pool_name": "GRP_CLUSTER_BLACK",
                        "model_dir": str(model_dir),
                        "members": [{"symbol": "RB0", "exchange": "SHFE"}],
                    }
                ],
            }
            reg_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            registry = LiveModelRegistry.from_registry_path(reg_path)
            resolved = registry.resolve_model_dir("RB0", "day")
            self.assertEqual(Path(resolved or ""), model_dir)

    def test_max_model_age_days_uses_artifact_mtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_live_registry_age_") as td:
            root = Path(td)
            model_dir = root / "model_black_day"
            model_dir.mkdir(parents=True, exist_ok=True)
            marker = model_dir / "trade_filter.joblib"
            marker.write_text("x", encoding="utf-8")
            # 固定 mtime: 2026-05-28 00:00:00
            marker_ts = pd.Timestamp("2026-05-28 00:00:00").timestamp()
            marker.chmod(0o644)
            import os
            os.utime(marker, (marker_ts, marker_ts))
            reg_path = root / "cluster_registry.json"
            payload = {
                "run_tag": "20260530",
                "group_by": "cluster",
                "trade_side_mode": "both",
                "intervals": ["day"],
                "entries": [
                    {
                        "interval": "day",
                        "group_name": "cluster_black",
                        "pool_name": "GRP_CLUSTER_BLACK",
                        "model_dir": str(model_dir),
                        "members": [{"symbol": "RB0", "exchange": "SHFE"}],
                    }
                ],
            }
            reg_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            registry = LiveModelRegistry.from_registry_path(reg_path)
            age = registry.max_model_age_days(as_of=pd.Timestamp("2026-05-30 00:00:00"))
            self.assertAlmostEqual(age, 2.0, places=5)

    def test_predict_delegates_to_cluster_registry(self) -> None:
        fake = _FakeClusterRegistry()
        hot = _FakeHotRegistry(fake)
        registry = LiveModelRegistry(hot_registry=hot)
        df = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["day"],
                "signal_type": ["donchian_breakout"],
            }
        )
        out = registry.predict(df, model_kind="trade_filter")
        self.assertIn("trade_filter_prob", out.columns)
        self.assertEqual(fake.calls, [("trade_filter", 1)])
        ok = registry.reload()
        self.assertTrue(ok)
        self.assertEqual(hot.reload_calls, 1)


if __name__ == "__main__":
    unittest.main()
