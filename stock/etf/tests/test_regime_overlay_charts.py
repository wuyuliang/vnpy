import json
import warnings
from dataclasses import FrozenInstanceError
from typing import Any

import numpy as np
import pandas as pd
import pytest
from PIL import Image

import stock.etf.regime_overlay_charts as regime_charts
from stock.etf.ema_trend_allocation_strategy import TrendAllocationResult
from stock.etf.regime_overlay_charts import (
    STATE_COLORS,
    StateBand,
    aggregate_weekly_state_tracks,
    prepare_daily_state_tracks,
    render_regime_comparison_charts,
    state_band,
)
from stock.etf.regime_overlay_strategy import RegimeOverlayResult
from stock.etf.render_trade_charts import render_symbol_card


EXPECTED_COLORS = {
    "趋势向下": "#f3c1bc",
    "震荡向下": "#f3dfb1",
    "无趋势": "#e2e8f0",
    "震荡向上": "#c8e8d4",
    "趋势向上": "#9fd8b5",
}


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": [
                "2026-07-16 15:00",
                "2026-07-06 09:30",
                "2026-07-10 15:00",
                "2026-07-08 15:00",
                "2026-07-13 15:00",
            ],
            "score_1d": [2.8, -2.8, 0.8, -1.8, 1.8],
            "score_3d": [2.5, -2.5, 0.5, -1.5, 1.5],
            "state_3d": [
                "趋势向上",
                "趋势向下",
                "无趋势",
                "震荡向下",
                "震荡向上",
            ],
            "ignored": ["e", "a", "c", "b", "d"],
        }
    )


def _comparison_results() -> tuple[
    RegimeOverlayResult,
    TrendAllocationResult,
    dict[str, float],
]:
    dates = pd.bdate_range("2026-01-05", "2026-02-27")
    close = np.linspace(10.0, 12.0, len(dates))
    score_3d = np.resize([-2.5, -1.5, 0.0, 1.5, 2.5], len(dates))
    state_by_score = {
        -2.5: "趋势向下",
        -1.5: "震荡向下",
        0.0: "无趋势",
        1.5: "震荡向上",
        2.5: "趋势向上",
    }
    signals = pd.DataFrame(
        {
            "datetime": dates,
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 10_000.0,
            "score_1d": np.linspace(-2.8, 2.8, len(dates)),
            "score_3d": score_3d,
            "state_3d": [state_by_score[score] for score in score_3d],
            "target_weight": np.resize([0.0, 0.5, 1.0], len(dates)),
        }
    )
    overlay_trades = pd.DataFrame(
        {
            "datetime": dates[[4, 12, 25]],
            "symbol": "159915.SZ",
            "side": ["buy", "buy", "sell"],
            "fill_price": [10.2, 10.8, 11.5],
            "quantity": [100, 100, 50],
            "commission": [1.0, 1.0, 1.0],
            "slippage_cost": [0.2, 0.2, 0.2],
            "realized_pnl": [0.0, 0.0, 60.0],
            "primary_reason": [
                "target_weight_0_to_0.5",
                "target_weight_0.5_to_1",
                "target_weight_1_to_0.5",
            ],
        }
    )
    ema_trades = pd.DataFrame(
        {
            "datetime": dates[[8, 30]],
            "symbol": "159915.SZ",
            "side": ["buy", "sell"],
            "fill_price": [10.4, 11.7],
            "quantity": [80, 80],
            "commission": [0.8, 0.8],
            "slippage_cost": [0.1, 0.1],
            "realized_pnl": [0.0, 102.4],
            "primary_reason": [
                "target_weight_0_to_0.5",
                "target_weight_0.5_to_0",
            ],
        }
    )
    overlay_positions = pd.DataFrame(
        {
            "datetime": [dates[-2], dates[-1]],
            "symbol": "159915.SZ",
            "quantity": [120, 150],
            "average_price": [10.7, 10.7],
            "market_value": [1_420.0, 1_800.0],
            "equity": [2_900.0, 3_000.0],
            "weight": [0.49, 0.6],
            "target_weight": [0.5, 0.5],
        }
    )
    ema_positions = pd.DataFrame(
        {
            "datetime": [dates[-2], dates[-1]],
            "symbol": "159915.SZ",
            "quantity": [80, 0],
            "average_price": [10.4, 0.0],
            "market_value": [936.0, 0.0],
            "equity": [3_100.0, 3_200.0],
            "weight": [0.3, 0.0],
            "target_weight": [0.5, 0.0],
        }
    )
    overlay_summary = {
        "total_return": 0.2,
        "max_drawdown": -0.08,
        "sharpe": 1.3,
        "annual_one_way_turnover": 3.2,
    }
    ema_metrics = {
        "total_return": 0.12,
        "max_drawdown": -0.11,
        "sharpe": 0.9,
        "annual_one_way_turnover": 2.1,
    }
    ema_result = TrendAllocationResult(
        signals=pd.DataFrame(),
        trades=ema_trades,
        positions=ema_positions,
        equity_curve=pd.DataFrame(),
        summary={"total_return": 999.0},
    )
    overlay_result = RegimeOverlayResult(
        signals=signals,
        trades=overlay_trades,
        positions=overlay_positions,
        equity_curve=pd.DataFrame(),
        summary=overlay_summary,
        ema_result=ema_result,
    )
    return overlay_result, ema_result, ema_metrics


