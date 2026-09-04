from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.legacy_adapters import scalp as adapter
from cta.strategy.brooks.scalp.session import SessionSegment, SessionSpec


TZ = ZoneInfo("Asia/Shanghai")


def test_legacy_loader_attaches_explicit_point_in_time_costs(monkeypatch) -> None:
    session_open = datetime(2026, 1, 5, 9, tzinfo=TZ)
    bars = pd.DataFrame(
        {
            "bar_end": pd.date_range(
                "2026-01-05 09:01", periods=2, freq="min", tz="Asia/Shanghai"
            ),
            "open": [99.5, 100.0],
            "high": [100.5, 101.0],
            "low": [99.0, 99.5],
            "close": [100.0, 100.5],
            "volume": [10.0, 11.0],
            "turnover": [1_000.0, 1_105.5],
            "open_interest": [1_000.0, 1_001.0],
            "contract_code": ["RB2605.SHF"] * 2,
            "exchange_trade_date": [date(2026, 1, 5)] * 2,
            "session_open": [session_open] * 2,
        }
    )
    sessions = (
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(SessionSegment("day", time(9), time(10), time(9)),),
        ),
    )
    instrument = SimpleNamespace(
        root_symbol="RB",
        exchange="SHFE",
        price_tick=0.5,
        contract_size=10.0,
        slippage_ticks_base=1.0,
        sessions=sessions,
    )
    fee = SimpleNamespace(
        open_fee_rate=0.001,
        close_fee_rate=0.001,
        close_today_fee_rate=0.002,
        fee_per_lot_open=1.0,
        fee_per_lot_close=1.0,
        fee_per_lot_close_today=2.0,
    )
    prepared = SimpleNamespace(
        normalized_frames=(bars,),
        instruments={"RB2605.SHF": instrument},
        fee_specs={("RB2605.SHF", date(2026, 1, 5), session_open): fee},
        files=(Path("RB/2026-01-05.parquet"),),
    )
    monkeypatch.setattr(adapter, "_prepare_symbol", lambda **kwargs: prepared)

    loaded = adapter.load_normalized_symbol(
        symbol="RB0.SHFE",
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=Path("unused"),
        metadata=object(),
        config=object(),
    )

    assert loaded.root_symbol == "RB"
    assert loaded.minute_bars["price_tick"].eq(0.5).all()
    assert loaded.minute_bars.iloc[0]["base_round_trip_cost_price"] == pytest.approx(
        1.6
    )
    assert loaded.sessions[0].segments[0].segment_id == "day"


def test_legacy_loader_uses_daily_contract_mechanics(monkeypatch) -> None:
    first_open = datetime(2026, 5, 8, 9, tzinfo=TZ)
    second_open = datetime(2026, 5, 11, 9, tzinfo=TZ)
    bars = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2026-05-08 09:01", "2026-05-11 09:01"]
            ).tz_localize("Asia/Shanghai"),
            "open": [1500.0, 1500.5],
            "high": [1500.1, 1501.0],
            "low": [1499.9, 1500.0],
            "close": [1500.1, 1500.5],
            "volume": [10.0, 11.0],
            "turnover": [1_000.0, 1_100.0],
            "open_interest": [1_000.0, 1_001.0],
            "contract_code": ["EC2606.INE", "EC2606.INE"],
            "exchange_trade_date": [date(2026, 5, 8), date(2026, 5, 11)],
            "session_open": [first_open, second_open],
        }
    )
    sessions = (
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(SessionSegment("day", time(9), time(10), time(9)),),
        ),
    )
    instrument = SimpleNamespace(
        root_symbol="EC",
        exchange="INE",
        price_tick=0.5,
        contract_size=1.0,
        slippage_ticks_base=1.0,
        sessions=sessions,
    )
    fee = SimpleNamespace(
        open_fee_rate=0.0,
        close_fee_rate=0.0,
        close_today_fee_rate=0.0,
        fee_per_lot_open=1.0,
        fee_per_lot_close=1.0,
        fee_per_lot_close_today=1.0,
    )
    prepared = SimpleNamespace(
        normalized_frames=(bars,),
        instruments={"EC2606.INE": instrument},
        daily_specs={
            ("EC2606.INE", date(2026, 5, 8)): SimpleNamespace(
                contract_size=50.0,
                price_tick=0.1,
            ),
            ("EC2606.INE", date(2026, 5, 11)): SimpleNamespace(
                contract_size=50.0,
                price_tick=0.5,
            ),
        },
        fee_specs={
            ("EC2606.INE", date(2026, 5, 8), first_open): fee,
            ("EC2606.INE", date(2026, 5, 11), second_open): fee,
        },
        files=(Path("EC/2026-05.parquet"),),
    )
    monkeypatch.setattr(adapter, "_prepare_symbol", lambda **kwargs: prepared)

    loaded = adapter.load_normalized_symbol(
        symbol="EC0.INE",
        start=date(2026, 5, 8),
        end=date(2026, 5, 11),
        data_root=Path("unused"),
        metadata=object(),
        config=object(),
    )

    assert loaded.minute_bars["price_tick"].tolist() == [0.1, 0.5]
    assert loaded.minute_bars["base_round_trip_cost_price"].tolist() == pytest.approx(
        [0.24, 1.04]
    )


