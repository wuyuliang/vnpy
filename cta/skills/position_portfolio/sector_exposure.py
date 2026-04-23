"""§07-03 sector exposure control."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal


Sector = Literal["black", "nonferrous", "chemicals", "agri", "precious"]


@dataclass
class SectorExposure:
    sector: Sector
    net_risk_pct: float
    gross_risk_pct: float
    same_dir_count: int


def _symbol_root(symbol: str) -> str:
    return re.sub(r"[^a-z]", "", str(symbol).lower())


def _sector_of(symbol: str) -> Sector:
    root = _symbol_root(symbol)
    if root.startswith(("rb", "hc", "i", "j", "jm", "sf", "sm")):
        return "black"
    if root.startswith(("cu", "al", "zn", "pb", "ni", "sn")):
        return "nonferrous"
    if root.startswith(("ta", "ma", "pp", "l", "v", "eg", "ru", "fu", "bu")):
        return "chemicals"
    if root.startswith(("m", "y", "a", "c", "cs", "p", "rm", "oi", "cf", "sr")):
        return "agri"
    if root.startswith(("au", "ag")):
        return "precious"
    return "chemicals"


def compute_portfolio_exposure(
    positions: list[dict],
    equity: float,
) -> dict[Sector, SectorExposure]:
    """
    Aggregate sector exposures from position risk amounts.

    Position dict schema:
    symbol, direction(long/short), risk_amount
    """
    eq = max(float(equity), 1e-9)
    net: dict[Sector, float] = defaultdict(float)
    gross: dict[Sector, float] = defaultdict(float)
    long_n: dict[Sector, int] = defaultdict(int)
    short_n: dict[Sector, int] = defaultdict(int)

    for pos in positions:
        sector = _sector_of(str(pos.get("symbol", "")))
        direction = str(pos.get("direction", "long")).lower()
        risk_amount = abs(float(pos.get("risk_amount", 0.0)))
        sign = 1.0 if direction == "long" else -1.0
        net[sector] += sign * risk_amount
        gross[sector] += risk_amount
        if direction == "long":
            long_n[sector] += 1
        else:
            short_n[sector] += 1

    out: dict[Sector, SectorExposure] = {}
    for sector in set(net.keys()) | set(gross.keys()):
        same_dir_count = max(long_n.get(sector, 0), short_n.get(sector, 0))
        out[sector] = SectorExposure(
            sector=sector,
            net_risk_pct=net.get(sector, 0.0) / eq,
            gross_risk_pct=gross.get(sector, 0.0) / eq,
            same_dir_count=int(same_dir_count),
        )
    return out


def sector_cap_gate(
    new_order: dict,
    current_exposure: dict[Sector, SectorExposure],
    max_sector_net: float = 0.01,
    max_same_dir: int = 3,
) -> bool:
    """True means pass gate."""
    sector = _sector_of(str(new_order.get("symbol", "")))
    direction = str(new_order.get("direction", "long")).lower()
    sign = 1.0 if direction == "long" else -1.0
    add_pct = float(new_order.get("risk_pct", 0.0))
    cur = current_exposure.get(
        sector,
        SectorExposure(sector=sector, net_risk_pct=0.0, gross_risk_pct=0.0, same_dir_count=0),
    )

    if abs(cur.net_risk_pct) >= float(max_sector_net):
        # if already over cap, reject same-side orders.
        if cur.net_risk_pct * sign >= 0:
            return False
    if cur.same_dir_count >= int(max_same_dir):
        if cur.net_risk_pct * sign >= 0:
            return False

    projected_net = cur.net_risk_pct + sign * add_pct
    if abs(projected_net) > float(max_sector_net):
        return False
    return True

