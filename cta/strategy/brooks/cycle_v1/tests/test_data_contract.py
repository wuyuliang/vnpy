from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.core.data_contract import validate_bar_contract


TZ = "Asia/Shanghai"


def _bars() -> pd.DataFrame:
    ends = pd.date_range("2026-01-05 09:01", periods=2, freq="1min", tz=TZ)
    return pd.DataFrame(
        {
            "source_calendar_date": [date(2026, 1, 5)] * 2,
            "exchange_trade_date": [date(2026, 1, 5)] * 2,
            "session_id": ["day:morning"] * 2,
            "bar_start": ends - pd.Timedelta(minutes=1),
            "bar_end": ends,
            "feature_asof": ends,
            "feature_sequence": [10, 20],
            "root_symbol": ["RB"] * 2,
            "vt_symbol": ["RB2605.SHFE"] * 2,
            "contract_code": ["RB2605.SHF"] * 2,
            "exchange": ["SHFE"] * 2,
            "open": [3_500.0, 3_501.0],
            "high": [3_502.0, 3_503.0],
            "low": [3_499.0, 3_500.0],
            "close": [3_501.0, 3_502.0],
            "volume": [10.0, 12.0],
            "turnover": [350_000.0, 420_000.0],
            "open_interest": [1_000.0, 1_002.0],
            "pre_settlement": [3_500.0] * 2,
            "limit_up": [3_850.0] * 2,
            "limit_down": [3_150.0] * 2,
            "source_path": ["rb.csv"] * 2,
            "source_row": [1, 2],
        }
    )


def test_minimum_bar_contract_accepts_auditable_completed_rows() -> None:
    validate_bar_contract(_bars())


def test_minimum_bar_contract_rejects_duplicate_contract_event() -> None:
    bars = _bars()
    bars.loc[1, "bar_end"] = bars.loc[0, "bar_end"]

    with pytest.raises(ValueError, match="unique"):
        validate_bar_contract(bars)


def test_minimum_bar_contract_rejects_invalid_ohlc_and_event_order() -> None:
    invalid_price = _bars()
    invalid_price.loc[1, "high"] = invalid_price.loc[1, "close"] - 1
    with pytest.raises(ValueError, match="OHLC"):
        validate_bar_contract(invalid_price)

    invalid_event = _bars()
    invalid_event.loc[1, "feature_asof"] = invalid_event.loc[1, "bar_start"]
    with pytest.raises(ValueError, match="feature_asof"):
        validate_bar_contract(invalid_event)


def test_minimum_bar_contract_requires_asia_shanghai_timezone() -> None:
    bars = _bars()
    for column in ("bar_start", "bar_end", "feature_asof"):
        bars[column] = bars[column].dt.tz_convert("UTC")

    with pytest.raises(ValueError, match="Asia/Shanghai"):
        validate_bar_contract(bars)
