"""Cross-source day/minute consistency audit.

目标：
1) 用分钟 parquet（默认 ``cta/data/origin/minute``）按 trade_date 聚合成日线；
2) 与日线 CSV（默认 ``cta/data/origin/day``）做 OHLCV 对账；
3) 抽样每个品种若干交易日（默认 5）输出误差明细；
4) 超过阈值的品种标记 ``disabled``，供后续训练前过滤。
"""
from __future__ import annotations

import argparse
import logging
import random
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from cta.data_code.futures_downloader import DATA_DIR, DAY_DIR, MINUTE_INTERVALS, alpha_prefix

logger = logging.getLogger("cta.data_code.cross_source_audit")

AUDIT_FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
OPTIONAL_FIELDS: tuple[str, ...] = ("open_interest", "turnover")

# P0.1：分字段 tolerance。OHLC 用 1‰，volume / open_interest / turnover 这种百万级
# 数值用 2%（夜盘重叠时两源 1-2 笔差异属正常），避免一刀切让 volume 把品种打 disabled。
FIELD_TOLERANCE: dict[str, float] = {
    "open": 0.001,
    "high": 0.001,
    "low": 0.001,
    "close": 0.001,
    "volume": 0.02,
    "open_interest": 0.02,
    "turnover": 0.02,
}


def _field_tolerance(field: str, fallback: float, overrides: dict[str, float] | None = None) -> float:
    if overrides and field in overrides:
        return float(overrides[field])
    if field in FIELD_TOLERANCE:
        return float(FIELD_TOLERANCE[field])
    return float(fallback)


@dataclass
class SymbolAuditSummary:
    """Per-symbol audit summary."""

    symbol: str
    day_rows: int = 0
    minute_trade_days: int = 0
    overlap_days: int = 0
    sampled_days: int = 0
    mismatch_days: int = 0
    max_rel_error: float = float("nan")
    disabled: bool = False
    notes: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "day_rows": self.day_rows,
            "minute_trade_days": self.minute_trade_days,
            "overlap_days": self.overlap_days,
            "sampled_days": self.sampled_days,
            "mismatch_days": self.mismatch_days,
            "max_rel_error": self.max_rel_error,
            "disabled": int(self.disabled),
            "notes": " | ".join(self.notes),
        }


def _safe_float(value: object) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return f


def _relative_error(lhs: float, rhs: float) -> float:
    """|lhs-rhs| / max(|lhs|, |rhs|, 1e-12)."""
    if not (np.isfinite(lhs) and np.isfinite(rhs)):
        return float("nan")
    denom = max(abs(lhs), abs(rhs), 1e-12)
    return abs(lhs - rhs) / denom


def _normalize_day_csv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "datetime" not in df.columns:
        return pd.DataFrame(columns=["trade_date", *AUDIT_FIELDS, *OPTIONAL_FIELDS])
    out = df.copy()
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).copy()
    out["trade_date"] = out["datetime"].dt.strftime("%Y-%m-%d")
    for c in AUDIT_FIELDS:
        out[c] = pd.to_numeric(out.get(c, np.nan), errors="coerce")
    for c in OPTIONAL_FIELDS:
        out[c] = pd.to_numeric(out.get(c, np.nan), errors="coerce")
    out = out[["trade_date", *AUDIT_FIELDS, *OPTIONAL_FIELDS]].drop_duplicates(subset=["trade_date"], keep="last")
    return out.sort_values("trade_date").reset_index(drop=True)


