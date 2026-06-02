"""Online model registry wrapper for sim/live signal generation."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from cta.live.hot_reloadable_registry import HotReloadableRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelArtifactInfo:
    model_dir: Path
    newest_mtime: pd.Timestamp | None
    age_days: float | None


class LiveModelRegistry:
    """Thin wrapper around HotReloadableRegistry + cluster model routing."""

    def __init__(self, *, hot_registry: Any) -> None:
        self._hot = hot_registry

    @classmethod
    def from_registry_path(cls, registry_path: Path | str, *, strict: bool = True) -> "LiveModelRegistry":
        hot = HotReloadableRegistry.from_path(registry_path, strict=strict)
        return cls(hot_registry=hot)

    @property
    def version(self) -> int:
        return int(getattr(self._hot, "version", 0))

    @property
    def current(self) -> Any:
        return self._hot.current

    def reload(self) -> bool:
        ok, _report = self._hot.reload()
        return bool(ok)

    def predict(self, df: pd.DataFrame, *, model_kind: str) -> pd.DataFrame:
        """Delegate prediction to ClusterModelRegistry.predict_proba."""
        return self.current.predict_proba(df, model_kind=model_kind)

    def resolve_model_dir(self, symbol: str, interval: str) -> Path | None:
        d = self.current.resolve_model_dir(symbol, interval)
        if d is None:
            return None
        return Path(d)

    def _iter_model_dirs(self) -> list[Path]:
        entries = getattr(self.current, "entries", {}) or {}
        out: list[Path] = []
        for entry in entries.values():
            d = Path(getattr(entry, "model_dir", ""))
            if d and d.exists():
                out.append(d)
        return sorted(set(out))

    @staticmethod
    def _latest_mtime_under(path: Path) -> pd.Timestamp | None:
        latest_ts: float | None = None
        for fp in path.rglob("*"):
            if not fp.is_file():
                continue
            try:
                mt = float(fp.stat().st_mtime)
            except OSError:
                continue
            if latest_ts is None or mt > latest_ts:
                latest_ts = mt
        if latest_ts is None:
            return None
        return pd.Timestamp(latest_ts, unit="s")

    def collect_model_artifacts(self, *, as_of: pd.Timestamp | None = None) -> list[ModelArtifactInfo]:
        ts = pd.Timestamp(as_of if as_of is not None else pd.Timestamp.now())
        out: list[ModelArtifactInfo] = []
        for model_dir in self._iter_model_dirs():
            newest = self._latest_mtime_under(model_dir)
            age_days: float | None = None
            if newest is not None:
                age_days = float((ts - newest).total_seconds() / 86400.0)
            out.append(ModelArtifactInfo(model_dir=model_dir, newest_mtime=newest, age_days=age_days))
        return out

    def max_model_age_days(self, *, as_of: pd.Timestamp | None = None) -> float:
        ages = [a.age_days for a in self.collect_model_artifacts(as_of=as_of) if a.age_days is not None]
        if not ages:
            return float("nan")
        return float(max(ages))


__all__ = ["LiveModelRegistry", "ModelArtifactInfo"]
