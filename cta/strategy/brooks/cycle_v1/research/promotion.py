"""Fail-closed promotion gate from research to vn.py decision-only dry-run."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


REQUIRED_DRY_RUN_GATES = (
    "metadata_auditable",
    "prefix_invariant",
    "offline_online_parity",
    "real_contract_backtest",
    "cross_instrument_locked_oos",
    "cost_delay_stress",
    "portfolio_fail_closed",
    "reproducible_bundle",
)


@dataclass(frozen=True)
class PromotionAssessment:
    status: str
    blockers: tuple[str, ...]


def assess_dry_run_promotion(evidence: Mapping[str, bool]) -> PromotionAssessment:
    unknown = sorted(set(evidence).difference(REQUIRED_DRY_RUN_GATES))
    if unknown:
        raise ValueError(f"unknown dry-run evidence gates: {unknown}")
    blockers = tuple(name for name in REQUIRED_DRY_RUN_GATES if evidence.get(name) is not True)
    return PromotionAssessment(
        status="DRY_RUN_ELIGIBLE" if not blockers else "RESEARCH_ONLY",
        blockers=blockers,
    )


__all__ = [
    "PromotionAssessment", "REQUIRED_DRY_RUN_GATES", "assess_dry_run_promotion"
]
