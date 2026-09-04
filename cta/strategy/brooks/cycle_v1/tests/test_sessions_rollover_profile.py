from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.instruments.profile import (
    TimeframeMetrics,
    calculate_timeframe_metrics,
    InstrumentResearchProfile,
    TimeframeConfig,
    evaluate_profile,
    select_timeframes,
)
from cta.strategy.brooks.cycle_v1.instruments.rollover import (
    AdjustmentEvent,
    ContractMapRecord,
    PointInTimeContinuousSeries,
    PointInTimeContractMapper,
    RollAdjustmentReference,
    RollFill,
    RollLeg,
    RollState,
    RollTracker,
    build_point_in_time_signal_bars,
    build_roll_adjustment,
)
from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
    TradingCalendarEntry,
    VersionedTradingCalendar,
    aggregate_completed_bars,
    aggregate_completed_daily_bars,
)
from cta.strategy.brooks.cycle_v1.core.multitimeframe import align_completed_snapshots


TZ = "Asia/Shanghai"


def _ts(value: str):
    return pd.Timestamp(value, tz=TZ).to_pydatetime()


def test_aggregation_does_not_cross_lunch_or_emit_incomplete_bucket() -> None:
    sessions = (
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(
                SessionSegment("morning", time(9), time(11, 30), time(9)),
                SessionSegment("afternoon", time(13, 30), time(15), time(13, 30)),
            ),
        ),
    )
    ends = list(pd.date_range("2026-01-05 11:26", periods=4, freq="1min", tz=TZ))
    ends += list(pd.date_range("2026-01-05 13:31", periods=6, freq="1min", tz=TZ))
    frame = pd.DataFrame(
        {
            "bar_end": ends,
            "open": np.arange(len(ends), dtype=float) + 100,
            "high": np.arange(len(ends), dtype=float) + 101,
            "low": np.arange(len(ends), dtype=float) + 99,
            "close": np.arange(len(ends), dtype=float) + 100.5,
            "volume": 10.0,
            "open_interest": 1_000.0,
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": date(2026, 1, 5),
        }
    )

    bars = aggregate_completed_bars(frame, minutes=5, sessions=sessions)

    assert len(bars) == 1
    assert bars.iloc[0]["bar_end"] == pd.Timestamp("2026-01-05 13:35", tz=TZ)
    assert bars.iloc[0]["volume"] == 50.0


def test_aggregation_rejects_duplicate_minute_that_masks_a_gap() -> None:
    sessions = (
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(SessionSegment("afternoon", time(13, 30), time(15), time(13, 30)),),
        ),
    )
    ends = pd.to_datetime(
        [
            "2026-01-05 13:31",
            "2026-01-05 13:32",
            "2026-01-05 13:32",
            "2026-01-05 13:34",
            "2026-01-05 13:35",
        ]
    ).tz_localize(TZ)
    frame = pd.DataFrame(
        {
            "bar_end": ends,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 10.0,
            "open_interest": 1_000.0,
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": date(2026, 1, 5),
        }
    )

    with pytest.raises(ValueError, match="unique"):
        aggregate_completed_bars(frame, minutes=5, sessions=sessions)


def test_session_segment_rejects_zero_duration() -> None:
    with pytest.raises(ValueError, match="nonzero duration"):
        SessionSegment("invalid", time(9), time(9), time(9))


def test_daily_aggregation_requires_every_declared_session_segment() -> None:
    sessions = (
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(
                SessionSegment("morning", time(9), time(9, 2), time(9)),
                SessionSegment("afternoon", time(13, 30), time(13, 32), time(13, 30)),
            ),
        ),
    )
    ends = pd.to_datetime(
        [
            "2026-01-05 09:01",
            "2026-01-05 09:02",
            "2026-01-05 13:31",
            "2026-01-05 13:32",
        ]
    ).tz_localize(TZ)
    frame = pd.DataFrame(
        {
            "bar_end": ends,
            "feature_sequence": [10, 20, 30, 40],
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": 10.0,
            "open_interest": [1_000.0, 1_001.0, 1_002.0, 1_003.0],
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": date(2026, 1, 5),
        }
    )

    daily = aggregate_completed_daily_bars(frame, sessions=sessions)
    incomplete = aggregate_completed_daily_bars(frame.iloc[:-1], sessions=sessions)

    assert len(daily) == 1
    assert daily.iloc[0]["bar_end"] == ends[-1]
    assert daily.iloc[0]["open"] == 100.0
    assert daily.iloc[0]["close"] == 103.5
    assert daily.iloc[0]["volume"] == 40.0
    assert daily.iloc[0]["feature_sequence"] == 40
    assert incomplete.empty


