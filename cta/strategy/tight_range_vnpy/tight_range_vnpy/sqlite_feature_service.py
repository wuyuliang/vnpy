from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .db import SQLiteStore
from .features import FeatureBuilder, FeatureConfig


@dataclass
class BuildResult:
    file_count: int
    row_count: int


class FeatureGenerationService:
    def __init__(self, db_path: Path | str, feature_cfg: Optional[FeatureConfig] = None) -> None:
        self.store = SQLiteStore(db_path)
        self.builder = FeatureBuilder(feature_cfg)

    def build_one_csv(self, csv_path: Path | str) -> pd.DataFrame:
        df = self.builder.build_from_csv(csv_path)
        self.store.upsert_dataframe("daily_features", df)
        return df

    def build_from_dir(self, csv_dir: Path | str, pattern: str = "*.csv") -> BuildResult:
        csv_dir = Path(csv_dir)
        files = sorted(csv_dir.glob(pattern))
        total_rows = 0
        for file in files:
            df = self.build_one_csv(file)
            total_rows += len(df)
            print(f"[OK] features saved | {file.name} | rows={len(df)}")
        return BuildResult(file_count=len(files), row_count=total_rows)

    def load_symbol_features(self, symbol: str, exchange: str) -> pd.DataFrame:
        return self.store.load_symbol_features(symbol.upper(), exchange.upper())
