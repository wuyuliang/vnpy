"""Pre-open go/no-go checklist for sim/live."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PreopenCheckConfig:
    max_prediction_stale_hours: float = 24.0
    max_model_age_days: float = 14.0
    margin_buffer_pct: float = 0.0


@dataclass(frozen=True)
class PreopenCheckItem:
    name: str
    passed: bool
    detail: str


@dataclass
class PreopenCheckReport:
    items: list[PreopenCheckItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.items)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.items.append(PreopenCheckItem(name=name, passed=bool(passed), detail=str(detail)))


def _prediction_staleness_check(
    *,
    path: Path,
    now: pd.Timestamp,
    max_stale_hours: float,
) -> PreopenCheckItem:
    if not path.exists():
        return PreopenCheckItem("prediction_staleness", False, f"missing predictions file: {path}")
    mtime = pd.Timestamp(path.stat().st_mtime, unit="s")
    age_h = float((now - mtime).total_seconds() / 3600.0)
    ok = age_h <= float(max_stale_hours)
    return PreopenCheckItem(
        "prediction_staleness",
        ok,
        f"age_hours={age_h:.2f}, limit={float(max_stale_hours):.2f}",
    )


def _model_age_check(
    *,
    model_registry: Any | None,
    now: pd.Timestamp,
    max_model_age_days: float,
) -> PreopenCheckItem:
    if model_registry is None:
        return PreopenCheckItem("model_age", True, "model_registry missing -> skip")
    try:
        age_d = float(model_registry.max_model_age_days(as_of=now))
    except Exception as exc:  # noqa: BLE001
        return PreopenCheckItem("model_age", False, f"model age check failed: {exc}")
    if pd.isna(age_d):
        return PreopenCheckItem("model_age", False, "model age unavailable")
    ok = age_d <= float(max_model_age_days)
    return PreopenCheckItem(
        "model_age",
        ok,
        f"age_days={age_d:.2f}, limit={float(max_model_age_days):.2f}",
    )


def _kill_switch_check(active: bool) -> PreopenCheckItem:
    return PreopenCheckItem("kill_switch", not bool(active), "active" if active else "off")


def _margin_check(*, available_cash: float, required_margin: float, buffer_pct: float) -> PreopenCheckItem:
    avail = float(available_cash)
    req = float(required_margin) * (1.0 + float(buffer_pct))
    ok = avail >= req
    return PreopenCheckItem(
        "margin_budget",
        ok,
        f"available={avail:.2f}, required={req:.2f}, buffer_pct={float(buffer_pct):.4f}",
    )


def run_preopen_checklist(
    *,
    predictions_path: Path,
    model_registry: Any | None,
    kill_switch_active: bool,
    available_cash: float,
    required_margin: float,
    now: pd.Timestamp | None = None,
    cfg: PreopenCheckConfig | None = None,
) -> PreopenCheckReport:
    """Run pre-open checks and return go/no-go report."""
    c = cfg or PreopenCheckConfig()
    ts = pd.Timestamp(now if now is not None else pd.Timestamp.now())
    report = PreopenCheckReport()
    report.items.append(
        _prediction_staleness_check(
            path=Path(predictions_path),
            now=ts,
            max_stale_hours=float(c.max_prediction_stale_hours),
        )
    )
    report.items.append(
        _model_age_check(
            model_registry=model_registry,
            now=ts,
            max_model_age_days=float(c.max_model_age_days),
        )
    )
    report.items.append(_kill_switch_check(bool(kill_switch_active)))
    report.items.append(
        _margin_check(
            available_cash=float(available_cash),
            required_margin=float(required_margin),
            buffer_pct=float(c.margin_buffer_pct),
        )
    )
    return report


__all__ = [
    "PreopenCheckConfig",
    "PreopenCheckItem",
    "PreopenCheckReport",
    "run_preopen_checklist",
]
