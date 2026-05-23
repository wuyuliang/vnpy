from __future__ import annotations

import numpy as np
import pandas as pd

from cta.config.adaptive_setup_window_config import AdaptiveSetupWindowConfig
from cta.strategy.baseline_feature_frame import prepare_master_feature_frame


def _bars(n: int = 80) -> pd.DataFrame:
    dt = pd.date_range("2024-01-01", periods=n, freq="D")
    base = 100.0 + np.linspace(0.0, 10.0, n)
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": base,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + 0.2,
            "volume": np.full(n, 1000.0),
            "open_interest": np.full(n, 2000.0),
            "turnover": np.full(n, 1e6),
        }
    )


def test_feature_frame_emits_adaptive_window_used_when_enabled() -> None:
    cfg = AdaptiveSetupWindowConfig(
        use_adaptive_setup_window=True,
        enabled_by_cluster_interval={"precious|day": True},
        window_multiplier_by_cluster_interval={"precious|day": 0.5},
    )
    out = prepare_master_feature_frame(_bars(), interval="day", symbol="AU0", adaptive_setup_window_cfg=cfg)
    assert "adaptive_window_used" in out.columns
    assert int(pd.to_numeric(out["adaptive_window_used"], errors="coerce").dropna().iloc[-1]) == 10

