"""Compatibility shim for model pipeline modules."""
from __future__ import annotations

from cta.model.orchestration import pipeline_orchestrator as _impl

for _name, _value in vars(_impl).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value

__all__ = list(getattr(_impl, "__all__", []))


if __name__ == "__main__":
    main()

