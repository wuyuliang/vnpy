"""OOT position sizing helpers (compatibility scaffold)."""
from __future__ import annotations

import pandas as pd


def compute_sizes(df: pd.DataFrame) -> pd.DataFrame:
    """Pass-through placeholder while OOT engine split is in progress."""
    return df


__all__ = ["compute_sizes"]
