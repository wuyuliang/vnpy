"""§10-05 strategy iteration loop helpers."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class ReleasePlan:
    strategy: str
    from_version: str
    to_version: str
    rollout_stages: list[dict]
    rollback_cmd: str
    observation_days: int = 10


def plan_release(
    strategy: str,
    current: str,
    next_ver: str,
    changes: list[str],
) -> ReleasePlan:
    """Build a staged rollout/rollback plan."""
    stages = [
        {"stage": "paper_trade", "duration_days": 5, "condition": "no critical alerts"},
        {"stage": "canary", "scope": "1 symbol / 1/3 risk", "duration_days": 5, "condition": "live_bt_diff < 20%"},
        {"stage": "full", "scope": "all symbols", "condition": "canary stable"},
    ]
    rollback = f"run_strategy --name {strategy} --version {current}"
    _ = changes
    return ReleasePlan(
        strategy=strategy,
        from_version=current,
        to_version=next_ver,
        rollout_stages=stages,
        rollback_cmd=rollback,
        observation_days=10,
    )


def append_change_log(
    date: pd.Timestamp,
    branch: str,
    task: str,
    files: list[str],
    conclusion: str,
) -> None:
    """Append one structured entry to change log."""
    path = Path(os.getenv("CTA_CHANGE_LOG_PATH", "cta/report/change_log.md"))
    path.parent.mkdir(parents=True, exist_ok=True)
    d = pd.Timestamp(date).strftime("%Y-%m-%d")
    lines = [
        f"## {d}",
        f"- 分支名: {branch}",
        f"- 任务名称: {task}",
        f"- 修改文件: {', '.join(files)}",
        f"- 主要结论: {conclusion}",
        "",
    ]
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))

