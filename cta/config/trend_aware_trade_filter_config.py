"""Config for trend-aware trade-filter threshold relaxation."""
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
class TrendAwareTradeFilterConfig:
    """Trend-aware threshold relaxation config."""

    use_trend_aware_trade_filter: bool = False
    require_ma_alignment_magnitude: int = 2
    require_regime_labels: tuple[str, ...] = ("trend_up", "trend_down", "expansion")
    require_vol_rank_above: float = 0.50
    trend_threshold_delta_pctl: float = -10.0
    trend_threshold_delta_raw: float = -0.05
    max_consecutive_relaxed_bars: int = 60
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if int(self.require_ma_alignment_magnitude) < 0:
            raise ValueError("require_ma_alignment_magnitude must be >= 0")
        if not (0.0 <= float(self.require_vol_rank_above) <= 1.0):
            raise ValueError("require_vol_rank_above must be in [0, 1]")
        if int(self.max_consecutive_relaxed_bars) <= 0:
            raise ValueError("max_consecutive_relaxed_bars must be > 0")
        if not self.require_regime_labels:
            raise ValueError("require_regime_labels must be non-empty")
        labels = tuple(
            str(x).strip().lower() for x in self.require_regime_labels if str(x).strip()
        )
        if not labels:
            raise ValueError("require_regime_labels must contain at least one non-empty label")
        object.__setattr__(self, "require_regime_labels", labels)
        object.__setattr__(
            self,
            "enabled_by_cluster_interval",
            _normalize_enabled_map(self.enabled_by_cluster_interval),
        )

    def is_enabled(self, cluster: str | None, interval: str) -> bool:
        if not bool(self.use_trend_aware_trade_filter):
            return False
        key = f"{str(cluster or 'other').strip().lower()}|{normalize_portfolio_interval(interval)}"
        return bool(self.enabled_by_cluster_interval.get(key, False))


__all__ = ["TrendAwareTradeFilterConfig"]