def test_state_band_is_frozen_and_uses_documented_colors() -> None:
    assert STATE_COLORS == EXPECTED_COLORS

    band = state_band(2.0)

    assert band == StateBand(state="趋势向上", color="#9fd8b5")
    with pytest.raises(FrozenInstanceError):
        band.color = "#000000"


@pytest.mark.parametrize(
    ("score", "expected_state"),
    [
        (-3.0, "趋势向下"),
        (-2.0, "趋势向下"),
        (-1.999, "震荡向下"),
        (-1.0, "无趋势"),
        (0.0, "无趋势"),
        (1.0, "无趋势"),
        (1.001, "震荡向上"),
        (1.999, "震荡向上"),
        (2.0, "趋势向上"),
        (3.0, "趋势向上"),
    ],
)
def test_state_band_uses_strategy_boundaries(
    score: float,
    expected_state: str,
) -> None:
    band = state_band(score)

    assert band.state == expected_state
    assert band.color == EXPECTED_COLORS[expected_state]


@pytest.mark.parametrize(
    "score",
    [
        True,
        False,
        "2",
        None,
        np.nan,
        np.inf,
        -np.inf,
        -3.001,
        3.001,
    ],
)
def test_state_band_rejects_invalid_scores(score: object) -> None:
    with pytest.raises(ValueError, match="score"):
        state_band(score)


def test_prepare_daily_state_tracks_normalizes_sorts_and_does_not_fill() -> None:
    signals = _signals()
    original = signals.copy(deep=True)

    daily = prepare_daily_state_tracks(signals)

    assert daily.columns.tolist() == [
        "datetime",
        "score_1d",
        "score_3d",
        "state_3d",
        "state_color",
    ]
    assert daily["datetime"].tolist() == [
        pd.Timestamp("2026-07-06"),
        pd.Timestamp("2026-07-08"),
        pd.Timestamp("2026-07-10"),
        pd.Timestamp("2026-07-13"),
        pd.Timestamp("2026-07-16"),
    ]
    assert daily["state_color"].tolist() == [
        "#f3c1bc",
        "#f3dfb1",
        "#e2e8f0",
        "#c8e8d4",
        "#9fd8b5",
    ]
    assert len(daily) == len(signals)
    pd.testing.assert_frame_equal(signals, original)


def test_prepare_daily_state_tracks_parses_mixed_date_formats() -> None:
    signals = pd.DataFrame(
        {
            "datetime": ["07/07/2026 15:00", "2026-07-06 09:30"],
            "score_1d": [1.5, -1.5],
            "score_3d": [1.5, -1.5],
            "state_3d": ["震荡向上", "震荡向下"],
        }
    )

    daily = prepare_daily_state_tracks(signals)

    assert daily["datetime"].tolist() == [
        pd.Timestamp("2026-07-06"),
        pd.Timestamp("2026-07-07"),
    ]


@pytest.mark.parametrize(
    "column",
    ["datetime", "score_1d", "score_3d", "state_3d"],
)
def test_prepare_daily_state_tracks_requires_columns(column: str) -> None:
    with pytest.raises(ValueError, match="missing columns"):
        prepare_daily_state_tracks(_signals().drop(columns=column))


@pytest.mark.parametrize("invalid_date", ["not-a-date", None])
def test_prepare_daily_state_tracks_rejects_invalid_dates(
    invalid_date: object,
) -> None:
    signals = _signals()
    signals.loc[0, "datetime"] = invalid_date

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        with pytest.raises(ValueError, match="datetime"):
            prepare_daily_state_tracks(signals)


def test_prepare_daily_state_tracks_rejects_duplicate_normalized_dates() -> None:
    signals = _signals()
    signals.loc[1, "datetime"] = "2026-07-16 09:30"

    with pytest.raises(ValueError, match="duplicate"):
        prepare_daily_state_tracks(signals)


