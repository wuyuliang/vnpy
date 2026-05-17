from __future__ import annotations

import importlib


def test_run_all_features_split_modules_importable() -> None:
    for mod in (
        "cta.feature.feature_compute_dispatch",
        "cta.feature.feature_interval_runner",
        "cta.feature.run_all_features",
    ):
        importlib.import_module(mod)
