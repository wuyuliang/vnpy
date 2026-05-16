"""Symbol cluster mapping and default roll-cost assumptions."""
from __future__ import annotations

from typing import Final


SYMBOL_CLUSTER_BY_PREFIX: Final[dict[str, str]] = {
    # 黑色系
    "RB": "black",
    "HC": "black",
    "I": "black",
    "J": "black",
    "JM": "black",
    # 有色
    "CU": "metal",
    "AL": "metal",
    "ZN": "metal",
    "NI": "metal",
    "SN": "metal",
    # 化工
    "TA": "chemical",
    "MA": "chemical",
    "PP": "chemical",
    "V": "chemical",
    "EG": "chemical",
    "L": "chemical",
    # 农产品 / 油脂
    "M": "agri",
    "Y": "agri",
    "P": "agri",
    "OI": "agri",
    "CF": "agri",
    "SR": "agri",
    # 贵金属
    "AU": "precious",
    "AG": "precious",
    # 股指
    "IF": "index",
    "IH": "index",
    "IC": "index",
    "IM": "index",
    # 国债
    "T": "bond",
    "TF": "bond",
    "TS": "bond",
}


CLUSTER_DEFAULT_ROLL_COST_PCT_PER_YEAR: Final[dict[str, float]] = {
    "index": 0.02,
    "black": 0.06,
    "metal": 0.05,
    "chemical": 0.05,
    "agri": 0.08,
    "precious": 0.03,
    "bond": 0.01,
    "other": 0.04,
}


# 涨跌停板比例（按 cluster 估的保守值，覆盖 80% 场景）。
# 实战里每个交易所对每个品种都有独立的涨跌停 board，且会节假日临时上调；
# 这里只是给"模型可见的特征"提供合理 proxy，不替代真实交易接口的 limit board。
CLUSTER_LIMIT_PCT: Final[dict[str, float]] = {
    "index": 0.10,     # 股指 10%
    "black": 0.06,     # 黑色系 4-7%
    "metal": 0.05,     # 有色 4-6%
    "chemical": 0.05,  # 化工 4-7%
    "agri": 0.07,      # 农产品 4-8%
    "precious": 0.05,  # 贵金属 4-6%
    "bond": 0.02,      # 国债 2%
    "other": 0.05,
}


def _symbol_prefix(symbol: str) -> str:
    s = str(symbol).strip().upper()
    if not s:
        return ""
    alpha = "".join(ch for ch in s if ch.isalpha())
    if not alpha:
        return ""
    if len(alpha) >= 2 and alpha[:2] in SYMBOL_CLUSTER_BY_PREFIX:
        return alpha[:2]
    return alpha[:1]


def infer_symbol_cluster(symbol: str) -> str:
    """Infer symbol cluster from symbol prefix."""
    pref = _symbol_prefix(symbol)
    if not pref:
        return "other"
    return SYMBOL_CLUSTER_BY_PREFIX.get(pref, "other")


def infer_symbol_roll_cost_pct(symbol: str, default_pct: float = 0.0) -> float:
    """Infer annualized roll cost pct from symbol cluster."""
    cluster = infer_symbol_cluster(symbol)
    if cluster in CLUSTER_DEFAULT_ROLL_COST_PCT_PER_YEAR:
        return float(CLUSTER_DEFAULT_ROLL_COST_PCT_PER_YEAR[cluster])
    return float(default_pct)


def infer_symbol_limit_pct(symbol: str, default_pct: float = 0.05) -> float:
    """Infer per-day price-limit pct (涨跌停比例) from symbol cluster."""
    cluster = infer_symbol_cluster(symbol)
    if cluster in CLUSTER_LIMIT_PCT:
        return float(CLUSTER_LIMIT_PCT[cluster])
    return float(default_pct)


__all__ = [
    "SYMBOL_CLUSTER_BY_PREFIX",
    "CLUSTER_DEFAULT_ROLL_COST_PCT_PER_YEAR",
    "CLUSTER_LIMIT_PCT",
    "infer_symbol_cluster",
    "infer_symbol_roll_cost_pct",
    "infer_symbol_limit_pct",
]

