"""Pipeline stage compatibility aliases."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import ModelPipelineResult
from cta.model.orchestration.pipeline_multi import run_model_pipeline_multi
from cta.model.orchestration.pipeline_run import run_model_pipeline

__all__ = [
    "ModelPipelineResult",
    "run_model_pipeline",
    "run_model_pipeline_multi",
]
