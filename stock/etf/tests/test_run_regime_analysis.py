import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock.etf.run_regime_analysis import run_regime_analysis

EXPECTED_FILES = {
    "daily_predictions.csv",
    "labeled_predictions.csv",
    "accuracy_by_period.csv",
    "confusion_matrix_1d.csv",
    "confusion_matrix_3d.csv",
    "transition_accuracy.csv",
    "summary.json",
    "source_audit.json",
}


def _daily(periods: int = 340) -> pd.DataFrame:
    index = np.arange(periods, dtype=float)
    close = 2.0 + index * 0.002 + np.sin(index / 7.0) * 0.08
    return pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * periods,
            "datetime": pd.bdate_range("2024-01-02", periods=periods),
            "open": close + np.sin(index / 3.0) * 0.01,
            "high": close + 0.03,
            "low": close - 0.03,
            "close": close,
            "volume": 10_000_000.0 + index * 1_000.0,
            "turnover": 20_000_000.0 + index * 2_000.0,
        }
    )


def _calendar(daily: pd.DataFrame) -> pd.DataFrame:
    dates = pd.bdate_range(
        pd.Timestamp(daily["datetime"].min()),
        pd.Timestamp(daily["datetime"].max()) + pd.offsets.BDay(8),
    )
    return pd.DataFrame({"datetime": dates, "is_open": 1})


def _run(output_dir: Path, *, overwrite: bool = False) -> dict[str, object]:
    daily = _daily()
    return run_regime_analysis(
        symbol="159915.SZ",
        daily=daily,
        factors=None,
        calendar=_calendar(daily),
        price_adjustment_mode="raw",
        output_dir=output_dir,
        source_audit={"provider": "synthetic-test"},
        overwrite=overwrite,
    )


def test_runner_writes_complete_artifact_set(tmp_path: Path) -> None:
    output_dir = tmp_path / "regime"

    summary = _run(output_dir)

    assert {path.name for path in output_dir.iterdir()} == EXPECTED_FILES
    assert summary["symbol"] == "159915.SZ"
    assert summary["data"]["bar_count"] == 340
    assert summary["anti_lookahead_audit"]["source_dates_valid"] is True
    for filename in EXPECTED_FILES:
        assert (output_dir / filename).stat().st_size > 0


def test_runner_rejects_nonempty_output_without_overwrite(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "regime"
    output_dir.mkdir()
    marker = output_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already contains"):
        _run(output_dir)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_runner_failure_preserves_previous_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "regime"
    output_dir.mkdir()
    marker = output_dir / "previous-success.txt"
    marker.write_text("previous", encoding="utf-8")
    daily = _daily()
    daily.loc[0, "close"] = 0.0

    with pytest.raises(ValueError, match="positive"):
        run_regime_analysis(
            symbol="159915.SZ",
            daily=daily,
            factors=None,
            calendar=_calendar(daily),
            price_adjustment_mode="raw",
            output_dir=output_dir,
            source_audit={"provider": "synthetic-test"},
            overwrite=True,
        )

    assert marker.read_text(encoding="utf-8") == "previous"
    assert {path.name for path in output_dir.iterdir()} == {"previous-success.txt"}


def test_summary_is_strict_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "regime"
    _run(output_dir)

    def reject_constant(value: str) -> object:
        raise AssertionError(f"non-standard JSON constant: {value}")

    summary = json.loads(
        (output_dir / "summary.json").read_text(encoding="utf-8"),
        parse_constant=reject_constant,
    )
    source_audit = json.loads(
        (output_dir / "source_audit.json").read_text(encoding="utf-8"),
        parse_constant=reject_constant,
    )

    assert summary["evaluation"]["eligibility_rule"]
    assert source_audit["price_adjustment_mode"] == "raw"


def test_latest_predictions_have_targets_without_fake_labels(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "regime"
    _run(output_dir)

    predictions = pd.read_csv(
        output_dir / "daily_predictions.csv",
        parse_dates=["feature_asof_date", "prediction_for_date"],
    )
    labeled = pd.read_csv(
        output_dir / "labeled_predictions.csv",
        parse_dates=["feature_asof_date"],
    )
    latest_date = predictions["feature_asof_date"].max()
    latest_predictions = predictions.loc[
        predictions["feature_asof_date"].eq(latest_date)
    ]
    latest_labeled = labeled.loc[labeled["feature_asof_date"].eq(latest_date)]

    assert set(latest_predictions["prediction_horizon"]) == {"1d", "3d"}
    assert latest_predictions["prediction_for_date"].notna().all()
    assert latest_labeled["label_date"].isna().all()
    assert latest_labeled["realized_state"].isna().all()
