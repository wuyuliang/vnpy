from __future__ import annotations

import inspect
import json
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from cta.strategy.brooks.scalp.config import (
    DEFAULT_CONFIG_PATH,
    config_sha256,
    load_config,
)
from cta.strategy.brooks.scalp.data import (
    CANONICAL_BAR_COLUMNS,
    ContractMapping,
    DataQualityError,
    MinuteBar,
    aggregate_completed_bars,
    apply_contract_roll_freeze,
    normalize_source_bars,
    validate_contract_mappings,
)
from cta.strategy.brooks.scalp.metadata import (
    DEFAULT_META_ROOT,
    BlockedMetadataError,
    DailyTradingSpec,
    FeeMarginSpec,
    InstrumentSpec,
    MetadataBundle,
)
from cta.strategy.brooks.scalp.metadata_importer import (
    CANONICAL_FILENAMES,
    StagingSource,
    import_metadata_bundle,
    sha256_file,
    validate_requested_coverage,
    verify_manifest,
)
from cta.strategy.brooks.scalp.session import (
    SHANGHAI_TZ,
    SessionCalendar,
    SessionError,
    SessionSegment,
    SessionSpec,
    TradingCalendarEntry,
)
from cta.strategy.brooks.scalp.research_pipeline import (
    _require_authoritative_sources,
    sessions_for_template,
)


def _aware(value: str) -> datetime:
    return pd.Timestamp(value, tz=SHANGHAI_TZ).to_pydatetime()


def _sessions() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            session_id="night",
            is_night=True,
            segments=(
                SessionSegment(
                    segment_id="night_continuous",
                    start=time(21, 0),
                    end=time(1, 0),
                    bucket_anchor=time(21, 0),
                ),
            ),
        ),
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(
                SessionSegment("day_1", time(9, 0), time(10, 15), time(9, 0)),
                SessionSegment("day_2", time(10, 30), time(11, 30), time(10, 30)),
                SessionSegment("day_3", time(13, 30), time(15, 0), time(13, 30)),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("template", "expected_end"),
    [
        ("CN_COMMODITY_NIGHT_2300", time(23, 0)),
        ("CN_COMMODITY_NIGHT_0100", time(1, 0)),
        ("CN_COMMODITY_NIGHT_0230", time(2, 30)),
    ],
)
def test_generic_commodity_session_templates_are_explicit(
    template: str,
    expected_end: time,
) -> None:
    sessions = sessions_for_template(template)

    assert sessions[0].session_id == "night"
    assert sessions[0].segments[0].end == expected_end
    assert sessions[-1].session_id == "day"


def test_generic_day_only_template_has_no_night_session() -> None:
    sessions = sessions_for_template("CN_COMMODITY_DAY")

    assert len(sessions) == 1
    assert sessions[0].session_id == "day"


def test_authoritative_source_validation_uses_contract_exchange() -> None:
    contract = "I2609.DCE"
    prepared = SimpleNamespace(
        config=SimpleNamespace(
            metadata=SimpleNamespace(require_authoritative_exchange_source=True)
        ),
        contract_rows={
            contract: {
                "exchange": "DCE",
                "source": "DCE_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC",
            }
        },
    )
    daily = DailyTradingSpec(
        contract_code=contract,
        exchange_trade_date=date(2026, 7, 27),
        pre_settlement=800.0,
        limit_up=880.0,
        limit_down=720.0,
        fee_margin_schedule_id="DCE-I2609-20260727",
        source="DCE_EXCHANGE_PARAMETER_MIRROR_TUSHARE_JIN10_RECONCILED",
        known_at=_aware("2026-07-24 21:00"),
    )
    fee = FeeMarginSpec(
        schedule_id="DCE-I2609-20260727",
        root_symbol="I",
        contract_code=contract,
        margin_rate_long=0.11,
        margin_rate_short=0.11,
        open_fee_rate=0.0001,
        close_fee_rate=0.0001,
        close_today_fee_rate=0.0001,
        fee_per_lot_open=0.0,
        fee_per_lot_close=0.0,
        fee_per_lot_close_today=0.0,
        effective_from=_aware("2026-07-24 21:00"),
        effective_to=None,
        source="DCE_EXCHANGE_PARAMETER_MIRROR_TUSHARE_JIN10_RECONCILED",
        known_at=_aware("2026-07-24 21:00"),
    )

    _require_authoritative_sources(prepared, contract, daily, fee)

    prepared.contract_rows[contract]["source"] = "SHFE_WRONG_EXCHANGE"
    with pytest.raises(BlockedMetadataError, match="non-authoritative"):
        _require_authoritative_sources(prepared, contract, daily, fee)


def _instrument(root: str = "RB") -> InstrumentSpec:
    return InstrumentSpec(
        root_symbol=root,
        exchange="SHFE",
        contract_size=10.0 if root == "RB" else 5.0,
        price_tick=1.0 if root == "RB" else 10.0,
        lot_step=1,
        slippage_ticks_base=1.0,
        sessions=_sessions(),
        effective_from=date(2025, 1, 1),
        effective_to=None,
    )


