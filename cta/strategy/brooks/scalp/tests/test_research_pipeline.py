from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.scalp.config import load_config
from cta.strategy.brooks.scalp.backtest_runner import (
    REQUIRED_OUTPUT_FILES,
    publish_backtest_artifacts,
)
from cta.strategy.brooks.scalp.metadata import BlockedMetadataError, MetadataBundle
from cta.strategy.brooks.scalp.online_runner import build_dry_run_runtime
from cta.strategy.brooks.scalp.research_pipeline import (
    _normalized_frames,
    _rollover_audit_passes,
    _source_files,
    _synchronize_roll_session_exclusions,
    _trim_pre_start_partition,
    _validate_trade_date_coverage,
    run_research_pipeline,
)


TZ = "Asia/Shanghai"


def test_source_files_include_only_nearest_partition_before_requested_start(
    tmp_path: Path,
) -> None:
    for name in (
        "2025-12-26.parquet",
        "2025-12-29.parquet",
        "2026-01-05.parquet",
        "2026-01-06.parquet",
    ):
        (tmp_path / name).touch()

    selected = _source_files(
        tmp_path,
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
    )

    assert [path.name for path in selected] == [
        "2025-12-29.parquet",
        "2026-01-05.parquet",
        "2026-01-06.parquet",
    ]


def test_trim_pre_start_partition_keeps_only_possible_night_session() -> None:
    frame = pd.DataFrame(
        {
            "trade_time": [
                "2025-12-29 09:01:00",
                "2025-12-29 15:00:00",
                "2025-12-29 21:01:00",
                "2025-12-29 23:00:00",
            ]
        }
    )

    result = _trim_pre_start_partition(
        frame,
        timestamp_column="trade_time",
        source_date=date(2025, 12, 29),
        start=date(2026, 1, 5),
    )

    assert result["trade_time"].tolist() == [
        "2025-12-29 21:01:00",
        "2025-12-29 23:00:00",
    ]


def _metadata(tmp_path: Path) -> MetadataBundle:
    known = "2025-01-01T00:00:00+08:00"
    frames = {
        "exchange_calendar.csv": pd.DataFrame(
            [
                {
                    "exchange": "SHFE",
                    "exchange_trade_date": day,
                    "is_open": True,
                    "prior_open_date": prior,
                    "next_open_date": following,
                    "night_session_start": "21:00:00",
                    "source": "SHFE-fixture",
                    "known_at": known,
                }
                for day, prior, following in (
                    ("2026-01-02", "2025-12-31", "2026-01-05"),
                    ("2026-01-05", "2026-01-02", "2026-01-06"),
                    ("2026-01-06", "2026-01-05", "2026-01-07"),
                    ("2026-01-07", "2026-01-06", "2026-01-08"),
                    ("2026-01-08", "2026-01-07", "2026-01-09"),
                    ("2026-01-09", "2026-01-08", "2026-01-12"),
                    ("2026-01-12", "2026-01-09", "2026-01-13"),
                    ("2026-01-13", "2026-01-12", "2026-01-14"),
                    ("2026-01-14", "2026-01-13", "2026-01-15"),
                )
            ]
        ),
        "contract_specs.csv": pd.DataFrame(
            [
                {
                    "contract_code": contract,
                    "root_symbol": root,
                    "exchange": "SHFE",
                    "contract_size": size,
                    "price_tick": tick,
                    "lot_step": 1,
                    "slippage_ticks_base": 1.0,
                    "last_trade_date": "2026-05-15",
                    "session_template_id": "SHFE_DAY",
                    "source": "SHFE-fixture",
                    "known_at": known,
                }
                for contract, root, size, tick in (
                    ("RB2605.SHF", "RB", 10.0, 1.0),
                    ("CU2605.SHF", "CU", 5.0, 10.0),
                )
            ]
        ),
        "contract_daily.csv": pd.DataFrame(
            [
                {
                    "contract_code": contract,
                    "exchange_trade_date": "2026-01-05",
                    "pre_settlement": price,
                    "settlement": price,
                    "limit_rate": 0.07,
                    "limit_up": price * 1.07,
                    "limit_down": price * 0.93,
                    "limit_rounding_rule": "fixture",
                    "source": "SHFE-fixture",
                    "source_url_or_file": "fixture.csv",
                    "known_at": known,
                    "fee_margin_schedule_id": schedule,
                }
                for contract, price, schedule in (
                    ("RB2605.SHF", 3_000.0, "RB_TEST"),
                    ("CU2605.SHF", 70_000.0, "CU_TEST"),
                )
            ]
        ),
        "fee_margin_schedule.csv": pd.DataFrame(
            [
                {
                    "schedule_id": schedule,
                    "root_symbol": root,
                    "contract_code": contract,
                    "effective_from": "2025-01-01T00:00:00+08:00",
                    "effective_to": "2027-01-01T00:00:00+08:00",
                    "margin_rate_long": 0.12,
                    "margin_rate_short": 0.12,
                    "open_fee_rate": 0.0001,
                    "close_fee_rate": 0.0001,
                    "close_today_fee_rate": 0.0002,
                    "fee_per_lot_open": 0.0,
                    "fee_per_lot_close": 0.0,
                    "fee_per_lot_close_today": 0.0,
                    "source": "SHFE-broker-fixture",
                    "source_url_or_file": "fixture.csv",
                    "known_at": known,
                }
                for schedule, root, contract in (
                    ("RB_TEST", "RB", "RB2605.SHF"),
                    ("CU_TEST", "CU", "CU2605.SHF"),
                )
            ]
        ),
    }
    manifest = {
        "schema_version": 1,
        "files": {
            filename: {"sha256": f"fixture-{filename}", "rows": len(frame)}
            for filename, frame in frames.items()
        },
    }
    return MetadataBundle(tmp_path / "meta", frames, manifest)


