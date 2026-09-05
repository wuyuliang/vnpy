from __future__ import annotations

from dataclasses import fields
from datetime import date

import pandas as pd

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.multi_timeframe_trend_backtest import engine
from cta.strategy.tests.test_multi_timeframe_trend_backtest import (
    _MetadataStore,
    _day_sessions,
    _minutes,
)


def test_trend_config_inherits_the_shared_replay_config() -> None:
    from cta.config.replay_common import BaseReplayConfig

    config = MultiTimeframeTrendConfig()

    assert isinstance(config, BaseReplayConfig)
    shared = {field.name for field in fields(BaseReplayConfig)}
    assert {
        "risk_per_trade",
        "max_risk_per_trade",
        "entry_blocked_session_windows",
        "daily_circuit_breaker_enabled",
        "max_positions_per_sector",
        "order_max_recess_minutes",
        "profit_floor_enabled",
        "drawdown_scale_threshold",
        "turnover_share_threshold",
    }.issubset(shared)
    assert config.risk_per_trade == 0.01
    assert config.max_risk_per_trade == 0.02
    assert config.profit_floor_giveback_r == 1.0


def test_portfolio_context_without_daily_direction_is_unconstrained() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 99.9, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 100.0, 100.2, 99.8, 100.0, "AG2602.SHF"),
        ]
    )
    context = pd.DataFrame({"bar_end": bars["bar_end"]})
    candidates = pd.DataFrame(
        columns=[
            "candidate_id",
            "contract_code",
            "setup_type",
            "direction",
            "signal_time",
            "active_time",
            "expires_at",
            "trigger",
            "stop_price",
            "filtered_reason",
        ]
    )
    for column in ("signal_time", "active_time", "expires_at"):
        candidates[column] = pd.Series(
            [], dtype="datetime64[ns, Asia/Shanghai]"
        )

    artifacts = engine.replay_trend_portfolio(
        inputs=(
            engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=bars,
                five_minute_context=context,
                candidates=candidates,
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=_MetadataStore(),
        config=MultiTimeframeTrendConfig(),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )

    assert artifacts.trades.empty
    assert "DAILY_DIRECTION_INVALID" not in set(artifacts.rejections["reason_code"])
