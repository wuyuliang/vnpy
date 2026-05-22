"""Configuration dataclasses for portfolio-level execution logic."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType


def _default_interval_rank() -> dict[str, float]:
    return {
        "day": 1.0,
        "60min": 0.85,
        "30min": 0.70,
        "15min": 0.55,
        "5min": 0.40,
        "min": 0.25,
    }


def _default_cooldown_bars_per_interval() -> dict[str, int]:
    """Recommended cooldown in bar units by base interval."""
    return {
        "day": 3,
        "60min": 2,
        "30min": 2,
        "15min": 4,
        "5min": 6,
        "min": 12,
    }


def _default_interval_to_minutes() -> dict[str, int]:
    return {
        "day": 1440,
        "60min": 60,
        "30min": 30,
        "15min": 15,
        "5min": 5,
        "min": 1,
    }


_PORTFOLIO_INTERVAL_ALIASES: dict[str, str] = {
    "daily": "day",
    "d": "day",
    "1d": "day",
    "minute60": "60min",
    "60m": "60min",
    "1h": "60min",
    "hour": "60min",
    "minute30": "30min",
    "30m": "30min",
    "minute15": "15min",
    "15m": "15min",
    "minute5": "5min",
    "5m": "5min",
    "minute": "min",
    "1m": "min",
    "1min": "min",
}


def normalize_portfolio_interval(interval: object) -> str:
    """Normalize data/storage interval names to portfolio-logic interval labels."""
    if interval is None:
        return ""
    raw = str(interval).strip().lower()
    return _PORTFOLIO_INTERVAL_ALIASES.get(raw, raw)


@dataclass(frozen=True)
class IntervalGateConfig:
    """HTF gate configuration."""

    htf_intervals: tuple[str, ...] = ("day", "60min")
    require_consensus: bool = True
    fallback_when_htf_missing: str = "skip"  # 'skip' | 'both'
    state_ttl_seconds: dict[str, int] = field(default_factory=lambda: {"day": 86400, "60min": 3600})
    interval_rank: dict[str, float] = field(default_factory=_default_interval_rank)

    def __post_init__(self) -> None:
        if self.fallback_when_htf_missing not in {"skip", "both"}:
            raise ValueError(f"unsupported fallback_when_htf_missing={self.fallback_when_htf_missing}")
        norm_rank: dict[str, float] = {}
        for k, v in self.interval_rank.items():
            fv = float(v)
            if not (0.0 < fv <= 1.0):
                raise ValueError(f"invalid interval_rank[{k}]={v}, should be in (0, 1]")
            norm_rank[normalize_portfolio_interval(k)] = fv
        norm_ttl = {normalize_portfolio_interval(k): int(v) for k, v in self.state_ttl_seconds.items()}
        object.__setattr__(self, "interval_rank", MappingProxyType(norm_rank))
        object.__setattr__(self, "state_ttl_seconds", MappingProxyType(norm_ttl))


@dataclass(frozen=True)
class CapsConfig:
    """Portfolio capacity limits."""

    max_total_positions: int = 10
    max_per_symbol: int = 1
    max_total_per_cluster: int = 4
    max_symbol_notional_pct: float = 0.30
    max_cluster_notional_pct: float = 0.50
    max_total_notional_pct: float = 1.5
    dedup_same_symbol_same_direction: bool = True

    def __post_init__(self) -> None:
        if not (0.0 < self.max_symbol_notional_pct <= self.max_cluster_notional_pct):
            raise ValueError("max_symbol_notional_pct must be <= max_cluster_notional_pct")
        if not (self.max_cluster_notional_pct <= self.max_total_notional_pct):
            raise ValueError("max_cluster_notional_pct must be <= max_total_notional_pct")


@dataclass(frozen=True)
class OpportunityRankerConfig:
    """Opportunity scoring weights."""

    w_prob: float = 0.40
    w_edge: float = 0.30
    w_rank: float = 0.20
    w_align: float = 0.10
    score_threshold_baseline: float = 0.45
    dedup_same_symbol: bool = True
    edge_clip: tuple[float, float] = (-2.0, 4.0)
    base_notional_pct: float = 0.10

    def __post_init__(self) -> None:
        total = float(self.w_prob + self.w_edge + self.w_rank + self.w_align)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {total}")
        if not (0.0 < float(self.base_notional_pct) <= 1.0):
            raise ValueError(f"base_notional_pct must be in (0,1], got {self.base_notional_pct}")


@dataclass(frozen=True)
class IntervalTrailingParams:
    """Per-interval trailing stop parameters."""

    atr_multiplier: float
    activation_profit_atr: float
    fallback_hard_stop_pct: float


def _default_interval_trailing() -> dict[str, IntervalTrailingParams]:
    return {
        "day": IntervalTrailingParams(atr_multiplier=4.0, activation_profit_atr=1.5, fallback_hard_stop_pct=0.04),
        "60min": IntervalTrailingParams(atr_multiplier=3.5, activation_profit_atr=1.2, fallback_hard_stop_pct=0.03),
        "30min": IntervalTrailingParams(atr_multiplier=3.0, activation_profit_atr=1.0, fallback_hard_stop_pct=0.025),
        "15min": IntervalTrailingParams(atr_multiplier=2.5, activation_profit_atr=0.8, fallback_hard_stop_pct=0.020),
        "5min": IntervalTrailingParams(atr_multiplier=2.0, activation_profit_atr=0.6, fallback_hard_stop_pct=0.015),
        "min": IntervalTrailingParams(atr_multiplier=1.5, activation_profit_atr=0.5, fallback_hard_stop_pct=0.010),
    }


@dataclass(frozen=True)
class TrailingExitConfig:
    """Trailing exit behavior."""

    enabled: bool = True
    activate_only_when_regime: tuple[str, ...] = ("trend_up", "trend_down")
    interval_params: dict[str, IntervalTrailingParams] = field(default_factory=_default_interval_trailing)
    update_only_in_favor: bool = True
    default_interval_params_key: str = "30min"


@dataclass(frozen=True)
class HorizonExtendConfig:
    """Horizon extension behavior in trending regime."""

    enabled: bool = True
    extend_when_regime: tuple[str, ...] = ("trend_up", "trend_down")
    max_extensions: int = 3
    extension_bars: int = 20
    use_model_recommendation: bool = False
    min_hold_extend_score: float = 0.60
    max_model_extension_bars: int = 60


@dataclass(frozen=True)
class PyramidConfig:
    """Pyramid (add-on) entry behavior."""

    enabled: bool = True
    max_active_layers: int = 4
    max_lifetime_layers: int = 6
    size_decay: tuple[float, ...] = (1.0, 0.5, 0.25, 0.15, 0.10, 0.10)
    min_profit_atr_to_add: float = 1.0
    cooldown_minutes: int = 30
    cooldown_bars_per_interval: dict[str, int] = field(default_factory=_default_cooldown_bars_per_interval)
    interval_to_minutes: dict[str, int] = field(default_factory=_default_interval_to_minutes)
    require_new_signal_same_direction: bool = True
    require_htf_still_aligned: bool = True
    one_layer_per_interval: bool = True
    apply_model_add_score_gate: bool = True
    min_model_add_score: float = 0.60
    apply_model_size_multiplier: bool = False
    min_model_size_multiplier: float = 0.0
    max_model_size_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.max_active_layers > self.max_lifetime_layers:
            raise ValueError("max_active_layers cannot be greater than max_lifetime_layers")
        if len(self.size_decay) < self.max_lifetime_layers:
            raise ValueError("size_decay length must be >= max_lifetime_layers")
        if not (0.0 <= float(self.min_model_add_score) <= 1.0):
            raise ValueError("min_model_add_score must be in [0, 1]")
        if not (0.0 <= float(self.min_model_size_multiplier) <= float(self.max_model_size_multiplier)):
            raise ValueError("min_model_size_multiplier must be <= max_model_size_multiplier and >= 0")
        norm_cooldown: dict[str, int] = {}
        for k, v in self.cooldown_bars_per_interval.items():
            norm = normalize_portfolio_interval(k)
            if int(v) < 0:
                raise ValueError(f"cooldown_bars_per_interval[{k}] must be >= 0")
            if norm not in self.interval_to_minutes:
                raise ValueError(
                    f"cooldown_bars_per_interval has unknown interval={k}; "
                    f"missing interval_to_minutes mapping"
                )
            norm_cooldown[norm] = int(v)
        norm_minutes: dict[str, int] = {}
        for k, v in self.interval_to_minutes.items():
            if int(v) <= 0:
                raise ValueError(f"interval_to_minutes[{k}] must be > 0")
            norm_minutes[normalize_portfolio_interval(k)] = int(v)
        object.__setattr__(self, "cooldown_bars_per_interval", MappingProxyType(norm_cooldown))
        object.__setattr__(self, "interval_to_minutes", MappingProxyType(norm_minutes))


@dataclass(frozen=True)
class ThrottleLevel:
    """A drawdown-dependent risk regime."""

    name: str
    drawdown_lo: float
    drawdown_hi: float
    max_total_positions_mult: float
    max_per_cluster_mult: float
    score_pctl_threshold: float
    allow_pyramid: bool
    max_pyramid_layers: int
    min_prob_pctl: float = 0.0

    @property
    def effective_min_prob_pctl(self) -> float:
        return float(self.min_prob_pctl)

    def __post_init__(self) -> None:
        if self.min_prob_pctl is None:
            raise ValueError("min_prob_pctl is required")
        v = float(self.min_prob_pctl)
        if not (0.0 <= v <= 100.0):
            raise ValueError(f"min_prob_pctl must be in [0,100], got {self.min_prob_pctl}")


@dataclass(frozen=True)
class RiskThrottleConfig:
    """Drawdown-adaptive risk throttling."""

    levels: tuple[ThrottleLevel, ...] = (
        ThrottleLevel(
            name="normal",
            drawdown_lo=0.00,
            drawdown_hi=0.05,
            max_total_positions_mult=1.0,
            max_per_cluster_mult=1.0,
            score_pctl_threshold=60.0,
            allow_pyramid=True,
            max_pyramid_layers=4,
            min_prob_pctl=60.0,
        ),
        ThrottleLevel(
            name="reduced",
            drawdown_lo=0.05,
            drawdown_hi=0.10,
            max_total_positions_mult=0.7,
            max_per_cluster_mult=0.7,
            score_pctl_threshold=80.0,
            allow_pyramid=True,
            max_pyramid_layers=2,
            min_prob_pctl=80.0,
        ),
        ThrottleLevel(
            name="conservative",
            drawdown_lo=0.10,
            drawdown_hi=0.15,
            max_total_positions_mult=0.4,
            max_per_cluster_mult=0.5,
            score_pctl_threshold=90.0,
            allow_pyramid=False,
            max_pyramid_layers=0,
            min_prob_pctl=90.0,
        ),
        ThrottleLevel(
            name="halt",
            drawdown_lo=0.15,
            drawdown_hi=1.00,
            max_total_positions_mult=0.0,
            max_per_cluster_mult=0.0,
            score_pctl_threshold=100.0,
            allow_pyramid=False,
            max_pyramid_layers=0,
            min_prob_pctl=100.0,
        ),
    )
    recovery_hysteresis: float = 0.02
    force_conservative_if_weekly_lt: float = -0.03
    force_conservative_if_monthly_lt: float = -0.08


@dataclass(frozen=True)
class PortfolioLogicConfig:
    """High-level switches for the new portfolio execution logic."""

    enable_htf_gate: bool = True
    enable_ranker: bool = True
    enable_trailing: bool = True
    enable_pyramid: bool = True
    enable_horizon_extend: bool = True
    enable_score_calibration: bool = True
    enable_risk_throttle: bool = True

    interval_gate: IntervalGateConfig = field(default_factory=IntervalGateConfig)
    ranker: OpportunityRankerConfig = field(default_factory=OpportunityRankerConfig)
    trailing: TrailingExitConfig = field(default_factory=TrailingExitConfig)
    horizon_extend: HorizonExtendConfig = field(default_factory=HorizonExtendConfig)
    pyramid: PyramidConfig = field(default_factory=PyramidConfig)
    risk_throttle: RiskThrottleConfig = field(default_factory=RiskThrottleConfig)
    caps: CapsConfig = field(default_factory=CapsConfig)

    def __post_init__(self) -> None:
        if self.enable_pyramid and not self.enable_trailing:
            raise ValueError("enable_pyramid requires enable_trailing")
        if self.enable_risk_throttle and not self.enable_score_calibration:
            raise ValueError("enable_risk_throttle requires enable_score_calibration")
