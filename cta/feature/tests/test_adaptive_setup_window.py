from __future__ import annotations

from cta.config.adaptive_setup_window_config import AdaptiveSetupWindowConfig
from cta.feature.adaptive_setup_window import resolve_setup_window


def test_resolve_setup_window_default_base_when_disabled() -> None:
    cfg = AdaptiveSetupWindowConfig()
    got = resolve_setup_window("AU0", "day", "atr_breakout", cfg)
    assert got == 20


def test_resolve_setup_window_precious_day_halved() -> None:
    cfg = AdaptiveSetupWindowConfig(
        use_adaptive_setup_window=True,
        enabled_by_cluster_interval={"precious|day": True},
        window_multiplier_by_cluster_interval={"precious|day": 0.5},
    )
    got = resolve_setup_window("AU0", "day", "donchian_breakout", cfg)
    assert got == 10


def test_resolve_setup_window_honors_floor() -> None:
    cfg = AdaptiveSetupWindowConfig(
        use_adaptive_setup_window=True,
        enabled_by_cluster_interval={"precious|day": True},
        window_multiplier_by_cluster_interval={"precious|day": 0.1},
        min_window=5,
    )
    got = resolve_setup_window("AU0", "day", "atr_breakout", cfg)
    assert got == 5

