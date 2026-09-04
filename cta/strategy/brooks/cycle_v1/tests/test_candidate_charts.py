from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest import candidate_charts
from cta.strategy.brooks.cycle_v1.backtest.candidate_charts import (
    _build_candidate_index,
    _candidate_title,
    _diagnose_candidates,
    _event_x,
    _load_chart_contexts,
    _require_config_hash,
    _select_event_window,
    _separate_label_positions,
    _write_report_chart_links,
    render_candidate_card,
)


def test_candidate_index_uses_most_advanced_observed_outcome() -> None:
    candidates = _candidate_frame(
        ("rejected", "planned", "ordered", "filled", "traded")
    )
    rejections = pd.DataFrame(
        {
            "candidate_id": ["rejected"],
            "feature_asof": ["2026-01-12 14:26:00+08:00"],
            "reason_code": ["CYCLE_CONFIDENCE_BLOCKED"],
            "detail": ["confidence=0.42"],
        }
    )

    index = _build_candidate_index(
        candidates,
        rejections=rejections,
        plans=pd.DataFrame(
            {"candidate_id": ["planned", "ordered", "filled", "traded"]}
        ),
        orders=pd.DataFrame(
            {"candidate_id": ["ordered", "filled", "traded"]}
        ),
        fills=pd.DataFrame({"candidate_id": ["filled", "traded"]}),
        trades=pd.DataFrame({"candidate_id": ["traded"]}),
    )

    assert index.set_index("candidate_id")["outcome_code"].to_dict() == {
        "rejected": "CYCLE_CONFIDENCE_BLOCKED",
        "planned": "ELIGIBLE_PLAN",
        "ordered": "ORDERED",
        "filled": "FILLED",
        "traded": "ROUND_TRIP",
    }
    assert index.loc[index["candidate_id"].eq("rejected"), "outcome_detail"].item() == (
        "confidence=0.42"
    )
    assert index["sequence"].tolist() == [1, 2, 3, 4, 5]


def test_candidate_index_rejects_duplicate_candidate_rejections() -> None:
    rejections = pd.DataFrame(
        {
            "candidate_id": ["candidate", "candidate"],
            "feature_asof": [
                "2026-01-12 14:26:00+08:00",
                "2026-01-12 14:27:00+08:00",
            ],
            "reason_code": ["FIRST", "SECOND"],
            "detail": ["one", "two"],
        }
    )

    with pytest.raises(ValueError, match="candidate.*multiple candidate rejections"):
        _build_candidate_index(
            _candidate_frame(("candidate",)),
            rejections=rejections,
            plans=pd.DataFrame(columns=["candidate_id"]),
            orders=pd.DataFrame(columns=["candidate_id"]),
            fills=pd.DataFrame(columns=["candidate_id"]),
            trades=pd.DataFrame(columns=["candidate_id"]),
        )


