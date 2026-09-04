"""Causal stop-management rules that can only reduce open risk."""
from __future__ import annotations

from ...config import BrooksCycleConfig


def allow_break_even(
    *,
    running_mfe_R: float,
    favorable_structure_confirmed: bool,
    remaining_space_R: float,
    config: BrooksCycleConfig,
) -> bool:
    return bool(
        running_mfe_R >= config.execution.break_even_min_mfe_R
        and favorable_structure_confirmed
        and remaining_space_R >= config.execution.runner_min_space_R
    )


def ratchet_stop(*, direction: int, current_stop: float, proposed_stop: float) -> float:
    if direction == 1:
        return max(current_stop, proposed_stop)
    if direction == -1:
        return min(current_stop, proposed_stop)
    raise ValueError("direction must be -1 or 1")


__all__ = ["allow_break_even", "ratchet_stop"]

