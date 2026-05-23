"""Config for adaptive ATR/Donchian setup windows."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from cta.config.interval_utils import normalize_portfolio_interval


def _normalize_enabled_map(values: dict[str, bool]) -> MappingProxyType[str, bool]:
    out: dict[str, bool] = {}
    for key, value in dict(values).items():
        parts = str(key).strip().lower().split("|")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"enabled_by_cluster_interval key must be cluster|interval, got {key!r}"
            )
        if not isinstance(value, bool):
            raise ValueError(f"enabled_by_cluster_interval[{key!r}] must be bool")
        out[f"{parts[0]}|{normalize_portfolio_interval(parts[1])}"] = bool(value)
    return MappingProxyType(out)


def _normalize_multiplier_map(values: dict[str, float]) -> MappingProxyType[str, float]:
    out: dict[str, float] = {}
    for key, value in dict(values).items():
        parts = str(key).strip().lower().split("|")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"window_multiplier_by_cluster_interval key must be cluster|interval, got {key!r}"
            )
        mul = float(value)
        if mul <= 0.0:
            raise ValueError(
                f"window_multiplier_by_cluster_interval[{key!r}] must be > 0"
            )
        out[f"{parts[0]}|{normalize_portfolio_interval(parts[1])}"] = mul
    return MappingProxyType(out)


@dataclass(frozen=True)
class AdaptiveSetupWindowConfig:
    """Adaptive setup window config.

    ``window_multiplier_by_cluster_interval`` values <1 shrink windows.
    """

    use_adaptive_setup_window: bool = False
    base_atr_window: int = 20
    base_donchian_window: int = 20
    window_multiplier_by_cluster_interval: dict[str, float] = field(default_factory=dict)
    min_window: int = 5
    max_window: int = 60
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("base_atr_window", "base_donchian_window", "min_window", "max_window"):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be > 0")
        if int(self.min_window) > int(self.max_window):
            raise ValueError("min_window must be <= max_window")
        object.__setattr__(
            self,
            "window_multiplier_by_cluster_interval",
            _normalize_multiplier_map(self.window_multiplier_by_cluster_interval),
        )
        object.__setattr__(
            self,
            "enabled_by_cluster_interval",
            _normalize_enabled_map(self.enabled_by_cluster_interval),
        )

    def is_enabled(self, cluster: str | None, interval: str) -> bool:
        if not bool(self.use_adaptive_setup_window):
            return False
        key = f"{str(cluster or 'other').strip().lower()}|{normalize_portfolio_interval(interval)}"
        return bool(self.enabled_by_cluster_interval.get(key, False))


__all__ = ["AdaptiveSetupWindowConfig"]