@pytest.mark.parametrize(
    ("table_name", "conflicting_frame"),
    (
        (
            "plans",
            pd.DataFrame(
                {
                    "candidate_id": ["candidate"],
                    "symbol": ["RB"],
                    "contract_code": ["RB2605.SHF"],
                }
            ),
        ),
        (
            "orders",
            pd.DataFrame(
                {
                    "candidate_id": ["candidate"],
                    "contract_code": ["RB2605.SHF"],
                }
            ),
        ),
        (
            "fills",
            pd.DataFrame(
                {
                    "candidate_id": ["candidate"],
                    "contract_code": ["RB2605.SHF"],
                }
            ),
        ),
        (
            "trades",
            pd.DataFrame(
                {
                    "candidate_id": ["candidate"],
                    "contract_code": ["RB2605.SHF"],
                    "direction": [-1],
                    "symbol": ["RB"],
                }
            ),
        ),
    ),
)
def test_candidate_index_rejects_conflicting_downstream_identity(
    table_name: str,
    conflicting_frame: pd.DataFrame,
) -> None:
    downstream = {
        "plans": pd.DataFrame(columns=["candidate_id"]),
        "orders": pd.DataFrame(columns=["candidate_id"]),
        "fills": pd.DataFrame(columns=["candidate_id"]),
        "trades": pd.DataFrame(columns=["candidate_id"]),
    }
    downstream[table_name] = conflicting_frame

    with pytest.raises(ValueError, match=f"{table_name} candidate identity conflict"):
        _build_candidate_index(
            _candidate_frame(("candidate",)),
            rejections=pd.DataFrame(
                columns=["candidate_id", "feature_asof", "reason_code", "detail"]
            ),
            **downstream,
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


def test_event_x_starts_review_after_last_bar_completed_at_event() -> None:
    index = pd.date_range("2026-01-12 13:00", periods=3, freq="1h", tz="Asia/Shanghai")
    chart = (0, 0, 300, 100)

    assert _event_x(index, pd.Timestamp("2026-01-12 14:00", tz="Asia/Shanghai"), chart) == 200
    assert _event_x(index, pd.Timestamp("2026-01-12 13:30", tz="Asia/Shanghai"), chart) == 100


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


def test_candidate_title_uses_actual_symbol_and_outcome() -> None:
    row = _diagnosed_candidate_row().copy()
    row["symbol"] = "RB"
    row["outcome_code"] = "ONE_LOT_EXCEEDS_RISK_BUDGET"

    title = _candidate_title(row)

    assert title.startswith("RB candidate 001")
    assert "H2 LONG" in title
    assert "ONE_LOT_EXCEEDS_RISK_BUDGET" in title


def test_chart_contexts_load_each_symbol_from_its_effective_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object, object]] = []

    def fake_load(**kwargs):
        calls.append((kwargs["symbol"], kwargs["start"], kwargs["end"]))
        return SimpleNamespace(
            minute_bars=_bars_with_bar_end(
                "2025-09-01 09:00", periods=20, frequency="1min"
            ),
            sessions=(object(),),
        )

    monkeypatch.setattr(candidate_charts, "load_normalized_symbol", fake_load)
    monkeypatch.setattr(
        candidate_charts.MetadataBundle,
        "load",
        lambda _root: object(),
    )
    monkeypatch.setattr(
        candidate_charts,
        "load_config",
        lambda: pytest.fail("general candidate charts must not reload default config"),
    )
    monkeypatch.setattr(candidate_charts, "load_scalp_config", lambda: object())
    monkeypatch.setattr(
        candidate_charts,
        "aggregate_completed_daily_bars",
        lambda bars, **_kwargs: bars,
    )
    monkeypatch.setattr(
        candidate_charts,
        "aggregate_completed_bars",
        lambda bars, **_kwargs: bars,
    )
    summary = {
        "requested_end": "2026-02-05",
        "loaded_symbols": ["AG0.SHFE", "RB0.SHFE"],
        "effective_warmup_starts": {
            "AG": "2025-09-01",
            "RB": "2025-10-09",
        },
        "execution_metadata_update": {"metadata_root": "metadata-cache"},
    }

    contexts = _load_chart_contexts(summary, Path("minute"), {"AG", "RB"})

    assert set(contexts) == {"AG", "RB"}
    assert calls == [
        ("AG0.SHFE", pd.Timestamp("2025-09-01").date(), pd.Timestamp("2026-02-05").date()),
        ("RB0.SHFE", pd.Timestamp("2025-10-09").date(), pd.Timestamp("2026-02-05").date()),
    ]
    assert all(not context.daily.empty for context in contexts.values())
    assert all(not context.hourly.empty for context in contexts.values())
    assert all(not context.minute.empty for context in contexts.values())


def test_chart_contexts_require_recorded_effective_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars_with_bar_end("2026-01-01 09:00", periods=20, frequency="1min")
    monkeypatch.setattr(candidate_charts.MetadataBundle, "load", lambda _root: object())
    monkeypatch.setattr(candidate_charts, "load_scalp_config", lambda: object())
    monkeypatch.setattr(
        candidate_charts,
        "load_normalized_symbol",
        lambda **_kwargs: SimpleNamespace(minute_bars=bars, sessions=(object(),)),
    )
    monkeypatch.setattr(
        candidate_charts,
        "aggregate_completed_daily_bars",
        lambda source, **_kwargs: source,
    )
    monkeypatch.setattr(
        candidate_charts,
        "aggregate_completed_bars",
        lambda source, **_kwargs: source,
    )
    summary = {
        "requested_start": "2026-01-01",
        "requested_end": "2026-02-05",
        "loaded_symbols": ["RB0.SHFE"],
        "effective_warmup_starts": {},
        "execution_metadata_update": {"metadata_root": "metadata-cache"},
    }

    with pytest.raises(ValueError, match="effective warmup start for RB"):
        _load_chart_contexts(summary, Path("minute"), {"RB"})


def test_level_labels_are_separated_inside_chart() -> None:
    result = _separate_label_positions(
        [200.0, 202.0, 204.0],
        top=100.0,
        bottom=220.0,
        minimum_gap=14.0,
    )

    assert all(100.0 <= value <= 220.0 for value in result)
    assert all(
        right - left >= 14.0
        for left, right in zip(result, result[1:], strict=False)
    )


def test_report_config_hash_must_match_recomputed_cycle_config() -> None:
    with pytest.raises(ValueError, match="config hash"):
        _require_config_hash({"config_hash": "report-hash"}, "current-hash")


