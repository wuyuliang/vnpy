import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock.etf.run_regime_overlay_backtest import (
    build_parser,
    run_regime_overlay_analysis,
)

EXPECTED_FILES = {
    "signals.csv",
    "trades.csv",
    "positions.csv",
    "equity_curve.csv",
    "comparison.csv",
    "annual_metrics.csv",
    "semiannual_metrics.csv",
    "summary.json",
    "source_audit.json",
}


def _daily(periods: int = 540) -> pd.DataFrame:
    index = np.arange(periods, dtype=float)
    close = 10.0 + index * 0.01 + np.sin(index / 8.0) * 0.5
    open_ = close + np.sin(index / 5.0) * 0.03
    return pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * periods,
            "datetime": pd.bdate_range("2016-01-04", periods=periods),
            "open": open_,
            "high": np.maximum(open_, close) + 0.1,
            "low": np.minimum(open_, close) - 0.1,
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
    return run_regime_overlay_analysis(
        symbol="159915.SZ",
        daily=daily,
        factors=None,
        calendar=_calendar(daily),
        price_adjustment_mode="raw",
        start=daily.iloc[300]["datetime"],
        end=daily.iloc[520]["datetime"],
        output_dir=output_dir,
        source_audit={"provider": "synthetic-test"},
        overwrite=overwrite,
    )


def test_runner_writes_complete_overlay_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "overlay"

    summary = _run(output_dir)

    assert {path.name for path in output_dir.iterdir()} == EXPECTED_FILES
    assert summary["symbol"] == "159915.SZ"
    assert summary["data"]["prewarm_bar_count"] == 300
    assert summary["anti_lookahead_audit"]["causal_dates_valid"] is True
    comparison = pd.read_csv(output_dir / "comparison.csv")
    assert set(comparison["strategy"]) == {
        "regime_overlay",
        "ema_only",
        "buy_hold",
    }
    semiannual = pd.read_csv(output_dir / "semiannual_metrics.csv")
    assert set(semiannual["strategy"]) == {
        "regime_overlay",
        "ema_only",
        "buy_hold",
    }
    for filename in EXPECTED_FILES:
        assert (output_dir / filename).stat().st_size > 0


def test_runner_rejects_nonempty_output_without_overwrite(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "overlay"
    output_dir.mkdir()
    marker = output_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already contains"):
        _run(output_dir)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_runner_failure_preserves_previous_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "overlay"
    output_dir.mkdir()
    marker = output_dir / "previous-success.txt"
    marker.write_text("previous", encoding="utf-8")
    daily = _daily()
    daily.loc[0, "close"] = 0.0

    with pytest.raises(ValueError, match="positive"):
        run_regime_overlay_analysis(
            symbol="159915.SZ",
            daily=daily,
            factors=None,
            calendar=_calendar(daily),
            price_adjustment_mode="raw",
            start=daily.iloc[300]["datetime"],
            end=daily.iloc[520]["datetime"],
            output_dir=output_dir,
            source_audit={"provider": "synthetic-test"},
            overwrite=True,
        )

    assert marker.read_text(encoding="utf-8") == "previous"
    assert {path.name for path in output_dir.iterdir()} == {"previous-success.txt"}


def test_runner_json_is_strict_and_dates_match_request(tmp_path: Path) -> None:
    output_dir = tmp_path / "overlay"
    daily = _daily()
    expected_start = str(pd.Timestamp(daily.iloc[300]["datetime"]).date())
    expected_end = str(pd.Timestamp(daily.iloc[520]["datetime"]).date())
    _run(output_dir)

    def reject_constant(value: str) -> object:
        raise AssertionError(f"non-standard JSON constant: {value}")

    summary = json.loads(
        (output_dir / "summary.json").read_text(encoding="utf-8"),
        parse_constant=reject_constant,
    )
    json.loads(
        (output_dir / "source_audit.json").read_text(encoding="utf-8"),
        parse_constant=reject_constant,
    )

    assert summary["backtest"]["start_date"] == expected_start
    assert summary["backtest"]["end_date"] == expected_end
    assert summary["semiannual"]["period_count"] >= 2


def test_cli_defaults_match_frozen_real_run() -> None:
    args = build_parser().parse_args(["--download"])

    assert args.symbol == "159915.SZ"
    assert args.data_start == "2016-01-01"
    assert args.start == "2017-08-14"
    assert args.end == "2026-07-20"
    assert (
        str(args.output_dir)
        .replace("\\", "/")
        .endswith("stock/etf/output/20260727_chuangyeban_regime_overlay")
    )
