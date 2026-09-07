from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.scalp.metadata import BlockedMetadataError
from cta.strategy.brooks.cycle_v1.backtest.data_loader import DiscoveredSymbol
from cta.strategy.brooks.cycle_v1.backtest import runner as runner_module
from cta.strategy.brooks.cycle_v1.backtest.runner import (
    build_reproduction_command,
    build_signal_series_provenance,
    build_parser,
    build_scan_summary,
    main,
    prepare_backtest_symbols,
    prepare_backtest_metadata,
    resolve_requested_symbols,
    validate_download_arguments,
)
from cta.strategy.brooks.cycle_v1.tests.test_cycle_scanner import _loaded_symbol


def _discovered() -> tuple[DiscoveredSymbol, ...]:
    return (
        DiscoveredSymbol("CU", "SHFE", "CU0.SHFE", Path("CU")),
        DiscoveredSymbol("RB", "SHFE", "RB0.SHFE", Path("RB")),
    )


def test_parser_has_independent_recommended_defaults() -> None:
    args = build_parser().parse_args(["--start", "2026-01-01", "--end", "2026-04-16"])

    assert args.symbols == ["all"]
    assert args.long_tf == "1hour"
    assert args.medium_tf == "30min"
    assert args.short_tf == "5min"
    assert not args.download_minute_data
    assert args.top_n == 0
    assert not args.include_ema_eligible
    assert args.metadata_cache_root.endswith("cycle_v1/meta_cache")


def test_parser_accepts_integrated_minute_download_options() -> None:
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-07-27",
            "--symbols",
            "LC",
            "--download-minute-data",
            "--top-n",
            "20",
            "--include-ema-eligible",
            "--ranking-csv",
            "ranking.csv",
            "--day-root",
            "day",
            "--download-rate-limit",
            "300",
            "--download-audit-output",
            "download.json",
        ]
    )

    assert args.download_minute_data
    assert args.top_n == 20
    assert args.include_ema_eligible
    assert args.ranking_csv == "ranking.csv"
    assert args.day_root == "day"
    assert args.download_rate_limit == 300
    assert args.download_audit_output == "download.json"
    assert validate_download_arguments(args) == ("LC",)


def test_reproduction_command_runs_from_repo_root_without_reusing_run_id() -> None:
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-07-27",
            "--symbols",
            "LC",
            "--download-minute-data",
            "--top-n",
            "2",
            "--include-ema-eligible",
            "--long-tf",
            "30min",
            "--medium-tf",
            "5min",
            "--short-tf",
            "1min",
            "--initial-equity",
            "200000",
            "--run-id",
            "manual_run",
        ]
    )

    command = build_reproduction_command(args)

    assert command["working_directory"] == str(runner_module.REPO_ROOT)
    assert command["argv"][:3] == [
        "python3",
        "-m",
        "cta.strategy.brooks.cycle_v1.backtest.runner",
    ]
    assert "--run-id" not in command["argv"]
    assert "--symbols" in command["argv"]
    assert "LC" in command["argv"]
    assert "--top-n" in command["argv"]
    assert "2" in command["argv"]
    assert "--include-ema-eligible" in command["argv"]
    assert command["shell_command"].startswith(
        f"cd {runner_module.shlex.quote(str(runner_module.REPO_ROOT))} && "
    )


def test_integrated_download_uses_prepared_execution_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared_root = tmp_path / "prepared"
    loaded_bundle = object()
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            metadata_root=prepared_root,
            to_audit_dict=lambda: {
                "status": "READY",
                "metadata_root": str(prepared_root),
            },
        )

    monkeypatch.setattr(runner_module, "prepare_execution_metadata", fake_prepare)
    monkeypatch.setattr(
        runner_module.MetadataBundle,
        "load",
        lambda root: loaded_bundle if Path(root) == prepared_root else pytest.fail(),
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-07-27",
            "--end",
            "2026-07-27",
            "--symbols",
            "I",
            "--download-minute-data",
            "--metadata-cache-root",
            str(tmp_path / "cache"),
        ]
    )
    selected = (
        DiscoveredSymbol("I", "DCE", "I0.DCE", tmp_path / "I"),
    )

    bundle, audit, gap = prepare_backtest_metadata(
        args,
        selected,
        start=pd.Timestamp("2026-07-27").date(),
        end=pd.Timestamp("2026-07-27").date(),
    )

    assert bundle is loaded_bundle
    assert audit["status"] == "READY"
    assert gap is None
    assert captured["symbols"] == selected
    assert captured["base_root"] == args.meta_root


