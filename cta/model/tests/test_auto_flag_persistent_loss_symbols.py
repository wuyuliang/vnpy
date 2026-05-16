"""Tests for auto_flag_persistent_loss_symbols.

Covers:
- aggregate_symbol_pnl: per-symbol summing across multiple trade-detail csvs
- flag_persistent_loss: thresholding by min_reports / loss_threshold
- write_candidate_manifest: schema sanity
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.model.tools.auto_flag_persistent_loss_symbols import (
    _find_trade_detail_files,
    aggregate_symbol_pnl,
    flag_persistent_loss,
    write_candidate_manifest,
)


def _make_report(root: Path, name: str, rows: list[dict]) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{name}_oot_trade_details.csv"
    pd.DataFrame(rows).to_csv(fp, index=False, encoding="utf-8-sig")
    return fp


class TestAutoFlagPersistentLoss(unittest.TestCase):
    def test_find_files_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_report(root, "20260512_POOL_minute60_both_model_pipeline", [])
            _make_report(root, "20260513_POOL_minute60_both_model_pipeline", [])
            _make_report(root, "20260514_POOL_day_both_model_pipeline", [])
            files = _find_trade_detail_files(root, "*_POOL_minute60_*_model_pipeline")
            self.assertEqual(len(files), 2)

    def test_aggregate_only_executed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_report(
                root,
                "20260512_POOL_minute60_both_model_pipeline",
                [
                    {"symbol": "AL0", "execution_status": "executed", "net_pnl": -2000.0},
                    {"symbol": "AL0", "execution_status": "blocked_symbol_cap", "net_pnl": -999.0},
                    {"symbol": "RB0", "execution_status": "executed", "net_pnl": 500.0},
                ],
            )
            files = _find_trade_detail_files(root, "*_POOL_minute60_*_model_pipeline")
            agg = aggregate_symbol_pnl(files)
            al_row = agg.loc[agg["symbol"] == "AL0"].iloc[0]
            self.assertAlmostEqual(float(al_row["net_pnl"]), -2000.0)
            # blocked 行不计入
            self.assertNotIn(-999.0, agg["net_pnl"].tolist())

    def test_flag_min_reports_threshold(self) -> None:
        agg = pd.DataFrame(
            [
                {"symbol": "AL0", "net_pnl": -2000.0, "report": "r1"},
                {"symbol": "AL0", "net_pnl": -3000.0, "report": "r2"},
                {"symbol": "AL0", "net_pnl": -1500.0, "report": "r3"},
                {"symbol": "RB0", "net_pnl": -2000.0, "report": "r1"},  # 单次亏损不够
                {"symbol": "RB0", "net_pnl": 500.0, "report": "r2"},
                {"symbol": "CU0", "net_pnl": -500.0, "report": "r1"},   # 亏损不够阈值
                {"symbol": "CU0", "net_pnl": -500.0, "report": "r2"},
                {"symbol": "CU0", "net_pnl": -500.0, "report": "r3"},
            ]
        )
        flagged = flag_persistent_loss(agg, min_reports=3, loss_threshold=-1000.0)
        self.assertEqual(set(flagged["symbol"].tolist()), {"AL0"})

    def test_flag_lower_threshold_includes_smaller_losses(self) -> None:
        agg = pd.DataFrame(
            [
                {"symbol": "CU0", "net_pnl": -300.0, "report": f"r{i}"} for i in range(4)
            ]
        )
        flagged = flag_persistent_loss(agg, min_reports=3, loss_threshold=-100.0)
        self.assertEqual(flagged["symbol"].tolist(), ["CU0"])

    def test_write_candidate_manifest_schema(self) -> None:
        flagged = pd.DataFrame(
            [
                {"symbol": "AL0", "loss_report_count": 3, "total_net_pnl": -6000.0, "reports": "r1;r2;r3"},
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "candidate.csv"
            write_candidate_manifest(
                flagged,
                out,
                interval_label="minute60",
                source_tag="20260512",
            )
            df = pd.read_csv(out, encoding="utf-8-sig")
            self.assertEqual(list(df.columns), ["symbol", "reason", "source", "disabled_at", "notes"])
            self.assertEqual(df.iloc[0]["symbol"], "AL0")
            self.assertEqual(df.iloc[0]["reason"], "persistent_loss")
            self.assertIn("oot_20260512_minute60", str(df.iloc[0]["source"]))


if __name__ == "__main__":
    unittest.main()
