"""Spread pair registry for cross-instrument/calendar arbitrage."""
from __future__ import annotations

from dataclasses import dataclass


_PAIR_TYPES = frozenset({"cross_instrument", "calendar"})


@dataclass(frozen=True)
class SpreadPair:
    """Static metadata for one tradable spread pair."""

    pair_key: str
    pair_type: str
    leg1_symbol: str
    leg2_symbol: str
    leg1_exchange: str
    leg2_exchange: str
    hedge_ratio: float = 1.0
    cluster: str = "other"
    description: str = ""

    def __post_init__(self) -> None:
        key = str(self.pair_key).strip().lower()
        pair_type = str(self.pair_type).strip().lower()
        leg1 = str(self.leg1_symbol).strip().upper()
        leg2 = str(self.leg2_symbol).strip().upper()
        ex1 = str(self.leg1_exchange).strip().upper()
        ex2 = str(self.leg2_exchange).strip().upper()
        cluster = str(self.cluster).strip().lower() or "other"
        ratio = float(self.hedge_ratio)
        if not key:
            raise ValueError("pair_key must not be empty")
        if pair_type not in _PAIR_TYPES:
            raise ValueError(f"pair_type must be one of {_PAIR_TYPES}, got {self.pair_type!r}")
        if not leg1 or not leg2:
            raise ValueError("leg symbols must not be empty")
        if not ex1 or not ex2:
            raise ValueError("leg exchanges must not be empty")
        if ratio <= 0.0:
            raise ValueError(f"hedge_ratio must be > 0, got {ratio}")
        object.__setattr__(self, "pair_key", key)
        object.__setattr__(self, "pair_type", pair_type)
        object.__setattr__(self, "leg1_symbol", leg1)
        object.__setattr__(self, "leg2_symbol", leg2)
        object.__setattr__(self, "leg1_exchange", ex1)
        object.__setattr__(self, "leg2_exchange", ex2)
        object.__setattr__(self, "cluster", cluster)
        object.__setattr__(self, "hedge_ratio", ratio)


DEFAULT_CROSS_INSTRUMENT_PAIRS: tuple[SpreadPair, ...] = (
    SpreadPair("rb_hc", "cross_instrument", "RB", "HC", "SHFE", "SHFE", 1.0, "black", "螺纹/热卷"),
    SpreadPair("m_rm", "cross_instrument", "M", "RM", "DCE", "CZCE", 1.0, "agri", "豆粕/菜粕"),
    SpreadPair("j_jm", "cross_instrument", "J", "JM", "DCE", "DCE", 1.0 / 1.3, "black", "焦炭/焦煤"),
    SpreadPair("cu_al", "cross_instrument", "CU", "AL", "SHFE", "SHFE", 1.0 / 5.0, "metal", "铜/铝"),
    SpreadPair("oi_y", "cross_instrument", "OI", "Y", "CZCE", "DCE", 1.0, "agri", "菜油/豆油"),
    SpreadPair("i_rb", "cross_instrument", "I", "RB", "DCE", "SHFE", 1.6, "black", "铁矿/螺纹"),
    SpreadPair("if_ic", "cross_instrument", "IF", "IC", "CFFEX", "CFFEX", 1.0, "index", "IF/IC"),
    SpreadPair("if_ih", "cross_instrument", "IF", "IH", "CFFEX", "CFFEX", 1.0, "index", "IF/IH"),
    SpreadPair("au_ag", "cross_instrument", "AU", "AG", "SHFE", "SHFE", 1.0 / 80.0, "precious", "金/银"),
    SpreadPair("t_tf", "cross_instrument", "T", "TF", "CFFEX", "CFFEX", 1.0 / 1.5, "bond", "10y/5y 国债"),
)

DEFAULT_CALENDAR_PAIRS: tuple[SpreadPair, ...] = (
    SpreadPair("rb_cal_1_3", "calendar", "RB_M1", "RB_M3", "SHFE", "SHFE", 1.0, "black", "RB近远月"),
    SpreadPair("i_cal_1_3", "calendar", "I_M1", "I_M3", "DCE", "DCE", 1.0, "black", "I近远月"),
    SpreadPair("au_cal_1_3", "calendar", "AU_M1", "AU_M3", "SHFE", "SHFE", 1.0, "precious", "AU近远月"),
    SpreadPair("cu_cal_1_3", "calendar", "CU_M1", "CU_M3", "SHFE", "SHFE", 1.0, "metal", "CU近远月"),
    SpreadPair("if_cal_1_3", "calendar", "IF_M1", "IF_M3", "CFFEX", "CFFEX", 1.0, "index", "IF近远月"),
)

DEFAULT_SPREAD_PAIRS: tuple[SpreadPair, ...] = (
    *DEFAULT_CROSS_INSTRUMENT_PAIRS,
    *DEFAULT_CALENDAR_PAIRS,
)

_PAIR_BY_KEY = {pair.pair_key: pair for pair in DEFAULT_SPREAD_PAIRS}


def get_spread_pair_by_key(pair_key: str) -> SpreadPair | None:
    """Lookup one pair by key."""
    return _PAIR_BY_KEY.get(str(pair_key).strip().lower())


__all__ = [
    "SpreadPair",
    "DEFAULT_CROSS_INSTRUMENT_PAIRS",
    "DEFAULT_CALENDAR_PAIRS",
    "DEFAULT_SPREAD_PAIRS",
    "get_spread_pair_by_key",
]

