"""Baseline-to-candidate bridge helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import BASELINE_SIGNAL_TYPES
from cta.config.skill_tight_range_breakout_config import BacktestConfig, CTA_ROOT
from cta.strategy.baseline_skill_suite import generate_candidate_opportunities, prepare_master_feature_frame
from cta.strategy.skill_tight_range_backtest import load_bars, normalize_interval, resolve_exchange

SYMBOLS_RANKING_PATH: Path = CTA_ROOT / "feature" / "symbols_research_ranking.csv"


def _normalize_intervals(raw: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for token in raw:
        if token is None:
            continue
        for piece in str(token).split(","):
            v = piece.strip().lower()
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(v)
    if not out:
        raise ValueError("no valid interval provided; expected one of day/60min/30min/15min/5min/min")
    return tuple(out)


def _load_top_n_symbols_from_ranking(
    ranking_path: Path,
    top_n: int,
) -> list[tuple[str, str | None]]:
    n = int(top_n)
    if n <= 0:
        return []
    path = Path(ranking_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"symbols ranking csv not found: {path}")

    df = pd.read_csv(path, encoding="utf-8-sig")
    if "symbol" not in df.columns:
        raise KeyError(f"ranking csv missing symbol column: {path}")
    df = df.copy()
    if "research_rank" in df.columns:
        df["_rank"] = pd.to_numeric(df["research_rank"], errors="coerce")
    else:
        df["_rank"] = np.arange(len(df), dtype=float)
    df["_rank"] = df["_rank"].fillna(np.inf)
    df = df.sort_values(["_rank"]).reset_index(drop=True)

    all_pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        sym = str(row.get("symbol", "")).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ex_raw = row.get("exchange", None)
        ex = str(ex_raw).strip().upper() if pd.notna(ex_raw) and str(ex_raw).strip() else None
        all_pairs.append((sym, ex))

    from cta.config.symbol_disable import filter_out_disabled_pairs

    filtered = filter_out_disabled_pairs(all_pairs)
    picked = filtered[:n]
    if not picked:
        raise ValueError(
            f"no valid symbols loaded from ranking csv: {path}; "
            f"check symbol_disable_manifest.csv if you expect more"
        )
    return picked


def _resolve_run_exchange(
    exchange_from_rank: str | None,
    cli_exchange: str | None,
) -> str | None:
    if exchange_from_rank:
        return str(exchange_from_rank).upper()
    if cli_exchange:
        return str(cli_exchange).upper()
    return None


def generate_candidate_events_from_baselines(
    symbol: str,
    exchange: str | None,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str = "both",
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    horizon_bars: int = 20,
) -> pd.DataFrame:
    """Generate candidate samples from baseline rule strategies."""
    sym = str(symbol).upper()
    interval_norm = normalize_interval(interval)
    bcfg = BacktestConfig(interval=interval_norm)
    ex = str(exchange).upper() if exchange else resolve_exchange(sym, bcfg.symbols_list_path)
    bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
    frame = prepare_master_feature_frame(bars, interval=interval_norm)

    parts: list[pd.DataFrame] = []
    for signal_type in signal_types:
        cand = generate_candidate_opportunities(
            frame=frame,
            symbol=sym,
            exchange=ex,
            interval=interval_norm,
            signal_type=str(signal_type),
            horizon_bars=horizon_bars,
            trade_side_mode=trade_side_mode,
        )
        if not cand.empty:
            parts.append(cand)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, axis=0, ignore_index=True)
    return out.sort_values("datetime").reset_index(drop=True)


__all__ = [
    "SYMBOLS_RANKING_PATH",
    "_normalize_intervals",
    "_load_top_n_symbols_from_ranking",
    "_resolve_run_exchange",
    "generate_candidate_events_from_baselines",
]