def test_prepare_daily_state_tracks_rejects_timezone_aware_dates() -> None:
    signals = _signals()
    signals["datetime"] = pd.to_datetime(signals["datetime"]).dt.tz_localize(
        "Asia/Shanghai"
    )

    with pytest.raises(ValueError, match="timezone"):
        prepare_daily_state_tracks(signals)


@pytest.mark.parametrize("column", ["score_1d", "score_3d"])
@pytest.mark.parametrize(
    "invalid_score",
    [True, "1", np.nan, np.inf, -np.inf, -3.01, 3.01],
)
def test_prepare_daily_state_tracks_rejects_invalid_scores(
    column: str,
    invalid_score: object,
) -> None:
    signals = _signals()
    signals[column] = [invalid_score, *signals[column].iloc[1:].tolist()]

    with pytest.raises(ValueError, match=column):
        prepare_daily_state_tracks(signals)


def test_prepare_daily_state_tracks_rejects_inconsistent_state() -> None:
    signals = _signals()
    signals.loc[0, "state_3d"] = "趋势向下"

    with pytest.raises(ValueError, match="state_3d"):
        prepare_daily_state_tracks(signals)


def test_aggregate_weekly_state_tracks_uses_last_actual_row_without_fill() -> None:
    daily = prepare_daily_state_tracks(_signals())

    weekly = aggregate_weekly_state_tracks(daily)

    assert weekly["datetime"].tolist() == [
        pd.Timestamp("2026-07-10"),
        pd.Timestamp("2026-07-17"),
    ]
    pd.testing.assert_series_equal(
        weekly.iloc[0].drop(labels="datetime"),
        daily.loc[daily["datetime"].eq("2026-07-10")].iloc[0].drop(labels="datetime"),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        weekly.iloc[1].drop(labels="datetime"),
        daily.loc[daily["datetime"].eq("2026-07-16")].iloc[0].drop(labels="datetime"),
        check_names=False,
    )


def test_aggregate_weekly_state_tracks_omits_fully_missing_week() -> None:
    signals = pd.DataFrame(
        {
            "datetime": ["2026-07-10", "2026-07-24"],
            "score_1d": [-2.5, 2.5],
            "score_3d": [-2.5, 2.5],
            "state_3d": ["趋势向下", "趋势向上"],
        }
    )

    weekly = aggregate_weekly_state_tracks(prepare_daily_state_tracks(signals))

    assert weekly["datetime"].tolist() == [
        pd.Timestamp("2026-07-10"),
        pd.Timestamp("2026-07-24"),
    ]
    assert not weekly.isna().any(axis=None)


def test_mutating_future_week_does_not_change_prior_week() -> None:
    original = aggregate_weekly_state_tracks(prepare_daily_state_tracks(_signals()))
    mutated_signals = _signals()
    future = pd.to_datetime(mutated_signals["datetime"]).dt.normalize().gt("2026-07-10")
    mutated_signals.loc[future, "score_1d"] = -2.9
    mutated_signals.loc[future, "score_3d"] = -2.8
    mutated_signals.loc[future, "state_3d"] = "趋势向下"

    mutated = aggregate_weekly_state_tracks(prepare_daily_state_tracks(mutated_signals))

    pd.testing.assert_frame_equal(
        original.iloc[[0]].reset_index(drop=True),
        mutated.iloc[[0]].reset_index(drop=True),
        check_exact=True,
    )


