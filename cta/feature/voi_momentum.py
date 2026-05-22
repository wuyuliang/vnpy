"""VOI regime-adaptive momentum feature calculations."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.config.voi_momentum_config import VoiMomentumConfig


def _rolling_rank(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(int(window), min_periods=int(window)).rank(pct=True)


def _realized_vol_rank(close: pd.Series, *, vol_window: int, rank_window: int) -> pd.Series:
    log_ret = np.log(pd.to_numeric(close, errors="coerce")).diff()
    realized_vol = log_ret.rolling(int(vol_window), min_periods=int(vol_window)).std() * np.sqrt(252.0)
    return _rolling_rank(realized_vol, int(rank_window))


def classify_vol_regime(
    close: pd.Series,
    *,
    vol_window: int = 20,
    rank_window: int = 252,
    high_threshold: float = 0.70,
    low_threshold: float = 0.30,
) -> pd.Series:
    """Classify rolling volatility into high, low, mid, or warmup NaN."""
    rank = _realized_vol_rank(close, vol_window=vol_window, rank_window=rank_window)
    regime = pd.Series(np.nan, index=close.index, dtype=object)
    valid = rank.notna()
    regime.loc[valid] = "mid"
    regime.loc[rank >= float(high_threshold)] = "high"
    regime.loc[rank <= float(low_threshold)] = "low"
    return regime


def compute_adaptive_momentum(
    close: pd.Series,
    vol_regime: pd.Series,
    *,
    fast_window: int = 5,
    slow_window: int = 20,
) -> pd.Series:
    """Use fast momentum in high vol, slow momentum in low vol, zero in mid."""
    px = pd.to_numeric(close, errors="coerce")
    fast = px.pct_change(int(fast_window))
    slow = px.pct_change(int(slow_window))
    regime = vol_regime.astype(object)
    out = pd.Series(np.nan, index=px.index, dtype=float)
    out.loc[regime == "high"] = fast.loc[regime == "high"]
    out.loc[regime == "low"] = slow.loc[regime == "low"]
    out.loc[regime == "mid"] = 0.0
    return out


def compute_voi_features(
    df: pd.DataFrame,
    *,
    cfg: VoiMomentumConfig,
    cluster: str | None = None,
    interval: str = "day",
) -> pd.DataFrame:
    """Return opt-in VOI momentum columns aligned to ``df`` rows."""
    if not cfg.is_enabled(cluster, interval):
        return pd.DataFrame(index=df.index)
    for col in ("close", "high", "low", "volume"):
        if col not in df.columns:
            raise KeyError(f"compute_voi_features missing column: {col}")

    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")
    vol_rank = _realized_vol_rank(close, vol_window=cfg.vol_window, rank_window=cfg.rank_window)
    vol_regime = classify_vol_regime(
        close,
        vol_window=cfg.vol_window,
        rank_window=cfg.rank_window,
        high_threshold=cfg.high_vol_threshold,
        low_threshold=cfg.low_vol_threshold,
    )
    adaptive = compute_adaptive_momentum(
        close,
        vol_regime,
        fast_window=cfg.fast_momentum_window,
        slow_window=cfg.slow_momentum_window,
    )
    span = (high - low).where(high > low, np.nan)
    intraday_position = ((close - low) / span).clip(lower=0.0, upper=1.0)
    intraday_factor = (2.0 * (intraday_position - 0.5)).clip(lower=-1.0, upper=1.0)
    volume_rank = _rolling_rank(volume, cfg.volume_rank_window).clip(lower=0.0, upper=1.0)
    raw = adaptive * intraday_factor * volume_rank
    signed = raw.copy()
    if float(cfg.min_volume_rank) > 0.0:
        signed = signed.where(volume_rank >= float(cfg.min_volume_rank), 0.0)
    signed = signed.where(vol_regime.notna())

    return pd.DataFrame(
        {
            "voi_vol_regime": vol_regime,
            "voi_realized_vol_rank": vol_rank,
            "voi_adaptive_momentum": adaptive,
            "voi_intraday_position": intraday_position,
            "voi_intraday_position_factor": intraday_factor,
            "voi_volume_rank": volume_rank,
            "voi_momentum_raw": raw,
            "voi_momentum_signed_score": signed,
        },
        index=df.index,
    )


__all__ = [
    "classify_vol_regime",
    "compute_adaptive_momentum",
    "compute_voi_features",
]
