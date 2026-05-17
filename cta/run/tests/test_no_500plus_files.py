from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_LINES = 500

# Temporary waivers for staged refactor waves in cta/docs/refactor_long_files_v2.md.
# Keep this list short and remove entries once the corresponding wave is done.
TEMPORARY_WHITELIST: dict[str, int] = {
}


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.py"):
        parts = set(path.parts)
        if "__pycache__" in parts or "fixtures" in parts:
            continue
        if ".claude" in parts:
            continue
        files.append(path)
    return files


def test_cta_python_file_line_limits() -> None:
    offenders: list[tuple[str, int, int]] = []
    for path in _iter_py_files():
        rel = str(path.relative_to(REPO_ROOT.parent))
        with path.open(encoding="utf-8") as fh:
            line_count = sum(1 for _ in fh)
        limit = TEMPORARY_WHITELIST.get(rel, MAX_LINES)
        if line_count > limit:
            offenders.append((rel, line_count, limit))
    assert offenders == [], f"Files exceed line limit: {offenders}"
