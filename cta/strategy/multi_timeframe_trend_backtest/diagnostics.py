"""Observable counters for strategy gates that deliberately fail open."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class GateFailOpenDiagnostics:
    """Count gate evaluations and missing-input fallbacks for one replay."""

    _evaluations: Counter[str] = field(default_factory=Counter)
    _fail_opens: Counter[str] = field(default_factory=Counter)

    def observe(self, gate: str, *, fail_open: bool = False) -> None:
        name = str(gate).strip()
        if not name:
            raise ValueError("gate diagnostic name must not be empty")
        self._evaluations[name] += 1
        if fail_open:
            self._fail_opens[name] += 1

    def fail_open_counts(self) -> dict[str, int]:
        return dict(sorted(self._fail_opens.items()))

    def evaluation_counts(self) -> dict[str, int]:
        return dict(sorted(self._evaluations.items()))


def observe_gate(
    diagnostics: GateFailOpenDiagnostics | None,
    gate: str,
    *,
    fail_open: bool = False,
) -> None:
    if diagnostics is not None:
        diagnostics.observe(gate, fail_open=fail_open)


__all__ = ["GateFailOpenDiagnostics", "observe_gate"]