def test_contract_mapping_is_point_in_time_and_never_uses_future_record() -> None:
    mapper = PointInTimeContractMapper(
        [
            ContractMapRecord(
                "RB", date(2026, 1, 5), "RB2605.SHF", _ts("2026-01-04 15:30"), "OI"
            ),
            ContractMapRecord(
                "RB", date(2026, 1, 6), "RB2610.SHF", _ts("2026-01-05 15:30"), "OI"
            ),
        ]
    )

    assert mapper.resolve("RB", date(2026, 1, 5), _ts("2026-01-05 09:00")).contract_code == "RB2605.SHF"
    with pytest.raises(LookupError, match="BLOCKED_CONTRACT_MAPPING"):
        mapper.resolve("RB", date(2026, 1, 6), _ts("2026-01-05 14:00"))


def test_rollover_contracts_reject_missing_identity_and_nonfinite_values() -> None:
    with pytest.raises(ValueError, match="mapping identity"):
        ContractMapRecord("", date(2026, 1, 5), "", _ts("2026-01-04 15:30"), "")
    with pytest.raises(ValueError, match="offset"):
        AdjustmentEvent(
            root_symbol="RB",
            effective_from=_ts("2026-01-05 09:00"),
            scale=1.0,
            offset=float("nan"),
            adjustment_known_at=_ts("2026-01-04 15:30"),
            version="v1",
        )
    with pytest.raises(ValueError, match="known by its effective event"):
        AdjustmentEvent(
            root_symbol="RB",
            effective_from=_ts("2026-01-05 09:00"),
            scale=1.0,
            offset=0.0,
            adjustment_known_at=_ts("2026-01-05 09:01"),
            version="v1",
        )
    with pytest.raises(ValueError, match="invalid roll fill"):
        RollFill(
            leg=RollLeg.CLOSE_OLD,
            contract_code="RB2605.SHF",
            quantity=1,
            event=_ts("2026-01-05 14:01"),
            price=float("nan"),
            fee=1.0,
        )
    with pytest.raises(ValueError, match="invalid roll fill"):
        RollFill(
            leg=RollLeg.CLOSE_OLD,
            contract_code="RB2605.SHF",
            quantity=1.5,
            event=_ts("2026-01-05 14:01"),
            price=3_500.0,
            fee=1.0,
        )


def test_roll_tracker_requires_old_leg_fill_before_new_leg() -> None:
    tracker = RollTracker(active_contract="RB2605.SHF", active_quantity=4)

    tracker.request_roll("RB2610.SHF")
    assert tracker.state is RollState.CLOSE_OLD_PENDING
    with pytest.raises(ValueError, match="old contract"):
        tracker.open_new_filled(
            quantity=1,
            event=_ts("2026-01-05 14:01"),
            price=3_500.0,
            fee=1.0,
        )
    tracker.close_old_filled(
        quantity=2,
        event=_ts("2026-01-05 14:02"),
        price=3_490.0,
        fee=2.0,
    )
    assert tracker.state is RollState.CLOSE_OLD_PENDING
    assert tracker.closed_quantity == 2
    tracker.close_old_filled(
        quantity=2,
        event=_ts("2026-01-05 14:03"),
        price=3_488.0,
        fee=2.0,
    )
    assert tracker.state is RollState.OPEN_NEW_PENDING
    tracker.open_new_filled(
        quantity=1,
        event=_ts("2026-01-05 14:04"),
        price=3_520.0,
        fee=1.0,
    )
    assert tracker.state is RollState.OPEN_NEW_PENDING
    tracker.open_new_filled(
        quantity=3,
        event=_ts("2026-01-05 14:05"),
        price=3_522.0,
        fee=3.0,
    )
    assert tracker.state is RollState.ROLLED
    assert tracker.active_contract == "RB2610.SHF"
    assert tracker.active_quantity == 4
    assert tracker.total_fee == 8.0
    assert len(tracker.fills) == 4


