"""Training feature builder.

拼接两类特征：
1. 候选机会相关特征（来自 baseline 候选样本，通常以 ``feature_`` 前缀命名）
2. `cta/data/feature/<interval>/<symbol>/*.parquet` 的通用特征（统一加 ``generic_`` 前缀）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.config.skill_tight_range_breakout_config import CTA_ROOT
from cta.strategy.skill_tight_range_backtest import normalize_interval

logger = logging.getLogger(__name__)

FEATURE_ROOT: Path = CTA_ROOT / "data" / "feature"

# 通用特征候选池：若调用方不指定 generic_columns，则从这批里选择存在列。
DEFAULT_GENERIC_COLUMNS: tuple[str, ...] = (
    "sma_20",
    "ema_20",
    "macd_dif",
    "macd_dea",
    "rsi_14",
    "atr_14",
    "bb_width",
    "stoch_k",
    "stoch_d",
    "mfi_14",
    "trend_score",
    "compression_score",
    "breakout_mode_score",
    "setup_quality_score",
    "breakout_quality_score",
    "context_score",
    "regime_label",
    "regime_conf",
)


def _iter_feature_files(
    symbol: str,
    interval: str,
    start_date: str,
    end_date: str,
    feature_root: Path,
) -> list[Path]:
    interval_norm = normalize_interval(interval)
    symbol_dir = feature_root / interval_norm / str(symbol).upper()
    if not symbol_dir.exists():
        raise FileNotFoundError(f"feature symbol dir not found: {symbol_dir}")

    files = sorted(symbol_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no parquet files in {symbol_dir}")

    start_day = pd.Timestamp(start_date).normalize()
    end_day = pd.Timestamp(end_date).normalize()

    picked: list[Path] = []
    parse_failures: list[str] = []
    for f in files:
        try:
            d = pd.Timestamp(f.stem).normalize()
        except Exception as exc:
            parse_failures.append(f.name)
            logger.warning("failed to parse feature filename date %s: %s", f.name, exc)
            continue
        if start_day <= d <= end_day:
            picked.append(f)

    # B9 fix: 大量文件名解析失败说明命名规范变了，不能再静默吞掉，否则 pipeline 会
    # 看上去成功但实际丢光所有 generic 特征。阈值取 50%，足以容忍少量异常文件。
    total_files = len(files)
    if total_files > 0 and (len(parse_failures) / total_files) >= 0.5:
        raise ValueError(
            f"too many feature files have non-date stems: "
            f"{len(parse_failures)}/{total_files} skipped under {symbol_dir}; "
            f"sample={parse_failures[:5]}"
        )

    if not picked:
        raise FileNotFoundError(
            f"no feature files in range [{start_day.date()}, {end_day.date()}] under {symbol_dir}"
        )
    return picked


def load_generic_feature_frame(
    symbol: str,
    interval: str,
    start_date: str,
    end_date: str,
    feature_root: Path = FEATURE_ROOT,
) -> pd.DataFrame:
    """Load generic feature parquet files and concat into one frame."""
    files = _iter_feature_files(
        symbol=symbol,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        feature_root=feature_root,
    )
    parts: list[pd.DataFrame] = []
    for f in files:
        try:
            parts.append(pd.read_parquet(f))
        except Exception as exc:
            logger.warning("failed reading %s: %s", f, exc)
    if not parts:
        raise ValueError("generic feature load produced empty parts")

    out = pd.concat(parts, axis=0, ignore_index=True)
    if "datetime" not in out.columns:
        raise KeyError("generic feature frame missing datetime column")
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates("datetime")
    out = out.reset_index(drop=True)
    return out


def merge_candidate_and_generic_features(
    candidate_df: pd.DataFrame,
    generic_df: pd.DataFrame,
    generic_columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Merge candidate opportunities with selected generic feature columns."""
    if "datetime" not in candidate_df.columns:
        raise KeyError("candidate_df missing datetime")
    if "datetime" not in generic_df.columns:
        raise KeyError("generic_df missing datetime")

    out = candidate_df.copy()
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).copy()
    out = out.sort_values("datetime").reset_index(drop=True)

    cols_pool = tuple(generic_columns) if generic_columns is not None else DEFAULT_GENERIC_COLUMNS
    cols = [c for c in cols_pool if c in generic_df.columns]
    if not cols:
        logger.warning("no generic columns matched, returning candidate table only")
        return out.sort_values("datetime").reset_index(drop=True)

    gf = generic_df[["datetime", *cols]].copy()
    gf["datetime"] = pd.to_datetime(gf["datetime"], errors="coerce")
    gf = gf.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    rename_map = {c: f"generic_{c}" for c in cols}
    gf = gf.rename(columns=rename_map)

    interval_raw = str(out.get("interval", pd.Series(["day"])).iloc[0]).strip().lower() if not out.empty else "day"
    interval_norm = normalize_interval(interval_raw)
    tolerance_map: dict[str, pd.Timedelta] = {
        "day": pd.Timedelta(days=1),
        "minute60": pd.Timedelta(minutes=60),
        "minute30": pd.Timedelta(minutes=30),
        "minute15": pd.Timedelta(minutes=15),
        "minute5": pd.Timedelta(minutes=5),
        "minute": pd.Timedelta(minutes=1),
    }
    tolerance = tolerance_map.get(interval_norm, pd.Timedelta(minutes=1))

    merged = pd.merge_asof(
        out,
        gf,
        on="datetime",
        direction="backward",
        tolerance=tolerance,
    )
    merged = merged.sort_values("datetime").reset_index(drop=True)
    return merged


def build_training_feature_table(
    candidate_df: pd.DataFrame,
    symbol: str,
    interval: str,
    feature_root: Path = FEATURE_ROOT,
    generic_columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Build training feature table by joining candidate + generic features."""
    if candidate_df.empty:
        return candidate_df.copy()
    if "datetime" not in candidate_df.columns:
        raise KeyError("candidate_df missing datetime")

    dt = pd.to_datetime(candidate_df["datetime"], errors="coerce").dropna()
    if dt.empty:
        raise ValueError("candidate_df datetime all invalid")
    # B3 fix: 截到天会丢掉跨日 lookback；asof 需要前一天的 generic 特征兜底，
    # 同时多保留一天的右边界，避免最后一根 bar 因 daylight/夜盘衔接错过。
    start_date = str((dt.min() - pd.Timedelta(days=1)).date())
    end_date = str((dt.max() + pd.Timedelta(days=1)).date())

    try:
        generic_df = load_generic_feature_frame(
            symbol=symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            feature_root=feature_root,
        )
    except Exception as exc:
        logger.warning("load_generic_feature_frame failed, fallback to candidate only: %s", exc)
        out = candidate_df.copy()
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
        return out.sort_values("datetime").reset_index(drop=True)

    merged = merge_candidate_and_generic_features(
        candidate_df=candidate_df,
        generic_df=generic_df,
        generic_columns=generic_columns,
    )
    return merged


__all__ = [
    "FEATURE_ROOT",
    "DEFAULT_GENERIC_COLUMNS",
    "load_generic_feature_frame",
    "merge_candidate_and_generic_features",
    "build_training_feature_table",
]
