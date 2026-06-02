"""Guard for score distribution drift (W9)."""
from __future__ import annotations

import pandas as pd

from cta.live.risk import RiskContext, RiskDecision, _BaseRule
from cta.risk.guards.config import ScoreDistributionDriftConfig
from cta.risk.monitors.score_distribution_drift import ScoreDistributionDriftMonitor


class ScoreDistributionDriftGuard(_BaseRule):
    """Block new opens when score distribution drift is critical or emergency."""

    name: str = "score_distribution_drift"

    def __init__(
        self,
        cfg: ScoreDistributionDriftConfig | None = None,
        *,
        monitor: ScoreDistributionDriftMonitor | None = None,
    ) -> None:
        self.cfg = cfg or ScoreDistributionDriftConfig()
        self.monitor = monitor or ScoreDistributionDriftMonitor(self.cfg)

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        offset = str(order.get("offset", "")).strip().lower()
        now = pd.Timestamp(getattr(ctx, "now", pd.Timestamp.now()))
        assessment = self.monitor.assess(now=now)
        severity = assessment.severity
        if severity in {"critical", "emergency"}:
            if offset == "close":
                if self.cfg.block_close_on_critical:
                    return RiskDecision(
                        False,
                        (
                            f"{self.name}:{severity}:"
                            f"value={assessment.value:.4f}:{assessment.metric}"
                        ),
                    )
                return RiskDecision(True, "")
            if self.cfg.block_open_on_critical:
                return RiskDecision(
                    False,
                    (
                        f"{self.name}:{severity}:"
                        f"value={assessment.value:.4f}:{assessment.metric}"
                    ),
                )
        return RiskDecision(True, "")


__all__ = ["ScoreDistributionDriftGuard"]
