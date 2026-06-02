"""按 (cluster, symbol, interval) 持久化模型分百分位点 manifest（子系统①）。

来源：train + valid split 的 ``trade_filter_prob``（不含 test/OOT，防止数据泄露）。
结构（JSON 顶层）：

```json
{
    "schema_version": 1,
    "generated_at": "2026-05-29T10:00:00",
    "run_tag": "cluster_both",
    "git_sha": "abc1234",
    "source_predictions_paths": ["..."],
    "exclude_splits": ["test", "oot"],
    "entries": [
        {
            "cluster": "metal", "symbol": "CU0", "interval": "day",
            "p50": 0.51, "p60": 0.55, "p70": 0.60, "p80": 0.66, "p90": 0.74, "p95": 0.81,
            "sample_count": 1200
        },
        ...
    ]
}
```

lookup 优先级：
1. 精确 (cluster, symbol, interval)
2. (cluster, "*", interval) cluster-wide fallback
3. 返回 None（caller fail-open）

注意：本模块只读 / 写 manifest，不实现 threshold 决策；那部分在 threshold/quantile_threshold.py。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from cta.risk.base import normalize_cluster, normalize_interval, normalize_symbol

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
WILDCARD_SYMBOL = "*"

QUANTILE_FIELDS: tuple[str, ...] = ("p50", "p60", "p70", "p80", "p90", "p95")
QUANTILE_VALUES: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95)


@dataclass(frozen=True)
class ScoreQuantileEntry:
    """单条 (cluster, symbol, interval) 分位点。"""

    cluster: str
    symbol: str
    interval: str
    p50: float
    p60: float
    p70: float
    p80: float
    p90: float
    p95: float
    sample_count: int = 0

    def quantile(self, field: str) -> float:
        return float(getattr(self, field))

    def to_dict(self) -> dict:
        return asdict(self)


class ScoreQuantileManifest:
    """只读 manifest：from_json 加载 + lookup 查询。"""

    def __init__(
        self,
        entries: Iterable[ScoreQuantileEntry],
        *,
        meta: dict | None = None,
    ) -> None:
        self._entries: dict[tuple[str, str, str], ScoreQuantileEntry] = {}
        for e in entries:
            key = (normalize_cluster(e.cluster), normalize_symbol(e.symbol), normalize_interval(e.interval))
            self._entries[key] = e
        self.meta = dict(meta or {})

    # ── load / save ─────────────────────────────────────────────────

    @classmethod
    def from_json(cls, path: str | Path) -> "ScoreQuantileManifest":
        """从 JSON 加载。文件缺失 / parse 失败 → 返回空 manifest（fail-open）。"""
        p = Path(path)
        if not p.exists():
            logger.info("score_quantile_manifest not found: %s — empty manifest", p)
            return cls([])
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("score_quantile_manifest parse failed at %s: %s — empty manifest", p, exc)
            return cls([])
        if not isinstance(payload, dict):
            logger.warning("score_quantile_manifest top-level not dict at %s — empty manifest", p)
            return cls([])
        entries_raw = payload.get("entries") or []
        entries: list[ScoreQuantileEntry] = []
        for row in entries_raw:
            if not isinstance(row, dict):
                continue
            try:
                entries.append(
                    ScoreQuantileEntry(
                        cluster=str(row.get("cluster", "")),
                        symbol=str(row.get("symbol", WILDCARD_SYMBOL)),
                        interval=str(row.get("interval", "")),
                        p50=float(row.get("p50", float("nan"))),
                        p60=float(row.get("p60", float("nan"))),
                        p70=float(row.get("p70", float("nan"))),
                        p80=float(row.get("p80", float("nan"))),
                        p90=float(row.get("p90", float("nan"))),
                        p95=float(row.get("p95", float("nan"))),
                        sample_count=int(row.get("sample_count", 0) or 0),
                    )
                )
            except (TypeError, ValueError) as exc:
                logger.debug("score_quantile_manifest skip bad row %r: %s", row, exc)
                continue
        meta = {k: v for k, v in payload.items() if k != "entries"}
        return cls(entries, meta=meta)

    def to_json(self, path: str | Path) -> Path:
        """写盘并返回路径；目录会自动创建。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            **self.meta,
            "entries": [e.to_dict() for e in self._entries.values()],
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    # ── query ───────────────────────────────────────────────────────

    def lookup(
        self,
        cluster: str,
        symbol: str,
        interval: str,
    ) -> ScoreQuantileEntry | None:
        """优先精确 (cluster, symbol, interval) → cluster-wide → None。"""
        cl = normalize_cluster(cluster)
        sym = normalize_symbol(symbol)
        itv = normalize_interval(interval)
        if not cl or not itv:
            return None
        exact = self._entries.get((cl, sym, itv))
        if exact is not None:
            return exact
        wide = self._entries.get((cl, WILDCARD_SYMBOL, itv))
        return wide

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries.values())


# ── build manifest from training predictions ────────────────────────


