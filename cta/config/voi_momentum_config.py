"""Configuration for VOI regime-adaptive momentum features."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from cta.portfolio_logic.config import normalize_portfolio_interval


def _normalize_enabled_map(values: dict[str, bool]) -> MappingProxyType[str, bool]:
    normalized: dict[str, bool] = {}
    for key, value in dict(values).items():
        parts = str(key).strip().lower().split("|")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"enabled_by_cluster_interval key must be cluster|interval: {key!r}")
        if not isinstance(value, bool):
            raise ValueError(f"enabled_by_cluster_interval[{key!r}] must be bool")
        normalized[f"{parts[0]}|{normalize_portfolio_interval(parts[1])}"] = bool(value)
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class VoiMomentumConfig:
    """Opt-in parameters for volatility-aware momentum features."""

    use_voi_regime_adaptive_momentum: bool = False
    vol_window: int = 20
    rank_window: int = 252
    high_vol_threshold: float = 0.70
    low_vol_threshold: float = 0.30
    fast_momentum_window: int = 5
    slow_momentum_window: int = 20
    volume_rank_window: int = 60
    min_volume_rank: float = 0.0
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "vol_window",
            "rank_window",
            "fast_momentum_window",
            "slow_momentum_window",
            "volume_rank_window",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be > 0")
        if not (0.0 < float(self.low_vol_threshold) < float(self.high_vol_threshold) < 1.0):
            raise ValueError("vol thresholds must satisfy 0 < low < high < 1")
        if not (0.0 <= float(self.min_volume_rank) <= 1.0):
            raise ValueError("min_volume_rank must be in [0, 1]")
        object.__setattr__(
            self,
            "enabled_by_cluster_interval",
            _normalize_enabled_map(self.enabled_by_cluster_interval),
        )

    def is_enabled(self, cluster: str | None, interval: str) -> bool:
        """Return whether VOI should be computed for one cluster and interval."""
        if not bool(self.use_voi_regime_adaptive_momentum):
            return False
        key = f"{str(cluster or 'other').strip().lower()}|{normalize_portfolio_interval(interval)}"
        return bool(self.enabled_by_cluster_interval.get(key, False))


__all__ = ["VoiMomentumConfig"]