def _calendar(sessions: tuple[SessionSpec, ...] | None = None) -> SessionCalendar:
    known_at = _aware("2024-12-01 00:00")
    entries = (
        TradingCalendarEntry(
            exchange="SHFE",
            exchange_trade_date=date(2025, 9, 3),
            is_open=True,
            prior_open_date=date(2025, 9, 2),
            next_open_date=date(2025, 9, 4),
            night_session_start=time(21, 0),
            source="SHFE-test-fixture",
            known_at=known_at,
        ),
        TradingCalendarEntry(
            exchange="SHFE",
            exchange_trade_date=date(2025, 9, 4),
            is_open=True,
            prior_open_date=date(2025, 9, 3),
            next_open_date=date(2025, 9, 5),
            night_session_start=time(21, 0),
            source="SHFE-test-fixture",
            known_at=known_at,
        ),
        TradingCalendarEntry(
            exchange="SHFE",
            exchange_trade_date=date(2025, 9, 5),
            is_open=True,
            prior_open_date=date(2025, 9, 4),
            next_open_date=date(2025, 9, 8),
            night_session_start=time(21, 0),
            source="SHFE-test-fixture",
            known_at=known_at,
        ),
        TradingCalendarEntry(
            exchange="SHFE",
            exchange_trade_date=date(2025, 9, 8),
            is_open=True,
            prior_open_date=date(2025, 9, 5),
            next_open_date=date(2025, 9, 9),
            night_session_start=time(21, 0),
            source="SHFE-test-fixture",
            known_at=known_at,
        ),
        TradingCalendarEntry(
            exchange="SHFE",
            exchange_trade_date=date(2025, 9, 9),
            is_open=True,
            prior_open_date=date(2025, 9, 8),
            next_open_date=date(2025, 9, 10),
            night_session_start=time(21, 0),
            source="SHFE-test-fixture",
            known_at=known_at,
        ),
    )
    return SessionCalendar(
        exchange="SHFE",
        entries=entries,
        sessions=sessions or _sessions(),
        calendar_sha256="calendar-fixture-sha256",
    )


def _daily(
    contract_code: str,
    trade_dates: tuple[date, ...] = (date(2025, 9, 5),),
) -> dict[tuple[str, date], DailyTradingSpec]:
    return {
        (contract_code, trade_date): DailyTradingSpec(
            contract_code=contract_code,
            exchange_trade_date=trade_date,
            pre_settlement=3200.0,
            limit_up=3424.0,
            limit_down=2976.0,
            fee_margin_schedule_id="RB-default",
            source="SHFE-test-fixture",
            known_at=_aware("2025-01-01 00:00"),
        )
        for trade_date in trade_dates
    }


def _source_frame(
    schema: str,
    timestamps: pd.DatetimeIndex,
    contract_code: str,
) -> pd.DataFrame:
    count = len(timestamps)
    base = pd.Series(range(count), dtype="float64") + 3200.0
    common = {
        "open": base,
        "high": base + 2.0,
        "low": base - 2.0,
        "close": base + 1.0,
    }
    if schema == "RB":
        return pd.DataFrame(
            {
                "datetime": timestamps,
                **common,
                "volume": pd.Series(range(1, count + 1), dtype="float64"),
                "open_interest": pd.Series(range(count), dtype="float64"),
                "turnover": pd.Series(range(100, 100 + count), dtype="float64"),
                "ts_code": contract_code,
                "symbol": "RB0",
                "exchange": "SHFE",
            }
        )
    if schema == "CU":
        return pd.DataFrame(
            {
                "ts_code": contract_code,
                "trade_time": timestamps,
                **common,
                "vol": pd.Series(range(1, count + 1), dtype="float64"),
                "amount": pd.Series(range(100, 100 + count), dtype="float64"),
                "oi": pd.Series(range(count), dtype="float64"),
                "trade_date": timestamps.strftime("%Y-%m-%d"),
                "raw_symbol": "CU0.SHF",
                "mapping_symbol": "CU.SHF",
                "contract_code": contract_code,
                "freq": "1min",
            }
        )
    raise AssertionError(f"unknown test schema: {schema}")


def _normalize_day(
    timestamps: pd.DatetimeIndex,
    *,
    root: str = "RB",
    contract_code: str = "RB2601.SHF",
) -> pd.DataFrame:
    return normalize_source_bars(
        _source_frame(root, timestamps, contract_code),
        _instrument(root),
        _calendar(),
        daily_specs=_daily(contract_code),
        source_path=f"{root}-fixture.parquet",
    )


