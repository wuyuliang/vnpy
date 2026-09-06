from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import pandas as pd

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)
from cta.strategy.second_leg_down.backtest import runner
from cta.strategy.second_leg_down.backtest.runner import (
    DEFAULT_OUTPUT_ROOT,
    _build_run_context,
    _capital_basis_summary,
    _prepare_strategy_data,
    build_parser,
    build_reproduction_command,
)
from cta.strategy.second_leg_down.config import SecondLegDownConfig


def test_runner_defaults_are_specific_to_second_leg_down() -> None:
    args = build_parser().parse_args(["--start", "2026-01-01", "--end", "2026-02-01"])

    assert args.risk_per_trade == 0.005
    assert args.output_root == str(DEFAULT_OUTPUT_ROOT)


def test_reproduction_command_uses_the_second_leg_module() -> None:
    args = build_parser().parse_args(
        ["--start", "2026-01-01", "--end", "2026-02-01", "--symbols", "RB"]
    )

    command = build_reproduction_command(args)

    assert command["argv"][:3] == [
        "python3",
        "-m",
        "cta.strategy.second_leg_down.backtest.runner",
    ]


def test_capital_basis_summary_counts_candidates_and_quantity() -> None:
    candidates = pd.DataFrame(
        {
            "capital_basis": ["margin", "notional", "margin"],
            "quantity": [3, 1, 0],
            "filtered_reason": ["", "", "CAPITAL_LIMIT"],
        }
    )

    assert _capital_basis_summary(candidates) == {
        "margin": {"candidate_count": 2, "eligible_count": 1, "quantity": 3},
        "notional": {"candidate_count": 1, "eligible_count": 1, "quantity": 1},
    }


def test_prepare_strategy_data_builds_daily_trend_from_signal_prices(
    monkeypatch,
) -> None:
    stamps = pd.date_range(
        "2026-01-05 09:01",
        periods=4,
        freq="min",
        tz="Asia/Shanghai",
    )
    minute = pd.DataFrame(
        {
            "bar_end": stamps,
            "open": [10.0, 11.0, 12.0, 13.0],
            "high": [11.0, 12.0, 13.0, 14.0],
            "low": [9.0, 10.0, 11.0, 12.0],
            "close": [10.5, 11.5, 12.5, 13.5],
            "signal_open": [100.0, 101.0, 102.0, 103.0],
            "signal_high": [101.0, 102.0, 103.0, 104.0],
            "signal_low": [99.0, 100.0, 101.0, 102.0],
            "signal_close": [100.5, 101.5, 102.5, 103.5],
            "volume": [10.0] * 4,
            "adjustment_scale": [1.0] * 4,
        }
    )
    sessions = (
        SessionSpec(
            "day",
            False,
            (SessionSegment("day", time(9), time(15), time(9)),),
        ),
    )
    loaded = SimpleNamespace(
        minute_bars=minute,
        sessions=sessions,
        root_symbol="RB",
        exchange="SHFE",
    )
    daily_inputs: list[list[float]] = []

    def aggregate_daily(frame, **_kwargs):
        daily_inputs.append(frame["close"].tolist())
        return pd.DataFrame(
            {
                "bar_end": [pd.Timestamp("2026-01-04 15:00", tz="Asia/Shanghai")],
                "open": [frame["open"].iloc[0]],
                "high": [frame["high"].max()],
                "low": [frame["low"].min()],
                "close": [frame["close"].iloc[-1]],
                "volume": [frame["volume"].sum()],
            }
        )

    captured: dict[str, pd.DataFrame] = {}

    def generate(_frame, **kwargs):
        captured["daily_bars"] = kwargs["daily_bars"].copy()
        return pd.DataFrame()

    monkeypatch.setattr(runner, "_attach_execution_instruments", lambda frame, **_: frame)
    monkeypatch.setattr(runner, "_representative_instrument", lambda *_, **__: object())
    monkeypatch.setattr(runner, "generate_second_leg_down_candidates", generate)
    monkeypatch.setattr(runner.shared_runner, "_aggregate_trading_day_daily_bars", aggregate_daily)
    monkeypatch.setattr(
        runner.shared_runner,
        "aggregate_completed_bars",
        lambda frame, **_: frame,
    )

    _prepare_strategy_data(
        loaded,
        metadata_store=object(),
        config=SecondLegDownConfig(signal_timeframe_minutes=1, atr_period=2),
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        initial_equity=1_000_000.0,
    )

    assert daily_inputs[0] == minute["signal_close"].tolist()
    assert daily_inputs[1] == minute["close"].tolist()
    assert captured["daily_bars"]["close"].tolist() == [103.5]


def test_run_context_reports_daily_signal_and_configured_entry_timeframe() -> None:
    config = SecondLegDownConfig(signal_timeframe_minutes=5)
    values = {
        "args": SimpleNamespace(output_root="/tmp/out"),
        "config": config,
        "aggregation_cache": SimpleNamespace(stats={}),
        "gate_diagnostics": SimpleNamespace(
            fail_open_counts=lambda: {},
            evaluation_counts=lambda: {},
        ),
        "start": date(2026, 1, 1),
        "end": date(2026, 2, 1),
        "warmup_start": date(2025, 12, 1),
        "effective_starts": {},
        "selected": [],
        "loaded_items": [],
        "discovered": [],
        "candidates": pd.DataFrame(),
        "minute_update": {},
        "metadata_update": {},
    }

    context = _build_run_context(**values)

    assert context["timeframes"] == {
        "direction": "1d",
        "entry": "5min",
        "execution": "1min",
    }
