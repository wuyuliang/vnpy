from __future__ import annotations

from datetime import time
from pathlib import Path

import pandas as pd
from PIL import Image

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)
from cta.strategy.multi_timeframe_trend_backtest import charts


TZ = "Asia/Shanghai"


def _candidates() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "candidate_id": "BR-1",
                "signal_time": pd.Timestamp("2026-03-02 09:05", tz=TZ),
                "setup_type": "BREAKOUT",
                "direction": 1,
                "symbol": "BR",
                "entry": 101.0,
                "stop": 99.0,
            },
            {
                "candidate_id": "EB-1",
                "signal_time": pd.Timestamp("2026-03-02 09:05", tz=TZ),
                "setup_type": "BREAKOUT",
                "direction": 1,
                "symbol": "EB",
                "entry": 101.0,
                "stop": 99.0,
            },
        ]
    )


def _bars(symbol: str) -> pd.DataFrame:
    ends = pd.date_range("2026-03-02 09:01", periods=5, freq="1min", tz=TZ)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "bar_end": ends,
            "open": range(100, 105),
            "high": range(101, 106),
            "low": range(99, 104),
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
            "volume": 10.0,
            "open_interest": 1_000.0,
            "contract_code": f"{symbol}2604.TEST",
            "exchange_trade_date": ends.date,
        }
    )


def _sessions() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            "day",
            False,
            (SessionSegment("day", time(9), time(15), time(9)),),
        ),
    )


def _chart_bars() -> pd.DataFrame:
    return pd.concat([_bars("BR"), _bars("EB")], ignore_index=True)


def test_chart_outcomes_none_never_aggregates_hourly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = 0

    def aggregate(*args, **kwargs):
        nonlocal calls
        calls += 1
        return args[0]

    monkeypatch.setattr(charts, "aggregate_completed_bars", aggregate, raising=False)
    bars = _chart_bars()

    charts.render_opportunity_charts(
        tmp_path,
        candidates=_candidates(),
        daily_bars=bars,
        five_minute_bars=bars,
        trades=pd.DataFrame(),
        orders=pd.DataFrame(),
        rejections=pd.DataFrame(),
        render_outcomes="none",
        minute_bars_by_symbol={"BR": _bars("BR"), "EB": _bars("EB")},
        sessions_by_symbol={"BR": _sessions(), "EB": _sessions()},
    )

    assert calls == 0


def test_chart_outcomes_traded_aggregates_only_traded_symbols(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    def aggregate(frame, **kwargs):
        calls.append(str(frame["symbol"].iat[0]))
        return frame

    monkeypatch.setattr(charts, "aggregate_completed_bars", aggregate, raising=False)
    monkeypatch.setattr(
        charts,
        "_render_card",
        lambda *args, **kwargs: Image.new("RGB", (1, 1)),
    )
    bars = _chart_bars()

    charts.render_opportunity_charts(
        tmp_path,
        candidates=_candidates(),
        daily_bars=bars,
        five_minute_bars=bars,
        trades=pd.DataFrame([{"candidate_id": "BR-1", "exit_price": 105.0}]),
        orders=pd.DataFrame(),
        rejections=pd.DataFrame(),
        render_outcomes="traded",
        minute_bars_by_symbol={"BR": _bars("BR"), "EB": _bars("EB")},
        sessions_by_symbol={"BR": _sessions(), "EB": _sessions()},
    )

    assert calls == ["BR"]
