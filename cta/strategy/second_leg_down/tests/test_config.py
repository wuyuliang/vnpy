from __future__ import annotations

from dataclasses import fields

import pytest

from cta.config.replay_common import BaseReplayConfig
from cta.strategy.second_leg_down.config import SecondLegDownConfig


def test_second_leg_config_inherits_replay_defaults_and_overrides_exits() -> None:
    config = SecondLegDownConfig()

    assert isinstance(config, BaseReplayConfig)
    assert config.entry_blocked_session_windows == ()
    assert config.profit_floor_arm_r == 0.5
    assert config.profit_floor_giveback_r == 0.5
    assert config.profit_floor_giveback_pct == 0.0
    assert config.no_progress_bars == 5
    assert {field.name for field in fields(config)} >= {
        "ema_fast",
        "min_risk_pct",
        "max_capital_share",
        "follow_through_body_atr_mult",
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ema_fast": 10, "ema_mid": 5}, "EMA periods"),
        ({"min_risk_pct": 0.006}, "risk percentages"),
        ({"stop_mode": "close"}, "stop_mode"),
        ({"volume_baseline_min_samples": 61}, "minimum samples"),
        ({"profit_floor_giveback_pct": 1.0}, "profit_floor_giveback_pct"),
    ],
)
def test_second_leg_config_rejects_invalid_values(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        SecondLegDownConfig(**overrides)
