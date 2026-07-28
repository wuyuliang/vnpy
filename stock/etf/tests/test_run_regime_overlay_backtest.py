import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

import stock.etf.run_regime_overlay_backtest as runner
from stock.etf.run_regime_overlay_backtest import (
    build_parser,
    run_regime_overlay_analysis,
)

OUTPUT_FILES = [
    "signals.csv",
    "trades.csv",
    "positions.csv",
    "equity_curve.csv",
    "comparison.csv",
    "annual_metrics.csv",
    "semiannual_metrics.csv",
    "summary.json",
    "source_audit.json",
]
CHART_FILES = [
    "0001_159915_SZ_易方达创业板ETF_状态覆盖.png",
    "0002_159915_SZ_易方达创业板ETF_EMA基线.png",
    "index.csv",
    "render_summary.json",
]
EXPECTED_ENTRIES = {
    *OUTPUT_FILES,
    "charts",
}
EXPECTED_ARTIFACTS = OUTPUT_FILES + [f"charts/{filename}" for filename in CHART_FILES]


def _daily(periods: int = 540, symbol: str = "159915.SZ") -> pd.DataFrame:
    index = np.arange(periods, dtype=float)
    close = 10.0 + index * 0.01 + np.sin(index / 8.0) * 0.5
    open_ = close + np.sin(index / 5.0) * 0.03
    return pd.DataFrame(
        {
            "symbol": [symbol] * periods,
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


def _run(
    output_dir: Path,
    *,
    symbol: str = "159915.SZ",
    overwrite: bool = False,
) -> dict[str, object]:
    daily = _daily(symbol=symbol)
    return run_regime_overlay_analysis(
        symbol=symbol,
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


def _directory_bytes(path: Path) -> dict[str, bytes]:
    return {entry.name: entry.read_bytes() for entry in path.iterdir()}


def _publication_fixture(tmp_path: Path) -> tuple[Path, Path]:
    output_dir = tmp_path / "overlay"
    output_dir.mkdir()
    (output_dir / "previous.txt").write_bytes(b"previous")
    staging_dir = tmp_path / ".overlay.test.staging"
    staging_dir.mkdir()
    (staging_dir / "current.txt").write_bytes(b"current")
    return staging_dir, output_dir


def test_publish_failure_restores_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging_dir, output_dir = _publication_fixture(tmp_path)
    real_replace = Path.replace
    publish_error = OSError("publish failed")

    def fail_staging_publish(path: Path, target: object) -> Path:
        if path == staging_dir and Path(target) == output_dir:
            raise publish_error
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_staging_publish)

    with pytest.raises(OSError, match="publish failed") as error:
        runner._publish_staging(staging_dir, output_dir)

    assert error.value is publish_error
    assert _directory_bytes(output_dir) == {"previous.txt": b"previous"}
    assert _directory_bytes(staging_dir) == {"current.txt": b"current"}
    assert not list(tmp_path.glob(".overlay.*.backup"))


def test_publish_and_restore_failures_preserve_backup_and_cleanup_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "overlay"
    output_dir.mkdir()
    (output_dir / "previous.txt").write_bytes(b"previous")
    previous_contents = _directory_bytes(output_dir)
    real_replace = Path.replace
    publish_error = OSError("publish failed")
    restore_error = OSError("restore failed")

    def fail_publish_and_restore(path: Path, target: object) -> Path:
        target_path = Path(target)
        if (
            path.parent == tmp_path
            and path.name.startswith(".overlay.")
            and path.name.endswith(".staging")
            and target_path == output_dir
        ):
            raise publish_error
        if (
            path.parent == tmp_path
            and path.name.startswith(".overlay.")
            and path.name.endswith(".backup")
            and target_path == output_dir
        ):
            raise restore_error
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_publish_and_restore)

    with pytest.raises(RuntimeError, match="publish.*restore") as error:
        _run(output_dir, overwrite=True)

    assert "OSError: publish failed" in str(error.value)
    assert "OSError: restore failed" in str(error.value)
    assert error.value.original_error is publish_error
    assert error.value.recovery_error is restore_error
    assert error.value.__cause__ is restore_error
    backups = list(tmp_path.glob(".overlay.*.backup"))
    assert len(backups) == 1
    assert _directory_bytes(backups[0]) == previous_contents
    assert not output_dir.exists()
    assert not list(tmp_path.glob(".overlay.*.staging"))


def test_backup_cleanup_failure_restores_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging_dir, output_dir = _publication_fixture(tmp_path)
    real_rmtree = runner.shutil.rmtree
    cleanup_error = OSError("backup cleanup failed")

    def fail_backup_cleanup(path: object, *args: object, **kwargs: object) -> None:
        candidate = Path(path)
        if (
            candidate.parent == tmp_path
            and candidate.name.startswith(".overlay.")
            and candidate.name.endswith(".backup")
        ):
            raise cleanup_error
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runner.shutil, "rmtree", fail_backup_cleanup)

    with pytest.raises(OSError, match="backup cleanup failed") as error:
        runner._publish_staging(staging_dir, output_dir)

    assert error.value is cleanup_error
    assert _directory_bytes(output_dir) == {"previous.txt": b"previous"}
    assert not staging_dir.exists()
    assert not list(tmp_path.glob(".overlay.*.backup"))


