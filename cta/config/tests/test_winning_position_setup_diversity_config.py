from __future__ import annotations

import pytest

from cta.config.winning_position_setup_diversity_config import (
    WinningPositionSetupDiversityConfig,
)


def test_winning_position_setup_diversity_defaults_disabled() -> None:
    cfg = WinningPositionSetupDiversityConfig()
    assert bool(cfg.use_winning_position_setup_diversity) is False
    assert cfg.is_enabled("precious", "day") is False


def test_winning_position_setup_diversity_enabled_key_normalized() -> None:
    cfg = WinningPositionSetupDiversityConfig(
        use_winning_position_setup_diversity=True,
        enabled_by_cluster_interval={"precious|minute60": True},
    )
    assert cfg.is_enabled("precious", "60min") is True


def test_winning_position_setup_diversity_rejects_empty_group() -> None:
    with pytest.raises(ValueError):
        WinningPositionSetupDiversityConfig(
            compatible_groups=(("atr_breakout", "donchian_breakout"), ()),
        )

