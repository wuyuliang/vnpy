from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path

    @property
    def feature_db(self) -> Path:
        return self.project_root / "data" / "tight_range_features.sqlite"

    @property
    def vnpy_sqlite_db(self) -> Path:
        return self.project_root / "data" / "database.db"


DEFAULT_DATE_COLUMNS = ["datetime", "date", "trade_date"]
DEFAULT_REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]