def test_legacy_loader_reconciles_only_identical_partition_overlap(monkeypatch) -> None:
    prepared = _overlapping_prepared(close_right=100.0)
    monkeypatch.setattr(adapter, "_prepare_symbol", lambda **kwargs: prepared)

    loaded = adapter.load_normalized_symbol(
        symbol="RB0.SHFE",
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=Path("unused"),
        metadata=object(),
        config=object(),
    )

    assert len(loaded.minute_bars) == 1
    assert loaded.overlapping_rows_removed == 1


def test_legacy_loader_blocks_conflicting_duplicate_bar(monkeypatch) -> None:
    prepared = _overlapping_prepared(close_right=100.25)
    monkeypatch.setattr(adapter, "_prepare_symbol", lambda **kwargs: prepared)

    with pytest.raises(
        ValueError,
        match=r"CONFLICTING_DUPLICATE_BAR.*RB2605\.SHF.*2026-01-05 09:01",
    ):
        adapter.load_normalized_symbol(
            symbol="RB0.SHFE",
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            data_root=Path("unused"),
            metadata=object(),
            config=object(),
        )


def test_legacy_loader_keeps_scalp_audited_roll_session_exclusions(monkeypatch) -> None:
    session_open = datetime(2026, 1, 5, 9, tzinfo=TZ)
    bars = pd.DataFrame(
        {
            "bar_end": pd.date_range(
                "2026-01-05 09:01", periods=2, freq="min", tz="Asia/Shanghai"
            ),
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "volume": [10.0, 10.0],
            "turnover": [1_000.0, 1_000.0],
            "open_interest": [1_000.0, 1_000.0],
            "contract_code": ["RB2605.SHF", "RB2605.SHF"],
            "exchange_trade_date": [date(2026, 1, 5)] * 2,
            "session_open": [session_open] * 2,
            "session_id": ["20260105:excluded", "20260105:day"],
        }
    )
    prepared = _overlapping_prepared(close_right=100.0)
    prepared.normalized_frames = (bars,)
    prepared.excluded_roll_sessions = {"20260105:excluded"}
    monkeypatch.setattr(adapter, "_prepare_symbol", lambda **kwargs: prepared)

    loaded = adapter.load_normalized_symbol(
        symbol="RB0.SHFE",
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=Path("unused"),
        metadata=object(),
        config=object(),
    )

    assert len(loaded.minute_bars) == 1
    assert loaded.minute_bars.iloc[0]["bar_end"].minute == 2


def test_legacy_loader_builds_roll_adjustment_from_audited_settlement_references(
    monkeypatch,
) -> None:
    first_open = datetime(2026, 1, 5, 9, tzinfo=TZ)
    roll_open = datetime(2026, 1, 6, 9, tzinfo=TZ)
    bars = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                [
                    "2026-01-05 09:01",
                    "2026-01-05 09:02",
                    "2026-01-06 09:01",
                    "2026-01-06 09:02",
                ]
            ).tz_localize("Asia/Shanghai"),
            "open": [3_499.0, 3_500.0, 3_519.0, 3_522.0],
            "high": [3_501.0, 3_502.0, 3_523.0, 3_525.0],
            "low": [3_498.0, 3_499.0, 3_518.0, 3_521.0],
            "close": [3_500.0, 3_501.0, 3_522.0, 3_524.0],
            "volume": 10.0,
            "turnover": 35_000.0,
            "open_interest": 1_000.0,
            "contract_code": [
                "RB2605.SHF",
                "RB2605.SHF",
                "RB2610.SHF",
                "RB2610.SHF",
            ],
            "exchange_trade_date": [
                date(2026, 1, 5),
                date(2026, 1, 5),
                date(2026, 1, 6),
                date(2026, 1, 6),
            ],
            "session_open": [first_open, first_open, roll_open, roll_open],
        }
    )
    prepared = _overlapping_prepared(close_right=100.0)
    prepared.normalized_frames = (bars,)
    prepared.instruments["RB2610.SHF"] = prepared.instruments["RB2605.SHF"]
    fee = next(iter(prepared.fee_specs.values()))
    prepared.fee_specs = {
        ("RB2605.SHF", date(2026, 1, 5), first_open): fee,
        ("RB2610.SHF", date(2026, 1, 6), roll_open): fee,
    }
    reference_known_at = datetime(2026, 1, 5, 15, 30, tzinfo=TZ)
    calls: list[tuple[str, str, date]] = []

    def settlement_reference(contract: str, trade_date: date):
        calls.append(("settlement", contract, trade_date))
        return SimpleNamespace(
            price=3_500.0,
            known_at=reference_known_at,
            source="SHFE_KX_JS_RULE_DERIVED",
        )

    def pre_settlement_reference(contract: str, trade_date: date):
        calls.append(("pre_settlement", contract, trade_date))
        return SimpleNamespace(
            price=3_520.0,
            known_at=reference_known_at,
            source="SHFE_KX_JS_RULE_DERIVED",
        )

    metadata = SimpleNamespace(
        settlement_reference=settlement_reference,
        pre_settlement_reference=pre_settlement_reference,
    )
    monkeypatch.setattr(adapter, "_prepare_symbol", lambda **kwargs: prepared)

    loaded = adapter.load_normalized_symbol(
        symbol="RB0.SHFE",
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        data_root=Path("unused"),
        metadata=metadata,
        config=object(),
    )

    assert list(loaded.minute_bars["adjustment_offset"]) == [
        0.0,
        0.0,
        -20.0,
        -20.0,
    ]
    assert loaded.minute_bars.iloc[2]["signal_close"] == 3_502.0
    assert calls == [
        ("settlement", "RB2605.SHF", date(2026, 1, 5)),
        ("pre_settlement", "RB2610.SHF", date(2026, 1, 6)),
    ]
    assert (
        loaded.minute_bars.iloc[2]["adjustment_source"]
        == "SHFE_PRE_SETTLEMENT:SHFE_KX_JS_RULE_DERIVED"
    )


