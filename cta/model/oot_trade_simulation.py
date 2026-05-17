"""OOT trade simulation helpers (compatibility scaffold)."""
from __future__ import annotations

import pandas as pd


def simulate_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Pass-through placeholder while OOT engine split is in progress."""
    return df


__all__ = ["simulate_trades"]
