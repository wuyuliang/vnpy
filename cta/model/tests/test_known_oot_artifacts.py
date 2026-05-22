"""Regression checks for known OOT artifact issues.

These tests are intentionally defensive: they skip when the referenced
research artifact is not present, but catch stale local reports that would
otherwise hide a fixed evaluator behind old CSV outputs.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.mark.parametrize(
    "out_dir",
    [
        Path("cta/report/backtest/20260518_GRP_CLUSTER_INDEX_day_both_model_pipeline"),
        Path(
            "cta/report/backtest/oot_20260518_100853_cluster_both/"
            "02_by_cluster/grp_cluster_index_day"
        ),
    ],
)
def test_20260518_index_day_artifact_is_not_stale_htf_missing(out_dir: Path) -> None:
    """The reported INDEX/day artifacts should not remain 100% htf_missing."""
    trades_path = out_dir / "20260518_GRP_CLUSTER_INDEX_day_both_oot_trade_details.csv"
    summary_path = out_dir / "20260518_GRP_CLUSTER_INDEX_day_both_oot_summary.csv"
    if not trades_path.exists() or not summary_path.exists():
        pytest.skip("known INDEX/day OOT artifact is not present in this workspace")

    trades = pd.read_csv(trades_path, encoding="utf-8-sig")
    summary = pd.read_csv(summary_path, encoding="utf-8-sig")

    assert not trades.empty
    htf_missing_ratio = (trades["block_reason"].astype(str) == "htf_missing").mean()
    assert htf_missing_ratio < 0.05
    assert int(summary.iloc[0]["trade_count"]) > 0