def test_metadata_preparation_failure_is_reportable_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_bundle = object()
    monkeypatch.setattr(
        runner_module,
        "prepare_execution_metadata",
        lambda **_kwargs: (_ for _ in ()).throw(
            runner_module.MetadataBuildError("MARGIN_MISMATCH", "I2609.DCE")
        ),
    )
    monkeypatch.setattr(
        runner_module.MetadataBundle,
        "load",
        lambda _root: base_bundle,
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-07-27",
            "--end",
            "2026-07-27",
            "--symbols",
            "I",
            "--download-minute-data",
        ]
    )

    bundle, audit, gap = prepare_backtest_metadata(
        args,
        (DiscoveredSymbol("I", "DCE", "I0.DCE", tmp_path / "I"),),
        start=pd.Timestamp("2026-07-27").date(),
        end=pd.Timestamp("2026-07-27").date(),
    )

    assert bundle is base_bundle
    assert audit["status"] == "BLOCKED_METADATA"
    assert gap is not None
    assert gap["reason_code"] == "MARGIN_MISMATCH"


@pytest.mark.parametrize(
    "option",
    [
        ["--top-n", "20"],
        ["--top-n", "0"],
        ["--ranking-csv", "cta/feature/symbols_research_ranking.csv"],
        ["--download-rate-limit", "450"],
    ],
)
def test_download_only_options_require_download_switch(option: list[str]) -> None:
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-07-27",
            *option,
        ]
    )

    with pytest.raises(ValueError, match="require --download-minute-data"):
        validate_download_arguments(args)


def test_download_default_all_requires_ranked_selection_source() -> None:
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-07-27",
            "--download-minute-data",
        ]
    )

    with pytest.raises(ValueError, match="explicit symbol.*top-n.*EMA"):
        validate_download_arguments(args)

    args.top_n = 20
    assert validate_download_arguments(args) == ()

    args.symbols = ["ALL"]
    assert validate_download_arguments(args) == ()


def test_integrated_download_precedes_discovery_and_sets_backtest_union(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        events.append("download")
        captured.update(kwargs)
        return {
            "selection": {
                "explicit": ["LC"],
                "top_n": ["RB"],
                "ema_eligible": ["RB"],
                "selected": [
                    {"root_symbol": "LC", "exchange": "GFEX", "sources": ["explicit"]},
                    {"root_symbol": "RB", "exchange": "SHFE", "sources": ["top_n"]},
                ],
            },
            "selection_rejections": [],
            "downloaded": {"LC": 1, "RB": 1},
            "download_errors": [],
        }

    def fake_discover(_data_root):
        events.append("discover")
        return (
            DiscoveredSymbol("RB", "SHFE", "RB0.SHFE", tmp_path / "RB"),
            DiscoveredSymbol("LC", "GFEX", "LC0.GFEX", tmp_path / "LC"),
        )

    monkeypatch.setattr(runner_module, "prepare_minute_data", fake_prepare)
    monkeypatch.setattr(runner_module, "discover_symbols", fake_discover)
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-06",
            "--symbols",
            "LC",
            "--download-minute-data",
            "--top-n",
            "1",
            "--include-ema-eligible",
            "--data-root",
            str(tmp_path),
        ]
    )

    discovered, selected, audit = prepare_backtest_symbols(
        args,
        start=pd.Timestamp("2026-01-05").date(),
        end=pd.Timestamp("2026-01-06").date(),
    )

    assert events == ["download", "discover"]
    assert [item.root_symbol for item in discovered] == ["RB", "LC"]
    assert [item.root_symbol for item in selected] == ["LC", "RB"]
    assert captured["explicit"] == ("LC",)
    assert captured["start"] == pd.Timestamp("2026-01-05").date()
    assert captured["end"] == pd.Timestamp("2026-01-06").date()
    assert captured["data_root"] == str(tmp_path)
    assert audit["enabled"] is True
    assert audit["downloaded"] == {"LC": 1, "RB": 1}


