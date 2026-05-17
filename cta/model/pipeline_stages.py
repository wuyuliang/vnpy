"""Stage helpers for orchestration refactor.

This file currently exposes compatibility aliases before stage internals are
fully migrated out of ``pipeline_orchestrator.py``.
"""
from __future__ import annotations

from cta.model.pipeline_orchestrator import ModelPipelineResult, run_model_pipeline, run_model_pipeline_multi

__all__ = [
    "ModelPipelineResult",
    "run_model_pipeline",
    "run_model_pipeline_multi",
]
