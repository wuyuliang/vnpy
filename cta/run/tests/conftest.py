"""Pytest bootstrap for run/* tests.

确保 vn.py logger 落到仓库可写目录，避免 CI / sandbox 环境下
默认 `~/.vntrader/log` 权限不足导致 import 阶段失败。
"""
from __future__ import annotations

from pathlib import Path


def pytest_configure() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    vntrader_dir = repo_root / ".vntrader"
    (vntrader_dir / "log").mkdir(parents=True, exist_ok=True)