def test_default_config_is_frozen_hashable_and_has_required_sections(tmp_path: Path) -> None:
    config = load_config()

    assert DEFAULT_CONFIG_PATH.name == "strategy.yaml"
    assert config.account.initial_equity == 200_000
    assert config.intervals.regime == "minute30"
    assert config.rules.allow_runtime_disable is False
    assert config.trade_plan.planned_net_payoff == pytest.approx(1.10)
    assert config.risk.daily_hard_loss_pct == pytest.approx(0.01)
    assert config.risk_components.liquidity.required_backtest_fields == (
        "volume_ratio",
        "turnover_activity_ratio",
    )
    assert config.validation.minimum_net_win_rate == pytest.approx(0.80)
    assert len(config.rules.required_rule_directions) == 6

    with pytest.raises(FrozenInstanceError):
        config.risk.daily_hard_loss_pct = 0.02  # type: ignore[misc]

    first_hash = config_sha256(config)
    assert first_hash == config_sha256(DEFAULT_CONFIG_PATH)
    assert len(first_hash) == 64

    changed = tmp_path / "changed.yaml"
    changed.write_text(
        DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").replace(
            "initial_equity: 200000", "initial_equity: 200001"
        ),
        encoding="utf-8",
    )
    assert config_sha256(changed) != first_hash

    assert tuple(inspect.signature(load_config).parameters) == ("path",)
    with pytest.raises(TypeError):
        load_config(DEFAULT_CONFIG_PATH, risk={})  # type: ignore[call-arg]


def test_rb_and_cu_source_schemas_normalize_to_same_contract() -> None:
    timestamps = pd.date_range("2025-09-05 09:01", periods=5, freq="min")
    rb = normalize_source_bars(
        _source_frame("RB", timestamps, "RB2601.SHF"),
        _instrument("RB"),
        _calendar(),
        daily_specs=_daily("RB2601.SHF"),
        source_path="rb.parquet",
    )
    cu = normalize_source_bars(
        _source_frame("CU", timestamps, "CU2510.SHF"),
        _instrument("CU"),
        _calendar(),
        daily_specs=_daily("CU2510.SHF"),
        source_path="cu.parquet",
    )

    assert tuple(rb.columns[: len(CANONICAL_BAR_COLUMNS)]) == CANONICAL_BAR_COLUMNS
    assert tuple(cu.columns[: len(CANONICAL_BAR_COLUMNS)]) == CANONICAL_BAR_COLUMNS
    assert str(rb["bar_end"].dt.tz) == "Asia/Shanghai"
    assert str(cu["bar_end"].dt.tz) == "Asia/Shanghai"
    assert rb["vt_symbol"].iat[0] == "RB2601.SHFE"
    assert cu["vt_symbol"].iat[0] == "CU2510.SHFE"
    assert rb["source_calendar_date"].iat[0] == date(2025, 9, 5)
    assert cu["source_calendar_date"].iat[0] == date(2025, 9, 5)
    assert rb["exchange_trade_date"].iat[0] == date(2025, 9, 5)
    assert cu["exchange_trade_date"].iat[0] == date(2025, 9, 5)
    assert_frame_equal(
        rb[["open", "high", "low", "close", "volume", "turnover", "open_interest"]],
        cu[["open", "high", "low", "close", "volume", "turnover", "open_interest"]],
        check_dtype=False,
    )
    assert rb["open_interest"].iloc[:2].tolist() == [0.0, 1.0]

    minute_bar = MinuteBar.from_mapping(rb.iloc[0])
    assert json.loads(json.dumps(minute_bar.to_dict()))["bar_end"].endswith("+08:00")


def test_cu_natural_date_is_separate_from_shfe_trade_date_across_weekend() -> None:
    timestamps = pd.DatetimeIndex(
        [
            "2025-09-05 00:00",
            "2025-09-05 23:59",
            "2025-09-06 00:00",
        ]
    )
    source = _source_frame("CU", timestamps, "CU2510.SHF")
    result = normalize_source_bars(
        source,
        _instrument("CU"),
        _calendar(),
        daily_specs=_daily(
            "CU2510.SHF", (date(2025, 9, 5), date(2025, 9, 8))
        ),
        source_path="cu-night.parquet",
    )

    assert result["source_calendar_date"].tolist() == [
        date(2025, 9, 5),
        date(2025, 9, 5),
        date(2025, 9, 6),
    ]
    assert result["exchange_trade_date"].tolist() == [
        date(2025, 9, 5),
        date(2025, 9, 8),
        date(2025, 9, 8),
    ]
    assert result["session_id"].tolist() == [
        "20250905:night",
        "20250908:night",
        "20250908:night",
    ]
    assert set(result["calendar_sha256"]) == {"calendar-fixture-sha256"}


def test_aggregation_allows_source_calendar_date_to_change_at_midnight() -> None:
    timestamps = pd.date_range("2025-09-05 23:56", periods=5, freq="min")
    minute = normalize_source_bars(
        _source_frame("CU", timestamps, "CU2510.SHF"),
        _instrument("CU"),
        _calendar(),
        daily_specs=_daily("CU2510.SHF", (date(2025, 9, 8),)),
        source_path="cu-midnight.parquet",
    )

    aggregated = aggregate_completed_bars(minute, 5, _calendar())

    assert len(aggregated) == 1
    assert aggregated.loc[0, "exchange_trade_date"] == date(2025, 9, 8)
    assert aggregated.loc[0, "source_calendar_date"] == date(2025, 9, 6)


