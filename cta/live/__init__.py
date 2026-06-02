"""cta.live package exports (lazy-loaded)."""
from __future__ import annotations

from importlib import import_module

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "LiveCtpSetting": ("cta.live.live_runner", "LiveCtpSetting"),
    "LiveRunConfig": ("cta.live.live_runner", "LiveRunConfig"),
    "run_live": ("cta.live.live_runner", "run_live"),
    "serve_live": ("cta.live.live_runner", "serve_live"),
    "LiveModelRegistry": ("cta.live.model_registry", "LiveModelRegistry"),
    "generate_today_candidates": ("cta.live.signal_generator", "generate_today_candidates"),
    "write_periodic_reports": ("cta.live.reporting.periodic_report", "write_periodic_reports"),
    "write_review_report": ("cta.live.reporting.review_report", "write_review_report"),
}


def __getattr__(name: str):
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = sorted(_LAZY_ATTRS)
