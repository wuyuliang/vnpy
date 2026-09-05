"""Chinese display names for futures roots.

The names already exist in the research ranking CSV (``螺纹钢连续`` for ``RB0``);
this module turns that column into a root -> name lookup so charts and reports
can label ``RB`` as ``螺纹钢 RB`` without every caller re-parsing the file.

The lookup fails open: an unknown root returns ``""`` and the caller keeps the
plain symbol, because a missing display name must never break a render.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
import re

DEFAULT_RANKING_CSV = Path("cta/feature/symbols_research_ranking.csv")

# 排名表里的名字是"连续合约"口径，图上只想要品种本名
_CONTINUOUS_SUFFIXES = ("连续", "主连", "指数", "连一", "连三")
_TRAILING_DIGITS = re.compile(r"\d+$")


def root_of(symbol: str) -> str:
    """``RB0`` / ``RB2610.SHF`` / ``rb`` -> ``RB``."""
    token = str(symbol).strip().upper().split(".")[0]
    return _TRAILING_DIGITS.sub("", token)


@lru_cache(maxsize=8)
def _names_by_root(ranking_csv: str) -> dict[str, str]:
    path = Path(ranking_csv)
    if not path.is_file():
        return {}
    names: dict[str, str] = {}
    try:
        # 排名表带 BOM，utf-8-sig 才能把第一列读成 symbol
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                root = root_of(row.get("symbol", ""))
                name = str(row.get("name", "") or "").strip()
                if not root or not name:
                    continue
                for suffix in _CONTINUOUS_SUFFIXES:
                    if name.endswith(suffix) and len(name) > len(suffix):
                        name = name[: -len(suffix)]
                        break
                names.setdefault(root, name)
    except (OSError, UnicodeError, csv.Error):
        return {}
    return names


def chinese_name_for_root(
    symbol: str,
    *,
    ranking_csv: str | Path = DEFAULT_RANKING_CSV,
) -> str:
    """Return the Chinese name for ``symbol``'s root, or ``""`` when unknown."""
    return _names_by_root(str(ranking_csv)).get(root_of(symbol), "")


def labelled_symbol(
    symbol: str,
    *,
    ranking_csv: str | Path = DEFAULT_RANKING_CSV,
) -> str:
    """``RB`` -> ``螺纹钢 RB``; an unknown root stays as it came in."""
    token = str(symbol).strip()
    name = chinese_name_for_root(token, ranking_csv=ranking_csv)
    return f"{name} {token}" if name else token