def build_from_predictions(
    predictions_paths: Iterable[str | Path],
    *,
    out_path: str | Path | None = None,
    exclude_splits: tuple[str, ...] = ("test", "oot"),
    prob_column: str = "trade_filter_prob",
    cluster_column: str = "cluster_name",
    symbol_column: str = "symbol",
    interval_column: str = "interval",
    split_column: str = "split",
    meta: dict | None = None,
    include_cluster_wide: bool = True,
) -> ScoreQuantileManifest:
    """从训练 predictions.csv 列表构建 manifest。

    Parameters
    ----------
    predictions_paths
        逐文件读取的 predictions.csv 路径列表。
    exclude_splits
        哪些 split 不参与统计（默认排 test / oot 防泄露）。若 csv 没有 split 列，全部使用。
    include_cluster_wide
        是否同步生成 (cluster, "*", interval) 聚合条目（推荐 True，给新 symbol fallback）。

    Returns
    -------
    构造好的 ScoreQuantileManifest（若 out_path 非 None 同时写盘）。
    """
    frames: list[pd.DataFrame] = []
    for path in predictions_paths:
        p = Path(path)
        if not p.exists():
            logger.warning("build_from_predictions: %s not found, skip", p)
            continue
        try:
            df = pd.read_csv(p, encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            logger.warning("build_from_predictions: failed to read %s: %s", p, exc)
            continue
        frames.append(df)
    if not frames:
        logger.warning("build_from_predictions: no usable predictions; empty manifest")
        manifest = ScoreQuantileManifest([], meta=meta or {})
        if out_path is not None:
            manifest.to_json(out_path)
        return manifest
    combined = pd.concat(frames, ignore_index=True, sort=False)
    if split_column in combined.columns and exclude_splits:
        keep = ~combined[split_column].astype(str).str.lower().isin(
            [str(s).lower() for s in exclude_splits]
        )
        combined = combined.loc[keep].copy()
    if prob_column not in combined.columns:
        logger.warning(
            "build_from_predictions: column %r missing; empty manifest", prob_column
        )
        manifest = ScoreQuantileManifest([], meta=meta or {})
        if out_path is not None:
            manifest.to_json(out_path)
        return manifest

    # 兼容老 schema：cluster 列名可能是 'cluster' 或 'cluster_name'
    if cluster_column not in combined.columns and "cluster" in combined.columns:
        combined[cluster_column] = combined["cluster"]
    if cluster_column not in combined.columns:
        # 缺 cluster → 空 manifest
        logger.warning(
            "build_from_predictions: cluster column %r missing; empty manifest", cluster_column
        )
        manifest = ScoreQuantileManifest([], meta=meta or {})
        if out_path is not None:
            manifest.to_json(out_path)
        return manifest

    combined[prob_column] = pd.to_numeric(combined[prob_column], errors="coerce")
    combined = combined.dropna(subset=[prob_column])
    entries: list[ScoreQuantileEntry] = []

    def _calc_entry(sub: pd.DataFrame, cluster: str, symbol: str, interval: str) -> ScoreQuantileEntry | None:
        if sub.empty:
            return None
        probs = sub[prob_column].astype(float).to_numpy()
        if len(probs) < 1:
            return None
        try:
            qs = np.quantile(probs, QUANTILE_VALUES)
        except Exception:  # noqa: BLE001
            return None
        return ScoreQuantileEntry(
            cluster=normalize_cluster(cluster),
            symbol=normalize_symbol(symbol),
            interval=normalize_interval(interval),
            p50=float(qs[0]), p60=float(qs[1]), p70=float(qs[2]),
            p80=float(qs[3]), p90=float(qs[4]), p95=float(qs[5]),
            sample_count=int(len(probs)),
        )

    group_cols = [cluster_column, symbol_column, interval_column]
    missing_cols = [c for c in group_cols if c not in combined.columns]
    if missing_cols:
        logger.warning(
            "build_from_predictions: missing group cols %s; empty manifest", missing_cols
        )
        manifest = ScoreQuantileManifest([], meta=meta or {})
        if out_path is not None:
            manifest.to_json(out_path)
        return manifest

    grouped = combined.groupby(group_cols, dropna=False)
    for (cluster, symbol, interval), sub in grouped:
        entry = _calc_entry(sub, str(cluster), str(symbol), str(interval))
        if entry is not None:
            entries.append(entry)

    if include_cluster_wide:
        for (cluster, interval), sub in combined.groupby([cluster_column, interval_column], dropna=False):
            entry = _calc_entry(sub, str(cluster), WILDCARD_SYMBOL, str(interval))
            if entry is not None:
                entries.append(entry)

    meta_full = dict(meta or {})
    meta_full.setdefault("source_predictions_paths", [str(p) for p in predictions_paths])
    meta_full.setdefault("exclude_splits", list(exclude_splits))
    manifest = ScoreQuantileManifest(entries, meta=meta_full)
    if out_path is not None:
        path = manifest.to_json(out_path)
        # latest symlink（best-effort，Windows 等不支持的环境忽略）
        try:
            latest = Path(path).parent / "score_quantile_manifest_latest.json"
            if latest.exists() or latest.is_symlink():
                latest.unlink()
            os.symlink(path.name, latest)
        except Exception as exc:  # noqa: BLE001
            logger.debug("symlink to latest failed: %s", exc)
    return manifest


__all__ = [
    "QUANTILE_FIELDS",
    "QUANTILE_VALUES",
    "SCHEMA_VERSION",
    "ScoreQuantileEntry",
    "ScoreQuantileManifest",
    "WILDCARD_SYMBOL",
    "build_from_predictions",
]
