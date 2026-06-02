"""§16.4 HolidayPositionReducer：节假日降仓。

依赖 ``cta.sim.trading_calendar.load_holidays_from_csv`` 复用现有节假日 CSV 加载。

公式（按距下一个节假日的"自然日"数 days_until_holiday）：
    days_until_holiday > pre_holiday_taper_days  → mult = 1.0
    days_until_holiday in [0, pre_holiday_taper_days]:
        step = days_until_holiday / max(1, pre_holiday_taper_days)
        mult = floor_mult + step × (1.0 - floor_mult)
    刚过完节 days_after_holiday < post_holiday_warmup_days：
        step = days_after_holiday / max(1, post_holiday_warmup_days)
        mult = floor_mult + step × (1.0 - floor_mult)
    其他 → mult = 1.0

inverse_for_clusters（避险品种节前满仓）：
    cluster in inverse_for_clusters 时 → 用 1.0 - (mult - floor_mult)，即翻转曲线
"""
from __future__ import annotations

import logging
from datetime import date
from math import floor
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.risk.base import PositionScaler, SignalContext, normalize_cluster
from cta.risk.sizing.config import HolidayPositionReducerConfig

logger = logging.getLogger(__name__)


class HolidayPositionReducer(PositionScaler):
    """节假日降仓。"""

    def __init__(
        self,
        cfg: HolidayPositionReducerConfig | None = None,
        *,
        holidays: Iterable[date] | None = None,
    ) -> None:
        self.cfg = cfg or HolidayPositionReducerConfig()
        if holidays is not None:
            self._holidays = sorted({pd.Timestamp(d).date() for d in holidays})
        elif self.cfg.holidays_csv:
            self._holidays = self._load_holidays_safe(self.cfg.holidays_csv)
        else:
            self._holidays = []

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "holiday:lots_already_zero"
        if not self._holidays:
            return lots, "holiday:no_calendar"
        cluster = self._cluster_of(ctx)
        mult = self._mult_for(ctx, cluster)
        if mult >= 1.0:
            return lots, f"holiday:cluster={cluster}:mult=1.00"
        new_lots = max(0, floor(lots * mult))
        if new_lots == 0 and lots >= 1 and mult > 0:
            new_lots = 1
        return new_lots, f"holiday:cluster={cluster}:mult={mult:.2f}"

    # ── helpers ─────────────────────────────────────────────────────

    def _mult_for(self, ctx: SignalContext, cluster: str) -> float:
        d_until = self._days_until_next_holiday(ctx.bar_dt)
        d_after = self._days_after_last_holiday(ctx.bar_dt)
        base_mult = 1.0
        if d_until is not None and 0 <= d_until <= self.cfg.pre_holiday_taper_days:
            step = d_until / max(1, self.cfg.pre_holiday_taper_days)
            base_mult = self.cfg.floor_mult + step * (1.0 - self.cfg.floor_mult)
        elif d_after is not None and 0 <= d_after < self.cfg.post_holiday_warmup_days:
            step = d_after / max(1, self.cfg.post_holiday_warmup_days)
            base_mult = self.cfg.floor_mult + step * (1.0 - self.cfg.floor_mult)
        # 避险品种反向（节前满仓避险）
        if cluster in {str(c).lower() for c in self.cfg.inverse_for_clusters}:
            # 翻转：mult=floor 时变为 1.0；mult=1.0 时变为 floor + (1-floor) = 1.0；
            # 等价 inverse 公式 mult' = 1 + floor - mult
            base_mult = max(self.cfg.floor_mult, min(1.0, 1.0 + self.cfg.floor_mult - base_mult))
        return float(base_mult)

    @staticmethod
    def _cluster_of(ctx: SignalContext) -> str:
        cl = ctx.candidate.get("cluster") if isinstance(ctx.candidate, dict) else None
        if cl:
            return normalize_cluster(cl)
        sym = ctx.candidate.get("symbol", "") if isinstance(ctx.candidate, dict) else ""
        return normalize_cluster(infer_symbol_cluster(str(sym)))

    def _days_until_next_holiday(self, bar_dt: pd.Timestamp) -> int | None:
        if not self._holidays:
            return None
        today = pd.Timestamp(bar_dt).date()
        future = [h for h in self._holidays if h >= today]
        if not future:
            return None
        return int((future[0] - today).days)

    def _days_after_last_holiday(self, bar_dt: pd.Timestamp) -> int | None:
        if not self._holidays:
            return None
        today = pd.Timestamp(bar_dt).date()
        past = [h for h in self._holidays if h < today]
        if not past:
            return None
        return int((today - past[-1]).days)

    @staticmethod
    def _load_holidays_safe(csv_path: str) -> list[date]:
        try:
            from cta.sim.trading_calendar import load_holidays_from_csv
            return sorted(load_holidays_from_csv(Path(csv_path)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("HolidayPositionReducer load_holidays failed at %s: %s", csv_path, exc)
            return []


__all__ = ["HolidayPositionReducer"]
