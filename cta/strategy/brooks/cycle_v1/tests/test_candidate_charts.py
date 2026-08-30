from __future__ import annotations

import pandas as pd

from cta.strategy.brooks.cycle_v1.backtest.candidate_charts import (
    _diagnose_candidates,
    _select_event_window,
    render_candidate_card,
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


def test_select_event_window_keeps_requested_context() -> None:
    minute = _bars("2026-01-12 12:30", periods=180, frequency="1min")
    signal = pd.Timestamp("2026-01-12 14:25:00", tz="Asia/Shanghai")

    selected = _select_event_window(minute, signal, before=80, after=40)

    assert len(selected) == 121
    assert selected.index[80] == signal


def test_render_candidate_card_has_three_panel_dimensions() -> None:
    row = _diagnosed_candidate_row()
    daily = _bars("2025-12-20", periods=30, frequency="1D")
    hourly = _bars("2026-01-10 09:00", periods=80, frequency="1h")
    minute = _bars("2026-01-12 12:30", periods=180, frequency="1min")

    image = render_candidate_card(
        row,
        daily=daily,
        hourly=hourly,
        minute=minute,
    )

    assert image.size == (1680, 1240)
    assert image.getbbox() == (0, 0, 1680, 1240)


def _diagnosed_candidate_row() -> pd.Series:
    return pd.Series(
        {
            "sequence": 1,
            "candidate_id": "candidate-ag",
            "symbol": "AG",
            "contract_code": "AG2604.SHF",
            "setup": "H2",
            "direction": 1,
            "cycle": "BULL_TIGHT_CHANNEL",
            "signal_time": pd.Timestamp(
                "2026-01-12 14:25:00", tz="Asia/Shanghai"
            ),
            "active_time": pd.Timestamp(
                "2026-01-12 14:27:00", tz="Asia/Shanghai"
            ),
            "entry": 20874.0,
            "stop": 20838.0,
            "target": 20933.0,
            "rejection_feature_asof": pd.Timestamp(
                "2026-01-12 14:26:00", tz="Asia/Shanghai"
            ),
            "rejection_code": "LARGE_CYCLE_UNAVAILABLE",
            "large_cycle": "UNAVAILABLE",
            "large_reason": "INSUFFICIENT_CAUSAL_HISTORY",
        }
    )


def _bars(
    start: str,
    *,
    periods: int,
    frequency: str,
) -> pd.DataFrame:
    index = pd.date_range(
        start,
        periods=periods,
        freq=frequency,
        tz="Asia/Shanghai",
    )
    base = pd.Series(range(periods), dtype=float).to_numpy() + 20_000.0
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 12.0,
            "low": base - 8.0,
            "close": base + 4.0,
            "volume": base - 19_900.0,
        },
        index=index,
    )
