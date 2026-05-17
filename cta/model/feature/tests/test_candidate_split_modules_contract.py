from __future__ import annotations

import importlib


def test_candidate_split_modules_importable() -> None:
    modules = [
        "cta.model.feature.candidate_schema",
        "cta.model.feature.candidate_baseline_bridge",
    ]
    for name in modules:
        importlib.import_module(name)
