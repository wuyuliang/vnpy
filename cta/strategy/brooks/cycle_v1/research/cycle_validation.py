"""Level-1 cycle coverage, persistence, and transition diagnostics."""
from __future__ import annotations

from typing import Any

import pandas as pd

from ..core.types import MarketCycle


def validate_cycles(cycles: pd.DataFrame) -> dict[str, Any]:
    required = {"cycle", "direction", "feature_asof"}
    missing = sorted(required.difference(cycles.columns))
    if missing:
        raise ValueError(f"missing cycle validation columns: {','.join(missing)}")
    if cycles.empty:
        return {
            "bar_count": 0,
            "unavailable_ratio": 1.0,
            "average_state_duration": 0.0,
            "coverage": {},
            "transitions": {},
        }
    values = cycles["cycle"].astype(str)
    run_id = values.ne(values.shift(1)).cumsum()
    durations = cycles.groupby(run_id, sort=False).size()
    transition_counts = (
        pd.DataFrame({"from": values.shift(1), "to": values})
        .dropna()
        .value_counts()
    )
    return {
        "bar_count": len(cycles),
        "unavailable_ratio": float(values.eq(MarketCycle.UNAVAILABLE.value).mean()),
        "average_state_duration": float(durations.mean()),
        "coverage": {key: float(value) for key, value in values.value_counts(normalize=True).items()},
        "transitions": {
            f"{source}->{target}": int(count)
            for (source, target), count in transition_counts.items()
        },
    }


__all__ = ["validate_cycles"]

