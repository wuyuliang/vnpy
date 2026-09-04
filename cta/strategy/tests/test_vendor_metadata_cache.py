"""Tests for the durable vendor metadata fetch cache."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest.vendor_metadata_cache import (
    CachingMetadataSourceClient,
)

YESTERDAY = date.today() - timedelta(days=1)
TOMORROW = date.today() + timedelta(days=1)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    @property
    def audit_records(self) -> tuple[dict[str, object], ...]:
        return ({"source": "vendor", "calls": len(self.calls)},)

    def _frame(self, tag: str) -> pd.DataFrame:
        return pd.DataFrame({"tag": [tag], "n": [len(self.calls)]})

    def fetch_calendar(self, exchange, start, end):
        self.calls.append(("calendar", exchange, start, end))
        return self._frame("calendar")

    def fetch_contracts(self, exchange):
        self.calls.append(("contracts", exchange))
        return self._frame("contracts")

    def fetch_settlements(self, trade_date):
        self.calls.append(("settlements", trade_date))
        return self._frame("settlements")

    def fetch_daily_quote(self, contract_code, trade_date):
        self.calls.append(("quote", contract_code, trade_date))
        return self._frame("quote")

    def fetch_vendor_parameters(self, source_date):
        self.calls.append(("vendor", source_date))
        return self._frame("vendor")

    def fetch_trading_rules(self, source_date):
        self.calls.append(("rules", source_date))
        return self._frame("rules")

    def fetch_czce_settlement_parameters(self, source_date):
        self.calls.append(("czce", source_date))
        return self._frame("czce")

    def fetch_shfe_settlement_parameters(self, source_date):
        self.calls.append(("shfe", source_date))
        return self._frame("shfe")

    def fetch_ine_settlement_parameters(self, source_date):
        self.calls.append(("ine", source_date))
        return self._frame("ine")


def test_historical_fetch_is_downloaded_once(tmp_path) -> None:
    inner = _Recorder()
    client = CachingMetadataSourceClient(inner, tmp_path)
    first = client.fetch_settlements(YESTERDAY)
    second = client.fetch_settlements(YESTERDAY)
    assert len(inner.calls) == 1
    pd.testing.assert_frame_equal(first, second)
    assert client.cache_stats["hits"] == 1
    assert client.cache_stats["misses"] == 1


def test_cache_survives_a_new_client_instance(tmp_path) -> None:
    inner = _Recorder()
    CachingMetadataSourceClient(inner, tmp_path).fetch_settlements(YESTERDAY)
    second_inner = _Recorder()
    CachingMetadataSourceClient(second_inner, tmp_path).fetch_settlements(YESTERDAY)
    assert not second_inner.calls


def test_today_and_future_dates_are_not_cached(tmp_path) -> None:
    inner = _Recorder()
    client = CachingMetadataSourceClient(inner, tmp_path)
    client.fetch_settlements(date.today())
    client.fetch_settlements(date.today())
    assert len(inner.calls) == 2
    assert client.cache_stats["bypassed"] == 2


def test_calendar_window_ending_in_the_future_is_not_cached(tmp_path) -> None:
    inner = _Recorder()
    client = CachingMetadataSourceClient(inner, tmp_path)
    client.fetch_calendar("SHFE", YESTERDAY, TOMORROW)
    client.fetch_calendar("SHFE", YESTERDAY, TOMORROW)
    assert len(inner.calls) == 2


def test_calendar_window_fully_in_the_past_is_cached(tmp_path) -> None:
    inner = _Recorder()
    client = CachingMetadataSourceClient(inner, tmp_path)
    start = YESTERDAY - timedelta(days=30)
    client.fetch_calendar("SHFE", start, YESTERDAY)
    client.fetch_calendar("SHFE", start, YESTERDAY)
    assert len(inner.calls) == 1


def test_contract_listing_refreshes_daily(tmp_path) -> None:
    inner = _Recorder()
    client = CachingMetadataSourceClient(inner, tmp_path)
    client.fetch_contracts("SHFE")
    client.fetch_contracts("SHFE")
    assert len(inner.calls) == 1
    # 换一天等于换 key，会重新拉取
    stale = list((tmp_path / "fetch_contracts").glob("*.parquet"))
    assert len(stale) == 1
    assert date.today().isoformat().replace("-", "") in stale[0].name.replace("-", "")


def test_distinct_arguments_do_not_collide(tmp_path) -> None:
    inner = _Recorder()
    client = CachingMetadataSourceClient(inner, tmp_path)
    client.fetch_daily_quote("RB2605.SHF", YESTERDAY)
    client.fetch_daily_quote("RB2609.SHF", YESTERDAY)
    client.fetch_daily_quote("RB2605.SHF", YESTERDAY - timedelta(days=1))
    assert len(inner.calls) == 3
    assert client.cache_stats["hits"] == 0


@pytest.mark.parametrize(
    "method",
    [
        "fetch_vendor_parameters",
        "fetch_trading_rules",
        "fetch_czce_settlement_parameters",
        "fetch_shfe_settlement_parameters",
        "fetch_ine_settlement_parameters",
    ],
)
def test_every_dated_table_is_cached(tmp_path, method: str) -> None:
    inner = _Recorder()
    client = CachingMetadataSourceClient(inner, tmp_path)
    getattr(client, method)(YESTERDAY)
    getattr(client, method)(YESTERDAY)
    assert len(inner.calls) == 1


def test_audit_records_include_cache_hits_and_inner_records(tmp_path) -> None:
    inner = _Recorder()
    client = CachingMetadataSourceClient(inner, tmp_path)
    client.fetch_settlements(YESTERDAY)
    client.fetch_settlements(YESTERDAY)
    records = client.audit_records
    assert any(item.get("source") == "cache" for item in records)
    assert any(item.get("source") == "vendor" for item in records)


def test_unwritable_cache_root_still_serves_the_fetch(tmp_path) -> None:
    target = tmp_path / "file"
    target.write_text("not a directory")
    client = CachingMetadataSourceClient(_Recorder(), target)
    frame = client.fetch_settlements(YESTERDAY)
    assert not frame.empty