def test_integrated_download_recovers_symbol_from_audited_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oi_directory = tmp_path / "OI"
    oi_directory.mkdir()
    oi_file = oi_directory / "20260105.parquet"
    oi_file.touch()
    monkeypatch.setattr(
        runner_module,
        "prepare_minute_data",
        lambda **_kwargs: {
            "selection": {
                "explicit": ["LC", "OI"],
                "selected": [
                    {"root_symbol": "LC", "exchange": "GFEX"},
                    {"root_symbol": "OI", "exchange": "CZCE"},
                ],
            },
            "selection_rejections": [],
            "download_errors": [],
            "files": [
                {
                    "status": "skipped",
                    "root_symbol": "OI",
                    "contract_code": "OI601.ZCE",
                    "mapping_exchange": "ZCE",
                    "path": str(oi_file),
                    "sha256": "audited",
                }
            ],
        },
    )
    monkeypatch.setattr(
        runner_module,
        "discover_symbols",
        lambda _data_root: (
            DiscoveredSymbol("LC", "GFEX", "LC0.GFEX", tmp_path / "LC"),
        ),
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-06",
            "--symbols",
            "LC",
            "OI",
            "--download-minute-data",
            "--data-root",
            str(tmp_path),
        ]
    )

    discovered, selected, audit = prepare_backtest_symbols(
        args,
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
    )

    assert [item.root_symbol for item in discovered] == ["LC", "OI"]
    assert [item.root_symbol for item in selected] == ["LC", "OI"]
    assert selected[1].source_directory == oi_directory.resolve()
    assert audit["symbols_recovered_from_file_audit"] == ["OI.CZCE"]


def test_integrated_download_rejects_missing_selected_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_module,
        "prepare_minute_data",
        lambda **_kwargs: {
            "selection": {
                "selected": [
                    {"root_symbol": "LC", "exchange": "GFEX", "sources": ["explicit"]},
                    {"root_symbol": "RB", "exchange": "SHFE", "sources": ["top_n"]},
                ]
            },
            "selection_rejections": [],
            "download_errors": [],
        },
    )
    monkeypatch.setattr(
        runner_module,
        "discover_symbols",
        lambda _data_root: (
            DiscoveredSymbol("LC", "GFEX", "LC0.GFEX", tmp_path / "LC"),
        ),
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-06",
            "--symbols",
            "LC",
            "--download-minute-data",
            "--top-n",
            "1",
        ]
    )

    with pytest.raises(ValueError, match="missing selected minute data.*RB"):
        prepare_backtest_symbols(
            args,
            start=pd.Timestamp("2026-01-05").date(),
            end=pd.Timestamp("2026-01-06").date(),
        )


def test_allow_missing_symbols_records_scoped_diagnoses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_module,
        "prepare_minute_data",
        lambda **_kwargs: {
            "selection": {
                "explicit": ["LC", "OI", "MA"],
                "selected": [
                    {"root_symbol": "LC", "exchange": "GFEX"},
                    {"root_symbol": "OI", "exchange": "CZCE"},
                    {"root_symbol": "MA", "exchange": "CZCE"},
                ],
            },
            "selection_rejections": [],
            "download_errors": [],
            "requested_dates": {"OI": 117, "MA": 117},
            "downloaded": {"OI": 0, "MA": 0},
            "skipped": {"OI": 117, "MA": 117},
            "empty": {"OI": 0, "MA": 0},
            "files": [],
        },
    )
    monkeypatch.setattr(
        runner_module,
        "discover_symbols",
        lambda _data_root: (
            DiscoveredSymbol("LC", "GFEX", "LC0.GFEX", tmp_path / "LC"),
        ),
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-06",
            "--symbols",
            "LC",
            "OI",
            "MA",
            "--download-minute-data",
            "--data-root",
            str(tmp_path),
        ]
    )
    args.allow_missing_symbols = True

    _, selected, audit = prepare_backtest_symbols(
        args,
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
    )

    assert [item.root_symbol for item in selected] == ["LC"]
    diagnoses = audit["missing_symbol_diagnoses"]
    assert "OI.CZCE" in diagnoses["OI.CZCE"]
    assert "MA.CZCE" not in diagnoses["OI.CZCE"]
    assert "MA.CZCE" in diagnoses["MA.CZCE"]
    assert "OI.CZCE" not in diagnoses["MA.CZCE"]


