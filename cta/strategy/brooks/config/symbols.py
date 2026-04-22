"""v3 品种解析:从 cta/feature/symbols_research_ranking.csv 按顺序选择前 N 个。

规则:
1. 读 ranking CSV → 按 research_rank 升序
2. max_tier 过滤(A < B < C < D)
3. explicit_exclude 剔除
4. require_feature_interval 过滤(feature_loader.list_symbol_dates 非空)
5. 截取前 top_n
6. explicit_include 追加

合约元数据优先从内置 V3_SYMBOL_META 查(覆盖 A 档前 20 个);否则回退到
ContractDefaultsCfg 的默认值。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from cta.strategy.brooks.config.params import ContractDefaultsCfg, SymbolsCfg

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]

TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


@dataclass(frozen=True)
class SymbolMeta:
    vt_symbol: str        # e.g. "RB0.SHFE"
    name: str
    size: float           # contract multiplier
    rate: float           # commission rate (pct)
    slippage: float       # price units
    pricetick: float      # price tick
    tier: str = "A"
    research_rank: int = 999


# A 档前 20 个常用品种的合约参数(从公开市场 + cta/config/futures_meta.py 汇总)
# slippage / pricetick 单位为人民币元(每一个 tick 的实际价格)。
V3_SYMBOL_META: dict[str, SymbolMeta] = {
    "RB0.SHFE": SymbolMeta("RB0.SHFE", "螺纹钢主连", 10, 0.0001, 1.0, 1.0),
    "HC0.SHFE": SymbolMeta("HC0.SHFE", "热卷主连",  10, 0.0001, 1.0, 1.0),
    "I0.DCE":   SymbolMeta("I0.DCE",   "铁矿主连",  100, 0.0001, 0.5, 0.5),
    "JM0.DCE":  SymbolMeta("JM0.DCE",  "焦煤主连",  60, 0.0001, 0.5, 0.5),
    "J0.DCE":   SymbolMeta("J0.DCE",   "焦炭主连",  100, 0.0001, 0.5, 0.5),
    "M0.DCE":   SymbolMeta("M0.DCE",   "豆粕主连",  10, 0.0001, 1.0, 1.0),
    "P0.DCE":   SymbolMeta("P0.DCE",   "棕榈油主连", 10, 0.0001, 2.0, 2.0),
    "Y0.DCE":   SymbolMeta("Y0.DCE",   "豆油主连",  10, 0.0001, 2.0, 2.0),
    "OI0.CZCE": SymbolMeta("OI0.CZCE", "菜油主连",  10, 0.0001, 1.0, 1.0),
    "MA0.CZCE": SymbolMeta("MA0.CZCE", "甲醇主连",  10, 0.0001, 1.0, 1.0),
    "TA0.CZCE": SymbolMeta("TA0.CZCE", "PTA主连",   5, 0.0001, 2.0, 2.0),
    "EG0.DCE":  SymbolMeta("EG0.DCE",  "乙二醇主连", 10, 0.0001, 1.0, 1.0),
    "PP0.DCE":  SymbolMeta("PP0.DCE",  "聚丙烯主连", 5, 0.0001, 1.0, 1.0),
    "L0.DCE":   SymbolMeta("L0.DCE",   "塑料主连",  5, 0.0001, 1.0, 1.0),
    "V0.DCE":   SymbolMeta("V0.DCE",   "PVC主连",   5, 0.0001, 1.0, 1.0),
    "CU0.SHFE": SymbolMeta("CU0.SHFE", "沪铜主连",  5, 0.0001, 10.0, 10.0),
    "AL0.SHFE": SymbolMeta("AL0.SHFE", "沪铝主连",  5, 0.0001, 5.0, 5.0),
    "ZN0.SHFE": SymbolMeta("ZN0.SHFE", "沪锌主连",  5, 0.0001, 5.0, 5.0),
    "AU0.SHFE": SymbolMeta("AU0.SHFE", "黄金主连",  1000, 0.0001, 0.02, 0.02),
    "AG0.SHFE": SymbolMeta("AG0.SHFE", "白银主连",  15, 0.0001, 1.0, 1.0),
}


def _to_vt(symbol: str, exchange: str) -> str:
    return f"{symbol}.{exchange}"


@lru_cache(maxsize=8)
def _read_ranking(csv_path: str) -> pd.DataFrame:
    p = Path(csv_path)
    if not p.is_absolute():
        p = REPO_ROOT / csv_path
    df = pd.read_csv(p)
    df["vt_symbol"] = df.apply(lambda r: _to_vt(r["symbol"], r["exchange"]), axis=1)
    df = df.sort_values("research_rank").reset_index(drop=True)
    return df


def resolve_symbols(cfg: SymbolsCfg) -> list[str]:
    """按 cfg 规则解析出最终 vt_symbol 列表。

    返回顺序同 ranking(前 top_n) + explicit_include(追加)。
    """
    from cta.feature.feature_loader import list_symbol_dates

    df = _read_ranking(cfg.ranking_csv)

    # 1) 档位过滤
    max_tier_idx = TIER_ORDER.get(cfg.max_tier.upper(), 3)
    df = df[df["tier"].map(lambda t: TIER_ORDER.get(str(t).upper(), 99) <= max_tier_idx)]

    # 2) explicit_exclude
    excluded = set(cfg.explicit_exclude)
    df = df[~df["vt_symbol"].isin(excluded)]

    # 3) require_feature_interval: feature 已生成才保留
    if cfg.require_feature_interval:
        interval = cfg.require_feature_interval
        keep: list[bool] = []
        for sym in df["symbol"].tolist():
            dates = list_symbol_dates(sym, interval)
            keep.append(len(dates) > 0)
        df = df[keep]

    # 4) 截取前 top_n
    picked = df["vt_symbol"].head(cfg.top_n).tolist()

    # 5) explicit_include 追加(不重复)
    for vt in cfg.explicit_include:
        if vt not in picked:
            picked.append(vt)

    logger.info(
        "resolve_symbols: top_n=%s max_tier=%s require=%s → %s",
        cfg.top_n, cfg.max_tier, cfg.require_feature_interval, picked,
    )
    return picked


def get_symbol_meta(
    vt_symbol: str,
    defaults: ContractDefaultsCfg | None = None,
) -> SymbolMeta:
    """查合约元数据;未配置则返回默认值并 warning。"""
    if vt_symbol in V3_SYMBOL_META:
        return V3_SYMBOL_META[vt_symbol]
    if defaults is None:
        defaults = ContractDefaultsCfg()
    logger.warning(
        "vt_symbol=%s 未在 V3_SYMBOL_META 中,使用默认合约参数", vt_symbol,
    )
    return SymbolMeta(
        vt_symbol=vt_symbol,
        name=vt_symbol,
        size=defaults.default_size,
        rate=defaults.default_rate,
        slippage=defaults.default_slippage,
        pricetick=defaults.default_pricetick,
    )


def vt_to_symbol_exchange(vt_symbol: str) -> tuple[str, str]:
    """'RB0.SHFE' → ('RB0', 'SHFE')"""
    symbol, exchange = vt_symbol.split(".", 1)
    return symbol, exchange


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="v3 品种解析工具")
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--max-tier", default="B")
    parser.add_argument("--interval", default="minute5",
                        help="require_feature_interval, 传 'none' 关闭")
    args = parser.parse_args()

    cfg = SymbolsCfg(
        ranking_csv="cta/feature/symbols_research_ranking.csv",
        top_n=args.top_n,
        max_tier=args.max_tier,
        require_feature_interval=(None if args.interval.lower() == "none"
                                  else args.interval),
    )
    syms = resolve_symbols(cfg)
    print(f"\n{len(syms)} symbols:")
    for s in syms:
        m = get_symbol_meta(s)
        print(f"  {s:12s} size={m.size:<6} tick={m.pricetick:<5} name={m.name}")
