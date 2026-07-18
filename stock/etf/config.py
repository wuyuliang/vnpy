from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    """Validated parameters for the ETF rotation strategy."""

    benchmark_symbol: str = "000300.SH"
    adx_period: int = 5
    adx_threshold: float = 20.0
    atr_period: int = 5
    atr_stop_multiple: float = 2.0
    risk_per_trade: float = 0.01
    max_position_weight: float = 0.20
    max_industry_weight: float = 0.50
    entry_rank: int = 5
    exit_rank: int = 10
    entry_confirmation_days: int = 1
    max_entry_gap_atr: float | None = None
    correlation_lookback: int = 60
    min_correlation_observations: int = 40
    correlation_threshold: float | None = None
    max_correlation_weight: float | None = None
    winner_holding_enabled: bool = False
    market_state_enabled: bool = False
    dynamic_risk_enabled: bool = False
    winner_promotion_atr_multiple: float = 2.0
    exit_rank_confirmation_days: int = 3
    max_portfolio_stop_risk: float | None = None
    max_cluster_stop_risk: float | None = None
    caution_max_gross_weight: float = 0.50
    caution_entry_rank: int = 3
    caution_risk_fraction: float = 0.50
    risk_off_breadth_threshold: float = 0.35
    market_state_confirmation_days: int = 2
    min_turnover: float = 200_000_000.0
    min_listing_months: int = 6
    lot_size: int = 100
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    slippage_rate: float = 0.0005
    initial_capital: float = 1_000_000.0
    warmup_bars: int = 80

    def __post_init__(self) -> None:
        if self.atr_period not in {5, 10}:
            raise ValueError("atr_period must be 5 or 10")
        if self.adx_period not in {5, 10}:
            raise ValueError("adx_period must be 5 or 10")
        if not 0 < self.risk_per_trade <= 1:
            raise ValueError("risk_per_trade must be in (0, 1]")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1]")
        if not 0 < self.max_industry_weight <= 1:
            raise ValueError("max_industry_weight must be in (0, 1]")
        if self.entry_rank < 1 or self.exit_rank < self.entry_rank:
            raise ValueError("exit_rank must be greater than or equal to entry_rank")
        if self.entry_confirmation_days < 1:
            raise ValueError("entry_confirmation_days must be positive")
        if self.max_entry_gap_atr is not None and self.max_entry_gap_atr <= 0:
            raise ValueError("max_entry_gap_atr must be positive when enabled")
        if self.correlation_lookback < 2:
            raise ValueError("correlation_lookback must be at least 2")
        if not 2 <= self.min_correlation_observations <= self.correlation_lookback:
            raise ValueError(
                "min_correlation_observations must be between 2 and correlation_lookback"
            )
        if (
            self.correlation_threshold is not None
            and not 0 < self.correlation_threshold <= 1
        ):
            raise ValueError("correlation_threshold must be in (0, 1] when enabled")
        if (
            self.max_correlation_weight is not None
            and not 0 < self.max_correlation_weight <= 1
        ):
            raise ValueError("max_correlation_weight must be in (0, 1] when enabled")
        if (self.correlation_threshold is None) != (
            self.max_correlation_weight is None
        ):
            raise ValueError(
                "correlation_threshold and max_correlation_weight must be enabled together"
            )
        if self.winner_promotion_atr_multiple <= 0:
            raise ValueError("winner_promotion_atr_multiple must be positive")
        if self.exit_rank_confirmation_days < 1:
            raise ValueError("exit_rank_confirmation_days must be positive")
        for name, value in (
            ("max_portfolio_stop_risk", self.max_portfolio_stop_risk),
            ("max_cluster_stop_risk", self.max_cluster_stop_risk),
        ):
            if value is not None and not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1] when enabled")
        if (
            self.max_cluster_stop_risk is not None
            and self.max_portfolio_stop_risk is not None
            and self.max_cluster_stop_risk > self.max_portfolio_stop_risk
        ):
            raise ValueError(
                "max_cluster_stop_risk must not exceed max_portfolio_stop_risk"
            )
        for name, value in (
            ("caution_max_gross_weight", self.caution_max_gross_weight),
            ("caution_risk_fraction", self.caution_risk_fraction),
            ("risk_off_breadth_threshold", self.risk_off_breadth_threshold),
        ):
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.caution_entry_rank < 1:
            raise ValueError("caution_entry_rank must be positive")
        if self.market_state_enabled and self.caution_entry_rank > self.entry_rank:
            raise ValueError("caution_entry_rank must not exceed entry_rank")
        if self.market_state_confirmation_days < 1:
            raise ValueError("market_state_confirmation_days must be positive")
        if self.lot_size < 1 or self.atr_stop_multiple <= 0:
            raise ValueError("lot_size and atr_stop_multiple must be positive")
