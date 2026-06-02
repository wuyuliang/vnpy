"""按 cluster (+ interval) 的真实 commission / slippage 成本表。

历史背景（2026-05-24 OOT 诊断 `cta/docs/review/20260524_profit_aware_postmortem.md` §7）：
  - 现有 OOT 在所有 5,200+ executed 行里 cost_pct 全是 3 bps（硬编码）。
  - 真实 Chinese commodity 单边 round-trip 应按品种分层，本表给出保守估计；
    实际生产用真实费率表/真实成交明细回放更准，但对回测口径足够。

数值来源（保守取值，含未来上调空间）：
  * commission：交易所手续费档位 + 期货公司加点（按品种白名单按万分点估）
  * slippage：流动性深度下"1-2 个 tick"占名义金额的比例
  * 这是 **单边** （open 或 close 各一次），双边请乘以 2 后在 pipeline 中扣减

interval 修正：
  * day 级用"标准 1 跳"slippage
  * 60min / 30min 用 1.3-1.5x slippage（深度浅）
  * commission 跟 interval 无关（按手数算）

引用：
  * 用于 `OotEvaluationConfig.commission_pct_by_cluster_interval` 和
    `slippage_pct_by_cluster_interval` 的初始化。
  * 仅 day 级 manifest 是"信心高"的，分钟级是 day × interval-multiplier 推算。
"""
from __future__ import annotations

import math
from typing import Final


# ===== day 级单边 commission（占名义金额比例） =====
# 例：black RB 主力 5000 点 × 10吨/手 = 50000 元名义；commission 0.45 万分点 + 期货公司 0.5 加点
#   合计 0.95 万分点 ≈ 0.0001 = 1 bp。这里用 0.00015 = 1.5 bp 留出加点空间。
CLUSTER_DAY_COMMISSION_PCT: Final[dict[str, float]] = {
    "bond":     0.00003,   # 国债 T/TF/TS：3 元/手 / 100k 名义 ≈ 0.3 bp
    "index":    0.00003,   # 股指 IF/IC/IH/IM：0.23 万分点（日内平今高）≈ 0.3 bp
    "precious": 0.00005,   # 贵金属 AU/AG：10-30 元/手 / 30-100k 名义 ≈ 0.5 bp
    "metal":    0.00010,   # 有色 CU/AL/ZN：0.5 万分点 ≈ 1 bp
    "black":    0.00015,   # 黑色 RB/HC/I：0.45-1 万分点 + 加点 ≈ 1.5 bp
    "chemical": 0.00010,   # 化工 MA/PP/V/L：0.5-1 万分点 ≈ 1 bp
    "agri":     0.00010,   # 农产品 M/Y/A/P：0.5-3 万分点（CF 高）取 1 bp
    "other":    0.00010,   # 默认 1 bp
}

# ===== day 级单边 slippage（occupy 1-2 tick / notional） =====
# 例：black RB 5000 点、1 tick = 1 点 → 1/5000 = 2 bp；保守取 3 bp
#   precious AU 480 元、1 tick = 0.02 → 0.02/480 ≈ 0.4 bp；取 1 bp
CLUSTER_DAY_SLIPPAGE_PCT: Final[dict[str, float]] = {
    "bond":     0.00005,   # 1 跳很小 → 0.5 bp
    "index":    0.00005,   # IF 0.2 / 4000 ≈ 0.5 bp
    "precious": 0.00010,   # AU 1 bp
    "metal":    0.00015,   # CU 1.5 bp
    "black":    0.00030,   # RB 黑色波动跳 ≈ 3 bp
    "chemical": 0.00020,   # MA/PP ≈ 2 bp
    "agri":     0.00020,   # 农产品 ≈ 2 bp
    "other":    0.00020,   # 默认 2 bp
}

# ===== fallback constants （manifest 未覆盖的 cluster 用这俩） =====
DEFAULT_COMMISSION_PCT: Final[float] = 0.00010   # 1 bp
DEFAULT_SLIPPAGE_PCT: Final[float] = 0.00020     # 2 bp

