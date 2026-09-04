from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import pytest

from cta.strategy.brooks.scalp.session import (
    SHANGHAI_TZ,
    SessionCalendar,
    SessionError,
    SessionSegment,
    SessionSpec,
    TradingCalendarEntry,
)
from cta.strategy.brooks.scalp.research_pipeline import (
    _drop_vendor_no_night_rows,
    _exclude_mixed_contract_sessions,
    _parse_time,
    _restore_cu_vendor_timestamps,
)
from cta.strategy.brooks.scalp.shfe_metadata_builder import (
    ProductRule,
    _calendar_rows,
    _night_leg_is_complete,
    build_contract_daily_row,
    parse_shfe_settlement_rows,
)


def test_source_download_uses_only_prior_dates_for_requested_trade_dates(
    monkeypatch,
    tmp_path,
) -> None:
    from cta.strategy.brooks.scalp import shfe_metadata_builder as builder

    requested = date(2026, 7, 27)
    prior = date(2026, 7, 24)
    calls: list[tuple[str, date]] = []

    def fake_fetch_one(**kwargs):
        calls.append((kwargs["kind"], kwargs["source_date"]))
        return builder.CachedSource(
            kind=kwargs["kind"],
            source_date=kwargs["source_date"],
            url="https://example.test/source",
            sha256="a" * 64,
            payload={"rows": [1]},
        )

    monkeypatch.setattr(builder, "_fetch_one", fake_fetch_one)

    builder._download_sources(
        trade_dates=(requested,),
        prior_open={
            requested: prior,
            date(2026, 8, 24): date(2026, 8, 21),
        },
        cache_root=tmp_path,
        workers=1,
        timeout=1.0,
    )

    assert sorted(calls) == [("js", prior), ("kx", requested)]


def _aware(value: str) -> datetime:
    return pd.Timestamp(value, tz=SHANGHAI_TZ).to_pydatetime()


def test_old_shfe_settlement_schema_uses_standard_fee_for_close_today() -> None:
    rows = parse_shfe_settlement_rows(
        {
            "o_cursor": [
                {
                    "INSTRUMENTID": "rb1805                        ",
                    "SETTLEMENTPRICE": 3_800,
                    "TRADEFEERATIO": 0.1,
                    "TRADEFEEUNIT": 0,
                    "SPECLONGMARGINRATIO": 0.09,
                    "SPECSHORTMARGINRATIO": 0.09,
                }
            ]
        },
        source_date=date(2018, 1, 2),
    )

    row = rows["RB1805.SHF"]
    assert row.open_fee_rate == pytest.approx(0.0001)
    assert row.close_today_fee_rate == pytest.approx(0.0001)
    assert row.fee_per_lot_open == 0.0
    assert row.margin_rate_long == pytest.approx(0.09)


def test_new_shfe_settlement_schema_uses_explicit_close_today_fee() -> None:
    rows = parse_shfe_settlement_rows(
        {
            "o_cursor": [
                {
                    "INSTRUMENTID": "cu2605",
                    "SETTLEMENTPRICE": 102_330,
                    "TRADEFEERATIO": 0.05,
                    "TTRADEFEERATIO": 0.025,
                    "TRADEFEEUNIT": 0,
                    "TTRADEFEEUNIT": 0,
                    "SPECLONGMARGINRATIO": 0.12,
                    "SPECSHORTMARGINRATIO": 0.12,
                }
            ]
        },
        source_date=date(2026, 4, 16),
    )

    row = rows["CU2605.SHF"]
    assert row.open_fee_rate == pytest.approx(0.00005)
    assert row.close_today_fee_rate == pytest.approx(0.000025)


def test_contract_daily_uses_prior_open_parameters_and_shfe_tick_flooring() -> None:
    rule = ProductRule(
        root_symbol="CU",
        contract_size=5.0,
        price_tick=10.0,
        session_template_id="SHFE_CU_NIGHT_0100",
    )
    settlement = parse_shfe_settlement_rows(
        {
            "o_cursor": [
                {
                    "INSTRUMENTID": "cu2605",
                    "SETTLEMENTPRICE": 100_010,
                    "TRADEFEERATIO": 0.05,
                    "TRADEFEEUNIT": 0,
                    "SPECLONGMARGINRATIO": 0.12,
                    "SPECSHORTMARGINRATIO": 0.12,
                }
            ]
        },
        source_date=date(2026, 4, 15),
    )["CU2605.SHF"]

    daily, fee = build_contract_daily_row(
        contract_code="CU2605.SHF",
        trade_date=date(2026, 4, 16),
        prior_open_date=date(2026, 4, 15),
        session_open=_aware("2026-04-15 21:00"),
        pre_settlement=100_010.0,
        settlement=100_100.0,
        observed_high=109_000.0,
        observed_low=91_000.0,
        product_rule=rule,
        settlement_parameters=settlement,
        market_source_url="https://www.shfe.com.cn/kx20260416.dat",
        parameter_source_url="https://www.shfe.com.cn/js20260415.dat",
    )

    assert daily["limit_rate"] == pytest.approx(0.10)
    assert daily["limit_up"] == 110_010.0
    assert daily["limit_down"] == 90_000.0
    assert daily["known_at"] == "2026-04-15T21:00:00+08:00"
    assert fee["effective_from"] == daily["known_at"]
    assert "js20260415" in fee["source_url_or_file"]


