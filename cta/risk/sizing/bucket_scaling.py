"""Bucket PnL 缩仓器（子系统②的 sizing 面）。

规则：
    pnl_bp 阶梯（默认 (-5, -10)）+ mults 阶梯（默认 (0.9, 0.7, 0.5)）：
        pnl_bp >= 0           → mult = 1.0
        pnl_bp in [-5, 0)     → mult = 0.9
        pnl_bp in [-10, -5)   → mult = 0.7
        pnl_bp < -10          → mult = 0.5
        trade_count < min     → mult = 1.0（样本不足，不动手）
        floor                 → 最终 mult 不低于 0.3

输出：new_lots = floor(lots × mult)；若结果 < 1 但 lots ≥ 1，返回 1（不直接 BLOCK）。
若 cfg.bucket_floor 设置较低且 mult 低于 1 / lots，可能落到 0 → orchestrator 标 BLOCK。
"""
from __future__ import annotations

import logging
from math import floor

from cta.risk.base import (
    PositionScaler,
    SignalContext,
    extract_score,
    normalize_cluster,
    normalize_interval,
)
from cta.risk.state.bucket_pnl_tracker import (
    BucketKey,
    BucketPnlTracker,
    classify_score_bucket,
)

logger = logging.getLogger(__name__)


class BucketScalingSizer(PositionScaler):
    """按 (cluster, interval, score_bucket) 30 日滚动 PnL 缩 lots。"""

    def __init__(
        self,
        tracker: BucketPnlTracker,
        *,
        bp_steps: tuple[float, ...] = (-10.0, -5.0, 0.0),
        mults: tuple[float, ...] = (0.5, 0.7, 0.9, 1.0),
        floor_mult: float = 0.3,
        min_trades: int = 20,
        bucket_edges: tuple[float, ...] = (60.0, 70.0, 80.0, 90.0),
        bucket_names: tuple[str, ...] = ("p60-70", "p70-80", "p80-90", "p90+"),
    ) -> None:
        if len(mults) != len(bp_steps) + 1:
            raise ValueError(
                f"mults length must = bp_steps + 1, got mults={mults} bp_steps={bp_steps}"
            )
        if list(bp_steps) != sorted(bp_steps):
            raise ValueError(f"bp_steps must be strictly ascending, got {bp_steps}")
        if len(bucket_names) != len(bucket_edges):
            raise ValueError(
                f"bucket_names length must = bucket_edges length, "
                f"got names={bucket_names} edges={bucket_edges}"
            )
        if not 0.0 < float(floor_mult) <= 1.0:
            raise ValueError(f"floor_mult must be in (0,1], got {floor_mult}")
        self.tracker = tracker
        self.bp_steps = tuple(float(s) for s in bp_steps)
        self.mults = tuple(float(m) for m in mults)
        self.floor_mult = float(floor_mult)
        self.min_trades = int(min_trades)
        self.bucket_edges = tuple(float(e) for e in bucket_edges)
        self.bucket_names = tuple(str(n) for n in bucket_names)

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "bucket:lots_already_zero"
        pctl = extract_score(ctx, prefer_pctl=True)
        bucket_name = classify_score_bucket(pctl, edges=self.bucket_edges, names=self.bucket_names)
        if bucket_name is None:
            return lots, "bucket:below_edges_no_scale"
        cluster = normalize_cluster(ctx.candidate.get("cluster"))
        interval = normalize_interval(ctx.candidate.get("interval"))
        if not cluster or not interval:
            return lots, "bucket:missing_cluster_interval"
        key = BucketKey(cluster=cluster, interval=interval, score_bucket=bucket_name)
        count = self.tracker.trade_count(key, now=ctx.bar_dt)
        if count < self.min_trades:
            return lots, f"bucket:sample_insufficient:{count}<{self.min_trades}"
        pnl_bp = self.tracker.rolling_pnl_bp(key, now=ctx.bar_dt)
        mult = self._mult_for_pnl_bp(pnl_bp)
        new_lots = max(0, floor(lots * mult))
        if new_lots == 0 and lots >= 1 and mult > 0:
            # 别让 round-down 把 1 手缩到 0；保留 1 手
            new_lots = 1
        return new_lots, f"bucket:pnl_bp={pnl_bp:.2f}:mult={mult:.2f}"

    # ── internals ───────────────────────────────────────────────────

    def _mult_for_pnl_bp(self, pnl_bp: float) -> float:
        """bp_steps 严格升序，mults 长度 = bp_steps + 1。

        语义（区间从左闭到右开）：
            v < bp_steps[0]                    → mults[0]
            bp_steps[i] <= v < bp_steps[i+1]   → mults[i+1]
            v >= bp_steps[-1]                  → mults[-1]
        """
        v = float(pnl_bp)
        steps = self.bp_steps
        mults = self.mults
        if v < steps[0]:
            mult = mults[0]
        elif v >= steps[-1]:
            mult = mults[-1]
        else:
            mult = mults[-1]
            for i in range(len(steps) - 1):
                if steps[i] <= v < steps[i + 1]:
                    mult = mults[i + 1]
                    break
        return max(self.floor_mult, mult)


__all__ = ["BucketScalingSizer"]
