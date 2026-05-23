from __future__ import annotations

import pytest

from cta.config.trailing_take_profit_config import TrailingTakeProfitConfig


def test_trailing_take_profit_config_defaults_disabled() -> None:
    cfg = TrailingTakeProfitConfig()
    assert bool(cfg.use_trailing_take_profit) is False
    assert cfg.is_enabled("precious", "day") is False


def test_trailing_take_profit_config_normalizes_enabled_map() -> None:
    cfg = TrailingTakeProfitConfig(
        use_trailing_take_profit=True,
        enabled_by_cluster_interval={"Precious|Minute60": True},
    )
    assert cfg.is_enabled("precious", "60min") is True
    assert cfg.is_enabled("precious", "day") is False


def test_trailing_take_profit_config_rejects_bad_tier() -> None:
    with pytest.raises(ValueError):
        TrailingTakeProfitConfig(tiers=((0.2, 0.05), (0.1, 0.08)))