def test_profile_eligibility_uses_only_asof_snapshot() -> None:
    profile = InstrumentResearchProfile(
        root_symbol="RB",
        sector="BLACK",
        timeframe_config=TimeframeConfig(
            base_tf="1m",
            large_tf="4h",
            medium_tf="30m",
            small_tf="5m",
            execution_tf="1m",
        ),
        median_atr_ticks=12.0,
        median_cost_to_atr=0.08,
        session_gap_atr_p95=0.5,
        zero_volume_ratio=0.0,
        bar_gap_ratio=0.0,
        roll_frequency=0.1,
        limit_event_rate=0.0,
        history_bars=10_000,
        history_sessions=600,
        metadata_coverage=1.0,
        profile_asof=_ts("2025-12-31 15:00"),
    )

    result = evaluate_profile(profile, load_config().profile)
    assert result.eligible
    assert result.reason_codes == ()


def _timeframe_metrics(timeframe: str, *, minutes: int, **changes) -> TimeframeMetrics:
    values = {
        "timeframe": timeframe,
        "duration_minutes": minutes,
        "complete_history_bars": 1_000,
        "bars_per_session": 20.0,
        "median_atr_ticks": 12.0,
        "median_cost_to_atr": 0.08,
        "zero_volume_ratio": 0.0,
        "bar_gap_ratio": 0.0,
    }
    values.update(changes)
    return TimeframeMetrics(**values)


def test_timeframe_selection_is_deterministic_and_not_pnl_driven() -> None:
    metrics = {
        "1m": _timeframe_metrics("1m", minutes=1),
        "5m": _timeframe_metrics("5m", minutes=5),
        "15m": _timeframe_metrics("15m", minutes=15),
        "30m": _timeframe_metrics("30m", minutes=30),
        "60m": _timeframe_metrics("60m", minutes=60),
        "4h": _timeframe_metrics(
            "4h", minutes=240, bars_per_session=1.0, median_cost_to_atr=9.0
        ),
        "1d": _timeframe_metrics(
            "1d", minutes=1_440, bars_per_session=1.0, median_cost_to_atr=9.0
        ),
    }

    selection = select_timeframes(
        metrics,
        load_config().profile,
        base_tf="1m",
        execution_tf="1m",
    )

    assert selection.reason_codes == ()
    assert selection.timeframes == TimeframeConfig(
        base_tf="1m",
        large_tf="30m",
        medium_tf="5m",
        small_tf="1m",
        execution_tf="1m",
    )


def test_timeframe_selection_fails_closed_when_role_has_no_usable_candidate() -> None:
    metrics = {
        "1m": _timeframe_metrics("1m", minutes=1),
        "5m": _timeframe_metrics("5m", minutes=5, bar_gap_ratio=0.10),
        "15m": _timeframe_metrics("15m", minutes=15, bar_gap_ratio=0.10),
        "30m": _timeframe_metrics("30m", minutes=30, bar_gap_ratio=0.10),
        "60m": _timeframe_metrics("60m", minutes=60),
        "4h": _timeframe_metrics("4h", minutes=240),
        "1d": _timeframe_metrics("1d", minutes=1_440),
    }

    selection = select_timeframes(
        metrics,
        load_config().profile,
        base_tf="1m",
        execution_tf="1m",
    )

    assert selection.timeframes is None
    assert selection.reason_codes == ("BLOCKED_MEDIUM_TIMEFRAME",)


