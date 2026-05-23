"""Config for profit-aware trailing take-profit exits."""
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


@dataclass(frozen=True)
class TrailingTakeProfitConfig:
    """Tiered trailing take-profit config.

    ``tiers`` is a tuple of ``(pnl_threshold, trail_distance_pct)`` pairs and
    must be monotonic increasing in threshold.
    """

    use_trailing_take_profit: bool = False
    activation_pnl_pct: float = 0.10
    trail_distance_pct_initial: float = 0.08
    trail_distance_pct_after_huge_gain: float = 0.04
    huge_gain_threshold: float = 0.50
    tiers: tuple[tuple[float, float], ...] = (
        (0.10, 0.08),
        (0.20, 0.06),
        (0.50, 0.04),
    )
    require_trend_confirmed: bool = True
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= float(self.activation_pnl_pct) <= 5.0):
            raise ValueError("activation_pnl_pct must be in [0, 5]")
        if not (0.0 < float(self.trail_distance_pct_initial) <= 1.0):
            raise ValueError("trail_distance_pct_initial must be in (0, 1]")
        if not (0.0 < float(self.trail_distance_pct_after_huge_gain) <= 1.0):
            raise ValueError("trail_distance_pct_after_huge_gain must be in (0, 1]")
        if float(self.huge_gain_threshold) < float(self.activation_pnl_pct):
            raise ValueError("huge_gain_threshold must be >= activation_pnl_pct")

        last_threshold = -1.0
        for idx, item in enumerate(self.tiers):
            if len(item) != 2:
                raise ValueError(f"tiers[{idx}] must be (threshold, trail_pct)")
            th, trail = float(item[0]), float(item[1])
            if th < 0.0:
                raise ValueError(f"tiers[{idx}].threshold must be >= 0")
            if not (0.0 < trail <= 1.0):
                raise ValueError(f"tiers[{idx}].trail_pct must be in (0, 1]")
            if th < last_threshold:
                raise ValueError("tiers thresholds must be monotonic non-decreasing")
            last_threshold = th

        object.__setattr__(
            self,
            "enabled_by_cluster_interval",
            _normalize_enabled_map(self.enabled_by_cluster_interval),
        )

    def is_enabled(self, cluster: str | None, interval: str) -> bool:
        if not bool(self.use_trailing_take_profit):
            return False
        key = f"{str(cluster or 'other').strip().lower()}|{normalize_portfolio_interval(interval)}"
        return bool(self.enabled_by_cluster_interval.get(key, False))


__all__ = ["TrailingTakeProfitConfig"]
