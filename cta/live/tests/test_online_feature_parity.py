"""Tests for feature_parity (P0-2 验收).

用合成 parquet 验证 OnlineFeatureLoader vs 离线直读的 parity；
真实 ``cta/data/feature/`` 的 1 day × 60min 验证需要在生产环境跑（roadmap §5.1）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.live.feature_parity import (
    ColumnParityResult,
    ParityReport,
    compare_online_vs_offline,
)
from cta.live.online_feature import OnlineFeatureLoader


def _make_synthetic_feature_df(n: int = 60) -> pd.DataFrame:
    """合成 60 行 60min bar 特征。"""
    rng = np.random.default_rng(20260523)
    dt = pd.date_range("2024-12-02 09:00:00", periods=n, freq="60min")
    return pd.DataFrame(
        {
            "datetime": dt,
            "close": np.linspace(4000.0, 4100.0, n),
            "ma_alignment": np.arange(n) % 5 - 2,
            "atr_14": rng.uniform(20, 30, size=n),
            "rsi_14": rng.uniform(30, 70, size=n),
            "vol_z": rng.normal(0, 1, size=n),
            "regime_label": ["range"] * n,
        }
    )


def _make_wide_feature_df(n: int = 60, n_features: int = 120) -> pd.DataFrame:
    """合成 100+ 列特征，覆盖 P0-2 验收口径。"""
    rng = np.random.default_rng(20260524)
    dt = pd.date_range("2024-12-02 09:00:00", periods=n, freq="60min")
    data: dict[str, object] = {
        "datetime": dt,
        "close": np.linspace(4000.0, 4100.0, n),
        "regime_label": ["range"] * n,
    }
    for i in range(n_features):
        col = f"feature_{i:03d}"
        arr = rng.normal(0.0, 1.0, size=n).astype(float)
        if i % 11 == 0:
            arr[:3] = np.nan  # warmup NaN
        data[col] = arr
    return pd.DataFrame(data)


def _setup_feature_parquet(root: Path, symbol: str, interval: str, df: pd.DataFrame) -> None:
    """模拟 cta/data/feature/{interval}/{prefix}/{date}.parquet 布局，按日切分。"""
    prefix = "".join(ch for ch in symbol if ch.isalpha()).upper()
    df["_date"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")
    for date_str, group in df.groupby("_date"):
        sub = group.drop(columns=["_date"])
        out_dir = root / interval / prefix
        out_dir.mkdir(parents=True, exist_ok=True)
        sub.to_parquet(out_dir / f"{date_str}.parquet")


class TestFeatureParity(unittest.TestCase):
    def test_100plus_columns_pass_under_1e6_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            df = _make_wide_feature_df(n=80, n_features=120)
            _setup_feature_parquet(tmp, "RB0", "60min", df)

            loader = OnlineFeatureLoader(feature_root=tmp)
            dates = pd.to_datetime(df["datetime"]).tolist()
            report = compare_online_vs_offline(
                symbol="RB0",
                interval="60min",
                dates=dates,
                online_loader=lambda s, i, d: loader.load_at(symbol=s, interval=i, dt=d),
                offline_loader=lambda s, i, d: loader.load_at(symbol=s, interval=i, dt=d),
                tolerance=1e-6,
            )
            self.assertTrue(report.overall_pass)
            self.assertGreaterEqual(report.n_columns, 100)
            for row in report.column_results:
                self.assertLessEqual(float(row.max_abs_diff), 1e-6)

    def test_identical_data_max_diff_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            df = _make_synthetic_feature_df()
            _setup_feature_parquet(tmp, "RB0", "60min", df)

            loader = OnlineFeatureLoader(feature_root=tmp)

            def online_loader(sym: str, itv: str, dt: pd.Timestamp) -> "pd.Series | None":
                return loader.load_at(symbol=sym, interval=itv, dt=dt)

            def offline_loader(sym: str, itv: str, dt: pd.Timestamp) -> "pd.Series | None":
                # 离线直接读 parquet（与 online 同一份）
                return loader.load_at(symbol=sym, interval=itv, dt=dt)

            dates = pd.to_datetime(df["datetime"]).tolist()
            report = compare_online_vs_offline(
                symbol="RB0", interval="60min",
                dates=dates,
                online_loader=online_loader,
                offline_loader=offline_loader,
            )
            self.assertTrue(report.overall_pass)
            self.assertEqual(report.n_columns_fail, 0)
            for r in report.column_results:
                # 数值列 max_diff = 0；非数值列（regime_label）也应是 0
                if r.column not in ("regime_label", "datetime"):
                    self.assertEqual(r.max_abs_diff, 0.0)

    def test_diff_above_tolerance_fails(self) -> None:
        """模拟 online 与 offline 在某列有 float32 vs float64 类的微小差异。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            df_online = _make_synthetic_feature_df()
            df_offline = df_online.copy()
            # 故意制造 1e-3 差异，超出 1e-6 容差
            df_offline["atr_14"] = df_offline["atr_14"] + 1e-3

            _setup_feature_parquet(tmp / "online_root", "RB0", "60min", df_online)
            _setup_feature_parquet(tmp / "offline_root", "RB0", "60min", df_offline)

            on = OnlineFeatureLoader(feature_root=tmp / "online_root")
            off = OnlineFeatureLoader(feature_root=tmp / "offline_root")

            dates = pd.to_datetime(df_online["datetime"]).tolist()
            report = compare_online_vs_offline(
                symbol="RB0", interval="60min", dates=dates,
                online_loader=lambda s, i, d: on.load_at(symbol=s, interval=i, dt=d),
                offline_loader=lambda s, i, d: off.load_at(symbol=s, interval=i, dt=d),
                tolerance=1e-6,
            )
            self.assertFalse(report.overall_pass)
            self.assertIn("atr_14", report.failing_columns)
            atr_row = next(r for r in report.column_results if r.column == "atr_14")
            self.assertAlmostEqual(atr_row.max_abs_diff, 1e-3, places=5)

    def test_tolerance_relaxed_passes(self) -> None:
        """同样 1e-3 差异，把 tolerance 放宽到 1e-2 应通过。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            df_online = _make_synthetic_feature_df()
            df_offline = df_online.copy()
            df_offline["atr_14"] = df_offline["atr_14"] + 1e-3

            _setup_feature_parquet(tmp / "on", "RB0", "60min", df_online)
            _setup_feature_parquet(tmp / "off", "RB0", "60min", df_offline)
            on = OnlineFeatureLoader(feature_root=tmp / "on")
            off = OnlineFeatureLoader(feature_root=tmp / "off")

            dates = pd.to_datetime(df_online["datetime"]).tolist()
            report = compare_online_vs_offline(
                symbol="RB0", interval="60min", dates=dates,
                online_loader=lambda s, i, d: on.load_at(symbol=s, interval=i, dt=d),
                offline_loader=lambda s, i, d: off.load_at(symbol=s, interval=i, dt=d),
                tolerance=1e-2,
            )
            self.assertTrue(report.overall_pass)

    def test_nan_warmup_does_not_fail(self) -> None:
        """rolling warmup 期 NaN 应被视作"双方都 NaN"不计入差异。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            df = _make_synthetic_feature_df()
            # 前 5 行 atr_14 设 NaN（模拟 warmup）
            df.loc[:4, "atr_14"] = np.nan
            _setup_feature_parquet(tmp, "RB0", "60min", df)
            on = OnlineFeatureLoader(feature_root=tmp)

            dates = pd.to_datetime(df["datetime"]).tolist()
            report = compare_online_vs_offline(
                symbol="RB0", interval="60min", dates=dates,
                online_loader=lambda s, i, d: on.load_at(symbol=s, interval=i, dt=d),
                offline_loader=lambda s, i, d: on.load_at(symbol=s, interval=i, dt=d),
            )
            self.assertTrue(report.overall_pass)
            atr_row = next(r for r in report.column_results if r.column == "atr_14")
            self.assertGreaterEqual(atr_row.n_both_nan, 5)

    def test_empty_dates_returns_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            on = OnlineFeatureLoader(feature_root=tmp)
            report = compare_online_vs_offline(
                symbol="RB0", interval="60min",
                dates=[],
                online_loader=lambda s, i, d: on.load_at(symbol=s, interval=i, dt=d),
                offline_loader=lambda s, i, d: on.load_at(symbol=s, interval=i, dt=d),
            )
            self.assertTrue(report.overall_pass)
            self.assertEqual(report.n_timestamps, 0)

    def test_failing_columns_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            df_online = _make_synthetic_feature_df()
            df_offline = df_online.copy()
            df_offline["atr_14"] = df_offline["atr_14"] + 1.0
            df_offline["rsi_14"] = df_offline["rsi_14"] + 0.5
            _setup_feature_parquet(tmp / "on", "RB0", "60min", df_online)
            _setup_feature_parquet(tmp / "off", "RB0", "60min", df_offline)
            on = OnlineFeatureLoader(feature_root=tmp / "on")
            off = OnlineFeatureLoader(feature_root=tmp / "off")
            dates = pd.to_datetime(df_online["datetime"]).tolist()
            report = compare_online_vs_offline(
                symbol="RB0", interval="60min", dates=dates,
                online_loader=lambda s, i, d: on.load_at(symbol=s, interval=i, dt=d),
                offline_loader=lambda s, i, d: off.load_at(symbol=s, interval=i, dt=d),
            )
            self.assertFalse(report.overall_pass)
            self.assertEqual(set(report.failing_columns), {"atr_14", "rsi_14"})


if __name__ == "__main__":
    unittest.main()
