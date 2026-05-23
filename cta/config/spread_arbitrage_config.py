"""Configuration for cross-instrument/calendar spread arbitrage."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from cta.portfolio_logic.config import normalize_portfolio_interval

WILDCARD_PAIR = "*"


def _normalize_enabled_map(values: dict[str, bool]) -> MappingProxyType[str, bool]:
    normalized: dict[str, bool] = {}
    for key, value in dict(values).items():
        parts = str(key).strip().lower().split("|")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"enabled_by_pair_interval key must be pair_key|interval (or *|interval): {key!r}"
            )
        if not isinstance(value, bool):
            raise ValueError(f"enabled_by_pair_interval[{key!r}] must be bool")
        pair_key = str(parts[0]).strip().lower()
        interval = normalize_portfolio_interval(parts[1])
        normalized[f"{pair_key}|{interval}"] = bool(value)
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class SpreadArbitrageConfig:
    """Opt-in configuration for spread mean-reversion strategy."""

    use_spread_arbitrage: bool = False
    rolling_window_days: int = 60
    z_entry: float = 2.0
    z_exit: float = 0.5
    z_stop: float = 3.5
    max_holding_days: int = 30
    use_log_spread: bool = True
    use_dynamic_hedge_ratio: bool = False
    hedge_ratio_window_days: int = 120

    notional_pct_per_spread: float = 0.05
    use_vol_target_weighting: bool = False
    target_spread_vol: float = 0.01
    max_concurrent_spreads: int = 5
    max_concurrent_per_cluster: int = 2

    leg_hard_stop_pct: float = 0.10
    kill_switch_dd_pct: float = 0.10

    rollover_days_before_expiry: int = 10
    exclude_within_rollover_window_days: int = 5

    min_avg_daily_volume_per_leg: int = 30_000
    min_avg_daily_open_interest_per_leg: int = 50_000
    min_history_days: int = 252

    enabled_pairs: tuple[str, ...] = ()
    enabled_by_pair_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 < float(self.z_exit) < float(self.z_entry) < float(self.z_stop)):
            raise ValueError("z params must satisfy 0 < z_exit < z_entry < z_stop")
        if not (0.0 < float(self.notional_pct_per_spread) <= 0.20):
            raise ValueError("notional_pct_per_spread must be in (0, 0.20]")
        for name in (
            "rolling_window_days",
            "max_holding_days",
            "hedge_ratio_window_days",
            "max_concurrent_spreads",
            "max_concurrent_per_cluster",
            "rollover_days_before_expiry",
            "exclude_within_rollover_window_days",
            "min_avg_daily_volume_per_leg",
            "min_avg_daily_open_interest_per_leg",
            "min_history_days",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be > 0")
        if not (0.0 < float(self.leg_hard_stop_pct) <= 1.0):
            raise ValueError("leg_hard_stop_pct must be in (0, 1]")
        if not (0.0 < float(self.kill_switch_dd_pct) <= 1.0):
            raise ValueError("kill_switch_dd_pct must be in (0, 1]")
        if float(self.target_spread_vol) <= 0.0:
            raise ValueError("target_spread_vol must be > 0")

        normalized_enabled_pairs = tuple(
            str(pair_key).strip().lower()
            for pair_key in self.enabled_pairs
            if str(pair_key).strip()
        )
        object.__setattr__(self, "enabled_pairs", normalized_enabled_pairs)
        object.__setattr__(
            self,
            "enabled_by_pair_interval",
            _normalize_enabled_map(self.enabled_by_pair_interval),
        )

    def is_enabled(self, pair_key: str, interval: str) -> bool:
        """Whether spread strategy is enabled for a given ``(pair_key, interval)``."""
        if not bool(self.use_spread_arbitrage):
            return False
        pair = str(pair_key).strip().lower()
        norm_interval = normalize_portfolio_interval(interval)
        explicit_key = f"{pair}|{norm_interval}"
        wildcard_key = f"{WILDCARD_PAIR}|{norm_interval}"
        if explicit_key in self.enabled_by_pair_interval:
            return bool(self.enabled_by_pair_interval[explicit_key])
        if wildcard_key in self.enabled_by_pair_interval:
            return bool(self.enabled_by_pair_interval[wildcard_key])
        if pair in set(self.enabled_pairs):
            return True
        return False


__all__ = ["SpreadArbitrageConfig", "WILDCARD_PAIR"]

