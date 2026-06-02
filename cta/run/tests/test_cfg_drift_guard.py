"""Config drift guard (P0Δ-4): disallow manifest hardcoding in sim/live runtime code."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _python_files_under(rel_path: str) -> list[Path]:
    base = ROOT / rel_path
    if not base.exists():
        return []
    out: list[Path] = []
    for path in base.rglob("*.py"):
        if "tests" in path.parts:
            continue
        out.append(path)
    return sorted(out)


class TestCfgDriftGuard(unittest.TestCase):
    def test_sim_live_runtime_does_not_hardcode_manifest_literals(self) -> None:
        banned_literals = (
            "0.0003",
            "0.00015",
            "STRICT_BOND_",
        )
        offenders: list[str] = []
        for path in _python_files_under("cta/sim") + _python_files_under("cta/live"):
            text = path.read_text(encoding="utf-8")
            for token in banned_literals:
                if token in text:
                    offenders.append(f"{path.relative_to(ROOT)} -> {token}")
        self.assertEqual(
            offenders,
            [],
            msg="Found hardcoded manifest literals in sim/live runtime code:\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()

