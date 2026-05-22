"""CTA model pipeline public orchestrator.

The heavy implementation is split across ``pipeline_*`` stage modules.
This module keeps the historic import path stable and synchronizes private
helpers so tests and CLI monkeypatches still affect the delegated functions.
"""
from __future__ import annotations

from types import ModuleType
from typing import Any

from cta.model.orchestration import pipeline_base as _base
from cta.model.orchestration import pipeline_cli as _cli
from cta.model.dataset import pipeline_dataset_prep as _dataset
from cta.model.reporting import pipeline_diagnostics as _diagnostics
from cta.model.dataset import pipeline_feature_curation as _curation
from cta.model.dataset import pipeline_feature_meaning as _meaning
from cta.model.dataset import pipeline_meta_features as _meta
from cta.model.orchestration import pipeline_multi as _multi
from cta.model.reporting import pipeline_outputs as _outputs
from cta.model.training import pipeline_param_grids as _params
from cta.model.orchestration import pipeline_run as _run
from cta.model.dataset import pipeline_symbol_ranking as _ranking


_MODULES: tuple[ModuleType, ...] = (
    _base,
    _meaning,
    _curation,
    _ranking,
    _diagnostics,
    _dataset,
    _params,
    _meta,
    _outputs,
    _run,
    _multi,
    _cli,
)

_IMPL_RUN_MODEL_PIPELINE = _run.run_model_pipeline
_IMPL_RUN_MODEL_PIPELINE_MULTI = _multi.run_model_pipeline_multi
_IMPL_MAIN = _cli.main
_IMPL_BUILD_POOLED_FEATURE_DF = _dataset._build_pooled_feature_df
_IMPL_RECOMPUTE_OOT = _outputs._recompute_oot_with_shared_htf_reference
_IMPL_WRITE_GROUP_POOL_BUNDLE = _outputs._write_group_pool_runtime_bundle


def _visible_symbols(module: ModuleType) -> dict[str, Any]:
    return {
        name: value
        for name, value in vars(module).items()
        if not (name.startswith("__") and name.endswith("__"))
    }


def _sync_module_names() -> None:
    """Share split-module helpers, including patched orchestrator attributes."""
    merged: dict[str, Any] = {}
    for module in _MODULES:
        merged.update(_visible_symbols(module))
    merged.update(
        {
            name: value
            for name, value in globals().items()
            if not (name.startswith("__") and name.endswith("__"))
        }
    )
    for module in _MODULES:
        module.__dict__.update(merged)
    globals().update(merged)


_sync_module_names()


def run_model_pipeline(*args: Any, **kwargs: Any) -> ModelPipelineResult:
    """Run the candidate -> feature -> model -> OOT pipeline."""
    _sync_module_names()
    return _IMPL_RUN_MODEL_PIPELINE(*args, **kwargs)


def run_model_pipeline_multi(*args: Any, **kwargs: Any) -> list[ModelPipelineResult]:
    """Run the model pipeline for multiple intervals."""
    _sync_module_names()
    return _IMPL_RUN_MODEL_PIPELINE_MULTI(*args, **kwargs)


def _build_pooled_feature_df(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
    """Compatibility wrapper used by tests that patch source helpers."""
    _sync_module_names()
    return _IMPL_BUILD_POOLED_FEATURE_DF(*args, **kwargs)


def _recompute_oot_with_shared_htf_reference(*args: Any, **kwargs: Any) -> int:
    """Compatibility wrapper for shared-HTF OOT recomputation."""
    _sync_module_names()
    return int(_IMPL_RECOMPUTE_OOT(*args, **kwargs))


def _write_group_pool_runtime_bundle(*args: Any, **kwargs: Any) -> Any:
    """Compatibility wrapper for group-pool runtime bundle writing."""
    _sync_module_names()
    return _IMPL_WRITE_GROUP_POOL_BUNDLE(*args, **kwargs)


def main(argv: Any = None) -> None:
    """CLI entry point."""
    _sync_module_names()
    _IMPL_MAIN(argv)


_sync_module_names()

__all__ = [
    name
    for name in globals()
    if not (name.startswith("__") and name.endswith("__"))
    and name not in {"ModuleType", "Any", "_visible_symbols"}
]


if __name__ == "__main__":
    main()
