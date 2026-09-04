"""Causal ranked-symbol selection for cycle_v1 minute downloads."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd


_SYMBOL_PATTERN = re.compile(
    r"^(?P<root>[A-Z]+)(?:0)?(?:\.(?P<exchange>[A-Z]+))?$"
)
_EXCHANGE_ALIASES = {
    "CFX": "CFFEX",
    "CZC": "CZCE",
    "GFE": "GFEX",
    "SHF": "SHFE",
    "ZCE": "CZCE",
}
SUPPORTED_EXCHANGES = ("SHFE", "DCE", "CZCE", "INE", "GFEX", "CFFEX")


@dataclass(frozen=True)
class RankedSymbol:
    root_symbol: str
    continuous_symbol: str
    exchange: str
    research_rank: int


@dataclass(frozen=True)
class SelectedSymbol:
    root_symbol: str
    exchange: str
    sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_symbol": self.root_symbol,
            "exchange": self.exchange,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class SelectionRejection:
    root_symbol: str
    stage: str
    reason_code: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "root_symbol": self.root_symbol,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class UniverseSelection:
    explicit: tuple[str, ...]
    top_n: tuple[str, ...]
    ema_eligible: tuple[str, ...]
    selected: tuple[SelectedSymbol, ...]
    rejections: tuple[SelectionRejection, ...]

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "explicit": list(self.explicit),
            "top_n": list(self.top_n),
            "ema_eligible": list(self.ema_eligible),
            "selected": [item.to_dict() for item in self.selected],
        }


def load_ranked_symbols(path: str | Path) -> tuple[RankedSymbol, ...]:
    ranking_path = Path(path)
    frame = pd.read_csv(ranking_path)
    required = {"symbol", "exchange", "research_rank"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"ranking csv is missing columns: {','.join(missing)}")

    rows: list[RankedSymbol] = []
    for row in frame.loc[:, ["symbol", "exchange", "research_rank"]].itertuples(
        index=False
    ):
        root, token_exchange = _parse_symbol(str(row.symbol))
        exchange = _normalize_exchange(str(row.exchange))
        if token_exchange and token_exchange != exchange:
            raise ValueError(f"ranking exchange conflict for root: {root}")
        rank_number = pd.to_numeric(row.research_rank, errors="coerce")
        if not np.isfinite(rank_number) or float(rank_number) <= 0:
            raise ValueError(f"invalid research_rank for root: {root}")
        rank = int(rank_number)
        if float(rank) != float(rank_number):
            raise ValueError(f"research_rank must be an integer for root: {root}")
        rows.append(
            RankedSymbol(
                root_symbol=root,
                continuous_symbol=f"{root}0",
                exchange=exchange,
                research_rank=rank,
            )
        )
    rows.sort(key=lambda item: (item.research_rank, item.root_symbol))
    roots = [item.root_symbol for item in rows]
    duplicate_roots = sorted({root for root in roots if roots.count(root) > 1})
    if duplicate_roots:
        raise ValueError(f"duplicate ranking root: {','.join(duplicate_roots)}")
    return tuple(rows)


def select_ranked_universe(
    *,
    explicit: Sequence[str],
    top_n: int,
    include_ema_eligible: bool,
    ranking_csv: str | Path,
    day_root: str | Path,
    start: date,
    end: date,
    contract_reference: pd.DataFrame,
) -> UniverseSelection:
    if top_n < 0:
        raise ValueError("top_n must be nonnegative")
    if end < start:
        raise ValueError("end must not precede start")
    ranking = load_ranked_symbols(ranking_csv)
    ranked_by_root = {item.root_symbol: item for item in ranking}
    rejections: list[SelectionRejection] = []
    selected: OrderedDict[str, tuple[str, list[str]]] = OrderedDict()
    explicit_roots: list[str] = []
    explicit_exchanges: dict[str, str] = {}
    blocked_roots: set[str] = set()

    def add(root: str, exchange: str, source: str) -> None:
        if root in blocked_roots:
            return
        existing = selected.get(root)
        if existing is None:
            selected[root] = (exchange, [source])
            return
        existing_exchange, sources = existing
        if existing_exchange != exchange:
            selected.pop(root, None)
            blocked_roots.add(root)
            rejections.append(
                SelectionRejection(
                    root_symbol=root,
                    stage=source,
                    reason_code="EXCHANGE_CONFLICT",
                    detail=f"{existing_exchange}!={exchange}",
                )
            )
            return
        if source not in sources:
            sources.append(source)

    for token in explicit:
        root, token_exchange = _parse_symbol(str(token))
        if root in explicit_roots:
            previous_exchange = explicit_exchanges.get(root, "")
            if token_exchange and previous_exchange and token_exchange != previous_exchange:
                selected.pop(root, None)
                blocked_roots.add(root)
                rejections.append(
                    SelectionRejection(
                        root_symbol=root,
                        stage="explicit",
                        reason_code="EXPLICIT_EXCHANGE_CONFLICT",
                        detail=f"{previous_exchange}!={token_exchange}",
                    )
                )
            elif token_exchange and not previous_exchange and root not in blocked_roots:
                explicit_exchanges[root] = token_exchange
                add(root, token_exchange, "explicit")
            continue
        explicit_roots.append(root)
        try:
            known_exchange = _resolve_exchange(
                root,
                ranked_by_root=ranked_by_root,
                day_root=Path(day_root),
                contract_reference=contract_reference,
                start=start,
                end=end,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            rejections.append(
                SelectionRejection(
                    root_symbol=root,
                    stage="explicit",
                    reason_code="EXCHANGE_RESOLUTION_ERROR",
                    detail=str(exc),
                )
            )
            continue
        if token_exchange and known_exchange and token_exchange != known_exchange:
            blocked_roots.add(root)
            rejections.append(
                SelectionRejection(
                    root_symbol=root,
                    stage="explicit",
                    reason_code="EXPLICIT_EXCHANGE_CONFLICT",
                    detail=f"{token_exchange}!={known_exchange}",
                )
            )
            continue
        exchange = token_exchange or known_exchange
        if not exchange:
            rejections.append(
                SelectionRejection(
                    root_symbol=root,
                    stage="explicit",
                    reason_code="UNRESOLVED_EXCHANGE",
                )
            )
            continue
        explicit_exchanges[root] = exchange
        add(root, exchange, "explicit")

    top_items = ranking[: min(top_n, len(ranking))]
    top_roots = tuple(item.root_symbol for item in top_items)
    for item in top_items:
        add(item.root_symbol, item.exchange, "top_n")

    eligible_roots: list[str] = []
    if include_ema_eligible:
        for item in ranking:
            try:
                eligible, reason = ema_eligible_in_interval(
                    Path(day_root) / f"{item.continuous_symbol}.csv",
                    start=start,
                    end=end,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                rejections.append(
                    SelectionRejection(
                        root_symbol=item.root_symbol,
                        stage="ema_eligible",
                        reason_code="DAY_DATA_ERROR",
                        detail=str(exc),
                    )
                )
                continue
            if reason:
                rejections.append(
                    SelectionRejection(
                        root_symbol=item.root_symbol,
                        stage="ema_eligible",
                        reason_code=reason,
                    )
                )
                continue
            if eligible:
                eligible_roots.append(item.root_symbol)
                add(item.root_symbol, item.exchange, "ema_eligible")

    selected_items = tuple(
        SelectedSymbol(root_symbol=root, exchange=exchange, sources=tuple(sources))
        for root, (exchange, sources) in selected.items()
    )
    return UniverseSelection(
        explicit=tuple(explicit_roots),
        top_n=top_roots,
        ema_eligible=tuple(eligible_roots),
        selected=selected_items,
        rejections=tuple(rejections),
    )


def ema_eligible_in_interval(
    path: Path,
    *,
    start: date,
    end: date,
) -> tuple[bool, str]:
    if not path.is_file():
        return False, "MISSING_DAY_DATA"
    frame = pd.read_csv(path)
    missing = sorted({"datetime", "close"}.difference(frame.columns))
    if missing:
        return False, "INVALID_DAY_SCHEMA"
    work = frame.loc[:, ["datetime", "close"]].copy()
    work["trade_date"] = pd.to_datetime(work["datetime"], errors="coerce").dt.date
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    valid = (
        work["trade_date"].notna()
        & np.isfinite(work["close"].to_numpy(float))
        & work["close"].gt(0)
    )
    if not bool(valid.all()) or work["trade_date"].duplicated().any():
        return False, "INVALID_DAY_DATA"
    work = work.sort_values("trade_date", kind="stable").reset_index(drop=True)
    for span in (1, 3, 5):
        work[f"ema{span}"] = work["close"].ewm(span=span, adjust=False).mean().shift(1)
    requested = work["trade_date"].map(lambda value: start <= value <= end)
    if not bool(requested.any()):
        return False, "NO_REQUEST_INTERVAL_DAY_DATA"
    eligible = (
        work["ema1"].gt(work["ema3"])
        & work["ema3"].gt(work["ema5"])
        & requested
    )
    if not bool(eligible.any()):
        return False, "NO_EMA_ELIGIBLE_DATE"
    return True, ""


def _resolve_exchange(
    root: str,
    *,
    ranked_by_root: Mapping[str, RankedSymbol],
    day_root: Path,
    contract_reference: pd.DataFrame,
    start: date,
    end: date,
) -> str:
    ranked = ranked_by_root.get(root)
    if ranked is not None:
        return ranked.exchange
    day_exchange = _exchange_from_day_file(day_root / f"{root}0.csv")
    if day_exchange:
        return day_exchange
    return _exchange_from_contract_reference(
        root,
        contract_reference=contract_reference,
        start=start,
        end=end,
    )


def _exchange_from_day_file(path: Path) -> str:
    if not path.is_file():
        return ""
    frame = pd.read_csv(path, usecols=lambda column: column == "exchange")
    if "exchange" not in frame:
        return ""
    exchanges = {
        _normalize_exchange(value)
        for value in frame["exchange"].dropna().astype(str)
        if str(value).strip()
    }
    return next(iter(exchanges)) if len(exchanges) == 1 else ""


def _exchange_from_contract_reference(
    root: str,
    *,
    contract_reference: pd.DataFrame,
    start: date,
    end: date,
) -> str:
    if contract_reference.empty or not {"root_symbol", "exchange"}.issubset(
        contract_reference.columns
    ):
        return ""
    frame = contract_reference.copy()
    frame["root_symbol"] = frame["root_symbol"].astype(str).str.upper().str.strip()
    frame = frame.loc[frame["root_symbol"].eq(root)].copy()
    if "list_date" in frame:
        listed = pd.to_datetime(frame["list_date"], errors="coerce")
        frame = frame.loc[listed.isna() | listed.le(pd.Timestamp(end))]
    if "delist_date" in frame:
        delisted = pd.to_datetime(frame["delist_date"], errors="coerce")
        frame = frame.loc[delisted.isna() | delisted.ge(pd.Timestamp(start))]
    exchanges = {
        _normalize_exchange(value)
        for value in frame["exchange"].dropna().astype(str)
        if str(value).strip()
    }
    return next(iter(exchanges)) if len(exchanges) == 1 else ""


def _parse_symbol(value: str) -> tuple[str, str]:
    token = value.strip().upper()
    match = _SYMBOL_PATTERN.fullmatch(token)
    if match is None:
        raise ValueError(f"invalid futures symbol: {value}")
    exchange = match.group("exchange") or ""
    return match.group("root"), _normalize_exchange(exchange) if exchange else ""


def _normalize_exchange(value: str) -> str:
    exchange = value.strip().upper()
    exchange = _EXCHANGE_ALIASES.get(exchange, exchange)
    if exchange not in SUPPORTED_EXCHANGES:
        raise ValueError(f"unsupported futures exchange: {value}")
    return exchange


__all__ = [
    "RankedSymbol",
    "SelectedSymbol",
    "SelectionRejection",
    "SUPPORTED_EXCHANGES",
    "UniverseSelection",
    "ema_eligible_in_interval",
    "load_ranked_symbols",
    "select_ranked_universe",
]
