import json
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from stock.etf.regime_data import (
    build_causal_bars,
    download_regime_inputs,
    normalize_trade_calendar,
)


def _daily(periods: int = 6) -> pd.DataFrame:
    close = np.arange(10.0, 10.0 + periods)
    return pd.DataFrame(
        {
            "ts_code": ["159915.SZ"] * periods,
            "trade_date": pd.bdate_range("2026-01-02", periods=periods),
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "vol": np.arange(1_000.0, 1_000.0 + periods),
            "amount": np.arange(2_000.0, 2_000.0 + periods),
        }
    )


def test_raw_mode_preserves_prices_and_volume() -> None:
    daily = _daily()

    bars = build_causal_bars(daily, None, "raw")

    np.testing.assert_allclose(bars["close"], daily["close"])
    np.testing.assert_allclose(bars["volume"], daily["vol"])
    np.testing.assert_allclose(bars["turnover"], daily["amount"])
    assert bars["factor_source_date"].isna().all()
    assert bars["adj_factor"].eq(1.0).all()
    assert bars["price_adjustment_mode"].eq("raw").all()


def test_point_in_time_adjustment_uses_same_or_prior_factor_only() -> None:
    daily = _daily(4)
    factors = pd.DataFrame(
        {
            "ts_code": ["159915.SZ", "159915.SZ"],
            "trade_date": [daily.iloc[0]["trade_date"], daily.iloc[2]["trade_date"]],
            "adj_factor": [1.0, 2.0],
        }
    )

    bars = build_causal_bars(daily, factors, "point_in_time_adjusted")

    np.testing.assert_allclose(
        bars["close"],
        daily["close"].to_numpy() * np.array([1.0, 1.0, 2.0, 2.0]),
    )
    np.testing.assert_allclose(
        bars["volume"],
        daily["vol"].to_numpy() / np.array([1.0, 1.0, 2.0, 2.0]),
    )
    np.testing.assert_allclose(bars["turnover"], daily["amount"])
    assert bars["factor_source_date"].tolist() == [
        daily.iloc[0]["trade_date"],
        daily.iloc[0]["trade_date"],
        daily.iloc[2]["trade_date"],
        daily.iloc[2]["trade_date"],
    ]
    assert (bars["factor_source_date"] <= bars["datetime"]).all()


def test_point_in_time_adjustment_rejects_missing_initial_factor() -> None:
    daily = _daily(4)
    factors = pd.DataFrame(
        {
            "ts_code": ["159915.SZ"],
            "trade_date": [daily.iloc[1]["trade_date"]],
            "adj_factor": [1.0],
        }
    )

    with pytest.raises(ValueError, match="initial adjustment factor"):
        build_causal_bars(daily, factors, "point_in_time_adjusted")


def test_mutating_future_factor_does_not_change_adjusted_prefix() -> None:
    daily = _daily(8)
    factors = pd.DataFrame(
        {
            "ts_code": ["159915.SZ"] * 8,
            "trade_date": daily["trade_date"],
            "adj_factor": np.linspace(1.0, 1.7, 8),
        }
    )
    cutoff = pd.Timestamp(daily.iloc[4]["trade_date"])
    mutated = factors.copy()
    mutated.loc[mutated["trade_date"] > cutoff, "adj_factor"] *= 10_000.0

    original = build_causal_bars(
        daily,
        factors,
        "point_in_time_adjusted",
    )
    changed = build_causal_bars(
        daily,
        mutated,
        "point_in_time_adjusted",
    )

    historical = original["datetime"] <= cutoff
    pd.testing.assert_frame_equal(
        original.loc[historical].reset_index(drop=True),
        changed.loc[historical].reset_index(drop=True),
        check_exact=True,
    )


def test_trade_calendar_normalization_keeps_open_and_closed_dates() -> None:
    raw = pd.DataFrame(
        {
            "cal_date": ["20260105", "20260102", "20260103"],
            "is_open": ["1", "1", "0"],
            "exchange": ["SZSE", "SZSE", "SZSE"],
        }
    )

    calendar = normalize_trade_calendar(raw)

    assert calendar["datetime"].tolist() == [
        pd.Timestamp("2026-01-02"),
        pd.Timestamp("2026-01-03"),
        pd.Timestamp("2026-01-05"),
    ]
    assert calendar["is_open"].tolist() == [1, 0, 1]


def test_download_adapter_does_not_persist_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "top-secret-token"

    class FakePro:
        def fund_daily(self, **_: object) -> pd.DataFrame:
            return _daily(4)

        def fund_adj(self, **_: object) -> pd.DataFrame:
            daily = _daily(4)
            return pd.DataFrame(
                {
                    "ts_code": daily["ts_code"],
                    "trade_date": daily["trade_date"],
                    "adj_factor": [1.0, 1.0, 1.0, 1.0],
                }
            )

        def trade_cal(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "exchange": ["SZSE"] * 10,
                    "cal_date": pd.date_range("2026-01-01", periods=10).strftime(
                        "%Y%m%d"
                    ),
                    "is_open": [0, 1, 0, 0, 1, 1, 1, 1, 0, 0],
                }
            )

    fake_tushare = SimpleNamespace(pro_api=lambda _: FakePro())
    monkeypatch.setitem(sys.modules, "tushare", fake_tushare)
    monkeypatch.setenv("TUSHARE_TOKEN", token)

    inputs = download_regime_inputs(
        "159915.SZ",
        "2026-01-02",
        "2026-01-07",
    )

    serialized_audit = json.dumps(inputs.source_audit)
    assert token not in serialized_audit
    assert len(inputs.daily) == 4
    assert len(inputs.factors) == 4
    assert not inputs.calendar.empty
