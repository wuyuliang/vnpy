"""Import-safety tests for cta.risk package."""
from __future__ import annotations

import importlib


def test_import_risk_submodules_without_live_side_effects() -> None:
    importlib.import_module("cta.risk.base")
    importlib.import_module("cta.risk.config")
    importlib.import_module("cta.risk.orchestrator")

