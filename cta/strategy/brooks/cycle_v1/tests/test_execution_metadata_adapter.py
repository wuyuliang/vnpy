from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest.data_loader import LoadedSymbol
from cta.strategy.brooks.cycle_v1.backtest.execution_metadata import (
    build_execution_metadata_store,
    merge_canonical_frames,
)
from cta.strategy.brooks.cycle_v1.backtest.replay import (
    build_metadata_coverage_requests,
)
from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)


def _bundle() -> SimpleNamespace:
    return SimpleNamespace(
        frames={
            "contract_specs.csv": pd.DataFrame(
                {
                    "contract_code": ["RB2605.SHF"],
                    "root_symbol": ["RB"],
                    "exchange": ["SHFE"],
                    "contract_size": [10.0],
                    "price_tick": [1.0],
                    "lot_step": [1],
                    "slippage_ticks_base": [1.0],
                    "last_trade_date": ["2026-05-15"],
                    "source": ["SHFE_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC"],
                    "known_at": ["2025-05-16T00:00:00+08:00"],
                }
            ),
            "contract_daily.csv": pd.DataFrame(
                {
                    "contract_code": ["RB2605.SHF"],
                    "exchange_trade_date": ["2026-01-05"],
                    "pre_settlement": [100.0],
                    "limit_up": [110.0],
                    "limit_down": [90.0],
                    "source": ["SHFE_KX_JS_RULE_DERIVED"],
                    "source_url_or_file": ["kx20260105|js20251231"],
                    "known_at": ["2026-01-05T09:00:00+08:00"],
                    "fee_margin_schedule_id": ["RB-20260105"],
                }
            ),
            "fee_margin_schedule.csv": pd.DataFrame(
                {
                    "schedule_id": ["RB-20260105"],
                    "root_symbol": ["RB"],
                    "contract_code": ["RB2605.SHF"],
                    "effective_from": ["2026-01-05T09:00:00+08:00"],
                    "effective_to": [pd.NA],
                    "margin_rate_long": [0.10],
                    "margin_rate_short": [0.11],
                    "open_fee_rate": [0.001],
                    "close_fee_rate": [0.001],
                    "close_today_fee_rate": [0.002],
                    "fee_per_lot_open": [1.0],
                    "fee_per_lot_close": [1.0],
                    "fee_per_lot_close_today": [2.0],
                    "source": ["SHFE_JS_OFFICIAL"],
                    "source_url_or_file": ["js20251231"],
                    "known_at": ["2026-01-05T09:00:00+08:00"],
                }
            ),
        }
    )


def _loaded() -> LoadedSymbol:
    return LoadedSymbol(
        root_symbol="RB",
        exchange="SHFE",
        vt_symbol="RB0.SHFE",
        minute_bars=pd.DataFrame(
            {
                "bar_end": pd.date_range(
                    "2026-01-05 09:01", periods=2, freq="min", tz="Asia/Shanghai"
                ),
                "contract_code": ["RB2605.SHF", "RB2605.SHF"],
                "exchange_trade_date": [date(2026, 1, 5), date(2026, 1, 5)],
            }
        ),
        sessions=(
            SessionSpec(
                session_id="day",
                is_night=False,
                segments=(SessionSegment("day", time(9), time(15), time(9)),),
            ),
        ),
        source_files=(),
    )


def _request(trade_date: date = date(2026, 1, 5)) -> dict[str, object]:
    return {
        "root_symbol": "RB",
        "exchange": "SHFE",
        "contract_code": "RB2605.SHF",
        "exchange_trade_date": trade_date,
        "gateway": "CYCLE_V1_BAR_BACKTEST",
        "order_types": ("STOP",),
        "decision_asof": pd.Timestamp(
            f"{trade_date.isoformat()} 09:05", tz="Asia/Shanghai"
        ).to_pydatetime(),
        "order_event": pd.Timestamp(
            f"{trade_date.isoformat()} 09:06", tz="Asia/Shanghai"
        ).to_pydatetime(),
    }


