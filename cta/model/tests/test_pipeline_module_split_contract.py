from __future__ import annotations

import importlib
from pathlib import Path


def test_pipeline_split_modules_importable() -> None:
    modules = [
        "cta.model.pipeline_provenance",
        "cta.model.pipeline_feature_meaning",
        "cta.model.pipeline_feature_curation",
        "cta.model.pipeline_symbol_ranking",
        "cta.model.pipeline_param_grids",
        "cta.model.pipeline_splits",
        "cta.model.pipeline_dataset_prep",
        "cta.model.pipeline_diagnostics",
        "cta.model.pipeline_meta_features",
        "cta.model.pipeline_stages",
        "cta.model.pipeline_cli",
        "cta.model.oot_gates",
        "cta.model.oot_position_sizing",
        "cta.model.oot_portfolio_constraints",
        "cta.model.oot_trade_simulation",
    ]
    for name in modules:
        importlib.import_module(name)


def test_no_shim_namespace_mocking_in_model_pipeline_tests() -> None:
    """防止回归到 patch model_pipeline shim 私有函数的问题。"""
    root = Path(__file__).resolve().parent
    patterns = (
        "mock.patch.object(mp,",
        "patch(\"cta.model.model_pipeline.",
        "patch('cta.model.model_pipeline.",
    )
    offenders: list[str] = []
    for path in root.glob("test_*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if any(p in text for p in patterns):
            offenders.append(str(path.name))
    assert not offenders, f"shim namespace patch found: {offenders}"
