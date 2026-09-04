"""Sector classification for Chinese commodity futures roots.

Used by the portfolio replay to report per-sector performance and to cap
concurrent exposure inside one highly correlated complex. Roots that are not
listed fall back to ``UNCLASSIFIED`` so a missing entry can never silently
merge two unrelated complexes into one bucket.
"""
from __future__ import annotations


UNCLASSIFIED_SECTOR = "UNCLASSIFIED"

SECTOR_BY_ROOT: dict[str, str] = {
    # 黑色建材
    "RB": "FERROUS",
    "HC": "FERROUS",
    "I": "FERROUS",
    "JM": "FERROUS",
    "J": "FERROUS",
    "SF": "FERROUS",
    "SM": "FERROUS",
    "SS": "FERROUS",
    "WR": "FERROUS",
    "FG": "BUILDING",
    "SA": "BUILDING",
    "UR": "BUILDING",
    "SH": "BUILDING",
    # 有色
    "CU": "BASE_METAL",
    "AL": "BASE_METAL",
    "ZN": "BASE_METAL",
    "PB": "BASE_METAL",
    "NI": "BASE_METAL",
    "SN": "BASE_METAL",
    "AO": "BASE_METAL",
    "BC": "BASE_METAL",
    "AD": "BASE_METAL",
    "LC": "NEW_ENERGY",
    "SI": "NEW_ENERGY",
    "PS": "NEW_ENERGY",
    # 贵金属
    "AU": "PRECIOUS",
    "AG": "PRECIOUS",
    "PL": "PRECIOUS",
    # 能源
    "SC": "ENERGY",
    "LU": "ENERGY",
    "FU": "ENERGY",
    "BU": "ENERGY",
    "PG": "ENERGY",
    "EC": "ENERGY",
    # 化工
    "MA": "CHEMICAL",
    "TA": "CHEMICAL",
    "EG": "CHEMICAL",
    "PP": "CHEMICAL",
    "L": "CHEMICAL",
    "V": "CHEMICAL",
    "EB": "CHEMICAL",
    "PF": "CHEMICAL",
    "PX": "CHEMICAL",
    "PR": "CHEMICAL",
    "BZ": "CHEMICAL",
    "SP": "CHEMICAL",
    "FB": "CHEMICAL",
    # 橡胶
    "RU": "RUBBER",
    "NR": "RUBBER",
    "BR": "RUBBER",
    # 油脂油料
    "M": "OILSEED",
    "RM": "OILSEED",
    "Y": "OILSEED",
    "P": "OILSEED",
    "OI": "OILSEED",
    "A": "OILSEED",
    "B": "OILSEED",
    "PK": "OILSEED",
    # 农产品
    "C": "AGRI",
    "CS": "AGRI",
    "CF": "AGRI",
    "CY": "AGRI",
    "SR": "AGRI",
    "JD": "AGRI",
    "LH": "AGRI",
    "AP": "AGRI",
    "CJ": "AGRI",
    "PT": "AGRI",
    "LG": "AGRI",
    "OP": "AGRI",
}


def sector_for_root(root_symbol: str) -> str:
    """Return the sector for ``root_symbol``, or ``UNCLASSIFIED``."""
    return SECTOR_BY_ROOT.get(str(root_symbol).strip().upper(), UNCLASSIFIED_SECTOR)


__all__ = ["SECTOR_BY_ROOT", "UNCLASSIFIED_SECTOR", "sector_for_root"]