def test_contract_daily_rejects_market_prices_outside_derived_limits() -> None:
    rule = ProductRule("RB", 10.0, 1.0, "SHFE_RB_NIGHT_2300")
    parameters = parse_shfe_settlement_rows(
        {
            "o_cursor": [
                {
                    "INSTRUMENTID": "rb2605",
                    "SETTLEMENTPRICE": 3_000,
                    "TRADEFEERATIO": 0.1,
                    "TRADEFEEUNIT": 0,
                    "SPECLONGMARGINRATIO": 0.09,
                    "SPECSHORTMARGINRATIO": 0.09,
                }
            ]
        },
        source_date=date(2026, 4, 15),
    )["RB2605.SHF"]

    with pytest.raises(ValueError, match="outside derived SHFE limits"):
        build_contract_daily_row(
            contract_code="RB2605.SHF",
            trade_date=date(2026, 4, 16),
            prior_open_date=date(2026, 4, 15),
            session_open=_aware("2026-04-15 21:00"),
            pre_settlement=3_000.0,
            settlement=3_010.0,
            observed_high=3_250.0,
            observed_low=2_990.0,
            product_rule=rule,
            settlement_parameters=parameters,
            market_source_url="kx",
            parameter_source_url="js",
        )


def test_calendar_skips_night_window_when_exchange_marks_no_night_session() -> None:
    sessions = (
        SessionSpec(
            "night",
            True,
            (SessionSegment("night", time(21), time(23), time(21)),),
        ),
        SessionSpec(
            "day",
            False,
            (SessionSegment("day", time(9), time(10), time(9)),),
        ),
    )
    entry = TradingCalendarEntry(
        exchange="SHFE",
        exchange_trade_date=date(2026, 2, 24),
        is_open=True,
        prior_open_date=date(2026, 2, 13),
        next_open_date=date(2026, 2, 25),
        night_session_start=None,
        source="SHFE-test",
        known_at=_aware("2026-02-13 15:30"),
    )
    calendar = SessionCalendar(
        exchange="SHFE",
        entries=(entry,),
        sessions=sessions,
        calendar_sha256="fixture",
    )

    with pytest.raises(SessionError, match="outside configured session"):
        calendar.assign_bar(
            _aware("2026-02-13 21:00"),
            _aware("2026-02-13 21:01"),
        )
    assigned = calendar.assign_bar(
        _aware("2026-02-24 09:00"),
        _aware("2026-02-24 09:01"),
    )
    assert assigned.session_kind == "day"


def test_blank_calendar_night_start_parses_as_no_night_session() -> None:
    assert _parse_time("") is None
    assert _parse_time(float("nan")) is None
    assert _parse_time("21:00:00") == time(21)


def test_calendar_preserves_night_template_for_warmup_dates() -> None:
    dates = pd.date_range("2017-12-28", "2018-01-05", freq="B")
    frame = pd.DataFrame(
        {
            "cal_date": dates.strftime("%Y%m%d"),
            "is_open": 1,
        }
    )
    rows, _ = _calendar_rows(
        frame,
        start=date(2018, 1, 3),
        end=date(2018, 1, 4),
        night_dates={date(2018, 1, 3)},
    )
    indexed = {row["exchange_trade_date"]: row for row in rows}

    assert indexed["2018-01-02"]["night_session_start"] == "21:00:00"
    assert indexed["2018-01-03"]["night_session_start"] == "21:00:00"
    assert indexed["2018-01-04"]["night_session_start"] == ""


def test_night_leg_requires_endpoint_coverage_and_minute_continuity() -> None:
    complete = set(range(0, 61))
    sparse_placeholder = set(range(0, 61, 10))

    assert _night_leg_is_complete(
        complete,
        has_positive_volume=True,
        first_minute=0,
        last_minute=60,
    )
    assert not _night_leg_is_complete(
        sparse_placeholder,
        has_positive_volume=True,
        first_minute=0,
        last_minute=60,
    )
    assert not _night_leg_is_complete(
        complete - {60},
        has_positive_volume=True,
        first_minute=0,
        last_minute=60,
    )
    assert not _night_leg_is_complete(
        complete,
        has_positive_volume=False,
        first_minute=0,
        last_minute=60,
    )


