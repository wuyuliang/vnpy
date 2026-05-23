from __future__ import annotations

from cta.model.orchestration.pipeline_cli import _build_effective_oot_config, _parse_args


def test_pipeline_cli_parse_args_smoke() -> None:
    ns = _parse_args(["--symbol", "RB0", "--interval", "60min"])
    assert ns.symbol == "RB0"


def test_pipeline_cli_does_not_publish_oscillation_taper_flag() -> None:
    ns = _parse_args(["--symbol", "RB0"])
    assert not hasattr(ns, "enable_oscillation_taper")


def test_pipeline_cli_profit_aware_flags_default_off() -> None:
    ns = _parse_args(["--symbol", "RB0"])
    cfg = _build_effective_oot_config(
        use_portfolio_logic_runtime=bool(getattr(ns, "use_portfolio_logic_runtime", False)),
        args=ns,
    )
    assert bool(cfg.use_trend_aware_trade_filter) is False
    assert bool(cfg.trailing_take_profit.use_trailing_take_profit) is False
    assert bool(cfg.profit_aware_horizon.use_profit_aware_horizon) is False


def test_pipeline_cli_profit_aware_flags_enable_and_normalize_cells() -> None:
    ns = _parse_args(
        [
            "--symbol",
            "RB0",
            "--use-portfolio-logic-runtime",
            "--enable-trailing-take-profit",
            "--trailing-tp-enabled-cells",
            "index|day,metal|60min",
            "--enable-profit-aware-horizon",
            "--profit-aware-horizon-enabled-cells",
            "index|day",
            "--enable-trend-aware-trade-filter",
            "--trend-aware-trade-filter-enabled-cells",
            "index|day",
        ]
    )
    cfg = _build_effective_oot_config(
        use_portfolio_logic_runtime=bool(getattr(ns, "use_portfolio_logic_runtime", False)),
        args=ns,
    )
    assert bool(cfg.use_portfolio_logic_runtime) is True
    assert bool(cfg.trailing_take_profit.use_trailing_take_profit) is True
    assert bool(cfg.trailing_take_profit.enabled_by_cluster_interval["index|day"]) is True
    assert bool(cfg.trailing_take_profit.enabled_by_cluster_interval["metal|60min"]) is True
    assert bool(cfg.profit_aware_horizon.use_profit_aware_horizon) is True
    assert bool(cfg.profit_aware_horizon.enabled_by_cluster_interval["index|day"]) is True
    assert bool(cfg.use_trend_aware_trade_filter) is True
    assert bool(cfg.trend_aware_trade_filter_enabled_by_cluster_interval["index|day"]) is True
