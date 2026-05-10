"""cta.live.online_feature 单测。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from cta.live.online_feature import OnlineFeatureLoader


def _make_minute_parquet(root: Path, interval: str, prefix: str,
                        date: str, datetimes: list[str], features: dict[str, list]) -> Path:
    df = pd.DataFrame({"datetime": pd.to_datetime(datetimes)})
    for k, v in features.items():
        df[k] = v
    out = root / interval / prefix / f"{date}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return out


def _make_day_parquet(root: Path, symbol: str, datetimes: list[str],
                     features: dict[str, list]) -> Path:
    df = pd.DataFrame({"datetime": pd.to_datetime(datetimes)})
    for k, v in features.items():
        df[k] = v
    out = root / "day" / f"{symbol}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return out


class TestOnlineFeatureLoader(unittest.TestCase):
    def test_loads_minute_parquet_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_minute_parquet(
                root, "minute60", "RB", "2024-01-02",
                ["2024-01-02 09:00", "2024-01-02 10:00", "2024-01-02 11:00"],
                {"feat_a": [1.0, 2.0, 3.0], "feat_b": [10.0, 20.0, 30.0]},
            )
            loader = OnlineFeatureLoader(feature_root=str(root))
            row = loader.load_at(symbol="RB0", interval="minute60",
                                dt=datetime(2024, 1, 2, 10, 30))
            self.assertIsNotNone(row)
            # 取 ≤ 10:30 的最近一行 = 10:00
            self.assertEqual(row["feat_a"], 2.0)
            self.assertEqual(row["feat_b"], 20.0)

    def test_loads_day_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_day_parquet(
                root, "RB0",
                ["2024-01-02", "2024-01-03", "2024-01-04"],
                {"feat_a": [1.0, 2.0, 3.0]},
            )
            loader = OnlineFeatureLoader(feature_root=str(root))
            row = loader.load_at(symbol="RB0", interval="day",
                                dt=datetime(2024, 1, 3, 23, 59))
            self.assertIsNotNone(row)
            self.assertEqual(row["feat_a"], 2.0)

    def test_returns_none_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loader = OnlineFeatureLoader(feature_root=str(tmp))
            row = loader.load_at(symbol="ZZ0", interval="minute60",
                                dt=datetime(2024, 1, 2, 10, 0))
            self.assertIsNone(row)

    def test_returns_none_when_dt_before_first_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_minute_parquet(
                root, "minute60", "RB", "2024-01-02",
                ["2024-01-02 14:00"], {"f": [1.0]},
            )
            loader = OnlineFeatureLoader(feature_root=str(root))
            # 请求 09:00 早于第一行 14:00 → 返回 None
            row = loader.load_at(symbol="RB0", interval="minute60",
                                dt=datetime(2024, 1, 2, 9, 0))
            self.assertIsNone(row)

    def test_caches_recent_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_minute_parquet(
                root, "minute60", "RB", "2024-01-02",
                ["2024-01-02 09:00"], {"f": [1.0]},
            )
            loader = OnlineFeatureLoader(feature_root=str(root), cache_size=2)
            for _ in range(5):
                loader.load_at(symbol="RB0", interval="minute60",
                              dt=datetime(2024, 1, 2, 9, 30))
            # 缓存应包含该路径
            self.assertEqual(len(loader._cache), 1)


class TestOnlineFeatureLoaderAsProvider(unittest.TestCase):
    """与 cta.live.model_filter.make_trade_filter 的 feature_provider 集成。"""

    def test_provider_call_returns_dataframe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_minute_parquet(
                root, "minute60", "RB", "2024-01-02",
                ["2024-01-02 09:00", "2024-01-02 10:00"],
                {"feat_a": [1.0, 2.0], "feat_b": [10.0, 20.0]},
            )
            loader = OnlineFeatureLoader(feature_root=str(root))
            from types import SimpleNamespace
            adapter = SimpleNamespace(
                vt_symbol="RB0.SHFE",
                interval="minute60",
                _buffer=[SimpleNamespace(datetime=datetime(2024, 1, 2, 10, 30))],
            )
            df = loader(adapter, columns=["feat_a", "feat_b"])
            self.assertIsNotNone(df)
            self.assertEqual(df["feat_a"].iloc[0], 2.0)
            self.assertEqual(df["feat_b"].iloc[0], 20.0)


if __name__ == "__main__":
    unittest.main()
