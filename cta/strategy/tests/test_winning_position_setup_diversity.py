from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from cta.config.winning_position_setup_diversity_config import (
    WinningPositionSetupDiversityConfig,
)
from cta.strategy.baseline_candidate_gen import filter_candidates_with_diversity


def _candidates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["AU0", "AU0", "AU0"],
            "side": ["long", "long", "long"],
            "signal_type": [
                "atr_breakout",
                "donchian_breakout",
                "breakout_pullback_continuation",
            ],
            "datetime": pd.to_datetime(
                ["2025-01-01 09:00:00", "2025-01-01 09:00:00", "2025-01-01 09:00:00"]
            ),
            "rank_score": [0.9, 0.8, 0.7],
        }
    )


def test_diversity_disabled_keeps_default_dedup() -> None:
    cfg = WinningPositionSetupDiversityConfig()
    out = filter_candidates_with_diversity(
        _candidates(),
        current_position=None,
        state=None,
        cfg=cfg,
    )
    assert len(out) == 1


def test_diversity_enabled_allows_multiple_when_winning_and_trending() -> None:
    cfg = WinningPositionSetupDiversityConfig(
        use_winning_position_setup_diversity=True,
        enabled_by_cluster_interval={"precious|day": True},
        max_concurrent_signal_types_per_symbol=3,
    )
    st = SimpleNamespace(current_pnl_pct=0.08, trend_score=0.6)
    out = filter_candidates_with_diversity(
        _candidates(),
        current_position={"symbol": "AU0"},
        state=st,
        cfg=cfg,
        cluster="precious",
        interval="day",
    )
    assert len(out) == 3

