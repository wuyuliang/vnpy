from __future__ import annotations

import importlib
from pathlib import Path


def test_pipeline_split_modules_importable() -> None:
    modules = [
        "cta.model.reporting.pipeline_provenance",
        "cta.model.dataset.pipeline_feature_meaning",
        "cta.model.dataset.pipeline_feature_curation",
        "cta.model.dataset.pipeline_symbol_ranking",
        "cta.model.training.pipeline_param_grids",
        "cta.model.dataset.pipeline_splits",
        "cta.model.dataset.pipeline_dataset_prep",
        "cta.model.reporting.pipeline_diagnostics",
        "cta.model.dataset.pipeline_meta_features",
        "cta.model.orchestration.pipeline_stages",
        "cta.model.orchestration.pipeline_cli",
        "cta.model.oot.oot_gates",
        "cta.model.oot.oot_position_sizing",
        "cta.model.oot.oot_portfolio_constraints",
        "cta.model.oot.oot_trade_simulation",
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


def test_pipeline_core_modules_do_not_use_exec_shim_loader() -> None:
    targets = [
        Path("cta/model/orchestration/pipeline_orchestrator.py"),
        Path("cta/model/oot/pipeline_oot_evaluation.py"),
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert "exec(" not in text


def test_pipeline_split_modules_use_explicit_imports() -> None:
    """Split modules should expose logical dependencies, not wildcard globals."""
    offenders: list[str] = []
    split_paths = (
        list(Path("cta/model/orchestration").glob("pipeline_*.py"))
        + list(Path("cta/model/dataset").glob("pipeline_*.py"))
        + list(Path("cta/model/training").glob("pipeline_*.py"))
        + list(Path("cta/model/reporting").glob("pipeline_*.py"))
        + [Path("cta/model/oot/pipeline_oot_evaluation.py")]
    )
    for path in split_paths:
        if path.name == "pipeline_base.py":
            continue
        text = path.read_text(encoding="utf-8")
        if " import *" in text:
            offenders.append(path.name)
    assert offenders == [], f"wildcard imports hide module boundaries: {offenders}"
