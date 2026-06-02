"""分位数阈值（子系统①）测试。

覆盖：
1. happy path：精确 (cluster, symbol, interval) → 取对应 p70 字段
2. cluster-wide fallback：symbol 不在 manifest → 落到 (cluster, "*", interval)
3. miss fallback：cluster/interval 也没有 → 返回 base_threshold（fail-open）
4. NaN / 异常字段 → fail-open
5. manifest 从 JSON 读写双向无损
6. build_from_predictions 排除 test/oot split
7. emit_pctl=False 时返回 raw prob 而非 pctl
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.state.score_quantile_manifest import (
    ScoreQuantileEntry,
    ScoreQuantileManifest,
    WILDCARD_SYMBOL,
    build_from_predictions,
)
from cta.risk.threshold.quantile_threshold import QuantileThresholdAdjuster


def _entry(cluster="metal", symbol="CU0", interval="day",
           p50=0.50, p60=0.55, p70=0.60, p80=0.66, p90=0.74, p95=0.81,
           sample_count=100) -> ScoreQuantileEntry:
    return ScoreQuantileEntry(
        cluster=cluster, symbol=symbol, interval=interval,
        p50=p50, p60=p60, p70=p70, p80=p80, p90=p90, p95=p95,
        sample_count=sample_count,
    )


def _ctx(symbol="CU0", cluster="metal", interval="day",
         prob=0.65, prob_pctl=75.0) -> SignalContext:
    return SignalContext(
        candidate={
            "symbol": symbol, "cluster": cluster, "interval": interval,
            "trade_filter_prob": prob, "trade_filter_prob_pctl": prob_pctl,
            "side": "long", "signal_type": "donchian_breakout",
        },
        portfolio={"equity": 1_000_000.0},
        bar_dt=pd.Timestamp("2026-01-02 10:00:00"),
    )


class TestQuantileThreshold(unittest.TestCase):

    def test_exact_lookup_returns_field_pctl(self) -> None:
        manifest = ScoreQuantileManifest([_entry()])
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p70")
        out = adj.resolve(_ctx(), base_threshold=70.0)
        self.assertEqual(out, 70.0)

    def test_quantile_field_p80(self) -> None:
        manifest = ScoreQuantileManifest([_entry()])
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p80")
        self.assertEqual(adj.resolve(_ctx(), base_threshold=70.0), 80.0)

    def test_cluster_wide_fallback(self) -> None:
        wide = _entry(symbol=WILDCARD_SYMBOL)
        manifest = ScoreQuantileManifest([wide])
        # 候选 symbol="XX0" 没有精确条目 → 命中 cluster-wide
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p70")
        out = adj.resolve(_ctx(symbol="XX0"), base_threshold=70.0)
        self.assertEqual(out, 70.0)

    def test_complete_miss_falls_back_to_base(self) -> None:
        manifest = ScoreQuantileManifest([])
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p70")
        out = adj.resolve(_ctx(), base_threshold=80.0)
        self.assertEqual(out, 80.0)

    def test_nan_quantile_field_fails_open(self) -> None:
        # 用极端值确保解析不报错
        entry = _entry(p70=float("nan"))
        manifest = ScoreQuantileManifest([entry])
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p70")
        # raw_q == NaN → 返回 base
        out = adj.resolve(_ctx(), base_threshold=65.0)
        self.assertEqual(out, 65.0)

    def test_json_roundtrip(self) -> None:
        manifest = ScoreQuantileManifest(
            [_entry(), _entry(symbol=WILDCARD_SYMBOL)],
            meta={"run_tag": "test"},
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "manifest.json"
            manifest.to_json(p)
            loaded = ScoreQuantileManifest.from_json(p)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded.meta.get("run_tag"), "test")
        e = loaded.lookup("metal", "CU0", "day")
        self.assertIsNotNone(e)
        self.assertAlmostEqual(e.p70, 0.60, places=6)

    def test_build_from_predictions_excludes_test_split(self) -> None:
        df = pd.DataFrame({
            "symbol": ["CU0"] * 100 + ["CU0"] * 100,
            "cluster_name": ["metal"] * 200,
            "interval": ["day"] * 200,
            "split": ["train"] * 100 + ["test"] * 100,
            "trade_filter_prob": list(range(100)) + list(range(1000, 1100)),  # 训练分布 != 测试分布
        })
        df["trade_filter_prob"] = df["trade_filter_prob"].astype(float) / 100.0
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "pred.csv"
            df.to_csv(csv, index=False)
            manifest = build_from_predictions(
                [csv],
                exclude_splits=("test", "oot"),
                include_cluster_wide=False,
            )
        e = manifest.lookup("metal", "CU0", "day")
        self.assertIsNotNone(e)
        # 训练 split 的 p70 应在 [0, 1] 之间，远小于 test split 的 [10, 11]
        self.assertLess(e.p70, 1.0)

    def test_emit_pctl_false_returns_raw_prob(self) -> None:
        manifest = ScoreQuantileManifest([_entry(p70=0.60)])
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p70", emit_pctl=False)
        out = adj.resolve(_ctx(), base_threshold=70.0)
        self.assertAlmostEqual(out, 0.60, places=6)

    def test_emit_pctl_does_not_lower_stricter_base_threshold(self) -> None:
        """emit_pctl=True 命中 manifest 时也不能把更严格的 base 往下拉。"""
        manifest = ScoreQuantileManifest([_entry(p70=0.60)])
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p70", emit_pctl=True)
        out = adj.resolve(_ctx(), base_threshold=80.0)
        self.assertEqual(out, 80.0)

    def test_emit_pctl_uses_symbol_raw_quantile_via_cluster_wide_distribution(self) -> None:
        """同一 cluster 内不同 symbol 的 raw_q 应映射成不同 pctl 阈值。"""
        cluster_wide = _entry(symbol=WILDCARD_SYMBOL, p50=0.40, p60=0.50, p70=0.60, p80=0.70, p90=0.80, p95=0.90)
        cu = _entry(symbol="CU0", p70=0.50)
        al = _entry(symbol="AL0", p70=0.80)
        manifest = ScoreQuantileManifest([cu, al, cluster_wide])
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p70", emit_pctl=True)

        self.assertEqual(adj.resolve(_ctx(symbol="CU0"), base_threshold=50.0), 60.0)
        self.assertEqual(adj.resolve(_ctx(symbol="AL0"), base_threshold=50.0), 90.0)

    def test_normalize_keys_case_insensitive(self) -> None:
        manifest = ScoreQuantileManifest([_entry(cluster="metal", symbol="cu0", interval="DAY")])
        adj = QuantileThresholdAdjuster(manifest, quantile_field="p70")
        # candidate 上大小写不同也能命中
        out = adj.resolve(_ctx(symbol="CU0", cluster="METAL", interval="day"), base_threshold=70.0)
        self.assertEqual(out, 70.0)

    def test_loader_fails_open_on_missing_file(self) -> None:
        manifest = ScoreQuantileManifest.from_json("/nonexistent/missing.json")
        self.assertEqual(len(manifest), 0)

    def test_loader_fails_open_on_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{not valid json", encoding="utf-8")
            manifest = ScoreQuantileManifest.from_json(p)
        self.assertEqual(len(manifest), 0)


if __name__ == "__main__":
    unittest.main()