def test_render_comparison_writes_two_strategy_images(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay_result, ema_result, ema_metrics = _comparison_results()
    calls: list[dict[str, Any]] = []

    def recording_render(**kwargs: Any) -> Image.Image:
        calls.append(kwargs)
        return render_symbol_card(**kwargs)

    monkeypatch.setattr(regime_charts, "render_symbol_card", recording_render)
    output_dir = tmp_path / "charts"

    summary = render_regime_comparison_charts(
        symbol="159915.SZ",
        name="易方达创业板ETF",
        overlay_result=overlay_result,
        ema_result=ema_result,
        ema_metrics=ema_metrics,
        output_dir=output_dir,
        report_start="2026-01-05",
        report_end="2026-02-27",
    )

    expected_files = [
        "0001_159915_SZ_易方达创业板ETF_状态覆盖.png",
        "0002_159915_SZ_易方达创业板ETF_EMA基线.png",
    ]
    assert sorted(path.name for path in output_dir.glob("*.png")) == expected_files
    expected_sizes = [(1680, 1120), (1680, 1000)]
    for filename, expected_size in zip(expected_files, expected_sizes, strict=True):
        with Image.open(output_dir / filename) as image:
            assert image.size == expected_size

    assert len(calls) == 2
    pd.testing.assert_frame_equal(calls[0]["daily_bars"], overlay_result.signals)
    pd.testing.assert_frame_equal(calls[1]["daily_bars"], overlay_result.signals)
    pd.testing.assert_frame_equal(calls[0]["trades"], overlay_result.trades)
    pd.testing.assert_frame_equal(calls[1]["trades"], ema_result.trades)
    assert calls[0]["latest_position"]["quantity"] == 150
    assert calls[1]["latest_position"] is None
    assert calls[0]["review_label"] == "状态覆盖策略"
    assert calls[1]["review_label"] == "EMA 基线"
    assert calls[0]["daily_state_scores"] is not None
    assert calls[0]["weekly_state_scores"] is not None
    assert calls[1].get("daily_state_scores") is None
    assert calls[1].get("weekly_state_scores") is None
    assert calls[1]["performance"] == ema_metrics

    target_counts = overlay_result.signals["target_weight"].value_counts()
    assert calls[0]["performance"]["target_distribution"] == {
        f"{weight:g}": {
            "days": int(target_counts.get(weight, 0)),
            "fraction": float(target_counts.get(weight, 0))
            / len(overlay_result.signals),
        }
        for weight in (0.0, 0.5, 1.0)
    }

    index = pd.read_csv(output_dir / "index.csv")
    assert index.columns.tolist() == [
        "rank",
        "strategy",
        "symbol",
        "name",
        "trade_count",
        "buy_count",
        "sell_count",
        "is_open",
        "total_return",
        "max_drawdown",
        "sharpe",
        "annual_one_way_turnover",
        "image_path",
    ]
    assert index["strategy"].tolist() == ["regime_overlay", "ema_only"]
    assert index["rank"].tolist() == [1, 2]
    assert index["trade_count"].tolist() == [3, 2]
    assert index["buy_count"].tolist() == [2, 1]
    assert index["sell_count"].tolist() == [1, 1]
    assert index["is_open"].tolist() == [True, False]
    assert index["total_return"].tolist() == pytest.approx([0.2, 0.12])
    assert index["image_path"].tolist() == expected_files

    raw_summary = (output_dir / "render_summary.json").read_text(encoding="utf-8")

    def reject_constant(value: str) -> None:
        raise AssertionError(f"non-finite JSON constant: {value}")

    assert json.loads(raw_summary, parse_constant=reject_constant) == summary
    assert summary == {
        "schema_version": 1,
        "symbol": "159915.SZ",
        "report_start": "2026-01-05",
        "report_end": "2026-02-27",
        "rendered_images": 2,
        "expected_images": 2,
        "image_files": expected_files,
        "state_background_driver": "score_3d",
        "score_tracks": ["score_1d", "score_3d"],
    }


def test_render_comparison_rejects_nonempty_output_without_overwrite(
    tmp_path: Any,
) -> None:
    overlay_result, ema_result, ema_metrics = _comparison_results()
    output_dir = tmp_path / "charts"
    output_dir.mkdir()
    marker = output_dir / "keep.txt"
    marker.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not empty"):
        render_regime_comparison_charts(
            symbol="159915.SZ",
            name="易方达创业板ETF",
            overlay_result=overlay_result,
            ema_result=ema_result,
            ema_metrics=ema_metrics,
            output_dir=output_dir,
            report_start="2026-01-05",
            report_end="2026-02-27",
        )

    assert marker.read_text(encoding="utf-8") == "existing"


def test_render_comparison_invalid_state_writes_no_audit_files(
    tmp_path: Any,
) -> None:
    overlay_result, ema_result, ema_metrics = _comparison_results()
    overlay_result.signals.loc[0, "score_3d"] = np.nan
    output_dir = tmp_path / "charts"

    with pytest.raises(ValueError, match="score_3d"):
        render_regime_comparison_charts(
            symbol="159915.SZ",
            name="易方达创业板ETF",
            overlay_result=overlay_result,
            ema_result=ema_result,
            ema_metrics=ema_metrics,
            output_dir=output_dir,
            report_start="2026-01-05",
            report_end="2026-02-27",
        )

    assert not (output_dir / "index.csv").exists()
    assert not (output_dir / "render_summary.json").exists()


def test_render_comparison_propagates_image_save_failure(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay_result, ema_result, ema_metrics = _comparison_results()
    output_dir = tmp_path / "charts"

    def fail_save(_image: Image.Image, _path: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Image.Image, "save", fail_save)

    with pytest.raises(OSError, match="disk full"):
        render_regime_comparison_charts(
            symbol="159915.SZ",
            name="易方达创业板ETF",
            overlay_result=overlay_result,
            ema_result=ema_result,
            ema_metrics=ema_metrics,
            output_dir=output_dir,
            report_start="2026-01-05",
            report_end="2026-02-27",
        )

    assert not (output_dir / "index.csv").exists()
    assert not (output_dir / "render_summary.json").exists()
