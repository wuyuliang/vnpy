"""Model pipeline orchestrator loader.

This thin module keeps the public import path stable while loading the
orchestrator implementation from an external source file.
"""
from __future__ import annotations

from pathlib import Path


_SOURCE_PATH = Path(__file__).with_name("pipeline_orchestrator_source.py.txt")


def _load_into_module_globals() -> None:
    code = _SOURCE_PATH.read_text(encoding="utf-8")
    exec(compile(code, str(_SOURCE_PATH), "exec"), globals())


_load_into_module_globals()

__all__ = list(globals().get("__all__", []))
