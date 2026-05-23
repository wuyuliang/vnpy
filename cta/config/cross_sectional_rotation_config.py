"""Configuration for cross-sectional momentum rotation strategy.

Strategy 假设：对全 universe 按过去 N 日累计收益排名，long 头部 + short 尾部，
按周度调仓。默认 off，按 (cluster, interval) 灰度启用；支持 `*|day` 通配。
设计文档：cta/docs/cross_sectional_momentum_rotation_design.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from cta.portfolio_logic.config import normalize_portfolio_interval


WILDCARD_CLUSTER = "*"


def _normalize_enabled_map(values: dict[str, bool]) -> MappingProxyType[str, bool]:
    """规范化 ``{cluster|interval: bool}`` 字典；支持 `*` 作为 cluster 通配符。"""
    normalized: dict[str, bool] = {}
    for key, value in dict(values).items():
        parts = str(key).strip().lower().split("|")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"enabled_by_cluster_interval key must be cluster|interval "
                f"(or *|interval): {key!r}"
            )
        if not isinstance(value, bool):
            raise ValueError(f"enabled_by_cluster_interval[{key!r}] must be bool")
        normalized[f"{parts[0]}|{normalize_portfolio_interval(parts[1])}"] = bool(value)
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class CrossSectionalRotationConfig:
    """Opt-in parameters for cross-sectional momentum rotation.

    见 [design §5.5](../docs/cross_sectional_momentum_rotation_design.md)。
    """

    use_cross_sectional_momentum_rotation: bool = False

    # signal
    lookback_days: int = 60
    skip_recent_days: int = 5

    # rebalance
    rebalance_weekday: int = 0
    max_holding_days: int = 5

    # selection quantiles
    top_quantile: float = 0.80
    bottom_quantile: float = 0.20
    top_quantile_in_cluster: float = 0.40
    bottom_quantile_in_cluster: float = 0.40

    # neutralization
    cluster_neutral: bool = True
    min_cluster_size: int = 3
    long_only_mode: bool = False

    # exposure
    gross_exposure_target: float = 0.50
    max_gross_exposure: float = 1.00
    net_exposure_target: float = 0.0
    max_net_exposure_abs: float = 0.20

    # weighting
    use_vol_target_weighting: bool = True
    target_vol_pct_per_symbol: float = 0.02
    realized_vol_window_days: int = 60
    max_symbol_notional_pct: float = 0.10

    # risk
    stop_loss_pct: float = 0.05
    kill_switch_dd_pct: float = 0.15

    # universe filters
    min_avg_daily_volume: int = 50_000
    min_avg_daily_open_interest: int = 100_000
    min_history_days: int = 252
    exclude_within_rollover_window_days: int = 3
    universe_clusters: tuple[str, ...] = (
        "black", "metal", "chemical", "agri", "precious", "index", "bond",
    )

    # gating
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "lookback_days", "skip_recent_days", "max_holding_days",
            "min_cluster_size", "realized_vol_window_days",
            "min_avg_daily_volume", "min_avg_daily_open_interest",
            "min_history_days", "exclude_within_rollover_window_days",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be >= 0")

        if int(self.lookback_days) <= int(self.skip_recent_days):
            raise ValueError("lookback_days must be > skip_recent_days")

        if not (0 <= int(self.rebalance_weekday) <= 6):
            raise ValueError("rebalance_weekday must be in [0, 6]")

        if not (0.0 < float(self.bottom_quantile) < float(self.top_quantile) < 1.0):
            raise ValueError("quantiles must satisfy 0 < bottom_quantile < top_quantile < 1")

        for name in ("top_quantile_in_cluster", "bottom_quantile_in_cluster"):
            if not (0.0 < float(getattr(self, name)) <= 1.0):
                raise ValueError(f"{name} must be in (0, 1]")

        if float(self.gross_exposure_target) < 0.0:
            raise ValueError("gross_exposure_target must be >= 0")
        if float(self.max_gross_exposure) < float(self.gross_exposure_target):
            raise ValueError("max_gross_exposure must be >= gross_exposure_target")
        if float(self.max_net_exposure_abs) < 0.0:
            raise ValueError("max_net_exposure_abs must be >= 0")

        if float(self.target_vol_pct_per_symbol) <= 0.0:
            raise ValueError("target_vol_pct_per_symbol must be > 0")
        if not (0.0 < float(self.max_symbol_notional_pct) <= 1.0):
            raise ValueError("max_symbol_notional_pct must be in (0, 1]")

        if float(self.stop_loss_pct) <= 0.0:
            raise ValueError("stop_loss_pct must be > 0")
        if not (0.0 < float(self.kill_switch_dd_pct) <= 1.0):
            raise ValueError("kill_switch_dd_pct must be in (0, 1]")

        normalized_clusters = tuple(
            str(c).strip().lower() for c in self.universe_clusters if str(c).strip()
        )
        object.__setattr__(self, "universe_clusters", normalized_clusters)

        object.__setattr__(
            self,
            "enabled_by_cluster_interval",
            _normalize_enabled_map(self.enabled_by_cluster_interval),
        )

    def is_enabled(self, cluster: str | None, interval: str) -> bool:
        """是否在某个 (cluster, interval) 启用 rotation；支持 `*` 通配 cluster。"""
        if not bool(self.use_cross_sectional_momentum_rotation):
            return False
        norm_interval = normalize_portfolio_interval(interval)
        cluster_key = f"{str(cluster or 'other').strip().lower()}|{norm_interval}"
        if bool(self.enabled_by_cluster_interval.get(cluster_key, False)):
            return True
        wildcard_key = f"{WILDCARD_CLUSTER}|{norm_interval}"
        return bool(self.enabled_by_cluster_interval.get(wildcard_key, False))


__all__ = ["CrossSectionalRotationConfig", "WILDCARD_CLUSTER"]