# ===== symbol 级 override（单边，占名义金额比例）=====
# 默认为空：生产可由 OotEvaluationConfig.commission_pct_by_symbol 注入真实费率。
COMMISSION_PCT_BY_SYMBOL: Final[dict[str, float]] = {}
SLIPPAGE_PCT_BY_SYMBOL: Final[dict[str, float]] = {}


# ===== interval 滑点倍数（commission 跟 interval 无关） =====
INTERVAL_SLIPPAGE_MULTIPLIER: Final[dict[str, float]] = {
    "day":    1.0,
    "60min":  1.3,
    "30min":  1.5,
    "15min":  1.7,
    "5min":   2.0,
    "min":    2.5,
}

DEFAULT_BUILD_INTERVALS: Final[tuple[str, ...]] = ("day", "60min", "30min")


def infer_commission_pct(cluster: str) -> float:
    """根据 cluster 返回单边 commission pct，未知 cluster 回退到 DEFAULT_COMMISSION_PCT。"""
    return CLUSTER_DAY_COMMISSION_PCT.get(str(cluster).strip().lower(), DEFAULT_COMMISSION_PCT)


def infer_slippage_pct(cluster: str, interval: str = "day") -> float:
    """根据 cluster + interval 返回单边 slippage pct。

    interval 用 INTERVAL_SLIPPAGE_MULTIPLIER 修正深度差异；未知 interval 走 1.0x。
    """
    base = CLUSTER_DAY_SLIPPAGE_PCT.get(str(cluster).strip().lower(), DEFAULT_SLIPPAGE_PCT)
    mult = INTERVAL_SLIPPAGE_MULTIPLIER.get(str(interval).strip().lower(), 1.0)
    return float(base * mult)


def build_cluster_interval_cost_dict(
    kind: str = "commission",
    intervals: tuple[str, ...] = DEFAULT_BUILD_INTERVALS,
) -> dict[str, float]:
    """生成 `cluster|interval -> pct` 字典，可直接喂给 OotEvaluationConfig 字段。

    kind: "commission" 或 "slippage"
    """
    if kind not in ("commission", "slippage"):
        raise ValueError(f"kind must be 'commission' or 'slippage', got {kind!r}")
    out: dict[str, float] = {}
    for cluster in CLUSTER_DAY_COMMISSION_PCT:
        for itv in intervals:
            key = f"{cluster}|{itv}"
            if kind == "commission":
                out[key] = infer_commission_pct(cluster)
            else:
                out[key] = infer_slippage_pct(cluster, itv)
    return out


def impact_cost_pct(order_lots: float, adv_lots: float, k: float = 0.10) -> float:
    """Estimate one-way impact cost from order participation in ADV lots.

    Formula: ``k * sqrt(order_lots / adv_lots)``. Missing or invalid ADV returns
    zero so historical evaluations fail open instead of inventing a penalty.
    """
    try:
        order = abs(float(order_lots))
        adv = float(adv_lots)
        coef = float(k)
    except (TypeError, ValueError):
        return 0.0
    if order <= 0.0 or adv <= 0.0 or coef <= 0.0:
        return 0.0
    return float(coef * math.sqrt(order / adv))


__all__ = [
    "CLUSTER_DAY_COMMISSION_PCT",
    "CLUSTER_DAY_SLIPPAGE_PCT",
    "DEFAULT_COMMISSION_PCT",
    "DEFAULT_SLIPPAGE_PCT",
    "COMMISSION_PCT_BY_SYMBOL",
    "SLIPPAGE_PCT_BY_SYMBOL",
    "INTERVAL_SLIPPAGE_MULTIPLIER",
    "DEFAULT_BUILD_INTERVALS",
    "infer_commission_pct",
    "infer_slippage_pct",
    "build_cluster_interval_cost_dict",
    "impact_cost_pct",
]
