"""Group-aware model filter tests."""
from __future__ import annotations

import pickle
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from cta.live.model_filter import make_group_trade_filter


class _FakeModel:
    def __init__(self, prob: float) -> None:
        self.prob = float(prob)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        n = len(X)
        return np.column_stack([np.full(n, 1.0 - self.prob), np.full(n, self.prob)])


def _dump(model: Any, path: Path) -> None:
    with open(path, "wb") as f:
        pickle.dump(model, f)


def _adapter_with_frame(frame: pd.DataFrame, vt_symbol: str) -> Any:
    logs: list[str] = []
    a = SimpleNamespace(_frame=frame, vt_symbol=vt_symbol, write_log=logs.append)
    a._logs = logs  # type: ignore[attr-defined]
    return a


class TestMakeGroupTradeFilter(unittest.TestCase):
    def test_routes_to_symbol_group_model(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_group_filter_") as td:
            root = Path(td)
            m_a = root / "model_a.pkl"
            m_b = root / "model_b.pkl"
            c_a = root / "model_a_features.csv"
            c_b = root / "model_b_features.csv"
            _dump(_FakeModel(prob=0.9), m_a)
            _dump(_FakeModel(prob=0.1), m_b)
            pd.DataFrame({"name": ["f1"]}).to_csv(c_a, index=False)
            pd.DataFrame({"name": ["f1"]}).to_csv(c_b, index=False)

            f = make_group_trade_filter(
                group_model_paths={"tier_a": str(m_a), "tier_b": str(m_b)},
                symbol_to_group={"RB0": "tier_a", "AU0": "tier_b"},
                feature_columns_csv_by_group={"tier_a": str(c_a), "tier_b": str(c_b)},
                threshold=0.5,
            )

            rb_adapter = _adapter_with_frame(pd.DataFrame([{"f1": 1.0}]), "RB0.SHFE")
            au_adapter = _adapter_with_frame(pd.DataFrame([{"f1": 1.0}]), "AU0.SHFE")
            self.assertTrue(f({"side": "long", "vt_symbol": "RB0.SHFE"}, rb_adapter))
            self.assertFalse(f({"side": "long", "vt_symbol": "AU0.SHFE"}, au_adapter))

    def test_unknown_symbol_falls_back_to_allow(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_group_filter_unknown_") as td:
            root = Path(td)
            m_a = root / "model_a.pkl"
            c_a = root / "model_a_features.csv"
            _dump(_FakeModel(prob=0.1), m_a)
            pd.DataFrame({"name": ["f1"]}).to_csv(c_a, index=False)
            f = make_group_trade_filter(
                group_model_paths={"tier_a": str(m_a)},
                symbol_to_group={"RB0": "tier_a"},
                feature_columns_csv_by_group={"tier_a": str(c_a)},
                threshold=0.5,
            )
            ad = _adapter_with_frame(pd.DataFrame([{"f1": 1.0}]), "CU0.SHFE")
            self.assertTrue(f({"side": "long", "vt_symbol": "CU0.SHFE"}, ad))


if __name__ == "__main__":
    unittest.main()

