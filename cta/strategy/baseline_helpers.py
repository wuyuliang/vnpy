"""Shared helpers for baseline skill suite."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from cta.strategy.skill_tight_range_backtest import normalize_interval
from cta.strategy.skill_tight_range_breakout import ContractSpec

SYMBOLS_RANKING_PATH = Path(__file__).resolve().parents[1] / "feature" / "symbols_research_ranking.csv"


@dataclass(frozen=True)
class BaselineSuiteRunResult:
    output_dir: Path
    summary_path: Path
    training_samples_path: Path
    report_path: Path


def _normalize_intervals(intervals: str | Sequence[str]) -> tuple[str, ...]:
    """Normalize one-or-many interval args."""
    if isinstance(intervals, str):
        raw_tokens: list[str] = [intervals]
    else:
        raw_tokens = [str(x) for x in intervals]
    parts: list[str] = []
    for tk in raw_tokens:
        parts.extend([p.strip() for p in str(tk).split(",") if p.strip()])

    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        canon = normalize_interval(p)
        if canon not in seen:
            seen.add(canon)
            out.append(canon)
    return tuple(out)


def _load_top_n_symbols_from_ranking(ranking_path: Path, top_n: int) -> list[tuple[str, str]]:
    """Load top-N symbols ordered by ``research_rank`` from ranking csv."""
    if int(top_n) <= 0:
        return []
    if not ranking_path.exists():
        raise FileNotFoundError(f"symbols ranking csv not found: {ranking_path}")

    df = pd.read_csv(ranking_path, encoding="utf-8-sig")
    need = {"symbol", "exchange", "research_rank"}
    miss = need - set(df.columns)
    if miss:
        raise ValueError(f"ranking csv missing columns: {sorted(miss)}")

    view = df[["symbol", "exchange", "research_rank"]].copy()
    view["symbol"] = view["symbol"].astype(str).str.strip().str.upper()
    view["exchange"] = view["exchange"].astype(str).str.strip().str.upper()
    view["research_rank"] = pd.to_numeric(view["research_rank"], errors="coerce")
    view = view.dropna(subset=["symbol", "exchange", "research_rank"])
    view = view.sort_values("research_rank").drop_duplicates(subset=["symbol", "exchange"], keep="first")
    view = view.head(int(top_n)).reset_index(drop=True)
    return [(str(r["symbol"]), str(r["exchange"])) for _, r in view.iterrows()]


def _resolve_run_exchange(exchange_from_rank: str | None, cli_exchange: str | None) -> str | None:
    rank_ex = str(exchange_from_rank).strip().upper() if exchange_from_rank else None
    cli_ex = str(cli_exchange).strip().upper() if cli_exchange else None
    return rank_ex or cli_ex


def _safe_float(v: Any) -> float:
    try:
        fv = float(v)
    except Exception:
        return float("nan")
    return fv


def _safe_bool(v: Any) -> bool:
    """Convert noisy values into bool safely (NaN/None -> False)."""
    if v is None:
        return False
    try:
        if isinstance(v, float) and np.isnan(v):
            return False
        if isinstance(v, np.floating) and bool(np.isnan(v)):
            return False
    except Exception:
        return False
    try:
        return bool(v)
    except Exception:
        return False


def _compute_atr14(df: pd.DataFrame) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=7).mean()


def _side_allowed(mode: str, side: str) -> bool:
    m = str(mode).strip().lower()
    s = str(side).strip().lower()
    if m == "both":
        return s in {"long", "short"}
    if m == "long":
        return s == "long"
    if m == "short":
        return s == "short"
    return False


def _entry_order(
    contract: ContractSpec,
    side: str,
    lots: int,
    order_type: str,
    price: float | None = None,
) -> dict[str, Any]:
    od: dict[str, Any] = {
        "side": str(side),
        "lots": int(max(1, lots)),
        "order_type": str(order_type),
        "symbol": contract.vt_symbol,
        "multiplier": float(contract.multiplier),
        "commission_rate": float(contract.commission_rate),
        "tick_size": float(contract.tick_size),
    }
    if price is not None and np.isfinite(float(price)):
        od["price"] = float(price)
    return od


__all__ = [
    "SYMBOLS_RANKING_PATH",
    "BaselineSuiteRunResult",
    "_normalize_intervals",
    "_load_top_n_symbols_from_ranking",
    "_resolve_run_exchange",
    "_safe_float",
    "_safe_bool",
    "_compute_atr14",
    "_side_allowed",
    "_entry_order",
]

