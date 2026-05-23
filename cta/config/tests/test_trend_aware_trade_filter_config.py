from __future__ import annotations

import pytest

from cta.config.trend_aware_trade_filter_config import TrendAwareTradeFilterConfig


def test_trend_aware_trade_filter_defaults_disabled() -> None:
    cfg = TrendAwareTradeFilterConfig()
    assert bool(cfg.use_trend_aware_trade_filter) is False
    assert cfg.is_enabled("index", "day") is False


def test_trend_aware_trade_filter_enabled_map_normalization() -> None:
    cfg = TrendAwareTradeFilterConfig(
        use_trend_aware_trade_filter=True,
        enabled_by_cluster_interval={"INDEX|Minute60": True},
    )
    assert cfg.is_enabled("index", "60min") is True
    assert cfg.is_enabled("index", "day") is False


def test_trend_aware_trade_filter_rejects_bad_vol_rank() -> None:
    with pytest.raises(ValueError):
        TrendAwareTradeFilterConfig(require_vol_rank_above=1.2)

