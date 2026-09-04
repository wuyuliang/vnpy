"""Discover local futures roots without relying on directory naming conventions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd

from ..instruments.sessions import SessionSpec


_CONTRACT_PATTERN = re.compile(r"^(?P<root>[A-Z]+)\d+\.(?P<exchange>[A-Z]+)$")
_EXCHANGE_ALIASES = {
    "CFX": "CFFEX",
    "CZC": "CZCE",
    "GFE": "GFEX",
    "SHF": "SHFE",
    "ZCE": "CZCE",
}


@dataclass(frozen=True)
class DiscoveredSymbol:
    root_symbol: str
    exchange: str
    vt_symbol: str
    source_directory: Path
    alias_directories: tuple[Path, ...] = ()


@dataclass(frozen=True)
class LoadedSymbol:
    root_symbol: str
    exchange: str
    vt_symbol: str
    minute_bars: pd.DataFrame
    sessions: tuple[SessionSpec, ...]
    source_files: tuple[Path, ...]
    overlapping_rows_removed: int = 0

    def __post_init__(self) -> None:
        if not self.root_symbol or not self.exchange or not self.vt_symbol:
            raise ValueError("loaded symbol identity is required")
        if not self.sessions:
            raise ValueError("loaded symbol sessions are required")
        if self.overlapping_rows_removed < 0:
            raise ValueError("overlapping_rows_removed must be nonnegative")


def discover_symbols(data_root: str | Path) -> tuple[DiscoveredSymbol, ...]:
    root = Path(data_root)
    if not root.is_dir():
        raise FileNotFoundError(f"minute data root does not exist: {root}")
    candidates: dict[tuple[str, str], list[tuple[Path, int]]] = {}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        files = tuple(sorted(directory.glob("*.parquet")))
        if not files:
            continue
        identity = _read_identity(files)
        if identity is None:
            continue
        candidates.setdefault(identity, []).append((directory, len(files)))
    discovered: list[DiscoveredSymbol] = []
    for (root_symbol, exchange), directories in sorted(candidates.items()):
        ranked = sorted(directories, key=lambda item: (-item[1], str(item[0])))
        primary = ranked[0][0]
        aliases = tuple(sorted((item[0] for item in ranked[1:]), key=str))
        discovered.append(
            DiscoveredSymbol(
                root_symbol=root_symbol,
                exchange=exchange,
                vt_symbol=f"{root_symbol}0.{exchange}",
                source_directory=primary,
                alias_directories=aliases,
            )
        )
    if not discovered:
        raise ValueError(f"no recognizable futures parquet data under {root}")
    return tuple(discovered)


def _read_identity(files: tuple[Path, ...]) -> tuple[str, str] | None:
    for path in files:
        frame = pd.read_parquet(path)
        if frame.empty:
            continue
        contract_column = "contract_code" if "contract_code" in frame else "ts_code"
        if contract_column not in frame:
            return None
        values = frame[contract_column].dropna().astype(str).str.upper().str.strip()
        identities = {_identity(value) for value in values}
        identities.discard(None)
        if len(identities) != 1:
            raise ValueError(f"source directory has mixed futures roots: {path.parent}")
        return next(iter(identities)) if identities else None
    return None


def _identity(contract_code: str) -> tuple[str, str] | None:
    match = _CONTRACT_PATTERN.fullmatch(contract_code)
    if match is None:
        return None
    exchange = _EXCHANGE_ALIASES.get(match.group("exchange"), match.group("exchange"))
    return match.group("root"), exchange


__all__ = ["DiscoveredSymbol", "LoadedSymbol", "discover_symbols"]
