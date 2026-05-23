"""Resolve main/secondary contracts for calendar spread pipelines."""
from __future__ import annotations

from dataclasses import dataclass
import re

import pandas as pd


_CONTRACT_PATTERN = re.compile(r"^([A-Z]+)(\d{4})(?:\.([A-Z]+))?$")


@dataclass(frozen=True)
class ParsedContractCode:
    """Parsed components from a futures contract code."""

    prefix: str
    year: int
    month: int
    exchange_suffix: str = ""

    @property
    def yyyymm(self) -> int:
        return int(self.year * 100 + self.month)


def parse_contract_code(contract_code: str) -> ParsedContractCode:
    """Parse code like ``RB2401.SHF``."""
    raw = str(contract_code).strip().upper()
    m = _CONTRACT_PATTERN.match(raw)
    if m is None:
        raise ValueError(f"invalid contract code: {contract_code!r}")
    prefix = str(m.group(1))
    yy_mm = str(m.group(2))
    year = 2000 + int(yy_mm[:2])
    month = int(yy_mm[2:])
    if not (1 <= month <= 12):
        raise ValueError(f"invalid contract month in {contract_code!r}")
    suffix = str(m.group(3) or "").upper()
    return ParsedContractCode(prefix=prefix, year=year, month=month, exchange_suffix=suffix)


def format_contract_code(parsed: ParsedContractCode) -> str:
    """Build standard code from parsed components."""
    yy = int(parsed.year) % 100
    core = f"{parsed.prefix}{yy:02d}{int(parsed.month):02d}"
    if parsed.exchange_suffix:
        return f"{core}.{parsed.exchange_suffix}"
    return core


def add_months_to_contract(contract_code: str, months_ahead: int) -> str:
    """Shift a contract code by N calendar months."""
    parsed = parse_contract_code(contract_code)
    delta = int(months_ahead)
    if delta < 0:
        raise ValueError("months_ahead must be >= 0")
    y = int(parsed.year)
    m = int(parsed.month)
    m0 = (m - 1) + delta
    y += m0 // 12
    m = (m0 % 12) + 1
    return format_contract_code(
        ParsedContractCode(
            prefix=parsed.prefix,
            year=y,
            month=m,
            exchange_suffix=parsed.exchange_suffix,
        )
    )


def resolve_main_secondary_by_mapping(
    mapping_df: pd.DataFrame,
    *,
    months_ahead: int = 2,
) -> pd.DataFrame:
    """Resolve daily main+secondary contracts from `fut_mapping` output.

    Input columns:
    - ``trade_date``
    - ``mapping_ts_code``
    """
    if mapping_df is None or mapping_df.empty:
        return pd.DataFrame(
            columns=["trade_date", "main_contract_code", "secondary_contract_code"]
        )
    if "trade_date" not in mapping_df.columns or "mapping_ts_code" not in mapping_df.columns:
        raise KeyError("mapping_df must contain trade_date and mapping_ts_code")

    out = mapping_df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out["main_contract_code"] = out["mapping_ts_code"].astype(str).str.strip().str.upper()
    out = out.dropna(subset=["trade_date", "main_contract_code"]).copy()
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    out["secondary_contract_code"] = out["main_contract_code"].map(
        lambda code: add_months_to_contract(str(code), int(months_ahead))
    )
    out = (
        out[["trade_date", "main_contract_code", "secondary_contract_code"]]
        .drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    return out


__all__ = [
    "ParsedContractCode",
    "parse_contract_code",
    "format_contract_code",
    "add_months_to_contract",
    "resolve_main_secondary_by_mapping",
]