def test_generate_candidate_charts_writes_all_images_index_and_report_links(
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
        "_load_chart_contexts",
        lambda summary, data_root, symbols: {"AG": context},
    )
    chart_dir = report_dir / "candidate_charts"
    chart_dir.mkdir()
    (chart_dir / "stale.png").write_bytes(b"stale")

    index = candidate_charts.generate_candidate_charts(report_dir)

    assert len(index) == 2
    assert len(list(chart_dir.glob("*.png"))) == 2
    assert not (chart_dir / "stale.png").exists()
    assert (chart_dir / "index.csv").exists()
    explanation = (report_dir / "CANDIDATE_CHARTS.md").read_text()
    assert "LARGE_CYCLE_UNAVAILABLE" in explanation
    assert "60min" in explanation
    report = (report_dir / "report.md").read_text()
    assert "CANDIDATE_CHARTS.md" in report
    assert "candidate_charts/index.csv" in report


def test_generate_candidate_charts_preserves_prior_images_when_rendering_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = _write_report_fixture(tmp_path)
    chart_dir = report_dir / "candidate_charts"
    chart_dir.mkdir()
    stale_path = chart_dir / "stale.png"
    stale_path.write_bytes(b"prior-complete-chart")
    monkeypatch.setattr(
        candidate_charts,
        "_load_chart_contexts",
        lambda *_args, **_kwargs: {
            "AG": SimpleNamespace(daily=None, hourly=None, minute=None)
        },
    )

    def fail_render(*_args, **_kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(candidate_charts, "render_candidate_card", fail_render)

    with pytest.raises(RuntimeError, match="render failed"):
        candidate_charts.generate_candidate_charts(report_dir)

    assert stale_path.read_bytes() == b"prior-complete-chart"


def test_generate_candidate_charts_supports_zero_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = _write_report_fixture(tmp_path)
    candidates = pd.read_csv(report_dir / "candidates.csv").iloc[0:0]
    candidates.to_csv(report_dir / "candidates.csv", index=False)
    pd.read_csv(report_dir / "rejections.csv").iloc[0:0].to_csv(
        report_dir / "rejections.csv", index=False
    )
    monkeypatch.setattr(
        candidate_charts,
        "_load_chart_contexts",
        lambda *_args, **_kwargs: pytest.fail("empty candidates must not load bars"),
    )

    index = candidate_charts.generate_candidate_charts(report_dir)

    assert index.empty
    persisted = pd.read_csv(report_dir / "candidate_charts" / "index.csv")
    assert persisted.empty
    assert not list((report_dir / "candidate_charts").glob("*.png"))
    assert "No candidates" in (report_dir / "CANDIDATE_CHARTS.md").read_text()


def test_report_chart_links_are_idempotent_and_preserve_later_sections(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.md"
    report_path.write_text(
        "# Report\n\n## Candidate Charts\n- old\n\n## Audit Trail\n- keep me\n"
    )

    _write_report_chart_links(report_path, 2)
    first = report_path.read_text()
    _write_report_chart_links(report_path, 2)
    second = report_path.read_text()

    assert second == first
    assert second.count("## Candidate Charts") == 1
    assert "## Audit Trail\n- keep me" in second


def test_candidate_chart_cli_reports_general_explanation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        candidate_charts,
        "generate_candidate_charts",
        lambda *_args, **_kwargs: pd.DataFrame({"candidate_id": ["one"]}),
    )

    result = candidate_charts.main(["--report-dir", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["candidate_count"] == 1
    assert payload["explanation"].endswith("CANDIDATE_CHARTS.md")


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
    (report_dir / "report.md").write_text("# Brooks Cycle V1 Report\n")
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
    for name in ("plans.csv", "orders.csv", "fills.csv", "trades.csv"):
        pd.DataFrame(columns=["candidate_id"]).to_csv(report_dir / name, index=False)
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


def _candidate_frame(candidate_ids: tuple[str, ...]) -> pd.DataFrame:
    count = len(candidate_ids)
    return pd.DataFrame(
        {
            "candidate_id": candidate_ids,
            "symbol": ["AG"] * count,
            "contract_code": ["AG2604.SHF"] * count,
            "setup": ["H1"] * count,
            "direction": [1] * count,
            "cycle": ["BULL_TIGHT_CHANNEL"] * count,
            "signal_time": pd.date_range(
                "2026-01-12 14:20:00+08:00", periods=count, freq="1min"
            ),
            "active_time": pd.date_range(
                "2026-01-12 14:22:00+08:00", periods=count, freq="1min"
            ),
            "entry": [20_100.0] * count,
            "stop": [20_080.0] * count,
            "target": [20_130.0] * count,
            "trade_mode": ["SWING"] * count,
        }
    )


def _bars_with_bar_end(
    start: str,
    *,
    periods: int,
    frequency: str,
) -> pd.DataFrame:
    frame = _bars(start, periods=periods, frequency=frequency).reset_index()
    return frame.rename(columns={"index": "bar_end"})