def test_adapter_builds_visible_actual_contract_snapshot_with_stressed_costs() -> None:
    store = build_execution_metadata_store(
        _bundle(),
        (_loaded(),),
        cost_stress_mult=2.0,
    )

    snapshot = store.execution_snapshot(**_request())

    assert snapshot.lifecycle.contract_code == "RB2605.SHF"
    assert snapshot.lifecycle.listed_on == date(2025, 5, 16)
    assert snapshot.lifecycle.last_trade_date == date(2026, 5, 15)
    assert snapshot.status.tradable
    assert snapshot.capability["STOP"].gateway == "CYCLE_V1_BAR_BACKTEST"
    assert snapshot.daily.margin_rate_long == 0.10
    assert snapshot.daily.margin_rate_short == 0.11
    assert snapshot.stressed_round_trip_fee_cash == 12.0
    assert snapshot.open_fee_rate == pytest.approx(0.001)
    assert snapshot.close_fee_rate == pytest.approx(0.001)
    assert snapshot.close_today_fee_rate == pytest.approx(0.002)
    assert snapshot.fee_per_lot_open == pytest.approx(1.0)
    assert snapshot.fee_per_lot_close == pytest.approx(1.0)
    assert snapshot.fee_per_lot_close_today == pytest.approx(2.0)
    assert snapshot.fee_stress_multiplier == pytest.approx(2.0)
    assert snapshot.fee_source == "SHFE_JS_OFFICIAL|js20251231"
    assert snapshot.fee_effective_from == pd.Timestamp(
        "2026-01-05T09:00:00+08:00"
    ).to_pydatetime()
    assert snapshot.fee_known_at == pd.Timestamp(
        "2026-01-05T09:00:00+08:00"
    ).to_pydatetime()
    assert snapshot.stressed_entry_slippage_ticks == 2.0
    assert snapshot.stressed_round_trip_slippage_ticks == 4.0
    assert len(snapshot.metadata_hash) == 64


def test_adapter_accepts_fee_visible_at_its_session_effective_time() -> None:
    bundle = _bundle()
    bundle.frames["contract_daily.csv"].loc[:, "known_at"] = (
        "2025-12-31T15:28:20+08:00"
    )

    store = build_execution_metadata_store(
        bundle,
        (_loaded(),),
        cost_stress_mult=2.0,
    )

    snapshot = store.execution_snapshot(**_request())
    assert snapshot.daily.fee_schedule_id == "RB-20260105"
    assert snapshot.stressed_round_trip_fee_cash == 12.0


def test_adapter_deduplicates_identical_root_mechanics_on_roll_day() -> None:
    bundle = _bundle()
    second_spec = bundle.frames["contract_specs.csv"].iloc[0].copy()
    second_spec["contract_code"] = "RB2610.SHF"
    second_spec["known_at"] = "2025-06-16T00:00:00+08:00"
    bundle.frames["contract_specs.csv"] = pd.concat(
        [bundle.frames["contract_specs.csv"], second_spec.to_frame().T],
        ignore_index=True,
    )
    second_daily = bundle.frames["contract_daily.csv"].iloc[0].copy()
    second_daily["contract_code"] = "RB2610.SHF"
    second_daily["fee_margin_schedule_id"] = "RB2610-20260105"
    bundle.frames["contract_daily.csv"] = pd.concat(
        [bundle.frames["contract_daily.csv"], second_daily.to_frame().T],
        ignore_index=True,
    )
    second_fee = bundle.frames["fee_margin_schedule.csv"].iloc[0].copy()
    second_fee["schedule_id"] = "RB2610-20260105"
    second_fee["contract_code"] = "RB2610.SHF"
    bundle.frames["fee_margin_schedule.csv"] = pd.concat(
        [bundle.frames["fee_margin_schedule.csv"], second_fee.to_frame().T],
        ignore_index=True,
    )
    loaded = _loaded()
    loaded = LoadedSymbol(
        root_symbol=loaded.root_symbol,
        exchange=loaded.exchange,
        vt_symbol=loaded.vt_symbol,
        minute_bars=loaded.minute_bars.assign(
            contract_code=["RB2605.SHF", "RB2610.SHF"]
        ),
        sessions=loaded.sessions,
        source_files=(),
    )

    store = build_execution_metadata_store(
        bundle,
        (loaded,),
        cost_stress_mult=2.0,
    )

    for contract_code in ("RB2605.SHF", "RB2610.SHF"):
        snapshot = store.execution_snapshot(
            **{**_request(), "contract_code": contract_code}
        )
        assert snapshot.contract_size == 10.0
        assert snapshot.price_tick == 1.0
        assert snapshot.instrument.known_at == pd.Timestamp(
            "2025-06-16T00:00:00+08:00"
        ).to_pydatetime()


