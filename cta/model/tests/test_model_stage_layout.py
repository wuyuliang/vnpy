from __future__ import annotations

import importlib
from pathlib import Path


def test_model_root_only_keeps_public_entrypoints() -> None:
    root_py = {p.name for p in Path("cta/model").glob("*.py")}
    # 2026-05-25 起 train/eval 拆分新增 train.py / eval.py / eval_only_{cli,run}.py 入口。
    assert root_py <= {
        "__init__.py",
        "model_pipeline.py",
        "train.py",
        "eval.py",
        "eval_only_cli.py",
        "eval_only_run.py",
    }

def test_stage_modules_do_not_keep_legacy_pipeline_orchestrator_prefix() -> None:
    stage_roots = [
        Path("cta/model/dataset"),
        Path("cta/model/training"),
        Path("cta/model/orchestration"),
        Path("cta/model/reporting"),
    ]
    offenders: list[str] = []
    for root in stage_roots:
        for path in root.glob("pipeline_orchestrator_*.py"):
            offenders.append(str(path))
    assert offenders == [], f"legacy prefixed modules should be removed: {offenders}"


def test_model_stage_packages_are_importable() -> None:
    modules = [
        "cta.model.dataset.pipeline_dataset_prep",
        "cta.model.dataset.pipeline_feature_enrichment",
        "cta.model.dataset.pipeline_splits",
        "cta.model.training.trade_filter_model",
        "cta.model.training.regime_classifier_model",
        "cta.model.training.mfe_mae_model",
        "cta.model.training.final_decision_model",
        "cta.model.orchestration.pipeline_orchestrator",
        "cta.model.orchestration.pipeline_run",
        "cta.model.orchestration.pipeline_cli",
        "cta.model.oot.pipeline_oot_evaluation",
        "cta.model.oot.oot_intrabar",
        "cta.model.oot.block_reasons",
        "cta.model.reporting.oot_report_writer",
        "cta.model.reporting.group_pool_aggregate",
        "cta.model.reporting.pipeline_html_report",
    ]
    for name in modules:
        importlib.import_module(name)


def test_model_pipeline_entrypoint_stays_stable() -> None:
    module = importlib.import_module("cta.model.model_pipeline")
    assert hasattr(module, "main")
    assert hasattr(module, "run_model_pipeline")
    assert hasattr(module, "run_model_pipeline_multi")