def _write_sources(root: Path) -> None:
    timestamps = pd.DatetimeIndex(
        [
            *pd.date_range("2026-01-05 09:01", "2026-01-05 10:15", freq="min"),
            *pd.date_range("2026-01-05 10:31", "2026-01-05 11:30", freq="min"),
            *pd.date_range("2026-01-05 13:31", "2026-01-05 15:00", freq="min"),
        ]
    )
    rb_price = pd.Series(range(len(timestamps)), dtype=float) + 3_000.0
    rb = pd.DataFrame(
        {
            "datetime": timestamps,
            "open": rb_price,
            "high": rb_price + 2.0,
            "low": rb_price - 2.0,
            "close": rb_price + 1.0,
            "volume": 100.0,
            "open_interest": 10_000.0,
            "turnover": 1_000_000.0,
            "ts_code": "RB2605.SHF",
            "symbol": "RB0",
            "exchange": "SHFE",
        }
    )
    cu_price = pd.Series(range(len(timestamps)), dtype=float) * 10.0 + 70_000.0
    cu = pd.DataFrame(
        {
            "ts_code": "CU2605.SHF",
            "trade_time": timestamps,
            "open": cu_price,
            "high": cu_price + 20.0,
            "low": cu_price - 20.0,
            "close": cu_price + 10.0,
            "vol": 100.0,
            "amount": 10_000_000.0,
            "oi": 10_000.0,
            "trade_date": "2026-01-05",
            "raw_symbol": "CU0.SHF",
            "mapping_symbol": "CU.SHF",
            "contract_code": "CU2605.SHF",
            "freq": "1min",
        }
    )
    (root / "RB").mkdir(parents=True)
    (root / "CU0.SHF").mkdir(parents=True)
    rb.to_parquet(root / "RB/2026-01-05.parquet", index=False)
    cu.to_parquet(root / "CU0.SHF/2026-01-05.parquet", index=False)


def test_roll_session_exclusion_is_synchronized_across_portfolio() -> None:
    rb = SimpleNamespace(
        excluded_roll_sessions=set(),
        portfolio_excluded_sessions=set(),
        bars_5m=pd.DataFrame(
            {"session_id": ["20180202:night", "20180202:day"], "close": [1.0, 2.0]}
        ),
        bars_30m=pd.DataFrame(
            {"session_id": ["20180202:night", "20180202:day"], "close": [1.0, 2.0]}
        ),
    )
    cu = SimpleNamespace(
        excluded_roll_sessions={"20180202:night"},
        portfolio_excluded_sessions=set(),
        bars_5m=pd.DataFrame(
            {"session_id": ["20180202:day"], "close": [2.0]}
        ),
        bars_30m=pd.DataFrame(
            {"session_id": ["20180202:day"], "close": [2.0]}
        ),
    )

    _synchronize_roll_session_exclusions([rb, cu])

    assert rb.portfolio_excluded_sessions == {"20180202:night"}
    assert cu.portfolio_excluded_sessions == {"20180202:night"}
    assert rb.bars_5m["session_id"].tolist() == ["20180202:day"]
    assert rb.bars_30m["session_id"].tolist() == ["20180202:day"]


def test_rollover_audit_distinguishes_october_contract_from_continuous_alias() -> None:
    assert _rollover_audit_passes(
        pd.DataFrame({"contract_code": ["RB1810.SHF", "CU2510.SHF"]})
    )
    assert not _rollover_audit_passes(
        pd.DataFrame({"contract_code": ["RB0.SHF", "CU2509.SHF"]})
    )


def test_normalized_frame_cache_avoids_reloading_source_files() -> None:
    cached = pd.DataFrame(
        {
            "session_id": ["20260105:day"],
            "contract_code": ["RB2605.SHF"],
        }
    )
    prepared = SimpleNamespace(
        normalized_frames=(cached,),
        files=(Path("/source-does-not-exist.parquet"),),
    )

    frames = list(_normalized_frames(prepared, collect_audit=False))

    assert len(frames) == 1
    assert frames[0].equals(cached)


