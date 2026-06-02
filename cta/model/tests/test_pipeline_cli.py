from __future__ import annotations

from cta.model.orchestration.pipeline_cli import _build_effective_oot_config, _parse_args


def test_pipeline_cli_parse_args_smoke() -> None:
    ns = _parse_args(["--symbol", "RB0", "--interval", "60min"])
    assert ns.symbol == "RB0"


def test_pipeline_cli_does_not_publish_oscillation_taper_flag() -> None:
    ns = _parse_args(["--symbol", "RB0"])
    assert not hasattr(ns, "enable_oscillation_taper")


def test_pipeline_cli_risk_system_default_off() -> None:
    ns = _parse_args(["--symbol", "RB0"])
    cfg = _build_effective_oot_config(
        use_portfolio_logic_runtime=bool(getattr(ns, "use_portfolio_logic_runtime", False)),
        args=ns,
    )
    assert cfg.risk_system is None


def test_pipeline_cli_can_enable_risk_system_and_overrides() -> None:
    ns = _parse_args(
        [
            "--symbol",
            "RB0",
            "--enable-risk-system",
            "--risk-enable-bucket-scaling",
            "--risk-disable-linear-dd-scaler",
            "--risk-disable-dynamic-bump",
            "--risk-quantile-field",
            "p80",
            "--risk-manifest-path",
            "cta/model/manifests/custom.json",
        ]
    )
    cfg = _build_effective_oot_config(
        use_portfolio_logic_runtime=bool(getattr(ns, "use_portfolio_logic_runtime", False)),
        args=ns,
    )
    assert cfg.risk_system is not None
    assert bool(cfg.risk_system.enable_quantile_threshold) is True
    assert bool(cfg.risk_system.enable_bucket_scaling) is True
    assert bool(cfg.risk_system.enable_linear_dd_scaler) is False
    assert bool(cfg.risk_system.enable_dynamic_bump) is False
    assert str(cfg.risk_system.quantile_field) == "p80"
    assert str(cfg.risk_system.quantile_manifest_path) == "cta/model/manifests/custom.json"


def test_pipeline_cli_can_enable_impact_cost_and_disable_liquidity_floor() -> None:
    ns = _parse_args(
        [
            "--symbol",
            "RB0",
            "--enable-impact-cost",
            "--impact-cost-k",
            "0.07",
            "--disable-liquidity-floor",
        ]
    )
    cfg = _build_effective_oot_config(
        use_portfolio_logic_runtime=bool(getattr(ns, "use_portfolio_logic_runtime", False)),
        args=ns,
    )
    assert bool(cfg.use_impact_cost) is True
    assert float(cfg.impact_cost_k) == 0.07
    assert bool(cfg.use_liquidity_floor_guard) is False