def build_daily_from_minute_files(minute_symbol_dir: Path) -> pd.DataFrame:
    """Aggregate minute parquet files into daily OHLCV by file stem date."""
    rows: list[dict[str, object]] = []
    for fp in sorted(minute_symbol_dir.glob("*.parquet")):
        trade_date = fp.stem
        try:
            pd.Timestamp(trade_date)
        except Exception:
            logger.debug("skip non-date parquet stem: %s", fp.name)
            continue

        try:
            df = pd.read_parquet(fp)
        except Exception as exc:  # noqa: BLE001
            logger.warning("read parquet failed %s: %s", fp, exc)
            continue
        if df.empty:
            continue
        if "datetime" not in df.columns:
            logger.debug("skip parquet(no datetime): %s", fp)
            continue
        required_cols = ("open", "high", "low", "close", "volume")
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            logger.debug("skip parquet missing cols %s: %s", missing_cols, fp)
            continue
        dfx = df.copy()
        dfx["datetime"] = pd.to_datetime(dfx["datetime"], errors="coerce")
        dfx = dfx.dropna(subset=["datetime"]).sort_values("datetime")
        if dfx.empty:
            continue

        o = pd.to_numeric(dfx["open"], errors="coerce")
        h = pd.to_numeric(dfx["high"], errors="coerce")
        l = pd.to_numeric(dfx["low"], errors="coerce")
        c = pd.to_numeric(dfx["close"], errors="coerce")
        v = pd.to_numeric(dfx["volume"], errors="coerce").fillna(0.0)
        if "open_interest" in dfx.columns:
            oi = pd.to_numeric(dfx["open_interest"], errors="coerce")
        else:
            oi = pd.Series(np.nan, index=dfx.index, dtype=float)
        if "turnover" in dfx.columns:
            tv = pd.to_numeric(dfx["turnover"], errors="coerce")
        else:
            tv = pd.Series(np.nan, index=dfx.index, dtype=float)
        if o.isna().all() or h.isna().all() or l.isna().all() or c.isna().all():
            continue
        rows.append(
            {
                "trade_date": trade_date,
                "open": float(o.dropna().iloc[0]),
                "high": float(h.max(skipna=True)),
                "low": float(l.min(skipna=True)),
                "close": float(c.dropna().iloc[-1]),
                "volume": float(v.sum()),
                "open_interest": float(oi.dropna().iloc[-1]) if oi.notna().any() else float("nan"),
                "turnover": float(tv.fillna(0.0).sum()) if tv.notna().any() else float("nan"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["trade_date", *AUDIT_FIELDS, *OPTIONAL_FIELDS])
    out = pd.DataFrame(rows).drop_duplicates(subset=["trade_date"], keep="last")
    return out.sort_values("trade_date").reset_index(drop=True)


def audit_symbol(
    *,
    symbol: str,
    day_csv_path: Path,
    minute_symbol_dir: Path,
    sample_days: int,
    tolerance: float,
    random_seed: int,
    min_mismatch_days_to_disable: int = 2,
    tolerance_overrides: dict[str, float] | None = None,
) -> tuple[SymbolAuditSummary, pd.DataFrame]:
    """Audit one symbol and return (summary, detail)."""
    summary = SymbolAuditSummary(symbol=str(symbol).upper())
    detail_rows: list[dict[str, object]] = []
    sym = str(symbol).upper()

    if not day_csv_path.exists():
        summary.disabled = True
        summary.notes.append(f"missing day csv: {day_csv_path}")
        return summary, pd.DataFrame(detail_rows)
    if not minute_symbol_dir.exists():
        summary.disabled = True
        summary.notes.append(f"missing minute dir: {minute_symbol_dir}")
        return summary, pd.DataFrame(detail_rows)

    try:
        day_raw = pd.read_csv(day_csv_path, encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        summary.disabled = True
        summary.notes.append(f"read day csv failed: {exc}")
        return summary, pd.DataFrame(detail_rows)
    day_df = _normalize_day_csv(day_raw)
    minute_df = build_daily_from_minute_files(minute_symbol_dir)

    summary.day_rows = int(len(day_df))
    summary.minute_trade_days = int(len(minute_df))
    if day_df.empty or minute_df.empty:
        summary.disabled = True
        summary.notes.append("empty day/minute daily frame")
        return summary, pd.DataFrame(detail_rows)

    merged = day_df.merge(
        minute_df,
        on="trade_date",
        how="inner",
        suffixes=("_day", "_minute"),
    )
    summary.overlap_days = int(len(merged))
    if merged.empty:
        summary.disabled = True
        summary.notes.append("no overlap trade_date")
        return summary, pd.DataFrame(detail_rows)

    k = max(1, int(sample_days))
    k = min(k, len(merged))
    # P0.1: numpy/random 要求 seed ∈ [0, 2**32-1)，crc32 + random_seed 可能溢出
    seed = (int(random_seed) + int(zlib.crc32(sym.encode("utf-8")) & 0xFFFFFFFF)) % (2**32 - 1)
    rng = random.Random(seed)
    picks = sorted(rng.sample(list(range(len(merged))), k=k))
    sampled = merged.iloc[picks].reset_index(drop=True)
    summary.sampled_days = int(len(sampled))

    mismatch_day_count = 0
    max_rel_error = float("nan")
    for _, row in sampled.iterrows():
        trade_date = str(row["trade_date"])
        day_mismatch = False
        for f in AUDIT_FIELDS:
            day_v = _safe_float(row.get(f"{f}_day"))
            minute_v = _safe_float(row.get(f"{f}_minute"))
            rel_err = _relative_error(day_v, minute_v)
            tol = _field_tolerance(f, tolerance, tolerance_overrides)
            is_mismatch = int(np.isfinite(rel_err) and rel_err > float(tol))
            if is_mismatch:
                day_mismatch = True
            if np.isfinite(rel_err):
                if not np.isfinite(max_rel_error):
                    max_rel_error = rel_err
                else:
                    max_rel_error = max(max_rel_error, rel_err)
            detail_rows.append(
                {
                    "symbol": sym,
                    "trade_date": trade_date,
                    "field": f,
                    "day_value": day_v,
                    "minute_value": minute_v,
                    "rel_error": rel_err,
                    "is_mismatch": is_mismatch,
                    "tolerance": float(tol),
                }
            )
        if day_mismatch:
            mismatch_day_count += 1

    summary.mismatch_days = int(mismatch_day_count)
    summary.max_rel_error = float(max_rel_error) if np.isfinite(max_rel_error) else float("nan")
    # P0.1：默认要求至少 2 天 mismatch 才 disable，避免单日因夜盘归属日错位被误杀。
    min_required = max(1, int(min_mismatch_days_to_disable))
    summary.disabled = summary.mismatch_days >= min_required
    if summary.disabled:
        summary.notes.append(
            f"mismatch_days={summary.mismatch_days}>={min_required} (max_rel_err={summary.max_rel_error:.4g})"
        )

    detail_df = pd.DataFrame(detail_rows)
    return summary, detail_df


def load_symbols_from_ranking(
    *,
    ranking_csv_path: Path,
    top_n: int,
    only_symbols: Iterable[str] | None = None,
) -> list[tuple[str, str]]:
    """Load symbols ordered by research_rank from ranking csv."""
    if not ranking_csv_path.exists():
        raise FileNotFoundError(f"ranking csv not found: {ranking_csv_path}")
    df = pd.read_csv(ranking_csv_path, encoding="utf-8-sig")
    if "symbol" not in df.columns:
        raise KeyError(f"ranking csv missing symbol column: {ranking_csv_path}")

    out = df.copy()
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["exchange"] = out.get("exchange", "").astype(str).str.strip().str.upper()
    rank_col = "research_rank" if "research_rank" in out.columns else None
    if rank_col:
        out["_rank"] = pd.to_numeric(out[rank_col], errors="coerce").fillna(np.inf)
    else:
        out["_rank"] = np.arange(len(out), dtype=float)
    out = out.sort_values(["_rank"]).reset_index(drop=True)

    if only_symbols:
        want = {str(s).upper() for s in only_symbols}
        out = out[out["symbol"].isin(want)].copy()

    if int(top_n) > 0:
        out = out.head(int(top_n)).copy()
    return list(zip(out["symbol"], out["exchange"]))


def run_audit(
    *,
    symbols: list[tuple[str, str]],
    sample_days: int = 5,
    tolerance: float = 0.001,
    random_seed: int = 2026,
    day_dir: Path = DAY_DIR,
    minute_root: Path = DATA_DIR / "origin" / "minute",
    min_mismatch_days_to_disable: int = 2,
    tolerance_overrides: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run audit for symbols and return (summary_df, detail_df)."""
    summary_rows: list[dict[str, object]] = []
    details: list[pd.DataFrame] = []
    for symbol, _exchange in symbols:
        sym = str(symbol).upper()
        prefix = alpha_prefix(sym)
        summary, detail = audit_symbol(
            symbol=sym,
            day_csv_path=day_dir / f"{sym}.csv",
            minute_symbol_dir=minute_root / prefix,
            sample_days=sample_days,
            tolerance=tolerance,
            random_seed=random_seed,
            min_mismatch_days_to_disable=min_mismatch_days_to_disable,
            tolerance_overrides=tolerance_overrides,
        )
        summary_rows.append(summary.to_row())
        if not detail.empty:
            details.append(detail)
    summary_df = pd.DataFrame(summary_rows)
    if details:
        detail_df = pd.concat(details, axis=0, ignore_index=True)
    else:
        detail_df = pd.DataFrame(
            columns=["symbol", "trade_date", "field", "day_value", "minute_value", "rel_error", "is_mismatch", "tolerance"]
        )
    return summary_df, detail_df


def _default_output_paths(report_dir: Path, run_tag: str) -> tuple[Path, Path, Path]:
    summary_path = report_dir / f"{run_tag}_cross_source_audit_summary.csv"
    detail_path = report_dir / f"{run_tag}_cross_source_audit_detail.csv"
    disabled_path = report_dir / f"{run_tag}_cross_source_audit_disabled_symbols.csv"
    return summary_path, detail_path, disabled_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cross-source day/minute audit for CTA symbols")
    parser.add_argument("--top-n", type=int, default=20, help="top-N symbols by research_rank (default 20)")
    parser.add_argument("--symbol", action="append", default=None, help="optional symbols (can repeat)")
    parser.add_argument(
        "--ranking-csv",
        type=str,
        default=str((DATA_DIR.parent / "feature" / "symbols_research_ranking.csv").resolve()),
        help="symbols ranking csv path",
    )
    parser.add_argument("--sample-days", type=int, default=5, help="sample days per symbol (default 5)")
    parser.add_argument("--tolerance", type=float, default=0.001, help="default relative error threshold (default 0.001)")
    parser.add_argument(
        "--volume-tolerance",
        type=float,
        default=0.02,
        help="relative error threshold for volume/open_interest/turnover (default 0.02)",
    )
    parser.add_argument(
        "--min-mismatch-days",
        type=int,
        default=2,
        help="minimal mismatch days to mark symbol as disabled (default 2, was 1)",
    )
    parser.add_argument("--seed", type=int, default=2026, help="random seed for sampling")
    parser.add_argument("--minute-interval", type=str, default="minute", choices=list(MINUTE_INTERVALS))
    parser.add_argument("--day-dir", type=str, default=str(DAY_DIR.resolve()))
    parser.add_argument("--out-summary", type=str, default=None)
    parser.add_argument("--out-detail", type=str, default=None)
    parser.add_argument("--out-disabled", type=str, default=None)
    parser.add_argument("--run-tag", type=str, default=pd.Timestamp.now().strftime("%Y%m%d"))
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()

    ranking_csv = Path(args.ranking_csv).expanduser().resolve()
    pairs = load_symbols_from_ranking(
        ranking_csv_path=ranking_csv,
        top_n=int(args.top_n),
        only_symbols=args.symbol,
    )
    if not pairs:
        logger.warning("no symbols matched for audit")
        return

    day_dir = Path(args.day_dir).expanduser().resolve()
    minute_root = (DATA_DIR / "origin" / str(args.minute_interval)).resolve()
    overrides = {
        "volume": float(args.volume_tolerance),
        "open_interest": float(args.volume_tolerance),
        "turnover": float(args.volume_tolerance),
    }
    summary_df, detail_df = run_audit(
        symbols=pairs,
        sample_days=int(args.sample_days),
        tolerance=float(args.tolerance),
        random_seed=int(args.seed),
        day_dir=day_dir,
        minute_root=minute_root,
        min_mismatch_days_to_disable=int(args.min_mismatch_days),
        tolerance_overrides=overrides,
    )
    report_dir = (DATA_DIR.parent / "report" / "data_audit").resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    summary_path_default, detail_path_default, disabled_path_default = _default_output_paths(
        report_dir=report_dir,
        run_tag=str(args.run_tag),
    )
    summary_path = Path(args.out_summary).expanduser().resolve() if args.out_summary else summary_path_default
    detail_path = Path(args.out_detail).expanduser().resolve() if args.out_detail else detail_path_default
    disabled_path = Path(args.out_disabled).expanduser().resolve() if args.out_disabled else disabled_path_default

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    disabled_path.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    detail_df.to_csv(detail_path, index=False, encoding="utf-8-sig")

    # P0.1：disabled csv 写全 symbol + mismatch_days + max_rel_error + notes，
    # 不再只列 symbol，便于运维不查 summary 表就能知道为何被禁用。
    disabled_cols = ["symbol", "mismatch_days", "max_rel_error", "notes"]
    disabled = summary_df.loc[
        pd.to_numeric(summary_df["disabled"], errors="coerce").fillna(0).astype(int) == 1,
        [c for c in disabled_cols if c in summary_df.columns],
    ]
    disabled.to_csv(disabled_path, index=False, encoding="utf-8-sig")

    total = len(summary_df)
    disabled_n = len(disabled)
    logger.info(
        "cross-source audit done: symbols=%s disabled=%s tolerance=%s sample_days=%s",
        total,
        disabled_n,
        args.tolerance,
        args.sample_days,
    )
    logger.info("summary:  %s", summary_path)
    logger.info("detail:   %s", detail_path)
    logger.info("disabled: %s", disabled_path)


if __name__ == "__main__":
    main()


__all__ = [
    "AUDIT_FIELDS",
    "SymbolAuditSummary",
    "build_daily_from_minute_files",
    "audit_symbol",
    "load_symbols_from_ranking",
    "run_audit",
]
