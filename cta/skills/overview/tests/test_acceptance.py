"""acceptance.py 单元测试."""
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd
import yaml

from cta.skills.overview.acceptance import (
    REQUIRED_COLUMNS,
    assert_passes_gate_a,
    assert_passes_gate_b,
    assess_against_gates,
    load_acceptance_config,
)


def _good_row_a() -> dict:
    # 过 gate A 的样本
    return {
        "symbol": "RB0", "interval": "day",
        "total_return": 1.2, "annual_return": 0.25,
        "max_drawdown": 0.12, "sharpe": 1.5, "calmar": 1.5,
        "win_rate": 0.45, "profit_factor": 1.6, "avg_holding_bars": 40,
        "trade_count": 120, "turnover": 30,
    }


def _bad_row_a() -> dict:
    r = _good_row_a()
    r["annual_return"] = 0.05        # 低于 gate A 的 20%
    r["max_drawdown"] = 0.25         # 高于 15%
    return r


def _good_row_b() -> dict:
    return {
        "symbol": "RB0", "interval": "minute5",
        "total_return": 1.3, "annual_return": 0.30,
        "max_drawdown": 0.08, "sharpe": 1.8, "calmar": 3.75,
        "win_rate": 0.50, "profit_factor": 1.5, "avg_holding_bars": 20,
        "trade_count": 500, "turnover": 80,
    }


class TestAcceptanceConfig(unittest.TestCase):
    def test_load_default(self) -> None:
        cfg = load_acceptance_config()
        self.assertIn("gate_a", cfg)
        self.assertIn("gate_b", cfg)
        self.assertIn("thresholds", cfg["gate_a"])

    def test_required_columns_not_empty(self) -> None:
        self.assertIn("symbol", REQUIRED_COLUMNS)
        self.assertIn("sharpe", REQUIRED_COLUMNS)
        self.assertIn("trade_count", REQUIRED_COLUMNS)


class TestGateA(unittest.TestCase):
    def test_pass(self) -> None:
        d = assert_passes_gate_a(_good_row_a())
        self.assertTrue(d.passed, d.failed_checks)
        self.assertEqual(d.gate, "A")
        self.assertFalse(d.failed_checks)

    def test_fail(self) -> None:
        d = assert_passes_gate_a(_bad_row_a())
        self.assertFalse(d.passed)
        # annual_return + max_drawdown 应都上榜
        joined = " ".join(d.failed_checks)
        self.assertIn("annual_return", joined)
        self.assertIn("max_drawdown", joined)

    def test_missing_column(self) -> None:
        r = _good_row_a()
        del r["sharpe"]
        d = assert_passes_gate_a(r)
        self.assertIn("sharpe", d.missing_columns)
        self.assertFalse(d.passed)


class TestGateB(unittest.TestCase):
    def test_pass(self) -> None:
        d = assert_passes_gate_b(_good_row_b())
        self.assertTrue(d.passed, d.failed_checks)

    def test_trade_count_too_low(self) -> None:
        r = _good_row_b()
        r["trade_count"] = 50
        d = assert_passes_gate_b(r)
        self.assertFalse(d.passed)
        self.assertTrue(any("trade_count" in c for c in d.failed_checks))


class TestAssessAgainstGates(unittest.TestCase):
    def test_mixed_intervals(self) -> None:
        rows = [_good_row_a(), _bad_row_a(), _good_row_b()]
        df = pd.DataFrame(rows)
        res = assess_against_gates(df)
        # 新增列齐全
        for c in ("gate", "passed", "failed_checks", "missing_columns"):
            self.assertIn(c, res.columns)
        # day → gate A；minute5 → gate B
        self.assertEqual(res.loc[0, "gate"], "A")
        self.assertEqual(res.loc[2, "gate"], "B")
        self.assertEqual(int(res.loc[0, "passed"]), 1)
        self.assertEqual(int(res.loc[1, "passed"]), 0)
        self.assertEqual(int(res.loc[2, "passed"]), 1)

    def test_missing_interval_column_raises(self) -> None:
        df = pd.DataFrame([{"sharpe": 1.5}])
        with self.assertRaises(KeyError):
            assess_against_gates(df)

    def test_empty_input(self) -> None:
        df = pd.DataFrame(columns=["interval", "sharpe"])
        out = assess_against_gates(df)
        self.assertEqual(len(out), 0)
        for c in ("gate", "passed", "failed_checks", "missing_columns"):
            self.assertIn(c, out.columns)

    def test_gate_override(self) -> None:
        # 强制 day 也走 B
        row = _good_row_a()
        row["trade_count"] = 20   # 不足以过 B 的 100 门槛
        df = pd.DataFrame([row])
        res = assess_against_gates(df, gate_override={"day": "B"})
        self.assertEqual(res.loc[0, "gate"], "B")
        self.assertEqual(int(res.loc[0, "passed"]), 0)


class TestTempYamlOverride(unittest.TestCase):
    def test_custom_thresholds(self) -> None:
        # 在临时目录写一个把 sharpe 阈值放得很低的 acceptance.yaml，
        # 然后验证 gate A 判定切换
        import tempfile
        cfg = {
            "required_columns": REQUIRED_COLUMNS,
            "gate_a": {
                "description": "relaxed",
                "thresholds": {
                    "sharpe": {"op": ">=", "value": 0.1},
                    "annual_return": {"op": ">=", "value": 0.0},
                },
            },
            "gate_b": {
                "description": "strict",
                "thresholds": {
                    "sharpe": {"op": ">=", "value": 99.0},
                },
            },
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as fh:
            yaml.safe_dump(cfg, fh)
            tmp = Path(fh.name)
        try:
            loaded = load_acceptance_config(tmp)
            row = {"interval": "day", "sharpe": 0.3, "annual_return": 0.05}
            d = assert_passes_gate_a(row, loaded)
            self.assertTrue(d.passed)
            # gate B 应失败
            d2 = assert_passes_gate_b({"interval": "day", "sharpe": 1.0}, loaded)
            self.assertFalse(d2.passed)
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