def test_session_boundaries_do_not_accept_break_minutes() -> None:
    calendar = _calendar()

    accepted = {
        "2025-09-05 10:15": "day_1",
        "2025-09-05 10:31": "day_2",
        "2025-09-05 11:30": "day_2",
        "2025-09-05 13:31": "day_3",
    }
    for value, segment_id in accepted.items():
        bar_end = pd.Timestamp(value, tz=SHANGHAI_TZ).to_pydatetime()
        assignment = calendar.assign_bar(bar_end.replace(second=0) - pd.Timedelta(minutes=1), bar_end)
        assert assignment.segment_id == segment_id

    for value in ("2025-09-05 10:30", "2025-09-05 13:30"):
        bar_end = pd.Timestamp(value, tz=SHANGHAI_TZ).to_pydatetime()
        with pytest.raises(SessionError, match="outside configured session segments"):
            calendar.assign_bar(bar_end - pd.Timedelta(minutes=1), bar_end)


def test_aggregate_completed_bars_respects_segment_anchors_and_prefix() -> None:
    timestamps = pd.date_range("2025-09-05 09:01", "2025-09-05 10:15", freq="min")
    timestamps = timestamps.append(
        pd.date_range("2025-09-05 10:31", "2025-09-05 11:30", freq="min")
    )
    timestamps = timestamps.append(
        pd.date_range("2025-09-05 13:31", "2025-09-05 15:00", freq="min")
    )
    normalized = _normalize_day(timestamps)

    bars_5m = aggregate_completed_bars(normalized, 5, _calendar())
    bars_30m = aggregate_completed_bars(normalized, 30, _calendar())

    ends_5m = set(bars_5m["bar_end"].dt.strftime("%H:%M"))
    assert {"10:15", "10:35", "11:30", "13:35", "15:00"} <= ends_5m
    assert not ((bars_5m["bar_start"].dt.strftime("%H:%M") == "10:15").any())
    assert bars_30m["bar_end"].dt.strftime("%H:%M").tolist() == [
        "09:30",
        "10:00",
        "11:00",
        "11:30",
        "14:00",
        "14:30",
        "15:00",
    ]
    assert (bars_30m["source_max_bar_end"] <= bars_30m["bar_end"]).all()

    cutoff = pd.Timestamp("2025-09-05 10:00", tz=SHANGHAI_TZ)
    prefix = normalized.loc[normalized["bar_end"] <= cutoff].copy()
    prefix_bars = aggregate_completed_bars(prefix, 5, _calendar())
    full_prefix = bars_5m.loc[bars_5m["bar_end"] <= cutoff].reset_index(drop=True)
    assert_frame_equal(full_prefix, prefix_bars.reset_index(drop=True))

    mutated = normalized.copy()
    mutated.loc[mutated["bar_end"] > cutoff, ["open", "high", "low", "close"]] *= 10
    mutated_prefix = aggregate_completed_bars(mutated, 5, _calendar())
    mutated_prefix = mutated_prefix.loc[mutated_prefix["bar_end"] <= cutoff].reset_index(drop=True)
    assert_frame_equal(full_prefix, mutated_prefix)


def test_incomplete_buckets_are_not_published() -> None:
    normalized = _normalize_day(pd.date_range("2025-09-05 09:01", periods=4, freq="min"))

    assert aggregate_completed_bars(normalized, 5, _calendar()).empty
    assert aggregate_completed_bars(normalized, 30, _calendar()).empty


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        (lambda frame: pd.concat([frame, frame.iloc[[-1]]], ignore_index=True), "DUPLICATE_BAR"),
        (lambda frame: frame.iloc[::-1].reset_index(drop=True), "OUT_OF_ORDER_BAR"),
        (lambda frame: frame.assign(high=frame["low"] - 1.0), "INVALID_OHLC"),
        (lambda frame: frame.assign(volume=-1.0), "NEGATIVE_ACTIVITY"),
        (lambda frame: frame.assign(turnover=-1.0), "NEGATIVE_ACTIVITY"),
    ],
)
def test_normalization_fails_closed_on_invalid_source(mutation, error_code: str) -> None:
    source = _source_frame(
        "RB", pd.date_range("2025-09-05 09:01", periods=3, freq="min"), "RB2601.SHF"
    )

    with pytest.raises(DataQualityError) as exc_info:
        normalize_source_bars(
            mutation(source),
            _instrument(),
            _calendar(),
            daily_specs=_daily("RB2601.SHF"),
        )
    assert exc_info.value.code == error_code