def test_timeframe_metrics_are_built_from_explicit_asof_quality_columns() -> None:
    frame = pd.DataFrame(
        {
            "bar_end": pd.date_range("2026-01-05 09:05", periods=6, freq="5min", tz=TZ),
            "exchange_trade_date": [date(2026, 1, 5)] * 3 + [date(2026, 1, 6)] * 3,
            "atr": [10.0, 12.0, 14.0, 10.0, 12.0, 14.0],
            "round_trip_cost_price": [1.0] * 6,
            "volume": [10.0, 0.0, 10.0, 10.0, 10.0, 10.0],
            "unexpected_gap": [False, False, True, False, False, False],
        }
    )

    metrics = calculate_timeframe_metrics(
        frame,
        timeframe="5m",
        duration_minutes=5,
        price_tick=1.0,
    )

    assert metrics.complete_history_bars == 6
    assert metrics.bars_per_session == 3.0
    assert metrics.median_atr_ticks == 12.0
    assert metrics.median_cost_to_atr == pytest.approx(1.0 / 12.0)
    assert metrics.zero_volume_ratio == pytest.approx(1.0 / 6.0)
    assert metrics.bar_gap_ratio == pytest.approx(1.0 / 6.0)


def test_timeframe_metrics_reject_duplicate_completed_bar_events() -> None:
    frame = pd.DataFrame(
        {
            "bar_end": [
                pd.Timestamp("2026-01-05 09:05", tz=TZ),
                pd.Timestamp("2026-01-05 09:05", tz=TZ),
            ],
            "exchange_trade_date": [date(2026, 1, 5)] * 2,
            "atr": [10.0, 10.0],
            "round_trip_cost_price": [1.0, 1.0],
            "volume": [10.0, 10.0],
            "unexpected_gap": [False, False],
        }
    )

    with pytest.raises(ValueError, match="strictly increasing"):
        calculate_timeframe_metrics(
            frame,
            timeframe="5m",
            duration_minutes=5,
            price_tick=1.0,
        )


def test_versioned_calendar_maps_friday_night_to_known_monday_trade_date() -> None:
    calendar = VersionedTradingCalendar(
        [
            TradingCalendarEntry(
                exchange="SHFE",
                source_calendar_date=date(2026, 1, 9),
                session_id="night",
                exchange_trade_date=date(2026, 1, 12),
                effective_from=_ts("2026-01-09 00:00"),
                effective_to=_ts("2026-01-10 00:00"),
                known_at=_ts("2026-01-08 18:00"),
                source="SHFE",
            )
        ]
    )

    assert calendar.trade_date(
        "SHFE", date(2026, 1, 9), "night", _ts("2026-01-09 21:00")
    ) == date(2026, 1, 12)


def test_multitimeframe_alignment_respects_timestamp_and_sequence() -> None:
    decisions = pd.DataFrame(
        {
            "decision_asof": [
                _ts("2026-01-05 09:10"),
                _ts("2026-01-05 09:15"),
            ],
            "decision_sequence": [30, 10],
        }
    )
    higher = pd.DataFrame(
        {
            "feature_asof": [
                _ts("2026-01-05 09:00"),
                _ts("2026-01-05 09:15"),
                _ts("2026-01-05 09:20"),
            ],
            "feature_sequence": [20, 20, 20],
            "cycle": ["OLD", "SAME_TIME_TOO_LATE", "FUTURE"],
        }
    )

    aligned = align_completed_snapshots(decisions, higher)

    assert aligned["cycle"].tolist() == ["OLD", "OLD"]


def test_multitimeframe_alignment_rejects_nonincreasing_decisions() -> None:
    decisions = pd.DataFrame(
        {
            "decision_asof": [
                _ts("2026-01-05 09:15"),
                _ts("2026-01-05 09:10"),
            ],
            "decision_sequence": [10, 10],
        }
    )
    higher = pd.DataFrame(
        {
            "feature_asof": [_ts("2026-01-05 09:00")],
            "feature_sequence": [20],
        }
    )

    with pytest.raises(ValueError, match="decision events must be strictly increasing"):
        align_completed_snapshots(decisions, higher)


