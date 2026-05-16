"""Offline leakage audit for generic feature parquets.

把 ``cta/data/feature/<interval>/<symbol>/*.parquet`` 里的每个数值列与"未来 1
bar 收益"以及"horizon edge"做 |IC|，过高的列大概率携带 lookahead 信息（命名上
不含 leakage filter 关注的关键词，因此 model_pipeline 的运行时正则拦不住）。

用法
----

```
python -m cta.model.tools.leakage_audit \
    --interval day \
    --symbols-ranking-path cta/feature/symbols_research_ranking.csv \
    --top-n 18 \
    --horizon 5 \
    --abs-ic-threshold 0.3 \
    --out cta/report/leakage_audit/<run_tag>.csv
```

输出
----

- ``<out>``: 全量 (symbol, column, ic_fwd1, ic_horizon_edge, ic_max) 行
- ``<out>.suspect.csv``: 仅 |ic_max| >= ``--abs-ic-threshold`` 的可疑列

注意
----

- 该脚本只查"命名未触发现有 leakage filter 的"特征，不替代 walk-forward 评估。
- IC 用 Spearman 与 Pearson 取 max，更鲁棒地识别非线性 lookahead。
- 同一个 symbol 内所有 parquet 按 datetime 升序拼接；horizon 用 close 计算
  ``fwd_return = close.shift(-h) / close - 1``。
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from cta.config.skill_tight_range_breakout_config import CTA_ROOT
from cta.strategy.skill_tight_range_backtest import normalize_interval

logger = logging.getLogger(__name__)

FEATURE_ROOT: Path = CTA_ROOT / "data" / "feature"
SYMBOLS_RANKING_PATH: Path = CTA_ROOT / "feature" / "symbols_research_ranking.csv"
CAUSALITY_MANIFEST_PATH: Path = CTA_ROOT / "feature" / "causality_manifest.csv"


def _load_manifest_features(manifest_path: Path) -> set[str]:
    """Return lowercased feature names from causality manifest, or empty set."""
    path = Path(manifest_path)
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return set()
    if "feature" not in df.columns:
        return set()
    out: set[str] = set()
    for v in df["feature"].astype(str).tolist():
        s = v.strip().lower()
        if s:
            out.add(s)
    return out

# 与 training_feature_builder 保持一致的非特征列。
_NON_FEATURE_COLUMNS: frozenset[str] = frozenset(
    {
        "datetime",
        "signal_datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
        "open_interest",
        "amount",
        "ts_code",
        "symbol",
        "exchange",
        "interval",
        "trade_date",
        "_merge_key",
    }
)


def _load_symbols_from_ranking(path: Path, top_n: int) -> list[str]:
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"ranking csv empty: {path}")
    if "symbol" not in df.columns:
        raise KeyError(f"ranking csv missing 'symbol' column: {path}")
    if "research_rank" in df.columns:
        df = df.sort_values("research_rank")
    syms = [str(s).strip().upper() for s in df["symbol"].astype(str).tolist() if str(s).strip()]
    if top_n > 0:
        syms = syms[:top_n]
    return syms


def _load_symbol_frame(interval: str, symbol: str, feature_root: Path) -> pd.DataFrame:
    interval_norm = normalize_interval(interval)
    sym_dir = feature_root / interval_norm / str(symbol).upper()
    if not sym_dir.exists():
        return pd.DataFrame()
    files = sorted(sym_dir.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    parts: list[pd.DataFrame] = []
    for f in files:
        try:
            parts.append(pd.read_parquet(f))
        except Exception as exc:
            logger.warning("skip unreadable parquet %s: %s", f, exc)
            continue
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, axis=0, ignore_index=True)
    if "datetime" not in df.columns:
        return pd.DataFrame()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    return df


def _abs_corr(series: pd.Series, target: pd.Series, method: str) -> float:
    s = pd.to_numeric(series, errors="coerce")
    t = pd.to_numeric(target, errors="coerce")
    mask = s.notna() & t.notna() & np.isfinite(s) & np.isfinite(t)
    if int(mask.sum()) < 30:
        return float("nan")
    try:
        v = float(s.loc[mask].corr(t.loc[mask], method=method))
    except Exception:
        return float("nan")
    if not np.isfinite(v):
        return float("nan")
    return abs(v)


def audit_symbol(
    df: pd.DataFrame,
    symbol: str,
    horizon: int,
    mae_penalty: float = 0.7,
) -> pd.DataFrame:
    """Return per-column IC vs forward returns for a single symbol."""
    if df.empty or "close" not in df.columns:
        return pd.DataFrame()
    close = pd.to_numeric(df["close"], errors="coerce")
    fwd1 = close.shift(-1) / close - 1.0
    # horizon edge：与 label_class 的语义对齐（mfe - 0.7 * mae），但用 close 路径估计。
    fwd_high = pd.to_numeric(df.get("high", df.get("close")), errors="coerce").rolling(window=horizon).max().shift(-horizon)
    fwd_low = pd.to_numeric(df.get("low", df.get("close")), errors="coerce").rolling(window=horizon).min().shift(-horizon)
    fwd_mfe = (fwd_high - close) / close
    fwd_mae = (close - fwd_low) / close
    fwd_edge = fwd_mfe - float(mae_penalty) * fwd_mae

    feature_cols: list[str] = []
    for c in df.columns:
        name = str(c)
        if name in _NON_FEATURE_COLUMNS:
            continue
        s = df[c]
        if not (pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s)):
            continue
        if s.isna().all():
            continue
        feature_cols.append(name)

    rows: list[dict[str, float | str]] = []
    for c in feature_cols:
        ic_p_fwd1 = _abs_corr(df[c], fwd1, "pearson")
        ic_s_fwd1 = _abs_corr(df[c], fwd1, "spearman")
        ic_p_edge = _abs_corr(df[c], fwd_edge, "pearson")
        ic_s_edge = _abs_corr(df[c], fwd_edge, "spearman")
        ic_fwd1 = float(np.nanmax([ic_p_fwd1, ic_s_fwd1])) if not (np.isnan(ic_p_fwd1) and np.isnan(ic_s_fwd1)) else float("nan")
        ic_edge = float(np.nanmax([ic_p_edge, ic_s_edge])) if not (np.isnan(ic_p_edge) and np.isnan(ic_s_edge)) else float("nan")
        ic_max = float(np.nanmax([ic_fwd1, ic_edge])) if not (np.isnan(ic_fwd1) and np.isnan(ic_edge)) else float("nan")
        rows.append(
            {
                "symbol": str(symbol).upper(),
                "column": str(c),
                "ic_fwd1": ic_fwd1,
                "ic_horizon_edge": ic_edge,
                "ic_max": ic_max,
            }
        )
    return pd.DataFrame(rows)


def run_audit(
    interval: str,
    symbols: Sequence[str],
    horizon: int,
    abs_ic_threshold: float,
    out_path: Path,
    feature_root: Path = FEATURE_ROOT,
    manifest_path: Path | None = None,
) -> tuple[Path, Path]:
    all_parts: list[pd.DataFrame] = []
    for sym in symbols:
        df = _load_symbol_frame(interval=interval, symbol=sym, feature_root=feature_root)
        if df.empty:
            logger.warning("no parquet rows for symbol=%s interval=%s", sym, interval)
            continue
        part = audit_symbol(df, symbol=sym, horizon=horizon)
        if not part.empty:
            all_parts.append(part)
        logger.info("audited symbol=%s columns=%d", sym, 0 if part is None else len(part))
    if not all_parts:
        logger.warning("no audit rows produced; check feature_root and symbols list")
        empty = pd.DataFrame(columns=["symbol", "column", "ic_fwd1", "ic_horizon_edge", "ic_max"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        empty.to_csv(out_path, index=False, encoding="utf-8-sig")
        suspect_path = out_path.with_suffix(".suspect.csv")
        empty.to_csv(suspect_path, index=False, encoding="utf-8-sig")
        return out_path, suspect_path
    full = pd.concat(all_parts, axis=0, ignore_index=True)
    full = full.sort_values(["ic_max"], ascending=False).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    full.to_csv(out_path, index=False, encoding="utf-8-sig")

    suspect = full.loc[pd.to_numeric(full["ic_max"], errors="coerce").fillna(0.0) >= float(abs_ic_threshold)].copy()
    # 汇总到 column 维度：跨 symbol 的 ic_max 中位数 + 命中 symbol 数
    if not suspect.empty:
        agg = (
            suspect.groupby("column", as_index=False)
            .agg(
                hit_symbols=("symbol", "nunique"),
                ic_max_median=("ic_max", "median"),
                ic_max_p90=("ic_max", lambda s: float(np.nanpercentile(s, 90))),
            )
            .sort_values(["hit_symbols", "ic_max_median"], ascending=[False, False])
            .reset_index(drop=True)
        )
    else:
        agg = pd.DataFrame(columns=["column", "hit_symbols", "ic_max_median", "ic_max_p90"])
    # P1.5 联动：把"|IC|>阈值且不在 manifest 中"的列额外标记为 needs_review。
    manifest_features = _load_manifest_features(
        Path(manifest_path) if manifest_path else CAUSALITY_MANIFEST_PATH
    )
    if manifest_features and not agg.empty:
        agg["in_manifest"] = agg["column"].astype(str).str.lower().isin(manifest_features).astype(int)
        agg["needs_review"] = (agg["in_manifest"] == 0).astype(int)
    else:
        agg["in_manifest"] = 0
        agg["needs_review"] = 1 if not agg.empty else 0

    suspect_path = out_path.with_suffix(".suspect.csv")
    agg.to_csv(suspect_path, index=False, encoding="utf-8-sig")
    return out_path, suspect_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline leakage audit for generic feature parquets")
    parser.add_argument("--interval", default="day", help="feature interval (default day)")
    parser.add_argument(
        "--symbols-ranking-path",
        default=str(SYMBOLS_RANKING_PATH),
        help="path to symbols ranking csv (default cta/feature/symbols_research_ranking.csv)",
    )
    parser.add_argument("--top-n", type=int, default=18, help="top-N symbols from ranking (default 18)")
    parser.add_argument("--symbols", nargs="+", default=None, help="explicit symbol list (overrides --top-n)")
    parser.add_argument("--horizon", type=int, default=5, help="horizon bars for fwd MFE/MAE (default 5)")
    parser.add_argument(
        "--abs-ic-threshold",
        type=float,
        default=0.30,
        help="|IC| threshold for marking a feature suspect (default 0.30)",
    )
    parser.add_argument(
        "--feature-root",
        default=str(FEATURE_ROOT),
        help=f"feature parquet root (default {FEATURE_ROOT})",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output csv path; defaults to cta/report/leakage_audit/<interval>_leakage_audit.csv",
    )
    parser.add_argument(
        "--manifest",
        default=str(CAUSALITY_MANIFEST_PATH),
        help=f"causality manifest csv path (default {CAUSALITY_MANIFEST_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _parse_args(argv)
    if args.symbols:
        symbols = [str(s).strip().upper() for s in args.symbols if str(s).strip()]
    else:
        symbols = _load_symbols_from_ranking(Path(args.symbols_ranking_path), top_n=int(args.top_n))
    if args.out:
        out_path = Path(args.out).resolve()
    else:
        out_path = CTA_ROOT / "report" / "leakage_audit" / f"{normalize_interval(args.interval)}_leakage_audit.csv"
    full_path, suspect_path = run_audit(
        interval=str(args.interval),
        symbols=symbols,
        horizon=int(args.horizon),
        abs_ic_threshold=float(args.abs_ic_threshold),
        out_path=out_path,
        feature_root=Path(args.feature_root),
        manifest_path=Path(args.manifest) if args.manifest else None,
    )
    logger.info("leakage audit full report:    %s", full_path)
    logger.info("leakage audit suspect report: %s", suspect_path)


__all__ = ["audit_symbol", "run_audit", "main"]


if __name__ == "__main__":
    main()
