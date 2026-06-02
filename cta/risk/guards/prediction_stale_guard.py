"""§18.2 PredictionStaleGuard：数据新鲜度。

判定：
    predictions.csv mtime 距 ctx.now > max_stale_hours  → 拒开仓
    model joblib mtime 距 ctx.now > max_model_age_days   → warning（不拦）

性能优化：mtime cache（默认 30s 内不重复 stat）；与 cta/docs/review/20260528.md §4.4
P1-4 kill_switch 同款思路，避免每订单一次 IO。

fail-open：
    predictions_path 空 / 文件不存在 → 放行（caller 没注入路径，不强制要求）
    stat 异常 → 放行 + 日志 warning
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from cta.live.risk import RiskContext, RiskDecision, _BaseRule
from cta.risk.guards.config import PredictionStaleGuardConfig

logger = logging.getLogger(__name__)


class PredictionStaleGuard(_BaseRule):
    """数据新鲜度硬约束。"""

    name: str = "prediction_stale"

    def __init__(self, cfg: PredictionStaleGuardConfig | None = None) -> None:
        self.cfg = cfg or PredictionStaleGuardConfig()
        self._cache_ts: float = 0.0
        self._cache_stale: bool = False
        self._cache_reason: str = ""

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        offset = str(order.get("offset", "")).strip().lower()
        if offset == "close" and not self.cfg.block_close_on_stale:
            return RiskDecision(True, "")
        if offset != "close" and not self.cfg.block_open_on_stale:
            return RiskDecision(True, "")
        # 路径空 → 不强制 → 放行
        if not self.cfg.predictions_path:
            return RiskDecision(True, "")
        now = pd.Timestamp(getattr(ctx, "now", pd.Timestamp.now()))
        is_stale, reason = self._check_stale(now)
        if is_stale:
            return RiskDecision(False, f"{self.name}:{reason}")
        return RiskDecision(True, "")

    # ── helpers ─────────────────────────────────────────────────────

    def _check_stale(self, now: pd.Timestamp) -> tuple[bool, str]:
        # mtime cache 命中且未过期
        if self.cfg.cache_seconds > 0:
            elapsed = time.time() - self._cache_ts
            if elapsed < float(self.cfg.cache_seconds):
                return self._cache_stale, self._cache_reason
        is_stale, reason = self._compute_stale(now)
        self._cache_ts = time.time()
        self._cache_stale = is_stale
        self._cache_reason = reason
        return is_stale, reason

    def _compute_stale(self, now: pd.Timestamp) -> tuple[bool, str]:
        p = Path(self.cfg.predictions_path)
        if not p.exists():
            return False, ""    # 路径不存在 → fail-open
        try:
            mtime = p.stat().st_mtime
        except Exception as exc:  # noqa: BLE001
            logger.warning("predictions stat failed at %s: %s — fail-open", p, exc)
            return False, ""
        # 注：用 time.time() 而非 now.timestamp()——pd.Timestamp.now() 是 naive Asia/Shanghai
        # wall clock，.timestamp() 把它当 UTC 解释会引入 +8h 偏差；mtime 是 wall-clock UTC epoch，
        # 两者直接相减更准确。ctx.now 仍可被调用方用于其他逻辑（日志归因等）。
        wall_now = time.time()
        age_hours = (wall_now - float(mtime)) / 3600.0
        if age_hours > float(self.cfg.max_stale_hours):
            return True, f"predictions_age={age_hours:.2f}h>{self.cfg.max_stale_hours}h"
        # model age 仅 warning，不影响 decision
        if self.cfg.model_path:
            mp = Path(self.cfg.model_path)
            if mp.exists():
                try:
                    m_mtime = mp.stat().st_mtime
                    age_days = (wall_now - float(m_mtime)) / 86400.0
                    if age_days > float(self.cfg.max_model_age_days):
                        logger.warning(
                            "model file %s age %.1f days > %.1f days; consider retrain",
                            mp, age_days, self.cfg.max_model_age_days,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("model stat failed at %s: %s", mp, exc)
        return False, ""


__all__ = ["PredictionStaleGuard"]
