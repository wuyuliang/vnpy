"""§09-04 feature store helpers."""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class FeatureMeta:
    name: str
    version: str
    interval: str
    depends_on: list[str]
    code_ref: str


_REGISTRY: dict[str, dict] = {}


def _feature_root() -> Path:
    env = os.getenv("CTA_FEATURE_ROOT")
    if env:
        return Path(env)
    return Path("cta/data/feature")


def register_feature(meta: FeatureMeta) -> None:
    """Register feature metadata in-memory registry."""
    key = f"{meta.name}:{meta.version}:{meta.interval}"
    _REGISTRY[key] = asdict(meta)


def _load_feature_panel(symbol: str, interval: str) -> pd.DataFrame:
    root = _feature_root()
    candidates = [
        root / interval / f"{symbol}.parquet",
        root / interval / f"{symbol}.csv",
        root / interval / f"{symbol.upper()}.parquet",
        root / interval / f"{symbol.upper()}.csv",
        root / interval / f"{symbol.lower()}.parquet",
        root / interval / f"{symbol.lower()}.csv",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise FileNotFoundError(f"未找到 feature 文件: {symbol} {interval}")
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    if "datetime" not in df.columns:
        raise KeyError("feature 文件缺少 datetime 列")
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.sort_values("datetime").reset_index(drop=True)


def load_features_as_of(
    symbol: str,
    interval: str,
    ts: pd.Timestamp,
    cols: list[str] | None = None,
) -> pd.Series:
    """Return latest feature row with datetime <= ts."""
    df = _load_feature_panel(symbol, interval)
    t = pd.Timestamp(ts)
    sub = df[df["datetime"] <= t]
    if sub.empty:
        raise ValueError(f"{symbol}/{interval} 在 {t} 之前无特征数据")
    row = sub.iloc[-1]
    if cols:
        use = [c for c in cols if c in row.index]
        return row[use]
    return row


def verify_online_offline(
    symbol: str,
    interval: str,
    sample_size: int = 100,
) -> pd.DataFrame:
    """
    Compare offline rows to a simple online replay baseline.

    Current baseline reuses as-of readback at each sampled timestamp.
    """
    df = _load_feature_panel(symbol, interval)
    n = min(max(int(sample_size), 1), len(df))
    if n <= 0:
        return pd.DataFrame(columns=["col", "diff_abs_max"])
    idx = np.linspace(0, len(df) - 1, n, dtype=int)
    sample = df.iloc[idx].copy()
    num_cols = [c for c in df.columns if c != "datetime" and pd.api.types.is_numeric_dtype(df[c])]
    if not num_cols:
        return pd.DataFrame(columns=["col", "diff_abs_max"])

    diffs: dict[str, float] = {c: 0.0 for c in num_cols}
    for _, row in sample.iterrows():
        online = load_features_as_of(symbol, interval, pd.Timestamp(row["datetime"]), cols=num_cols)
        for c in num_cols:
            d = abs(float(row[c]) - float(online[c]))
            if d > diffs[c]:
                diffs[c] = d
    return pd.DataFrame({"col": list(diffs.keys()), "diff_abs_max": list(diffs.values())})

