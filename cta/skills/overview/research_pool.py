"""§02 研究边界 / Research Boundary —— 研究池解析.

对应 cta/cta_skills/00_overview_methodology/02_research_boundary.md §5-§6。

功能
----
1. ResearchPool: 解析 research_pool.yaml + cta/feature/symbols_research_ranking.csv
   的组合结果。
2. in_research_pool(symbol, tier): 某 symbol 是否在指定 tier。
3. resolve_research_symbols(tier, interval): 返回按 research_rank 升序的
   `[{symbol, exchange, interval, research_rank}, ...]`；若指定 interval，
   还会过滤出**该 interval 下已有 feature 落盘**的品种（coverage 检查）。

多周期兼容
----------
- 每个 tier 有默认 intervals 列表；调用方可覆盖
- Feature coverage 检查用 cta/data/feature/{interval}/{SYMBOL}/*.parquet 是否存在
  （与 cta/feature/run_all_features.py 的布局对齐）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd
import yaml

from cta.feature.loader import load_symbols_ranked, normalize_interval
from cta.skills import CANON_INTERVALS, CONFIG_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

DEFAULT_POOL_YAML: Path = CONFIG_DIR / "research_pool.yaml"
FEATURE_DIR: Path = PROJECT_ROOT / "cta" / "data" / "feature"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PoolMember:
    symbol: str
    exchange: str
    research_rank: int
    tier: str                               # 'A' | 'B'

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"


@dataclass
class ResearchPool:
    """研究池的解析结果。不可变配置，按需构造，可用 lru_cache。"""
    tier_a: List[PoolMember] = field(default_factory=list)
    tier_b: List[PoolMember] = field(default_factory=list)
    intervals_by_tier: Dict[str, List[str]] = field(default_factory=dict)
    coverage_threshold: float = 0.8

    # ---- 查询 ----
    def symbols(self, tier: str = "A") -> List[str]:
        """按 research_rank 升序返回 tier 内 symbol。"""
        members = self._members(tier)
        return [m.symbol for m in members]

    def vt_symbols(self, tier: str = "A") -> List[str]:
        return [m.vt_symbol for m in self._members(tier)]

    def members(self, tier: str = "A") -> List[PoolMember]:
        return list(self._members(tier))

    def contains(self, symbol: str, tier: str = "A") -> bool:
        return symbol.upper() in {m.symbol for m in self._members(tier)}

    def intervals(self, tier: str = "A") -> List[str]:
        tier = tier.upper()
        lst = self.intervals_by_tier.get(tier) or self.intervals_by_tier.get("A") or []
        return [normalize_interval(i) for i in lst]

    # ---- internal ----
    def _members(self, tier: str) -> Sequence[PoolMember]:
        tier = tier.upper()
        if tier == "A":
            return self.tier_a
        if tier == "B":
            # B 档 = B 独有成员（排名 > A.max_rank 的那批）
            return self.tier_b
        if tier in ("A+B", "AB", "ALL"):
            return list(self.tier_a) + list(self.tier_b)
        raise ValueError(f"未知 tier: {tier}（合法值: A / B / A+B）")


# ---------------------------------------------------------------------------
# 配置读取 + 构造
# ---------------------------------------------------------------------------
def _load_yaml(path: Path) -> Dict:
    if not path.exists():
        raise FileNotFoundError(f"research_pool 配置不存在: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _validate_intervals(intervals: Iterable[str]) -> List[str]:
    bad = [i for i in intervals if normalize_interval(i) not in CANON_INTERVALS]
    if bad:
        raise ValueError(
            f"intervals 含不认识的周期 {bad}，合法值: {CANON_INTERVALS}"
        )
    return [normalize_interval(i) for i in intervals]


@lru_cache(maxsize=4)
def build_research_pool(yaml_path: str | None = None) -> ResearchPool:
    """
    合并 yaml + ranking CSV 构造 ResearchPool。
    lru_cache 允许按 yaml 路径缓存；传不同 path 走不同缓存项。
    """
    path = Path(yaml_path) if yaml_path else DEFAULT_POOL_YAML
    cfg = _load_yaml(path)

    tiers = cfg.get("tiers", {}) or {}
    a_max = int((tiers.get("A") or {}).get("max_rank", 0))
    b_max = int((tiers.get("B") or {}).get("max_rank", 0))
    if a_max <= 0:
        raise ValueError(f"{path.name}::tiers.A.max_rank 必须 > 0")
    if b_max < a_max:
        raise ValueError(
            f"{path.name}::tiers.B.max_rank ({b_max}) 不能小于 A.max_rank ({a_max})"
        )

    explicit_include = {s.upper() for s in (cfg.get("explicit_include") or [])}
    explicit_exclude = {s.upper() for s in (cfg.get("explicit_exclude") or [])}

    intervals_map_raw: Dict = cfg.get("intervals") or {}
    intervals_by_tier = {
        k.upper(): _validate_intervals(v or [])
        for k, v in intervals_map_raw.items()
    }

    coverage_threshold = float(
        (cfg.get("coverage") or {}).get("min_feature_ratio", 0.8)
    )

    # 读 ranking CSV，按 rank 升序
    ranking = load_symbols_ranked()

    tier_a: List[PoolMember] = []
    tier_b: List[PoolMember] = []
    for _, row in ranking.iterrows():
        sym = str(row["symbol"]).upper()
        vt = f"{sym}.{str(row['exchange']).upper()}"
        if vt in explicit_exclude or sym in explicit_exclude:
            continue
        rank = int(row["research_rank"])
        tag: Optional[str] = None
        if rank <= a_max:
            tag = "A"
        elif rank <= b_max:
            tag = "B"
        # explicit_include: 无论 rank 多大都塞进指定 tier（默认进 B）
        if tag is None and (vt in explicit_include or sym in explicit_include):
            tag = "B"
        if tag is None:
            continue
        mem = PoolMember(
            symbol=sym,
            exchange=str(row["exchange"]).upper(),
            research_rank=rank,
            tier=tag,
        )
        (tier_a if tag == "A" else tier_b).append(mem)

    # explicit_include 里排名很前的（<= a_max）已经进了 A；这里处理漏网
    for vt in explicit_include:
        bare = vt.split(".", 1)[0]
        if any(m.symbol == bare for m in tier_a + tier_b):
            continue
        # 在 ranking 里找元数据；找不到则用兜底
        match = ranking[ranking["symbol"] == bare]
        if match.empty:
            logger.warning(f"explicit_include {vt!r} 不在 ranking CSV 里，跳过")
            continue
        r0 = match.iloc[0]
        tier_b.append(PoolMember(
            symbol=bare,
            exchange=str(r0["exchange"]).upper(),
            research_rank=int(r0["research_rank"]),
            tier="B",
        ))

    tier_a.sort(key=lambda m: m.research_rank)
    tier_b.sort(key=lambda m: m.research_rank)

    pool = ResearchPool(
        tier_a=tier_a,
        tier_b=tier_b,
        intervals_by_tier=intervals_by_tier,
        coverage_threshold=coverage_threshold,
    )
    logger.info(
        "ResearchPool built: A=%d, B=%d (from %s)",
        len(tier_a), len(tier_b), path.name,
    )
    return pool


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def in_research_pool(
    symbol: str,
    tier: str = "A",
    yaml_path: str | None = None,
) -> bool:
    """某 symbol 是否在指定 tier 的研究池中（symbol 不含交易所后缀）。"""
    return build_research_pool(yaml_path).contains(symbol, tier)


def _has_feature_coverage(
    symbol: str,
    interval: str,
    feature_root: Path = FEATURE_DIR,
) -> bool:
    """cta/data/feature/{interval}/{SYMBOL}/*.parquet 是否有至少 1 个文件。"""
    d = feature_root / normalize_interval(interval) / symbol.upper()
    if not d.exists():
        return False
    try:
        next(d.glob("*.parquet"))
        return True
    except StopIteration:
        return False


def resolve_research_symbols(
    tier: str = "A",
    interval: str | None = None,
    require_feature: bool = False,
    yaml_path: str | None = None,
) -> List[Dict[str, object]]:
    """
    按 research_rank 升序返回研究池成员，可选按 interval 过滤 feature 覆盖。

    Parameters
    ----------
    tier : 'A' | 'B' | 'A+B'
    interval : 可选，规范名 day/minute/minute5/... 或老别名 5min/...
    require_feature : True 时剔除在该 interval 下无 feature parquet 的品种
    yaml_path : 可选，覆盖 research_pool.yaml 路径（测试用）

    Returns
    -------
    [{symbol, exchange, research_rank, tier, interval?}, ...]
    """
    pool = build_research_pool(yaml_path)
    members = pool.members(tier)

    out: List[Dict[str, object]] = []
    canon_interval = normalize_interval(interval) if interval else None
    for m in members:
        entry: Dict[str, object] = {
            "symbol": m.symbol,
            "exchange": m.exchange,
            "research_rank": m.research_rank,
            "tier": m.tier,
        }
        if canon_interval:
            entry["interval"] = canon_interval
            if require_feature and not _has_feature_coverage(m.symbol, canon_interval):
                continue
        out.append(entry)
    return out


def reset_cache() -> None:
    """清 ResearchPool 的 lru_cache；在测试之间切换 yaml 时使用。"""
    build_research_pool.cache_clear()
