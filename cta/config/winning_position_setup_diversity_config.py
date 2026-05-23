"""Config for winning-position setup diversity."""
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
class WinningPositionSetupDiversityConfig:
    """Allow multi-signal candidates for winning trend-following positions."""

    use_winning_position_setup_diversity: bool = False
    activation_pnl_pct: float = 0.05
    max_concurrent_signal_types_per_symbol: int = 3
    require_trend_confirmed: bool = True
    compatible_groups: tuple[tuple[str, ...], ...] = (
        ("atr_breakout", "donchian_breakout", "bull_pullback_continuation"),
        ("breakout_pullback_continuation", "trend_acceleration_breakout"),
    )
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= float(self.activation_pnl_pct) <= 5.0):
            raise ValueError("activation_pnl_pct must be in [0, 5]")
        if int(self.max_concurrent_signal_types_per_symbol) <= 0:
            raise ValueError("max_concurrent_signal_types_per_symbol must be > 0")

        normalized_groups: list[tuple[str, ...]] = []
        for idx, group in enumerate(self.compatible_groups):
            norm = tuple(str(x).strip().lower() for x in group if str(x).strip())
            if not norm:
                raise ValueError(f"compatible_groups[{idx}] must be non-empty")
            normalized_groups.append(norm)
        object.__setattr__(self, "compatible_groups", tuple(normalized_groups))
        object.__setattr__(
            self,
            "enabled_by_cluster_interval",
            _normalize_enabled_map(self.enabled_by_cluster_interval),
        )

    def is_enabled(self, cluster: str | None, interval: str) -> bool:
        if not bool(self.use_winning_position_setup_diversity):
            return False
        key = f"{str(cluster or 'other').strip().lower()}|{normalize_portfolio_interval(interval)}"
        return bool(self.enabled_by_cluster_interval.get(key, False))


__all__ = ["WinningPositionSetupDiversityConfig"]
