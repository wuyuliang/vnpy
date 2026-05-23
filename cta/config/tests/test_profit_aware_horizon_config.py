from __future__ import annotations

import pytest

from cta.config.profit_aware_horizon_config import ProfitAwareHorizonConfig


def test_profit_aware_horizon_defaults_disabled() -> None:
    cfg = ProfitAwareHorizonConfig()
    assert bool(cfg.use_profit_aware_horizon) is False
    assert cfg.is_enabled("index", "day") is False


def test_profit_aware_horizon_normalizes_interval_keys() -> None:
    cfg = ProfitAwareHorizonConfig(
        use_profit_aware_horizon=True,
        enabled_by_cluster_interval={"index|Minute60": True},
    )
    assert cfg.is_enabled("index", "60min") is True
    assert cfg.is_enabled("index", "day") is False


def test_profit_aware_horizon_rejects_cap_below_base() -> None:
    with pytest.raises(ValueError):
        ProfitAwareHorizonConfig(
            cap_total_holding_bars=50,
            base_max_holding_bars_by_interval={"day": 60},
            extended_max_holding_bars_by_interval={"day": 90},
        )

