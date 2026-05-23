"""Adaptive setup-window resolution for ATR/Donchian signals."""
from __future__ import annotations

from cta.config.adaptive_setup_window_config import AdaptiveSetupWindowConfig
from cta.config.interval_utils import normalize_portfolio_interval
from cta.config.symbol_cluster_config import infer_symbol_cluster


def resolve_setup_window(
    symbol: str,
    interval: str,
    setup_type: str,
    cfg: AdaptiveSetupWindowConfig,
) -> int:
    """Resolve setup lookback window by (cluster, interval)."""
    setup = str(setup_type).strip().lower()
    if setup not in {"atr_breakout", "donchian_breakout"}:
        raise ValueError(f"unsupported setup_type={setup_type!r}")
    base = (
        int(cfg.base_atr_window)
        if setup == "atr_breakout"
        else int(cfg.base_donchian_window)
    )
    if not bool(cfg.use_adaptive_setup_window):
        return base
    cluster = str(infer_symbol_cluster(str(symbol).upper())).strip().lower()
    iv = normalize_portfolio_interval(interval)
    if not cfg.is_enabled(cluster, iv):
        return base
    key = f"{cluster}|{iv}"
    mul = float(cfg.window_multiplier_by_cluster_interval.get(key, 1.0))
    out = int(round(base * mul))
    out = max(int(cfg.min_window), min(int(cfg.max_window), out))
    return out


__all__ = ["resolve_setup_window"]
