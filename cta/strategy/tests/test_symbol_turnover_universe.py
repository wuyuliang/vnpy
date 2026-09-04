"""Tests for the per-root turnover table and the turnover-share entry gate."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.data_code.build_symbol_turnover import (
    build_turnover_table,
    load_contract_sizes,
    load_turnover_table,
)
from cta.strategy.multi_timeframe_trend_backtest.engine import (
    _turnover_share_blocked,
    turnover_eligible_by_date,
)


def _table(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"root_symbol": r, "trade_date": pd.Timestamp(d).date(), "turnover": v}
            for r, d, v in rows
        ]
    )


def _five_days(values: dict[str, float]) -> pd.DataFrame:
    rows = []
    for day in ("2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"):
        for root, value in values.items():
            rows.append((root, day, value))
    return _table(rows)


def _write_calendar(
    meta_root,
    rows: list[tuple[str, str, str]],
) -> None:
    bundle = meta_root / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "exchange": exchange,
                "exchange_trade_date": trade_date,
                "is_open": 1,
                "prior_open_date": prior_open,
                "next_open_date": next_open,
                "night_session_start": "21:00:00",
            }
            for exchange, trade_date, next_open in rows
            for prior_open in [trade_date]
        ]
    ).to_csv(bundle / "exchange_calendar.csv", index=False)


# --------------------------------------------------------------------------
# 队列计算
# --------------------------------------------------------------------------
def test_cohort_is_the_smallest_prefix_reaching_the_share() -> None:
    table = _five_days({"AU": 50.0, "AG": 30.0, "RB": 15.0, "MA": 5.0})
    eligible = turnover_eligible_by_date(table, share=0.60, lookback_days=5)
    cohort = eligible[date(2026, 1, 9)]
    # AU 50% 不足 60%，加 AG 到 80% 才够
    assert cohort == frozenset({"AU", "AG"})


def test_higher_share_admits_more_roots() -> None:
    table = _five_days({"AU": 50.0, "AG": 30.0, "RB": 15.0, "MA": 5.0})
    # 累计到 RB 恰好 95%，队列止步于 RB；再高一点才轮到 MA
    assert turnover_eligible_by_date(table, share=0.95, lookback_days=5)[
        date(2026, 1, 9)
    ] == frozenset({"AU", "AG", "RB"})
    assert turnover_eligible_by_date(table, share=0.99, lookback_days=5)[
        date(2026, 1, 9)
    ] == frozenset({"AU", "AG", "RB", "MA"})


def test_cohort_never_uses_the_current_day_turnover() -> None:
    # RB 只在最后一天暴量，当天的队列不应因此把 RB 拉进来
    rows = [("AU", d, 100.0) for d in ("2026-01-05", "2026-01-06", "2026-01-07")]
    rows += [("RB", d, 1.0) for d in ("2026-01-05", "2026-01-06", "2026-01-07")]
    rows.append(("RB", "2026-01-08", 100_000.0))
    rows.append(("AU", "2026-01-08", 100.0))
    eligible = turnover_eligible_by_date(_table(rows), share=0.60, lookback_days=5)
    assert "RB" not in eligible[date(2026, 1, 8)]
    assert "AU" in eligible[date(2026, 1, 8)]


def test_turnover_is_summed_across_contracts_of_one_root() -> None:
    rows = [
        ("RB", "2026-01-05", 40.0),
        ("RB", "2026-01-05", 40.0),  # 同根不同合约
        ("AU", "2026-01-05", 70.0),
        ("RB", "2026-01-06", 1.0),
        ("AU", "2026-01-06", 1.0),
    ]
    eligible = turnover_eligible_by_date(_table(rows), share=0.50, lookback_days=5)
    # RB 两个合约合并后 80 > AU 70，独自覆盖 80/150=53% 已过 50% 门槛
    assert eligible[date(2026, 1, 6)] == frozenset({"RB"})


def test_empty_or_disabled_table_yields_no_mapping() -> None:
    assert turnover_eligible_by_date(None, share=0.6, lookback_days=5) == {}
    assert turnover_eligible_by_date(pd.DataFrame(), share=0.6, lookback_days=5) == {}
    table = _five_days({"AU": 1.0})
    assert turnover_eligible_by_date(table, share=0.0, lookback_days=5) == {}


# --------------------------------------------------------------------------
# 闸门
# --------------------------------------------------------------------------
def test_gate_blocks_a_root_outside_the_cohort() -> None:
    eligible = {date(2026, 1, 9): frozenset({"AU", "AG"})}
    assert not _turnover_share_blocked("AU", date(2026, 1, 9), eligible)
    assert "cohort_size=2" in _turnover_share_blocked("RB", date(2026, 1, 9), eligible)


def test_gate_fails_open_without_data_for_that_day() -> None:
    eligible = {date(2026, 1, 9): frozenset({"AU"})}
    assert not _turnover_share_blocked("RB", date(2026, 1, 12), eligible)
    assert not _turnover_share_blocked("RB", date(2026, 1, 9), {})


def test_gate_is_case_insensitive() -> None:
    eligible = {date(2026, 1, 9): frozenset({"AU"})}
    assert not _turnover_share_blocked("au", date(2026, 1, 9), eligible)


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def test_turnover_gate_is_off_by_default() -> None:
    assert MultiTimeframeTrendConfig().turnover_share_threshold == 0.0
    assert MultiTimeframeTrendConfig().turnover_lookback_days == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"turnover_share_threshold": -0.1},
        {"turnover_share_threshold": 1.5},
        {"turnover_lookback_days": 0},
    ],
)
def test_invalid_turnover_config_is_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        MultiTimeframeTrendConfig(**kwargs)


# --------------------------------------------------------------------------
# 建表
# --------------------------------------------------------------------------
def test_build_prefers_minute_turnover_and_sums_contracts(tmp_path) -> None:
    minute = tmp_path / "minute" / "RB"
    minute.mkdir(parents=True)
    pd.DataFrame(
        {
            "datetime": ["2026-01-05 09:01:00", "2026-01-05 09:02:00"],
            "contract_code": ["RB2605.SHF", "RB2610.SHF"],
            "exchange": ["SHFE", "SHFE"],
            "turnover": [30.0, 70.0],
        }
    ).to_parquet(minute / "2026-01-05.parquet", index=False)
    _write_calendar(
        tmp_path / "meta",
        [("SHFE", "2026-01-05", "2026-01-06")],
    )
    frame, audit = build_turnover_table(
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        minute_root=tmp_path / "minute",
        day_root=tmp_path / "day",
        meta_cache_root=tmp_path / "meta",
    )
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["turnover"] == pytest.approx(100.0)
    assert row["contracts"] == 2
    assert row["source"] == "minute"
    assert row["date_semantics"] == "exchange_trade_date"
    assert audit["roots_from_minute"] == ["RB"]


def test_minute_turnover_maps_night_session_to_next_exchange_trade_date(
    tmp_path,
) -> None:
    minute = tmp_path / "minute" / "RB"
    minute.mkdir(parents=True)
    pd.DataFrame(
        {
            "datetime": ["2026-01-05 09:01:00", "2026-01-05 21:01:00"],
            "contract_code": ["RB2605.SHF", "RB2605.SHF"],
            "exchange": ["SHFE", "SHFE"],
            "turnover": [100.0, 30.0],
        }
    ).to_parquet(minute / "2026-01-05.parquet", index=False)
    pd.DataFrame(
        {
            "datetime": ["2026-01-06 00:01:00", "2026-01-06 09:01:00"],
            "contract_code": ["RB2605.SHF", "RB2605.SHF"],
            "exchange": ["SHFE", "SHFE"],
            "turnover": [70.0, 200.0],
        }
    ).to_parquet(minute / "2026-01-06.parquet", index=False)
    _write_calendar(
        tmp_path / "meta",
        [
            ("SHFE", "2026-01-05", "2026-01-06"),
            ("SHFE", "2026-01-06", "2026-01-07"),
        ],
    )

    frame, _ = build_turnover_table(
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        minute_root=tmp_path / "minute",
        day_root=tmp_path / "day",
        meta_cache_root=tmp_path / "meta",
    )

    values = frame.set_index("trade_date")["turnover"].to_dict()
    assert values == {
        date(2026, 1, 5): pytest.approx(100.0),
        date(2026, 1, 6): pytest.approx(300.0),
    }
    assert set(frame["date_semantics"]) == {"exchange_trade_date"}


def test_build_falls_back_to_daily_when_minute_is_absent(tmp_path) -> None:
    day = tmp_path / "day"
    day.mkdir(parents=True)
    pd.DataFrame(
        {
            "datetime": ["2026-01-05 00:00:00"],
            "close": [100.0],
            "volume": [20.0],
        }
    ).to_csv(day / "XX0.csv", index=False)
    meta = tmp_path / "meta" / "bundle"
    meta.mkdir(parents=True)
    pd.DataFrame({"root_symbol": ["XX"], "contract_size": [5.0]}).to_csv(
        meta / "contract_specs.csv", index=False
    )
    frame, audit = build_turnover_table(
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        minute_root=tmp_path / "minute",
        day_root=day,
        meta_cache_root=tmp_path / "meta",
    )
    assert frame.iloc[0]["turnover"] == pytest.approx(20.0 * 100.0 * 5.0)
    assert frame.iloc[0]["source"] == "daily_approx"
    assert audit["roots_missing_multiplier"] == []


def test_root_without_a_known_multiplier_is_reported_not_ranked(tmp_path) -> None:
    day = tmp_path / "day"
    day.mkdir(parents=True)
    pd.DataFrame(
        {"datetime": ["2026-01-05 00:00:00"], "close": [100.0], "volume": [20.0]}
    ).to_csv(day / "ZZ0.csv", index=False)
    frame, audit = build_turnover_table(
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        minute_root=tmp_path / "minute",
        day_root=day,
        meta_cache_root=tmp_path / "meta",
    )
    assert frame.empty
    assert audit["roots_missing_multiplier"] == ["ZZ"]


def test_load_turnover_table_is_tolerant_of_a_missing_file(tmp_path) -> None:
    assert load_turnover_table(tmp_path / "nope.parquet").empty


def test_load_turnover_table_rejects_legacy_natural_date_semantics(tmp_path) -> None:
    path = tmp_path / "legacy.parquet"
    pd.DataFrame(
        {
            "root_symbol": ["RB"],
            "trade_date": [date(2026, 1, 5)],
            "turnover": [100.0],
            "contracts": [1],
            "source": ["minute"],
        }
    ).to_parquet(path, index=False)

    result = load_turnover_table(path)

    assert result.empty
    assert "date_semantics" in result.columns


def test_load_contract_sizes_prefers_the_richest_bundle(tmp_path) -> None:
    small = tmp_path / "a"; small.mkdir(parents=True)
    pd.DataFrame({"root_symbol": ["RB"], "contract_size": [10.0]}).to_csv(
        small / "contract_specs.csv", index=False
    )
    big = tmp_path / "b"; big.mkdir(parents=True)
    pd.DataFrame(
        {"root_symbol": ["RB", "AU", "AG"], "contract_size": [10.0, 1000.0, 15.0]}
    ).to_csv(big / "contract_specs.csv", index=False)
    sizes = load_contract_sizes(tmp_path)
    assert sizes == {"RB": 10.0, "AU": 1000.0, "AG": 15.0}
