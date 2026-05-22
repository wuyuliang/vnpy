from __future__ import annotations

from cta.model.orchestration.pipeline_orchestrator import ModelPipelineResult, run_model_pipeline, run_model_pipeline_multi


def test_pipeline_orchestrator_exports() -> None:
    assert ModelPipelineResult is not None
    assert callable(run_model_pipeline)
    assert callable(run_model_pipeline_multi)

