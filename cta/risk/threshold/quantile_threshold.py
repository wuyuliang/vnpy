"""按 manifest 查 (cluster, symbol, interval) 模型分位点 → 设阈值（子系统①）。

行为：
    entry = manifest.lookup(cluster, symbol, interval)
    if entry is None: return base_threshold   ← fail-open
    raw_q = entry.quantile(cfg.quantile_field)  ← e.g. symbol 自己的 p70 → 0.60
    pctl_q = cluster-wide CDF(raw_q)             ← 转为 trade_filter_prob_pctl 同单位

注意：本 adjuster **输出的是 pctl 阈值（0-100）**，与 trade_filter_prob_pctl 同单位。
caller 若用 raw 阈值模式，应自己换算。

当 manifest 的输出明显偏离 base（差 > sanity_band_pp）：仅 warning，不强制 clamp（避免
manifest 过期时悄悄拦截）。
"""
from __future__ import annotations

import logging

from cta.risk.base import SignalContext, normalize_cluster, normalize_interval, normalize_symbol
from cta.risk.state.score_quantile_manifest import ScoreQuantileEntry, ScoreQuantileManifest, WILDCARD_SYMBOL

logger = logging.getLogger(__name__)


class QuantileThresholdAdjuster:
    """从 ScoreQuantileManifest 查 (cluster, symbol, interval) 分位点作阈值。"""

    def __init__(
        self,
        manifest: ScoreQuantileManifest,
        *,
        quantile_field: str = "p70",
        sanity_band_pp: float = 50.0,
        emit_pctl: bool = True,
    ) -> None:
        self.manifest = manifest
        self.quantile_field = str(quantile_field)
        self.sanity_band_pp = float(sanity_band_pp)
        self.emit_pctl = bool(emit_pctl)

    def resolve(self, ctx: SignalContext, base_threshold: float) -> float:
        cluster = normalize_cluster(ctx.candidate.get("cluster"))
        symbol = normalize_symbol(ctx.candidate.get("symbol"))
        interval = normalize_interval(ctx.candidate.get("interval"))
        entry = self.manifest.lookup(cluster, symbol, interval)
        if entry is None:
            return float(base_threshold)
        try:
            raw_q = entry.quantile(self.quantile_field)
        except (AttributeError, ValueError):
            return float(base_threshold)
        if raw_q is None or raw_q != raw_q:  # NaN check
            return float(base_threshold)
        if self.emit_pctl:
            cluster_entry = self.manifest.lookup(cluster, WILDCARD_SYMBOL, interval)
            label_value = self._pctl_from_cluster_distribution(raw_q, cluster_entry)
            new_thr = max(float(base_threshold), float(label_value))
        else:
            # raw 阈值模式：用 manifest 里这个 cluster/symbol 在 quantile_field 处的 raw prob
            new_thr = float(raw_q)
        # sanity check
        if self.emit_pctl and self.sanity_band_pp > 0:
            if abs(new_thr - float(base_threshold)) > self.sanity_band_pp:
                logger.warning(
                    "quantile threshold for %s/%s/%s diverges from base: %.2f vs %.2f (band=%.1fpp); using new",
                    cluster, symbol, interval, new_thr, float(base_threshold), self.sanity_band_pp,
                )
        return new_thr

    @staticmethod
    def _pctl_from_field(field: str) -> float:
        m = {"p50": 50.0, "p60": 60.0, "p70": 70.0, "p80": 80.0, "p90": 90.0, "p95": 95.0}
        return float(m.get(str(field), 70.0))

    def _pctl_from_cluster_distribution(self, raw_q: float, cluster_entry: ScoreQuantileEntry | None) -> float:
        """Map a symbol-level raw probability quantile into cluster-wide pctl units.

        ``RiskOrchestrator`` compares against ``trade_filter_prob_pctl`` (0-100).
        The manifest stores raw probabilities per symbol. When a cluster-wide
        entry is available, interpolate the symbol's raw quantile on that
        cluster distribution. If the cluster entry is missing or unusable, fall
        back to the quantile label (p70 -> 70), preserving fail-open behavior.
        """
        if cluster_entry is None:
            return self._pctl_from_field(self.quantile_field)
        points: list[tuple[float, float]] = []
        for field, pctl in (("p50", 50.0), ("p60", 60.0), ("p70", 70.0), ("p80", 80.0), ("p90", 90.0), ("p95", 95.0)):
            try:
                value = float(cluster_entry.quantile(field))
            except (AttributeError, TypeError, ValueError):
                continue
            if value == value:
                points.append((value, pctl))
        if not points:
            return self._pctl_from_field(self.quantile_field)
        points.sort(key=lambda x: x[0])
        raw = float(raw_q)
        if raw <= points[0][0]:
            return float(points[0][1])
        if raw >= points[-1][0]:
            return float(points[-1][1])
        for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
            if x0 <= raw <= x1:
                if x1 == x0:
                    return float(max(y0, y1))
                weight = (raw - x0) / (x1 - x0)
                return float(y0 + weight * (y1 - y0))
        return self._pctl_from_field(self.quantile_field)


__all__ = ["QuantileThresholdAdjuster"]
