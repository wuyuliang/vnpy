"""Drawdown-adaptive risk throttling utilities."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from cta.portfolio_logic.config import CapsConfig, PyramidConfig, RiskThrottleConfig, ThrottleLevel


@dataclass(frozen=True)
class EquitySnapshot:
    """Point-in-time equity/risk state."""

    drawdown_pct: float
    weekly_return_pct: float
    monthly_return_pct: float
    equity: float
    running_high: float
    running_high_at: pd.Timestamp | None


@dataclass
class EquityTracker:
    """Track equity and compute drawdown/weekly/monthly performance."""

    equity_history: list[tuple[pd.Timestamp, float]]
    running_high: float
    running_high_at: pd.Timestamp | None

    def __init__(self) -> None:
        self.equity_history = []
        self.running_high = 0.0
        self.running_high_at = None

    @classmethod
    def bootstrap_from_broker(
        cls,
        initial_equity: float,
        history_path: str | Path | None = None,
    ) -> "EquityTracker":
        """Build tracker from broker-recovered equity + optional persisted history."""
        out = cls()
        eq = max(0.0, float(initial_equity))
        now_ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
        out.on_bar(eq, now_ts)
        if history_path is None:
            return out
        p = Path(history_path).expanduser()
        if not p.exists():
            return out
        try:
            hist = pd.read_csv(p, encoding="utf-8-sig")
        except Exception:
            return out
        if "datetime" not in hist.columns or "equity" not in hist.columns:
            return out
        hist = hist.copy()
        hist["datetime"] = pd.to_datetime(hist["datetime"], errors="coerce")
        hist["equity"] = pd.to_numeric(hist["equity"], errors="coerce")
        hist = hist.dropna(subset=["datetime", "equity"]).sort_values("datetime")
        out.equity_history = []
        out.running_high = 0.0
        out.running_high_at = None
        for _, row in hist.iterrows():
            out.on_bar(float(row["equity"]), pd.Timestamp(row["datetime"]))
        if not out.equity_history:
            out.on_bar(eq, now_ts)
        return out

    def on_bar(self, equity: float, ts: pd.Timestamp) -> None:
        ets = pd.Timestamp(ts)
        eq = float(equity)
        self.equity_history.append((ets, eq))
        if eq > self.running_high:
            self.running_high = eq
            self.running_high_at = ets

    def current_drawdown_pct(self) -> float:
        if not self.equity_history or self.running_high <= 0.0:
            return 0.0
        current = float(self.equity_history[-1][1])
        return max(0.0, 1.0 - current / self.running_high)

    def _period_return_pct(self, days: int) -> float:
        if not self.equity_history:
            return 0.0
        now = self.equity_history[-1][0]
        cutoff = now - pd.Timedelta(days=int(days))
        base_equity = self.equity_history[0][1]
        for ts, eq in self.equity_history:
            if ts >= cutoff:
                base_equity = eq
                break
        current = self.equity_history[-1][1]
        if float(base_equity) <= 0.0:
            return 0.0
        return float(current) / float(base_equity) - 1.0

    def weekly_return_pct(self) -> float:
        return self._period_return_pct(days=7)

    def monthly_return_pct(self) -> float:
        return self._period_return_pct(days=30)

    def snapshot(self) -> EquitySnapshot:
        equity = float(self.equity_history[-1][1]) if self.equity_history else 0.0
        return EquitySnapshot(
            drawdown_pct=self.current_drawdown_pct(),
            weekly_return_pct=self.weekly_return_pct(),
            monthly_return_pct=self.monthly_return_pct(),
            equity=equity,
            running_high=self.running_high,
            running_high_at=self.running_high_at,
        )


class RiskThrottle:
    """Select risk regime according to drawdown and recent performance."""

    def __init__(self, cfg: RiskThrottleConfig) -> None:
        self.cfg = cfg
        self._name_to_level = {lv.name: lv for lv in cfg.levels}
        self._severity_rank = {lv.name: i for i, lv in enumerate(cfg.levels)}

    def level_by_name(self, name: str) -> ThrottleLevel:
        return self._name_to_level[str(name)]

    def _severity(self, level: ThrottleLevel) -> int:
        return self._severity_rank.get(level.name, -1)

    def _is_relaxation(self, candidate: ThrottleLevel, current: ThrottleLevel) -> bool:
        return self._severity(candidate) < self._severity(current)

    def make_snapshot(
        self,
        *,
        drawdown_pct: float,
        weekly_return_pct: float,
        monthly_return_pct: float,
        equity: float,
    ) -> EquitySnapshot:
        """Small helper for tests and tooling."""
        return EquitySnapshot(
            drawdown_pct=float(drawdown_pct),
            weekly_return_pct=float(weekly_return_pct),
            monthly_return_pct=float(monthly_return_pct),
            equity=float(equity),
            running_high=float(equity),
            running_high_at=None,
        )

    def compute(self, snap: EquitySnapshot, current_level: ThrottleLevel | None) -> ThrottleLevel:
        """Pick throttle level with hysteresis and weekly/monthly hardening."""
        dd = float(snap.drawdown_pct)
        candidate = self.cfg.levels[-1]
        for level in self.cfg.levels:
            if float(level.drawdown_lo) <= dd < float(level.drawdown_hi):
                candidate = level
                break

        if current_level is not None and self._is_relaxation(candidate, current_level):
            relax_boundary = float(candidate.drawdown_hi) - float(self.cfg.recovery_hysteresis)
            if dd >= relax_boundary:
                candidate = current_level

        if (
            float(snap.weekly_return_pct) < float(self.cfg.force_conservative_if_weekly_lt)
            or float(snap.monthly_return_pct) < float(self.cfg.force_conservative_if_monthly_lt)
        ):
            conservative = self.level_by_name("conservative")
            if self._severity(conservative) > self._severity(candidate):
                candidate = conservative

        return candidate

    def apply_to_caps(self, base_caps: CapsConfig, level: ThrottleLevel) -> CapsConfig:
        """Scale position caps using the selected throttle level."""
        total = max(0, int(base_caps.max_total_positions * float(level.max_total_positions_mult)))
        per_cluster = max(0, int(base_caps.max_total_per_cluster * float(level.max_per_cluster_mult)))
        return replace(
            base_caps,
            max_total_positions=total,
            max_total_per_cluster=per_cluster,
        )

    def apply_to_pyramid(self, base_cfg: PyramidConfig, level: ThrottleLevel) -> PyramidConfig:
        """Scale/disable pyramid behavior under tighter throttle levels."""
        if not bool(level.allow_pyramid):
            return replace(
                base_cfg,
                enabled=False,
                max_active_layers=0,
                max_lifetime_layers=0,
            )
        max_layers = max(0, int(level.max_pyramid_layers))
        if max_layers <= 0:
            return replace(
                base_cfg,
                enabled=False,
                max_active_layers=0,
                max_lifetime_layers=0,
            )
        return replace(
            base_cfg,
            enabled=bool(base_cfg.enabled),
            max_active_layers=min(int(base_cfg.max_active_layers), max_layers),
            max_lifetime_layers=min(int(base_cfg.max_lifetime_layers), max_layers),
        )
