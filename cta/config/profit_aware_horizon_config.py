"""Config for profit-aware horizon extension."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from cta.config.interval_utils import normalize_portfolio_interval


def _normalize_interval_int_mapping(values: dict[str, int]) -> MappingProxyType[str, int]:
    out: dict[str, int] = {}
    for key, value in dict(values).items():
        iv = normalize_portfolio_interval(key)
        out[iv] = int(value)
    return MappingProxyType(out)


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
class ProfitAwareHorizonConfig:
    """Profit-aware max holding bars config."""

    use_profit_aware_horizon: bool = False
    activation_pnl_pct: float = 0.05
    base_max_holding_bars_by_interval: dict[str, int] = field(
        default_factory=lambda: {
            "day": 60,
            "60min": 60,
            "30min": 60,
            "15min": 60,
            "5min": 60,
            "min": 60,
        }
    )
    extended_max_holding_bars_by_interval: dict[str, int] = field(
        default_factory=lambda: {
            "day": 90,
            "60min": 90,
            "30min": 80,
            "15min": 80,
            "5min": 70,
            "min": 70,
        }
    )
    require_trend_confirmed: bool = True
    cap_total_holding_bars: int = 120
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= float(self.activation_pnl_pct) <= 5.0):
            raise ValueError("activation_pnl_pct must be in [0, 5]")
        if int(self.cap_total_holding_bars) <= 0:
            raise ValueError("cap_total_holding_bars must be > 0")

        base = {k: int(v) for k, v in self.base_max_holding_bars_by_interval.items()}
        ext = {k: int(v) for k, v in self.extended_max_holding_bars_by_interval.items()}
        for name, mapping in (("base", base), ("extended", ext)):
            for key, value in mapping.items():
                if int(value) <= 0:
                    raise ValueError(f"{name}_max_holding_bars_by_interval[{key}] must be > 0")

        for key, value in base.items():
            iv = normalize_portfolio_interval(key)
            ext_v = int(ext.get(iv, value))
            if ext_v < int(value):
                raise ValueError(
                    f"extended_max_holding_bars_by_interval[{iv}] must be >= base ({value})"
                )
            if int(value) > int(self.cap_total_holding_bars):
                raise ValueError(
                    f"base_max_holding_bars_by_interval[{iv}] must be <= cap_total_holding_bars"
                )
            if ext_v > int(self.cap_total_holding_bars):
                raise ValueError(
                    f"extended_max_holding_bars_by_interval[{iv}] must be <= cap_total_holding_bars"
                )

        object.__setattr__(
            self,
            "base_max_holding_bars_by_interval",
            _normalize_interval_int_mapping(base),
        )
        object.__setattr__(
            self,
            "extended_max_holding_bars_by_interval",
            _normalize_interval_int_mapping(ext),
        )
        object.__setattr__(
            self,
            "enabled_by_cluster_interval",
            _normalize_enabled_map(self.enabled_by_cluster_interval),
        )

    def is_enabled(self, cluster: str | None, interval: str) -> bool:
        if not bool(self.use_profit_aware_horizon):
            return False
        key = f"{str(cluster or 'other').strip().lower()}|{normalize_portfolio_interval(interval)}"
        return bool(self.enabled_by_cluster_interval.get(key, False))


__all__ = ["ProfitAwareHorizonConfig"]