def test_adapter_uses_effective_dated_daily_contract_mechanics() -> None:
    bundle = _bundle()
    bundle.frames["contract_specs.csv"].loc[:, "contract_code"] = "EC2606.INE"
    bundle.frames["contract_specs.csv"].loc[:, "root_symbol"] = "EC"
    bundle.frames["contract_specs.csv"].loc[:, "exchange"] = "INE"
    bundle.frames["contract_specs.csv"].loc[:, "contract_size"] = 1.0
    bundle.frames["contract_specs.csv"].loc[:, "price_tick"] = 0.5
    bundle.frames["contract_specs.csv"].loc[:, "last_trade_date"] = "2026-06-29"
    bundle.frames["contract_daily.csv"] = pd.DataFrame(
        {
            "contract_code": ["EC2606.INE", "EC2606.INE"],
            "exchange_trade_date": ["2026-05-08", "2026-05-11"],
            "pre_settlement": [1500.1, 1500.5],
            "limit_up": [1650.1, 1650.5],
            "limit_down": [1350.1, 1350.5],
            "contract_size": [50.0, 50.0],
            "price_tick": [0.1, 0.5],
            "mechanics_source": ["INE_OFFICIAL_EC", "INE_OFFICIAL_EC"],
            "mechanics_known_at": [
                "2023-08-18T00:00:00+08:00",
                "2026-01-16T00:00:00+08:00",
            ],
            "source": ["INE_DAILY", "INE_DAILY"],
            "source_url_or_file": ["ine:20260508", "ine:20260511"],
            "known_at": [
                "2026-05-08T09:00:00+08:00",
                "2026-05-11T09:00:00+08:00",
            ],
            "fee_margin_schedule_id": ["EC-20260508", "EC-20260511"],
        }
    )
    fee = bundle.frames["fee_margin_schedule.csv"].iloc[0].to_dict()
    bundle.frames["fee_margin_schedule.csv"] = pd.DataFrame(
        [
            {
                **fee,
                "schedule_id": "EC-20260508",
                "contract_code": "EC2606.INE",
                "effective_from": "2026-05-08T09:00:00+08:00",
                "known_at": "2026-05-08T09:00:00+08:00",
            },
            {
                **fee,
                "schedule_id": "EC-20260511",
                "contract_code": "EC2606.INE",
                "effective_from": "2026-05-11T09:00:00+08:00",
                "known_at": "2026-05-11T09:00:00+08:00",
            },
        ]
    )
    loaded = _loaded()
    loaded = LoadedSymbol(
        root_symbol="EC",
        exchange="INE",
        vt_symbol="EC0.INE",
        minute_bars=pd.DataFrame(
            {
                "bar_end": pd.to_datetime(
                    ["2026-05-08 09:01", "2026-05-11 09:01"]
                ).tz_localize("Asia/Shanghai"),
                "contract_code": ["EC2606.INE", "EC2606.INE"],
                "exchange_trade_date": [date(2026, 5, 8), date(2026, 5, 11)],
            }
        ),
        sessions=loaded.sessions,
        source_files=(),
    )

    store = build_execution_metadata_store(bundle, (loaded,), cost_stress_mult=2.0)
    before = store.execution_snapshot(
        **{
            **_request(date(2026, 5, 8)),
            "root_symbol": "EC",
            "exchange": "INE",
            "contract_code": "EC2606.INE",
        }
    )
    after = store.execution_snapshot(
        **{
            **_request(date(2026, 5, 11)),
            "root_symbol": "EC",
            "exchange": "INE",
            "contract_code": "EC2606.INE",
        }
    )

    assert before.contract_size == pytest.approx(50.0)
    assert before.price_tick == pytest.approx(0.1)
    assert after.contract_size == pytest.approx(50.0)
    assert after.price_tick == pytest.approx(0.5)


