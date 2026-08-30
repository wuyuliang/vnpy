from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest import candidate_charts
from cta.strategy.brooks.cycle_v1.backtest.candidate_charts import (
    _diagnose_candidates,
    _require_config_hash,
    _select_event_window,
    _separate_label_positions,
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


def test_level_labels_are_separated_inside_chart() -> None:
    result = _separate_label_positions(
        [200.0, 202.0, 204.0],
        top=100.0,
        bottom=220.0,
        minimum_gap=14.0,
    )

    assert all(100.0 <= value <= 220.0 for value in result)
    assert all(right - left >= 14.0 for left, right in zip(result, result[1:]))


def test_report_config_hash_must_match_recomputed_cycle_config() -> None:
    with pytest.raises(ValueError, match="config hash"):
        _require_config_hash({"config_hash": "report-hash"}, "current-hash")


def test_generate_candidate_charts_writes_index_images_and_explanation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = _write_report_fixture(tmp_path)
    context = SimpleNamespace(
        daily=_bars("2025-12-20", periods=30, frequency="1D"),
        hourly=_bars("2026-01-10 09:00", periods=80, frequency="1h"),
        minute=_bars("2026-01-12 12:30", periods=180, frequency="1min"),
        long=pd.DataFrame(
            {
                "feature_asof": pd.to_datetime(
                    [
                        "2026-01-12 14:00:00+08:00",
                        "2026-01-12 14:30:00+08:00",
                    ]
                ),
                "cycle": ["UNAVAILABLE", "UNAVAILABLE"],
                "reason": [
                    "INSUFFICIENT_CAUSAL_HISTORY",
                    "INSUFFICIENT_PRESSURE_HISTORY",
                ],
            }
        ),
    )
    monkeypatch.setattr(
        candidate_charts,
        "_load_chart_context",
        lambda summary, data_root: context,
        raising=False,
    )

    index = candidate_charts.generate_candidate_charts(report_dir)

    assert len(index) == 2
    assert len(list((report_dir / "candidate_charts").glob("*.png"))) == 2
    assert (report_dir / "candidate_charts" / "index.csv").exists()
    explanation = (report_dir / "LARGE_CYCLE_UNAVAILABLE.md").read_text()
    assert "INSUFFICIENT_CAUSAL_HISTORY" in explanation
    assert "60min is review context" in explanation


def _write_report_fixture(tmp_path: Path) -> Path:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    summary = {
        "requested_start": "2026-01-01",
        "requested_end": "2026-02-05",
        "requested_symbols": ["AG0.SHFE"],
        "loaded_symbols": ["AG0.SHFE"],
        "timeframes": {"long": "30min", "medium": "5min", "short": "1min"},
        "execution_metadata_update": {"metadata_root": "unused-in-test"},
    }
    (report_dir / "summary.json").write_text(json.dumps(summary))
    candidates = pd.DataFrame(
        {
            "candidate_id": ["early", "later"],
            "symbol": ["AG", "AG"],
            "contract_code": ["AG2604.SHF", "AG2604.SHF"],
            "setup": ["H2", "H1"],
            "direction": [1, -1],
            "cycle": ["BULL_TIGHT_CHANNEL", "BEAR_TIGHT_CHANNEL"],
            "signal_time": [
                "2026-01-12 14:25:00+08:00",
                "2026-01-12 14:35:00+08:00",
            ],
            "active_time": [
                "2026-01-12 14:27:00+08:00",
                "2026-01-12 14:37:00+08:00",
            ],
            "entry": [20100.0, 20110.0],
            "stop": [20080.0, 20130.0],
            "target": [20130.0, 20080.0],
            "trade_mode": ["SWING", "SWING"],
        }
    )
    candidates.to_csv(report_dir / "candidates.csv", index=False)
    pd.DataFrame(
        {
            "candidate_id": ["early", "later"],
            "feature_asof": [
                "2026-01-12 14:26:00+08:00",
                "2026-01-12 14:36:00+08:00",
            ],
            "reason_code": ["LARGE_CYCLE_UNAVAILABLE"] * 2,
            "detail": ["large_cycle=UNAVAILABLE"] * 2,
        }
    ).to_csv(report_dir / "rejections.csv", index=False)
    return report_dir


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
