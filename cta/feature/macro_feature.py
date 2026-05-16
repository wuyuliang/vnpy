"""Macro reference feature builder from index daily bars."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from cta.config.skill_tight_range_breakout_config import CTA_ROOT

logger = logging.getLogger(__name__)

_INDEX_ALIAS: dict[str, str] = {
    "000001.SH": "sse",
    "000852.SH": "csi1000",
    "000300.SH": "csi300",
}


class MacroFeatureBuilder:
    """Build macro daily feature table shared by all tradable symbols."""

    def __init__(
        self,
        index_root: Path = CTA_ROOT / "data" / "origin_index" / "day",
        index_reference_csv: Path = CTA_ROOT / "feature" / "index_reference_symbols.csv",
    ) -> None:
        self.index_root = Path(index_root)
        self.index_reference_csv = Path(index_reference_csv)
        self._file_safe_name_map = self._load_file_safe_name_map()

    def _load_file_safe_name_map(self) -> dict[str, str]:
        path = Path(self.index_reference_csv)
        if not path.exists():
            return {}
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to read index reference csv %s: %s", path, exc)
            return {}
        if "ts_code" not in df.columns:
            return {}
        out: dict[str, str] = {}
        for _, row in df.iterrows():
            ts_code = str(row.get("ts_code", "")).strip().upper()
            if not ts_code:
                continue
            safe_name = str(row.get("file_safe_name", "")).strip().upper()
            if not safe_name:
                safe_name = ts_code.replace(".", "_")
            out[ts_code] = safe_name
        return out

    def _safe_name(self, ts_code: str) -> str:
        code = str(ts_code).strip().upper()
        return self._file_safe_name_map.get(code, code.replace(".", "_"))

    def _load_index_close(self, ts_code: str) -> pd.DataFrame:
        path = self.index_root / f"{self._safe_name(ts_code)}.csv"
        if not path.exists():
            raise FileNotFoundError(f"index csv not found: {path}")
        df = pd.read_csv(path, encoding="utf-8-sig")
        if "datetime" in df.columns:
            dt = pd.to_datetime(df["datetime"], errors="coerce")
        elif "trade_date" in df.columns:
            dt = pd.to_datetime(df["trade_date"], errors="coerce")
        else:
            raise KeyError(f"index csv missing datetime/trade_date: {path}")
        close = pd.to_numeric(df.get("close"), errors="coerce")
        out = pd.DataFrame({"trade_date": dt.dt.normalize(), "close": close})
        out = out.dropna(subset=["trade_date", "close"]).sort_values("trade_date").reset_index(drop=True)
        return out

    def build(self, symbols: list[str] | None = None) -> pd.DataFrame:
        targets = symbols or list(_INDEX_ALIAS.keys())
        frames: list[pd.DataFrame] = []
        for ts_code in targets:
            alias = _INDEX_ALIAS.get(str(ts_code).upper())
            if alias is None:
                continue
            base = self._load_index_close(str(ts_code))
            ret1 = base["close"].pct_change(1)
            feat = pd.DataFrame(
                {
                    "trade_date": base["trade_date"],
                    f"macro_{alias}_close": base["close"],
                    f"macro_{alias}_ret_1d": ret1,
                    f"macro_{alias}_ret_5d": base["close"].pct_change(5),
                    f"macro_{alias}_ret_20d": base["close"].pct_change(20),
                    f"macro_{alias}_vol_5d": ret1.rolling(5, min_periods=2).std(),
                    f"macro_{alias}_vol_20d": ret1.rolling(20, min_periods=5).std(),
                }
            )
            frames.append(feat)

        if not frames:
            return pd.DataFrame()

        merged = frames[0]
        for extra in frames[1:]:
            merged = merged.merge(extra, on="trade_date", how="outer")
        merged = merged.sort_values("trade_date").reset_index(drop=True)

        if {"macro_csi300_ret_5d", "macro_csi1000_ret_5d"}.issubset(merged.columns):
            merged["macro_spread_csi300_csi1000_ret_5d"] = (
                merged["macro_csi300_ret_5d"] - merged["macro_csi1000_ret_5d"]
            )
        else:
            merged["macro_spread_csi300_csi1000_ret_5d"] = pd.NA

        merged = merged.set_index("trade_date")
        merged.index.name = "trade_date"
        return merged

    def save(
        self,
        df: pd.DataFrame,
        out_path: Path = CTA_ROOT / "data" / "feature" / "macro" / "macro_daily.parquet",
    ) -> Path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        out = df.copy()
        if out.index.name != "trade_date":
            if "trade_date" in out.columns:
                out = out.set_index("trade_date")
            else:
                out.index = pd.to_datetime(out.index, errors="coerce")
                out.index.name = "trade_date"
        out.to_parquet(path)
        return path

    @classmethod
    def load(cls, path: Path) -> pd.DataFrame:
        df = pd.read_parquet(path)
        if "trade_date" in df.columns:
            df = df.set_index("trade_date")
        df.index = pd.to_datetime(df.index, errors="coerce")
        df.index.name = "trade_date"
        return df.sort_index()


__all__ = [
    "MacroFeatureBuilder",
]
