from __future__ import annotations

import pandas as pd

from cta.strategy.brooks.cycle_v1.backtest.candidate_charts import (
    _diagnose_candidates,
)


def test_diagnose_candidates_uses_latest_visible_large_snapshot() -> None:
    candidates = pd.DataFrame(
        {
            "candidate_id": ["later", "early"],
            "signal_time": [
                "2026-01-28 13:47:00+08:00",
                "2026-01-16 00:30:00+08:00",
            ],
            "active_time": [
                "2026-01-28 13:49:00+08:00",
                "2026-01-16 00:32:00+08:00",
            ],
            "symbol": ["AG", "AG"],
            "contract_code": ["AG2604.SHF", "AG2604.SHF"],
            "setup": ["H1", "H2"],
            "direction": [1, 1],
            "cycle": ["BULL_TIGHT_CHANNEL", "BULL_BROAD_CHANNEL"],
            "entry": [29402.0, 22911.0],
            "stop": [29350.0, 22879.0],
            "target": [29456.0, 22994.0],
        }
    )
    rejections = pd.DataFrame(
        {
            "candidate_id": ["early", "later"],
            "feature_asof": [
                "2026-01-16 00:31:00+08:00",
                "2026-01-28 13:48:00+08:00",
            ],
            "reason_code": ["LARGE_CYCLE_UNAVAILABLE"] * 2,
            "detail": ["large_cycle=UNAVAILABLE"] * 2,
        }
    )
    long_frame = pd.DataFrame(
        {
            "feature_asof": pd.to_datetime(
                [
                    "2026-01-16 00:30:00+08:00",
                    "2026-01-28 11:30:00+08:00",
                ]
            ),
            "cycle": ["UNAVAILABLE", "UNAVAILABLE"],
            "reason": [
                "INSUFFICIENT_CAUSAL_HISTORY",
                "INSUFFICIENT_PRESSURE_HISTORY",
            ],
        }
    )

    result = _diagnose_candidates(candidates, rejections, long_frame)

    assert result["candidate_id"].tolist() == ["early", "later"]
    assert result["large_reason"].tolist() == [
        "INSUFFICIENT_CAUSAL_HISTORY",
        "INSUFFICIENT_PRESSURE_HISTORY",
    ]
    assert result["sequence"].tolist() == [1, 2]
    assert result["diagnostic_source"].eq(
        "recomputed_30min_snapshot"
    ).all()