def test_adapter_coverage_blocks_missing_contract_day_instead_of_falling_back() -> None:
    store = build_execution_metadata_store(
        _bundle(),
        (_loaded(),),
        cost_stress_mult=2.0,
    )

    coverage = store.coverage_report(
        [_request(), _request(date(2026, 1, 6))]
    )

    first = coverage.loc[coverage["request_index"].eq(0)]
    second = coverage.loc[coverage["request_index"].eq(1)]
    assert first["covered"].eq(1).all()
    assert second["covered"].eq(0).any()
    assert set(second.loc[second["covered"].eq(0), "reason_code"]) == {
        "BLOCKED_METADATA",
        "BLOCKED_TRADING_STATUS",
    }


def test_replay_coverage_requests_are_unique_per_actual_contract_day() -> None:
    requests = build_metadata_coverage_requests(
        (_loaded(),),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
    )

    assert len(requests) == 1
    assert requests[0]["contract_code"] == "RB2605.SHF"
    assert requests[0]["gateway"] == "CYCLE_V1_BAR_BACKTEST"
    assert requests[0]["decision_asof"] < requests[0]["order_event"]


def test_metadata_merge_preserves_observed_sessions_over_generic_calendar() -> None:
    base = {
        "exchange_calendar.csv": pd.DataFrame(
            {
                "exchange": ["SHFE", "SHFE"],
                "exchange_trade_date": ["2026-04-17", "2026-04-20"],
                "night_session_start": [pd.NA, pd.NA],
                "source": [
                    "SHFE_CALENDAR_VIA_TUSHARE_AND_OBSERVED_SESSIONS",
                    "SHFE_CALENDAR_VIA_TUSHARE_AND_OBSERVED_SESSIONS",
                ],
            }
        ),
        "contract_specs.csv": pd.DataFrame(
            {"contract_code": ["RB2610.SHF"], "price_tick": [1.0]}
        ),
        "contract_daily.csv": pd.DataFrame(
            {
                "contract_code": ["RB2610.SHF"],
                "exchange_trade_date": ["2026-04-17"],
                "pre_settlement": [3100.0],
                "fee_margin_schedule_id": ["RB-20260417"],
            }
        ),
        "fee_margin_schedule.csv": pd.DataFrame(
            {
                "schedule_id": ["RB-20260417"],
                "contract_code": ["RB2610.SHF"],
                "effective_from": ["2026-04-16T21:00:00+08:00"],
                "open_fee_rate": [0.0001],
            }
        ),
    }
    extension = {name: frame.copy() for name, frame in base.items()}
    extension["exchange_calendar.csv"]["night_session_start"] = "21:00:00"
    extension["exchange_calendar.csv"]["source"] = "SHFE_CALENDAR_VIA_TUSHARE_TRADE_CAL"

    merged = merge_canonical_frames(
        base,
        extension,
        start=date(2026, 1, 5),
        end=date(2026, 7, 27),
    )

    calendar = merged["exchange_calendar.csv"]
    assert calendar["night_session_start"].isna().all()
    assert set(calendar["source"]) == {
        "SHFE_CALENDAR_VIA_TUSHARE_AND_OBSERVED_SESSIONS"
    }


