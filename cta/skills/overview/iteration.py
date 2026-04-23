"""§05 迭代流程 / Iteration Workflow —— 想法目录的轻量追踪.

对应 cta/cta_skills/00_overview_methodology/05_iteration_workflow.md §6。

目录约定
--------
cta/report/ideas/{YYYYMMDD}_{short_name}/
├── hypothesis.md           # 阶段 1：假设
├── micro_experiment.ipynb  # 阶段 2：最小实验（可选）
├── decision.md             # 阶段 3+：结论
└── meta.yaml               # 本模块维护的状态元数据

meta.yaml schema
----------------
id: "20260420_tight_range_filter"
name: "Tight Range Filter"
stage: "hypothesize" | "micro" | "backtest" | "paper" | "live" | "dropped"
created_at: "2026-04-20"
owner: "wu"
hypothesis_doc: "hypothesis.md"
decision: null | "promoted" | "dropped"
next_action: "跑 RB0 minute5 IC"
notes: "..."
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml

from cta.skills import PROJECT_ROOT

logger = logging.getLogger(__name__)

IDEAS_DIR: Path = PROJECT_ROOT / "cta" / "report" / "ideas"

Stage = Literal["hypothesize", "micro", "backtest", "paper", "live", "dropped"]
VALID_STAGES: tuple[Stage, ...] = (
    "hypothesize", "micro", "backtest", "paper", "live", "dropped",
)

_ID_RE = re.compile(r"^\d{8}_[a-z0-9_]+$")
_SLUG_CLEAN = re.compile(r"[^a-z0-9_]+")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class IdeaRecord:
    id: str                                      # 20260420_tight_range_filter
    name: str
    stage: Stage
    created_at: str                              # 'YYYY-MM-DD'
    owner: str = "unknown"
    hypothesis_doc: str = "hypothesis.md"
    decision: Optional[str] = None               # 'promoted' | 'dropped' | None
    next_action: str = ""
    notes: str = ""
    folder: str = ""                             # 相对 ideas 根的文件夹名

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValueError(
                f"id 应符合 'YYYYMMDD_slug' 格式（小写+下划线），got {self.id!r}"
            )
        if self.stage not in VALID_STAGES:
            raise ValueError(
                f"stage 非法: {self.stage!r}，合法值: {VALID_STAGES}"
            )
        try:
            datetime.strptime(self.created_at, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"created_at 应为 YYYY-MM-DD: {e}") from e

    def folder_path(self, root: Path = IDEAS_DIR) -> Path:
        return root / (self.folder or self.id)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 文件操作
# ---------------------------------------------------------------------------
def _slugify(name: str) -> str:
    """'Tight Range Filter' -> 'tight_range_filter'"""
    s = name.strip().lower().replace(" ", "_").replace("-", "_")
    s = _SLUG_CLEAN.sub("", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        raise ValueError(f"name={name!r} slugify 后为空")
    return s


def _meta_path(folder: Path) -> Path:
    return folder / "meta.yaml"


def _write_meta(rec: IdeaRecord, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = _meta_path(folder)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            rec.to_dict(),
            fh,
            allow_unicode=True,
            sort_keys=False,
        )
    return path


def _write_hypothesis_template(folder: Path, rec: IdeaRecord) -> Path:
    path = folder / "hypothesis.md"
    if path.exists():
        return path
    content = (
        f"# {rec.name} / Hypothesis\n\n"
        f"> id: `{rec.id}` · owner: {rec.owner} · created: {rec.created_at}\n\n"
        "## 1. 假设\n"
        "若 A 则 B。（写清楚因果方向 + 可观察量）\n\n"
        "## 2. 预期效应大小\n"
        "- 年化提升：? %\n"
        "- Sharpe 提升：?\n\n"
        "## 3. 关键信号 / 指标\n"
        "- 信号定义：\n"
        "- 关键阈值：\n\n"
        "## 4. 已知反例 / 可能失效场景\n"
        "- \n\n"
        "## 5. Micro-experiment 计划\n"
        "- 品种：\n"
        "- 周期：\n"
        "- 评估指标：IC / 分组收益 / ...\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def record_idea(
    name: str,
    owner: str = "unknown",
    stage: Stage = "hypothesize",
    root: Path = IDEAS_DIR,
    created_at: Optional[str] = None,
    next_action: str = "",
    notes: str = "",
    write_template: bool = True,
) -> IdeaRecord:
    """
    在 `cta/report/ideas/` 下新建一个想法目录并写入 meta.yaml + 模板。
    若目录已存在，原样返回已有记录（幂等）。
    """
    d = created_at or date.today().isoformat()
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"created_at 应为 YYYY-MM-DD: {e}") from e
    id_slug = f"{d.replace('-', '')}_{_slugify(name)}"

    folder = root / id_slug
    meta = _meta_path(folder)
    if meta.exists():
        # 幂等：若已存在则读出来返回
        try:
            loaded = _load_meta(meta)
            logger.info("idea %s 已存在，返回已有 meta.yaml", id_slug)
            return loaded
        except Exception as e:
            logger.warning("meta.yaml 读取失败（%s），将覆盖重建", e)

    rec = IdeaRecord(
        id=id_slug,
        name=name,
        stage=stage,
        created_at=d,
        owner=owner,
        next_action=next_action,
        notes=notes,
        folder=id_slug,
    )
    _write_meta(rec, folder)
    if write_template:
        _write_hypothesis_template(folder, rec)
    logger.info("new idea: %s -> %s", id_slug, folder)
    return rec


def _load_meta(path: Path) -> IdeaRecord:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # 兼容旧版没有 folder 字段
    data.setdefault("folder", path.parent.name)
    return IdeaRecord(**data)


def list_open_ideas(
    root: Path = IDEAS_DIR,
    include_dropped: bool = False,
    include_live: bool = True,
) -> List[IdeaRecord]:
    """
    扫描 ideas 根目录返回所有 meta.yaml。
    默认排除 stage == 'dropped'，保留 live（若不想看 live，include_live=False）。
    """
    if not root.exists():
        return []
    out: List[IdeaRecord] = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        meta = _meta_path(sub)
        if not meta.exists():
            continue
        try:
            rec = _load_meta(meta)
        except Exception as e:
            logger.warning("跳过 %s（meta.yaml 解析失败: %s）", sub.name, e)
            continue
        if rec.stage == "dropped" and not include_dropped:
            continue
        if rec.stage == "live" and not include_live:
            continue
        out.append(rec)
    return out


def advance_stage(idea_id: str, new_stage: Stage,
                  root: Path = IDEAS_DIR,
                  decision: Optional[str] = None) -> IdeaRecord:
    """推进某 idea 到新阶段，原子写回 meta.yaml."""
    if new_stage not in VALID_STAGES:
        raise ValueError(f"new_stage 非法: {new_stage!r}")
    folder = root / idea_id
    meta = _meta_path(folder)
    if not meta.exists():
        raise FileNotFoundError(f"idea 不存在: {idea_id}（meta.yaml: {meta}）")
    rec = _load_meta(meta)
    rec.stage = new_stage
    if decision is not None:
        rec.decision = decision
    _write_meta(rec, folder)
    return rec