def test_integrated_download_rejects_dropped_explicit_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_module,
        "prepare_minute_data",
        lambda **_kwargs: {
            "selection": {
                "explicit": ["LC"],
                "selected": [
                    {"root_symbol": "RB", "exchange": "SHFE", "sources": ["top_n"]}
                ],
            },
            "selection_rejections": [
                {
                    "root_symbol": "LC",
                    "stage": "contract_reference",
                    "reason_code": "CONTRACT_REFERENCE_ERROR",
                    "detail": "api unavailable",
                }
            ],
            "download_errors": [],
        },
    )
    monkeypatch.setattr(
        runner_module,
        "discover_symbols",
        lambda _data_root: pytest.fail("discovery must not run"),
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-06",
            "--symbols",
            "LC",
            "--download-minute-data",
            "--top-n",
            "1",
        ]
    )

    with pytest.raises(ValueError, match="explicit minute symbols were rejected.*LC"):
        prepare_backtest_symbols(
            args,
            start=pd.Timestamp("2026-01-05").date(),
            end=pd.Timestamp("2026-01-06").date(),
        )


def test_run_clamps_warmup_without_skipping_requested_dates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "HC"
    source.mkdir()
    (source / "2026-01-05.parquet").touch()
    selected = (
        DiscoveredSymbol("HC", "SHFE", "HC0.SHFE", source),
    )
    captured: dict[str, object] = {}
    events: list[str] = []

    monkeypatch.setattr(
        runner_module,
        "prepare_backtest_symbols",
        lambda *_args, **_kwargs: (selected, selected, {"enabled": False}),
    )
    monkeypatch.setattr(
        runner_module,
        "prepare_backtest_metadata",
        lambda *_args, **_kwargs: (object(), {"enabled": False}, None),
    )

    def capture_load(**kwargs):
        captured.update(kwargs)
        raise ValueError("stop after capturing load boundary")

    monkeypatch.setattr(runner_module, "load_normalized_symbol", capture_load)

    def capture_report(path, **_kwargs):
        events.append("report")
        return path

    def capture_charts(report_dir, *, data_root):
        events.append("charts")
        captured["chart_report_dir"] = report_dir
        captured["chart_data_root"] = data_root
        return pd.DataFrame()

    monkeypatch.setattr(runner_module, "write_report_bundle", capture_report)
    monkeypatch.setattr(
        runner_module,
        "generate_candidate_charts",
        capture_charts,
        raising=False,
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-07-27",
            "--symbols",
            "HC",
            "--output-root",
            str(tmp_path / "reports"),
            "--run-id",
            "warmup_test",
        ]
    )

    summary, _ = runner_module.run_from_args(args)

    assert captured["start"] == date(2026, 1, 1)
    assert summary["warmup_start"] < "2026-01-01"
    assert summary["effective_warmup_starts"] == {"HC": "2026-01-01"}
    assert events == ["report", "charts"]
    assert captured["chart_report_dir"] == tmp_path / "reports" / "warmup_test"
    assert captured["chart_data_root"] == Path(args.data_root)


def test_run_downloads_exact_requested_interval_and_loads_local_warmup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "AG"
    source.mkdir()
    (source / "2025-08-30.parquet").touch()
    selected = (
        DiscoveredSymbol("AG", "SHFE", "AG0.SHFE", source),
    )
    captured: dict[str, dict[str, object]] = {}

    def capture_symbols(*_args, **kwargs):
        captured["symbols"] = kwargs
        return selected, selected, {"enabled": True}

    def capture_metadata(*_args, **kwargs):
        captured["metadata"] = kwargs
        return object(), {"enabled": True}, None

    monkeypatch.setattr(runner_module, "prepare_backtest_symbols", capture_symbols)
    monkeypatch.setattr(runner_module, "prepare_backtest_metadata", capture_metadata)
    monkeypatch.setattr(
        runner_module,
        "load_normalized_symbol",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("stop after boundaries")),
    )
    monkeypatch.setattr(
        runner_module,
        "write_report_bundle",
        lambda path, **_kwargs: path,
    )
    monkeypatch.setattr(
        runner_module,
        "generate_candidate_charts",
        lambda *_args, **_kwargs: pd.DataFrame(),
        raising=False,
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-02-05",
            "--symbols",
            "AG",
            "--download-minute-data",
            "--long-tf",
            "30min",
            "--medium-tf",
            "5min",
            "--short-tf",
            "1min",
            "--output-root",
            str(tmp_path / "reports"),
            "--run-id",
            "warmup_download_test",
        ]
    )

    summary, _ = runner_module.run_from_args(args)

    assert captured["symbols"] == {
        "start": date(2026, 1, 1),
        "end": date(2026, 2, 5),
    }
    assert captured["metadata"]["start"] == date(2025, 8, 30)
    assert summary["requested_start"] == "2026-01-01"
    assert summary["effective_warmup_starts"] == {"AG": "2025-08-30"}


