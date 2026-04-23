"""§03-04 cross-sectional momentum."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class XSPortfolioTarget:
    rebalance_date: pd.Timestamp
    long_symbols: list[str]
    short_symbols: list[str]
    weights: dict[str, float]


def compute_xs_momentum(
    price_panel: pd.DataFrame,
    lookback: int = 60,
    vol_window: int = 60,
) -> pd.DataFrame:
    """Compute vol-adjusted momentum for each symbol by date."""
    if price_panel.empty:
        return price_panel.copy()
    ret = price_panel.astype(float).pct_change(int(lookback))
    vol = price_panel.astype(float).pct_change().rolling(vol_window, min_periods=max(5, vol_window // 3)).std()
    mom = ret / vol.replace(0.0, np.nan)
    return mom.replace([np.inf, -np.inf], np.nan)


def build_xs_portfolio(
    momentum: pd.DataFrame,
    top_pct: float = 0.2,
    bot_pct: float = 0.2,
    gross_exposure: float = 1.0,
) -> list[XSPortfolioTarget]:
    """Build long-short portfolio targets on each rebalance date."""
    if momentum.empty:
        return []
    top_pct = float(max(0.0, min(0.5, top_pct)))
    bot_pct = float(max(0.0, min(0.5, bot_pct)))
    targets: list[XSPortfolioTarget] = []
    for ts, row in momentum.dropna(how="all").iterrows():
        s = row.dropna().sort_values(ascending=False)
        if len(s) < 3:
            continue
        n_long = max(1, int(np.floor(len(s) * top_pct)))
        n_short = max(1, int(np.floor(len(s) * bot_pct)))
        long_symbols = list(s.index[:n_long])
        short_symbols = list(s.index[-n_short:])

        gross = float(gross_exposure)
        w_long = gross * 0.5 / max(n_long, 1)
        w_short = gross * 0.5 / max(n_short, 1)
        weights = {sym: w_long for sym in long_symbols}
        for sym in short_symbols:
            weights[sym] = -w_short
        targets.append(
            XSPortfolioTarget(
                rebalance_date=pd.Timestamp(ts),
                long_symbols=long_symbols,
                short_symbols=short_symbols,
                weights=weights,
            )
        )
    return targets