def test_zero_volume_negative_turnover_vendor_sentinel_normalizes_to_zero() -> None:
    source = _source_frame(
        "RB", pd.date_range("2025-09-05 09:01", periods=3, freq="min"), "RB2601.SHF"
    )
    source.loc[1, ["volume", "turnover"]] = [0.0, -5_370.0]

    normalized = normalize_source_bars(
        source,
        _instrument(),
        _calendar(),
        daily_specs=_daily("RB2601.SHF"),
    )

    assert normalized["turnover"].tolist() == [source.loc[0, "turnover"], 0.0, source.loc[2, "turnover"]]
    assert source.loc[1, "turnover"] == -5_370.0


def test_first_minute_zero_ohlc_reconciles_from_tick_aligned_turnover() -> None:
    source = _source_frame(
        "RB", pd.date_range("2025-09-05 09:01", periods=3, freq="min"), "RB2601.SHF"
    )
    source.loc[0, ["open", "high", "low", "close"]] = 0.0
    source.loc[0, ["volume", "turnover"]] = [18.0, 576_000.0]

    normalized = normalize_source_bars(
        source,
        _instrument(),
        _calendar(),
        daily_specs=_daily("RB2601.SHF"),
    )

    assert normalized.loc[0, ["open", "high", "low", "close"]].tolist() == [
        3_200.0,
        3_200.0,
        3_200.0,
        3_200.0,
    ]
    assert source.loc[0, "open"] == 0.0


def test_normalization_skips_empty_opening_repair_for_mixed_numeric_dtypes() -> None:
    timestamps = pd.date_range("2025-09-05 09:01", periods=3, freq="min")
    source = _source_frame("CU", timestamps, "CU2510.SHF")
    source[["vol", "amount", "oi"]] = source[["vol", "amount", "oi"]].astype(
        "int64"
    )

    normalized = normalize_source_bars(
        source,
        _instrument("CU"),
        _calendar(),
        daily_specs=_daily("CU2510.SHF"),
    )

    assert normalized["open"].tolist() == source["open"].tolist()


@pytest.mark.parametrize(
    ("position", "turnover"),
    [
        (1, 576_000.0),
        (0, 576_045.0),
    ],
)
def test_zero_ohlc_reconciliation_rejects_ambiguous_rows(
    position: int,
    turnover: float,
) -> None:
    source = _source_frame(
        "RB", pd.date_range("2025-09-05 09:01", periods=3, freq="min"), "RB2601.SHF"
    )
    source.loc[position, ["open", "high", "low", "close"]] = 0.0
    source.loc[position, ["volume", "turnover"]] = [18.0, turnover]

    with pytest.raises(DataQualityError) as exc_info:
        normalize_source_bars(
            source,
            _instrument(),
            _calendar(),
            daily_specs=_daily("RB2601.SHF"),
        )

    assert exc_info.value.code == "INVALID_OHLC"


def test_zero_ohlc_at_later_session_segment_remains_invalid() -> None:
    source = _source_frame(
        "RB", pd.date_range("2025-09-05 10:31", periods=3, freq="min"), "RB2601.SHF"
    )
    source.loc[0, ["open", "high", "low", "close"]] = 0.0
    source.loc[0, ["volume", "turnover"]] = [18.0, 576_000.0]

    with pytest.raises(DataQualityError) as exc_info:
        normalize_source_bars(
            source,
            _instrument(),
            _calendar(),
            daily_specs=_daily("RB2601.SHF"),
        )

    assert exc_info.value.code == "INVALID_OHLC"


def test_more_than_two_consecutive_missing_minutes_fails_closed() -> None:
    source = _source_frame(
        "RB",
        pd.DatetimeIndex(["2025-09-05 09:01", "2025-09-05 09:05"]),
        "RB2601.SHF",
    )

    with pytest.raises(DataQualityError) as exc_info:
        normalize_source_bars(
            source,
            _instrument(),
            _calendar(),
            daily_specs=_daily("RB2601.SHF"),
        )
    assert exc_info.value.code == "SESSION_GAP_EXCEEDED"


def test_missing_daily_limits_block_normalization() -> None:
    source = _source_frame(
        "RB", pd.date_range("2025-09-05 09:01", periods=2, freq="min"), "RB2601.SHF"
    )

    with pytest.raises(BlockedMetadataError) as exc_info:
        normalize_source_bars(source, _instrument(), _calendar(), daily_specs={})
    assert exc_info.value.status == "BLOCKED_METADATA"