def test_signal_series_provenance_counts_observed_roll_adjustments() -> None:
    loaded = _loaded_symbol()
    roll_at = len(loaded.minute_bars) // 2
    loaded.minute_bars.loc[roll_at:, "adjustment_offset"] = -20.0
    loaded.minute_bars.loc[roll_at:, "adjustment_version"] = "RB-ROLL-V1"

    provenance = build_signal_series_provenance((loaded,))

    assert provenance["signal_price_series"] == "PIT_PRE_SETTLEMENT_ADDITIVE_V1"
    assert provenance["signal_adjustment_count"] == 1
    assert provenance["signal_adjustment_versions"] == {"RB": ["RB-ROLL-V1"]}


def test_symbol_resolution_supports_all_and_rejects_duplicates() -> None:
    assert resolve_requested_symbols(["all"], _discovered()) == _discovered()
    assert [
        item.root_symbol
        for item in resolve_requested_symbols(["RB0.SHFE", "CU"], _discovered())
    ] == ["RB", "CU"]

    try:
        resolve_requested_symbols(["RB", "RB0.SHFE"], _discovered())
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("duplicate symbols must be rejected")


def test_scan_summary_preserves_fail_closed_official_contract() -> None:
    gaps = [{"root_symbol": "RB", "field": "lifecycle", "reason": "missing"}]
    summary = build_scan_summary(
        discovered_count=2,
        requested_count=1,
        loaded_count=1,
        universe_daily=pd.DataFrame({"eligible": [False, True]}),
        cycle_snapshots=pd.DataFrame({"cycle": ["TRADING_RANGE"]}),
        metadata_gaps=gaps,
    )

    assert summary["requested_interval_status"] == "BLOCKED_METADATA"
    assert summary["official_performance"] is None
    assert summary["primary_curve_for_requested_interval"] is None
    assert summary["signal_only_label"] == "SIGNAL_ONLY_DIAGNOSTIC"
    assert summary["minute_data_update"] == {"enabled": False}


def test_scan_summary_publishes_performance_after_replay_and_coverage() -> None:
    performance = {"trade_count": 2, "annualized_return": 0.12}
    curve = [{"date": "2026-01-05", "equity": 200_100.0}]
    minute_audit = {"enabled": True, "downloaded": {"RB": 1}}

    summary = build_scan_summary(
        discovered_count=2,
        requested_count=2,
        loaded_count=2,
        universe_daily=pd.DataFrame({"eligible": [True, True]}),
        cycle_snapshots=pd.DataFrame({"cycle": ["BULL_TIGHT_CHANNEL"]}),
        metadata_gaps=[],
        performance=performance,
        primary_curve=curve,
        execution_funnel={
            "candidates": 3,
            "eligible_plans": 2,
            "orders": 2,
            "fills": 4,
            "round_trips": 2,
        },
        minute_data_update=minute_audit,
    )

    assert summary["requested_interval_status"] == "COMPLETE"
    assert summary["official_performance"] == performance
    assert summary["primary_curve_for_requested_interval"] == curve
    assert summary["funnel"]["round_trips"] == 2
    assert summary["minute_data_update"] == minute_audit


def test_main_reports_invalid_timeframe_without_traceback(capsys) -> None:
    code = main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-04-16",
            "--long-tf",
            "1d",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["status"] == "INVALID_ARGUMENT"


def test_invalid_run_id_is_rejected_before_minute_download(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    download_called = False

    def unexpected_download(**_kwargs):
        nonlocal download_called
        download_called = True
        raise ValueError("download should not run")

    monkeypatch.setattr(runner_module, "prepare_minute_data", unexpected_download)

    code = main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-07-27",
            "--symbols",
            "LC",
            "--download-minute-data",
            "--run-id",
            "../bad",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert not download_called
    assert payload["status"] == "INVALID_ARGUMENT"
    assert "run-id must be one plain directory name" in payload["reason"]


def test_main_preserves_blocked_metadata_status(monkeypatch, capsys) -> None:
    def blocked(_args):
        raise BlockedMetadataError("missing manifest")

    monkeypatch.setattr(runner_module, "run_from_args", blocked)

    code = main(["--start", "2026-01-01", "--end", "2026-04-16"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["status"] == "BLOCKED_METADATA"
    assert "missing manifest" in payload["reason"]