def test_continuous_series_append_never_rewrites_existing_prefix() -> None:
    series = PointInTimeContinuousSeries("RB")
    mapping = ContractMapRecord(
        "RB", date(2026, 1, 5), "RB2605.SHF", _ts("2026-01-04 15:30"), "OI"
    )
    adjustment = AdjustmentEvent(
        "RB", _ts("2026-01-01 00:00"), 1.0, 0.0, _ts("2025-12-31 15:30"), "v1"
    )
    series.append(
        bar_end=_ts("2026-01-05 09:05"),
        raw_price=3500.0,
        mapping=mapping,
        adjustment=adjustment,
    )
    prefix = series.to_frame().copy(deep=True)
    series.append(
        bar_end=_ts("2026-01-05 09:10"),
        raw_price=3510.0,
        mapping=mapping,
        adjustment=adjustment,
    )

    pd.testing.assert_frame_equal(prefix, series.to_frame().iloc[:1].reset_index(drop=True))


def test_roll_adjustment_preserves_known_overlap_price_without_rewriting_prefix() -> None:
    previous = AdjustmentEvent(
        "RB", _ts("2026-01-01 00:00"), 1.0, 10.0, _ts("2025-12-31 15:30"), "v1"
    )

    current = build_roll_adjustment(
        previous=previous,
        old_raw_price=3_500.0,
        new_raw_price=3_540.0,
        effective_from=_ts("2026-01-06 09:00"),
        adjustment_known_at=_ts("2026-01-05 15:30"),
        version="v2",
    )

    assert current.adjusted(3_540.0) == previous.adjusted(3_500.0)
    assert current.scale == previous.scale


def test_point_in_time_signal_bars_adjust_roll_without_rewriting_raw_prefix() -> None:
    bars = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                [
                    "2026-01-05 14:59",
                    "2026-01-05 15:00",
                    "2026-01-06 09:01",
                    "2026-01-06 09:02",
                ]
            ).tz_localize(TZ),
            "open": [3_499.0, 3_500.0, 3_519.0, 3_522.0],
            "high": [3_501.0, 3_502.0, 3_523.0, 3_525.0],
            "low": [3_498.0, 3_499.0, 3_518.0, 3_521.0],
            "close": [3_500.0, 3_501.0, 3_522.0, 3_524.0],
            "contract_code": [
                "RB2605.SHF",
                "RB2605.SHF",
                "RB2610.SHF",
                "RB2610.SHF",
            ],
        }
    )
    reference = RollAdjustmentReference(
        root_symbol="RB",
        old_contract="RB2605.SHF",
        new_contract="RB2610.SHF",
        effective_from=_ts("2026-01-06 09:01"),
        old_reference_price=3_500.0,
        new_reference_price=3_520.0,
        known_at=_ts("2026-01-06 09:00"),
        source="SHFE_PRE_SETTLEMENT",
        version="RB-20260106-v1",
    )

    adjusted = build_point_in_time_signal_bars(
        bars,
        root_symbol="RB",
        references=(reference,),
    )
    future = bars.copy()
    future.loc[3, ["open", "high", "low", "close"]] = [
        9_000,
        9_010,
        8_990,
        9_005,
    ]
    extended = build_point_in_time_signal_bars(
        future,
        root_symbol="RB",
        references=(reference,),
    )

    assert adjusted[["open", "high", "low", "close"]].equals(
        bars[["open", "high", "low", "close"]]
    )
    assert list(adjusted["adjustment_offset"]) == [0.0, 0.0, -20.0, -20.0]
    assert adjusted.loc[2, "signal_open"] == 3_499.0
    assert adjusted.loc[2, "signal_close"] == 3_502.0
    pd.testing.assert_frame_equal(
        adjusted.loc[
            :2,
            ["signal_open", "signal_high", "signal_low", "signal_close"],
        ],
        extended.loc[
            :2,
            ["signal_open", "signal_high", "signal_low", "signal_close"],
        ],
    )