def test_contract_mapping_is_causal_and_roll_freezes_whole_session() -> None:
    mappings = (
        ContractMapping(
            root_symbol="RB",
            contract_code="RB2601.SHF",
            effective_session="20250908:day",
            session_open=_aware("2025-09-08 09:00"),
            decision_asof=_aware("2025-09-08 08:59"),
            source="fixture",
        ),
    )
    validate_contract_mappings(mappings)
    with pytest.raises(DataQualityError) as exc_info:
        validate_contract_mappings(
            (replace(mappings[0], decision_asof=_aware("2025-09-08 09:01")),)
        )
    assert exc_info.value.code == "NON_CAUSAL_CONTRACT_MAPPING"
    validate_contract_mappings(
        (
            replace(
                mappings[0],
                decision_asof=_aware("2025-09-08 09:01"),
                source="local_session_first_bar",
            ),
        )
    )
    with pytest.raises(DataQualityError) as local_late:
        validate_contract_mappings(
            (
                replace(
                    mappings[0],
                    decision_asof=_aware("2025-09-08 09:02"),
                    source="local_session_first_bar",
                ),
            )
        )
    assert local_late.value.code == "NON_CAUSAL_CONTRACT_MAPPING"

    bars = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2025-09-05 14:59+08:00", "2025-09-08 09:01+08:00"]
            ),
            "session_id": ["20250905:day", "20250908:day"],
            "contract_code": ["RB2510.SHF", "RB2601.SHF"],
        }
    )
    frozen = apply_contract_roll_freeze(bars)
    assert frozen["roll_freeze"].tolist() == [False, True]
    assert frozen["roll_reason"].tolist() == [None, "contract_switch"]

    intra_session = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2025-09-08 09:01+08:00", "2025-09-08 09:02+08:00"]
            ),
            "session_id": ["20250908:day", "20250908:day"],
            "contract_code": ["RB2510.SHF", "RB2601.SHF"],
        }
    )
    with pytest.raises(DataQualityError) as switch_error:
        apply_contract_roll_freeze(intra_session)
    assert switch_error.value.code == "INTRASESSION_CONTRACT_SWITCH"


def _write_staging_bundle(root: Path, *, late_daily: bool = False) -> dict[str, StagingSource]:
    root.mkdir(parents=True, exist_ok=True)
    known_at = "2025-09-04T22:00:00+08:00" if late_daily else "2025-01-01T00:00:00+08:00"
    frames = {
        "exchange_calendar.csv": pd.DataFrame(
            [
                {
                    "exchange": "SHFE",
                    "exchange_trade_date": "2025-09-05",
                    "is_open": True,
                    "prior_open_date": "2025-09-04",
                    "next_open_date": "2025-09-08",
                    "night_session_start": "21:00",
                    "source": "SHFE fixture",
                    "known_at": "2024-12-01T00:00:00+08:00",
                },
                {
                    "exchange": "SHFE",
                    "exchange_trade_date": "2025-09-08",
                    "is_open": True,
                    "prior_open_date": "2025-09-05",
                    "next_open_date": "2025-09-09",
                    "night_session_start": "21:00",
                    "source": "SHFE fixture",
                    "known_at": "2024-12-01T00:00:00+08:00",
                },
            ]
        ),
        "contract_specs.csv": pd.DataFrame(
            [
                {
                    "contract_code": "RB2601.SHF",
                    "root_symbol": "RB",
                    "exchange": "SHFE",
                    "contract_size": 10.0,
                    "price_tick": 1.0,
                    "lot_step": 1,
                    "slippage_ticks_base": 1.0,
                    "last_trade_date": "2026-01-15",
                    "session_template_id": "SHFE_RB_NIGHT",
                    "source": "SHFE fixture",
                    "known_at": "2024-12-01T00:00:00+08:00",
                }
            ]
        ),
        "contract_daily.csv": pd.DataFrame(
            [
                {
                    "contract_code": "RB2601.SHF",
                    "exchange_trade_date": "2025-09-05",
                    "pre_settlement": 3200.0,
                    "settlement": 3210.0,
                    "limit_rate": 0.07,
                    "limit_up": 3424.0,
                    "limit_down": 2976.0,
                    "limit_rounding_rule": "fixture-only",
                    "source": "SHFE fixture",
                    "source_url_or_file": "https://example.invalid/shfe-fixture.csv",
                    "known_at": known_at,
                    "pre_settlement_known_at": "2025-09-04T15:30:00+08:00",
                    "settlement_known_at": "2025-09-05T15:30:00+08:00",
                    "fee_margin_schedule_id": "RB-default",
                }
            ]
        ),
        "fee_margin_schedule.csv": pd.DataFrame(
            [
                {
                    "schedule_id": "RB-default",
                    "root_symbol": "RB",
                    "contract_code": "RB2601.SHF",
                    "effective_from": "2025-01-01T00:00:00+08:00",
                    "effective_to": "",
                    "margin_rate_long": 0.12,
                    "margin_rate_short": 0.12,
                    "open_fee_rate": 0.0001,
                    "close_fee_rate": 0.0001,
                    "close_today_fee_rate": 0.0001,
                    "fee_per_lot_open": 0.0,
                    "fee_per_lot_close": 0.0,
                    "fee_per_lot_close_today": 0.0,
                    "source": "broker fixture",
                    "source_url_or_file": "broker-fixture.csv",
                    "known_at": "2024-12-01T00:00:00+08:00",
                }
            ]
        ),
    }
    coverage = {
        "exchange_calendar.csv": ("2025-09-05", "2025-09-08"),
        "contract_specs.csv": ("2026-01-15", "2026-01-15"),
        "contract_daily.csv": ("2025-09-05", "2025-09-05"),
        "fee_margin_schedule.csv": ("2025-01-01", "2025-01-01"),
    }
    result: dict[str, StagingSource] = {}
    for filename, frame in frames.items():
        path = root / filename
        frame.to_csv(path, index=False)
        result[filename] = StagingSource(
            path=path,
            source_url_or_file=f"fixture://{filename}",
            downloaded_at=_aware("2026-08-09 09:00"),
            coverage_start=coverage[filename][0],
            coverage_end=coverage[filename][1],
            sha256=sha256_file(path),
        )
    return result


