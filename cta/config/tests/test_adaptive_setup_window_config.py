from __future__ import annotations

import pytest

from cta.config.adaptive_setup_window_config import AdaptiveSetupWindowConfig


def test_adaptive_setup_window_defaults_disabled() -> None:
    cfg = AdaptiveSetupWindowConfig()
    assert bool(cfg.use_adaptive_setup_window) is False
    assert cfg.is_enabled("precious", "day") is False


def test_adaptive_setup_window_enabled_lookup() -> None:
    cfg = AdaptiveSetupWindowConfig(
        use_adaptive_setup_window=True,
        enabled_by_cluster_interval={"precious|day": True},
    )
    assert cfg.is_enabled("precious", "day") is True
    assert cfg.is_enabled("precious", "60min") is False


def test_adaptive_setup_window_rejects_invalid_window_range() -> None:
    with pytest.raises(ValueError):
        AdaptiveSetupWindowConfig(min_window=21, max_window=20)