def test_cu_vendor_midnight_timestamp_is_restored_across_weekend() -> None:
    raw = pd.DataFrame(
        {
            "trade_time": ["2018-01-08 00:01:00", "2018-01-08 09:01:00"],
            "vol": [2, 3],
            "amount": [100.0, 200.0],
        }
    )
    calendar = pd.DataFrame(
        [
            {
                "exchange": "SHFE",
                "exchange_trade_date": "2018-01-08",
                "prior_open_date": "2018-01-05",
                "night_session_start": "21:00:00",
            }
        ]
    )

    restored, shifted, dropped = _restore_cu_vendor_timestamps(
        raw, "trade_time", calendar
    )

    assert restored.loc[0, "trade_time"] == pd.Timestamp("2018-01-06 00:01:00")
    assert restored.loc[1, "trade_time"] == pd.Timestamp("2018-01-08 09:01:00")
    assert (shifted, dropped) == (1, 0)


def test_cu_zero_placeholder_is_removed_when_calendar_has_no_night() -> None:
    raw = pd.DataFrame(
        {
            "trade_time": ["2018-01-02 00:00:00", "2018-01-02 09:01:00"],
            "vol": [0, 3],
            "amount": [0.0, 200.0],
        }
    )
    calendar = pd.DataFrame(
        [
            {
                "exchange": "SHFE",
                "exchange_trade_date": "2018-01-02",
                "prior_open_date": "2017-12-29",
                "night_session_start": "",
            }
        ]
    )

    restored, shifted, dropped = _restore_cu_vendor_timestamps(
        raw, "trade_time", calendar
    )

    assert restored["trade_time"].tolist() == [pd.Timestamp("2018-01-02 09:01:00")]
    assert (shifted, dropped) == (0, 1)


def test_mixed_contract_night_session_is_excluded_as_a_whole() -> None:
    frame = pd.DataFrame(
        {
            "session_id": ["20180202:night", "20180202:night", "20180202:day"],
            "session_kind": ["night", "night", "day"],
            "exchange_trade_date": [date(2018, 2, 2)] * 3,
            "contract_code": ["CU1803.SHF", "CU1804.SHF", "CU1804.SHF"],
        }
    )

    clean, excluded = _exclude_mixed_contract_sessions(frame)

    assert clean["session_id"].tolist() == ["20180202:day"]
    assert excluded == {"20180202:night"}


def test_positive_vendor_evening_copy_is_dropped_for_no_night_trade_date() -> None:
    raw = pd.DataFrame(
        {
            "datetime": ["2018-02-14 21:01:00", "2018-02-14 15:00:00"],
            "volume": [100, 200],
        }
    )
    calendar = pd.DataFrame(
        [
            {
                "exchange": "SHFE",
                "exchange_trade_date": "2018-02-22",
                "prior_open_date": "2018-02-14",
                "night_session_start": "",
            }
        ]
    )

    clean, dropped = _drop_vendor_no_night_rows(
        raw,
        "datetime",
        calendar,
        exchange="SHFE",
    )

    assert clean["datetime"].tolist() == ["2018-02-14 15:00:00"]
    assert dropped == 1


def test_no_night_filter_does_not_apply_shfe_calendar_to_other_exchanges() -> None:
    raw = pd.DataFrame(
        {
            "datetime": ["2026-01-09 21:01:00", "2026-01-12 09:01:00"],
            "volume": [100, 200],
        }
    )
    calendar = pd.DataFrame(
        [
            {
                "exchange": "SHFE",
                "exchange_trade_date": "2026-01-12",
                "prior_open_date": "2026-01-09",
                "night_session_start": "",
            }
        ]
    )

    clean, dropped = _drop_vendor_no_night_rows(
        raw,
        "datetime",
        calendar,
        exchange="DCE",
    )

    assert clean.equals(raw)
    assert dropped == 0


def test_no_night_filter_drops_shfe_after_midnight_copy() -> None:
    raw = pd.DataFrame(
        {
            "datetime": [
                "2026-04-16 21:01:00",
                "2026-04-17 00:00:00",
                "2026-04-17 09:01:00",
            ],
            "volume": [100, 100, 200],
        }
    )
    calendar = pd.DataFrame(
        [
            {
                "exchange": "SHFE",
                "exchange_trade_date": "2026-04-17",
                "prior_open_date": "2026-04-16",
                "night_session_start": "",
            }
        ]
    )

    clean, dropped = _drop_vendor_no_night_rows(
        raw,
        "datetime",
        calendar,
        exchange="SHFE",
    )

    assert clean["datetime"].tolist() == ["2026-04-17 09:01:00"]
    assert dropped == 2
