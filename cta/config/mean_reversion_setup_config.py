"""Configuration for range mean-reversion setup generation."""
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
class MeanReversionSetupConfig:
    """Opt-in parameters for range-bound mean-reversion setups."""

    use_mean_reversion_setup: bool = False
    bb_window: int = 20
    bb_std_mult: float = 2.0
    rsi_window: int = 14
    rsi_upper: float = 70.0
    rsi_lower: float = 30.0
    adx_window: int = 14
    adx_max: float = 20.0
    require_range_regime: bool = True
    target_atr_mult_stop: float = 0.5
    max_holding_bars: int = 5
    early_exit_on_regime_flip: bool = True
    early_exit_on_adx_breakout: float = 25.0
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("bb_window", "rsi_window", "adx_window", "max_holding_bars"):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be > 0")
        if float(self.bb_std_mult) <= 0.0:
            raise ValueError("bb_std_mult must be > 0")
        if not (0.0 < float(self.rsi_lower) < float(self.rsi_upper) < 100.0):
            raise ValueError("rsi thresholds must satisfy 0 < lower < upper < 100")
        if float(self.adx_max) < 0.0 or float(self.early_exit_on_adx_breakout) < 0.0:
            raise ValueError("adx thresholds must be >= 0")
        if float(self.target_atr_mult_stop) <= 0.0:
            raise ValueError("target_atr_mult_stop must be > 0")
        object.__setattr__(
            self,
            "enabled_by_cluster_interval",
            _normalize_enabled_map(self.enabled_by_cluster_interval),
        )

    def is_enabled(self, cluster: str | None, interval: str) -> bool:
        """Return whether setup generation is enabled for one rollout cell."""
        if not bool(self.use_mean_reversion_setup):
            return False
        key = f"{str(cluster or 'other').strip().lower()}|{normalize_portfolio_interval(interval)}"
        return bool(self.enabled_by_cluster_interval.get(key, False))


__all__ = ["MeanReversionSetupConfig"]