def test_trade_date_coverage_scopes_calendar_to_contract_exchange() -> None:
    trade_date = date(2026, 1, 5)
    calendar = pd.DataFrame(
        [
            {
                "exchange": exchange,
                "exchange_trade_date": trade_date.isoformat(),
                "is_open": True,
                "night_session_start": None,
            }
            for exchange in ("SHFE", "DCE")
        ]
    )
    prepared = SimpleNamespace(
        root_symbol="RB",
        symbol="RB0.SHFE",
        start=trade_date,
        end=trade_date,
        contract_rows={"RB2605.SHF": pd.Series({"exchange": "SHFE"})},
        metadata=SimpleNamespace(frames={"exchange_calendar.csv": calendar}),
        observed_sessions={trade_date: {"day"}},
        instruments={
            "RB2605.SHF": SimpleNamespace(
                sessions=(SimpleNamespace(session_id="day"),)
            )
        },
    )

    _validate_trade_date_coverage(prepared)


def test_online_runtime_builds_two_symbol_shared_account_from_frozen_metadata(
    tmp_path: Path,
) -> None:
    runtime = build_dry_run_runtime(
        symbols=("RB0.SHFE", "CU0.SHFE"),
        contracts=("RB2605.SHF", "CU2605.SHF"),
        trade_date=date(2026, 1, 5),
        bundle=_metadata(tmp_path),
        config=load_config(),
    )

    snapshot = runtime.snapshot()
    assert snapshot.initial_equity == 200_000.0
    assert snapshot.marked_equity == 200_000.0
    assert set(snapshot.engines) == {"RB0.SHFE", "CU0.SHFE"}


def test_online_runtime_rejects_daily_metadata_known_after_session_open(
    tmp_path: Path,
) -> None:
    bundle = _metadata(tmp_path)
    bundle.frames["contract_daily.csv"].loc[
        bundle.frames["contract_daily.csv"]["contract_code"].eq("RB2605.SHF"),
        "known_at",
    ] = "2026-01-05T10:00:00+08:00"

    with pytest.raises(BlockedMetadataError, match="known_at is later than session open"):
        build_dry_run_runtime(
            symbols=("RB0.SHFE",),
            contracts=("RB2605.SHF",),
            trade_date=date(2026, 1, 5),
            bundle=bundle,
            config=load_config(),
        )


def test_dual_symbol_pipeline_streams_shared_equity_and_emits_auditable_artifacts(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "minute"
    _write_sources(data_root)

    artifacts = run_research_pipeline(
        symbols=("RB0.SHFE", "CU0.SHFE"),
        start="2026-01-05",
        end="2026-01-05",
        data_root=data_root,
        metadata=_metadata(tmp_path),
        config=load_config(),
        research_only=True,
    )

    assert artifacts.status == "INCONCLUSIVE"
    assert artifacts.source_audit["causal_audit_passed"] is True
    assert artifacts.source_audit["source_hash_stability_passed"] is True
    assert set(artifacts.contract_mapping["root_symbol"]) == {"RB", "CU"}
    assert len(artifacts.minute_equity) == 225
    assert artifacts.minute_equity["marked_equity"].eq(200_000.0).all()
    assert artifacts.daily_equity.to_dict("records") == [
        {"date": date(2026, 1, 5), "equity": 200_000.0}
    ]
    assert artifacts.trades.empty
    assert not artifacts.symbol_bars["RB"].empty
    assert not artifacts.symbol_bars["CU"].empty
    independent = artifacts.summary["independent_symbol_results"]
    assert set(independent) == {"RB0.SHFE", "CU0.SHFE"}
    assert {value["status"] for value in independent.values()} == {"INCONCLUSIVE"}
    assert {
        value["final_equity"] for value in independent.values()
    } == {200_000.0}
    assert set(artifacts.risk_score["symbols"]) == {"RB0.SHFE", "CU0.SHFE"}

    report = tmp_path / "report"
    publish_backtest_artifacts(artifacts, report, config=load_config())
    present = {
        str(path.relative_to(report)) for path in report.rglob("*") if path.is_file()
    }
    assert present == REQUIRED_OUTPUT_FILES


def test_requested_end_drops_next_trade_date_night_tail_before_assignment(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "minute"
    _write_sources(data_root)
    for relative, timestamp_column in (
        ("RB/2026-01-05.parquet", "datetime"),
        ("CU0.SHF/2026-01-05.parquet", "trade_time"),
    ):
        path = data_root / relative
        frame = pd.read_parquet(path)
        tail = frame.iloc[[-1]].copy()
        tail[timestamp_column] = pd.Timestamp("2026-01-05 21:01")
        pd.concat([frame, tail], ignore_index=True).to_parquet(path, index=False)
    metadata = _metadata(tmp_path)
    calendar = metadata.frames["exchange_calendar.csv"]
    metadata.frames["exchange_calendar.csv"] = calendar.loc[
        calendar["exchange_trade_date"].le("2026-01-05")
    ].reset_index(drop=True)

    artifacts = run_research_pipeline(
        symbols=("RB0.SHFE", "CU0.SHFE"),
        start="2026-01-05",
        end="2026-01-05",
        data_root=data_root,
        metadata=metadata,
        config=load_config(),
        research_only=True,
    )

    assert all(
        frame["bar_end"].max().hour == 15
        for frame in artifacts.symbol_bars.values()
    )
