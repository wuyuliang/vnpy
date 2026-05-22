"""Mean-reversion feature calculations for range setups."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _compute_rsi(close: pd.Series, window: int) -> pd.Series:
    delta = pd.to_numeric(close, errors="coerce").diff()
    gains = delta.clip(lower=0.0).rolling(int(window), min_periods=int(window)).mean()
    losses = (-delta.clip(upper=0.0)).rolling(int(window), min_periods=int(window)).mean()
    rs = gains / losses.where(losses > 0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(losses > 0.0, 100.0)
    return rsi.where(gains.notna() & losses.notna())


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> pd.Series:
    hi = pd.to_numeric(high, errors="coerce")
    lo = pd.to_numeric(low, errors="coerce")
    px = pd.to_numeric(close, errors="coerce")
    up = hi.diff()
    down = -lo.diff()
    plus_dm = up.where((up > down) & (up > 0.0), 0.0)
    minus_dm = down.where((down > up) & (down > 0.0), 0.0)
    tr = pd.concat([(hi - lo).abs(), (hi - px.shift(1)).abs(), (lo - px.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(int(window), min_periods=int(window)).mean()
    plus_di = 100.0 * plus_dm.rolling(int(window), min_periods=int(window)).mean() / atr.where(atr > 0.0, np.nan)
    minus_di = 100.0 * minus_dm.rolling(int(window), min_periods=int(window)).mean() / atr.where(atr > 0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).where((plus_di + minus_di) > 0.0, np.nan)
    return dx.rolling(int(window), min_periods=int(window)).mean()


def compute_mean_reversion_features(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    *,
    bb_window: int = 20,
    bb_std_mult: float = 2.0,
    rsi_window: int = 14,
    adx_window: int = 14,
) -> pd.DataFrame:
    """Return Bollinger, RSI and ADX features for range mean reversion."""
    px = pd.to_numeric(close, errors="coerce")
    sma = px.rolling(int(bb_window), min_periods=int(bb_window)).mean()
    std = px.rolling(int(bb_window), min_periods=int(bb_window)).std(ddof=0)
    zscore = (px - sma) / std.where(std > 0.0, np.nan)
    rsi = _compute_rsi(px, int(rsi_window))
    adx = _compute_adx(high, low, px, int(adx_window))
    signal_strength = (-zscore / max(float(bb_std_mult), 1e-9)).clip(lower=-2.0, upper=2.0)
    return pd.DataFrame(
        {
            "mr_sma": sma,
            "mr_bb_upper": sma + float(bb_std_mult) * std,
            "mr_bb_lower": sma - float(bb_std_mult) * std,
            "mr_zscore": zscore,
            "mr_rsi": rsi,
            "mr_adx": adx,
            "mr_signal_strength": signal_strength,
        },
        index=px.index,
    )


__all__ = ["compute_mean_reversion_features"]