def test_metadata_import_manifest_and_coverage_are_recomputable(tmp_path: Path) -> None:
    staging = _write_staging_bundle(tmp_path / "staging")
    output = tmp_path / "canonical"

    manifest = import_metadata_bundle(staging, output)
    assert set(manifest["files"]) == set(CANONICAL_FILENAMES)
    assert manifest == verify_manifest(output)

    bundle = MetadataBundle.load(output)
    session_open = _aware("2025-09-04 21:00")
    bundle.require_coverage(
        contract_codes=("RB2601.SHF",),
        trade_dates=(date(2025, 9, 5),),
        session_opens={("RB2601.SHF", date(2025, 9, 5)): session_open},
    )
    daily = bundle.daily_spec("RB2601.SHF", date(2025, 9, 5))
    pre_settlement = bundle.pre_settlement_reference(
        "RB2601.SHF", date(2025, 9, 5)
    )
    settlement = bundle.settlement_reference("RB2601.SHF", date(2025, 9, 5))
    fee = bundle.fee_margin_spec("RB-default", "RB2601.SHF", session_open)
    assert daily.pre_settlement == 3200.0
    assert pre_settlement.price == 3200.0
    assert pre_settlement.known_at == _aware("2025-09-04 15:30")
    assert settlement.price == 3210.0
    assert settlement.known_at == _aware("2025-09-05 15:30")
    assert fee.margin_rate_long == pytest.approx(0.12)
    assert json.loads(json.dumps(daily.to_dict()))["known_at"].endswith("+08:00")
    assert json.loads(json.dumps(fee.to_dict()))["effective_to"] is None

    with (output / "contract_daily.csv").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(BlockedMetadataError, match="sha256"):
        verify_manifest(output)


def test_official_shfe_daily_rows_have_causal_reference_time_fallbacks() -> None:
    row = {
        "contract_code": "RB2605.SHF",
        "exchange_trade_date": "2026-04-08",
        "pre_settlement": 3094.0,
        "settlement": 3083.0,
        "source": "SHFE_KX_JS_RULE_DERIVED",
        "source_url_or_file": (
            "https://www.shfe.com.cn/data/tradedata/future/dailydata/"
            "kx20260408.dat|https://www.shfe.com.cn/data/tradedata/future/"
            "dailydata/js20260407.dat"
        ),
        "known_at": "2026-04-07T21:00:00+08:00",
        "fee_margin_schedule_id": "RB-default",
    }
    bundle = MetadataBundle(
        Path("unused"),
        {"contract_daily.csv": pd.DataFrame([row])},
        {},
    )

    pre_settlement = bundle.pre_settlement_reference(
        "RB2605.SHF", date(2026, 4, 8)
    )
    settlement = bundle.settlement_reference("RB2605.SHF", date(2026, 4, 8))

    assert pre_settlement.known_at == _aware("2026-04-07 15:30")
    assert settlement.known_at == _aware("2026-04-08 15:30")

    unsupported = MetadataBundle(
        Path("unused"),
        {
            "contract_daily.csv": pd.DataFrame(
                [{**row, "source": "UNVERIFIED_FIXTURE"}]
            )
        },
        {},
    )
    with pytest.raises(BlockedMetadataError, match="settlement_known_at is missing"):
        unsupported.settlement_reference("RB2605.SHF", date(2026, 4, 8))


def test_metadata_missing_late_or_unverifiable_blocks_entire_run(tmp_path: Path) -> None:
    staging = _write_staging_bundle(tmp_path / "staging")
    bad_source = replace(staging["contract_daily.csv"], sha256="0" * 64)
    with pytest.raises(BlockedMetadataError, match="staging sha256"):
        import_metadata_bundle(
            {**staging, "contract_daily.csv": bad_source}, tmp_path / "bad-hash"
        )

    output = tmp_path / "canonical"
    import_metadata_bundle(staging, output)
    bundle = MetadataBundle.load(output)
    with pytest.raises(BlockedMetadataError) as missing:
        bundle.require_coverage(
            contract_codes=("RB2601.SHF",),
            trade_dates=(date(2025, 9, 8),),
            session_opens={
                ("RB2601.SHF", date(2025, 9, 8)): _aware("2025-09-05 21:00")
            },
        )
    assert missing.value.status == "BLOCKED_METADATA"

    late_staging = _write_staging_bundle(tmp_path / "late-staging", late_daily=True)
    late_output = tmp_path / "late-canonical"
    import_metadata_bundle(late_staging, late_output)
    late_bundle = MetadataBundle.load(late_output)
    with pytest.raises(BlockedMetadataError, match="known_at"):
        late_bundle.require_coverage(
            contract_codes=("RB2601.SHF",),
            trade_dates=(date(2025, 9, 5),),
            session_opens={
                ("RB2601.SHF", date(2025, 9, 5)): _aware("2025-09-04 21:00")
            },
        )