def test_backup_cleanup_and_rollback_failures_preserve_recoverable_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging_dir, output_dir = _publication_fixture(tmp_path)
    real_rmtree = runner.shutil.rmtree
    cleanup_error = OSError("backup cleanup failed")
    rollback_error = OSError("rollback delete failed")

    def fail_cleanup_and_rollback(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        candidate = Path(path)
        if (
            candidate.parent == tmp_path
            and candidate.name.startswith(".overlay.")
            and candidate.name.endswith(".backup")
        ):
            raise cleanup_error
        if candidate == output_dir:
            raise rollback_error
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runner.shutil, "rmtree", fail_cleanup_and_rollback)

    with pytest.raises(RuntimeError, match="failed to roll back") as error:
        runner._publish_staging(staging_dir, output_dir)

    assert "OSError: backup cleanup failed" in str(error.value)
    assert "OSError: rollback delete failed" in str(error.value)
    assert error.value.original_error is cleanup_error
    assert error.value.recovery_error is rollback_error
    assert error.value.__cause__ is rollback_error
    backups = list(tmp_path.glob(".overlay.*.backup"))
    assert len(backups) == 1
    assert _directory_bytes(backups[0]) == {"previous.txt": b"previous"}
    assert _directory_bytes(output_dir) == {"current.txt": b"current"}
    assert not staging_dir.exists()


def test_runner_writes_complete_overlay_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "overlay"

    summary = _run(output_dir)

    assert {path.name for path in output_dir.iterdir()} == EXPECTED_ENTRIES
    assert summary["symbol"] == "159915.SZ"
    assert summary["data"]["prewarm_bar_count"] == 300
    assert summary["anti_lookahead_audit"]["causal_dates_valid"] is True
    assert summary["output_files"] == EXPECTED_ARTIFACTS
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
    for filename in OUTPUT_FILES:
        assert (output_dir / filename).stat().st_size > 0

    charts = output_dir / "charts"
    assert {path.name for path in charts.iterdir()} == set(CHART_FILES)
    for filename, expected_size in zip(
        CHART_FILES[:2],
        [(1680, 1120), (1680, 1000)],
        strict=True,
    ):
        with Image.open(charts / filename) as image:
            assert image.size == expected_size

    index = pd.read_csv(charts / "index.csv")
    assert len(index) == 2
    assert index["strategy"].tolist() == ["regime_overlay", "ema_only"]
    assert index["image_path"].tolist() == CHART_FILES[:2]
    for image_path in index["image_path"]:
        assert (charts / image_path).is_file()


def test_runner_publishes_dynamic_chart_artifacts_for_other_symbol(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "overlay"
    symbol = "510300.SH"
    chart_files = [
        "0001_510300_SH_510300_SH_状态覆盖.png",
        "0002_510300_SH_510300_SH_EMA基线.png",
        "index.csv",
        "render_summary.json",
    ]

    summary = _run(output_dir, symbol=symbol)

    assert {path.name for path in output_dir.iterdir()} == EXPECTED_ENTRIES
    charts = output_dir / "charts"
    assert {path.name for path in charts.iterdir()} == set(chart_files)

    index = pd.read_csv(charts / "index.csv")
    assert index["name"].tolist() == [symbol, symbol]
    assert index["image_path"].tolist() == chart_files[:2]

    render_summary = json.loads(
        (charts / "render_summary.json").read_text(encoding="utf-8")
    )
    assert render_summary["image_files"] == chart_files[:2]
    assert summary["output_files"] == OUTPUT_FILES + [
        f"charts/{filename}" for filename in chart_files
    ]


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


def test_chart_failure_preserves_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "overlay"
    output_dir.mkdir()
    marker = output_dir / "previous-success.txt"
    marker.write_text("previous", encoding="utf-8")

    def fail_render(**kwargs: object) -> None:
        raise RuntimeError("chart failed")

    monkeypatch.setattr(runner, "render_regime_comparison_charts", fail_render)

    with pytest.raises(RuntimeError, match="chart failed"):
        _run(output_dir, overwrite=True)

    assert marker.read_text(encoding="utf-8") == "previous"
    assert {path.name for path in output_dir.iterdir()} == {"previous-success.txt"}


def test_operation_and_staging_cleanup_failures_preserve_both_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "overlay"
    output_dir.mkdir()
    marker = output_dir / "previous-success.txt"
    marker.write_text("previous", encoding="utf-8")
    real_rmtree = runner.shutil.rmtree
    operation_error = RuntimeError("operation failed")
    cleanup_error = OSError("staging cleanup failed")

    def fail_render(**kwargs: object) -> None:
        raise operation_error

    def fail_staging_cleanup(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        candidate = Path(path)
        if (
            candidate.parent == tmp_path
            and candidate.name.startswith(".overlay.")
            and candidate.name.endswith(".staging")
        ):
            raise cleanup_error
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runner, "render_regime_comparison_charts", fail_render)
    monkeypatch.setattr(runner.shutil, "rmtree", fail_staging_cleanup)

    with pytest.raises(RuntimeError, match="operation.*staging cleanup") as error:
        _run(output_dir, overwrite=True)

    assert "RuntimeError: operation failed" in str(error.value)
    assert "OSError: staging cleanup failed" in str(error.value)
    assert error.value.original_error is operation_error
    assert error.value.recovery_error is cleanup_error
    assert error.value.__cause__ is cleanup_error
    assert marker.read_text(encoding="utf-8") == "previous"
    assert len(list(tmp_path.glob(".overlay.*.staging"))) == 1
    assert not list(tmp_path.glob(".overlay.*.backup"))


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
