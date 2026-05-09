"""cta.live.model_filter 单测。"""
from __future__ import annotations

import pickle
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from cta.live.model_filter import make_trade_filter


class _FakeModel:
    """sklearn-style: predict_proba(X) returns Nx2 array."""

    def __init__(self, prob: float = 0.8) -> None:
        self.prob = float(prob)
        self.calls = 0

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        self.calls += 1
        n = len(X)
        return np.column_stack([np.full(n, 1.0 - self.prob), np.full(n, self.prob)])


def _dump(model: Any, path: Path) -> None:
    """简化：用 pickle 落盘（make_trade_filter 同时支持 joblib，但 pickle 也能加载）。"""
    with open(path, "wb") as f:
        pickle.dump(model, f)


def _adapter_with_frame(frame: pd.DataFrame) -> Any:
    """伪 adapter，只暴露 _frame 与 write_log。"""
    logs: list[str] = []
    a = SimpleNamespace(_frame=frame, write_log=logs.append)
    a._logs = logs  # type: ignore[attr-defined]
    return a


def _frame_with(features: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame([features])


class TestMakeTradeFilter(unittest.TestCase):
    def test_high_prob_allows_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            mp = tmp_p / "model.pkl"
            fc = tmp_p / "model_features.csv"
            _dump(_FakeModel(prob=0.9), mp)
            pd.DataFrame({"name": ["f1", "f2"]}).to_csv(fc, index=False)
            f = make_trade_filter(str(mp), feature_columns_csv=str(fc), threshold=0.5)
            adapter = _adapter_with_frame(_frame_with({"f1": 1.0, "f2": 2.0}))
            self.assertTrue(f({"side": "long"}, adapter))

    def test_low_prob_blocks_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            mp = tmp_p / "model.pkl"
            fc = tmp_p / "model_features.csv"
            _dump(_FakeModel(prob=0.2), mp)
            pd.DataFrame({"name": ["f1", "f2"]}).to_csv(fc, index=False)
            f = make_trade_filter(str(mp), feature_columns_csv=str(fc), threshold=0.5)
            adapter = _adapter_with_frame(_frame_with({"f1": 1.0, "f2": 2.0}))
            self.assertFalse(f({"side": "long"}, adapter))

    def test_close_orders_pass_without_model_check(self) -> None:
        """平仓不应被模型阻止（避免持仓被锁住无法离场）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            mp = tmp_p / "model.pkl"
            fc = tmp_p / "model_features.csv"
            _dump(_FakeModel(prob=0.0), mp)   # 任何开仓都被拒
            pd.DataFrame({"name": ["f1"]}).to_csv(fc, index=False)
            f = make_trade_filter(str(mp), feature_columns_csv=str(fc), threshold=0.5)
            adapter = _adapter_with_frame(_frame_with({"f1": 1.0}))
            self.assertTrue(f({"side": "flat"}, adapter))

    def test_no_frame_passes_with_warning(self) -> None:
        """adapter._frame 为空时不拦截（warmup 期间），并写日志。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            mp = tmp_p / "model.pkl"
            fc = tmp_p / "model_features.csv"
            _dump(_FakeModel(prob=0.0), mp)
            pd.DataFrame({"name": ["f1"]}).to_csv(fc, index=False)
            f = make_trade_filter(str(mp), feature_columns_csv=str(fc), threshold=0.5)
            adapter = _adapter_with_frame(pd.DataFrame())
            self.assertTrue(f({"side": "long"}, adapter))
            self.assertTrue(any("frame" in m.lower() for m in adapter._logs))

    def test_missing_feature_columns_passes_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            mp = tmp_p / "model.pkl"
            fc = tmp_p / "model_features.csv"
            _dump(_FakeModel(prob=0.0), mp)
            pd.DataFrame({"name": ["f1", "missing_f"]}).to_csv(fc, index=False)
            f = make_trade_filter(str(mp), feature_columns_csv=str(fc), threshold=0.5)
            adapter = _adapter_with_frame(_frame_with({"f1": 1.0}))
            self.assertTrue(f({"side": "long"}, adapter))
            self.assertTrue(any("missing" in m.lower() or "column" in m.lower()
                                for m in adapter._logs))

    def test_auto_locates_features_csv(self) -> None:
        """未传 feature_columns_csv 时，按 model 同名 _features.csv 自动加载。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            mp = tmp_p / "trade_filter_xyz.joblib"
            fc = tmp_p / "trade_filter_xyz_features.csv"
            _dump(_FakeModel(prob=0.9), mp)
            pd.DataFrame({"name": ["f1"]}).to_csv(fc, index=False)
            f = make_trade_filter(str(mp), threshold=0.5)
            adapter = _adapter_with_frame(_frame_with({"f1": 1.0}))
            self.assertTrue(f({"side": "long"}, adapter))


if __name__ == "__main__":
    unittest.main()