def test_metadata_import_rejects_duplicate_contract_primary_key(tmp_path: Path) -> None:
    staging = _write_staging_bundle(tmp_path / "staging")
    source = staging["contract_specs.csv"]
    frame = pd.read_csv(source.path)
    conflicting = frame.copy()
    conflicting.loc[0, "contract_size"] = 99.0
    pd.concat([frame, conflicting], ignore_index=True).to_csv(source.path, index=False)
    staging["contract_specs.csv"] = replace(source, sha256=sha256_file(source.path))

    with pytest.raises(BlockedMetadataError, match="duplicate primary key"):
        import_metadata_bundle(staging, tmp_path / "canonical")


def test_metadata_preflight_rejects_interior_open_date_gap(tmp_path: Path) -> None:
    staging = _write_staging_bundle(tmp_path / "staging")

    calendar_source = staging["exchange_calendar.csv"]
    calendar = pd.read_csv(calendar_source.path)
    next_day = calendar.iloc[[-1]].copy()
    next_day["exchange_trade_date"] = "2025-09-09"
    calendar = pd.concat([calendar, next_day], ignore_index=True)
    calendar.to_csv(calendar_source.path, index=False)
    staging["exchange_calendar.csv"] = replace(
        calendar_source,
        coverage_end="2025-09-09",
        sha256=sha256_file(calendar_source.path),
    )

    daily_source = staging["contract_daily.csv"]
    daily = pd.read_csv(daily_source.path)
    endpoint = daily.copy()
    endpoint["exchange_trade_date"] = "2025-09-09"
    pd.concat([daily, endpoint], ignore_index=True).to_csv(daily_source.path, index=False)
    staging["contract_daily.csv"] = replace(
        daily_source,
        coverage_end="2025-09-09",
        sha256=sha256_file(daily_source.path),
    )

    output = tmp_path / "canonical"
    import_metadata_bundle(staging, output)
    with pytest.raises(BlockedMetadataError, match="missing open dates"):
        validate_requested_coverage(
            output,
            symbols=("RB0.SHFE",),
            start="2025-09-05",
            end="2025-09-09",
        )


def test_fee_schedule_rejects_overlapping_same_priority_rows(tmp_path: Path) -> None:
    staging = _write_staging_bundle(tmp_path / "staging")
    source = staging["fee_margin_schedule.csv"]
    frame = pd.read_csv(source.path)
    overlapping = frame.copy()
    overlapping["effective_from"] = "2025-06-01T00:00:00+08:00"
    pd.concat([frame, overlapping], ignore_index=True).to_csv(source.path, index=False)
    staging["fee_margin_schedule.csv"] = replace(
        source,
        coverage_end="2025-06-01",
        sha256=sha256_file(source.path),
    )
    output = tmp_path / "canonical"
    import_metadata_bundle(staging, output)
    bundle = MetadataBundle.load(output)

    with pytest.raises(BlockedMetadataError, match="overlapping fee schedule"):
        bundle.fee_margin_spec(
            "RB-default",
            "RB2601.SHF",
            _aware("2025-09-04 21:00"),
        )


def test_repository_metadata_bundle_is_populated_and_manifest_matches() -> None:
    bundle = MetadataBundle.load(DEFAULT_META_ROOT)

    assert all(not frame.empty for frame in bundle.frames.values())
    for filename, frame in bundle.frames.items():
        assert bundle.manifest["files"][filename]["rows"] == len(frame)


def test_phase1_types_are_json_serializable() -> None:
    instrument = _instrument()
    daily = next(iter(_daily("RB2601.SHF").values()))
    fee = FeeMarginSpec(
        schedule_id="RB-default",
        root_symbol="RB",
        contract_code="RB2601.SHF",
        margin_rate_long=0.12,
        margin_rate_short=0.12,
        open_fee_rate=0.0001,
        close_fee_rate=0.0001,
        close_today_fee_rate=0.0001,
        fee_per_lot_open=0.0,
        fee_per_lot_close=0.0,
        fee_per_lot_close_today=0.0,
        effective_from=_aware("2025-01-01 00:00"),
        effective_to=None,
        source="fixture",
        known_at=_aware("2024-12-01 00:00"),
    )

    json.dumps(instrument.to_dict())
    json.dumps(daily.to_dict())
    json.dumps(fee.to_dict())