def test_roll_adjustment_uses_pre_settlement_for_same_trade_date_roll() -> None:
    trade_date = date(2026, 4, 3)
    visible_at = datetime(2026, 4, 2, 15, 30, tzinfo=TZ)
    bars = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2026-04-02 21:01", "2026-04-03 09:01"]
            ).tz_localize("Asia/Shanghai"),
            "contract_code": ["HC2605.SHF", "HC2610.SHF"],
            "exchange_trade_date": [trade_date, trade_date],
            "session_open": [
                datetime(2026, 4, 2, 21, tzinfo=TZ),
                datetime(2026, 4, 3, 9, tzinfo=TZ),
            ],
        }
    )
    calls: list[tuple[str, str, date]] = []

    def settlement_reference(contract: str, reference_date: date):
        calls.append(("settlement", contract, reference_date))
        return SimpleNamespace(
            price=3_500.0,
            known_at=datetime(2026, 4, 3, 15, 30, tzinfo=TZ),
            source="SHFE_KX_JS_RULE_DERIVED",
        )

    def pre_settlement_reference(contract: str, reference_date: date):
        calls.append(("pre_settlement", contract, reference_date))
        return SimpleNamespace(
            price=3_500.0 if contract == "HC2605.SHF" else 3_520.0,
            known_at=visible_at,
            source="SHFE_KX_JS_RULE_DERIVED",
        )

    metadata = SimpleNamespace(
        settlement_reference=settlement_reference,
        pre_settlement_reference=pre_settlement_reference,
    )

    references = adapter._roll_adjustment_references(
        bars,
        root_symbol="HC",
        metadata=metadata,
    )

    assert len(references) == 1
    assert references[0].old_reference_price == 3_500.0
    assert references[0].new_reference_price == 3_520.0
    assert references[0].known_at == visible_at
    assert calls == [
        ("pre_settlement", "HC2605.SHF", trade_date),
        ("pre_settlement", "HC2610.SHF", trade_date),
    ]


def _overlapping_prepared(*, close_right: float):
    session_open = datetime(2026, 1, 5, 9, tzinfo=TZ)
    left = pd.DataFrame(
        {
            "bar_end": [pd.Timestamp("2026-01-05 09:01", tz="Asia/Shanghai")],
            "open": [99.5],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [10.0],
            "turnover": [1_000.0],
            "open_interest": [1_000.0],
            "contract_code": ["RB2605.SHF"],
            "exchange_trade_date": [date(2026, 1, 5)],
            "session_open": [session_open],
            "source_path": ["left.parquet"],
            "source_row": [0],
        }
    )
    right = left.copy()
    right.loc[0, "close"] = close_right
    right.loc[0, "source_path"] = "right.parquet"
    sessions = (
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(SessionSegment("day", time(9), time(10), time(9)),),
        ),
    )
    instrument = SimpleNamespace(
        root_symbol="RB",
        exchange="SHFE",
        price_tick=0.5,
        contract_size=10.0,
        slippage_ticks_base=1.0,
        sessions=sessions,
    )
    fee = SimpleNamespace(
        open_fee_rate=0.001,
        close_fee_rate=0.001,
        close_today_fee_rate=0.002,
        fee_per_lot_open=1.0,
        fee_per_lot_close=1.0,
        fee_per_lot_close_today=2.0,
    )
    return SimpleNamespace(
        normalized_frames=(left, right),
        instruments={"RB2605.SHF": instrument},
        fee_specs={("RB2605.SHF", date(2026, 1, 5), session_open): fee},
        files=(Path("left.parquet"), Path("right.parquet")),
    )