def test_metadata_merge_blocks_mechanics_conflict() -> None:
    base = {
        "exchange_calendar.csv": pd.DataFrame(
            {
                "exchange": ["SHFE"],
                "exchange_trade_date": ["2026-04-17"],
                "source": ["SHFE_CALENDAR_VIA_TUSHARE_TRADE_CAL"],
            }
        ),
        "contract_specs.csv": pd.DataFrame(
            {"contract_code": ["RB2610.SHF"], "price_tick": [1.0]}
        ),
        "contract_daily.csv": pd.DataFrame(
            {
                "contract_code": ["RB2610.SHF"],
                "exchange_trade_date": ["2026-04-17"],
                "pre_settlement": [3100.0],
                "fee_margin_schedule_id": ["RB-20260417"],
            }
        ),
        "fee_margin_schedule.csv": pd.DataFrame(
            {
                "schedule_id": ["RB-20260417"],
                "contract_code": ["RB2610.SHF"],
                "effective_from": ["2026-04-16T21:00:00+08:00"],
                "open_fee_rate": [0.0001],
            }
        ),
    }
    extension = {name: frame.copy() for name, frame in base.items()}
    extension["contract_daily.csv"].loc[0, "pre_settlement"] = 3101.0

    with pytest.raises(ValueError, match="conflicting contract_daily.csv"):
        merge_canonical_frames(
            base,
            extension,
            start=date(2026, 1, 5),
            end=date(2026, 7, 27),
        )


def test_metadata_merge_replaces_legacy_daily_and_linked_fee_with_pit_rows() -> None:
    base = {
        "exchange_calendar.csv": pd.DataFrame(
            {
                "exchange": ["SHFE"],
                "exchange_trade_date": ["2026-01-12"],
            }
        ),
        "contract_specs.csv": pd.DataFrame(
            {"contract_code": ["RB2605.SHF"], "price_tick": [1.0]}
        ),
        "contract_daily.csv": pd.DataFrame(
            {
                "contract_code": ["RB2605.SHF"],
                "exchange_trade_date": ["2026-01-12"],
                "pre_settlement": [3146.0],
                "fee_margin_schedule_id": ["SHFE-RB2605-20260112"],
                "known_at": ["2026-01-12T09:00:00+08:00"],
            }
        ),
        "fee_margin_schedule.csv": pd.DataFrame(
            {
                "schedule_id": ["SHFE-RB2605-20260112"],
                "contract_code": ["RB2605.SHF"],
                "effective_from": ["2026-01-12T09:00:00+08:00"],
                "known_at": ["2026-01-12T09:00:00+08:00"],
            }
        ),
    }
    extension = {name: frame.copy() for name, frame in base.items()}
    extension["contract_daily.csv"] = extension["contract_daily.csv"].assign(
        known_at="2026-01-09T16:00:00+08:00",
        pre_settlement_known_at="2026-01-09T15:30:00+08:00",
        settlement_known_at="2026-01-12T15:30:00+08:00",
        contract_size=10.0,
        price_tick=1.0,
        mechanics_source="SHFE_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC",
        mechanics_known_at="2025-05-16T00:00:00+08:00",
    )
    extension["fee_margin_schedule.csv"].loc[:, "known_at"] = (
        "2026-01-09T16:00:00+08:00"
    )
    extension["fee_margin_schedule.csv"].loc[:, "effective_from"] = (
        "2026-01-09T21:00:00+08:00"
    )

    merged = merge_canonical_frames(
        base,
        extension,
        start=date(2026, 1, 5),
        end=date(2026, 7, 27),
    )

    assert (
        merged["contract_daily.csv"].iloc[0]["known_at"]
        == "2026-01-09T16:00:00+08:00"
    )
    assert merged["contract_daily.csv"].iloc[0]["contract_size"] == 10.0
    assert (
        merged["fee_margin_schedule.csv"].iloc[0]["known_at"]
        == "2026-01-09T16:00:00+08:00"
    )
    assert len(merged["fee_margin_schedule.csv"]) == 1
    assert (
        merged["fee_margin_schedule.csv"].iloc[0]["effective_from"]
        == "2026-01-09T21:00:00+08:00"
    )
